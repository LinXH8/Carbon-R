"""Company-year scenarios: one business's documents for one reporting year (spec 4.5).

The calculation eval of Tier 2 Block 3 needs what single documents cannot give it: a YEAR,
so that a missing month, an estimated reading, a bill straddling the year's edge, a USD
invoice or a factor that is not published can each be seen in a roll-up and an allocation.

Every scenario is built from the v1 builders, templates and quirk injectors, so a scenario
document is a v1-style document. Three things are new:

- the scenario-only businesses in `profiles.py` (Makan Mates and its variants), kept out of
  the tuples the v1 planner walks, so v1 does not move (tests/test_synthgen_v1_stability);
- the `usd_invoice` quirk, on LPG, priced from its own fictional USD list;
- a meter-read cycle for a whole year (`elec_cycle_day`), so the first and last bills
  straddle the reporting year's edges.

What a scenario records is activity data and the facts an allocation needs (revenue and
contract revenue). It never looks up a factor or computes an emission: the eval does that
from these activities and the DB's verified factors (spec 3.4).

The holdout layout `d` is never used: the holdout is scored once, last, and a scenario
document on it would be a leak (a test asserts it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from carbon_r.synthgen import electricity, fuel_card, lpg
from carbon_r.synthgen.layouts import layout
from carbon_r.synthgen.plan import ELEC, FUEL, HOLDOUT_LAYOUT, LPG, PREFIX, DocPlan
from carbon_r.synthgen.profiles import (
    FUEL_CUSTOMERS,
    LPG_CUSTOMERS,
    SCENARIO_FUEL_CUSTOMERS,
    SCENARIO_LPG_CUSTOMERS,
    customer_by_key,
    incidental_details,
)
from carbon_r.synthgen.quirks import BUILT_IN, dress

# Its own seed, so a scenario bill for an account that also has v1 bills is not the same
# random draw as the v1 bill for that month.
SCENARIO_SEED = 20261020
GENERATOR_VERSION = "synthgen-scenario-1"
LAYOUT_CYCLE = ("a", "b", "c")  # never HOLDOUT_LAYOUT

assert HOLDOUT_LAYOUT not in LAYOUT_CYCLE


@dataclass(frozen=True)
class Company:
    """A business, the documents it produces, and the two figures allocation needs.

    Revenue and contract revenue are fictional and exist only to give an allocation a
    stated basis. `contract_revenue_sgd` is what one buyer paid this business in the year;
    the eval allocates the business's Scope 1 + 2 by that share of its revenue.
    """

    key: str
    industry: str
    revenue_sgd: Decimal
    contract_revenue_sgd: Decimal
    lpg_account: str | None = None
    fuel_account: str | None = None


COMPANIES: dict[str, Company] = {
    company.key: company
    for company in (
        Company(
            "makan_mates",
            "restaurant kitchen",
            Decimal("2400000"),
            Decimal("720000"),
            lpg_account="GAS-5520-11",
            fuel_account="FLT-5520-11",
        ),
        Company(
            "lion_city_laundry",
            "commercial laundry",
            Decimal("1850000"),
            Decimal("370000"),
            lpg_account="GAS-7718-02",
            fuel_account="FLT-7741-03",
        ),
        Company(
            "kranji_cold_store",
            "cold store",
            Decimal("5200000"),
            Decimal("1300000"),
            fuel_account="FLT-3309-02",
        ),
        Company(
            "tampines_print_works",
            "print works",
            Decimal("1100000"),
            Decimal("165000"),
            lpg_account="GAS-5520-09",
        ),
        Company("serangoon_dental", "dental clinic", Decimal("950000"), Decimal("95000")),
        Company(
            "jurong_auto",
            "vehicle workshop",
            Decimal("1600000"),
            Decimal("480000"),
            fuel_account="FLT-4487-01",
        ),
    )
}


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    title: str
    company: str
    reporting_year: int = 2025
    # The month the business started trading. Months before it are not gaps.
    active_from_month: int = 1
    # (kind, month) documents the business never handed over. Built and then withheld, so
    # the meter keeps running underneath the gap, as it would.
    omit: tuple[tuple[str, int], ...] = ()
    # (kind, month, quirk): injected quirks, applied by the v1 injectors.
    quirks: tuple[tuple[str, int, str], ...] = ()
    # Bill electricity on a meter cycle from this day, so the year's first bill starts
    # in December of the previous year and the last ends in January of the next.
    elec_cycle_day: int | None = None
    lpg_usd_months: tuple[int, ...] = ()
    fuel_account: str | None = None
    # Document kinds this scenario does not include, though the business has them.
    without: tuple[str, ...] = ()
    contract_revenue_sgd: Decimal | None = None
    # Also run end to end, through extraction (three scenarios; the rest are calculation
    # level only).
    end_to_end: bool = False
    note: str = ""
    tags: tuple[str, ...] = field(default=())

    @property
    def business(self) -> Company:
        return COMPANIES[self.company]

    @property
    def active_period(self) -> tuple[date, date]:
        return date(self.reporting_year, self.active_from_month, 1), date(
            self.reporting_year, 12, 31
        )

    @property
    def contract_revenue(self) -> Decimal:
        return (
            self.contract_revenue_sgd
            if self.contract_revenue_sgd is not None
            else self.business.contract_revenue_sgd
        )


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "makan_mates_2025",
        "Makan Mates, full year 2025",
        "makan_mates",
        end_to_end=True,
        note="The base case: twelve bills, twelve LPG invoices, twelve fuel statements.",
    ),
    Scenario(
        "makan_mates_missing_month",
        "Makan Mates, March electricity bill not supplied",
        "makan_mates",
        omit=((ELEC, 3),),
        end_to_end=True,
        note="The roll-up must list March as uncovered for electricity, not total eleven bills.",
    ),
    Scenario(
        "makan_mates_two_missing",
        "Makan Mates, two electricity bills and one fuel statement not supplied",
        "makan_mates",
        omit=((ELEC, 3), (ELEC, 8), (FUEL, 8)),
    ),
    Scenario(
        "makan_mates_estimated",
        "Makan Mates, June and July meter readings estimated",
        "makan_mates",
        quirks=((ELEC, 6, "estimated_reading"), (ELEC, 7, "estimated_reading")),
        end_to_end=True,
        note="Both documents must be listed as estimated in the roll-up.",
    ),
    Scenario(
        "makan_mates_mwh",
        "Makan Mates, three bills printed in MWh",
        "makan_mates",
        quirks=((ELEC, 2, "unit_mwh"), (ELEC, 5, "unit_mwh"), (ELEC, 11, "unit_mwh")),
    ),
    Scenario(
        "makan_mates_meter_cycle",
        "Makan Mates, billed on a 15th-to-14th meter cycle",
        "makan_mates",
        elec_cycle_day=15,
        note=(
            "Thirteen bills: the first starts 15 Dec 2024 and the last ends 14 Jan 2026. Only "
            "the 2025 days of each count toward 2025."
        ),
    ),
    Scenario(
        "makan_mates_credits",
        "Makan Mates, credits and balances forward across all three kinds",
        "makan_mates",
        quirks=(
            (ELEC, 4, "credit_adjustment"),
            (ELEC, 9, "balance_forward"),
            (LPG, 5, "credit_adjustment"),
            (LPG, 10, "balance_forward"),
            (FUEL, 6, "credit_adjustment"),
            (FUEL, 11, "balance_forward"),
        ),
        note="A credit's decoy quantity must never reduce the year's activity.",
    ),
    Scenario(
        "makan_mates_usd_lpg",
        "Makan Mates, April to June LPG invoiced in USD",
        "makan_mates",
        lpg_usd_months=(4, 5, 6),
        note="Currency changes the money only. The kg, and so the figure, are unaffected.",
    ),
    Scenario(
        "makan_mates_2024",
        "Makan Mates, reporting year 2024",
        "makan_mates",
        reporting_year=2024,
        # The fictional pump-price series does not cover early 2024, and extending it to
        # make this scenario possible would be inventing prices for a test's convenience.
        # The LPG invoices alone carry the no_results path.
        without=(FUEL,),
        note=(
            "DESNZ is loaded from 2025, so no LPG factor exists at or before 2024: every "
            "LPG invoice is no_results and must be named, never dropped."
        ),
    ),
    Scenario(
        "makan_mates_mixed_fuel",
        "Makan Mates, diesel van plus a petrol runabout",
        "makan_mates",
        fuel_account="FLT-5520-12",
    ),
    Scenario(
        "makan_mates_partial_year",
        "Makan Mates, trading from July 2025",
        "makan_mates",
        active_from_month=7,
        note="January to June are before the business existed, not gaps.",
    ),
    Scenario(
        "makan_mates_share_0",
        "Makan Mates, contract share 0%",
        "makan_mates",
        contract_revenue_sgd=Decimal("0"),
    ),
    Scenario(
        "makan_mates_share_1",
        "Makan Mates, contract share 100%",
        "makan_mates",
        contract_revenue_sgd=Decimal("2400000"),
    ),
    Scenario(
        "makan_mates_gst_headline",
        "Makan Mates, GST-inclusive headlines on every kind",
        "makan_mates",
        quirks=((ELEC, 1, "gst_confusion"), (LPG, 2, "gst_confusion"), (FUEL, 3, "gst_confusion")),
    ),
    Scenario(
        "makan_mates_lpg_quiet_month",
        "Makan Mates, no LPG invoice for August",
        "makan_mates",
        omit=((LPG, 8),),
        note=(
            "One month without an LPG invoice is plausible usage - a kitchen can run a month "
            "on cylinders already on site - so it is NOT flagged (LPG_MAX_GAP_DAYS)."
        ),
    ),
    Scenario(
        "makan_mates_lpg_long_gap",
        "Makan Mates, no LPG invoices for April to June",
        "makan_mates",
        omit=((LPG, 4), (LPG, 5), (LPG, 6)),
        note="Ninety-one days without gas is a gap, and is flagged.",
    ),
    Scenario("lion_city_laundry_2025", "Lion City Laundry, full year 2025", "lion_city_laundry"),
    Scenario("kranji_cold_store_2025", "Kranji Cold Store, full year 2025", "kranji_cold_store"),
    Scenario(
        "tampines_print_works_2025", "Tampines Print Works, full year 2025", "tampines_print_works"
    ),
    Scenario(
        "serangoon_dental_2025",
        "Serangoon Dental, full year 2025 - electricity only",
        "serangoon_dental",
        note="No Scope 1 documents: Scope 1 is reported as nothing found, not as zero.",
    ),
    Scenario("jurong_auto_2025", "Jurong Auto, full year 2025, mixed fleet", "jurong_auto"),
)


def scenario_by_id(scenario_id: str) -> Scenario:
    for scenario in SCENARIOS:
        if scenario.scenario_id == scenario_id:
            return scenario
    raise KeyError(f"No scenario {scenario_id!r}")


# --- building -----------------------------------------------------------------------------


@dataclass
class _Counter:
    """Per-kind document numbering and layout rotation within one scenario."""

    numbers: dict[str, int] = field(default_factory=dict)

    def next(self, kind: str) -> tuple[int, str]:
        n = self.numbers.get(kind, 0) + 1
        self.numbers[kind] = n
        return n, LAYOUT_CYCLE[(n - 1) % len(LAYOUT_CYCLE)]


def _injected(scenario: Scenario, kind: str, month: int) -> tuple[str, ...]:
    return tuple(q for k, m, q in scenario.quirks if k == kind and m == month)


def _tier(scenario: Scenario, kind: str, month: int) -> str:
    """As in v1: a document with no injected quirk is clean; one or two is moderate."""
    return "moderate" if _injected(scenario, kind, month) else "clean"


def _dressed(document, scenario: Scenario, kind: str, number: int, letter: str, month: int, seed):
    """Apply this scenario's injected quirks for (kind, month), through the v1 `dress`."""
    injected = _injected(scenario, kind, month)
    built = tuple(q for q in document.quirks if q in BUILT_IN)
    tier = _tier(scenario, kind, month)
    plan = DocPlan(
        kind=kind,
        number=number,
        account=document.account_number,
        group="series",
        year=document.period_end.year,
        month=month,
        layout=letter,
        tier=tier,
        quirks=built + injected,
    )
    return dress(document, plan, seed)


