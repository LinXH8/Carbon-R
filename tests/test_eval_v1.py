"""The eval's Tier 2 Block 2 plumbing: discovery, the holdout rule, per-year activities.

Offline: a manifest is written to a temporary directory, and nothing calls a model or the
database. The scoring itself is unchanged from Block 1 and tested where it was.
"""

import dataclasses
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from carbon_r.synthgen import electricity
from carbon_r.synthgen.plan import ELEC, FUEL, LPG, plan_kind
from eval_extraction import (
    KINDS,
    compare_activities,
    compare_fields,
    discover,
    read_activities,
    select,
    summarise,
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
    assert verdict["credit_adjustment_sgd"]["expected"] == str(bill.credit_amount_sgd)


def test_a_credit_on_the_key_is_compared_against_the_reading():
    # The Block 2 defect: the key was looked up under the reading's name, so "expected"
    # was always empty and a document WITH a credit could only fail.
    bill = next(b for b in electricity.build_v1() if b.credit_amount_sgd)

    class Right:
        credit_adjustment_sgd = bill.credit_amount_sgd

    class Absent:
        credit_adjustment_sgd = None

    spec = KINDS["electricity_bill"]
    assert compare_fields(bill, Right(), spec)["credit_adjustment_sgd"]["correct"]
    assert not compare_fields(bill, Absent(), spec)["credit_adjustment_sgd"]["correct"]


def test_every_scored_field_exists_on_the_answer_key():
    for kind, spec in KINDS.items():
        missing = [
            f for f in spec.scored_fields if spec.key_field(f) not in spec.key_model.model_fields
        ]
        assert not missing, f"{kind}: {missing}"


def test_a_scored_field_missing_from_the_key_raises_rather_than_defaulting():
    bill = next(iter(electricity.build_v1()))
    spec = KINDS["electricity_bill"]
    broken = dataclasses.replace(spec, key_fields={"credit_adjustment_sgd": "no_such_field"})
    with pytest.raises(KeyError, match="no_such_field"):
        compare_fields(bill, None, broken)


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


def _row(kind: str, tier: str, quirks: list[str], *, fields_ok: bool, cached: bool = True):
    return {
        "true_kind": kind,
        "difficulty_tier": tier,
        "quirks": quirks,
        "fields_scored": 2,
        "fields_correct": 2 if fields_ok else 1,
        "activity": {"correct": True},
        "pipeline_exact": True,
        "cached": cached,
        "usage": [{"input_tokens": 10, "output_tokens": 3}],
    }


def test_summary_counts_per_tier_and_per_quirk_and_marks_small_cells():
    rows = [
        _row("electricity_bill", "clean", [], fields_ok=True),
        _row("electricity_bill", "hard", ["unit_mwh", "cross_year_period"], fields_ok=False),
        _row("electricity_bill", "hard", ["unit_mwh"], fields_ok=True, cached=False),
    ]
    summary = summarise(rows)
    hard = summary["by_tier"]["hard"]["electricity_bill"]
    assert (hard["documents"], hard["every_field"], hard["pipeline"]) == (2, 1, 2)
    assert hard["small_n"]
    # A document counts once in each of its quirks' rows.
    assert summary["by_quirk"]["unit_mwh"]["electricity_bill"]["documents"] == 2
    assert summary["by_quirk"]["cross_year_period"]["electricity_bill"]["documents"] == 1
    # Only the fresh row is this run's cost.
    assert summary["tokens_this_run"] == {"calls": 1, "input_tokens": 10, "output_tokens": 3}


def test_a_factor_lookup_error_is_retried_never_memoised_and_finally_aborts(monkeypatch):
    import eval_extraction
    from carbon_r.tools.base import error, ok

    answers = iter([error("server closed the connection"), ok({"factor": {}}, "found")])
    calls = []

    def lookup(category, unit, year):
        calls.append(year)
        return next(answers)

    monkeypatch.setattr(eval_extraction, "lookup_emission_factor", lookup)
    monkeypatch.setattr(eval_extraction, "_FACTORS", {})
    monkeypatch.setattr(eval_extraction, "LOOKUP_RETRIES", [])
    assert eval_extraction._factor("c", "u", 2024).status == "ok"  # retried, not scored
    assert eval_extraction._factor("c", "u", 2024).status == "ok"  # now memoised
    assert calls == [2024, 2024]
    assert len(eval_extraction.LOOKUP_RETRIES) == 1

    monkeypatch.setattr(eval_extraction, "lookup_emission_factor", lambda *a: error("down"))
    with pytest.raises(eval_extraction.LookupFailed):
        eval_extraction._factor("c", "u", 2025)
