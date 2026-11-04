"""Eval 5a: peer table extraction by representation - page image vs two text layers.

    uv run python scripts/eval_peer_extraction.py
    uv run python scripts/eval_peer_extraction.py --arm vision --arm layout

Part B found the PDF text layer fuses footnote markers onto figures and loses which column
is which year (DBS PDF p.79), and drafted from page images for that reason. This measures
the claim instead of asserting it: the same model reads the same pages three ways.

    vision      the page rendered at 200 dpi, sent as an image at detail "original"
    layout      `pdftotext -layout` of the page, as text
    pdfplumber  `extract_tables()` of the page, each table as tab-separated rows. A page
                where pdfplumber finds no table is sent saying so, and counted

Everything else is held fixed: the extractor model (`settings.openai_model`, never the
drafting model whose readings became the labels), the `PeerTableReading` schema, the
drafting instruction with only its first sentence changed to name the input (`instruction`),
and the same Python after the model - `continued_unit` with the declared continuations,
`PAGE_YEARS`, and `to_disclosures`.

Scored against `peer_disclosures_verified` only, on every page that holds a verified row:

    row presence    `match_cells`, as the completeness measure uses it: matched exactly,
                    matched with a different method, missed, and report-year cells read
                    that no verified row accounts for (extra). A missed row is also checked
                    for a reading under another year's column.
    fields          of the rows present: figure exactly as printed; figure with a footnote
                    marker fused on (the verified digits, then more); unit; method.

The bias, stated: the labels began as the vision drafter's readings (gpt-5.5), verified by
hand. A different model reading the same image may share that drafter's misreadings where
the author's check missed them; the blind samples (18 of 18 agreed) are the evidence on how
far verification leaned on the drafts.

The run JSON is committed with NO figures - counts, page numbers and row labels only - and
`leak-check` enforces that. Replies are cached locally by everything that determines them.

Local only: needs the reports, a database with the verified rows, and an API key.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from carbon_r.config import PROJECT_ROOT, get_settings
from carbon_r.db import connect
from carbon_r.llm import openai_client
from carbon_r.models.peer_disclosure import (
    DRAFT_INSTRUCTION,
    DraftedCell,
    PeerTableReading,
    cell_year,
    continued_unit,
    parse_figure,
    to_disclosures,
)
from carbon_r.models.peer_verification import (
    NO_YEAR_COLUMN,
    DraftedCellRef,
    InventoryEntry,
    match_cells,
)
from carbon_r.peer_sources import PAGE_YEARS, TABLE_CONTINUES
from carbon_r.rag.scoring import git_state, n_of
from carbon_r.sources import BY_ID, PEERS
from carbon_r.tools.extract import _image_content, rasterise_page, request_hash

RUNS_DIR = PROJECT_ROOT / "data" / "evals" / "runs"
CACHE_DIR = PROJECT_ROOT / "data" / "generated" / "eval_cache" / "peer_extraction"
ARMS = ("vision", "layout", "pdfplumber")
DPI, DETAIL = 200, "original"  # as the drafter
TIMEOUT_S = 300.0  # one full table page, as the drafter

_VISION_SENTENCE = (
    "You are transcribing the greenhouse-gas emissions table on this page of a company "
    "sustainability report."
)
_INPUT_SENTENCE = {
    "vision": _VISION_SENTENCE,
    "layout": (
        "You are transcribing the greenhouse-gas emissions table in the text below, extracted "
        "with pdftotext -layout from one page of a company sustainability report."
    ),
    "pdfplumber": (
        "You are transcribing the greenhouse-gas emissions table in the tables below, "
        "extracted with pdfplumber from one page of a company sustainability report: one "
        "block per table, one line per row, cells separated by tabs."
    ),
}
NO_TABLE = "(pdfplumber found no table on this page.)"


def instruction(arm: str) -> str:
    """The drafting instruction with only its first sentence, naming the input, changed."""
    if not DRAFT_INSTRUCTION.startswith(_VISION_SENTENCE):
        raise RuntimeError("DRAFT_INSTRUCTION no longer starts with the vision sentence")
    return _INPUT_SENTENCE[arm] + DRAFT_INSTRUCTION[len(_VISION_SENTENCE) :]


# --- inputs -------------------------------------------------------------------------------


def layout_text(path: Path, pdf_page: int) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", "-f", str(pdf_page), "-l", str(pdf_page), str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def plumber_text(path: Path, pdf_page: int) -> tuple[str, int]:
    """The page's tables as TSV blocks, and how many tables pdfplumber found."""
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        tables = pdf.pages[pdf_page - 1].extract_tables()
    blocks = [
        "\n".join("\t".join(" ".join((c or "").split()) for c in row) for row in table)
        for table in tables
    ]
    return ("\n\n".join(blocks) if blocks else NO_TABLE), len(tables)


