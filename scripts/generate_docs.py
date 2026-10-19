"""Generate the synthgen v1 document set with its answer keys. Deterministic for a seed.

Layout (SYNTHGEN_SPEC.md 3.2, under a versioned root since Tier 2 Block 2):

    data/generated/synthgen_v1/<doc_kind>/<tier>/doc_XXX.json
                                                  doc_XXX.pdf          clean, or crisp moderate
                                                  doc_XXX.png          scanned / photographed
                                                  doc_XXX.clean.pdf    the page a PNG was made from
    data/generated/synthgen_v1/<doc_kind>/manifest.json

The Block 1 set at data/generated/<doc_kind>/clean/ is never written to: its eval runs stay
reproducible from disk, and it is the witness the v1 stability diff is taken against.

    uv run python scripts/generate_docs.py --plan     (or: make docs-plan) - writes nothing
    uv run python scripts/generate_docs.py            (or: make docs)

`--plan` prints, before anything is rendered, the per-quirk x tier table for the
non-holdout set and checks two things that must hold before rendering is worth doing:
every injected quirk reaches the floor of 15 non-holdout documents per kind, and every
activity year in the set resolves against the factor years the table holds.

make docs is the supported entry point on macOS: WeasyPrint needs
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib, which the Makefile exports.
"""

import argparse
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

from carbon_r.config import PROJECT_ROOT, get_settings
from carbon_r.models.electricity_bill import ElectricityBill
from carbon_r.models.fuel_card_statement import FuelCardStatement
from carbon_r.models.lpg_invoice import LPGInvoice
from carbon_r.money import round2
from carbon_r.prorate import split_by_year
from carbon_r.synthgen import electricity, fuel_card, lpg
from carbon_r.synthgen.degrade import degrade_pdf, is_image
from carbon_r.synthgen.plan import (
    FACTOR_YEARS,
    LAST_PERIOD_MONTH,
    MAX_FACTOR_YEAR_GAP,
    MIN_PER_QUIRK,
    plan_all,
    quirk_table,
    shortfalls,
)
from carbon_r.synthgen.render import render_html, template_for, write_pdf

GENERATED_ROOT = PROJECT_ROOT / "data" / "generated" / "synthgen_v1"

# doc_kind -> (v1 builder, generator version). One row per kind; `--kind all` walks them
# in this order.
KINDS = {
    electricity.DOC_KIND: (electricity.build_v1, electricity.GENERATOR_VERSION),
    lpg.DOC_KIND: (lpg.build_v1, lpg.GENERATOR_VERSION),
    fuel_card.DOC_KIND: (fuel_card.build_v1, fuel_card.GENERATOR_VERSION),
}
DEFAULT_SEED = electricity.DEFAULT_SEED
TIERS = ("clean", "moderate", "hard")

# Documents whose activity year is deliberately unresolvable: the honest answer for them is
# no figure. lpg_001's December 2024 supply precedes every DESNZ year loaded (2025-2026).
EXPECTED_REFUSALS = {"lpg_001"}

# Citations the generated set depends on, recorded so the manifest is self-describing.
# GST is verified from primary (SOURCES.md #4). The tariff *structure* is not - every rate
# and plan in profiles.py is fictional and nothing claims to model real SG pricing.
CITATIONS = {
    "gst_rate": (
        "IRAS, '2024 GST Rate Change: A Guide for GST-registered Businesses' (1st edition): "
        "GST rose from 8% to 9% with effect from 1 Jan 2024. Verified from primary."
    ),
    "tariff_structure": (
        "NOT VERIFIED. Rates, plans, vendor and UEN are fictional; no real retailer's "
        "pricing is reproduced. See SOURCES.md rows 6-7."
    ),
}


def _summary_closes(document) -> bool:
    """balance - payment + this period's total = amount due, where a summary is printed."""
    if document.amount_due_sgd is None:
        return True
    due = document.balance_forward_sgd - document.payment_received_sgd + document.total_sgd
    return due == document.amount_due_sgd


def _electricity_closes(bill: ElectricityBill) -> bool:
    energy = round2(Decimal(bill.consumption_kwh) * bill.tariff_cents_per_kwh / Decimal(100))
    subtotal = energy + bill.fixed_charge_sgd - bill.credit_amount_sgd
    gst = round2(subtotal * bill.gst_rate)
    split = split_by_year(bill.consumption_kwh, bill.period_start, bill.period_end)
    return (
        (subtotal + gst) == bill.total_sgd
        and split == bill.split_kwh_by_year
        and sum(split.values()) == bill.consumption_kwh
        and _summary_closes(bill)
    )


