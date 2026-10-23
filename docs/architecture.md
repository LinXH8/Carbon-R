# Architecture

One FastAPI process, one Postgres database, one model provider. The model chooses tools and
transcribes; Python does every calculation; two audits check the reply before it is shown.

```mermaid
flowchart LR
    user([Browser page]) -->|/upload, /chat| api[FastAPI<br/>main.py]
    api --> loop[Agent loop<br/>agent/loop.py<br/>max 8 turns]
    loop <-->|tool calls| provider[(OpenAI<br/>Responses API)]

    subgraph tools[Four tools - agent/tools.py]
        extract[extract_from_document<br/>classify, transcribe, one retry]
        lookup[lookup_emission_factor<br/>exact SQL]
        calc[calculate_emissions<br/>plain Python]
        method[search_methodology<br/>pgvector + judge]
    end

    loop --> extract & lookup & calc & method
    extract -->|pdftoppm page images| vision[(OpenAI vision)]
    lookup -->|category, unit, year| factors[(emission_factors)]
    method -->|cosine, filtered by model<br/>and chunker version| chunks[(document_chunks)]
    method -->|answerability, quotes| judge[(OpenAI judge)]
    extract -.->|act_N handles| calc
    lookup -.->|factor_N handles| calc

    loop --> audit{Audits on the reply<br/>unverified_figures<br/>unverified_quotes}
    audit --> api
```

What the diagram encodes:

- **Handles, not values.** `calculate_emissions` receives `act_N` and `factor_N` handles
  into the session, never a number typed by the model (dotted arrows).
- **Two retrieval paths that never cross.** Factors come by exact SQL
  ([ADR 0001](adr/0001-exact-sql-for-factors.md)); methodology prose comes by similarity
  ([ADR 0002](adr/0002-postgres-pgvector.md)). Nothing from `search_methodology` can enter
  a calculation.
- **Both audits run on every reply.** A number no tool produced, or a quoted span no tool
  returned, is listed on the response and shown on the page.

Deployment shape (`docker compose up`): an `app` container (this process, plus the loaders
that seed the factors on start) and a `db` container (`pgvector/pgvector:pg17`).