def read(client, model: str, arm: str, source_id: str, pdf_page: int) -> tuple[dict, dict]:
    """(entry, input facts) for one page and arm: the cached reply, or one model call."""
    path = BY_ID[source_id].path
    facts: dict = {}
    if arm == "vision":
        image = rasterise_page(path, pdf_page, DPI)
        payload = hashlib.sha256(image.read_bytes()).hexdigest()
        content = [_image_content(image, DETAIL)]
    else:
        if arm == "layout":
            text = layout_text(path, pdf_page)
        else:
            text, facts["tables_found"] = plumber_text(path, pdf_page)
        payload = hashlib.sha256(text.encode()).hexdigest()
        content = [{"type": "input_text", "text": text}]
    prompt = instruction(arm)
    key = hashlib.sha256(
        "\x1f".join(
            [arm, source_id, str(pdf_page), model, request_hash(prompt, PeerTableReading), payload]
        ).encode()  # fmt: skip
    ).hexdigest()[:24]
    cached = CACHE_DIR / f"{key}.json"
    if cached.exists():
        return json.loads(cached.read_text()), facts | {"from_cache": True}
    response = client.responses.parse(
        model=model,
        input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}, *content]}],
        text_format=PeerTableReading,
    )
    if response.output_parsed is None:
        raise RuntimeError(f"{arm} {source_id} p.{pdf_page}: no parsed output")
    entry = {
        "reading": response.output_parsed.model_dump(),
        "usage": response.usage.model_dump() if response.usage else {},
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(entry))
    return entry, facts | {"from_cache": False}


# --- scoring (pure) -----------------------------------------------------------------------