def _doc_id(kind: str, number: int) -> str:
    return f"{PREFIX[kind]}_{number:03d}"


def _electricity(scenario: Scenario, seed: int, counter: _Counter) -> list:
    profile = customer_by_key(scenario.company)
    address, meter = incidental_details(profile, seed)
    year = scenario.reporting_year
    reading = electricity.opening_reading_kwh(profile, seed)
    months = range(scenario.active_from_month, 13)

    periods: list[tuple[int, tuple[date, date] | None]] = []
    if scenario.elec_cycle_day is None:
        periods = [(m, None) for m in months]
    else:
        day = scenario.elec_cycle_day
        starts = [date(year - 1, 12, day)] + [date(year, m, day) for m in months]
        for start, nxt in zip(starts, [*starts[1:], date(year + 1, 1, day)], strict=True):
            periods.append((nxt.month if nxt.year == year else 12, (start, nxt - timedelta(1))))
        periods[-1] = (13, periods[-1][1])  # the bill that ends in the next year

    documents = []
    for month, period in periods:
        number, letter = counter.next(ELEC)
        bill_year, bill_month = (period[1].year, period[1].month) if period else (year, month)
        bill = electricity.build_bill(
            profile,
            bill_year,
            bill_month,
            reading,
            _doc_id(ELEC, number),
            seed,
            customer_address=address,
            meter_number=meter,
            period=period,
            layout_id=f"elec_{letter}",
            is_holdout_layout=layout(f"elec_{letter}").is_holdout,
            difficulty_tier=_tier(scenario, ELEC, month),
        )
        reading = bill.current_reading_kwh
        if (ELEC, month) in scenario.omit:
            continue
        documents.append(_dressed(bill, scenario, ELEC, number, letter, month, seed))
    return documents


