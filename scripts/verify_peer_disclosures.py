"""Record the author's verification of drafted `peer_disclosures` rows (CLAUDE.md rule 4).

    uv run python scripts/verify_peer_disclosures.py worksheet SOURCE_ID
    uv run python scripts/verify_peer_disclosures.py apply SOURCE_ID --by "NAME"

`worksheet` writes `data/generated/peer_verification/<source_id>.csv` (git-ignored) with one
line per unverified draft, blind-sample figures masked as [blind]. It never overwrites a
worksheet that exists: that file holds the author's decisions.

The author checks each line against the PDF - blind rows first, from the page alone - and
fills in `blind_value_by_author` (blind rows only), `outcome` (ok / corrected / rejected),
the corrected figure and/or row label as printed, and a note.

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
import sys
from datetime import UTC, datetime

from carbon_r.config import PROJECT_ROOT
from carbon_r.db import DatabaseNotConfiguredError, connect
from carbon_r.models.peer_verification import (
    APPLY_UPDATE,
    WORKSHEET_COLUMNS,
    DraftRow,
    WorksheetEntry,
    plan_verification,
)

WORKSHEET_DIR = PROJECT_ROOT / "data" / "generated" / "peer_verification"

_DRAFTS = """
select id, pdf_page, drafted_row_label, column_header, drafted_figure_printed, blind_sample,
       notes, unit, scope, scope3_category, method
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
                 category or "", method or "", "yes" if blind else "", "", "", "", "", ""]
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
    sub.add_parser("worksheet").add_argument("source_id")
    a = sub.add_parser("apply")
    a.add_argument("source_id")
    a.add_argument("--by", required=True, help="who checked the rows against the PDF")
    args = parser.parse_args()
    try:
        if args.command == "worksheet":
            return worksheet(args.source_id)
        return apply(args.source_id, args.by)
    except DatabaseNotConfiguredError as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
