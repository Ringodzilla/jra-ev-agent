"""NAR past runs are recovered from the official keiba.go.jp result table."""
from jra_scraper.nar_history import (
    collect_nar_history,
    find_race_no,
    is_nar_history_row,
)

MENU = """<html><body>
<p>2026年10月6日（火） 大井競馬 当日メニュー</p>
<table>
<tr><td>10R</td><td>ダイヤモンドリリー特別</td></tr>
<tr><td>11R</td><td><a>レディスプレリュードJpnII</a></td></tr>
<tr><td>12R</td><td>ペガサス賞</td></tr>
</table></body></html>"""

RESULT = """<p>2026年10月6日 大井 天候：晴</p><table>
<tr><th>上がり 3F</th><th>コーナー 通過順</th></tr>
<tr><td class="a">5</td><td class="horseName"><a>アピーリングルック</a></td>
<td class="g">55.0</td><td class="k">1:53.2</td><td class="m">39.0</td>
<td class="n">5-5-4-5</td><td class="o">5</td><td class="p">20.9</td></tr></table>"""

ROW = dict(race_id="20261114_東京_11", horse_id="h1", horse_number="3",
           horse_name="アピーリングルック", run_index="1", date="2026年10月6日",
           course="大井", race_name="レディスプレ", position="5",
           last_3f="36.0", last_3f_source="fallback", passing_order="")


def fetch_from(pages):
    def fetch(url):
        for marker, html in pages.items():
            if marker in url:
                return html.encode("cp932")
        return None
    return fetch


def test_predicate_requires_nar_track_and_unobserved_sectionals():
    assert is_nar_history_row(ROW)
    assert is_nar_history_row(dict(ROW, date="2026-10-06"))
    assert not is_nar_history_row(dict(ROW, course="東京"))
    assert not is_nar_history_row(dict(ROW, course="園田"))  # code unverified
    assert not is_nar_history_row(dict(ROW, last_3f="39.0", last_3f_source="observed",
                                       passing_order="5-5-4-5"))
    assert is_nar_history_row(dict(ROW, last_3f="39.0", last_3f_source="observed"))
    assert not is_nar_history_row(dict(ROW, position="中止"))


def test_find_race_no_verifies_track_and_name():
    assert find_race_no(MENU, "レディスプレリュード", "大井") == 11
    assert find_race_no(MENU, "第23回レディスプレリュード", "大井") == 11
    assert find_race_no(MENU, "レディスプレリュード", "川崎") is None
    assert find_race_no(MENU, "東京ダービー", "大井") is None


def test_collect_recovers_observed_sectionals():
    result = collect_nar_history(
        ROW, fetch_bytes=fetch_from({"RaceList": MENU, "RaceMarkTable": RESULT}))
    assert result["attempt"]["outcome"] == "observed"
    observation = result["observation"]
    assert observation["last_3f"] == "39.0"
    assert observation["last_3f_status"] == "OBSERVED"
    assert observation["passing_order"] == "5-5-4-5"
    assert observation["popularity"] == "5" and observation["odds"] == "20.9"
    assert observation["weather"] == "晴"
    artifact = result["artifact"]
    assert artifact["kind"] == "nar_history_result"
    assert "k_babaCode=20" in artifact["url"] and "k_raceNo=11" in artifact["url"]


def test_collect_records_race_not_found():
    result = collect_nar_history(
        dict(ROW, race_name="存在しないレース"),
        fetch_bytes=fetch_from({"RaceList": MENU, "RaceMarkTable": RESULT}))
    assert result["attempt"]["outcome"] == "race_not_found"
    assert result["artifact"] is None and result["observation"] is None


def test_collect_records_fetch_failure():
    result = collect_nar_history(ROW, fetch_bytes=lambda url: None)
    assert result["attempt"]["outcome"] == "fetch_failed"


def test_collect_route_unresolved_for_unverified_track():
    result = collect_nar_history(dict(ROW, course="園田"), fetch_bytes=lambda url: None)
    assert result["attempt"]["outcome"] == "route_unresolved"
    assert result["artifact"] is None


def test_collect_result_page_fetch_failure():
    result = collect_nar_history(
        ROW, fetch_bytes=fetch_from({"RaceList": MENU}))
    assert result["attempt"]["outcome"] == "fetch_failed"
    assert "RaceMarkTable" in result["attempt"]["url"]


def test_collect_records_parse_failure():
    result = collect_nar_history(
        ROW, fetch_bytes=fetch_from({"RaceList": MENU, "RaceMarkTable": "<p>no table</p>"}))
    assert result["attempt"]["outcome"] == "parse_failed"
    assert result["observation"] is None


def test_find_race_no_skips_header_and_empty_rows():
    menu = """<p>2026年10月6日（火） 大井競馬 当日メニュー</p><table>
    <tr><th>競走</th><th>競走名</th></tr>
    <tr></tr>
    <tr><td>11R</td><td><a>レディスプレリュードJpnII</a></td></tr>
    </table>"""
    assert find_race_no(menu, "レディスプレ", "大井") == 11


def test_find_race_no_edge_cases():
    assert find_race_no(MENU, "", "大井") is None
    assert find_race_no("<table><tr></tr></table>", "x", "大井") is None
    ambiguous = MENU.replace("ペガサス賞", "レディスプレ特別")
    assert find_race_no(ambiguous, "ジャパンDC", "大井") is None


def test_predicate_rejects_unparseable_date_and_bad_position():
    assert not is_nar_history_row(dict(ROW, date="2026/10/06"))
    assert not is_nar_history_row(dict(ROW, position="5着"))


def test_decode_page_utf8_branch():
    from jra_scraper.nar_history import decode_page
    html = '<meta charset="utf-8"><p>2026年10月6日 大井 天候：晴</p>'
    assert "天候：晴" in decode_page(html.encode("utf-8"))


def test_menu_is_fetched_once_per_card():
    calls = []

    def fetch(url):
        calls.append(url)
        return (MENU if "RaceList" in url else RESULT).encode("cp932")

    cache = {}
    collect_nar_history(ROW, fetch_bytes=fetch, menu_cache=cache)
    collect_nar_history(dict(ROW, horse_id="h2"), fetch_bytes=fetch, menu_cache=cache)
    assert sum("RaceList" in url for url in calls) == 1


def test_truncated_profile_names_still_resolve():
    assert find_race_no(MENU, "レディスプレ", "大井") == 11
    menu = MENU.replace("レディスプレリュードJpnII", "ジャパンダートクラシック")
    assert find_race_no(menu, "ジャパンDC", "大井") == 11
