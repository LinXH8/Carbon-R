# Tier 1 Block 5 — the interface, and Tier 1 signed off

Dated 18 Oct 2026. Block 4 left a working agent behind two HTTP endpoints and no way for a
person to see either. Every figure the project had produced had been read out of a
script's stdout.

```
Block 4   person ──► curl ──► JSON ──► read the prose and hope
Block 5   person ──► page ──► tool calls as they happen ──► the figure, its factor,
                                                           its source, its disclosures
```

Outcome: a single page with no build step, the tool calls streamed as they land, and — the
part that matters — **the number on screen comes from `calculate_emissions`, not from the
model's prose.**

---

## The measurement

`make flow-10`: the full upload → chat → answer flow, live, over ten bills.

| Check | First five (`elec_001`–`005`) | Ten spanning both factor years |
|---|---|---|
| answered | 5 of 5 | **10 of 10** |
| figure matches the answer key | 5 of 5 | **10 of 10** |
| cited (factor, publisher, year used) | 5 of 5 | **10 of 10** |
| disclosed (not assured; both years when substituted) | 5 of 5 | **10 of 10** |
| no stray numbers (figure audit empty) | 5 of 5 | **10 of 10** |
| factor-year substituted | 0 of 5 | **5 of 10** |
| turns used | 20 across 5 | **40 across 10** |

Four turns per document, every document, with no retries: extract, look up, calculate,
answer. Every expected figure matched to the last decimal place — for example `elec_013`,
2474 kWh × 0.402 = 994.548 kg, with the 2024 factor substituted for a 2025 activity year.

Tests: **228 passing** (`make check`), 21 of them new in this block, all offline — no
`.env`, no database, no API key, no network.

### What this does not say

- **Ten documents of one layout, asked one question, is not an accuracy measurement**, and
  the script says so on every line it prints. `scripts/eval_extraction.py` (18 of 18 on 24
  fields) remains the measured number. Ten passes here mean the wiring holds, not that the
  pipeline is accurate.
- The count was 5 in Block 4 and 10 here, and both runs share the single `clean` layout.
  Nothing in either number speaks to a scan, a phone photograph, MWh units, or a billing
  period that straddles a month.
- **The interface itself is unmeasured.** The figure panel, the streamed steps and the
  honesty states have been exercised by hand and by offline tests against the shape of the
  response. The drag physics have been reasoned and reviewed, not instrumented.

---

## Architecture

```
GET  /                 ──► static/index.html          one page, no build step
GET  /static/*         ──► app.css, app.js, spring.js

POST /chat             ──► run_agent ──► ChatResponse  the Block 4 contract, unchanged
POST /chat/stream      ──► run_agent(on_event=…) ──► SSE
                             │
                             ├─ {type: turn}     a provider call is starting
                             ├─ {type: tool}     one tool result, as it lands
                             └─ {type: final}    the whole ChatResponse, exactly once
```

`ChatResponse` gains `figures: list[FigureOut]` — the output of every
`calculate_emissions` in this turn, each carrying `kg_co2e`, `tonnes_co2e`, the
`derivation`, `method`, `basis`, the activity amount and unit, the full `FactorOut`
citation, the disclosure flags and the disclosure sentences.

---

## Decisions

### The page renders `figures`. It never reads a number out of `reply`

This is the decision the rest of the block hangs from.

The obvious way to build this page is to show the agent's reply, because the reply already
contains the figure, nicely formatted, in a sentence. That page would be a rule-1
violation wearing a different hat: the number a user reads would be a number the *model*
wrote, with the deterministic calculation reduced to an influence on it. Moving the
arithmetic out of the model and then quoting the model's transcription of it back to the
user buys nothing.

So the structured payload that already existed in the session workspace now crosses the
HTTP boundary as a typed response model, and the page renders from that. `reply` is
displayed too — labelled "The agent's wording" — but nothing is parsed out of it. The only
transformation `app.js` performs on a number is inserting thousands separators into a
string; it never converts one to a `Number`, because a float round-trip is the corruption
the `Decimal` discipline exists to prevent.

`FactorOut` has no leaner variant to reach for. There is no response model in the codebase
that can carry a factor without its publisher, source document, page and both years, which
makes rule 2 as hard to bypass at the boundary as `FactorCitation` makes it inside the
process.

### Progress is streamed because the alternative was to invent it

The agent takes fifteen to twenty-five seconds: a vision call, a SQL lookup, the
calculation, the answer. Three options existed.