def _lpg_customer(account: str):
    for customer in SCENARIO_LPG_CUSTOMERS + LPG_CUSTOMERS:
        if customer.account_number == account:
            return customer
    raise KeyError(f"No LPG customer {account!r}")


def _fuel_customer(account: str):
    for customer in SCENARIO_FUEL_CUSTOMERS + FUEL_CUSTOMERS:
        if customer.account_number == account:
            return customer
    raise KeyError(f"No fuel customer {account!r}")


def _lpg(scenario: Scenario, seed: int, counter: _Counter) -> list:
    account = scenario.business.lpg_account
    if account is None or LPG in scenario.without:
        return []
    customer = _lpg_customer(account)
    address, _ = incidental_details(customer_by_key(customer.key), seed)
    year = scenario.reporting_year
    first_day = date(year, scenario.active_from_month, 1)

    # (month, period): one per calendar month for a monthly customer; for a per-delivery
    # customer, one per visit, chained from the day after the last, with the year's last
    # visit cut at 31 December - an LPG period may not cross a year (it is not pro-rated).
    if customer.billing_basis == "per_delivery":
        visits: list[tuple[int, tuple[date, date] | None]] = []
        last_visit = first_day - timedelta(days=1)
        while last_visit < date(year, 12, 31):
            start, end = lpg.delivery_period(seed, f"{account}-{len(visits)}", last_visit)
            end = min(end, date(year, 12, 31))
            visits.append((end.month, (start, end)))
            last_visit = end
    else:
        visits = [(month, None) for month in range(scenario.active_from_month, 13)]

    documents = []
    previous_cylinders = 0
    for month, period in visits:
        number, letter = counter.next(LPG)
        shape = layout(f"lpg_{letter}")
        invoice = lpg.build_invoice(
            customer,
            year,
            month,
            _doc_id(LPG, number),
            seed,
            customer_address=address,
            previous_cylinders=previous_cylinders,
            period=period,
            layout_id=f"lpg_{letter}",
            is_holdout_layout=shape.is_holdout,
            difficulty_tier=_tier(scenario, LPG, month),
            prints_total_kg=shape.prints_total_kg,
            currency="USD" if month in scenario.lpg_usd_months else "SGD",
        )
        previous_cylinders = invoice.cylinder_count
        if (LPG, month) in scenario.omit:
            continue
        documents.append(_dressed(invoice, scenario, LPG, number, letter, month, seed))
    return documents


