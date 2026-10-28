"""Which documents exist in synthgen v1, and how each is dressed: the plan before the build.

Everything a document IS (an account, a period) is declared here as literal windows, and
everything it is DRESSED IN (layout, tier, injected quirks) is assigned here by fixed
rules. The kind modules then build each planned document. Keeping the two apart means the
whole set can be counted and checked - `generate_docs.py --plan` - before a single PDF is
rendered.

Assignment rules (Tier 2 Block 2 plan, Step 4):

- **Block 1 documents** (`elec_001..018`, `lpg_001..012`, `fuel_001..006`) stay clean, on
  layout `a`, with no injected quirk. Their numbers come from the profiles, as before.
- **Series documents** cycle layout a->b->c->d and tier clean->moderate->hard over the
  kind's series in document-number order. 4 and 3 are coprime, so every layout x tier
  cell is populated, including the held-out layout `d` in all three tiers.
- **Standalone period documents** (electricity only) are the bills whose period is not a
  calendar month: 15 across 31 December, 15 on a mid-month meter cycle. They are never on
  the held-out layout and never clean, because each already carries an injected quirk.
- **Injected quirks** are filled least-used first, counting non-holdout documents only,
  ties broken in catalogue order, visiting documents in number order. A document's choice
  therefore depends only on documents numbered before it, so a profile appended at the
  end - numbered after everything else - changes no existing document's dress.
- **How many:** moderate alternates 1, 2; hard takes 4 on electricity and every one of the
  kind's 3 on LPG and fuel (which have only 3 injected quirks - a confound recorded in
  docs/blocks/TIER2_BLOCK2.md).

Nothing here is random except the cycle day of a standalone period, drawn from the
document's own labelled stream so it cannot disturb any other draw.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from carbon_r.models.quirks import INJECTED_QUIRKS, injected_for
from carbon_r.synthgen.rng import doc_rng

ELEC = "electricity_bill"
LPG = "lpg_invoice"
FUEL = "fuel_card_statement"
PREFIX = {ELEC: "elec", LPG: "lpg", FUEL: "fuel"}

LAYOUTS = ("a", "b", "c", "d")
HOLDOUT_LAYOUT = "d"
TIERS = ("clean", "moderate", "hard")
MODERATE_QUIRK_COUNTS = (1, 2)
HARD_QUIRK_COUNT = {ELEC: 4, LPG: 3, FUEL: 3}

# The floor the --plan table is checked against: non-holdout documents per injected quirk,
# per kind. Below it, a per-quirk accuracy figure is too thin to state.
MIN_PER_QUIRK = 15

# Nothing is dated after this: the set is generated on 19 Oct 2026, and every period ends
# by September 2026 so every document date falls before it.
LAST_PERIOD_MONTH = (2026, 9)

# The years the factor table can serve, by category, as queried on 2026-10-19:
# electricity_grid_sg 2005-2024 (EMA), fuel_* 2025-2026 (DESNZ). With
# MAX_FACTOR_YEAR_GAP = 2, an electricity year up to 2026 resolves by substitution. A
# DB-backed test keeps this list honest; --plan uses it OFFLINE, before any rendering.
FACTOR_YEARS = {
    "electricity": range(2005, 2025),
    "lpg": range(2025, 2027),
    "diesel": range(2025, 2027),
    "petrol": range(2025, 2027),
}
MAX_FACTOR_YEAR_GAP = 2  # mirrors tools/factors.py; a test asserts they agree


@dataclass(frozen=True)
class Window:
    """Consecutive documents for one account, numbered from `first`."""

    kind: str
    account: str
    first: int
    start: tuple[int, int]  # (year, month) of the first document's period
    count: int


# --- the windows: literal, so the set is reviewable in one place -------------------------
#
# Block 2 series. Each starts after the account's Block 1 months, so meter readings and
# cylinder counts carry straight on, and no two documents for one account overlap.

SERIES: tuple[Window, ...] = (
    # electricity: 45 bills across 7 accounts
    Window(ELEC, "kopi_corner", 19, (2024, 4), 7),
    Window(ELEC, "lion_city_laundry", 26, (2024, 7), 7),
    Window(ELEC, "kranji_cold_store", 33, (2025, 1), 6),
    Window(ELEC, "tampines_print_works", 39, (2025, 5), 7),
    Window(ELEC, "serangoon_dental", 46, (2025, 8), 6),
    Window(ELEC, "jurong_auto", 52, (2025, 12), 6),
    Window(ELEC, "tampines_catering", 58, (2025, 10), 6),
    # LPG: 39 invoices; every period inside 2025-2026 (DESNZ years)
    Window(LPG, "kopi_corner", 13, (2025, 3), 10),
    Window(LPG, "lion_city_laundry", 23, (2025, 6), 11),
    Window(LPG, "tampines_catering", 34, (2026, 2), 8),
    Window(LPG, "tampines_print_works", 42, (2026, 1), 10),  # per delivery; continues lpg_012
    # fuel: 48 statements; every period inside 2025-2026
    Window(FUEL, "jurong_auto", 7, (2026, 2), 8),
    Window(FUEL, "lion_city_laundry", 15, (2025, 5), 9),
    Window(FUEL, "kopi_corner", 24, (2025, 1), 10),
    Window(FUEL, "kranji_cold_store", 34, (2025, 3), 10),
    Window(FUEL, "tampines_catering", 44, (2025, 10), 11),
)

# Standalone bills across 31 December: (account, December's year, number). Spread over
# four crossings and all seven accounts. 2022/23 and 2023/24 cross between two PUBLISHED
# EMA years with different values (0.417 / 0.412 / 0.402); 2024/25 and 2025/26 fall back
# to 2024 on one or both halves. Each avoids the account's other periods.
CROSS_YEAR: tuple[tuple[str, int, int], ...] = (
    ("kopi_corner", 2022, 64),
    ("kranji_cold_store", 2022, 65),
    ("serangoon_dental", 2022, 66),
    ("tampines_catering", 2022, 67),
    ("lion_city_laundry", 2023, 68),
    ("tampines_print_works", 2023, 69),
    ("jurong_auto", 2023, 70),
    ("tampines_catering", 2023, 71),
    ("kopi_corner", 2024, 72),
    ("tampines_print_works", 2024, 73),
    ("serangoon_dental", 2024, 74),
    ("jurong_auto", 2024, 75),
    ("lion_city_laundry", 2025, 76),
    ("kranji_cold_store", 2025, 77),
    ("kopi_corner", 2025, 78),
)

# Standalone mid-month meter cycles inside one year: (account, year, start month, number).
# In 2022-2023, before any account's monthly series, and ending by mid-November, so they
# cannot touch a December crossing.
CROSS_MONTH: tuple[tuple[str, int, int, int], ...] = (
    ("kopi_corner", 2022, 3, 79),
    ("lion_city_laundry", 2022, 5, 80),
    ("kranji_cold_store", 2022, 7, 81),
    ("tampines_print_works", 2022, 9, 82),
    ("serangoon_dental", 2022, 4, 83),
    ("jurong_auto", 2022, 6, 84),
    ("tampines_catering", 2022, 8, 85),
    ("kopi_corner", 2023, 10, 86),
    ("lion_city_laundry", 2023, 2, 87),
    ("kranji_cold_store", 2023, 4, 88),
    ("tampines_print_works", 2023, 6, 89),
    ("serangoon_dental", 2023, 8, 90),
    ("jurong_auto", 2023, 3, 91),
    ("tampines_catering", 2023, 5, 92),
    ("kopi_corner", 2023, 7, 93),
)

# A meter-read cycle starts on a day in this range (inclusive).
CYCLE_DAY_RANGE = (8, 22)


@dataclass(frozen=True)
class DocPlan:
    """One planned document: what it is, and how it is dressed."""

    kind: str
    number: int
    account: str
    group: str  # "block1" | "series" | "cross_year" | "cross_month"
    year: int
    month: int
    layout: str = "a"
    tier: str = "clean"
    quirks: tuple[str, ...] = ()
    # Only for standalone electricity bills: the period, when it is not a calendar month.
    period: tuple[date, date] | None = field(default=None, compare=False)
    # Position within its account's window (0-based); per-delivery LPG chains on it.
    offset: int = 0

    @property
    def doc_id(self) -> str:
        return f"{PREFIX[self.kind]}_{self.number:03d}"

    @property
    def layout_id(self) -> str:
        return f"{PREFIX[self.kind]}_{self.layout}"

    @property
    def is_holdout(self) -> bool:
        return self.layout == HOLDOUT_LAYOUT


def add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    index = (year * 12) + (month - 1) + offset
    return index // 12, index % 12 + 1


def cycle_day(seed: int, doc_id: str) -> int:
    return doc_rng(seed, doc_id, "period").randint(*CYCLE_DAY_RANGE)


def standalone_period(seed: int, doc_id: str, year: int, month: int) -> tuple[date, date]:
    """A meter cycle from day d of (year, month) to day d-1 of the next month."""
    day = cycle_day(seed, doc_id)
    next_year, next_month = add_months(year, month, 1)
    return date(year, month, day), date(next_year, next_month, day - 1)


def _block1(kind: str, accounts) -> list[DocPlan]:
    plans = []
    for account in accounts:
        if account.first_doc_number <= 0:
            continue  # added after Block 1; has no Block 1 run
        for offset in range(3):
            year, month = add_months(account.start_year, account.start_month, offset)
            plans.append(
                DocPlan(
                    kind=kind,
                    number=account.first_doc_number + offset,
                    account=account.key,
                    group="block1",
                    year=year,
                    month=month,
                    offset=offset,
                )
            )
    return plans


def _dress(plans: list[DocPlan]) -> list[DocPlan]:
    """Assign layout, tier and injected quirks to one kind's planned documents."""
    kind = plans[0].kind
    eligible = [q for q in injected_for(kind) if not q.startswith("cross_")]
    counts: Counter[str] = Counter({q: 0 for q in injected_for(kind)})
    series_index = standalone_index = moderate_index = 0
    dressed: list[DocPlan] = []

    for plan in sorted(plans, key=lambda p: p.number):
        if plan.group == "block1":
            dressed.append(plan)
            continue
        if plan.group == "series":
            layout = LAYOUTS[series_index % len(LAYOUTS)]
            tier = TIERS[series_index % len(TIERS)]
            series_index += 1
            quirks: list[str] = []
        else:
            layout = LAYOUTS[standalone_index % 3]  # a, b, c: never the holdout
            tier = ("moderate", "hard")[standalone_index % 2]
            standalone_index += 1
            quirks = [f"{plan.group}_period"]

        if tier == "moderate":
            wanted = MODERATE_QUIRK_COUNTS[moderate_index % len(MODERATE_QUIRK_COUNTS)]
            moderate_index += 1
        elif tier == "hard":
            wanted = HARD_QUIRK_COUNT[kind]
        else:
            wanted = 0

        candidates = sorted(
            (q for q in eligible if q not in quirks),
            key=lambda q: (counts[q], INJECTED_QUIRKS.index(q)),
        )
        quirks += candidates[: max(0, wanted - len(quirks))]
        if layout != HOLDOUT_LAYOUT:
            counts.update(quirks)
        ordered = tuple(q for q in INJECTED_QUIRKS if q in quirks)
        dressed.append(
            DocPlan(
                kind=plan.kind,
                number=plan.number,
                account=plan.account,
                group=plan.group,
                year=plan.year,
                month=plan.month,
                layout=layout,
                tier=tier,
                quirks=ordered,
                period=plan.period,
                offset=plan.offset,
            )
        )
    return dressed


