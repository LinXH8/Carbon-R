"""The chunker: exact page citations, cleaning, and the printed-page rule.

The page texts here are written by hand to have the shapes the GHG Protocol PDFs have - a
vertical margin tab, words hyphenated across lines, the three footer styles - without
copying any of their text, which their terms do not permit in a public repository.
"""

import pytest

from carbon_r.rag.chunking import (
    CHUNKER_VERSION,
    Page,
    chunk_page,
    chunk_pages,
    chunker_version,
    clean_page,
    embedding_input,
    footer_numbers,
    normalise,
    printed_page,
)


def words(n: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def page(n: int, text: str, layout: str = "") -> Page:
    return Page(pdf_page=n, text=text, layout_text=layout)


# --- cleaning ---------------------------------------------------------------------------


def test_single_capital_letter_lines_are_dropped():
    raw = "M\n\nBoundaries are set once\nA\nR\nand applied every year.\nG\n"
    assert clean_page(raw) == "Boundaries are set once and applied every year."


def test_a_word_hyphenated_across_a_line_is_rejoined():
    assert clean_page("the reporting compa-\nny decides") == "the reporting company decides"


def test_a_real_hyphen_before_a_capital_is_kept():
    assert clean_page("location-\nBased") == "location- Based"


def test_whitespace_is_collapsed():
    assert clean_page("a  b\n\n\tc   ") == "a b c"


def test_digits_and_markers_alone_on_a_line_are_content_and_kept():
    """Table cells and footnote markers. Dropping them would delete values."""
    assert clean_page("Total\n4\nsites\n*\nestimated") == "Total 4 sites * estimated"


def test_normalise_matches_what_cleaning_stores():
    assert normalise("x \n  y") == "x y"


# --- chunking ---------------------------------------------------------------------------


def test_a_short_page_is_one_chunk():
    (chunk,) = chunk_page(page(3, words(40)), offset=None, window=300, overlap=50)
    assert chunk.pdf_page == 3 and chunk.chunk_index == 0
    assert chunk.text == words(40)


def test_an_empty_page_makes_no_chunk():
    assert chunk_page(page(1, ""), offset=None) == []


def test_long_pages_overlap_by_the_stated_amount():
    chunks = chunk_page(page(1, words(25)), offset=None, window=10, overlap=3)
    texts = [c.text.split() for c in chunks]
    assert [len(t) for t in texts] == [10, 10, 10, 4]
    for left, right in zip(texts, texts[1:], strict=False):
        assert left[-3:] == right[:3]
    # Every word of the page is in some chunk.
    assert {w for t in texts for w in t} == set(words(25).split())


def test_a_chunk_never_spans_two_pages():
    pages = [page(1, words(14, "a")), page(2, words(14, "b"))]
    for chunk in chunk_pages(pages, offset=None, window=10, overlap=3):
        prefix = "a" if chunk.pdf_page == 1 else "b"
        assert all(word.startswith(prefix) for word in chunk.text.split())


def test_chunk_indexes_restart_on_each_page():
    pages = [page(1, words(14)), page(2, words(14))]
    chunks = chunk_pages(pages, offset=None, window=10, overlap=3)
    assert [(c.pdf_page, c.chunk_index) for c in chunks] == [(1, 0), (1, 1), (2, 0), (2, 1)]


def test_the_content_hash_follows_the_text():
    a, b = chunk_page(page(1, words(14)), offset=None, window=10, overlap=3)
    assert a.content_sha256 != b.content_sha256
    assert len(a.content_sha256) == 64


# --- printed pages ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "footer",
    [
        "28",
        "28 Scope 2 Guidance",
        "Setting Organizational Boundaries     28",
        "[28] Corporate Value Chain (Scope 3) Accounting and Reporting Standard",
        "                                                       [28]",
    ],
)
def test_each_footer_shape_is_read(footer):
    layout = f"Some body text on the page\nmore body text\n{footer}\n"
    assert 28 in footer_numbers(layout)
    assert printed_page(layout, pdf_page=30, offset=2) == 28


def test_a_number_that_disagrees_with_the_offset_is_not_used():
    """A chapter number in the last line ("03 Summary ...") must not become a citation."""
    layout = "03 Summary of Steps and Requirements\nbody\n03 Summary of Steps and Requirements"
    assert printed_page(layout, pdf_page=20, offset=2) is None


def test_no_footer_means_no_printed_page_never_an_inferred_one():
    assert printed_page("A full-page figure\nwith a caption", pdf_page=40, offset=2) is None


def test_no_offset_means_no_printed_page():
    assert printed_page("body\n28", pdf_page=30, offset=None) is None


def test_chunks_carry_the_confirmed_printed_page():
    (chunk,) = chunk_page(page(30, words(5), "body\n28 Scope 2 Guidance"), offset=2)
    assert chunk.printed_page == 28


# --- version ----------------------------------------------------------------------------


def test_the_title_flag_is_part_of_the_version_and_off_reproduces_the_original():
    assert chunker_version(300, 50, embed_title=True) != chunker_version(300, 50, embed_title=False)
    assert chunker_version(300, 50, embed_title=False) == "20927f94c1b4"


def test_the_title_goes_into_the_embedding_input_only_when_asked():
    assert embedding_input("Std", "body", embed_title=True) == "Std\n\nbody"
    assert embedding_input("Std", "body", embed_title=False) == "body"


def test_the_version_changes_with_either_parameter():
    base = chunker_version(300, 50)
    assert base == CHUNKER_VERSION
    assert chunker_version(250, 50) != base
    assert chunker_version(300, 40) != base


def test_the_version_changes_with_the_revision(monkeypatch):
    import carbon_r.rag.chunking as chunking

    before = chunking.chunker_version(300, 50)
    monkeypatch.setattr(chunking, "CHUNKER_REVISION", "2")
    assert chunking.chunker_version(300, 50) != before


@pytest.mark.parametrize(("window", "overlap"), [(10, 10), (10, 11), (10, -1)])
def test_an_overlap_that_cannot_advance_is_refused(window, overlap):
    with pytest.raises(ValueError):
        chunker_version(window, overlap)
