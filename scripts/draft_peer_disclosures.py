"""Draft `peer_disclosures` rows from page images of peer reports (Tier 2 Block 4 Part B, 2a).

    uv run python scripts/draft_peer_disclosures.py scan SOURCE_ID       no model call
    uv run python scripts/draft_peer_disclosures.py draft SOURCE_ID --model MODEL_ID
    uv run python scripts/draft_peer_disclosures.py load                 upsert every draft
    uv run python scripts/draft_peer_disclosures.py review [SOURCE_ID]   blind rows masked
    uv run python scripts/draft_peer_disclosures.py leak-check [PATH...] before any commit

`scan` lists the pages whose text layer names a scope and a CO2e unit. The text layer is
used only to FIND pages - never to read a figure, which is the thing it gets wrong. The pages
drafted are `peer_sources.TABLE_PAGES`, set by hand from the notes and this scan.

`draft` renders each listed page (`extract.rasterise_page`), sends the image to the model
with `DRAFT_INSTRUCTION`, validates the reply as a `PeerTableReading`, and turns it into rows
with `to_disclosures`. Replies are cached on everything that can change them (source, page,
model, dpi, detail, request hash), so a re-run makes no model call. `--model` has no
default: the drafting model must differ from any model later scored against these rows.
The draft - readings, rows, drops by reason, token usage, the blind sample - goes to
git-ignored `data/generated/peer_drafts/<source_id>.json`.

`load` upserts the drafts into `peer_disclosures`. A verified row is never overwritten, and
an unchanged draft is not touched, so a second load reports 0 inserted and 0 updated.

`review` prints the drafts for the author to check against the PDF, ordered by file and
page. Figures in the blind sample show as [blind]: the author enters those before seeing
them (CLAUDE.md rule 4).

`leak-check` enforces where real peer figures may be committed: verified ones in
`data/peer_disclosures_verified.json` only, drafts and garbled readings nowhere (CLAUDE.md).

Local only: the reports are git-ignored, so this never runs in CI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from openai import OpenAI, OpenAIError

from carbon_r.config import PROJECT_ROOT, get_settings
from carbon_r.db import DatabaseNotConfiguredError, apply_migrations, connect
from carbon_r.models.peer_disclosure import (
    BLIND_SEED,
    BLIND_SHARE,
    DRAFT_INSTRUCTION,
    UPSERT_DISCLOSURE,
    PeerDisclosure,
    PeerTableReading,
    blind_sample,
    to_disclosures,
)
from carbon_r.peer_disclosures_file import VERIFIED_FILE, read_file
from carbon_r.peer_sources import TABLE_PAGES
from carbon_r.rag.chunking import extract_pages
from carbon_r.sources import BY_ID, PEERS, REGISTRY, UPSERT, check_local_files
from carbon_r.tools.extract import _parse, rasterise_page, request_hash

DRAFT_DIR = PROJECT_ROOT / "data" / "generated" / "peer_drafts"
REPLY_CACHE = PROJECT_ROOT / "data" / "generated" / ".cache" / "peer_drafts"

DPI = 200
# "original", not the extractor's "high": "high" lets the provider scale a page down to fit,
# and a footnote superscript on a dense table is exactly the detail that would be lost.
DETAIL = "original"
REQUEST_HASH = request_hash(DRAFT_INSTRUCTION, PeerTableReading)

_SCOPE = re.compile(r"\bscope\s*[123]\b", re.IGNORECASE)
_CO2E = re.compile(r"co2\s*-?\s*e|tco2", re.IGNORECASE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- scan ---------------------------------------------------------------------------------


def scan(source_id: str) -> int:
    problems = check_local_files((source_id,))
    if problems:
        print("Cannot scan:", *problems, sep="\n  ")
        return 1
    pages = extract_pages(BY_ID[source_id].path)
    hits = [p for p in pages if _SCOPE.search(p.text) and _CO2E.search(p.text)]
    print(f"{source_id}: {len(hits)} of {len(pages)} pages name a scope and a CO2e unit")
    for page in hits:
        scopes = sorted({m.group(0).lower() for m in _SCOPE.finditer(page.text)})
        print(f"  PDF p.{page.pdf_page:<4} {', '.join(scopes)}")
    print(f"registered in TABLE_PAGES: {TABLE_PAGES.get(source_id, 'none')}")
    return 0


# --- draft --------------------------------------------------------------------------------


def _read_page(
    client: OpenAI, model: str, source_id: str, image: Path, pdf_page: int
) -> tuple[PeerTableReading, dict, str, bool]:
    """The reading for one page: from the reply cache, or one model call."""
    REPLY_CACHE.mkdir(parents=True, exist_ok=True)
    cached = REPLY_CACHE / f"{source_id}-p{pdf_page}-{model}-{DPI}dpi-{DETAIL}-{REQUEST_HASH}.json"
    if cached.exists():
        entry = json.loads(cached.read_text())
        return (
            PeerTableReading.model_validate(entry["reading"]),
            entry["usage"],
            entry["read_at"],
            True,
        )
    usage: list[dict] = []
    reading = _parse(client, model, [image], DRAFT_INSTRUCTION, PeerTableReading, DETAIL, usage)
    read_at = datetime.now(UTC).isoformat(timespec="seconds")
    entry = {
        "reading": reading.model_dump(),
        "usage": usage[0] if usage else {},
        "read_at": read_at,
        "image_sha256": _sha256(image),
    }
    cached.write_text(json.dumps(entry, indent=2))
    return reading, entry["usage"], read_at, False  # type: ignore[return-value]


def draft(source_id: str, model: str) -> int:
    if source_id not in TABLE_PAGES:
        print(f"{source_id} has no TABLE_PAGES entry; run `scan` and register its pages first.")
        return 1
    problems = check_local_files((source_id,))
    if problems:
        print("Refusing to draft:", *problems, sep="\n  ")
        return 1
    settings = get_settings()
    if not settings.llm_configured:
        print("OPENAI_API_KEY is not set.")
        return 1
    if model == settings.openai_model:
        print(f"Refusing: {model} is the configured extractor; the drafter must differ.")
        return 1

    record, peer = BY_ID[source_id], PEERS[source_id]
    client = OpenAI(api_key=settings.openai_api_key)
    pages, rows, calls = [], [], 0
    dropped: Counter[str] = Counter()
    tokens: Counter[str] = Counter()
    all_problems: list[str] = []
    for pdf_page in TABLE_PAGES[source_id]:
        image = rasterise_page(record.path, pdf_page, DPI)
        try:
            reading, usage, read_at, from_cache = _read_page(
                client, model, source_id, image, pdf_page
            )
        except OpenAIError as exc:
            # Pages already read are cached, so a re-run resumes here; nothing is written.
            print(f"{source_id} PDF p.{pdf_page}: provider error, stopping ({exc})")
            return 1
        calls += not from_cache
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            tokens[key] += usage.get(key, 0)
        page_draft = to_disclosures(reading, peer, pdf_page=pdf_page)
        dropped.update(page_draft.dropped)
        all_problems += page_draft.problems
        rows += [(row, read_at) for row in page_draft.rows]
        pages.append(
            {
                "pdf_page": pdf_page,
                "image_sha256": _sha256(image),
                "read_at": read_at,
                "from_cache": from_cache,
                "reading": reading.model_dump(),
                "cells_read": len(reading.cells),
                "kept": len(page_draft.rows),
                "dropped": page_draft.dropped,
                "problems": page_draft.problems,
            }
        )

    blind = blind_sample([row.key for row, _ in rows])
    image_by_page = {page["pdf_page"]: page["image_sha256"] for page in pages}
    out_rows = [
        row.model_copy(update={"blind_sample": row.key in blind}).model_dump(mode="json")
        | {
            "drafted_by": model,
            "drafted_at": read_at,
            "draft_request_hash": REQUEST_HASH,
            "page_image_sha256": image_by_page[row.pdf_page],
        }
        for row, read_at in rows
    ]
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    out = DRAFT_DIR / f"{source_id}.json"
    out.write_text(
        json.dumps(
            {
                "source_id": source_id,
                "source_sha256": record.sha256,
                "model": model,
                "dpi": DPI,
                "detail": DETAIL,
                "request_hash": REQUEST_HASH,
                "blind": {"seed": BLIND_SEED, "share": BLIND_SHARE, "size": len(blind)},
                "tokens": dict(tokens),
                "model_calls": calls,
                "pages": pages,
                "rows": out_rows,
            },
            indent=2,
        )
    )

    # Counts only: values are for `review`, which masks the blind sample.
    print(f"== {source_id}  [{peer.company}, {peer.sector}]  model {model}, {DPI} dpi, {DETAIL}")
    for page in pages:
        print(
            f"   PDF p.{page['pdf_page']:<4} table={page['reading']['page_has_ghg_table']!s:5} "
            f"cells read {page['cells_read']:>3}, kept {page['kept']:>3}, "
            f"dropped {page['dropped'] or {}}{'  (cache)' if page['from_cache'] else ''}"
        )
    markers = sum(1 for row in out_rows if "footnote marker" in (row["notes"] or ""))
    fused = sum(1 for row in out_rows if "malformed thousands" in (row["notes"] or ""))
    unparsed = sum(1 for row in out_rows if row["figure"] is None)
    methods = Counter(row["method"] for row in out_rows if row["scope"] == "2")
    print(
        f"   rows {len(out_rows)}; dropped {dict(dropped)}; problems {len(all_problems)}; "
        f"with footnote markers {markers}; malformed thousands {fused}; unparsed {unparsed}"
    )
    print(f"   scope 2 method: {dict(methods)}; blind sample {len(blind)} (seed {BLIND_SEED})")
    print(f"   model calls {calls}; tokens {dict(tokens)}")
    for problem in all_problems:
        print(f"   problem: {problem}")
    print(f"   -> {out.relative_to(PROJECT_ROOT)}")
    return 0


# --- load ---------------------------------------------------------------------------------


def _drafts(source_id: str | None = None) -> list[dict]:
    paths = sorted(DRAFT_DIR.glob("*.json")) if DRAFT_DIR.exists() else []
    drafts = [json.loads(p.read_text()) for p in paths]
    return [d for d in drafts if source_id in (None, d["source_id"])]


def load() -> int:
    drafts = _drafts()
    if not drafts:
        print("No drafts to load.")
        return 1
    try:
        with connect() as conn:
            for path in apply_migrations(conn):
                print(f"migration  {path.name}")
            for record in REGISTRY:
                conn.execute(UPSERT, record.row())
            for d in drafts:
                inserted = updated = 0
                for row in d["rows"]:
                    PeerDisclosure.model_validate(
                        {k: v for k, v in row.items() if k in PeerDisclosure.model_fields}
                    )
                    result = conn.execute(UPSERT_DISCLOSURE, row).fetchone()
                    if result is None:
                        continue  # unchanged, or verified and so left alone
                    inserted += result[0]
                    updated += not result[0]
                print(
                    f"{d['source_id']:28} rows {len(d['rows']):>3}  inserted {inserted:>3}  "
                    f"updated {updated:>3}  untouched {len(d['rows']) - inserted - updated:>3}"
                )
            conn.commit()
            for label, sql in (
                ("rows", "select count(*) from peer_disclosures"),
                ("verified", "select count(*) from peer_disclosures_verified"),
                ("drafts", "select count(*) from peer_disclosures where verified_at is null"),
                (
                    "method, no Scope 2",
                    "select count(*) from peer_disclosures where method is not null "
                    "and scope not in ('2', '1+2', '1+2+3')",
                ),
                (
                    "orphans",
                    "select count(*) from peer_disclosures d left join sources s "
                    "on s.id = d.source_id where s.id is null",
                ),
            ):
                print(f"  {label:22} {conn.execute(sql).fetchone()[0]}")
    except DatabaseNotConfiguredError as exc:
        print(exc)
        return 1
    return 0


# --- review -------------------------------------------------------------------------------


def review(source_id: str | None) -> int:
    drafts = _drafts(source_id)
    if not drafts:
        print("No drafts.")
        return 1
    for d in drafts:
        record = BY_ID[d["source_id"]]
        print(f"\n=== {record.title}\n    data/raw/{PEERS[d['source_id']].local_filename}")
        print(f"    drafted by {d['model']} ({d['dpi']} dpi, {d['detail']})")
        for page in d["pages"]:
            reading = page["reading"]
            rows = [r for r in d["rows"] if r["pdf_page"] == page["pdf_page"]]
            printed = rows[0]["printed_page"] if rows else None
            where = f"PDF p.{page['pdf_page']}" + (f" (printed p.{printed})" if printed else "")
            print(f"\n  -- {where}: {reading['table_title_printed']!r}")
            if reading["notes"]:
                print(f"     model notes: {reading['notes']}")
            if page["dropped"]:
                print(f"     dropped: {page['dropped']}")
            for r in rows:
                value = "[blind]" if r["blind_sample"] else r["figure_printed"]
                scope = f"S{r['scope']}" + (
                    f" cat {r['scope3_category']}" if r["scope3_category"] else ""
                )
                method = f" {r['method']}-based" if r["method"] else ""
                print(
                    f"     {r['row_label']!r:58} | {r['column_header']!r:12} | {value:>14} "
                    f"{r['unit']:8} | {scope}{method}"
                )
                if r["notes"] and not r["blind_sample"]:
                    print(f"{'':63}note: {r['notes']}")
        blind = [r for r in d["rows"] if r["blind_sample"]]
        print(f"\n  blind sample ({len(blind)} of {len(d['rows'])}, seed {d['blind']['seed']}):")
        for r in blind:
            print(f"     PDF p.{r['pdf_page']}  {r['row_label']!r}  |  {r['column_header']!r}")
    return 0


# --- leak check ---------------------------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, capture_output=True, check=False
    ).stdout.decode(errors="ignore")


def _significant(figure: str) -> bool:
    return len(re.sub(r"\D", "", figure)) >= 3


def leak_check(paths: list[str]) -> int:
    """Real peer figures may appear in ONE tracked file, `data/peer_disclosures_verified.json`,
    and only as verified values (or as that file's labelled `draft` fields). Fails on:

    - any drafted or verified figure NEW since HEAD in any other file about to be committed:
      the index (tracked and staged files), or the PATHS given (new files not yet staged);
    - a verified-file row whose figure is a draft value that was never verified.

    Figures come from the local drafts (`data/generated/peer_drafts/`) and the verified file.
    A figure already at HEAD in another file is listed, not failed: a short number such as a
    page count or a hash fragment turns up by coincidence. Read that list by eye all the same.
    """
    drafted = {r["figure_printed"] for d in _drafts() for r in d["rows"]}
    verified_rows = read_file().rows if VERIFIED_FILE.exists() else []
    verified = {row.figure_printed for row in verified_rows}
    drafted |= {row.draft.figure_printed for row in verified_rows}
    never_verified = drafted - verified
    figures = {f for f in drafted | verified if _significant(f)}
    allowed = str(VERIFIED_FILE.relative_to(PROJECT_ROOT))

    bad_in_file = [
        f"{row.source_id} p.{row.pdf_page} {row.row_label!r}"
        for row in verified_rows
        if row.figure_printed in never_verified
    ]

    names = set(paths) or set(filter(None, _git("ls-files", "-z").split("\0")))
    names.discard(allowed)
    patterns = {f: re.compile(rf"(?<![\d,.]){re.escape(f)}(?![\d,]|\.\d)") for f in figures}
    new: list[tuple[str, str]] = []
    at_head: list[tuple[str, str]] = []
    for name in sorted(names):
        path = PROJECT_ROOT / name
        if not path.is_file():
            continue
        text = path.read_text(errors="ignore")
        found = [f for f, pattern in patterns.items() if pattern.search(text)]
        if not found:
            continue
        head = _git("show", f"HEAD:{name}")
        for figure in found:
            (at_head if patterns[figure].search(head) else new).append((name, figure))
    print(
        f"{len(figures)} peer figures of 3+ digits ({len(verified)} verified, "
        f"{len(never_verified)} never verified); {len(names)} files checked besides {allowed}"
    )
    for name, figure in at_head:
        print(f"  already at HEAD (coincidence?) {name}: {figure}")
    for name, figure in new:
        print(f"  NEW {name}: {figure}")
    for where in bad_in_file:
        print(f"  UNVERIFIED VALUE in {allowed}: {where}")
    print(
        f"new since HEAD: {len(new)}; already at HEAD: {len(at_head)}; "
        f"unverified values in {allowed}: {len(bad_in_file)}"
    )
    return 1 if new or bad_in_file else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan").add_argument("source_id")
    d = sub.add_parser("draft")
    d.add_argument("source_id")
    d.add_argument("--model", required=True)
    sub.add_parser("load")
    sub.add_parser("review").add_argument("source_id", nargs="?")
    sub.add_parser("leak-check").add_argument("paths", nargs="*")
    args = parser.parse_args()
    if args.command == "scan":
        return scan(args.source_id)
    if args.command == "draft":
        return draft(args.source_id, args.model)
    if args.command == "load":
        return load()
    if args.command == "review":
        return review(args.source_id)
    return leak_check(args.paths)


if __name__ == "__main__":
    sys.exit(main())