def plan_kind(
    kind: str,
    seed: int,
    *,
    series=SERIES,
    cross_year=CROSS_YEAR,
    cross_month=CROSS_MONTH,
    accounts=None,
) -> list[DocPlan]:
    """Every planned document of one kind, dressed, in number order."""
    from carbon_r.synthgen.profiles import CUSTOMER_PROFILES, FUEL_CUSTOMERS, LPG_CUSTOMERS

    if accounts is None:
        accounts = {ELEC: CUSTOMER_PROFILES, LPG: LPG_CUSTOMERS, FUEL: FUEL_CUSTOMERS}[kind]
    plans = _block1(kind, accounts)

    for window in series:
        if window.kind != kind:
            continue
        for offset in range(window.count):
            year, month = add_months(*window.start, offset)
            plans.append(
                DocPlan(
                    kind=kind,
                    number=window.first + offset,
                    account=window.account,
                    group="series",
                    year=year,
                    month=month,
                    offset=offset,
                )
            )

    if kind == ELEC:
        for account, year, number in cross_year:
            doc_id = f"elec_{number:03d}"
            plans.append(
                DocPlan(
                    kind=kind,
                    number=number,
                    account=account,
                    group="cross_year",
                    year=year,
                    month=12,
                    period=standalone_period(seed, doc_id, year, 12),
                )
            )
        for account, year, month, number in cross_month:
            doc_id = f"elec_{number:03d}"
            plans.append(
                DocPlan(
                    kind=kind,
                    number=number,
                    account=account,
                    group="cross_month",
                    year=year,
                    month=month,
                    period=standalone_period(seed, doc_id, year, month),
                )
            )

    numbers = [p.number for p in plans]
    duplicates = sorted(n for n, c in Counter(numbers).items() if c > 1)
    if duplicates:
        raise ValueError(f"{kind}: document numbers planned twice: {duplicates}")
    return _dress(plans)


