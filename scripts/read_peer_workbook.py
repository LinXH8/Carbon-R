"""Read peer rows from an issuer's workbook by code, no model (Tier 3 Block 1: FLCT, CICT).

    uv run python scripts/read_peer_workbook.py read SOURCE_ID

Needs the source's tables declared in `peer_sources.WORKBOOK_TABLES` and the author's
inventory (`verify_peer_disclosures.py inventory SOURCE_ID`, filled in first): every
report-year cell of those tables, with its label and header as printed, scope, Scope 3
category and method. `read` checks each listed cell against the workbook and the tables
against the list (`carbon_r.peer_workbook.read_workbook`). On any problem, or any cell
holding a value that nobody listed, it writes nothing.

Otherwise it writes the draft to git-ignored `data/generated/peer_drafts/<source_id>.json`
in the same shape as a page draft, so `load`, `review`, `worksheet`, `apply`, `export` and
the leak check work unchanged. Each row cites its sheet and cell, records the sha256 of the
sheet it was read from, and is tagged as read by code. The seeded blind sample is drawn as
for a page draft: the author reads those values in the workbook before seeing them.

Re-running gives the same rows and the same blind sample; only `drafted_at` moves, and
`load` leaves a row whose other fields are unchanged as it is.

Local only: the workbooks are git-ignored, so this never runs in CI.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import UTC, datetime

from carbon_r.config import PROJECT_ROOT
from carbon_r.models.peer_disclosure import BLIND_SEED, BLIND_SHARE, blind_sample
from carbon_r.peer_sources import WORKBOOK_TABLES
from carbon_r.peer_workbook import READER, WorkbookInventoryEntry, read_workbook
from carbon_r.sources import BY_ID, PEERS, check_local_files
from carbon_r.xlsx import sheet_sha256

DRAFT_DIR = PROJECT_ROOT / "data" / "generated" / "peer_drafts"
WORKSHEET_DIR = PROJECT_ROOT / "data" / "generated" / "peer_verification"


def inventory_path(source_id: str):
    return WORKSHEET_DIR / f"{source_id}.inventory.csv"


def read_inventory(source_id: str) -> list[WorkbookInventoryEntry] | None:
    """The author's list, or None when there is none. Blank lines are skipped."""
    path = inventory_path(source_id)
    if not path.exists():
        return None
    with path.open(newline="") as f:
        rows = [r for r in csv.DictReader(f) if any((v or "").strip() for v in r.values())]
    return [WorkbookInventoryEntry.from_csv_row(r) for r in rows]


def inventory_digest(entries: list[WorkbookInventoryEntry]) -> str:
    lines = sorted(json.dumps(e.model_dump(mode="json"), sort_keys=True) for e in entries)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def read(source_id: str) -> int:
    peer = PEERS.get(source_id)
    if peer is None or not peer.is_workbook:
        print(f"{source_id} is not a registered workbook source.")
        return 1
    tables = WORKBOOK_TABLES.get(source_id)
    if not tables:
        print(f"{source_id} has no WORKBOOK_TABLES entry; declare its tables first.")
        return 1
    problems = check_local_files((source_id,))
    if problems:
        print("Refusing to read:", *problems, sep="\n  ")
        return 1
    try:
        listed = read_inventory(source_id)
    except ValueError as exc:
        print(f"Refusing to read: the inventory does not read ({exc}).")
        return 1
    if not listed:
        print(f"No inventory for {source_id}: list the cells first (it is the classification).")
        return 1

    record = BY_ID[source_id]
    draft = read_workbook(record.path, peer, tables, listed)
    print(f"== {source_id}  [{peer.company}]  {READER}")
    print(f"   listed {draft.listed}; read {len(draft.rows)}; problems {len(draft.problems)}; "
          f"report-year cells not listed {len(draft.unlisted)}")  # fmt: skip
    for problem in draft.problems:
        print(f"   problem: {problem}")
    for cell in draft.unlisted:
        print(f"   not listed: {cell}")
    if draft.problems or draft.unlisted:
        print("   nothing written")
        return 1

    read_at = datetime.now(UTC).isoformat()
    tables_json = [t.model_dump() for t in tables]
    request_hash = hashlib.sha256(
        json.dumps([READER, tables_json], sort_keys=True).encode()
    ).hexdigest()[:16]
    sheet_hashes = {t.sheet: sheet_sha256(record.path, t.sheet) for t in tables}
    blind = blind_sample([row.key for row in draft.rows])
    rows = [
        row.model_copy(update={"blind_sample": row.key in blind}).model_dump(mode="json")
        | {
            "drafted_by": READER,
            "drafted_at": read_at,
            "draft_request_hash": request_hash,
            "page_image_sha256": sheet_hashes[row.sheet_name],
        }
        for row in draft.rows
    ]
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    out = DRAFT_DIR / f"{source_id}.json"
    out.write_text(
        json.dumps(
            {
                "source_id": source_id,
                "source_sha256": record.sha256,
                "model": READER,
                "request_hash": request_hash,
                "inventory_sha256": inventory_digest(listed),
                "blind": {"seed": BLIND_SEED, "share": BLIND_SHARE, "size": len(blind)},
                "tables": tables_json,
                "sheet_sha256": sheet_hashes,
                "completeness": {"listed": draft.listed, "read": len(rows), "unlisted": 0},
                "pages": [],
                "rows": rows,
            },
            indent=2,
        )
    )
    noted = sum(1 for r in rows if r["notes"])
    print(f"   rows {len(rows)}; shown other than stored {noted}; blind sample {len(blind)} "
          f"(seed {BLIND_SEED})")  # fmt: skip
    print(f"   -> {out.relative_to(PROJECT_ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("read").add_argument("source_id")
    args = parser.parse_args()
    return read(args.source_id)


if __name__ == "__main__":
    sys.exit(main())
