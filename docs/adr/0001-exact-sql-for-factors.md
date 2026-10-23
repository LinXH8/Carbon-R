# ADR 0001 — Emission factors by exact SQL, never by vector search

**Status:** accepted (Tier 1; restated Tier 2 Block 5).

## Context

Every emissions figure is activity × factor. The factor must be the published value for
the right category, unit and year, with its provenance. A retrieval-augmented design is
the default shape for "an LLM app with a knowledge base", and would put the factor tables
in a vector index alongside the prose.

A factor found by similarity is the nearest row to a query, not the right row. "Grid
electricity Singapore 2025" is close to the 2024 row, to the 2023 row and to Malaysia's;
the nearest one is a wrong number with a correct-looking citation. That failure is silent:
nothing in the result says it was a near miss.

## Decision

Factors are fetched with `where category = … and unit = … and year = …` against the
table's unique key (`tools/factors.py`). Similarity search is allowed only for prose
(`search_methodology`) and, later, for matching a spend description to a category, after
which SQL fetches the exact value. There is no fallback to a public factor API at request
time.

When the year is not published, the rule is explicit rather than "closest": the latest year
at or before the activity year, at most two years back, stated on the result; beyond that,
`no_results`.

## Consequences

- A missing factor is a refusal (`no_results`), which the agent reports and asks about.
  The evals score that refusal as correct; a guess would be scored as a miss.
- Category and unit strings must match the publisher's exactly. That is friction for the
  model (the prompt lists them) and it is the point: a wrong string fails loudly.
- Substitution across years is visible in every citation (`year_requested`, `year_used`,
  `substituted`), and is tested.
- Evidence: the synthgen v1 eval scores every emissions figure exact (166 of 166), and the
  DESNZ back-series gap (no 2024 fuel factor) produces a refusal on the document built to
  test it, not a 2025 value.