def plan_all(seed: int) -> dict[str, list[DocPlan]]:
    return {kind: plan_kind(kind, seed) for kind in (ELEC, LPG, FUEL)}


# --- the table shown before rendering ----------------------------------------------------


def quirk_table(plans: dict[str, list[DocPlan]]) -> list[dict]:
    """Per kind and injected quirk: non-holdout documents by tier."""
    rows = []
    for kind, kind_plans in plans.items():
        visible = [p for p in kind_plans if not p.is_holdout]
        for quirk in injected_for(kind):
            tiers = Counter(p.tier for p in visible if quirk in p.quirks)
            rows.append(
                {
                    "kind": kind,
                    "quirk": quirk,
                    "moderate": tiers["moderate"],
                    "hard": tiers["hard"],
                    "total": tiers["moderate"] + tiers["hard"],
                }
            )
    return rows


def shortfalls(plans: dict[str, list[DocPlan]]) -> list[str]:
    return [
        f"{row['kind']} {row['quirk']}: {row['total']} non-holdout documents, "
        f"below the floor of {MIN_PER_QUIRK}"
        for row in quirk_table(plans)
        if row["total"] < MIN_PER_QUIRK
    ]


__all__ = [
    "CROSS_MONTH",
    "CROSS_YEAR",
    "FACTOR_YEARS",
    "MIN_PER_QUIRK",
    "SERIES",
    "DocPlan",
    "Window",
    "plan_all",
    "plan_kind",
    "quirk_table",
    "shortfalls",
]
