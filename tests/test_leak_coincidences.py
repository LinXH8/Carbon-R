import pytest
from pydantic import ValidationError

from carbon_r.leak_check import Coincidence, Leak, split_reviewed


def _leak(path="src/x.py", figure="4,321") -> Leak:
    return Leak(where="working tree", path=path, figure=figure, at_base=False)


def _entry(path="src/x.py", figure="4,321") -> Coincidence:
    return Coincidence(path=path, figure=figure, reason="a timeout in seconds",
                       reviewed_by="author", reviewed_on="2026-11-02")  # fmt: skip


def test_a_reviewed_coincidence_exempts_only_its_own_path_and_figure():
    leaks = [_leak(), _leak(path="src/y.py"), _leak(figure="1,234")]
    reviewed, failing, unused = split_reviewed(leaks, [_entry()])
    assert reviewed == [leaks[0]]
    assert failing == leaks[1:]
    assert unused == []


def test_an_entry_that_matches_nothing_is_reported():
    _, _, unused = split_reviewed([], [_entry()])
    assert unused == [_entry()]


def test_an_entry_needs_a_reason_and_a_reviewer():
    with pytest.raises(ValidationError):
        Coincidence(path="a", figure="4,321", reason="", reviewed_by="x", reviewed_on="2026-11-02")
    with pytest.raises(ValidationError):
        Coincidence(path="a", figure="4,321", reason="r", reviewed_by="", reviewed_on="2026-11-02")
