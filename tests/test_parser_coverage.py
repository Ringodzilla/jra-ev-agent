from __future__ import annotations

from bs4 import BeautifulSoup
import pytest

from jra_scraper.models import HorseEntry, ParserIssue
from jra_scraper.parser import JRAParser, HeaderMatch, _extract_access_o_cnames, _parse_odds_range


def _soup(html: str):
    return BeautifulSoup(html, "html.parser")


def test_odds_cname_and_race_link_fallbacks() -> None:
    parser = JRAParser("https://jra.test")
    anchor = "<a onclick=\"doAction('/JRADB/accessO.html', 'win')\">単勝・複勝</a>"
    assert parser.extract_initial_odds_cname(anchor) == "win"
    raw = "<script>doAction('/JRADB/accessO.html', 'abcouSxyz')</script>"
    assert parser.extract_initial_odds_cname(raw) == "abcouSxyz"
    assert parser.extract_initial_odds_cname("<p>none</p>") == ""
    assert parser.extract_odds_cnames("<a onclick=\"broken\">単勝・複勝</a>") == {}
    assert _extract_access_o_cnames("doAction('/JRADB/accessO.html', ' x ')") == ["x"]

    html = '<a href="/JRADB/accessD.html"></a><a href="/JRADB/accessD.html?20260829">札幌7R Race</a>'
    links = parser.parse_race_list(html)
    assert len(links) == 1
    fallback = parser._build_race_id("/no-date", "Unknown")
    assert fallback.startswith("race_")


def test_public_parser_missing_and_blank_tables_report_issues() -> None:
    parser = JRAParser("https://jra.test")
    issues: list[ParserIssue] = []
    with pytest.raises(ValueError):
        parser.parse_race_detail("<p>none</p>", "R", "Race", issue_sink=issues)
    assert issues[-1].code == "entry_table_missing"

    issues = []
    blank_entry = "<table><tr><th>枠</th><th>馬番</th><th>馬名</th></tr><tr><td></td><td></td><td></td></tr></table>"
    with pytest.raises(ValueError):
        parser.parse_race_detail(blank_entry, "R", "Race", issue_sink=issues)
    assert any(issue.code == "entry_row_incomplete" for issue in issues)

    issues = []
    assert parser.parse_horse_last5("<p>none</p>", "R", "H", "Horse", "url", issue_sink=issues) == []
    assert issues[-1].code == "history_table_missing"

    blank_history = (
        "<table><tr><th>日付</th><th>着順</th><th>上り3F</th><th>人気</th></tr>"
        "<tr><td></td><td></td><td></td><td></td></tr></table>"
    )
    assert parser.parse_horse_last5(blank_history, "R", "H", "Horse", "url") == []


def test_history_current_entry_enrichment() -> None:
    parser = JRAParser("https://jra.test")
    html = (
        "<table><tr><th>日付</th><th>着順</th><th>上り3F</th><th>人気</th></tr>"
        "<tr><td>2026/01/01</td><td>1</td><td>35.0</td><td>2</td></tr></table>"
    )
    entry = HorseEntry(
        race_id="R", race_name="Race", horse_id="H", horse_name="Horse", horse_url="url",
        frame_number="1", horse_number="2", current_jockey="J", assigned_weight="55",
        current_body_weight="470", body_weight_change="2", body_weight_status="published",
        current_odds="3", current_popularity="2", target_track="札幌", target_race_date="2026-08-29",
        target_race_number="7", target_surface="芝", target_distance="1200", target_weather="晴",
        target_track_condition="良", target_conditions_captured_at="T", horse_country="JPN",
    )
    rows = parser.parse_horse_last5(html, "R", "H", "Horse", "url", current_entry=entry)
    assert rows[0]["current_body_weight"] == "470"