def _lpg_closes(invoice: LPGInvoice) -> bool:
    """Recompute the invoice from counts, sizes and prices - including the kg.

    The kg check is the one that matters on this kind: the activity is derived, not
    printed, so an error in the cylinder arithmetic would never show up in the money.
    """
    gas = sum((Decimal(line.count) * line.size_kg for line in invoice.cylinders), Decimal(0))
    cylinders = sum(
        (round2(Decimal(line.count) * line.price_per_cylinder_sgd) for line in invoice.cylinders),
        Decimal(0),
    )
    count = sum(line.count for line in invoice.cylinders)
    rental = round2(Decimal(count) * invoice.rental_per_cylinder_sgd)
    visits = len({line.delivery_date for line in invoice.cylinders})
    delivery = round2(Decimal(visits) * invoice.delivery_fee_sgd)
    subtotal = cylinders + delivery + rental - invoice.credit_amount_sgd
    gst = round2(subtotal * invoice.gst_rate)
    return (
        gas == invoice.lpg_kg and (subtotal + gst) == invoice.total_sgd and _summary_closes(invoice)
    )


def _fuel_closes(statement: FuelCardStatement) -> bool:
    """Recompute the statement from litres and prices - including litres per product."""
    litres: dict[str, Decimal] = {}
    gross = Decimal(0)
    for transaction in statement.transactions:
        litres[transaction.product] = (
            litres.get(transaction.product, Decimal(0)) + transaction.litres
        )
        gross += round2(transaction.litres * transaction.unit_price_sgd)
    discount = round2(sum(litres.values(), Decimal(0)) * statement.discount_per_litre_sgd)
    subtotal = gross - discount - statement.credit_amount_sgd
    gst = round2(subtotal * statement.gst_rate)
    return (
        litres == dict(statement.litres_by_product)
        and (subtotal + gst) == statement.total_sgd
        and _summary_closes(statement)
    )


# doc_kind -> an independent recheck of that kind's arithmetic.
#
# Deliberately does not call any validator: a validator cannot be its own witness
# (spec 1.3). Each of these recomputes the printed totals from the key's raw inputs.
CLOSERS = {
    "electricity_bill": _electricity_closes,
    "lpg_invoice": _lpg_closes,
    "fuel_card_statement": _fuel_closes,
}


def arithmetic_closes(document) -> bool:
    """Recompute one document's printed totals from its raw inputs, whatever its kind."""
    return CLOSERS[document.doc_kind](document)


def activity_summary(document) -> dict:
    """What a manifest records about the activity on one document, per kind.

    The generator records activity data only; it never looks up a factor or computes
    emissions (spec 3.4), so this is quantities and nothing else.
    """
    if document.doc_kind == "electricity_bill":
        return {
            "consumption_kwh": document.consumption_kwh,
            "split_kwh_by_year": {str(y): n for y, n in document.split_kwh_by_year.items()},
        }
    if document.doc_kind == "lpg_invoice":
        return {
            "lpg_kg": str(document.lpg_kg),
            "cylinders": document.cylinder_count,
            "cylinder_sizes_kg": sorted({str(line.size_kg) for line in document.cylinders}),
            "visits": document.deliveries,
        }
    return {
        "litres_by_product": {k: str(v) for k, v in document.litres_by_product.items()},
        "transactions": len(document.transactions),
    }


def activity_years(document) -> list[int]:
    """The years this document's activity asks a factor for."""
    if document.doc_kind == "electricity_bill":
        return sorted(document.split_kwh_by_year)
    return [document.factor_year]


def factor_categories(document) -> list[str]:
    if document.doc_kind == "electricity_bill":
        return ["electricity"]
    if document.doc_kind == "lpg_invoice":
        return ["lpg"]
    return sorted(
        "diesel" if p.lower().startswith("diesel") else "petrol" for p in document.products
    )


def resolves(category: str, year: int) -> bool:
    """True when the factor table can serve `year`: exactly, or within the substitution cap."""
    held = [y for y in FACTOR_YEARS[category] if y <= year]
    return bool(held) and year - max(held) <= MAX_FACTOR_YEAR_GAP


def unresolved(documents) -> list[str]:
    """Every (document, category, year) the factor table cannot serve, bar the expected."""
    problems = []
    for document in documents:
        for category in factor_categories(document):
            for year in activity_years(document):
                if not resolves(category, year) and document.doc_id not in EXPECTED_REFUSALS:
                    problems.append(f"{document.doc_id}: no {category} factor for {year}")
    return problems


