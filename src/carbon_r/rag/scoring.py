"""What the retrieval evals share: the measured commit, a reply cache, and page-hit scoring.

Moved out of `scripts/eval_retrieval.py` (methodology) when `scripts/eval_peer_retrieval.py`
(peer, Tier 2 Block 4 Part B 2b) needed the same pieces, so both report hit@1, hit@5 and MRR
the same way rather than by two copies that could drift.

A hit is a PAGE match: the keys label (source, PDF page), not chunks, so a passage counts
when its page is one of the question's expected pages, whichever chunk of the page it is.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path

from carbon_r.config import PROJECT_ROOT


def git_state() -> dict:
    """The commit measured, and whether anything other than an eval's own output is dirty.

    `data/evals/runs/` is excluded: an eval writes its run file there, so counting it would
    mark the second of two back-to-back runs dirty even though no code had changed.
    """

    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()

    status = run("status", "--porcelain", "--", ".", ":(exclude)data/evals/runs")
    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(status)}


def digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Cache:
    """Question embeddings and judgements, keyed by everything that determines them."""

    def __init__(self, root: Path, enabled: bool) -> None:
        self.root, self.enabled = root, enabled

    def get(self, kind: str, key: str):
        path = self.root / kind / f"{key}.json"
        if self.enabled and path.exists():
            return json.loads(path.read_text())
        return None

    def put(self, kind: str, key: str, value) -> None:
        path = self.root / kind / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))


def first_hit_rank(
    ranked_pages: Sequence[tuple[str, int]], expected: Iterable[tuple[str, int]]
) -> int | None:
    """The 1-based rank of the first passage on an expected page, or None."""
    wanted = set(expected)
    return next((i for i, page in enumerate(ranked_pages, 1) if page in wanted), None)


def hit_metrics(first_hit_ranks: Sequence[int | None]) -> dict:
    """hit@1, hit@5 (counts) and MRR over the answerable questions' first-hit ranks."""
    n = len(first_hit_ranks)
    return {
        "hit_at_1": sum(r == 1 for r in first_hit_ranks),
        "hit_at_5": sum(r is not None and r <= 5 for r in first_hit_ranks),
        "mrr": round(sum(1 / r for r in first_hit_ranks if r) / n, 3) if n else None,
    }


def n_of(n: int, total: int) -> str:
    return f"{n} of {total}" if total else "-"


__all__ = [
    "Cache",
    "digest",
    "file_sha256",
    "first_hit_rank",
    "git_state",
    "hit_metrics",
    "n_of",
]
