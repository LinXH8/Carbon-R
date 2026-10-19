"""The layout registry: what each layout prints, and how it formats it.

One entry per layout id. The builders read it for the facts that change the answer key
(an LPG layout that prints a kg total is not the `lpg_cylinders` case); the renderer reads
it for formatting. Dates are formatted only with formats `parsing.DATE_FORMATS` already
accepts, so a new layout cannot introduce a date the pipeline has never been taught.

Layout `d` of every kind is the HOLDOUT (spec 6.1): never looked at while tuning prompts,
scored separately, excluded from the eval by default.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Layout:
    layout_id: str
    date_format: str
    is_holdout: bool = False
    # LPG only: whether the page prints the total kg delivered. False is `lpg_cylinders`.
    prints_total_kg: bool = False


LAYOUTS: dict[str, Layout] = {
    layout.layout_id: layout
    for layout in (
        # electricity
        Layout("elec_a", "%d %b %Y"),  # 04 Dec 2025
        Layout("elec_b", "%d/%m/%Y"),  # 04/12/2025
        Layout("elec_c", "%Y-%m-%d"),  # 2025-12-04
        Layout("elec_d", "%d %B %Y", is_holdout=True),  # 04 December 2025
        # LPG
        Layout("lpg_a", "%d %b %Y"),
        Layout("lpg_b", "%d/%m/%Y", prints_total_kg=True),
        Layout("lpg_c", "%d-%m-%Y"),  # 04-12-2025
        Layout("lpg_d", "%d %B %Y", is_holdout=True, prints_total_kg=True),
        # fuel cards
        Layout("fuel_a", "%d %b %Y"),
        Layout("fuel_b", "%Y-%m-%d"),
        Layout("fuel_c", "%d/%m/%Y"),
        Layout("fuel_d", "%d %b %y", is_holdout=True),  # 04 Dec 25
    )
}


def layout(layout_id: str) -> Layout:
    try:
        return LAYOUTS[layout_id]
    except KeyError:
        raise ValueError(f"No layout {layout_id!r}; have {sorted(LAYOUTS)}") from None


__all__ = ["LAYOUTS", "Layout", "layout"]
