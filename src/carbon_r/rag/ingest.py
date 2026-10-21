"""Replace a source's chunks, or leave them alone. The decision is pure; the store is injected.

A source is ingested as a unit. Its rows are either already exactly right, or they are all
replaced inside one transaction - there is no partial update to reason about:

    unchanged   the stored rows for (source, chunker_version, embedding_model) are exactly
                the chunks computed now - same positions, same text hashes - and the source
                has no row under any OTHER version or model. Nothing is embedded or written.
    replace     anything else. Every row of the source is deleted (stale versions, stale
                models and the current pair alike) and the fresh chunks are inserted.

Why "no row under any other version" is part of `unchanged`: identity includes the chunker
version, so after a parameter change the new chunks never collide with the old ones. A check
that only asked "are the current-version rows right?" would pass on the second run while the
retired rows were still in the table and still searchable by anything that forgot the version
filter. Deleting the whole source closes that.

`content_sha256` is compared as well as position because the cleaning can change text
without changing how many chunks a page makes, and a hand-bumped CHUNKER_REVISION is the
only other line of defence against that.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from carbon_r.rag.chunking import Chunk
from carbon_r.rag.embed import Embedder, to_pgvector

Position = tuple[int, int, str]  # (pdf_page, chunk_index, content_sha256)


@dataclass(frozen=True)
class StoredSummary:
    """What the store holds for one source, as the decision needs it."""

    current: frozenset[Position]  # rows under the current (version, model) pair
    other_rows: int  # rows of this source under any other version or model


@dataclass(frozen=True)
class ChunkRow:
    source_id: str
    corpus: str
    chunker_version: str
    embedding_model: str
    chunk: Chunk
    embedding: list[float]


class ChunkStore(Protocol):
    def summary(
        self, source_id: str, chunker_version: str, embedding_model: str
    ) -> StoredSummary: ...

    def replace_source(self, source_id: str, rows: Sequence[ChunkRow]) -> int:
        """Delete every row of `source_id`, insert `rows`, atomically. Returns rows deleted."""
        ...


@dataclass(frozen=True)
class IngestOutcome:
    source_id: str
    action: Literal["unchanged", "replaced"]
    inserted: int
    deleted: int


def positions(chunks: Sequence[Chunk]) -> frozenset[Position]:
    return frozenset((c.pdf_page, c.chunk_index, c.content_sha256) for c in chunks)


def decide(chunks: Sequence[Chunk], stored: StoredSummary) -> Literal["unchanged", "replace"]:
    if stored.other_rows == 0 and stored.current == positions(chunks):
        return "unchanged"
    return "replace"


def ingest_source(
    store: ChunkStore,
    *,
    source_id: str,
    corpus: str,
    chunks: Sequence[Chunk],
    chunker_version: str,
    embedding_model: str,
    embed: Embedder,
) -> IngestOutcome:
    """Bring one source's stored chunks in line with `chunks`. Embeds only on replace."""
    if not chunks:
        raise ValueError(f"{source_id}: no chunks to ingest; refusing to delete its rows.")
    stored = store.summary(source_id, chunker_version, embedding_model)
    if decide(chunks, stored) == "unchanged":
        return IngestOutcome(source_id, "unchanged", inserted=0, deleted=0)

    vectors = embed([c.text for c in chunks])
    rows = [
        ChunkRow(source_id, corpus, chunker_version, embedding_model, chunk, vector)
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    deleted = store.replace_source(source_id, rows)
    return IngestOutcome(source_id, "replaced", inserted=len(rows), deleted=deleted)


class PostgresChunkStore:
    """`ChunkStore` over a psycopg connection. The caller owns the transaction boundary."""

    _SUMMARY = """
    select pdf_page, chunk_index, content_sha256,
           (chunker_version = %(version)s and embedding_model = %(model)s) as is_current
    from document_chunks where source_id = %(source)s
    """

    _INSERT = """
    insert into document_chunks (source_id, corpus, chunker_version, embedding_model,
        pdf_page, chunk_index, printed_page, section, text, content_sha256, embedding)
    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
    """

    def __init__(self, conn) -> None:
        self.conn = conn

    def summary(self, source_id: str, chunker_version: str, embedding_model: str) -> StoredSummary:
        rows = self.conn.execute(
            self._SUMMARY,
            {"version": chunker_version, "model": embedding_model, "source": source_id},
        ).fetchall()
        current = frozenset((r[0], r[1], r[2]) for r in rows if r[3])
        return StoredSummary(current=current, other_rows=sum(1 for r in rows if not r[3]))

    def replace_source(self, source_id: str, rows: Sequence[ChunkRow]) -> int:
        with self.conn.transaction():
            deleted = self.conn.execute(
                "delete from document_chunks where source_id = %s", (source_id,)
            ).rowcount
            with self.conn.cursor() as cur:
                cur.executemany(
                    self._INSERT,
                    [
                        (
                            r.source_id,
                            r.corpus,
                            r.chunker_version,
                            r.embedding_model,
                            r.chunk.pdf_page,
                            r.chunk.chunk_index,
                            r.chunk.printed_page,
                            r.chunk.section,
                            r.chunk.text,
                            r.chunk.content_sha256,
                            to_pgvector(r.embedding),
                        )
                        for r in rows
                    ],
                )
        return deleted