def _unit(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def score_page(
    verified: list[dict], cells: list[DraftedCell], outcomes: list[str], *,
    pdf_page: int, report_year: int, declared_year: int | None, carried_unit: str | None,
) -> dict:  # fmt: skip
    """One page of one arm against its verified rows. Pure; returns counts and labels only.

    `verified` rows carry row_label, column_header, scope, method, figure_printed, unit. A
    verified label "Heading > Row" is listed by its last segment, which a drafted label
    matches whole or as its own last segment (`match_cells`)."""
    entries = [
        InventoryEntry(
            pdf_page=pdf_page,
            row_label=v["row_label"].split(">")[-1].strip(),
            column_header=NO_YEAR_COLUMN if declared_year else v["column_header"],
            scope=v["scope"],
            method=v["method"],
            paired_draft_label=None,
        )
        for v in verified
    ]
    by_entry = {id(e): v for e, v in zip(entries, verified, strict=True)}
    refs, by_ref = [], {}
    for cell, outcome in zip(cells, outcomes, strict=True):
        if cell_year(cell.column_header_printed, declared_year) != report_year:
            continue
        ref = DraftedCellRef(
            pdf_page=pdf_page,
            drafted_row_label=cell.row_label_printed.strip(),
            column_header=cell.column_header_printed.strip(),
            scope=cell.scope,
            method=cell.method,
            outcome=outcome,
        )
        refs.append(ref)
        by_ref[id(ref)] = cell
    result = match_cells(entries, refs, [pdf_page])

    present = result.matched + result.method_differs
    figure = fused = unit = method = 0
    for m in present:
        v, cell = by_entry[id(m.listed)], by_ref[id(m.drafted)]
        printed = cell.figure_printed.strip()
        figure += printed == v["figure_printed"]
        mine, theirs = _digits(printed), _digits(v["figure_printed"])
        fused += mine != theirs and mine.startswith(theirs) and parse_figure(printed) is not None
        unit += _unit(cell.unit_printed.strip() or (carried_unit or "")) == _unit(v["unit"])
        method += cell.method == v["method"]

    # A missed row: was it read under another year's column instead?
    def same_row(e: InventoryEntry, cell: DraftedCell) -> bool:
        label = cell.row_label_printed.strip()
        forms = {label.casefold(), label.split(">")[-1].strip().casefold()}
        return cell.scope == e.scope and e.row_label.casefold() in forms

    wrong_year = sum(
        any(
            same_row(e, c)
            and cell_year(c.column_header_printed, declared_year) not in (None, report_year)
            for c in cells
        )  # fmt: skip
        for e in result.skipped
    )
    return {
        "verified": len(verified),
        "matched_exactly": len(result.matched),
        "method_differs": len(result.method_differs),
        "missed": len(result.skipped),
        "missed_read_under_another_year": wrong_year,
        "extra_kept": sum(r.outcome == "kept" for r in result.extra),
        "extra_dropped": sum(r.outcome != "kept" for r in result.extra),
        "present": len(present),
        "figure_exact": figure,
        "figure_fused_marker": fused,
        "unit_exact": unit,
        "method_exact": method,
        "dropped_by_rule": result.dropped_by_rule,
        "missed_rows": [e.row_label for e in result.skipped],
    }


FIELDS = (
    "verified", "matched_exactly", "method_differs", "missed", "missed_read_under_another_year",
    "extra_kept", "extra_dropped", "present", "figure_exact", "figure_fused_marker",
    "unit_exact", "method_exact",
)  # fmt: skip


def total(pages: list[dict]) -> dict:
    return {f: sum(p[f] for p in pages) for f in FIELDS}


# --- run ----------------------------------------------------------------------------------

_VERIFIED_SQL = """
select source_id, pdf_page, row_label, column_header, scope, method, figure_printed, unit,
       drafted_by
from peer_disclosures_verified
order by source_id, pdf_page, id
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arm", action="append", choices=ARMS, help="default: all three")
    args = parser.parse_args(argv)
    arms = args.arm or list(ARMS)

    model = get_settings().openai_model
    with connect() as conn:
        rows = conn.execute(_VERIFIED_SQL).fetchall()
    drafters = {r[8] for r in rows}
    if model in drafters:
        print(f"Refusing: {model} drafted the labels; the scored model must differ.")
        return 1
    verified: dict[tuple[str, int], list[dict]] = {}
    for sid, page, label, header, scope, method, fig, unit, _ in rows:
        verified.setdefault((sid, page), []).append(
            {
                "row_label": label,
                "column_header": header,
                "scope": scope,
                "method": method,
                "figure_printed": fig,
                "unit": unit,
            }  # fmt: skip
        )

    git = git_state()
    dirty = " (DIRTY TREE - not a clean measurement)" if git["dirty"] else ""
    print(
        f"peer extraction eval: {len(verified)} pages, {len(rows)} verified rows; model {model} "
        f"(labels drafted by {', '.join(sorted(drafters))}); commit {git['commit'][:7]}{dirty}\n"
    )
    client = openai_client(timeout_s=TIMEOUT_S)
    usage: Counter[str] = Counter()
    calls = 0
    record_arms = {}
    for arm in arms:
        pages = []
        previous: dict[str, tuple[int, PeerTableReading]] = {}
        no_table = 0
        for (sid, pdf_page), rows_here in sorted(verified.items()):
            entry, facts = read(client, model, arm, sid, pdf_page)
            calls += not facts["from_cache"]
            for k in ("input_tokens", "output_tokens", "total_tokens"):
                usage[k] += entry["usage"].get(k, 0)
            no_table += facts.get("tables_found") == 0
            reading = PeerTableReading.model_validate(entry["reading"])
            declared = TABLE_CONTINUES.get(sid, {}).get(pdf_page) is not None
            carried = continued_unit(reading, pdf_page, previous.get(sid), declared=declared)
            previous[sid] = (pdf_page, reading)
            declared_year = PAGE_YEARS.get(sid, {}).get(pdf_page)
            peer = PEERS[sid]
            draft = to_disclosures(
                reading, peer, pdf_page=pdf_page, carried_unit=carried,
                declared_year=declared_year,
            )  # fmt: skip
            scored = score_page(
                rows_here, reading.cells, draft.outcomes, pdf_page=pdf_page,
                report_year=peer.report_year, declared_year=declared_year,
                carried_unit=carried.unit if carried else None,
            )  # fmt: skip
            pages.append({"source_id": sid, "pdf_page": pdf_page, **facts, **scored})
        t = total(pages)
        record_arms[arm] = {"total": t, "pages_no_table_found": no_table, "pages": pages}
        v = t["verified"]
        print(f"  {arm}")
        print(f"    rows present           {n_of(t['present'], v)}  (exact {t['matched_exactly']}, "
              f"method differs {t['method_differs']})")  # fmt: skip
        print(f"    missed                 {n_of(t['missed'], v)}  (read under another year: "
              f"{t['missed_read_under_another_year']})")  # fmt: skip
        print(f"    extra cells            kept {t['extra_kept']}, dropped by a rule "
              f"{t['extra_dropped']}")  # fmt: skip
        p = t["present"]
        print(f"    figure exact           {n_of(t['figure_exact'], p)}  "
              f"(marker fused on: {t['figure_fused_marker']})")  # fmt: skip
        print(f"    unit exact             {n_of(t['unit_exact'], p)}")
        print(f"    method exact           {n_of(t['method_exact'], p)}")
        if arm == "pdfplumber":
            print(f"    pages with no table    {n_of(no_table, len(pages))}")
        for pg in pages:
            if pg["missed"] or pg["figure_exact"] < pg["present"]:
                where = f"{pg['source_id']} p.{pg['pdf_page']}"
                print(f"      {where}: present {pg['present']} of {pg['verified']}, "
                      f"figure exact {pg['figure_exact']} of {pg['present']}")  # fmt: skip

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = RUNS_DIR / f"peer_extraction_{stamp}.json"
    out.write_text(
        json.dumps(
            {
                "git": git,
                "model": model,
                "labels_drafted_by": sorted(drafters),
                "dpi": DPI,
                "detail": DETAIL,
                "pdfplumber_version": version("pdfplumber"),
                "pdftotext": subprocess.run(
                    ["pdftotext", "-v"], capture_output=True, text=True
                ).stderr.splitlines()[0],
                "instruction_sha256": {
                    a: hashlib.sha256(instruction(a).encode()).hexdigest() for a in arms
                },
                "model_calls": calls,
                "usage_tokens": dict(usage),
                "arms": record_arms,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\n  model calls {calls}; tokens {usage.get('total_tokens', 0)}")
    print(f"  run written to {out.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
