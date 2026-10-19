"""`split_by_year` against literals from an independent route.

Every expected split below was produced by a scratch script that does NOT call
`prorate.py`: plain integer arithmetic, half-up as `(2 x amount x days + total) // (2 x
total)`, remainder to the last year. The synthgen key and the pipeline both call
`split_by_year`, so it cannot be its own witness (spec 1.3); these literals are.
"""

from datetime import date

import pytest

from carbon_r.prorate import days_by_year, split_by_year


@pytest.mark.parametrize(
    ("amount", "start", "end", "expected"),
    [
        # 17 of 31 days in 2025: 2355.87... rounds to 2356; 2026 takes the remainder.
        (4296, date(2025, 12, 15), date(2026, 1, 14), {2025: 2356, 2026: 1940}),
        # A 2023/24 crossing, the kind whose two years have different EMA factors.
        (9000, date(2023, 12, 16), date(2024, 1, 15), {2023: 4645, 2024: 4355}),
        # One day in the first year, then one day in the second.
        (1000, date(2023, 12, 31), date(2024, 1, 30), {2023: 32, 2024: 968}),
        (1000, date(2023, 12, 2), date(2024, 1, 1), {2023: 968, 2024: 32}),
        (31, date(2022, 12, 17), date(2023, 1, 16), {2022: 15, 2023: 16}),
        # Exactly half: half UP gives the first year the unit, never banker's rounding.
        (1, date(2023, 12, 31), date(2024, 1, 1), {2023: 1, 2024: 0}),
        # Inside one year (a leap-year March): unchanged.
        (5000, date(2024, 3, 1), date(2024, 3, 31), {2024: 5000}),
    ],
)
def test_split_by_year(amount, start, end, expected):
    assert split_by_year(amount, start, end) == expected


def test_parts_always_sum_to_the_whole():
    for amount in (0, 1, 7, 4296, 180_001):
        shares = split_by_year(amount, date(2024, 12, 20), date(2025, 1, 19))
        assert sum(shares.values()) == amount


def test_days_by_year_counts_inclusively():
    assert days_by_year(date(2023, 12, 16), date(2024, 1, 15)) == {2023: 16, 2024: 15}
    assert days_by_year(date(2024, 2, 1), date(2024, 2, 29)) == {2024: 29}


def test_a_reversed_period_is_refused():
    with pytest.raises(ValueError, match="after period end"):
        split_by_year(10, date(2024, 1, 2), date(2024, 1, 1))
