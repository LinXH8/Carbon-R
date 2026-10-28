"""PDF pages to citable chunks. Pure apart from `extract_pages`, which shells out to pdftotext.

The property everything here protects is that a chunk's citation is exact: a chunk never
spans a page, so "page N" is the page the passage is printed on, not the page it starts on.

Text extraction
---------------
Chunk text comes from `pdftotext` in its DEFAULT (reading-order) mode. `-layout` mode
preserves the page's geometry, which on a two-column page means it prints line 1 of the left
column beside line 1 of the right column - measured on the Corporate Standard's PDF page 20,
where it interleaves "consolidation, i.e., if the operation is fully consolidated" with
"Table 2 in the guidance section of this chapter illustrates". Chunks built from that are
garbled, embed badly and cannot match a verbatim quote. Layout mode is used for one thing
only: reading the page number in the footer, which it keeps on the page's last line.

Cleaning (`clean_page`) is applied to the STORED text, and the verbatim-quote check and the
eval anchors are checked against that same stored text, so the cleaning can never be the
reason a true quote fails to match:

- a line holding a single capital letter is dropped. The Corporate Standard prints vertical
  "STANDARD" and "GUIDANCE" tabs in its margin, which reading order emits one letter per
  line. The first version dropped every one-character line, and a test caught it deleting
  a real line; counted across the three standards, the other one-character lines are digits
  and markers from tables and footnotes, so they are kept. Counts are in
  docs/blocks/TIER2_BLOCK4.md;
- a word hyphenated across a line break is re-joined ("compa-" + "nies");
- whitespace is collapsed to single spaces.

Printed pages
-------------
The page number a reader sees is not the PDF page index. For each document there is one
offset that holds through its body (2, for both standards measured), recorded in the source
registry. `printed_page` is set only where a number in the page's own footer confirms
`pdf_page - offset`; otherwise it is None and a citation gives the PDF page instead. It is
never computed from the offset alone, because front matter, chapter dividers and full-page
figures carry no number and an inferred one would be a citation nobody can find.

Not attempted: section headings. A numbered-heading pattern was tried against all three
standards and matched footnotes ("1 For example, activity-specific secondary data...") and
numbered list items as readily as headings. A wrong section in a citation is worse than
none, so `section` is left None; document and page are the citation.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Chunk parameters. Changing either changes CHUNKER_VERSION, which retires every stored
# chunk made under the old values at the next ingest (see scripts/ingest_methodology.py).
WINDOW_WORDS = 300
OVERLAP_WORDS = 50

# Bump by hand when the CLEANING or splitting logic changes in a way the two parameters
# above do not capture - a change to `clean_page` produces different text from the same PDF.
CHUNKER_REVISION = "1"

# Prepend the document's title to a chunk's text when it is EMBEDDED. The stored text - what
# a citation shows and every quote is verified against - is never changed. Part of
# CHUNKER_VERSION: a different embedding input is a different vector for the same row.
#
# OFF. Tried in Tier 2 Block 4 and measured on dev as a negative result
# (docs/blocks/TIER2_BLOCK4.md): hit@5 fell from 12 to 10 of 18, because the title pulled
# in passages from the wrong standard. False reproduces the original chunker version,
# 20927f94c1b4.
EMBED_TITLE = False

# A footer page number, in the shapes the three standards use: a bare number, or a number
# at either end of a line that also carries the running title ("28 Scope 2 Guidance"),
# optionally in square brackets as the Scope 3 Standard prints it ("[38] Corporate Value
# Chain ...", "... [39]").
_FOOTER_NUMBER = (
    re.compile(r"^\[?(\d{1,3})\]?(?:\s+\S.*)?$"),
    re.compile(r"^.*\S\s+\[?(\d{1,3})\]?$"),
)
_EDGE_NUMBER = re.compile(r"^\[?(\d{1,3})\]?\s{2,}|\s{2,}\[?(\d{1,3})\]?$|^\[?(\d{1,3})\]?$")

_HYPHENATED_BREAK = re.compile(r"([A-Za-z])-\n([a-z])")

# One capital letter alone on a line: a letter of a vertical margin tab. Digits, symbols and
# lower-case letters alone on a line are kept - in these documents they are table cells and
# footnote markers, which are content.
_MARGIN_TAB_LETTER = re.compile(r"^\s*[A-Z]\s*$")


def chunker_version(
    window: int = WINDOW_WORDS, overlap: int = OVERLAP_WORDS, embed_title: bool | None = None
) -> str:
    """A short, stable id for the chunk parameters, cleaning revision and embedding input."""
    if not 0 <= overlap < window:
        raise ValueError(f"overlap must be in [0, window); got window={window}, {overlap=}")
    embed_title = EMBED_TITLE if embed_title is None else embed_title
    key = f"rev={CHUNKER_REVISION}|window={window}|overlap={overlap}"
    if embed_title:
        key += "|embed_title=1"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


CHUNKER_VERSION = chunker_version()


def embedding_input(title: str, text: str, embed_title: bool | None = None) -> str:
    """The string sent to the embedding model for a chunk. Not what is stored."""
    embed_title = EMBED_TITLE if embed_title is None else embed_title
    return f"{title}\n\n{text}" if embed_title else text


def normalise(text: str) -> str:
    """Whitespace collapsed to single spaces. The form every verbatim comparison uses."""
    return " ".join(text.split())


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def clean_page(raw: str) -> str:
    """Reading-order page text, as stored and as every quote is checked against."""
    kept = [line for line in raw.splitlines() if not _MARGIN_TAB_LETTER.match(line)]
    joined = _HYPHENATED_BREAK.sub(r"\1\2", "\n".join(kept))
    return normalise(joined)


def footer_numbers(layout_text: str) -> set[int]:
    """Every number that a page's layout-mode text could be printing as its page number.

    The last non-blank line is read with both footer shapes; the first and last lines are
    also read for a number standing alone at either end, which is where a running header
    puts it.
    """
    lines = [line.strip() for line in layout_text.splitlines() if line.strip()]
    if not lines:
        return set()
    found: set[int] = set()
    for pattern in _FOOTER_NUMBER:
        match = pattern.match(lines[-1])
        if match:
            found.add(int(match.group(1)))
    for line in (lines[0], lines[-1]):
        for match in _EDGE_NUMBER.finditer(line):
            found.update(int(g) for g in match.groups() if g)
    return found


def printed_page(layout_text: str, pdf_page: int, offset: int | None) -> int | None:
    """`pdf_page - offset`, but only if the page's own footer prints that number."""
    if offset is None:
        return None
    expected = pdf_page - offset
    return expected if expected >= 1 and expected in footer_numbers(layout_text) else None


