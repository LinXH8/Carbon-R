"""Caveats on verified peer rows (Tier 3 Block 1, proposal 1).

A footnote that qualifies a figure - "the last three months are estimates" - enters a
verified row as a caveat: the text as printed, its kind, and where it is printed. The author
enters it from the page; a separate command sets only the caveat columns of rows already
verified, never a figure, label or method. The lookup shows it with the figure, and the loop
attaches it to the reply in code. Invented companies, texts and values throughout.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from carbon_r.config import get_settings
from carbon_r.db import MIGRATIONS_DIR
from carbon_r.models.peer_caveat import (
    Caveat,
    CaveatEntry,
    CaveatTarget,
    attached_caveats,
    has_emissions_figure,
    plan_caveats,
)

ESTIMATE = (
    "For 2025 environmental data, the first nine months are based on actual data and the "
    "last three months on best estimates."
)


@pytest.mark.parametrize(
    ("text", "has_figure"),
    [
        (ESTIMATE, False),
        ("Scope 2 is market-based for Category 1 and Category 13 only.", False),
        ("Restated for FY2023/24 to include two new assets.", False),
        ("Location-based Scope 2 was 3,456 tCO2e.", True),
        ("Location-based Scope 2 emissions amounted to 3456.", True),
        ("Includes 6.6 kt of refrigerant losses.", True),
        ("Excludes 12 tonnes CO2e from the divested site.", True),
    ],
)
def test_a_caveat_may_not_carry_an_emissions_figure(text, has_figure):
    assert has_emissions_figure(text) is has_figure


def test_a_caveat_names_its_kind_and_one_place_it_is_printed():
    Caveat(printed=ESTIMATE, kind="estimate", pdf_page=37, cell=None)
    Caveat(printed="Grid factor used as a proxy.", kind="method", pdf_page=None,
           cell="Scope of Reporting!B12")  # fmt: skip
    for bad in (
        {"kind": "guess"},
        {"pdf_page": None},
        {"cell": "Scope of Reporting!B12"},
        {"printed": "  "},
        {"printed": "Location-based Scope 2 was 3,456 tCO2e."},
    ):
        with pytest.raises(ValidationError):
            Caveat(**({"printed": ESTIMATE, "kind": "estimate", "pdf_page": 37, "cell": None}
                      | bad))  # fmt: skip


def _target(i, label, caveat=None, page=37):
    return CaveatTarget(id=i, pdf_page=page, sheet_name=None, cell_ref=None,
                        drafted_row_label=label, column_header="2025¹", method=None,
                        caveat=caveat)  # fmt: skip


def _entry(label, printed=ESTIMATE, kind="estimate", page=37):
    return CaveatEntry(pdf_page=page, sheet_name=None, cell_ref=None, row_label=label,
                       column_header="2025¹", method=None, caveat_printed=printed,
                       caveat_kind=kind, caveat_pdf_page=37 if printed else None,
                       caveat_cell=None)  # fmt: skip


def test_the_plan_sets_caveats_on_verified_rows_only_and_skips_blank_lines():
    targets = [_target(1, "Scope 1"), _target(2, "Scope 2 (market-based)")]
    plan = plan_caveats(targets, [_entry("Scope 1"), _entry("Scope 2 (market-based)",
                                                           printed="", kind="")])  # fmt: skip
    assert plan.problems == [] and [u.id for u in plan.updates] == [1] and plan.unchanged == 0


def test_a_line_for_no_verified_row_stops_everything():
    plan = plan_caveats([_target(1, "Scope 1")], [_entry("Scope 1"), _entry("Scope 9")])
    assert plan.updates == [] and any("Scope 9" in p for p in plan.problems)


def test_an_identical_caveat_is_unchanged_and_a_different_one_is_refused():
    have = Caveat(printed=ESTIMATE, kind="estimate", pdf_page=37, cell=None)
    again = plan_caveats([_target(1, "Scope 1", caveat=have)], [_entry("Scope 1")])
    assert again.updates == [] and again.unchanged == 1 and again.problems == []
    other = plan_caveats([_target(1, "Scope 1", caveat=have)],
                         [_entry("Scope 1", printed="Restated.", kind="restated")])  # fmt: skip
    assert other.updates == [] and any("already" in p for p in other.problems)


def test_the_caveats_of_this_turns_figures_are_attached_once_each():
    figure = {
        "company": "Example Engineering", "row_label": "Scope 1", "column_header": "2025¹",
        "caveat_printed": ESTIMATE, "caveat_kind": "estimate",
        "caveat_citation": "Example Engineering Sustainability Report 2025, PDF p. 37",
    }  # fmt: skip
    plain = figure | {"row_label": "Scope 2", "caveat_printed": None, "caveat_kind": None,
                      "caveat_citation": None}  # fmt: skip
    payloads = [
        {"tool": "lookup_peer_disclosure", "status": "ok", "data": {"figures": [figure, plain]}},
        {"tool": "lookup_peer_disclosure", "status": "ok", "data": {"figures": [figure]}},
        {"tool": "search_peer_disclosures", "status": "ok", "data": {"passages": []}},
    ]
    (line,) = attached_caveats(payloads)
    assert line.startswith("Example Engineering, Scope 1 [2025¹]: partly estimated")
    assert ESTIMATE in line and "PDF p. 37" in line
    assert attached_caveats([]) == []


# --- the lookup and the loop ------------------------------------------------------------


def test_the_lookup_shows_a_caveat_with_its_figure():
    from carbon_r.tools.peer_figures import PeerQuery, _resolve

    row = {
        "source_id": "peer_stengg_2025", "title": "Example Engineering Sustainability Report",
        "company": "ST Engineering", "period_start": datetime(2025, 1, 1).date(),
        "period_end": datetime(2025, 12, 31).date(), "scope": "1", "scope3_category": None,
        "method": None, "figure_printed": "6.6", "figure": Decimal("6.6"), "unit": "kt",
        "pdf_page": 37, "printed_page": 37, "sheet_name": None, "cell_ref": None,
        "row_label": "Scope 1", "column_header": "2025¹", "notes": None,
        "verification": "confirmed", "verified_at": datetime(2026, 11, 10, tzinfo=UTC),
        "drafted_by": "drafter", "caveat_printed": ESTIMATE, "caveat_kind": "estimate",
        "caveat_pdf_page": 37, "caveat_cell": None,
    }  # fmt: skip
    result = _resolve(PeerQuery(company="ST Engineering", scope="1"), [row], [], [])
    assert result.status == "ok"
    (figure,) = result.data["figures"]
    assert figure["caveat_kind"] == "estimate" and figure["caveat_printed"] == ESTIMATE
    assert f'Printed with this figure (PDF p. 37): "{ESTIMATE}"' in result.message
    assert "partly estimated" in result.message


def test_the_loop_attaches_the_caveat_in_code(session):
    from carbon_r.agent.loop import run_agent
    from carbon_r.agent.tools import Toolkit
    from carbon_r.tools.base import ok
    from conftest import call, fake_client

    figure = {"company": "ST Engineering", "row_label": "Scope 1", "column_header": "2025¹",
              "caveat_printed": ESTIMATE, "caveat_kind": "estimate",
              "caveat_citation": "Example report, PDF p. 37"}  # fmt: skip

    def fake_lookup_peers(*_, **__):
        return ok({"figures": [figure]}, "1 verified figure.")

    run = run_agent(
        session, "q",
        client=fake_client(call("lookup_peer_disclosure", company="ST Engineering", scope="1"),
                           "Its Scope 1 is as printed."),
        model="m", toolkit=Toolkit(session, lookup_peers=fake_lookup_peers),
    )  # fmt: skip
    assert run.reply.startswith("Its Scope 1 is as printed.")
    assert ESTIMATE in run.reply and run.reply.index(ESTIMATE) > run.reply.index("self-reported")


# --- the table ----------------------------------------------------------------------------


@pytest.mark.skipif(not get_settings().database_configured, reason="DATABASE_URL not configured")
def test_migration_0009_keeps_a_caveat_whole_and_on_verified_rows_only():
    """0006-0009 applied twice, in a transaction that is rolled back."""
    import psycopg

    from carbon_r.db import connect

    names = ("0006_peer_disclosures.sql", "0007_peer_cell_key_method.sql",
             "0008_peer_workbook_cells.sql", "0009_peer_caveats.sql")  # fmt: skip
    insert = (
        "insert into peer_disclosures (source_id, company, period_start, period_end, scope, "
        "figure_printed, figure, drafted_figure_printed, unit, pdf_page, row_label, "
        "drafted_row_label, column_header, drafted_by, drafted_at, draft_request_hash, "
        "page_image_sha256, verified_by, verified_at, verification) values ('zz_test_source', "
        "'ZZ', '2025-01-01', '2025-12-31', '1', '6.6', 6.6, '6.6', 'kt', 37, %(label)s, "
        "%(label)s, '2025', 'm', now(), 'h', repeat('0', 64), %(by)s, %(at)s, %(outcome)s) "
        "returning id"
    )
    set_caveat = (
        "update peer_disclosures set caveat_printed = %(t)s, caveat_kind = %(k)s, "
        "caveat_pdf_page = %(p)s, caveat_cell = %(c)s, caveat_by = 'author', caveat_at = now() "
        "where id = %(id)s"
    )
    with connect() as conn:
        try:
            for name in names + names:
                conn.execute((MIGRATIONS_DIR / name).read_text())
            conn.execute(
                "insert into sources (id, title, publisher, year, url, retrieved_at, sha256, "
                "kind, verification_status) values ('zz_test_source', 't', 'p', 1999, 'u', "
                "'2026-11-11', repeat('0', 64), 'peer_report', 'secondary only')"
            )
            verified = conn.execute(insert, {"label": "a", "by": "author",
                                             "at": datetime.now(UTC),
                                             "outcome": "confirmed"}).fetchone()[0]  # fmt: skip
            draft = conn.execute(insert, {"label": "b", "by": None, "at": None,
                                          "outcome": None}).fetchone()[0]  # fmt: skip
            good = {"t": ESTIMATE, "k": "estimate", "p": 37, "c": None, "id": verified}
            conn.execute(set_caveat, good)
            for bad in (
                good | {"id": draft},  # not verified
                good | {"k": "guess"},
                good | {"k": None},
                good | {"p": None},  # nowhere
                good | {"c": "Sheet!B12"},  # two places
                good | {"c": "B12", "p": None},  # not Sheet!A1
            ):
                with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                    conn.execute(set_caveat, bad)
            with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute(
                    "update peer_disclosures set caveat_by = null where id = %(id)s",
                    {"id": verified},
                )
            columns = {
                r[0]
                for r in conn.execute(
                    "select column_name from information_schema.columns "
                    "where table_name = 'peer_disclosures_verified'"
                )
            }
            assert {"caveat_printed", "caveat_kind", "caveat_by"} <= columns
        finally:
            conn.rollback()


def test_a_caveat_travels_in_the_verified_file_and_a_row_without_one_is_written_as_before():
    from carbon_r.peer_disclosures_file import VERIFIED_FILE, VerifiedRow, read_file

    if not VERIFIED_FILE.exists():
        pytest.skip("no verified file")
    row = read_file().rows[0]
    assert "caveat" not in row.model_dump(mode="json")
    record = {"printed": ESTIMATE, "kind": "estimate", "pdf_page": 37, "cell": None,
              "added_by": "author", "added_at": "2026-11-11T00:00:00+00:00"}  # fmt: skip
    data = row.model_dump(mode="json") | {"caveat": record}
    assert VerifiedRow.model_validate(data).model_dump(mode="json")["caveat"] == record
    with pytest.raises(ValidationError):
        VerifiedRow.model_validate(data | {"caveat": record | {"printed": "It was 3,456 t."}})