def test_malformed_odds_tables_are_skipped() -> None:
    parser = JRAParser("https://jra.test")
    win_place = _soup("<table><tbody><tr><td class=odds_tan>2.0</td><td class=odds_fuku>1.2-1.4</td></tr></tbody></table>")
    assert parser._parse_win_place_odds(win_place, race_id="R", source_cname="c", captured_at="T") == []

    matrices = _soup(
        "<table class=fuku3><caption class='pair1_2'></caption><tbody><tr><th></th><td>3.0</td></tr></tbody></table>"
        "<table class=fuku3><tbody><tr><th>3</th><td>3.0</td></tr></tbody></table>"
        "<table class=fuku3><caption>1</caption><tbody><tr><th>2</th><td></td></tr></tbody></table>"
        "<table class=fuku3><caption>1</caption><tbody><tr><th>2</th><td>3.0</td></tr></tbody></table>"
    )
    assert parser._parse_matrix_odds(
        matrices, "sanrenpuku", "fuku3", "R", "c", "T", caption_pair=True
    ) == []

    trifecta = _soup(
        "<div><table class=tan3><tbody><tr><th>3</th><td>5</td></tr></tbody></table></div>"
        "<div><div class=p_line><span class=num>1</span></div><div class=p_line><span class=num>2</span></div>"
        "<table class=tan3><tbody><tr><th></th><td>5</td></tr><tr><th>3</th><td></td></tr></tbody></table></div>"
    )
    assert parser._parse_trifecta_odds(trifecta, race_id="R", source_cname="c", captured_at="T") == []


def test_table_header_repair_and_mapping_edges() -> None:
    parser = JRAParser("https://jra.test")
    assert parser._select_last5_table(_soup("<table><tr></tr></table>")) is None
    assert parser._extract_headers(_soup("<table></table>").table) == []
    td_header = _soup("<table><tr><td>A</td><td>B</td><td>C</td></tr></table>").table
    assert parser._extract_headers(td_header) == ["A", "B", "C"]

    issues: list[ParserIssue] = []
    assert parser._repair_cells(["a"], 3, [], issues, context={}, aggressive=False) == ["a", "", ""]
    assert issues[-1].code == "row_padding"
    assert parser._repair_cells(["a", "b", "c"], 2, [], issues, context={}, aggressive=True) == ["a", "b c"]
    assert parser._map_row([HeaderMatch(2, "x", "X")], ["a"]) == {}


def test_entry_fallbacks_cover_country_numbers_odds_weight_and_jockey() -> None:
    parser = JRAParser("https://jra.test")
    flag_row = _soup(
        "<table><tr><td class=horse><div class=name_line><div class=name><div class=line>"
        "<span class=flag><img alt='USA'></span></div></div></div></td></tr></table>"
    ).tr
    assert parser._apply_entry_fallbacks({}, flag_row, [], False, allow_odds_fallback=False)["horse_country"] == "USA"

    empty_row = _soup("<table><tr><td></td></tr></table>").tr
    mapped = parser._apply_entry_fallbacks({}, empty_row, ["9", "4"], False, allow_odds_fallback=False)
    assert mapped["horse_number"] == "9" and mapped["frame_number"] == "4"

    mapped = parser._apply_entry_fallbacks(
        {"frame_number": "1", "horse_number": "2", "assigned_weight": "55"},
        empty_row,
        ["x", "1", "2", "3.5"],
        False,
        allow_odds_fallback=True,
    )
    assert mapped["current_odds"] == "3.5"
    assert parser._apply_entry_fallbacks({}, empty_row, ["55.0"], False, allow_odds_fallback=False)["assigned_weight"] == "55.0"
    assert parser._apply_entry_fallbacks(
        {"horse_name": "Horse"}, empty_row, ["Rider"], True, allow_odds_fallback=False
    )["current_jockey"] == "Rider"
    assert parser._parse_current_body_weight("unknown") == ("", "", "unpublished")


def test_condition_embedded_dedupe_and_low_level_helpers() -> None:
    parser = JRAParser("https://jra.test")
    soup = _soup(
        "<div class='cell baba'><ul><li><span class=cap>芝以外</span><span class=txt>重</span></li></ul></div>"
    )
    assert parser._extract_current_race_conditions(soup, target_surface="芝以外") == ("", "重")

    blank_past = _soup("<table><tr><td class=past><span>empty</span></td></tr></table>").tr
    assert parser._extract_embedded_history(blank_past) == []
    horse = HorseEntry(race_id="R", race_name="Race", horse_id="H", horse_name="Horse", horse_url="url")
    assert parser._dedupe_horses([horse, horse]) == [horse]

    assert parser._extract_frame_number_from_row(_soup("<tr><td class=waku>枠3</td></tr>").tr) == "3"
    assert parser._extract_frame_number_from_row(_soup("<tr><td class=waku>none</td></tr>").tr) == ""
    assert parser._extract_text(_soup("<div></div>").div, ".missing") == ""
    assert parser._cell_text(None) == ""
    sink: list[ParserIssue] = []
    parser._issue(sink, stage="x", severity="low", code="c", message="m", context={})
    assert sink[0].code == "c"
    assert _parse_odds_range("") == ("", "")