# The share of pages with a readable footer number that one offset must explain before it
# is used for a document (Tier 2 Block 4 Part B, set by the author). Below it, the document
# has no offset and every citation gives the PDF page and says so.
OFFSET_MATCH_THRESHOLD = 0.8

# The offsets tried. Widened from 0..2 to -2..+2 after the first peer preview: Keppel's
# printed page is its PDF page + 1 (offset -1) on 94 of 99 readable footers, which 0..2
# could not express. A negative offset is a cover or other unnumbered front page that the
# PDF omits; nothing about the rule is specific to one report.
OFFSET_CANDIDATES = (-2, -1, 0, 1, 2)


@dataclass(frozen=True)
class OffsetDecision:
    """Which page offset, if any, a document's footers support - with the evidence."""

    readable: int  # pages whose footer yields at least one number
    matches: dict[int, int]  # offset -> pages whose footer prints pdf_page - offset
    offset: int | None
    reason: str

    def rate(self, offset: int) -> float:
        return self.matches[offset] / self.readable if self.readable else 0.0


def choose_offset(
    footers: list[tuple[int, set[int]]],
    candidates: tuple[int, ...] = OFFSET_CANDIDATES,
    threshold: float = OFFSET_MATCH_THRESHOLD,
) -> OffsetDecision:
    """The offset that at least `threshold` of the readable footers confirm, else None.

    `footers` is (pdf_page, footer_numbers(layout_text)) per page. A page with no footer
    number is not evidence either way, so it is left out of the denominator.
    """
    readable = [(page, numbers) for page, numbers in footers if numbers]
    matches = {
        k: sum(1 for page, numbers in readable if page - k >= 1 and page - k in numbers)
        for k in candidates
    }
    if not readable:
        return OffsetDecision(0, matches, None, "no page has a readable footer number")
    # Ties go to the offset nearest zero, then the smaller one: deterministic either way.
    best = max(candidates, key=lambda k: (matches[k], -abs(k), -k))
    rate = matches[best] / len(readable)
    if rate >= threshold:
        reason = f"offset {best} matches {matches[best]}/{len(readable)} ({rate:.0%})"
        return OffsetDecision(len(readable), matches, best, reason)
    reason = (
        f"best is offset {best} at {matches[best]}/{len(readable)} ({rate:.0%}), "
        f"below {threshold:.0%}"
    )
    return OffsetDecision(len(readable), matches, None, reason)


