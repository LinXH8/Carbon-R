"""The pre-push hook blocks a push when the leak check fails, and passes it the right range.

The hook is run by a real `git push` to a throwaway bare repository. The leak check itself is
tested in test_leak_check.py; here a stub stands in for it, so this test is about the
hook's wiring: that git runs it, that its exit code decides the push, and which commits it
asks the check to read."""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from carbon_r.config import PROJECT_ROOT

HOOK = PROJECT_ROOT / ".githooks" / "pre-push"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

STUB = """import os, sys
with open(os.environ["LEAK_ARGS"], "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("LEAK_EXIT", "0")))
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    remote, work, bin_dir = tmp_path / "remote.git", tmp_path / "work", tmp_path / "bin"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True,
                   capture_output=True)  # fmt: skip
    (work / "scripts").mkdir()
    (work / "scripts" / "draft_peer_disclosures.py").write_text(STUB)
    (work / ".githooks").mkdir()
    shutil.copy(HOOK, work / ".githooks" / "pre-push")
    _git(work, "config", "core.hooksPath", ".githooks")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "base")
    # A `uv` on PATH that runs the stub with this interpreter: `uv run --quiet python X` ->
    # `<python> X`.
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(f'#!/bin/sh\nshift 3\nexec "{sys.executable}" "$@"\n')
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    return work


def _push(work: Path, leak_exit: int, *args: str) -> subprocess.CompletedProcess:
    env = os.environ | {
        "PATH": f"{work.parent / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "LEAK_EXIT": str(leak_exit),
        "LEAK_ARGS": str(work.parent / "args.txt"),
    }
    return subprocess.run(["git", "push", "-q", "origin", *args], cwd=work, env=env,
                          capture_output=True, text=True)  # fmt: skip


def _remote_head(work: Path) -> str:
    return _git(work.parent / "remote.git", "rev-parse", "main")


def test_the_hook_is_executable_and_installed_by_make():
    assert os.access(HOOK, os.X_OK)
    assert "git config core.hooksPath .githooks" in (PROJECT_ROOT / "Makefile").read_text()


def test_a_failing_check_blocks_the_push(clone):
    first = _git(clone, "rev-parse", "HEAD")
    assert _push(clone, 0, "main").returncode == 0
    (clone / "notes.md").write_text("x\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-q", "-m", "would leak")
    result = _push(clone, 1, "main")
    assert result.returncode != 0
    assert "BLOCKED" in result.stderr
    assert _remote_head(clone) == first  # nothing reached the remote


def test_a_passing_check_lets_the_push_through_with_the_pushed_range(clone):
    assert _push(clone, 0, "main").returncode == 0
    before = _git(clone, "rev-parse", "HEAD")
    (clone / "notes.md").write_text("x\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-q", "-m", "clean")
    after = _git(clone, "rev-parse", "HEAD")
    assert _push(clone, 0, "main").returncode == 0
    assert _remote_head(clone) == after
    last = (clone.parent / "args.txt").read_text().splitlines()[-1]
    assert last == f"leak-check --base {before} --head {after}"


def test_ci_runs_the_leak_check_on_full_history_before_the_tests():
    ci = yaml.safe_load((PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps = ci["jobs"]["test"]["steps"]
    assert steps[0]["with"]["fetch-depth"] == 0
    names = [s.get("name") for s in steps]
    check = steps[names.index("Leak check (peer figures)")]
    assert "draft_peer_disclosures.py leak-check --base" in check["run"]
    assert names.index("Leak check (peer figures)") < names.index("Tests (offline)")