An indeterminate spinner is honest and tells the user nothing. A staged skeleton that
advances on a timer looks far better and is a lie — the interface would be asserting that
extraction had finished because 6 seconds had passed. In a project whose entire premise is
that figures travel with their provenance, an interface that fabricates its own progress
indicator is not a small inconsistency.

So `run_agent` gained an optional `on_event` callback and `/chat/stream` pushes the real
tool results as they complete. The steps on screen are the tool transcript being built.

`run_agent` stayed synchronous. It is the most inspected code in the project and is tested
with a scripted fake; making it async to suit one endpoint would have rewritten it for the
benefit of the presentation layer. The streaming endpoint runs it on a worker thread and
drains a queue instead.

Two details that are easy to get wrong and expensive to debug: every run ends with
**exactly one `final`**, including a run the provider broke — one terminal shape means the
client has no branch in which it waits forever — and the response sets
`X-Accel-Buffering: no`, without which a proxy buffers the whole stream and delivers it at
the end, which is indistinguishable from the blocking request this replaces.

### Both chat routes share `_prepare_turn`

The 503 for an unconfigured provider, the 404 for an unknown session, the 404 for an
unknown document and the appending of the document list all happen in one function that
both `/chat` and `/chat/stream` call. Two copies of that logic would eventually differ,
and the way they differ is that the newer route quietly accepts a request the older one
refuses. `test_the_stream_refuses_what_chat_refuses` asserts the pair.

### Vanilla, served by FastAPI

Three files and no toolchain: `git clone` → `make run` → the page. React with Framer
Motion would have matched the design references more directly, at the cost of a Node
build, a second dependency tree and a build artifact for Tier 2's Dockerfile to handle.
The project checklist lists React as the first thing to cut under time pressure, and
nothing on this page needs component state that four `replaceChildren` calls cannot do.

### A hand-written spring, and only one surface that uses it

`spring.js` is about 60 lines of actual logic: a critically-damped-by-default spring
parameterised by *bounce* and *duration* rather than mass, stiffness and damping;
momentum projection; rubber-banding; and a velocity tracker.

CSS transitions were not an option for the audit sheet. A transition interpolates from
where it was told to start, so grabbing a sheet mid-close makes it jump back; a spring
integrates from its current value and carries its current velocity through a re-target,
which is what makes a reversed gesture continuous instead of hitting a wall. Everything
that is *not* grabbable — the step rows arriving, the button press — stays on short CSS
transitions and one keyframe, because there is nothing there to interrupt.

Restraint was the harder half of the decision. This is an audit trail; a figure that
bounces invites less trust than one that does not. So exactly one surface is directly
manipulated — the audit sheet — and it gets the full treatment: 1:1 tracking from the grab
point with pointer capture, a 10px commit threshold, release velocity from a windowed
sample rather than the last delta, the landing point chosen by Apple's exponential
projection rather than by proximity to the release point, velocity handed to the spring so
there is no seam, and overshoot (`bounce 0.2`) only when the release was an actual flick.
Everything else on the page holds still.

### Three accessibility signals, not one

`prefers-reduced-motion` replaces travel with a short cross-fade, and the spring jumps to
its target rather than springing to it — the sheet still drags, it just does not oscillate.
`prefers-reduced-transparency` makes the bar and the sheet solid and drops the blur.
`prefers-contrast: more` collapses the translucent tokens onto solid surfaces and promotes
every hairline to the stronger border. Reduced motion alone would have left a user who had
asked for less transparency reading text over a live blur.

### The flow-check script lost its sample size from its filename

`agent_smoke_5bills.py` became `agent_flow_check.py` the moment the same script was run
over ten documents. A filename that states a sample size becomes wrong the first time the
sample changes, and the honesty of the banner this script prints is the whole reason it
exists. `make agent-smoke` still runs the original five; `make flow-10` runs ten chosen to
span both factor years.

---

## Defects found, and what caught them

Three of the four below were caught by reading the code rather than by a test, which is
worth stating plainly: there is no automated check on this page's interaction behaviour,
and that is a real gap rather than an oversight to gloss.

