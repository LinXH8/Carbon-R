"""Money arithmetic, in exact decimals.

One definition of "round to cents", shared by everything that touches a currency amount:
the synthetic-document generator, the ground-truth validator, and (from Block 3) the
extraction sanity check. They must agree to the cent or an eval result is ambiguous, and
the cheapest way to guarantee that is for them to call the same function.

Floating point is not used. `0.09 * 1001.50` lands on 90.13 where exact arithmetic gives
90.14 — rarely, which is worse than always, because the few documents checked by hand will
look correct.
"""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def round2(value: Decimal) -> Decimal:
    """Round to cents, half away from zero.

    ROUND_HALF_UP rather than Python's default ROUND_HALF_EVEN: commercial invoices round
    half up, and banker's rounding would make a hand-check disagree with the printed page
    on roughly one line in a few hundred.
    """
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
