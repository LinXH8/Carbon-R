"""Chunk, embed and store the GHG Protocol methodology corpus. Idempotent: safe to re-run.

    uv run python scripts/ingest_methodology.py --preview     no database, no embedding
    uv run python scripts/ingest_methodology.py               (or: make ingest-methodology)

Order of work:

1. every local PDF is checked against the sha256 in the source registry, and a mismatch
   stops the run before anything is read - different bytes are a different document;
2. migrations are applied and the registry is upserted;
3. each methodology source is chunked and handed to `rag.ingest.ingest_source`, which
   either leaves it untouched (second run) or replaces all of its rows in one transaction.

Local only. The corpus is git-ignored and its terms forbid redistribution (see
`carbon_r.sources`), so this never runs in CI; the offline tests cover the chunker and the
replace logic without it.

`--preview` exists for one reason: a human reads real chunks before any are embedded. It
prints the chunks of the Corporate Standard's PDF page 20, a two-column page with the margin
tab, which is where a reading-order or cleaning defect would show first.
"""

from __future__ import annotations

import argparse
import sys

from carbon_r.db import DatabaseNotConfiguredError, apply_migrations, connect
from carbon_r.rag.chunking import (
    CHUNKER_VERSION,
    EMBED_TITLE,
    OVERLAP_WORDS,
    WINDOW_WORDS,
    chunk_pages,
    embedding_input,
    extract_pages,
)
from carbon_r.rag.embed import configured_model, openai_embedder
from carbon_r.rag.ingest import PostgresChunkStore, ingest_source
from carbon_r.sources import BY_ID, METHODOLOGY_IDS, REGISTRY, UPSERT, check_local_files

PREVIEW_SOURCE = "ghgp_corporate"
PREVIEW_PAGE = 20


def preview() -> int:
    record = BY_ID[PREVIEW_SOURCE]
    pages = extract_pages(record.path)
    page = pages[PREVIEW_PAGE - 1]
    chunks = chunk_pages([page], offset=record.printed_page_offset)
    print(
        f"{record.title}\nPDF page {PREVIEW_PAGE}, printed page {chunks[0].printed_page}, "
        f"{len(page.text.split())} words -> {len(chunks)} chunks "
        f"(window {WINDOW_WORDS}, overlap {OVERLAP_WORDS}, chunker {CHUNKER_VERSION})\n"
    )
    for chunk in chunks:
        print(f"--- chunk {chunk.chunk_index} ({len(chunk.text.split())} words)")
        print(chunk.text)
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
    print(f"embedding model {model}; chunker {CHUNKER_VERSION} (title embedded: {EMBED_TITLE})")

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
                record = BY_ID[source_id]
                chunks = chunk_pages(extract_pages(record.path), offset=record.printed_page_offset)
                outcome = ingest_source(
                    store,
                    source_id=source_id,
                    corpus="methodology",
                    chunks=chunks,
                    chunker_version=CHUNKER_VERSION,
                    embedding_model=model,
                    embed=embed,
                    embed_input=lambda text, title=record.title: embedding_input(title, text),
                )
                conn.commit()
                print(
                    f"{source_id:16} {outcome.action:9} inserted={outcome.inserted} "
                    f"deleted={outcome.deleted} chunks={len(chunks)}"
                )

            rows = conn.execute(
                """
                select source_id, count(*), count(*) filter (where printed_page is null),
                       count(distinct (chunker_version, embedding_model))
                from document_chunks where corpus = 'methodology'
                group by source_id order by source_id
                """
            ).fetchall()
    except DatabaseNotConfiguredError as exc:
        print(f"Cannot ingest: {exc}")
        return 1

    for source_id, total, no_printed, versions in rows:
        print(
            f"stored     {source_id:16} rows={total} no_printed_page={no_printed} "
            f"version_model_pairs={versions}"
        )
    tokens = sum(u.get("total_tokens", 0) for u in usage)
    print(f"embedding tokens this run: {tokens}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preview", action="store_true", help="print chunks; no DB, no API")
    parser.add_argument("--source", choices=METHODOLOGY_IDS, action="append")
    args = parser.parse_args(argv)
    if args.preview:
        return preview()
    return ingest(tuple(args.source) if args.source else METHODOLOGY_IDS)


if __name__ == "__main__":
    sys.exit(main())
