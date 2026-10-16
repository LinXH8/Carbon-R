"""Generate synthetic source documents with their answer keys. Deterministic for a seed.

Layout follows SYNTHGEN_SPEC.md 3.2:

    data/generated/<doc_kind>/<tier>/doc_XXX.pdf + doc_XXX.json
    data/generated/<doc_kind>/manifest.json

v0 produces the `clean` tier only; `moderate` and `hard` arrive with the degradation
pipeline in Tier 2 Block 2, which is why --tiers already exists and rejects the others
explicitly rather than silently producing nothing.

    uv run python scripts/generate_docs.py        (or: make docs)

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
from carbon_r.money import round2
from carbon_r.synthgen.electricity import (
    DEFAULT_SEED,
    DOC_KIND,
    GENERATOR_VERSION,
    build_bill_series,
)
from carbon_r.synthgen.render import render_html, template_for, write_pdf

GENERATED_ROOT = PROJECT_ROOT / "data" / "generated"
KINDS = {DOC_KIND: build_bill_series}
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


def arithmetic_closes(bill: ElectricityBill) -> bool:
    """Recompute the printed total from the key's raw inputs, independently of the model.

    Deliberately does not call the validator: a validator cannot be its own witness
    (spec 1.3).
    """
    energy = round2(Decimal(bill.consumption_kwh) * bill.tariff_cents_per_kwh / Decimal(100))
    subtotal = energy + bill.fixed_charge_sgd
    gst = round2(subtotal * bill.gst_rate)
    return (subtotal + gst) == bill.total_sgd


def write_set(kind: str, out_root: Path, seed: int) -> tuple[list[ElectricityBill], int]:
    documents = KINDS[kind](seed)

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
        "generator_version": GENERATOR_VERSION,
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
                "consumption_kwh": d.consumption_kwh,
                "tariff_plan_code": d.tariff_plan_code,
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
    parser.add_argument("--kind", choices=sorted(KINDS), default=DOC_KIND)
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

    try:
        documents, closes = write_set(args.kind, args.out, args.seed)
    except OSError as exc:
        print(f"Could not render: {exc}", file=sys.stderr)
        print(
            "On macOS WeasyPrint needs DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib; "
            "use `make docs`.",
            file=sys.stderr,
        )
        return 1

    accounts = {d.account_number for d in documents}
    by_year = Counter(d.factor_year for d in documents)
    target = args.out / args.kind
    rel = target.relative_to(PROJECT_ROOT) if target.is_relative_to(PROJECT_ROOT) else target

    print(f"\n{args.kind}: {len(documents)} documents written to {rel}  (seed {args.seed})")
    print(
        f"  {len(accounts)} accounts x {len(documents) // len(accounts)} months; "
        + ", ".join(f"{n} in {y}" for y, n in sorted(by_year.items()))
    )
    print(
        f"  tiers {dict(Counter(d.difficulty_tier for d in documents))}, "
        f"layouts {dict(Counter(d.layout_id for d in documents))}, quirks none"
    )
    print(f"  arithmetic closes to the cent on {closes} of {len(documents)}")
    print(
        f"  consumption {min(d.consumption_kwh for d in documents):,}"
        f" - {max(d.consumption_kwh for d in documents):,} kWh"
    )
    return 0 if closes == len(documents) else 1


if __name__ == "__main__":
    raise SystemExit(main())
