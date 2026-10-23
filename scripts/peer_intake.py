"""Peer-report intake (Tier 2 Block 4, B0). Data only; never touches the database.

    uv run python scripts/peer_intake.py inventory     read-only: every peer PDF, its bytes
    uv run python scripts/peer_intake.py periods       read-only: "About this report" candidates
    uv run python scripts/peer_intake.py check         data/peer_sources.json against the files

`inventory` lists every PDF in `data/raw/` outside the folders already accounted for
(`ghgp/`, `ema/`, `desnz/`), plus any already moved to `data/raw/peer/`, with its size, page
count, sha256 and the first lines of its cover and the next two pages. A file is matched to
a company by a human reading that output, not by this script: a filename can mislead, and a
match that is not clear is asked about, never guessed.

`periods` prints, for each file, the passages in its first pages that look like a statement
of the reporting period, so the period in `data/peer_sources.json` can be read from the
report itself, with its page cited, rather than copied from an expectation.

The reports are copyright of their issuers. They stay in git-ignored `data/raw/`; only
their provenance is committed.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from carbon_r.config import PROJECT_ROOT

RAW = PROJECT_ROOT / "data" / "raw"
ACCOUNTED = {"ghgp", "ema", "desnz"}
PEER_SOURCES = PROJECT_ROOT / "data" / "peer_sources.json"


class PeerSource(BaseModel):
    """One file of one company's report, as committed in data/peer_sources.json."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    company: str
    sector: Literal["bank", "reit", "industrial", "energy"]  # Carbon R's labels, not SGX's
    document_kind: Literal["sustainability_report", "data_pack", "databook", "annual_report"]
    period_start: date
    period_end: date
    # stated: both dates read from the report. from_companion: taken from the companion
    # report named in `notes`. start_assumed: only the end date is stated.
    period_basis: Literal["stated", "from_companion", "start_assumed"]
    period_pdf_page: int = Field(ge=1)
    period_printed_page: int | None
    url: str
    url_note: str | None
    local_filename: str = Field(pattern=r"^peer/.+\.pdf$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=1)
    licence_note: str
    notes: str

    @model_validator(mode="after")
    def _period(self) -> PeerSource:
        if self.period_end <= self.period_start:
            raise ValueError(f"{self.local_filename}: period ends before it starts")
        return self


class PeerSources(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    drafted: str
    note: str
    sources: list[PeerSource]


def load_peer_sources(path: Path = PEER_SOURCES) -> PeerSources:
    return PeerSources.model_validate_json(path.read_text())


# Phrases a report uses to state its own period. Deliberately broad: the output is read by
# a person, so a false candidate costs a glance and a missed one costs a wrong period.
_PERIOD = re.compile(
    r"(reporting period|this report covers|covers the period|period from|financial year|"
    r"fiscal year|\bFY\s?20\d\d|1 January 20\d\d|1 April 20\d\d|1 October 20\d\d|"
    r"31 December 20\d\d|31 March 20\d\d|30 September 20\d\d)",
    re.IGNORECASE,
)


def peer_pdfs() -> list[Path]:
    top = [p for p in RAW.glob("*.pdf")]
    moved = list((RAW / "peer").glob("*.pdf")) if (RAW / "peer").exists() else []
    others = [
        p
        for d in RAW.iterdir()
        if d.is_dir() and d.name not in ACCOUNTED | {"peer"}
        for p in d.rglob("*.pdf")
    ]
    return sorted(top + moved + others, key=lambda p: p.name.lower())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def page_count(path: Path) -> int | None:
    out = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True).stdout
    match = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(match.group(1)) if match else None


def page_text(path: Path, first: int, last: int) -> list[str]:
    out = subprocess.run(
        ["pdftotext", "-enc", "UTF-8", "-f", str(first), "-l", str(last), str(path), "-"],
        capture_output=True,
        text=True,
    ).stdout
    return out.split("\f")


def _lines(text: str, n: int) -> str:
    kept = [" ".join(line.split()) for line in text.splitlines() if len(line.strip()) > 2]
    return " | ".join(kept[:n])


def inventory() -> int:
    files = peer_pdfs()
    seen: dict[str, Path] = {}
    print(f"{len(files)} PDF(s) under data/raw/ outside {sorted(ACCOUNTED)}\n")
    for path in files:
        digest = sha256(path)
        duplicate = f"  ** DUPLICATE of {seen[digest].name}" if digest in seen else ""
        seen.setdefault(digest, path)
        pages = page_text(path, 1, 3)
        print(f"{path.relative_to(RAW)}{duplicate}")
        print(f"  size {path.stat().st_size} bytes; pages {page_count(path)}; sha256 {digest}")
        for number, text in enumerate(pages[:3], start=1):
            print(f"  p{number}: {_lines(text, 8) or '(no text layer)'}")
        print()
    return 0


def periods(max_page: int = 12, per_file: int = 6) -> int:
    for path in peer_pdfs():
        print(f"== {path.relative_to(RAW)}")
        shown = 0
        for number, text in enumerate(page_text(path, 1, max_page), start=1):
            flat = " ".join(text.split())
            for match in _PERIOD.finditer(flat):
                start = max(0, match.start() - 160)
                print(f"  PDF p{number}: ...{flat[start : match.end() + 200]}...")
                shown += 1
                break  # one passage per page is enough to find the right page
            if shown >= per_file:
                break
        if not shown:
            print(f"  (no period statement in the first {max_page} pages)")
        print()
    return 0


def check() -> int:
    """Every entry's file exists under data/raw/ with the recorded sha256 and page count."""
    problems = []
    entries = load_peer_sources().sources
    for entry in entries:
        path = RAW / entry.local_filename
        if not path.exists():
            problems.append(f"{entry.local_filename}: missing")
            continue
        if sha256(path) != entry.sha256:
            problems.append(f"{entry.local_filename}: sha256 differs from the recorded one")
        if page_count(path) != entry.page_count:
            problems.append(f"{entry.local_filename}: page count differs")
    unlisted = {p.relative_to(RAW).as_posix() for p in peer_pdfs()} - {
        e.local_filename for e in entries
    }
    problems += [f"{name}: on disk but not in peer_sources.json" for name in sorted(unlisted)]
    companies = {e.company for e in entries}
    print(f"{len(entries)} entries, {len(companies)} companies; {len(problems)} problem(s)")
    for problem in problems:
        print(f"  {problem}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("inventory", "periods", "check"))
    parser.add_argument("--max-page", type=int, default=12)
    args = parser.parse_args(argv)
    if args.command == "inventory":
        return inventory()
    if args.command == "check":
        return check()
    return periods(max_page=args.max_page)


if __name__ == "__main__":
    sys.exit(main())
