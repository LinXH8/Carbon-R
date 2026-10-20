"""The company-year roll-up and allocation (Tier 2 Block 3). No model, no database.

Every rule here is one the roll-up must never break quietly (CLAUDE.md rule 7): a day
outside the year counted, a document it could not cost dropped, a missing month read as a
quiet one, no document reported as zero.

The last section runs the scenario eval itself against a stand-in factor table and shows
it FAILS when the roll-up is broken - an eval that passes everything on its first run has
to be shown able to fail before its passes mean anything.
"""

from datetime import date
from decimal import Decimal

import pytest

import eval_scenarios
from carbon_r.synthgen.plan import HOLDOUT_LAYOUT
from carbon_r.synthgen.scenario import SCENARIOS, build_scenario, scenario_by_id
from carbon_r.tools import company_year
from carbon_r.tools.base import no_results, ok
from carbon_r.tools.company_year import (
    ALLOCATION_ASSUMPTION,
    LPG_MAX_GAP_DAYS,
    DocumentFigure,
    allocate,
    roll_up,
)
from conftest import FACTOR_2024, FACTOR_LPG_2026

YEAR = 2025


def _line(kind, kg, scope, year=YEAR, *, substituted=False, jurisdiction="SG", estimated=False):
    return {
        "kind": kind,
        "kg_co2e_exact": str(kg),
        "derivation": f"{kg} kg",
        "scope": scope,
        "method": "location-based" if scope == 2 else "activity-based",
        "gas_basis": "CO2" if scope == 2 else "CO2e",
        "activity": {"factor_year": year, "is_estimated": estimated},
        "factor": {
            "year_used": year - 1 if substituted else year,
            "substituted": substituted,
            "publisher": "EMA" if scope == 2 else "DESNZ",
            "jurisdiction": jurisdiction,
        },
    }


