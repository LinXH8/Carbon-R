"""The eval's Tier 2 Block 2 plumbing: discovery, the holdout rule, per-year activities.

Offline: a manifest is written to a temporary directory, and nothing calls a model or the
database. The scoring itself is unchanged from Block 1 and tested where it was.
"""

import json
import re
from decimal import Decimal
from pathlib import Path

from carbon_r.synthgen import electricity
from carbon_r.synthgen.plan import ELEC, FUEL, LPG, plan_kind
from eval_extraction import (
    KINDS,
    compare_activities,
    compare_fields,
    discover,
    read_activities,
    select,
)

SEED = electricity.DEFAULT_SEED


def _manifest(root: Path, kind: str, documents: list[dict]) -> None:
    (root / kind).mkdir(parents=True)
    (root / kind / "manifest.json").write_text(json.dumps({"documents": documents}))


def test_discovery_follows_the_manifest_file_and_flags_the_holdout(tmp_path):
    _manifest(
        tmp_path,
        "electricity_bill",
        [
            {"doc_id": "elec_020", "file": "moderate/elec_020.png", "is_holdout_layout": False},
            {"doc_id": "elec_022", "file": "hard/elec_022.png", "is_holdout_layout": True},
        ],
    )
    refs = discover(tmp_path, "electricity_bill")
    assert [r.page.name for r in refs] == ["elec_020.png", "elec_022.png"]
    assert refs[0].key == tmp_path / "electricity_bill" / "moderate" / "elec_020.json"
    assert [r.is_holdout for r in refs] == [False, True]


def test_the_block1_manifest_without_file_entries_still_resolves(tmp_path):
    _manifest(tmp_path, "lpg_invoice", [{"doc_id": "lpg_001"}])
    (ref,) = discover(tmp_path, "lpg_invoice")
    assert ref.page == tmp_path / "lpg_invoice" / "clean" / "lpg_001.pdf"
    assert not ref.is_holdout


def test_the_holdout_is_excluded_by_default_even_when_named(tmp_path):
    _manifest(
        tmp_path,
        "electricity_bill",
        [
            {"doc_id": "elec_020", "file": "moderate/elec_020.png"},
            {"doc_id": "elec_022", "file": "hard/elec_022.png", "is_holdout_layout": True},
        ],
    )
    refs = discover(tmp_path, "electricity_bill")
    assert [r.doc_id for r in select(refs, docs=None, include_holdout=False)] == ["elec_020"]
    assert select(refs, docs="elec_022", include_holdout=False) == []
    assert [r.doc_id for r in select(refs, docs=None, include_holdout=True)] == [
        "elec_020",
        "elec_022",
    ]


def test_activities_are_compared_by_kind_and_year():
    """A cross-year bill read as one 2023 activity is a miss even with the right kWh."""
    expected = {("electricity", 2023): Decimal(4645), ("electricity", 2024): Decimal(4355)}
    split = [
        {"kind": "electricity", "amount": "4645", "unit": "kWh", "factor_year": 2023},
        {"kind": "electricity", "amount": "4355", "unit": "kWh", "factor_year": 2024},
    ]
    unsplit = [{"kind": "electricity", "amount": "9000", "unit": "kWh", "factor_year": 2023}]
    assert compare_activities(expected, split)["correct"]
    assert not compare_activities(expected, unsplit)["correct"]


def test_two_grades_of_one_fuel_in_one_year_are_summed_like_the_key():
    found = [
        {"kind": "petrol", "amount": "10.5", "unit": "litres", "factor_year": 2026},
        {"kind": "petrol", "amount": "4.5", "unit": "litres", "factor_year": 2026},
    ]
    assert read_activities(found) == {("petrol", 2026): Decimal("15.0")}


def test_a_key_split_matches_its_own_activities():
    bill = next(b for b in electricity.build_v1() if "cross_year_period" in b.quirks)
    expected = KINDS["electricity_bill"].key_activities(bill)
    assert len(expected) == 2
    assert sum(expected.values()) == bill.consumption_kwh


def test_an_absent_credit_scores_as_the_keys_zero():
    bill = next(b for b in electricity.build_v1() if not b.credit_amount_sgd)

    class Reading:
        credit_adjustment_sgd = None

    verdict = compare_fields(bill, Reading(), KINDS["electricity_bill"])
    assert verdict["credit_adjustment_sgd"]["correct"]


def test_flow_block2_names_no_holdout_document_and_crosses_published_years():
    makefile = (Path(__file__).parents[1] / "Makefile").read_text()
    line = next(text for text in makefile.splitlines() if "elec_068" in text and "--docs" in text)
    named = re.search(r"--docs (\S+)", line).group(1).split(",")
    plans = {p.doc_id: p for kind in (ELEC, LPG, FUEL) for p in plan_kind(kind, SEED)}
    assert named == ["elec_068", "elec_056", "lpg_029", "fuel_012"]
    assert all(not plans[doc_id].is_holdout for doc_id in named)
    bill = next(b for b in electricity.build_v1() if b.doc_id == "elec_068")
    # Both years are published EMA rows (0.412 and 0.402), so the two lines differ.
    assert sorted(bill.split_kwh_by_year) == [2023, 2024]
