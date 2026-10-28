"""Chunk, embed and store the peer reports (Tier 2 Block 4 Part B). Idempotent: safe to re-run.

    uv run python scripts/ingest_peer.py --preview     no database, no embedding
    uv run python scripts/ingest_peer.py               (or: make ingest-peer)

The same chunker as the methodology corpus (`carbon_r.rag.chunking`, same CHUNKER_VERSION),
stored in `document_chunks` with corpus 'peer' and each report's company, sector and report
year on every row. Order of work, as in `ingest_methodology.py`:

1. every local PDF is checked against the sha256 in the registry (from
   `data/peer_sources.json`); a mismatch stops the run before anything is read;
2. migrations are applied and the registry is upserted;
3. each report is chunked and handed to `rag.ingest.ingest_source`, which leaves it
   untouched (second run) or replaces all of its rows in one transaction.

`--preview` is the gate before anything is embedded. Per report it prints the page and word
counts, the near-empty pages (no text layer), how often each candidate page offset is
confirmed by the page's own footer against the 80% rule (`chunking.choose_offset`) - from
which the author sets `peer_sources.PRINTED_PAGE_OFFSETS` - and the chunks of the first page
that looks like a GHG table, which is where extraction does worst.

Local only. The reports are copyright of their issuers and git-ignored, so this never runs
in CI; the offline tests cover the offset rule, the registry and the replace logic.
"""

from __future__ import annotations

import argparse
import re
import sys

from carbon_r.db import DatabaseNotConfiguredError, apply_migrations, connect
from carbon_r.rag.chunking import (
    CHUNKER_VERSION,
    OFFSET_MATCH_THRESHOLD,
    choose_offset,
    chunk_page,
    chunk_pages,
    embedding_input,
    extract_pages,
    footer_numbers,
)
from carbon_r.rag.embed import configured_model, openai_embedder
from carbon_r.rag.ingest import PeerMetadata, PostgresChunkStore, ingest_source
from carbon_r.sources import BY_ID, PEER_IDS, PEERS, REGISTRY, UPSERT, check_local_files

# A page a person would call a GHG table: it names Scope 1 and a CO2e unit.
_SCOPE_1 = re.compile(r"\bscope\s*1\b", re.IGNORECASE)
_CO2E = re.compile(r"co2\s*-?\s*e|tco2", re.IGNORECASE)

# Fewer words than this and a page has, in practice, no text layer.
_NEAR_EMPTY_WORDS = 5


def preview(ids: tuple[str, ...]) -> int:
    problems = check_local_files(ids)
    if problems:
        print("Cannot preview:", *problems, sep="\n  ")
        return 1
    print(
        f"chunker {CHUNKER_VERSION}; offset rule: one offset must match at least "
        f"{OFFSET_MATCH_THRESHOLD:.0%} of the pages with a readable footer number\n"
    )
    for source_id in ids:
        record, peer = BY_ID[source_id], PEERS[source_id]
        pages = extract_pages(record.path)
        words = sum(len(p.text.split()) for p in pages)
        empty = [p.pdf_page for p in pages if len(p.text.split()) < _NEAR_EMPTY_WORDS]
        decision = choose_offset([(p.pdf_page, footer_numbers(p.layout_text)) for p in pages])
        rates = "  ".join(
            f"offset {k}: {decision.matches[k]}/{decision.readable} ({decision.rate(k):.0%})"
            for k in sorted(decision.matches)
        )
        print(f"== {source_id}  [{peer.company}, {peer.sector}, report_year {peer.report_year}]")
        print(f"   {record.title}")
        print(f"   pages {len(pages)}; words {words}; near-empty pages {empty or 'none'}")
        print(f"   readable footers {decision.readable}/{len(pages)}; {rates}")
        print(
            f"   decision: offset {decision.offset} - {decision.reason}; "
            f"registered now: {record.printed_page_offset}"
        )

        table = next((p for p in pages if _SCOPE_1.search(p.text) and _CO2E.search(p.text)), None)
        if table is None:
            print("   no page names both Scope 1 and a CO2e unit\n")
            continue
        chunks = chunk_page(table, offset=decision.offset)
        print(f"   first GHG-table-like page: PDF p.{table.pdf_page}, {len(chunks)} chunk(s)")
        for chunk in chunks:
            if _SCOPE_1.search(chunk.text):
                print(f"   --- chunk {chunk.chunk_index} (printed page {chunk.printed_page})")
                print(f"   {chunk.text}")
                break
        print()
    return 0


def ingest(ids: tuple[str, ...]) -> int:
    problems = check_local_files(ids)
    if problems:
        print("Refusing to ingest:", *problems, sep="\n  ")
        return 1

    model = configured_model()
    usage: list[dict] = []
    embed = openai_embedder(model, usage)
    print(f"embedding model {model}; chunker {CHUNKER_VERSION}")

    try:
        with connect() as conn:
            for path in apply_migrations(conn):
                print(f"migration  {path.name}")
            for record in REGISTRY:
                conn.execute(UPSERT, record.row())
            conn.commit()
            print(f"sources    {len(REGISTRY)} registered")

            store = PostgresChunkStore(conn)
            for source_id in ids:
                record, peer = BY_ID[source_id], PEERS[source_id]
                chunks = chunk_pages(extract_pages(record.path), offset=record.printed_page_offset)
                outcome = ingest_source(
                    store,
                    source_id=source_id,
                    corpus="peer",
                    chunks=chunks,
                    chunker_version=CHUNKER_VERSION,
                    embedding_model=model,
                    embed=embed,
                    embed_input=lambda text, title=record.title: embedding_input(title, text),
                    peer=PeerMetadata(peer.company, peer.sector, peer.report_year),
                )
                conn.commit()
                print(
                    f"{source_id:30} {outcome.action:9} inserted={outcome.inserted} "
                    f"deleted={outcome.deleted} chunks={len(chunks)}"
                )

            rows = conn.execute(
                """
                select source_id, count(*), count(*) filter (where printed_page is null),
                       count(distinct (chunker_version, embedding_model)),
                       count(*) filter (where company is null or sector is null
                                        or report_year is null)
                from document_chunks where corpus = 'peer'
                group by source_id order by source_id
                """
            ).fetchall()
    except DatabaseNotConfiguredError as exc:
        print(f"Cannot ingest: {exc}")
        return 1

    for source_id, total, no_printed, versions, no_meta in rows:
        print(
            f"stored     {source_id:30} rows={total} no_printed_page={no_printed} "
            f"version_model_pairs={versions} missing_metadata={no_meta}"
        )
    tokens = sum(u.get("total_tokens", 0) for u in usage)
    print(f"embedding tokens this run: {tokens}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preview", action="store_true", help="print evidence; no DB, no API")
    parser.add_argument("--source", choices=PEER_IDS, action="append")
    args = parser.parse_args(argv)
    ids = tuple(args.source) if args.source else PEER_IDS
    return preview(ids) if args.preview else ingest(ids)


if __name__ == "__main__":
    sys.exit(main())
