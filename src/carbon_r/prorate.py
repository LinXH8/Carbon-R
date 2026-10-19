"""Split a period's quantity across calendar years, by days. The one pro-rating rule.

A bill read 15 Dec 2023 to 14 Jan 2024 measures one quantity over two years, and the two
years take different grid factors (EMA publishes one per year). The quantity has to be
divided before a factor is applied, and the division is Python's (CLAUDE.md rule 1).

The rule, from SYNTHGEN_SPEC.md 5.2:

- each year's share is proportional to the days of the period that fall in it;
- every share but the last is rounded to a whole unit, half up;
- the last share is the remainder, so the parts sum EXACTLY to the whole.

Split by year, not by month. Factors are annual, so a monthly split would produce more
lines and change no figure; spec 5.2's monthly example is recorded as a deviation there.

Both sides of the eval call this function: synthgen writes its answer into the key, and
the pipeline splits what it read. That makes it a shared definition rather than an
independent check - which is why its tests assert literals produced by a separate scratch
script, not by calling it.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal


def days_by_year(start: date, end: date) -> dict[int, int]:
    """Days of the inclusive period `start..end` that fall in each calendar year."""
    if start > end:
        raise ValueError(f"period start {start} is after period end {end}")
    days: dict[int, int] = {}
    for year in range(start.year, end.year + 1):
        first = max(start, date(year, 1, 1))
        last = min(end, date(year, 12, 31))
        days[year] = (last - first).days + 1
    return days


def split_by_year(amount: int, start: date, end: date) -> dict[int, int]:
    """`amount` whole units over `start..end`, divided between calendar years by days.

    A period inside one year returns `{year: amount}` unchanged.
    """
    if amount < 0:
        raise ValueError(f"cannot split a negative amount ({amount})")
    days = days_by_year(start, end)
    total_days = sum(days.values())
    years = sorted(days)

    shares: dict[int, int] = {}
    allocated = 0
    for year in years[:-1]:
        share = int(
            (Decimal(amount) * Decimal(days[year]) / Decimal(total_days)).quantize(
                Decimal(1), rounding=ROUND_HALF_UP
            )
        )
        shares[year] = share
        allocated += share
    shares[years[-1]] = amount - allocated
    return shares


__all__ = ["days_by_year", "split_by_year"]