| Defect | How it would have shown | Caught by |
|---|---|---|
| The sheet's drag handler was bound to the whole header, which contains the **Done** button. Pressing Done started a drag and captured the pointer on the button. | Done would intermittently fail to close the sheet. | Code review |
| `releasePointerCapture` throws when the capture is already gone, which is exactly what `pointercancel` means. | An exception on every cancelled gesture, leaving `data-dragging` set and the sheet stuck. | Code review |
| The citation grid showed **Jurisdiction** only when a factor had no page reference. | A non-Singapore factor would silently not be labelled as one — a rule 7 violation, and the rule exists because a UK factor applied to a Singapore bill is a materially wrong figure. | Code review |
| The figure panel captioned itself "Scope 2", which the UI had composed rather than received. | True for every Tier 1 document, and wrong the first time a non-electricity document is added. | Code review |
| Mounting the static files at `/` instead of `/static` would shadow every route declared after the mount. | `/health` and `/chat` returning 404 while the page itself worked. | `test_static_ui.py` |

### The rule-1 probe behaved differently than in Block 4

Block 4 asked, as a second turn, "annualise that over 12 months and tell me the yearly
tonnes", and the agent refused in one turn with no tool call. The same question through
the streaming endpoint took two turns: it re-called `calculate_emissions` on the stored
reading, then refused to annualise and asked which basis was wanted — twelve times the
month, or actual twelve-month usage.

The conclusion is the same and the route is not. It stated no yearly figure, it performed
no arithmetic, the monthly tonnes it did quote came from the tool, and the figure audit
came back empty. But two live probes of one question have now produced two different
tool-call sequences, which is a reminder of how little either probe establishes.

### An observation for Tier 2, not a defect

The stored EMA citation has `source_document` = "Singapore Energy Statistics 2025, Table
2.4" and `source_page` = "Table 2.4", so the citation grid names the table twice, and
`publisher` is stored as "EMA" rather than the full "Energy Market Authority (EMA)". Both
are data-shape questions in the factor table rather than display bugs, and both want
settling when Tier 2 loads a second publisher alongside EMA.

---

## Files added

| Path | Purpose |
|---|---|
| `src/carbon_r/api/stream.py` | SSE for `/chat`: a worker thread, a queue, exactly one `final` |
| `src/carbon_r/static/index.html` | The page: dropzone, question, steps, answer, audit sheet |
| `src/carbon_r/static/app.css` | Tokens, the type scale with size-specific tracking, the materials, the three accessibility signals |
| `src/carbon_r/static/app.js` | Upload, the SSE client, rendering from `figures`, the sheet gesture |
| `src/carbon_r/static/spring.js` | The spring, momentum projection, rubber-banding, velocity tracking |

Modified: `agent/loop.py` (`figures`, `on_event`), `api/models.py` (`FactorOut`,
`FigureOut`), `main.py` (the page, the static mount, `/chat/stream`, `_prepare_turn`),
`Makefile` (`flow-10`). `scripts/agent_smoke_5bills.py` → `scripts/agent_flow_check.py`.

Untouched, deliberately: `calculate_emissions`, `FactorCitation`, `render_disclosures` and
`audit_figures`. The figure and its provenance were already exactly the right shape, which
is why this block is mostly a serialisation layer and a page. The disclosure sentences are
still rendered in one place server-side, and the UI composes no wording of its own.

Tests, all offline:

| Path | Tests |
|---|---|
| `tests/test_api_chat_figures.py` | 9 — the figure's shape and provenance, numbers as strings, both years, a factor without provenance refusing to serialise, and that a follow-up turn does not re-report the previous turn's figure |
| `tests/test_api_stream.py` | 8 — event order, exactly one `final`, the provider-failure path, what the stream refuses |
| `tests/test_static_ui.py` | 4 — the page and its assets serve, the API is not shadowed, and the script reads `figures` rather than the reply |

---

## Open items carried forward

- **The interaction has no automated coverage.** Every UI defect above was found by
  reading the code. A headless browser driving the drag and asserting the sheet lands on a
  snap point is the obvious first test, and it does not exist.
- **The honesty paths have still never been seen live.** No live run has produced a
  `no_results` from the factor table, because every synthetic bill falls inside the
  two-year substitution cap. The page has honest states for `max_turns`, `provider_error`,
  a non-empty `unverified_figures` and a figureless answer; only the last of those has been
  seen with a real response.
- **Sessions and uploads still accumulate** in memory and on disk with no eviction, and a
  restart loses every session. `get_session` / `create_session` is the seam.
- **One layout, one question.** No holdout layout exists yet, so there is no honest
  unseen-layout number to quote. Tier 2 Block 2 holds one out.
- The figure audit's tolerances (thousands separators, stated rounding, percentage) have
  now been exercised on twenty-two live replies with no false positive and no catch. They
  remain reasoned rather than stress-tested against a deliberately chatty answer.
