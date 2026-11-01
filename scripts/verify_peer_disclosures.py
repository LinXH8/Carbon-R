"""Record the author's verification of drafted `peer_disclosures` rows (CLAUDE.md rule 4).

    uv run python scripts/verify_peer_disclosures.py inventory SOURCE_ID      before drafting
    uv run python scripts/verify_peer_disclosures.py completeness SOURCE_ID   after drafting
    uv run python scripts/verify_peer_disclosures.py worksheet SOURCE_ID
    uv run python scripts/verify_peer_disclosures.py apply SOURCE_ID --by "NAME"

`inventory` (Part B 2b) writes an empty `<source_id>.inventory.csv` beside the worksheets, for
the author to list every report-year cell on each table page - row label and column header as
printed, scope, method - BEFORE the report is drafted, so the list cannot lean on what the
model read. It refuses once a draft exists. No figure is listed. `completeness` then compares
the list with the draft (`peer_verification.match_cells`): cells the drafter skipped, and
cells it drafted that nobody listed. The draft records the inventory's digest, so a list
changed after drafting is reported.

`worksheet` writes `data/generated/peer_verification/<source_id>.csv` (git-ignored) with one
line per unverified draft, blind-sample figures masked as [blind]. It never overwrites a
worksheet that exists: that file holds the author's decisions.

The author checks each line against the PDF - blind rows first, from the page alone - and
fills in `blind_value_by_author` (blind rows only), `outcome` (ok / corrected / rejected),
the corrected figure and/or row label as printed, a corrected method (location / market /
none, for a method the page does not state), and a note.

`apply` reads the worksheet and updates the table in one transaction, or not at all: a draft
missing from the worksheet, a line matching no draft, or a corrected figure that is not a
number stops it (`peer_verification.plan_verification`). Already-verified rows are never
touched, so a second apply updates 0.

The worksheet is the only record of the author's decisions outside the database, and it
holds real figures, so it is never committed. Back it up with the reports.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from carbon_r.config import PROJECT_ROOT
from carbon_r.db import DatabaseNotConfiguredError, connect
from carbon_r.models.peer_verification import (
    APPLY_UPDATE,
    INVENTORY_COLUMNS,
    WORKSHEET_COLUMNS,
    DraftRow,
    InventoryEntry,
    WorksheetEntry,
    inventory_digest,
    match_cells,
    plan_verification,
    report_year_cells,
)
from carbon_r.peer_sources import TABLE_PAGES
from carbon_r.sources import PEERS

WORKSHEET_DIR = PROJECT_ROOT / "data" / "generated" / "peer_verification"
DRAFT_DIR = PROJECT_ROOT / "data" / "generated" / "peer_drafts"


def inventory_path(source_id: str) -> Path:
    return WORKSHEET_DIR / f"{source_id}.inventory.csv"


def read_inventory(source_id: str) -> list[InventoryEntry] | None:
    """The author's list, or None when there is none. Blank lines are skipped."""
    path = inventory_path(source_id)
    if not path.exists():
        return None
    with path.open(newline="") as f:
        rows = [r for r in csv.DictReader(f) if any((v or "").strip() for v in r.values())]
    return [InventoryEntry.from_csv_row(r) for r in rows]


def inventory(source_id: str) -> int:
    path = inventory_path(source_id)
    if source_id not in TABLE_PAGES:
        print(f"{source_id} has no TABLE_PAGES entry; register its pages first.")
        return 1
    if (DRAFT_DIR / f"{source_id}.json").exists():
        print(f"{source_id} is already drafted; an inventory taken now could lean on the draft.")
        return 1
    if path.exists():
        print(f"{path.relative_to(PROJECT_ROOT)} exists; not overwritten (it holds your list).")
        return 1
    WORKSHEET_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        csv.writer(f).writerow(INVENTORY_COLUMNS)
    pages = ", ".join(str(p) for p in TABLE_PAGES[source_id])
    print(f"{path.relative_to(PROJECT_ROOT)}: empty. List every report-year cell on PDF p. {pages}")
    print("  one line per cell; scope 1, 2, 3, 1+2 or 1+2+3; method location, market or blank;")
    print("  leave paired_draft_label blank (it is for after `completeness`).")
    return 0


def completeness(source_id: str) -> int:
    try:
        listed = read_inventory(source_id)
    except ValueError as exc:
        print(f"Inventory not read: {exc}")
        return 1
    draft_file = DRAFT_DIR / f"{source_id}.json"
    if listed is None or not draft_file.exists():
        print(f"{source_id}: needs both an inventory and a draft.")
        return 1
    draft = json.loads(draft_file.read_text())
    try:
        cells = report_year_cells(draft["pages"], PEERS[source_id].report_year)
    except ValueError as exc:
        print(f"{source_id}: {exc}")
        return 1
    result = match_cells(listed, cells, TABLE_PAGES[source_id])
    recorded = draft.get("inventory_sha256")
    if recorded is None:
        print("  WARNING: the draft recorded no inventory: it was drafted before one existed.")
    elif recorded != inventory_digest(listed):
        print("  WARNING: the inventory changed after drafting (beyond paired_draft_label).")

    def cell_text(page: int, label: str, header: str, scope: str, method: str | None) -> str:
        return f"p.{page}  {label!r} [{header}] scope {scope}{' ' + method if method else ''}"

    print(f"== {source_id}: listed {result.listed}; report-year cells read {result.read}")
    print(f"   matched exactly     {len(result.matched)} of {result.listed}")
    print(f"   paired by hand      {len(result.paired_by_hand)} of {result.listed} "
          "(read, different wording)")  # fmt: skip
    print(f"   method differs      {len(result.method_differs)} of {result.listed} "
          "(read, but not a clean match)")  # fmt: skip
    print(f"   skipped by drafter  {len(result.skipped)} of {result.listed}")
    print(f"   read, not listed    {len(result.extra)}")
    print(f"   of the listed cells read, dropped by a rule: {result.dropped_by_rule or 'none'}")
    for m in result.paired_by_hand:
        e, c = m.listed, m.drafted
        where = cell_text(e.pdf_page, e.row_label, e.column_header, e.scope, e.method)
        print(f"   by hand  {where} = drafted {c.drafted_row_label!r} [{c.column_header}] "
              f"({c.outcome})")  # fmt: skip
    for m in result.method_differs:
        e, c = m.listed, m.drafted
        print(f"   method   {cell_text(e.pdf_page, e.row_label, e.column_header, e.scope, None)}"
              f": listed {e.method or 'none'}, drafted {c.method or 'none'}")  # fmt: skip
    for e in result.skipped:
        print(
            f"   skipped  {cell_text(e.pdf_page, e.row_label, e.column_header, e.scope, e.method)}"
        )
    for c in result.extra:
        where = cell_text(c.pdf_page, c.drafted_row_label, c.column_header, c.scope, c.method)
        print(f"   extra    {where} ({c.outcome})")
    for problem in result.problems:
        print(f"   problem: {problem}")
    return 1 if result.problems else 0


