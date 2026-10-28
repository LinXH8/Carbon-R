"""The peer reports in the source registry: built from data/peer_sources.json, no PDFs needed."""

from carbon_r.peer_sources import COMPANY_SLUGS, PRINTED_PAGE_OFFSETS, load_peer_sources
from carbon_r.sources import BY_ID, METHODOLOGY_IDS, PEER_IDS, PEERS

ENTRIES = load_peer_sources().sources


def test_every_peer_file_is_registered_once_with_its_recorded_bytes():
    assert len(PEER_IDS) == len(ENTRIES) == 14
    assert len(set(PEER_IDS)) == 14
    by_sha = {BY_ID[i].sha256: BY_ID[i] for i in PEER_IDS}
    for entry in ENTRIES:
        record = by_sha[entry.sha256]
        assert record.kind == "peer_report"
        assert record.local_path == f"data/raw/{entry.local_filename}"
        assert record.year == entry.period_end.year
        assert record.publisher == entry.company


def test_ids_are_the_documented_shape():
    assert {"peer_dbs_2025", "peer_clar_2025_datapack", "peer_seatrium_2025_databook"} <= set(
        PEER_IDS
    )
    assert PEERS["peer_mpact_2026"].period_end.isoformat() == "2026-03-31"
    assert PEERS["peer_cict_2024"].report_year == 2024


def test_every_company_has_a_slug_and_every_slug_a_company():
    assert set(COMPANY_SLUGS) == {e.company for e in ENTRIES}


def test_offsets_name_only_registered_reports():
    assert set(PRINTED_PAGE_OFFSETS) <= set(PEER_IDS)
    for source_id in PEER_IDS:
        assert BY_ID[source_id].printed_page_offset == PRINTED_PAGE_OFFSETS.get(source_id)


def test_methodology_sources_are_unchanged():
    assert METHODOLOGY_IDS == ("ghgp_corporate", "ghgp_scope2", "ghgp_scope3")
