"""Where real peer figures may be committed: one file, verified values only (CLAUDE.md).

`find_leaks` compares against a BASE ref - by default `origin/main`, i.e. what is already
public - rather than against HEAD. Comparing with HEAD stops working the moment a leak is
committed: the figure is then "already at HEAD". Two checks:

- **every commit in `base..HEAD`**: each file it touches, as of that commit, against the
  same file at BASE. A figure added in one commit and removed in the next is still in the
  history being pushed, so it is still a leak;
- **the working tree**: each file about to be committed (the index, or explicit paths),
  against BASE, so a leak is caught before it is committed at all.

A figure already present at BASE in the same file is reported separately, not failed: short
numbers (a page count, a hash fragment) coincide. The allowed file is never searched.

Matching is on the figure as printed, with no digit, comma or decimal part on either side,
so "1,234" does not match inside "21,234" or "1,234.5".
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class Leak(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    where: str  # a commit sha, or "working tree"
    path: str
    figure: str
    at_base: bool  # present in this file at BASE already: listed, not failed


def significant(figure: str) -> bool:
    """Three or more digits: shorter numbers coincide too often to mean anything."""
    return len(re.sub(r"\D", "", figure)) >= 3


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
) -> list[Leak]:
    """Every occurrence of a significant figure introduced since `base`, in commits or in
    the working tree, outside `allowed`. Raises if `base` is not a known ref."""
    _git(repo, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}", check=True)
    patterns = {f: _pattern(f) for f in figures if significant(f)}
    leaks: list[Leak] = []

    def scan(where: str, path: str, text: str) -> None:
        if path in allowed or not text:
            return
        found = [f for f, p in patterns.items() if p.search(text)]
        if not found:
            return
        before = _show(repo, base, path)
        leaks.extend(
            Leak(where=where, path=path, figure=f, at_base=bool(patterns[f].search(before)))
            for f in found
        )

    for sha in _git(repo, "rev-list", "--reverse", f"{base}..HEAD").split():
        changed = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split("\n")
        for path in filter(None, changed):
            scan(sha[:7], path, _show(repo, sha, path))

    names = paths if paths is not None else _git(repo, "ls-files", "-z").split("\0")
    for name in filter(None, names):
        file = repo / name
        if file.is_file():
            scan("working tree", name, file.read_text(errors="ignore"))
    return leaks


__all__ = ["Leak", "find_leaks", "significant"]
