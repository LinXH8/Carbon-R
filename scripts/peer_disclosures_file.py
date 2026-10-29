"""Export the verified peer figures to the committed file, or seed the table from it.

    uv run python scripts/peer_disclosures_file.py export   # table -> the committed file
    uv run python scripts/peer_disclosures_file.py load     # file -> table (or: make seed-peer)

`export` writes every row of `peer_disclosures_verified` (verified, not rejected), in key
order, as deterministic JSON. `load` applies migrations, registers the sources, and inserts
the file's rows: identical rows are left alone, and a row that exists but differs stops the
load with nothing written. Running either twice changes nothing.

See `carbon_r.peer_disclosures_file` for what each row holds and why.
"""

from __future__ import annotations

import sys

from carbon_r.db import DatabaseNotConfiguredError, apply_migrations, connect
from carbon_r.peer_disclosures_file import VERIFIED_FILE, dumps, export_rows, load_rows, read_file
from carbon_r.sources import REGISTRY, UPSERT


def export() -> int:
    with connect() as conn:
        verified = export_rows(conn)
    text = dumps(verified)
    before = VERIFIED_FILE.read_text() if VERIFIED_FILE.exists() else None
    VERIFIED_FILE.write_text(text)
    sources = sorted({row.source_id for row in verified.rows})
    print(f"{VERIFIED_FILE.name}: {len(verified.rows)} rows from {len(sources)} reports "
          f"({', '.join(sources)}); {'unchanged' if text == before else 'written'}")  # fmt: skip
    return 0


def load() -> int:
    verified = read_file()
    with connect() as conn:
        apply_migrations(conn)
        for record in REGISTRY:
            conn.execute(UPSERT, record.row())
        result = load_rows(conn, verified)
        if result.conflicts:
            conn.rollback()
            print("Not loaded; nothing written. Rows that differ from the table:",
                  *result.conflicts, sep="\n  ")  # fmt: skip
            return 1
        conn.commit()
    print(f"{VERIFIED_FILE.name}: {len(verified.rows)} rows; inserted {result.inserted}, "
          f"unchanged {result.unchanged}")  # fmt: skip
    return 0


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command not in ("export", "load"):
        print(__doc__)
        return 2
    try:
        return export() if command == "export" else load()
    except DatabaseNotConfiguredError as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
