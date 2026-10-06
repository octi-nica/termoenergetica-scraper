"""The parser, checked against real page eras and small synthetic tables.

Fixtures are real snapshots: the 2026-09-18 page plus four older
ones (first scrape 2021, a backend error page, an all-sectors-empty page,
and the 2026 page in which CMTEB published a negative block count).
"""

import pytest

from scraper.parse import (
    fold,
    parse_agent,
    parse_page,
    parse_remediere,
    parse_sector,
    parse_street_line,
)
from tests.conftest import load_fixture

HEADER_ROW = "<tr><th>Sector</th><th>Zone</th><th>Agent</th><th>Cauza</th><th>Data</th></tr>"


def make_page(rows_html, wrapper_id="ST"):
    """Wrap table rows in the page structure the parser expects.

    Parameters
    ----------
    rows_html : str
        `<tr>` elements for the data rows.
    wrapper_id : str, optional
        id of the enclosing div, by default "ST" (the all-sectors tab).

    Returns
    -------
    str
        Minimal HTML page.
    """
    return f"<div id='{wrapper_id}'><table class='raport'>{HEADER_ROW}{rows_html}</table></div>"


def test_current_page_parses_every_street_with_flags():
    """The 2026-09-18 page: 10 rows, 22 PTs, 62 streets, first row fully checked."""
    result = parse_page(load_fixture("functionare_2026-09-18.html"))
    assert result.status == "ok"
    assert result.n_rows == 10
    assert len(result.observations) == 62
    pts = {(o.row_index, o.pt_name) for o in result.observations}
    assert len(pts) == 22
    first = result.observations[0]
    assert first.pt_name == "Ct Luterana"
    assert first.pt_name_norm == "ct luterana"
    assert first.blocks_count == 5
    assert first.blocks_unit == "blocuri/imobile"
    assert first.street == "Str Ion Câmpineanu"
    assert first.buildings == "Bl. 3, 6, 4, 5, 6A"
    assert first.is_oprire and not first.is_deficienta
    assert first.affects_acc and not first.affects_inc
    assert first.remediere_iso == "2026-10-01T20:00"
    for observation in result.observations:
        assert observation.blocks_count is not None
        assert observation.street_recognised


def test_pt_with_several_streets_gives_one_row_per_street():
    """A4 Basarabia lists two streets: two rows sharing the PT fields."""
    result = parse_page(load_fixture("functionare_2026-09-18.html"))
    rows = [o for o in result.observations if o.pt_name == "A4 Basarabia"]
    assert [o.street for o in rows] == ["Str Murgeni", "Bld Basarabia"]
    assert [o.buildings for o in rows] == ["bl. O1", "bl. A4, B3, D5, E2, F2, L10, L10A"]
    for observation in rows:
        assert observation.blocks_count == 8


def test_2021_era_with_self_closing_br_and_combined_services():
    """The 2021 markup (`<br/>`) and an "ACC/INC" agent giving both flags."""
    result = parse_page(load_fixture("2021-12-19_first-scrape.html"))
    assert result.status == "ok"
    assert len(result.observations) == 127
    combined = [o for o in result.observations if o.agent_raw == "Deficienta ACC/INC"]
    assert combined
    assert combined[0].is_deficienta and not combined[0].is_oprire
    assert combined[0].affects_acc and combined[0].affects_inc
    chibrit = [o for o in result.observations if o.pt_name == "D Chibrit"]
    assert len(chibrit) == 5
    assert chibrit[0].blocks_count == 12
    assert chibrit[3].street == "Str Puţul lui Crăciun"
    assert chibrit[3].buildings == "imob.Nr.5"


def test_negative_block_count_is_kept_as_published():
    """CMTEB published "-2 blocuri" on 2026-02-06; we keep it, not drop it."""
    result = parse_page(load_fixture("2026-02-06_negative-blocks.html"))
    assert result.status == "ok"
    assert len(result.observations) == 1036
    negatives = [o for o in result.observations if (o.blocks_count or 0) < 0]
    assert negatives


def test_empty_page_is_empty():
    """The yellow "no records" banner means a real zero, not an error."""
    result = parse_page(load_fixture("2025-01-01_all-sectors-empty.html"))
    assert result.status == "empty"
    assert result.error is None


def test_backend_error_page_is_error():
    """No table and no banner is a backend failure page."""
    result = parse_page(load_fixture("2022-03-07_backend-error-no-route.html"))
    assert result.status == "error"
    assert result.observations == []
    assert result.error


def test_header_without_block_count_is_kept_not_dropped():
    """A PT header with no "-- N blocuri" keeps the PT with blocks_count None."""
    row = ("<tr><td>4</td><td>Punct termic: <strong>X Fara Numar</strong><br>"
           "&bull; Str Unu - bl. 1<br>&bull; Fundatura Doi - bl. 2<br>&bull; Zona Trei - 5</td>"
           "<td>Oprire INC</td><td>Lucrari</td><td>Nedefinit</td></tr>")
    result = parse_page(make_page(row))
    assert result.status == "ok"
    assert len(result.observations) == 3
    assert [o.street_recognised for o in result.observations] == [True, True, False]
    observation = result.observations[2]
    assert observation.pt_name == "X Fara Numar"
    assert observation.blocks_count is None
    assert observation.street == "Zona Trei"
    assert observation.buildings == "5"
    assert observation.is_oprire and observation.affects_inc and not observation.affects_acc
    assert observation.remediere_iso == ""
    assert observation.remediere_raw == "Nedefinit"


