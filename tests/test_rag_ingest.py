"""Replace-per-source ingestion: a re-run changes nothing, a parameter change leaves no old rows.

The store is in memory and mirrors the table's identity (source, chunker version, embedding
model, page, chunk index), so the property under test is the one the database would see.
"""

from dataclasses import dataclass, field

import pytest

from carbon_r.rag.chunking import Page, chunk_pages, chunker_version
from carbon_r.rag.ingest import ChunkRow, StoredSummary, decide, ingest_source, positions


@dataclass
class FakeStore:
    rows: list[ChunkRow] = field(default_factory=list)
    replaced: int = 0

    def summary(self, source_id, chunker_version, embedding_model):
        mine = [r for r in self.rows if r.source_id == source_id]
        current = [
            r
            for r in mine
            if r.chunker_version == chunker_version and r.embedding_model == embedding_model
        ]
        return StoredSummary(
            current=positions([r.chunk for r in current]),
            other_rows=len(mine) - len(current),
        )

    def replace_source(self, source_id, rows):
        before = len(self.rows)
        self.rows = [r for r in self.rows if r.source_id != source_id] + list(rows)
        self.replaced += 1
        return before - (len(self.rows) - len(rows))


class CountingEmbedder:
    def __init__(self):
        self.calls = 0

    def __call__(self, texts):
        self.calls += 1
        return [[0.0] * 1536 for _ in texts]


PAGES = [
    Page(1, " ".join(f"one{i}" for i in range(40)), ""),
    Page(2, " ".join(f"two{i}" for i in range(40)), ""),
]


def run(store, embed, *, window, overlap, model="model-a", pages=PAGES, source="src"):
    return ingest_source(
        store,
        source_id=source,
        corpus="methodology",
        chunks=chunk_pages(pages, offset=None, window=window, overlap=overlap),
        chunker_version=chunker_version(window, overlap),
        embedding_model=model,
        embed=embed,
    )


def test_a_second_run_is_unchanged_and_embeds_nothing():
    store, embed = FakeStore(), CountingEmbedder()
    first = run(store, embed, window=20, overlap=5)
    second = run(store, embed, window=20, overlap=5)
    assert first.action == "replaced" and first.inserted == len(store.rows)
    assert second.action == "unchanged" and second.inserted == 0
    assert embed.calls == 1
    assert store.replaced == 1


def test_changing_chunk_parameters_leaves_no_row_from_the_old_version():
    store, embed = FakeStore(), CountingEmbedder()
    run(store, embed, window=20, overlap=5)
    outcome = run(store, embed, window=15, overlap=3)
    new_version = chunker_version(15, 3)
    assert outcome.action == "replaced" and outcome.deleted > 0
    assert {r.chunker_version for r in store.rows} == {new_version}
    # And the run after that is a no-op, so idempotence holds at the new version too.
    assert run(store, embed, window=15, overlap=3).action == "unchanged"


def test_changing_the_embedding_model_replaces_the_old_models_rows():
    store, embed = FakeStore(), CountingEmbedder()
    run(store, embed, window=20, overlap=5, model="model-a")
    run(store, embed, window=20, overlap=5, model="model-b")
    assert {r.embedding_model for r in store.rows} == {"model-b"}


def test_changed_text_at_the_same_positions_is_replaced():
    store, embed = FakeStore(), CountingEmbedder()
    run(store, embed, window=20, overlap=5)
    edited = [PAGES[0], Page(2, PAGES[1].text.replace("two7", "TWO7"), "")]
    assert run(store, embed, window=20, overlap=5, pages=edited).action == "replaced"


def test_identical_text_on_two_pages_is_two_rows():
    same = " ".join(f"w{i}" for i in range(10))
    store, embed = FakeStore(), CountingEmbedder()
    run(store, embed, window=20, overlap=5, pages=[Page(1, same, ""), Page(2, same, "")])
    assert sorted(r.chunk.pdf_page for r in store.rows) == [1, 2]


def test_one_source_is_replaced_without_touching_another():
    store, embed = FakeStore(), CountingEmbedder()
    run(store, embed, window=20, overlap=5, source="a")
    run(store, embed, window=20, overlap=5, source="b")
    run(store, embed, window=15, overlap=3, source="a")
    assert {r.chunker_version for r in store.rows if r.source_id == "b"} == {chunker_version(20, 5)}


def test_rows_under_another_version_force_a_replace_even_if_the_current_ones_match():
    chunks = chunk_pages(PAGES, offset=None, window=20, overlap=5)
    stored = StoredSummary(current=positions(chunks), other_rows=3)
    assert decide(chunks, stored) == "replace"


def test_no_chunks_is_refused_rather_than_deleting_the_source():
    with pytest.raises(ValueError):
        run(FakeStore(), CountingEmbedder(), window=20, overlap=5, pages=[Page(1, "", "")])