def _fuel(scenario: Scenario, seed: int, counter: _Counter) -> list:
    account = scenario.fuel_account or scenario.business.fuel_account
    if account is None or FUEL in scenario.without:
        return []
    customer = _fuel_customer(account)
    address, _ = incidental_details(customer_by_key(customer.key), seed)
    documents = []
    for month in range(scenario.active_from_month, 13):
        number, letter = counter.next(FUEL)
        statement = fuel_card.build_statement(
            customer,
            scenario.reporting_year,
            month,
            _doc_id(FUEL, number),
            seed,
            customer_address=address,
            layout_id=f"fuel_{letter}",
            is_holdout_layout=layout(f"fuel_{letter}").is_holdout,
            difficulty_tier=_tier(scenario, FUEL, month),
        )
        if (FUEL, month) in scenario.omit:
            continue
        documents.append(_dressed(statement, scenario, FUEL, number, letter, month, seed))
    return documents


def build_scenario(scenario: Scenario, seed: int = SCENARIO_SEED) -> list:
    """Every document the business handed over for the year, dressed, in kind order."""
    counter = _Counter()
    return [
        *_electricity(scenario, seed, counter),
        *_lpg(scenario, seed, counter),
        *_fuel(scenario, seed, counter),
    ]


__all__ = [
    "COMPANIES",
    "GENERATOR_VERSION",
    "LAYOUT_CYCLE",
    "SCENARIOS",
    "SCENARIO_SEED",
    "Company",
    "Scenario",
    "build_scenario",
    "scenario_by_id",
]