def test_one_row_with_two_pts_shares_cause_and_estimate():
    """Every PT in a row becomes its own observation with the row's other cells."""
    row = ("<tr><td>2</td><td>Punct termic: <strong>A</strong> -- 2 blocuri<br>&bull; Str X - bl. 1"
           "<br>Punct termic: <strong>B</strong> -- 3 imobile<br>&bull; Bld Y - bl. 7</td>"
           "<td>Deficienta ACC</td><td>Avarie</td><td>01.02.2026 08:30</td></tr>")
    result = parse_page(make_page(row))
    names = [o.pt_name for o in result.observations]
    assert names == ["A", "B"]
    assert result.observations[1].blocks_unit == "imobile"
    for observation in result.observations:
        assert observation.row_index == 0
        assert observation.sector == 2
        assert observation.cause_raw == "Avarie"
        assert observation.remediere_iso == "2026-02-01T08:30"


def test_pt_name_without_strong_is_taken_from_header_text():
    """Older markup without `<strong>`: name sits between "Punct termic:" and the count."""
    row = ("<tr><td>1</td><td>Punct termic: Vitan 3 -- 4 blocuri/imobile<br>&bull; Cal Vitan - bl. 1</td>"
           "<td>Oprire ACC</td><td>x</td><td>Nedefinit</td></tr>")
    observation = parse_page(make_page(row)).observations[0]
    assert observation.pt_name == "Vitan 3"
    assert observation.blocks_count == 4


def test_duplicate_streets_give_one_row_with_both_buildings():
    """The same street listed twice under a PT is one row (folded comparison)."""
    row = ("<tr><td>1</td><td>Punct termic: <strong>P</strong> -- 2 blocuri<br>"
           "&bull; Str Ştefan - bl. 1<br>&bull; Str Stefan - bl. 2</td>"
           "<td>Oprire ACC</td><td>x</td><td>Nedefinit</td></tr>")
    observations = parse_page(make_page(row)).observations
    assert len(observations) == 1
    assert observations[0].street == "Str Ştefan"
    assert observations[0].buildings == "bl. 1; bl. 2"


def test_pt_without_streets_keeps_one_row():
    """A PT header with no street lines still yields a row, with an empty street."""
    row = ("<tr><td>1</td><td>Punct termic: <strong>Solo</strong> -- 1 blocuri</td>"
           "<td>Oprire ACC</td><td>x</td><td>Nedefinit</td></tr>")
    observations = parse_page(make_page(row)).observations
    assert len(observations) == 1
    assert observations[0].pt_name == "Solo"
    assert observations[0].street == ""
    assert observations[0].buildings == ""


def test_table_rows_without_pts_is_parse_error():
    """A table with rows but no readable PT is flagged, not reported as empty."""
    row = "<tr><td>1</td><td></td><td>Oprire ACC</td><td>x</td><td>Nedefinit</td></tr>"
    result = parse_page(make_page(row))
    assert result.status == "parse_error"
    assert result.n_rows == 1


def test_all_sectors_table_is_preferred():
    """With several tables, the one inside div#ST wins even if it is not first."""
    other = ("<tr><td>9</td><td>Punct termic: <strong>WRONG</strong> -- 1 blocuri</td>"
             "<td>Oprire ACC</td><td>x</td><td>Nedefinit</td></tr>")
    right = ("<tr><td>1</td><td>Punct termic: <strong>RIGHT</strong> -- 1 blocuri</td>"
             "<td>Oprire ACC</td><td>x</td><td>Nedefinit</td></tr>")
    html = make_page(other, wrapper_id="S1") + make_page(right, wrapper_id="ST")
    result = parse_page(html)
    assert [o.pt_name for o in result.observations] == ["RIGHT"]


@pytest.mark.parametrize("line,street,buildings,recognised", [
    ("• Şos Mihai Bravu - bl. P7", "Şos Mihai Bravu", "bl. P7", True),
    ("Pţa Alba Iulia - bl. 1", "Pţa Alba Iulia", "bl. 1", True),
    ("Int Serelor - 2", "Int Serelor", "2", True),
    ("Prl Ghencea - bl. 5", "Prl Ghencea", "bl. 5", True),
    ("Ale Lereşti - instituţie", "Ale Lereşti", "instituţie", True),
    ("B-dul Unirii - bl. 1", "B-dul Unirii", "bl. 1", True),
    ("Str Venerei -", "Str Venerei", "", True),
    ("Some Place - 3", "Some Place", "3", False),
    ("  •  ", "", "", False),
])
def test_street_lines(line, street, buildings, recognised):
    """Street / buildings split and prefix recognition on single lines."""
    assert parse_street_line(line) == (street, buildings, recognised)


@pytest.mark.parametrize("raw,expected", [
    ("Oprire ACC/INC", (True, False, True, True)),
    ("Deficienţă INC", (False, True, False, True)),
    ("Deficienta ACC", (False, True, True, False)),
    ("", (False, False, False, False)),
])
def test_parse_agent(raw, expected):
    """Severity and service flags from the agent cell."""
    flags = parse_agent(raw)
    actual = (flags["is_oprire"], flags["is_deficienta"], flags["affects_acc"], flags["affects_inc"])
    assert actual == expected


@pytest.mark.parametrize("raw,expected", [
    ("19.12.2021 23:00", "2021-12-19T23:00"),
    ("1.2.2026 8:05", "2026-02-01T08:05"),
    ("Nedefinit", ""),
    (None, ""),
])
def test_parse_remediere(raw, expected):
    """Estimated restore time to ISO minute."""
    assert parse_remediere(raw) == expected


def test_parse_sector():
    """Sector cell: integer or None."""
    assert parse_sector(" 3 ") == 3
    assert parse_sector("Ilfov") is None


def test_fold():
    """Folding handles both comma-below and cedilla diacritics."""
    assert fold("Şoseaua Ştefan cel Mare") == "soseaua stefan cel mare"
    assert fold("  Șoseaua   Țepeș ") == "soseaua tepes"
    assert fold(None) == ""
