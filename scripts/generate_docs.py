"""Generate synthetic source documents with their answer keys. Deterministic for a seed.

Layout follows SYNTHGEN_SPEC.md 3.2:

    data/generated/<doc_kind>/<tier>/doc_XXX.pdf + doc_XXX.json
    data/generated/<doc_kind>/manifest.json

This block produces the `clean` tier only; `moderate` and `hard` arrive with the
degradation pipeline in Tier 2 Block 2, which is why --tiers already exists and rejects
the others explicitly rather than silently producing nothing.

    uv run python scripts/generate_docs.py                    (or: make docs)
    uv run python scripts/generate_docs.py --kind lpg_invoice

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
from carbon_r.synthgen import electricity, fuel_card, lpg
from carbon_r.synthgen.render import render_html, template_for, write_pdf

GENERATED_ROOT = PROJECT_ROOT / "data" / "generated"

# doc_kind -> (series builder, generator version). One row per kind; `--kind all` walks
# them in this order.
KINDS = {
    electricity.DOC_KIND: (electricity.build_bill_series, electricity.GENERATOR_VERSION),
    lpg.DOC_KIND: (lpg.build_invoice_series, lpg.GENERATOR_VERSION),
    fuel_card.DOC_KIND: (fuel_card.build_statement_series, fuel_card.GENERATOR_VERSION),
}
DEFAULT_SEED = electricity.DEFAULT_SEED
IMPLEMENTED_TIERS = ("clean",)

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


def _electricity_closes(bill: ElectricityBill) -> bool:
    energy = round2(Decimal(bill.consumption_kwh) * bill.tariff_cents_per_kwh / Decimal(100))
    subtotal = energy + bill.fixed_charge_sgd
    gst = round2(subtotal * bill.gst_rate)
    return (subtotal + gst) == bill.total_sgd


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
    subtotal = cylinders + invoice.delivery_charge_sgd + rental
    gst = round2(subtotal * invoice.gst_rate)
    return gas == invoice.lpg_kg and (subtotal + gst) == invoice.total_sgd


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
    subtotal = gross - discount
    gst = round2(subtotal * statement.gst_rate)
    return litres == dict(statement.litres_by_product) and (subtotal + gst) == statement.total_sgd


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
        return {"consumption_kwh": document.consumption_kwh}
    if document.doc_kind == "lpg_invoice":
        return {
            "lpg_kg": str(document.lpg_kg),
            "cylinders": document.cylinder_count,
            "cylinder_size_kg": str(document.cylinders[0].size_kg),
        }
    return {
        "litres_by_product": {k: str(v) for k, v in document.litres_by_product.items()},
        "transactions": len(document.transactions),
    }


def write_set(kind: str, out_root: Path, seed: int) -> tuple[list, int]:
    builder, generator_version = KINDS[kind]
    documents = builder(seed)

    closes = 0
    for doc in documents:
        tier_dir = out_root / kind / doc.difficulty_tier
        tier_dir.mkdir(parents=True, exist_ok=True)
        write_pdf(render_html(doc), tier_dir / f"{doc.doc_id}.pdf")
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
            "by_factor_year": dict(Counter(d.factor_year for d in documents)),
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
                "customer_name": d.customer_name,
                "account_number": d.account_number,
                "layout_id": d.layout_id,
                "difficulty_tier": d.difficulty_tier,
                "period_start": d.period_start.isoformat(),
                "period_end": d.period_end.isoformat(),
                "document_date": d.document_date.isoformat(),
                "factor_year": d.factor_year,
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
        "--tiers",
        default="clean",
        help="comma-separated difficulty tiers; only 'clean' exists until Tier 2 Block 2",
    )
    args = parser.parse_args()

    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]
    unimplemented = [t for t in tiers if t not in IMPLEMENTED_TIERS]
    if unimplemented:
        print(
            f"Tier(s) {', '.join(unimplemented)} need the degradation pipeline "
            "(Tier 2 Block 2); only 'clean' is implemented.",
            file=sys.stderr,
        )
        return 2

    kinds = sorted(KINDS) if args.kind == "all" else [args.kind]
    failed = 0
    for kind in kinds:
        try:
            documents, closes = write_set(kind, args.out, args.seed)
        except OSError as exc:
            print(f"Could not render {kind}: {exc}", file=sys.stderr)
            print(
                "On macOS WeasyPrint needs DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib; "
                "use `make docs`.",
                file=sys.stderr,
            )
            return 1

        accounts = {d.account_number for d in documents}
        by_year = Counter(d.factor_year for d in documents)
        target = args.out / kind
        rel = target.relative_to(PROJECT_ROOT) if target.is_relative_to(PROJECT_ROOT) else target

        print(f"\n{kind}: {len(documents)} documents written to {rel}  (seed {args.seed})")
        print(
            f"  {len(accounts)} accounts x {len(documents) // len(accounts)} months; "
            + ", ".join(f"{n} in {y}" for y, n in sorted(by_year.items()))
        )
        print(
            f"  tiers {dict(Counter(d.difficulty_tier for d in documents))}, "
            f"layouts {dict(Counter(d.layout_id for d in documents))}, quirks none"
        )
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
