"""data/peer_sources.json: its shape anywhere, its files where the reports are present."""

from collections import Counter

import pytest
from peer_intake import RAW, check, load_peer_sources

SOURCES = load_peer_sources()


def test_the_file_validates_and_names_twelve_companies():
    assert len({s.company for s in SOURCES.sources}) == 12


def test_each_file_appears_once():
    names = Counter(s.local_filename for s in SOURCES.sources)
    assert max(names.values()) == 1
    assert len({s.sha256 for s in SOURCES.sources}) == len(SOURCES.sources)


def test_an_assumed_start_is_never_labelled_stated():
    for s in SOURCES.sources:
        if "assumed" in s.notes.lower():
            assert s.period_basis == "start_assumed", s.local_filename


@pytest.mark.skipif(
    not all((RAW / s.local_filename).exists() for s in SOURCES.sources),
    reason="needs the peer reports in data/raw/peer/ (git-ignored)",
)
def test_every_file_matches_its_recorded_bytes():
    assert check() == 0
