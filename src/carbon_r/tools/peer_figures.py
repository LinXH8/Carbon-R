"""`lookup_peer_disclosure` - a peer's emissions figure, as printed, verified by hand.

The figure-shaped counterpart of `search_peer_disclosures`. That tool retrieves prose and
never a figure, because a PDF text layer fuses footnote markers onto numbers. This one
returns figures, and only figures that have been verified by hand: drafted by a language
model from an IMAGE of the table page (Part B's pass 2a), or read by code from a cell of the
company's own workbook (Tier 3 Block 1), then checked by the author against that page or
cell (CLAUDE.md rule 4, the peer-disclosure exception). It reads `peer_disclosures_verified` and
nothing else, so a draft nobody checked cannot reach the agent.

Rule 3 holds: the lookup is exact SQL on company and scope, plus method, Scope 3 category
and report year when given. Nothing is embedded or ranked by similarity.

Rule 1 holds: every row comes back as the report prints it - `figure_printed` and `unit`
as on the page, `figure` the same text parsed in Python when the row was loaded. Nothing is
summed, converted or chosen between. A report often prints several rows for one scope (its
parts and its total, or one table per region), and each is returned with its row label and
column header so the reply can state which is which.

Two kinds of `no_results`, worded so absence is never read as evidence:

    company has rows   nothing matches the filter; the message lists the scopes, methods and
                       report years Carbon R does hold for that company, and the
                       report_year convention when a year was asked for.
    company has none   Carbon R holds no verified figure for that company at all; the message
                       lists the companies it does hold figures for.

Neither says "not disclosed": the table holds what has been verified, not everything the
reports print.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from carbon_r.db import DatabaseNotConfiguredError, connect
from carbon_r.tools.base import ToolResult, error, no_results, ok
from carbon_r.tools.peer import PEER_CAVEAT, period_text

Scope = Literal["1", "2", "3", "1+2", "1+2+3"]
Method = Literal["location", "market"]

DISCLOSURE = (
    f"{PEER_CAVEAT} Each figure was drafted by a language model from an image of the cited "
    "page, or read by code from the cited cell of the company's own workbook, and then "
    "verified by hand against that page or cell on the date given; it is stated as printed, "
    "in the unit printed, and is never summed, converted or ranked by Carbon R."
)

_YEAR_CONVENTION = (
    "report_year is the calendar year in which a report's period ENDS: a financial year from "
    "1 April 2025 to 31 March 2026 is report_year 2026."
)

# Every filter is optional except company and scope; a null parameter matches every row.
_ROWS_SQL = """
select d.source_id, s.title, d.company, d.period_start, d.period_end, d.scope,
       d.scope3_category, d.method, d.figure_printed, d.figure, d.unit, d.pdf_page,
       d.printed_page, d.sheet_name, d.cell_ref, d.row_label, d.column_header, d.notes,
       d.verification, d.verified_at, d.drafted_by
from peer_disclosures_verified d
join sources s on s.id = d.source_id
where d.company = %(company)s
  and d.scope = %(scope)s
  and (%(method)s::text is null or d.method = %(method)s)
  and (%(scope3_category)s::smallint is null or d.scope3_category = %(scope3_category)s)
  and (%(report_year)s::integer is null
       or extract(year from d.period_end)::integer = %(report_year)s)
order by d.source_id, d.pdf_page, d.sheet_name, d.cell_ref, d.id
"""

# What the company does have, for a miss.
_HELD_SQL = """
select distinct extract(year from period_end)::integer as report_year, scope, method,
       scope3_category
from peer_disclosures_verified
where company = %(company)s
order by report_year, scope, method nulls first, scope3_category nulls first
"""

_COMPANIES_SQL = "select distinct company from peer_disclosures_verified order by company"


class PeerQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    company: str
    scope: Scope
    method: Method | None = None
    scope3_category: int | None = Field(default=None, ge=1, le=15)
    report_year: int | None = None

    def describe(self) -> str:
        parts = [self.company, f"Scope {self.scope}"]
        if self.scope3_category is not None:
            parts.append(f"category {self.scope3_category}")
        if self.method is not None:
            parts.append(f"{self.method}-based")
        if self.report_year is not None:
            parts.append(f"report_year {self.report_year}")
        return ", ".join(parts)


class PeerFigure(BaseModel):
    """One verified cell of a peer report's table, with what is needed to check it by eye."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    report: str
    company: str
    period_start: date
    period_end: date
    period: str
    scope: Scope
    scope3_category: int | None
    method: Method | None
    figure_printed: str
    figure: Decimal | None
    unit: str
    pdf_page: int | None
    printed_page: int | None
    # A row read from the issuer's workbook cites a sheet and cell instead of a page.
    sheet_name: str | None = None
    cell_ref: str | None = None
    citation: str
    row_label: str
    column_header: str
    notes: str | None
    verification: Literal["confirmed", "corrected"]
    verified_on: date
    drafted_by: str


def citation_text(
    report: str,
    printed_page: int | None,
    pdf_page: int | None,
    sheet_name: str | None = None,
    cell_ref: str | None = None,
) -> str:
    """Where a figure is printed: the page of a report, or the sheet and cell of a workbook."""
    if pdf_page is None:
        return f"{report}, sheet '{sheet_name}', cell {cell_ref}"
    page = (
        f"p. {printed_page} (PDF p. {pdf_page})"
        if printed_page is not None
        else (f"PDF p. {pdf_page}")
    )
    return f"{report}, {page}"


