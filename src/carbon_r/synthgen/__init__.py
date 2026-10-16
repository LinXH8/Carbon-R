"""synthgen: synthetic source documents with their answer keys.

The generator decides the answer and then draws a document that states it, so ground truth
never comes from reading a document with a model (CLAUDE.md rule 4). Each document is
written as a `doc_XXX.pdf` / `doc_XXX.json` pair: the JSON *is* the answer key.
"""

from carbon_r.synthgen.electricity import (
    DEFAULT_SEED,
    GENERATOR_VERSION,
    build_bill,
    build_bill_series,
)
from carbon_r.synthgen.render import render_html, write_pdf

__all__ = [
    "DEFAULT_SEED",
    "GENERATOR_VERSION",
    "build_bill",
    "build_bill_series",
    "render_html",
    "write_pdf",
]
