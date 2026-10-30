"""The peer-figure leak check, against a throwaway git repository. Figures are invented."""

import subprocess
from pathlib import Path

import pytest

from carbon_r.leak_check import find_leaks

FIGURE = "48,213"
ALLOWED = {"data/peer_disclosures_verified.json"}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo, capture_output=True, check=True, text=True,
    ).stdout.strip()  # fmt: skip


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "--short=7", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, {"README.md": "A page count: 316.\n"}, "base")
    _git(tmp_path, "tag", "base")
    return tmp_path


def _new(leaks):
    return [(leak.where, leak.path, leak.figure) for leak in leaks if not leak.at_base]


def test_a_figure_introduced_in_a_commit_is_caught_with_its_commit(repo):
    sha = _commit(repo, {"docs/notes.md": f"Scope 1 was {FIGURE} tCO2e.\n"}, "leak")
    leaks = find_leaks(repo, {FIGURE}, base="base", allowed=ALLOWED)
    assert (sha, "docs/notes.md", FIGURE) in _new(leaks)


def test_a_figure_added_then_removed_is_still_caught_in_the_history(repo):
    sha = _commit(repo, {"docs/notes.md": f"{FIGURE}\n"}, "leak")
    _commit(repo, {"docs/notes.md": "removed\n"}, "remove it")
    assert _new(find_leaks(repo, {FIGURE}, base="base", allowed=ALLOWED)) == [
        (sha, "docs/notes.md", FIGURE)
    ]


def test_an_uncommitted_change_to_a_tracked_file_is_caught(repo):
    (repo / "README.md").write_text(f"A page count: 316. And {FIGURE}.\n")
    assert _new(find_leaks(repo, {FIGURE}, base="base", allowed=ALLOWED)) == [
        ("working tree", "README.md", FIGURE)
    ]


def test_the_allowed_file_is_not_searched(repo):
    _commit(repo, {"data/peer_disclosures_verified.json": f'{{"f": "{FIGURE}"}}\n'}, "data")
    assert find_leaks(repo, {FIGURE}, base="base", allowed=ALLOWED) == []


def test_a_figure_already_at_base_is_listed_not_new(repo):
    _commit(repo, {"README.md": "A page count: 316. Edited.\n"}, "edit")
    leaks = find_leaks(repo, {"316"}, base="base", allowed=ALLOWED)
    assert leaks and all(leak.at_base for leak in leaks)


def test_nothing_since_base_is_clean(repo):
    assert _new(find_leaks(repo, {FIGURE}, base="base", allowed=ALLOWED)) == []


def test_a_figure_inside_a_longer_number_does_not_match(repo):
    _commit(repo, {"docs/notes.md": f"1{FIGURE} and {FIGURE}.5 and {FIGURE}0\n"}, "near misses")
    assert _new(find_leaks(repo, {FIGURE}, base="base", allowed=ALLOWED)) == []


def test_short_figures_are_ignored(repo):
    _commit(repo, {"docs/notes.md": "42\n"}, "short")
    assert find_leaks(repo, {"42"}, base="base", allowed=ALLOWED) == []


def test_an_unknown_base_is_an_error_not_a_pass(repo):
    with pytest.raises(subprocess.CalledProcessError):
        find_leaks(repo, {FIGURE}, base="no-such-ref", allowed=ALLOWED)
