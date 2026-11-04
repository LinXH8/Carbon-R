# Architecture

One FastAPI process, one Postgres database, one model provider. The model chooses tools and
transcribes; Python does every calculation; two audits check the reply before it is shown.

```mermaid
flowchart LR
    user([Browser page]) -->|/upload, /chat| api[FastAPI<br/>main.py]
    api --> loop[Agent loop<br/>agent/loop.py<br/>max 8 turns]
    loop <-->|tool calls| provider[(OpenAI<br/>Responses API)]

    subgraph tools[Six tools - agent/tools.py]
        extract[extract_from_document<br/>classify, transcribe, one retry]
        lookup[lookup_emission_factor<br/>exact SQL]
        calc[calculate_emissions<br/>plain Python]
        method[search_methodology<br/>pgvector + judge]
        peer[search_peer_disclosures<br/>vector + keyword, RRF, judge]
        peerfig[lookup_peer_disclosure<br/>exact SQL]
    end

    loop --> extract & lookup & calc & method & peer & peerfig
    extract -->|pdftoppm page images| vision[(OpenAI vision)]
    lookup -->|category, unit, year| factors[(emission_factors)]
    method -->|cosine, filtered by model<br/>and chunker version| chunks[(document_chunks)]
    method -->|answerability, quotes| judge[(OpenAI judge)]
    peer -->|cosine + tsvector, filtered by<br/>company, sector, year| chunks
    peer -->|answerability, quotes| judge
    peerfig -->|company, scope, method, year| verified[(peer_disclosures_verified)]
    extract -.->|act_N handles| calc
    lookup -.->|factor_N handles| calc

    loop --> audit{Audits on the reply<br/>unverified_figures<br/>unverified_quotes}
    audit -->|peer tool used:<br/>caveat attached| api
```

What the diagram encodes:

- **Handles, not values.** `calculate_emissions` receives `act_N` and `factor_N` handles
  into the session, never a number typed by the model (dotted arrows).
- **Two retrieval paths that never cross.** Factors come by exact SQL
  ([ADR 0001](adr/0001-exact-sql-for-factors.md)); methodology prose comes by similarity
  ([ADR 0002](adr/0002-postgres-pgvector.md)), and so do peer-report passages. Nothing from
  either search can enter a calculation.
- **Peer passages are prose, never figures.** Their PDF text layers fuse footnote markers
  onto table numbers, so the figure audit refuses any number in the agent's own words that
  only peer text could have supplied, and the tool withholds table-like quotes (cited by
  page only). Peer figures come only from `lookup_peer_disclosure`: exact SQL on the view
  of rows verified by hand against the report page.
- **The peer caveat is code, not prose.** When a turn used a peer tool, the loop appends
  the fixed self-reported / not-assured sentence after the audits have read the model's
  words.
- **Both audits run on every reply.** A number no tool produced, or a quoted span no tool
  returned, is listed on the response and shown on the page. They record; they do not
  block, so a flagged reply is still shown.

Deployment shape (`docker compose up`): an `app` container (this process, plus the loaders
that seed the factors on start) and a `db` container (`pgvector/pgvector:pg17`).
