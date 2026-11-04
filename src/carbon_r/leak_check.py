"""Where real peer figures may be committed: one file, verified values only (CLAUDE.md).

`find_leaks` compares against a BASE ref - by default `origin/main`, i.e. what is already
public - rather than against HEAD. Comparing with HEAD stops working the moment a leak is
committed: the figure is then "already at HEAD". Two checks:

- **every commit in `base..HEAD`**: each file it touches, as of that commit, against the
  same file at BASE. A figure added in one commit and removed in the next is still in the
  history being pushed, so it is still a leak;
- **the working tree**: each file about to be committed (the index, or explicit paths),
  against BASE, so a leak is caught before it is committed at all.

A figure present at BASE in the same file, at least as many times, is reported separately,
not failed: short numbers (a page count, a hash fragment) coincide. Counted, not just
present (5 Nov): with presence alone, once a file held one "4" every later "4" in it passed,
which the wider search below would have made a real hole. The allowed file is never searched.

A new coincidence - a number in the code that happens to equal a peer figure, like the
OpenAI timeout in seconds - is exempted only by an entry the author has reviewed, in
`data/leak_check_coincidences.json`: one path and one figure, with the reason, the reviewer
and the date (`split_reviewed`). The same figure in any other file still fails, every
exemption is printed on every run, and an entry that matches nothing is reported so the
list cannot outlive its reason.

Matching is on the figure as printed, with no digit, comma or decimal part on either side,
so "1,234" does not match inside "21,234" or "1,234.5".

Which figures are searched, and where (`reach`), widened on 5 Nov after a leak of short
decimals (one digit, a point, one digit) that the old 3-digit floor could not see:

- **everywhere**: a figure with 3 or more digits, or with a decimal point, however short;
- **in context only**: a whole number of one or two digits ("4", "75"). Searched only on a
  line that also names a peer company or an emissions unit in tonnes (`context`), because a
  bare "4" is on nearly every line of code. Known limit: a short whole number on its own
  line, away from any company name or unit, is not seen.

Every new match outside the allowed file fails unless reviewed, as before. The check runs
on its own before every push (`.githooks/pre-push`) and in CI, so a push cannot depend on
remembering it.
"""

from __future__ import annotations

import re
import subprocess
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Leak(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    where: str  # a commit sha, or "working tree"
    path: str
    figure: str
    at_base: bool  # present in this file at BASE already: listed, not failed


class Coincidence(BaseModel):
    """One reviewed exemption: this figure in this file is not a peer figure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)
    figure: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    reviewed_by: str = Field(min_length=1)
    reviewed_on: date


class Coincidences(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    note: str
    entries: list[Coincidence]


def read_coincidences(path: Path) -> list[Coincidence]:
    return Coincidences.model_validate_json(path.read_text()).entries if path.exists() else []


def split_reviewed(
    leaks: list[Leak], entries: list[Coincidence]
) -> tuple[list[Leak], list[Leak], list[Coincidence]]:
    """(reviewed, failing, unused): a leak is reviewed only if an entry names its exact path
    and figure. Leaks already at base are never failing, but they still use an entry: an
    entry is unused only when its figure is in its file nowhere at all. Pure."""
    exempt = {(e.path, e.figure) for e in entries}
    new = [leak for leak in leaks if not leak.at_base]
    reviewed = [leak for leak in new if (leak.path, leak.figure) in exempt]
    failing = [leak for leak in new if (leak.path, leak.figure) not in exempt]
    hit = {(leak.path, leak.figure) for leak in leaks}
    unused = [e for e in entries if (e.path, e.figure) not in hit]
    return reviewed, failing, unused


def reach(figure: str) -> str:
    """'everywhere', or 'context' for a one- or two-digit whole number (see module doc)."""
    return "everywhere" if len(re.sub(r"\D", "", figure)) >= 3 or "." in figure else "context"


def context_pattern(companies: list[str]) -> re.Pattern[str]:
    """A line that names a peer company, or a tonnes-CO2e unit."""
    names = "|".join(re.escape(c) for c in sorted(companies, key=len, reverse=True))
    return re.compile(rf"\b(?:{names})\b|\bk?tCO[2\u2082]e\b|\btonnes CO[2\u2082]e\b", re.I)


def _count(figure: str, pattern: re.Pattern[str], text: str, context: re.Pattern[str]) -> int:
    if reach(figure) == "everywhere":
        return len(pattern.findall(text))
    return sum(len(pattern.findall(line)) for line in text.splitlines() if context.search(line))


def _pattern(figure: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\d,.]){re.escape(figure)}(?![\d,]|\.\d)")


def _git(repo: Path, *args: str, check: bool = False) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=check)
    return result.stdout.decode(errors="ignore")


def _show(repo: Path, ref: str, path: str) -> str:
    return _git(repo, "show", f"{ref}:{path}")


def find_leaks(
    repo: Path,
    figures: set[str],
    *,
    base: str,
    allowed: set[str],
    paths: list[str] | None = None,
    head: str = "HEAD",
    context: re.Pattern[str] | None = None,
) -> list[Leak]:
    """Every occurrence of a figure introduced since `base`, in the commits `base..head` or
    in the working tree, outside `allowed`. Raises if `base` or `head` is not a known ref."""
    _git(repo, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}", check=True)
    _git(repo, "rev-parse", "--verify", "--quiet", f"{head}^{{commit}}", check=True)
    context = context or context_pattern([])
    patterns = {f: _pattern(f) for f in figures}
    leaks: list[Leak] = []

    def scan(where: str, path: str, text: str) -> None:
        if path in allowed or not text:
            return
        found = {f: n for f, p in patterns.items() if (n := _count(f, p, text, context))}
        if not found:
            return
        before = _show(repo, base, path)
        leaks.extend(
            Leak(
                where=where,
                path=path,
                figure=f,
                at_base=n <= _count(f, patterns[f], before, context),
            )
            for f, n in found.items()
        )

    for sha in _git(repo, "rev-list", "--reverse", f"{base}..{head}").split():
        changed = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split("\n")
        for path in filter(None, changed):
            scan(sha[:7], path, _show(repo, sha, path))

    names = paths if paths is not None else _git(repo, "ls-files", "-z").split("\0")
    for name in filter(None, names):
        file = repo / name
        if file.is_file():
            scan("working tree", name, file.read_text(errors="ignore"))
    return leaks


__all__ = [
    "Coincidence",
    "Coincidences",
    "Leak",
    "find_leaks",
    "context_pattern",
    "reach",
    "read_coincidences",
    "split_reviewed",
]