def _bill(month, kg="100.0005", **line):
    start = date(YEAR, month, 1)
    end = date(YEAR + month // 12, month % 12 + 1, 1).replace(day=1)
    end = date.fromordinal(end.toordinal() - 1)
    return DocumentFigure(
        document_id=f"elec_{month:03d}",
        document_kind="electricity_bill",
        period_start=start,
        period_end=end,
        status="ok",
        lines=[_line("electricity", kg, 2, **line)],
    )


def _lpg(doc_id, start, end, kg="10", status="ok"):
    return DocumentFigure(
        document_id=doc_id,
        document_kind="lpg_invoice",
        period_start=start,
        period_end=end,
        status=status,
        message="" if status == "ok" else "No fuel_lpg factor is published for 2024",
        lines=[_line("lpg", kg, 1)] if status == "ok" else [],
    )


def test_a_full_year_sums_exact_products_and_rounds_once():
    # 12 x 100.0005 = 1200.006 exactly; rounding each line first would give 1200.012.
    result = roll_up([_bill(m) for m in range(1, 13)], reporting_year=YEAR)
    assert result.status == "ok"
    assert result.data["scopes"]["2"]["kg_co2e"] == "1200.006"
    assert result.data["gaps"] == {}
    assert result.data["disclosures"]["not_assured"]


def test_a_missing_monthly_document_is_a_listed_gap():
    result = roll_up([_bill(m) for m in range(1, 13) if m != 3], reporting_year=YEAR)
    assert result.data["gaps"] == {"electricity_bill": ["2025-03"]}
    assert result.data["disclosures"]["coverage_gap"]
    assert "gaps" in result.message


def test_months_before_the_business_started_are_not_gaps():
    result = roll_up(
        [_bill(m) for m in range(7, 13)],
        reporting_year=YEAR,
        active_period=(date(YEAR, 7, 1), date(YEAR, 12, 31)),
    )
    assert result.data["gaps"] == {}


def test_one_quiet_lpg_month_is_not_a_gap_but_a_long_run_is():
    months = [(1, 31), (2, 28), (3, 31), (5, 31)]  # April missing: 30 days
    invoices = [_lpg(f"lpg_{m}", date(YEAR, m, 1), date(YEAR, m, d)) for m, d in months] + [
        _lpg("lpg_12", date(YEAR, 12, 1), date(YEAR, 12, 31))
    ]  # June-November: 183 days
    result = roll_up(invoices, reporting_year=YEAR)
    gaps = result.data["coverage"]["lpg_invoice"]
    assert [g["days"] for g in gaps["gaps"]] == [183]
    assert [g["days"] for g in gaps["uncovered_not_flagged"]] == [30]
    assert 30 <= LPG_MAX_GAP_DAYS < 183


def test_a_document_that_could_not_be_costed_is_named_and_its_scope_partial():
    docs = [
        _bill(1),
        _lpg("lpg_001", date(YEAR, 1, 1), date(YEAR, 1, 31)),
        _lpg("lpg_002", date(YEAR, 2, 1), date(YEAR, 2, 28), status="no_results"),
    ]
    result = roll_up(docs, reporting_year=YEAR, active_period=(date(YEAR, 1, 1), date(YEAR, 2, 28)))
    assert [e["document_id"] for e in result.data["excluded"]] == ["lpg_002"]
    assert result.data["excluded"][0]["reason"]
    assert result.data["scopes"]["1"]["partial"]
    assert "could not be costed" in result.data["scopes"]["1"]["note"]
    assert not result.data["scopes"]["2"]["partial"]


def test_a_scope_with_no_document_has_no_figure_not_zero():
    result = roll_up([_bill(m) for m in range(1, 13)], reporting_year=YEAR)
    assert result.data["scopes"]["1"]["kg_co2e"] is None
    assert "not zero" in result.data["scopes"]["1"]["note"]


def test_days_outside_the_reporting_year_are_listed_not_counted():
    straddle = DocumentFigure(
        document_id="elec_001",
        document_kind="electricity_bill",
        period_start=date(YEAR - 1, 12, 15),
        period_end=date(YEAR, 1, 14),
        status="ok",
        lines=[_line("electricity", "70", 2, YEAR - 1), _line("electricity", "50", 2)],
    )
    result = roll_up(
        [straddle], reporting_year=YEAR, active_period=(date(YEAR, 1, 1), date(YEAR, 1, 14))
    )
    assert result.data["scopes"]["2"]["kg_co2e"] == "50.000"
    assert [o["activity_year"] for o in result.data["outside_reporting_year"]] == [YEAR - 1]


def test_estimates_substitutions_and_currency_are_listed():
    docs = [_bill(m, substituted=True, estimated=(m == 6)) for m in range(1, 13)]
    docs[0] = docs[0].model_copy(update={"currency": "USD"})
    result = roll_up(docs, reporting_year=YEAR)
    assert result.data["estimated_documents"] == ["elec_006"]
    assert len(result.data["substitutions"]) == 12
    assert result.data["non_sgd_documents"] == [{"document_id": "elec_001", "currency": "USD"}]


def test_nothing_costed_is_no_results_with_the_reasons():
    result = roll_up(
        [_lpg("lpg_001", date(YEAR, 1, 1), date(YEAR, 1, 31), status="no_results")],
        reporting_year=YEAR,
    )
    assert result.status == "no_results"
    assert result.data["excluded"][0]["document_id"] == "lpg_001"


# --- allocation ---------------------------------------------------------------------------


def _year():
    return roll_up([_bill(m, kg="1000") for m in range(1, 13)], reporting_year=YEAR).data


@pytest.mark.parametrize(
    ("contract", "allocated"), [("0", "0.000"), ("720000", "3600.000"), ("2400000", "12000.000")]
)
def test_allocation_by_revenue_share_carries_its_inputs(contract, allocated):
    result = allocate(
        _year(),
        contract_revenue_sgd=Decimal(contract),
        total_revenue_sgd=Decimal("2400000"),
        share_source="test",
    )
    assert result.status == "ok"
    assert result.data["allocated_kg_co2e"] == allocated
    assert result.data["allocates"] == "Scope 1 + Scope 2"
    assert result.data["assumption"] == ALLOCATION_ASSUMPTION
    assert result.data["contract_revenue_sgd"] == contract


def test_allocation_multiplies_before_dividing():
    # 1/3 of 12000.001: dividing first by 0.333333 would lose the last gram.
    data = roll_up(
        [_bill(1, kg="12000.001")],
        reporting_year=YEAR,
        active_period=(date(YEAR, 1, 1), date(YEAR, 1, 31)),
    ).data
    result = allocate(
        data, contract_revenue_sgd=Decimal(1), total_revenue_sgd=Decimal(3), share_source="t"
    )
    assert result.data["allocated_kg_co2e"] == "4000.000"


@pytest.mark.parametrize(("contract", "revenue"), [("-1", "10"), ("11", "10"), ("1", "0")])
def test_a_share_outside_zero_to_one_is_an_error(contract, revenue):
    result = allocate(
        _year(),
        contract_revenue_sgd=Decimal(contract),
        total_revenue_sgd=Decimal(revenue),
        share_source="t",
    )
    assert result.status == "error"


def test_allocation_inherits_the_roll_ups_disclosures():
    data = roll_up([_bill(m) for m in range(1, 13) if m != 3], reporting_year=YEAR).data
    result = allocate(
        data, contract_revenue_sgd=Decimal(1), total_revenue_sgd=Decimal(2), share_source="t"
    )
    assert "coverage_gap" in result.data["inherits"]


# --- scenarios ------------------------------------------------------------------------------


def test_no_scenario_document_uses_the_holdout_layout():
    for scenario in SCENARIOS:
        for document in build_scenario(scenario):
            assert not document.is_holdout_layout, (scenario.scenario_id, document.doc_id)
            assert not document.layout_id.endswith(f"_{HOLDOUT_LAYOUT}")


def test_usd_appears_only_where_a_scenario_asks_for_it():
    for scenario in SCENARIOS:
        usd = {d.doc_id for d in build_scenario(scenario) if d.currency == "USD"}
        assert bool(usd) == bool(scenario.lpg_usd_months), scenario.scenario_id
        for document in build_scenario(scenario):
            if document.currency == "USD":
                assert "usd_invoice" in document.quirks


def _stand_in_factors(monkeypatch):
    """Exact-year factors for every activity, except no LPG or fuel before 2025 - the
    shape of the real table, without the database."""

    def factor(category, unit, year):
        if category != "electricity_grid_sg" and year < 2025:
            return no_results(f"No {category} factor is published for {year} or earlier.")
        base = FACTOR_2024 if category == "electricity_grid_sg" else FACTOR_LPG_2026
        row = dict(
            base,
            category=category,
            unit=unit,
            year_requested=year,
            year_used=year,
            substituted=False,
            substitution_note=None,
        )
        if category in ("fuel_diesel", "fuel_petrol"):
            row["factor"] = "2.5"
        return ok({"factor": row}, "stand-in")

    monkeypatch.setattr(eval_scenarios, "_factor", factor)


@pytest.mark.parametrize(
    "scenario_id",
    [
        "makan_mates_missing_month",
        "makan_mates_estimated",
        "makan_mates_2024",
        "serangoon_dental_2025",
    ],
)
def test_the_scenario_eval_passes_a_correct_roll_up(monkeypatch, scenario_id):
    _stand_in_factors(monkeypatch)
    result = eval_scenarios.evaluate_scenario(scenario_by_id(scenario_id))
    assert result["passed"], {k: v for k, v in result["checks"].items() if not v}


def test_the_scenario_eval_fails_a_roll_up_that_hides_gaps_and_exclusions(monkeypatch):
    _stand_in_factors(monkeypatch)
    real = company_year.roll_up

    def quiet(documents, **kwargs):
        result = real([d for d in documents if d.status == "ok"], **kwargs)
        data = result.data | {
            "gaps": {},
            "disclosures": result.data["disclosures"] | {"coverage_gap": False},
        }
        return ok(data, result.message)

    monkeypatch.setattr(eval_scenarios, "roll_up", quiet)
    missing = eval_scenarios.evaluate_scenario(scenario_by_id("makan_mates_missing_month"))
    assert not missing["checks"]["gaps_exact"]
    refused = eval_scenarios.evaluate_scenario(scenario_by_id("makan_mates_2024"))
    assert not refused["checks"]["excluded_named"]


def test_the_scenario_eval_fails_a_roll_up_that_counts_the_neighbouring_year(monkeypatch):
    _stand_in_factors(monkeypatch)
    real = company_year.roll_up

    def greedy(documents, *, reporting_year, active_period=None):
        relabelled = [
            d.model_copy(
                update={
                    "lines": [
                        line | {"activity": line["activity"] | {"factor_year": reporting_year}}
                        for line in d.lines
                    ]
                }
            )
            for d in documents
        ]
        return real(relabelled, reporting_year=reporting_year, active_period=active_period)

    monkeypatch.setattr(eval_scenarios, "roll_up", greedy)
    result = eval_scenarios.evaluate_scenario(scenario_by_id("makan_mates_meter_cycle"))
    assert not result["checks"]["scope_2_exact"]
    assert not result["checks"]["outside_year_listed"]


def test_every_scenario_passes_against_the_real_factor_table():
    """The measured run, against the DB. Skipped without DATABASE_URL, like the other
    DB-backed test; Block 5's CI seeds Postgres so it runs there."""
    from carbon_r.config import get_settings

    if not get_settings().database_configured:
        pytest.skip("DATABASE_URL not set")
    run = eval_scenarios.run_all()
    failed = {r["scenario_id"]: r["checks"] for r in run["scenarios"] if not r["passed"]}
    assert not failed
