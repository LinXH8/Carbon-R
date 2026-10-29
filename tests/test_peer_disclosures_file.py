"""The committed verified peer figures, and the export/load round trip."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from carbon_r.config import get_settings
from carbon_r.db import MIGRATIONS_DIR
from carbon_r.models.peer_disclosure import parse_figure
from carbon_r.peer_disclosures_file import (
    VERIFIED_FILE,
    VerifiedFile,
    VerifiedRow,
    dumps,
    export_rows,
    load_rows,
    read_file,
)
from carbon_r.sources import PEER_IDS


def _row(**overrides) -> dict:
    """An invented row in file form."""
    return {
        "source_id": "peer_dbs_2025",
        "company": "DBS",
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "scope": "1",
        "scope3_category": None,
        "figure_printed": "1,234",
        "figure": "1234",
        "unit": "tCO2e",
        "method": None,
        "pdf_page": 9,
        "printed_page": 9,
        "row_label": "Scope 1",
        "column_header": "2025",
        "notes": None,
        "verification": {
            "verified_by": "author",
            "verified_at": "2026-10-29T00:00:00+00:00",
            "outcome": "confirmed",
        },
        "draft": {
            "figure_printed": "1,234",
            "row_label": "Scope 1",
            "drafted_by": "zz-model",
            "drafted_at": "2026-10-29T00:00:00+00:00",
            "draft_request_hash": "zz",
            "page_image_sha256": "0" * 64,
        },
        "blind": {"in_sample": False, "author_value": None},
    } | overrides


# --- the committed file (runs anywhere, including CI) --------------------------------------


def test_the_committed_file_validates_ordered_and_cites_registered_reports():
    verified = read_file()
    assert verified.rows
    assert {row.source_id for row in verified.rows} <= set(PEER_IDS)
    for row in verified.rows:
        assert row.figure == parse_figure(row.figure_printed)
        assert row.verification.outcome in ("confirmed", "corrected")


def test_the_committed_file_is_written_deterministically():
    assert dumps(read_file()) == VERIFIED_FILE.read_text()


# --- the file's own rules -----------------------------------------------------------------


def test_a_figure_that_is_not_the_parse_of_the_printed_text_is_refused():
    with pytest.raises(ValidationError, match="parse_figure"):
        VerifiedRow.model_validate(_row(figure="1235"))
    with pytest.raises(ValidationError):
        VerifiedRow.model_validate(_row(figure_printed="2,5113", figure="25113"))


def test_a_rejected_or_unverified_row_cannot_be_in_the_file():
    with pytest.raises(ValidationError):
        rejected = {"verified_by": "a", "verified_at": "x", "outcome": "rejected"}
        VerifiedRow.model_validate(_row(verification=rejected))
    with pytest.raises(ValidationError):
        VerifiedRow.model_validate(_row(verification=None))


def test_a_blind_value_outside_the_sample_is_refused():
    with pytest.raises(ValidationError):
        VerifiedRow.model_validate(_row(blind={"in_sample": False, "author_value": "1,234"}))


def test_rows_must_be_ordered_and_unique():
    a, b = _row(pdf_page=1), _row(pdf_page=2)
    VerifiedFile.model_validate({"version": 1, "note": "n", "rows": [a, b]})
    with pytest.raises(ValidationError, match="order"):
        VerifiedFile.model_validate({"version": 1, "note": "n", "rows": [b, a]})
    with pytest.raises(ValidationError, match="twice"):
        VerifiedFile.model_validate({"version": 1, "note": "n", "rows": [a, a]})


def test_dumps_is_stable_and_keeps_figures_as_text():
    verified = VerifiedFile.model_validate({"version": 1, "note": "n", "rows": [_row()]})
    text = dumps(verified)
    assert text == dumps(VerifiedFile.model_validate_json(text))
    assert '"figure": "1234"' in text and VerifiedFile.model_validate_json(text).rows[
        0
    ].figure == Decimal("1234")


# --- export -> load, executed (needs a database; skipped in CI) -----------------------------


@pytest.mark.skipif(not get_settings().database_configured, reason="DATABASE_URL not configured")
def test_export_then_load_round_trips_and_a_second_load_changes_nothing():
    from carbon_r.db import connect
    from carbon_r.models.peer_disclosure import UPSERT_DISCLOSURE

    draft = {
        "source_id": "zz_test_source", "company": "ZZ", "period_start": "2025-01-01",
        "period_end": "2025-12-31", "scope": "2", "scope3_category": None,
        "figure_printed": "1,234", "figure": Decimal("1234"), "drafted_figure_printed": "1,234",
        "unit": "tCO2e", "method": "market", "pdf_page": 9, "printed_page": None,
        "row_label": "Scope 2¹", "drafted_row_label": "Scope 2¹", "column_header": "2025",
        "notes": None, "drafted_by": "zz-model", "drafted_at": "2026-10-29T01:02:03.456+00:00",
        "draft_request_hash": "zz", "page_image_sha256": "0" * 64, "blind_sample": True,
    }  # fmt: skip
    with connect() as conn:
        try:
            for name in ("0006_peer_disclosures.sql",):
                conn.execute((MIGRATIONS_DIR / name).read_text())
            conn.execute(
                "insert into sources (id, title, publisher, year, url, retrieved_at, sha256, "
                "kind, verification_status) values ('zz_test_source', 't', 'p', 1999, 'u', "
                "'2026-10-29', repeat('0', 64), 'peer_report', 'secondary only')"
            )
            conn.execute(UPSERT_DISCLOSURE, draft)
            conn.execute(UPSERT_DISCLOSURE, draft | {"drafted_row_label": "never verified"})
            conn.execute(
                "update peer_disclosures set verified_by = 'author', "
                "verified_at = '2026-10-29T04:05:06.789+00:00', verification = 'corrected', "
                "row_label = 'Scope 2', blind_figure_printed = '1,234' "
                "where source_id = 'zz_test_source' and drafted_row_label = 'Scope 2¹'"
            )
            first = export_rows(conn, ["zz_test_source"])

            conn.execute(
                "delete from peer_disclosures where source_id = 'zz_test_source' "
                "and verified_at is not null"
            )
            loaded = load_rows(conn, first)
            second = export_rows(conn, ["zz_test_source"])
            again = load_rows(conn, first)

            conn.execute(
                "update peer_disclosures set figure_printed = '1,235', figure = 1235 "
                "where source_id = 'zz_test_source' and verified_at is not null"
            )
            conflict = load_rows(conn, first)
        finally:
            conn.rollback()

    assert len(first.rows) == 1  # the unverified draft is not exported
    (row,) = first.rows
    assert (row.row_label, row.draft.row_label, row.blind.author_value) == (
        "Scope 2",
        "Scope 2¹",
        "1,234",
    )
    assert (loaded.inserted, loaded.unchanged, loaded.conflicts) == (1, 0, [])
    assert dumps(second) == dumps(first)
    assert (again.inserted, again.unchanged, again.conflicts) == (0, 1, [])
    assert conflict.inserted == 0 and "figure_printed" in conflict.conflicts[0]
