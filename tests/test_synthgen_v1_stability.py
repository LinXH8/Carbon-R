"""synthgen v1 is frozen: every answer key and every rendered page, byte for byte.

The Block 3 scenarios reuse the v1 builders, templates and quirk catalogue, and add to them
(a USD price list, a `usd_invoice` quirk, a currency argument). Spec 1.4 asks that growing
the generator never changes a document already published - an eval figure is only
comparable across commits if the documents it was measured on did not move.

The digest was taken at 8e38882, before any Block 3 generator change, from a separate
worktree so it could not import the changed code. It compares the answer key's JSON and the
rendered HTML, not PDF bytes: WeasyPrint embeds a creation timestamp, so a PDF differs on
every render while the page it draws does not (spec 1.4).
"""

import hashlib
import json
from pathlib import Path

from carbon_r.synthgen import electricity, fuel_card, lpg
from carbon_r.synthgen.render import render_html, template_for

DIGEST = json.loads((Path(__file__).parent / "fixtures" / "synthgen_v1_digest.json").read_text())


def _digest(document) -> dict[str, str]:
    return {
        "json": hashlib.sha256(document.model_dump_json().encode()).hexdigest(),
        "html": hashlib.sha256(render_html(document, template_for(document)).encode()).hexdigest(),
    }


def test_every_v1_key_and_page_is_unchanged():
    now = {
        document.doc_id: _digest(document)
        for build in (electricity.build_v1, lpg.build_v1, fuel_card.build_v1)
        for document in build()
    }
    assert sorted(now) == sorted(DIGEST), "the v1 document set gained or lost a document"
    moved = {
        doc_id: [part for part in ("json", "html") if now[doc_id][part] != DIGEST[doc_id][part]]
        for doc_id in now
    }
    moved = {doc_id: parts for doc_id, parts in moved.items() if parts}
    assert not moved, f"{len(moved)} v1 documents changed: {dict(list(moved.items())[:5])}"