def _figure(row: dict[str, Any]) -> PeerFigure:
    report = row["title"]
    return PeerFigure(
        source_id=row["source_id"],
        report=report,
        company=row["company"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        period=period_text(row["period_start"], row["period_end"]),
        scope=row["scope"],
        scope3_category=row["scope3_category"],
        method=row["method"],
        figure_printed=row["figure_printed"],
        figure=row["figure"],
        unit=row["unit"],
        pdf_page=row["pdf_page"],
        printed_page=row["printed_page"],
        sheet_name=row.get("sheet_name"),
        cell_ref=row.get("cell_ref"),
        citation=citation_text(
            report, row["printed_page"], row["pdf_page"], row.get("sheet_name"), row.get("cell_ref")
        ),
        row_label=row["row_label"],
        column_header=row["column_header"],
        notes=row["notes"],
        verification=row["verification"],
        verified_on=row["verified_at"].date(),
        drafted_by=row["drafted_by"],
    )


def _held_line(held: dict[str, Any]) -> str:
    text = f"report_year {held['report_year']}: Scope {held['scope']}"
    if held["scope3_category"] is not None:
        text += f" category {held['scope3_category']}"
    if held["method"] is not None:
        text += f", {held['method']}-based"
    return f"- {text}"


def _resolve(
    q: PeerQuery,
    rows: Sequence[dict[str, Any]],
    held: Sequence[dict[str, Any]],
    companies: Sequence[str],
) -> ToolResult:
    """Every branch after the query, with no database. Pure.

    `held` is what the company has in the verified view (used only on a miss); `companies`
    is every company that has a verified row.
    """
    context: dict[str, Any] = {"query": q.model_dump(mode="json"), "disclosure": DISCLOSURE}
    if rows:
        figures = [_figure(r) for r in rows]
        lines = [
            f"- {f.row_label} [{f.column_header}]: {f.figure_printed} {f.unit} "
            f"({f.citation}; verified {f.verified_on.isoformat()})"
            for f in figures
        ]
        return ok(
            context | {"figures": [f.model_dump(mode="json") for f in figures]},
            f"{len(figures)} verified figure(s) for {q.describe()}:\n"
            + "\n".join(lines)
            + "\nState each figure as printed, with its unit, row label, period and page (or "
            "sheet and cell), and "
            "its Scope 2 method where it has one (say 'method not stated' where it has none). "
            "Where several rows match, they are different rows of the report (parts and a "
            "total, or separate tables): name each by its label; never sum them or pick one "
            "as 'the' figure. " + DISCLOSURE,
        )

    if held:
        message = (
            f"Carbon R holds no verified figure for {q.describe()}. This does not mean the "
            f"company did not disclose one: only figures checked by hand are held. Verified "
            f"figures held for {q.company}:\n" + "\n".join(_held_line(h) for h in held)
        )
        if q.report_year is not None:
            message += f"\n{_YEAR_CONVENTION}"
    else:
        message = (
            f"Carbon R holds no verified figure for {q.company} at all. This does not mean "
            f"the company did not disclose one: only figures checked by hand are held, and "
            f"none has been checked for {q.company} yet. Companies with verified figures: "
            + (", ".join(companies) if companies else "none")
            + "."
        )
    message += (
        "\nTell the user that, state no figure, and offer to point to where the report "
        "discusses it (search_peer_disclosures) instead."
    )
    return no_results(
        message,
        context
        | {
            "held": [dict(h) for h in held],
            "companies_with_verified_figures": list(companies),
        },
    )


Fetcher = Callable[[PeerQuery], tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]]


def _db_fetch(q: PeerQuery) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    params = q.model_dump()
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(_ROWS_SQL, params).fetchall()
        if rows:
            return rows, [], []
        held = cur.execute(_HELD_SQL, params).fetchall()
        companies = [r["company"] for r in cur.execute(_COMPANIES_SQL).fetchall()]
    return rows, held, companies


def lookup_peer_disclosure(
    company: str,
    scope: str,
    method: str | None = None,
    scope3_category: int | None = None,
    report_year: int | None = None,
    *,
    fetch: Fetcher | None = None,
) -> ToolResult:
    """The verified figures a peer report prints for one scope, or an honest no_results."""
    try:
        q = PeerQuery(
            company=company,
            scope=scope,
            method=method,
            scope3_category=scope3_category,
            report_year=report_year,
        )
    except ValueError as exc:
        return error(f"Cannot look up a peer figure: {exc}")
    if q.scope3_category is not None and q.scope != "3":
        return error("Cannot look up a peer figure: scope3_category applies to Scope 3 only.")
    try:
        rows, held, companies = (fetch or _db_fetch)(q)
    except DatabaseNotConfiguredError as exc:
        return error(f"Cannot look up a peer figure: {exc}")
    except psycopg.Error as exc:
        return error(f"Database error during peer figure lookup: {exc}")
    return _resolve(q, rows, held, companies)


def _demo() -> None:  # pragma: no cover - manual verification aid
    """`python -m carbon_r.tools.peer_figures COMPANY SCOPE [method]`."""
    import sys

    company, scope, *rest = sys.argv[1:]
    result = lookup_peer_disclosure(company, scope, method=rest[0] if rest else None)
    print(f"{result.status}: {result.message}")


if __name__ == "__main__":  # pragma: no cover
    _demo()


__all__ = ["DISCLOSURE", "PeerFigure", "PeerQuery", "citation_text", "lookup_peer_disclosure"]