def out_of_window(documents) -> list[str]:
    """Documents whose period ends after the last month the set may cover."""
    last_year, last_month = LAST_PERIOD_MONTH
    return [
        f"{d.doc_id}: period ends {d.period_end}"
        for d in documents
        if (d.period_end.year, d.period_end.month) > (last_year, last_month)
    ]


def document_file(document) -> str:
    """The file a reader of this document should open: the PNG for a degraded tier."""
    return f"{document.doc_id}.png" if is_image(document.degradation) else f"{document.doc_id}.pdf"


def write_set(kind: str, out_root: Path, seed: int, tiers: tuple[str, ...]) -> tuple[list, int]:
    builder, generator_version = KINDS[kind]
    documents = [d for d in builder(seed) if d.difficulty_tier in tiers]

    closes = 0
    for doc in documents:
        tier_dir = out_root / kind / doc.difficulty_tier
        tier_dir.mkdir(parents=True, exist_ok=True)
        html = render_html(doc)
        if is_image(doc.degradation):
            clean_pdf = tier_dir / f"{doc.doc_id}.clean.pdf"
            write_pdf(html, clean_pdf)
            degrade_pdf(clean_pdf, tier_dir / f"{doc.doc_id}.png", doc.degradation)
        else:
            write_pdf(html, tier_dir / f"{doc.doc_id}.pdf")
        (tier_dir / f"{doc.doc_id}.json").write_text(
            doc.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        closes += arithmetic_closes(doc)

    quirk_counts = Counter(q for doc in documents for q in doc.quirks)
    manifest = {
        "doc_kind": kind,
        "generator_version": generator_version,
        "seed": seed,
        "document_count": len(documents),
        "counts": {
            "by_tier": dict(Counter(d.difficulty_tier for d in documents)),
            "by_layout": dict(Counter(d.layout_id for d in documents)),
            "by_quirk": dict(quirk_counts) or {"(none)": len(documents)},
            "by_factor_year": dict(Counter(y for d in documents for y in activity_years(d))),
            "holdout_documents": sum(d.is_holdout_layout for d in documents),
        },
        "holdout_layouts": sorted({d.layout_id for d in documents if d.is_holdout_layout}),
        "templates": sorted({template_for(d) for d in documents}),
        "gst_rate": str(get_settings().gst_rate),
        "citations": CITATIONS,
        "note": (
            "Synthetic documents. Vendor, customer, account, meter and tariff are fictional. "
            "Answer keys are produced by the generator, never by reading a document with a "
            "model. The emission-factor year comes from the activity period, not "
            "document_date. The generator records activity data only; it never looks up an "
            "emission factor or computes emissions."
        ),
        "documents": [
            {
                "doc_id": d.doc_id,
                "file": f"{d.difficulty_tier}/{document_file(d)}",
                "customer_name": d.customer_name,
                "account_number": d.account_number,
                "layout_id": d.layout_id,
                "is_holdout_layout": d.is_holdout_layout,
                "difficulty_tier": d.difficulty_tier,
                "period_start": d.period_start.isoformat(),
                "period_end": d.period_end.isoformat(),
                "document_date": d.document_date.isoformat(),
                "factor_years": activity_years(d),
                "activity": activity_summary(d),
                "total_sgd": str(d.total_sgd),
                "quirks": d.quirks,
            }
            for d in documents
        ],
    }
    (out_root / kind / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return documents, closes


def print_plan(seed: int) -> int:
    """The --plan report: counts, the quirk table, and the pre-render checks. Writes nothing."""
    plans = plan_all(seed)
    print(f"synthgen v1 plan, seed {seed} - nothing is written\n")
    total = 0
    for kind, kind_plans in plans.items():
        total += len(kind_plans)
        visible = [p for p in kind_plans if not p.is_holdout]
        print(
            f"{kind:20s} {len(kind_plans):3d} documents  "
            f"({len(visible)} non-holdout)  groups {dict(Counter(p.group for p in kind_plans))}"
        )
        print(f"{'':20s} tiers {dict(Counter(p.tier for p in kind_plans))}")
        print(f"{'':20s} layouts {dict(sorted(Counter(p.layout_id for p in kind_plans).items()))}")
    print(f"{'total':20s} {total:3d} documents\n")

    print(f"Injected quirks, NON-HOLDOUT documents (floor {MIN_PER_QUIRK} per kind):")
    print(f"  {'kind':20s} {'quirk':20s} {'moderate':>8s} {'hard':>5s} {'total':>6s}")
    for row in quirk_table(plans):
        flag = "" if row["total"] >= MIN_PER_QUIRK else "  <- below floor"
        print(
            f"  {row['kind']:20s} {row['quirk']:20s} {row['moderate']:8d} {row['hard']:5d} "
            f"{row['total']:6d}{flag}"
        )

    documents = [d for kind in KINDS for d in KINDS[kind][0](seed)]
    print("\nStructural quirks, NON-HOLDOUT documents, by tier:")
    for name in ("lpg_cylinders", "mixed_fuel"):
        tiers = Counter(
            d.difficulty_tier for d in documents if name in d.quirks and not d.is_holdout_layout
        )
        print(f"  {name:20s} {dict(tiers)}  total {sum(tiers.values())}")

    crossings = Counter(
        tuple(sorted(d.split_kwh_by_year))
        for d in documents
        if d.doc_kind == "electricity_bill" and "cross_year_period" in d.quirks
    )
    print(
        "\nCross-year bills by crossing:",
        {f"{a}/{b}": n for (a, b), n in sorted(crossings.items())},
    )

    problems = shortfalls(plans) + unresolved(documents) + out_of_window(documents)
    refusals = sorted(d.doc_id for d in documents if d.doc_id in EXPECTED_REFUSALS)
    print(f"Expected refusals (no factor by design): {refusals}")
    if problems:
        print("\nPLAN FAILS - nothing should be rendered:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("\nEvery quirk reaches the floor; every activity year resolves; nothing is dated late.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kind",
        choices=[*sorted(KINDS), "all"],
        default="all",
        help="document kind to generate, or 'all' for every kind",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=GENERATED_ROOT)
    parser.add_argument(
        "--tiers", default=",".join(TIERS), help="comma-separated difficulty tiers to write"
    )
    parser.add_argument(
        "--plan", action="store_true", help="print the plan table and checks; write nothing"
    )
    args = parser.parse_args()

    if args.plan:
        return print_plan(args.seed)

    tiers = tuple(t.strip() for t in args.tiers.split(",") if t.strip())
    unknown = [t for t in tiers if t not in TIERS]
    if unknown:
        print(f"Unknown tier(s) {', '.join(unknown)}; have {', '.join(TIERS)}.", file=sys.stderr)
        return 2

    # The pre-render checks run again here, so `make docs` cannot render a set the plan
    # would have refused.
    if print_plan(args.seed) != 0:
        return 1

    kinds = sorted(KINDS) if args.kind == "all" else [args.kind]
    failed = 0
    for kind in kinds:
        try:
            documents, closes = write_set(kind, args.out, args.seed, tiers)
        except OSError as exc:
            print(f"Could not render {kind}: {exc}", file=sys.stderr)
            print(
                "On macOS WeasyPrint needs DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib; "
                "use `make docs`.",
                file=sys.stderr,
            )
            return 1

        target = args.out / kind
        rel = target.relative_to(PROJECT_ROOT) if target.is_relative_to(PROJECT_ROOT) else target
        print(f"\n{kind}: {len(documents)} documents written to {rel}  (seed {args.seed})")
        print(
            f"  tiers {dict(Counter(d.difficulty_tier for d in documents))}, "
            f"layouts {dict(sorted(Counter(d.layout_id for d in documents).items()))}"
        )
        print(f"  quirks {dict(Counter(q for d in documents for q in d.quirks))}")
        print(f"  arithmetic closes to the cent on {closes} of {len(documents)}")
        print(f"  activity: {_activity_range(kind, documents)}")
        if closes != len(documents):
            failed += 1

    return 0 if failed == 0 else 1


def _activity_range(kind: str, documents: list) -> str:
    """A one-line span of the activity in a set, for the run output."""
    if kind == "electricity_bill":
        amounts = [d.consumption_kwh for d in documents]
        return f"{min(amounts):,} - {max(amounts):,} kWh"
    if kind == "lpg_invoice":
        amounts = [d.lpg_kg for d in documents]
        return f"{min(amounts)} - {max(amounts)} kg LPG"
    products = sorted({p for d in documents for p in d.litres_by_product})
    spans = []
    for product in products:
        values = [d.litres_by_product[product] for d in documents if product in d.litres_by_product]
        spans.append(f"{product} {min(values)} - {max(values)} L")
    return "; ".join(spans)


if __name__ == "__main__":
    raise SystemExit(main())