_DRAFTS = """
select id, pdf_page, drafted_row_label, column_header, drafted_figure_printed, blind_sample,
       notes, unit, scope, scope3_category, drafted_method
from peer_disclosures
where source_id = %(source_id)s and verified_at is null
order by pdf_page, id
"""


def worksheet(source_id: str) -> int:
    path = WORKSHEET_DIR / f"{source_id}.csv"
    if path.exists():
        print(
            f"{path.relative_to(PROJECT_ROOT)} exists; not overwritten (it holds your decisions)."
        )
        return 1
    with connect() as conn:
        rows = conn.execute(_DRAFTS, {"source_id": source_id}).fetchall()
    if not rows:
        print(f"No unverified drafts for {source_id}.")
        return 1
    WORKSHEET_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(WORKSHEET_COLUMNS)
        for _id, page, label, header, figure, blind, _notes, unit, scope, category, method in rows:
            writer.writerow(
                [page, label, header, "[blind]" if blind else figure, unit, scope,
                 category or "", method or "", "yes" if blind else "", "", "", "", "", "", ""]
            )  # fmt: skip
    blind = sum(1 for r in rows if r[5])
    print(f"{path.relative_to(PROJECT_ROOT)}: {len(rows)} rows, {blind} blind")
    return 0


def apply(source_id: str, verified_by: str) -> int:
    path = WORKSHEET_DIR / f"{source_id}.csv"
    if not path.exists():
        print(f"No worksheet at {path.relative_to(PROJECT_ROOT)}; run `worksheet` first.")
        return 1
    with path.open(newline="") as f:
        lines = [row for row in csv.DictReader(f) if any((v or "").strip() for v in row.values())]
    try:
        entries = [WorksheetEntry.from_csv_row(row) for row in lines]
    except ValueError as exc:
        print(f"Worksheet not applied: {exc}")
        return 1

    with connect() as conn:
        drafts = [
            DraftRow(
                id=r[0],
                pdf_page=r[1],
                drafted_row_label=r[2],
                column_header=r[3],
                method=r[10],
                drafted_figure_printed=r[4],
                blind_sample=r[5],
                notes=r[6],
            )
            for r in conn.execute(_DRAFTS, {"source_id": source_id}).fetchall()
        ]
        if not drafts:
            print(f"{source_id}: no unverified drafts; updated 0.")
            return 0
        plan = plan_verification(drafts, entries)
        if plan.problems:
            print("Worksheet not applied; nothing changed:", *plan.problems, sep="\n  ")
            return 1
        verified_at = datetime.now(UTC)
        updated = sum(
            conn.execute(
                APPLY_UPDATE,
                u.model_dump() | {"verified_by": verified_by, "verified_at": verified_at},
            ).rowcount
            for u in plan.updates
        )
        conn.commit()
        counts = conn.execute(
            "select verification, count(*) from peer_disclosures "
            "where source_id = %(s)s and verified_at is not null group by 1 order by 1",
            {"s": source_id},
        ).fetchall()
        (in_view,) = conn.execute(
            "select count(*) from peer_disclosures_verified where source_id = %(s)s",
            {"s": source_id},
        ).fetchone()
        (left,) = conn.execute(
            "select count(*) from peer_disclosures where source_id = %(s)s and verified_at is null",
            {"s": source_id},
        ).fetchone()

    print(f"{source_id}: updated {updated} of {len(drafts)} drafts, verified by {verified_by}")
    print(f"  outcomes      {dict(counts)}")
    print(f"  blind         {plan.blind_matched} of {plan.blind_checked} matched the draft")
    print(f"  in verified view {in_view}; still unverified {left}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inventory").add_argument("source_id")
    sub.add_parser("completeness").add_argument("source_id")
    sub.add_parser("worksheet").add_argument("source_id")
    a = sub.add_parser("apply")
    a.add_argument("source_id")
    a.add_argument("--by", required=True, help="who checked the rows against the PDF")
    args = parser.parse_args()
    try:
        if args.command == "inventory":
            return inventory(args.source_id)
        if args.command == "completeness":
            return completeness(args.source_id)
        if args.command == "worksheet":
            return worksheet(args.source_id)
        return apply(args.source_id, args.by)
    except DatabaseNotConfiguredError as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
