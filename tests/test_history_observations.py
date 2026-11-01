from bs4 import BeautifulSoup
import pytest

from jra_scraper.history_observations import parse_result_history_semantics, sectional_observation
from jra_scraper.parser import JRAParser


def result_page(header="推定上り", value="35.1", course="芝・右", corner="3-2", place="1"):
    return BeautifulSoup(f'''<div class="cell course">コース：1,200メートル（{course}）</div>
    <table><thead><tr><th>着順</th><th class="f_time">{header}</th><th>通過順位</th></tr></thead>
    <tbody><tr><td class="place">{place}</td><td class="f_time">{value}</td>
    <td class="corner">{corner}</td></tr></tbody></table>''', "html.parser")


def test_result_average_furlong_is_not_a_last3f_observation():
    soup = result_page("平均1F", "14.0", "芝→ダート")
    result = parse_result_history_semantics(soup, soup.select_one("tbody tr"))
    assert result["average_1f"] == "14.0"
    assert result["average_1f_status"] == "OBSERVED"
    assert result["last_3f"] == ""
    assert result["last_3f_status"] == "NOT_APPLICABLE"
    assert result["last_3f_missing_reason"] == "RESULT_COLUMN_IS_AVERAGE_1F"
    assert result["sectional_header"] == "平均1F"


@pytest.mark.parametrize("header", ["推定上り", "上り3F", "上がり3F"])
def test_normal_flat_result_retains_observed_last3f(header):
    soup = result_page(header)
    result = parse_result_history_semantics(soup, soup.select_one("tbody tr"))
    assert result["last_3f"] == "35.1"
    assert result["last_3f_status"] == "OBSERVED"
    assert result["passing_order"] == "3-2"
    assert result["passing_order_status"] == "OBSERVED"


def test_unknown_header_never_establishes_sectional_units_from_value():
    soup = result_page("不明な指標", "14.0", "芝→ダート")
    result = parse_result_history_semantics(soup, soup.select_one("tbody tr"))
    assert result["last_3f"] == result["average_1f"] == ""
    assert result["last_3f_status"] == "UNVERIFIED"
    assert result["sectional_raw"] == "14.0"


@pytest.mark.parametrize("raw,header", [("上り3F 未測定", "上り3F"), ("3F 計不", "上り3F"),
                                       ("平均1F", "平均1F"), ("平均1F --", "平均1F")])
def test_unit_label_digits_are_never_timing_observations(raw, header):
    result = sectional_observation(raw, header)
    assert result["last_3f"] == result["average_1f"] == ""
    assert result["last_3f_status"] != "OBSERVED"
    assert result["average_1f_status"] == "NOT_OBSERVED"


@pytest.mark.parametrize("raw,header", [("平均1F 14.2", "上り3F"), ("3F 35.4", "平均1F")])
def test_conflicting_explicit_units_are_preserved_as_unverified(raw, header):
    result = sectional_observation(raw, header)
    assert result["last_3f"] == result["average_1f"] == ""
    assert result["last_3f_status"] == result["average_1f_status"] == "UNVERIFIED"
    assert result["last_3f_missing_reason"] == "CONFLICTING_SECTIONAL_UNITS"
    assert result["sectional_header"] == header and result["sectional_raw"] == raw


def test_generic_header_with_explicit_average_label_can_establish_its_units():
    result = sectional_observation("平均1F 14.2", "上り", jump=True)
    assert result["average_1f"] == "14.2" and result["last_3f"] == ""


def test_straight_course_is_nonapplicable_even_when_normal_corner_header_exists():
    soup = result_page(course="芝・直", corner="")
    result = parse_result_history_semantics(soup, soup.select_one("tbody tr"))
    assert soup.find("th", string="通過順位")
    assert result["passing_order"] == ""
    assert result["passing_order_status"] == "NOT_APPLICABLE"
    assert result["passing_order_missing_reason"] == "STRAIGHT_COURSE_NO_CORNERS"


def test_empty_ordinary_course_corner_is_not_mislabeled_straight():
    soup = result_page(corner="")
    result = parse_result_history_semantics(soup, soup.select_one("tbody tr"))
    assert result["passing_order_status"] == "NOT_OBSERVED"