@dataclass(frozen=True)
class Page:
    """One PDF page: its reading-order text (cleaned) and its layout text (footer only)."""

    pdf_page: int
    text: str
    layout_text: str


@dataclass(frozen=True)
class Chunk:
    pdf_page: int
    chunk_index: int
    printed_page: int | None
    text: str
    section: str | None = None

    @property
    def content_sha256(self) -> str:
        return sha256_text(self.text)


def chunk_page(
    page: Page,
    *,
    offset: int | None,
    window: int = WINDOW_WORDS,
    overlap: int = OVERLAP_WORDS,
) -> list[Chunk]:
    """Split one page into overlapping word windows. Never crosses into the next page.

    A page of at most `window` words is one chunk. Longer pages step by `window - overlap`,
    so consecutive chunks share `overlap` words and a sentence cut at a window edge appears
    whole in one of the two.
    """
    chunker_version(window, overlap)  # validates the pair
    words = page.text.split()
    if not words:
        return []
    printed = printed_page(page.layout_text, page.pdf_page, offset)
    step = window - overlap
    starts = range(0, max(len(words) - overlap, 1), step)
    return [
        Chunk(
            pdf_page=page.pdf_page,
            chunk_index=index,
            printed_page=printed,
            text=" ".join(words[start : start + window]),
        )
        for index, start in enumerate(starts)
    ]


def chunk_pages(
    pages: list[Page],
    *,
    offset: int | None,
    window: int = WINDOW_WORDS,
    overlap: int = OVERLAP_WORDS,
) -> list[Chunk]:
    return [
        chunk
        for page in pages
        for chunk in chunk_page(page, offset=offset, window=window, overlap=overlap)
    ]


def _pdftotext(pdf: Path, *, layout: bool) -> list[str]:
    """Every page's text, split on the form feed pdftotext writes after each page."""
    args = ["pdftotext", "-enc", "UTF-8", *(["-layout"] if layout else []), str(pdf), "-"]
    out = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    pages = out.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]  # the trailing form feed leaves one empty string
    return pages


def extract_pages(pdf: Path) -> list[Page]:
    """Read a PDF into pages. Needs `pdftotext` (poppler) on PATH; local ingest only."""
    reading = _pdftotext(pdf, layout=False)
    layout = _pdftotext(pdf, layout=True)
    if len(reading) != len(layout):
        raise RuntimeError(
            f"{pdf.name}: pdftotext gave {len(reading)} pages in reading order but "
            f"{len(layout)} in layout mode; page citations would not line up."
        )
    return [
        Page(pdf_page=number, text=clean_page(text), layout_text=lay)
        for number, (text, lay) in enumerate(zip(reading, layout, strict=True), start=1)
    ]
