# ADR 0002 — Postgres with pgvector, not a separate vector database; no ANN index yet

**Status:** accepted (Tier 2 Block 4; recorded Block 5).

## Context

Tier 2 Block 4 added similarity search over GHG Protocol methodology prose; Part B will add
peer sustainability reports. The options were a dedicated vector store (a hosted service or
a separate container) or the `vector` extension in the Postgres that already holds the
factors and the source registry.

The corpus is small: 768 chunks from three documents. The retrieval eval is meant to be
reproducible from a commit SHA.

## Decision

1. **pgvector in the existing Postgres.** `document_chunks` sits beside `emission_factors`
   and `sources`, with a foreign key to `sources`. One database, one connection, one
   migration path, and `docker compose up` stays two containers.
2. **No approximate index (HNSW or IVFFlat) yet.** Every search is an exact scan, filtered
   first on corpus, embedding model and chunker version.

## Why

- **One store keeps provenance joins honest.** A passage's title, licence and URL come from
  `sources` by foreign key in the same query, not from a copy kept in another system's
  metadata that could drift.
- **A second database buys nothing at this size.** It would add a service, credentials and
  a consistency boundary in exchange for speed that 768 rows do not need.
- **Exact scan is deterministic.** An ANN index trades recall for speed and can return a
  different neighbour set after a rebuild. The eval's two dev runs on identical retrieval
  matched on 24 of 24 questions; with an approximate index that equality would not be
  guaranteed, and a hit-rate change could come from the index rather than the code.
- **The filters must apply before ranking.** Rows from two embedding models or two chunker
  versions must never be compared. A filtered exact scan does that by construction.

## Consequences and the upgrade path

- Search cost grows linearly with the corpus. That is fine for three standards and a dozen
  peer reports.
- **Add HNSW when** the searchable set per filter reaches tens of thousands of chunks, or
  when measured search latency becomes a visible share of a `/chat` turn. Then: an HNSW
  index on `embedding vector_cosine_ops`, a partial index or a filtered query plan so the
  model/version filter still applies, and a recall check against exact scan on the eval key
  before switching, recorded with both SHAs.
- Moving to a separate vector database would be justified by scale or multi-tenant
  isolation, neither of which this project has.