def test_corner_text_is_not_inferred_from_partial_numeric_garbage():
    soup = result_page(corner="不明3")
    result = parse_result_history_semantics(soup, soup.select_one("tbody tr"))
    assert result["passing_order"] == ""
    assert result["passing_order_status"] == "UNVERIFIED"


@pytest.mark.parametrize("jump", [False, True])
def test_nonfinisher_official_empty_last3f_is_not_imputed(jump):
    result = sectional_observation("", "上り", jump=jump, position="中止")
    assert result["last_3f"] == ""
    assert result["last_3f_status"] == "NOT_OBSERVED"
    assert result["last_3f_missing_reason"] == "NONFINISHER_SECTIONAL_NOT_OBSERVED"


@pytest.mark.parametrize("header,value,race", [
    ("平均1F", "14.0", "障害未勝利"),
    ("上り", "14.0", "障害未勝利"),
    ("上り", "", "障害未勝利"),
])
def test_horse_history_does_not_fill_unobserved_jump_last3f(header, value, race):
    html = f'''<table><tr><th>年月日</th><th>レース名</th><th>距離</th><th>着順</th><th>{header}</th></tr>
    <tr><td>2026/09/20</td><td>{race}</td><td>障2970</td><td>2</td><td>{value}</td></tr></table>'''
    row = JRAParser("https://jra.jp").parse_horse_last5(html, "r", "h", "A", "")[0]
    assert row["last_3f"] == ""
    assert row["last_3f_source"] != "fallback"
    if header == "平均1F":
        assert row["average_1f"] == "14.0"
        assert row["last_3f_status"] == "NOT_APPLICABLE"
    else:
        assert row["average_1f"] == ""
        assert row["last_3f_status"] == "UNVERIFIED"


def test_separate_sectional_columns_do_not_cross_assign_values():
    html = '''<table><tr><th>年月日</th><th>レース名</th><th>距離</th><th>着順</th>
    <th>上り3F</th><th>平均1F</th></tr><tr><td>2026/09/20</td><td>テスト</td>
    <td>芝1200</td><td>1</td><td>35.4</td><td>14.2</td></tr></table>'''
    row = JRAParser("https://jra.jp").parse_horse_last5(html, "r", "h", "A", "")[0]
    assert row["last_3f"] == "35.4" and row["last_3f_status"] == "OBSERVED"
    assert row["average_1f"] == "14.2" and row["average_1f_status"] == "OBSERVED"


def test_embedded_average_label_and_nonfinisher_remain_distinct_from_last3f():
    parser = JRAParser("https://jra.jp")
    html = '''<tr><td class="past"><div class="date_line"><div class="date">2026/09/20</div></div>
    <div class="race_line"><div class="name">障害未勝利</div></div>
    <div class="place_line"><div class="place">中止</div></div>
    <div class="info_line2"><span class="dist">障2970</span></div>
    <div class="info_line3"><div class="f3">平均1F 14.0</div></div></td></tr>'''
    row = parser._extract_embedded_history(BeautifulSoup(html, "html.parser"))[0]
    assert row["average_1f"] == "14.0" and row["last_3f"] == ""
    assert row["position"] == "中止"
    row = parser._extract_embedded_history(BeautifulSoup(html.replace("平均1F 14.0", ""), "html.parser"))[0]
    assert row["last_3f_status"] == "NOT_OBSERVED"
    assert row["last_3f_missing_reason"] == "NONFINISHER_SECTIONAL_NOT_OBSERVED"


def test_embedded_transfer_event_is_not_a_race_start():
    html = '''<tr><td class="past"><div class="date_line"><div class="date">2026/09/30</div></div>
    <div>JRAへ転入</div></td><td class="past"><div class="date_line"><div class="date">2026/09/20</div></div>
    <div class="race_line"><div class="name">テスト競走</div></div></td></tr>'''
    rows = JRAParser("https://jra.jp")._extract_embedded_history(BeautifulSoup(html, "html.parser"))
    assert len(rows) == 1
    assert rows[0]["date"] == "2026/09/20" and rows[0]["run_index"] == "1"
