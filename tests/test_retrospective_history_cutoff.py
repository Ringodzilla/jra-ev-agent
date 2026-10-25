from pathlib import Path

import pytest

from jra_scraper.parser import JRAParser


def test_cutoff_is_applied_before_five_start_cap():
    html = Path('tests/fixtures/horse_history.html').read_text()
    parser = JRAParser('https://jra.jp')
    rows = parser.parse_horse_last5(html, 'R', 'H', 'A', '', before_date='2026-03-01')
    assert len(rows) == 5
    assert rows[0]['date'] == '2026/02/01'
    assert rows[-1]['date'] == '2025/09/12'
    assert [row['run_index'] for row in rows] == ['1', '2', '3', '4', '5']
    assert parser.parse_horse_last5(html, 'R', 'H', 'A', '')[0]['date'] == '2026/03/01'


def test_cutoff_rejects_unknown_or_invalid_dates_instead_of_inventing_them():
    html = Path('tests/fixtures/horse_history.html').read_text()
    parser = JRAParser('https://jra.jp')
    with pytest.raises(ValueError, match='Cannot verify'):
        parser.parse_horse_last5(html.replace('2026/03/01', 'unknown'), 'R', 'H', 'A', '', before_date='2026-03-01')
    with pytest.raises(ValueError):
        parser.parse_horse_last5(html.replace('2026/03/01', '2026/13/01'), 'R', 'H', 'A', '', before_date='2026-03-01')
    with pytest.raises(ValueError):
        parser.parse_horse_last5(html, 'R', 'H', 'A', '', before_date='unknown')


def test_historical_result_fields_are_observed_and_unique():
    from src.data_integrity import _history_result_observations
    html = '''<div class="cell date">2026年10月11日（日曜）4回京都4日</div>
    <ul><li class="weather"><span class="txt">晴</span></li></ul>
    <table><tr><td class="horse"><a>A</a></td><td class="place">7</td>
    <td class="f_time">39.5</td><td class="time">1:27.0</td>
    <td class="corner"><li>5</li><li>3</li></td></tr></table>'''
    data = _history_result_observations(html, {'race_id': 'R', 'horse_id': 'H', 'horse_name': 'A'})
    assert data[0]['last_3f'] == '39.5'
    assert data[0]['passing_order'] == '5-3'
    assert data[0]['weather'] == '晴'
    assert data[0]['weight'] == ''
    assert data[0]['date'] == '2026-10-11'
    assert _history_result_observations(html.replace('晴', ''), {'horse_name': 'A'})[0]['weather'] == ''
    with pytest.raises(ValueError, match='unique'):
        _history_result_observations(html, {'horse_name': 'B'})
    with pytest.raises(ValueError, match='date is unverified'):
        _history_result_observations(html.replace('2026年10月11日', 'unknown'), {'horse_name': 'A'})


def test_source_audit_accepts_recovered_observation_from_historical_result():
    import hashlib
    from src.data_integrity import verify_source_artifacts
    from jra_scraper.validation import _normalize_row
    history = Path('tests/fixtures/horse_history.html').read_text().replace('<th>上り</th>', '<th>不明列</th>').replace('5-5-4-2', '')
    parser = JRAParser('https://jra.jp')
    row = _normalize_row(parser.parse_horse_last5(history, 'R', 'H', 'A', '')[0])
    row.update(target_race_date='2026-10-24', last_3f='34.2', last_3f_source='official_historical_result', passing_order='5-5-4-2')
    result_html = '''<div class="cell date">2026年3月1日（日曜）2回中山2日</div>
    <table><tr><td class="horse"><a>A</a></td><td class="f_time">34.2</td>
    <td class="corner"><li>5</li><li>5</li><li>4</li><li>2</li></td></tr></table>'''
    artifacts = [dict(kind=kind, html=html, sha256=hashlib.sha256(html.encode()).hexdigest(), race_id='R', horse_id='H', horse_name='A')
                 for kind, html in [('horse_history', history), ('history_result', result_html)]]
    audit = verify_source_artifacts({'rows': [row], 'source_audit': {'artifacts': artifacts}})
    assert audit['status'] == 'PASS'
    assert audit['detected_imputations'] == []


def test_historical_odds_require_unique_horse_date_and_positive_observation():
    from src.data_integrity import _history_odds_observations
    html = '''<div class="cell date">2025年8月2日（土曜） 1回札幌3日</div>
    <table><tr><td class="horse">A</td><td class="odds_tan">1,234.5</td></tr></table>'''
    artifact = dict(race_id='R', horse_id='H', horse_name='A')
    assert _history_odds_observations(html, artifact)[0] == dict(
        race_id='R', horse_id='H', horse_name='A', date='2025-08-02', odds='1234.5')
    for invalid in [html.replace('A', 'B'), html.replace('2025年8月2日', 'unknown'),
                    html.replace('1,234.5', '---'), html.replace('odds_tan', 'missing'),
                    html.replace('8月2日', '13月2日')]:
        with pytest.raises(ValueError):
            _history_odds_observations(invalid, artifact)


def test_source_audit_accepts_observed_historical_odds():
    import hashlib
    from src.data_integrity import verify_source_artifacts
    from jra_scraper.validation import _normalize_row
    html = Path('tests/fixtures/horse_history.html').read_text()
    row = _normalize_row(JRAParser('https://jra.jp').parse_horse_last5(html, 'R', 'H', 'A', '')[0])
    row.update(target_race_date='2026-10-24', odds='4.0')
    odds_html = '''<div class="cell date">2026年3月1日（日曜）</div>
    <table><tr><td class="horse">A</td><td class="odds_tan">4.0</td></tr></table>'''
    artifacts = [dict(kind=kind, html=source, sha256=hashlib.sha256(source.encode()).hexdigest(),
                      race_id='R', horse_id='H', horse_name='A')
                 for kind, source in [('horse_history', html), ('history_odds', odds_html)]]
    audit = verify_source_artifacts({'rows': [row], 'source_audit': {'artifacts': artifacts}})
    assert audit['status'] == 'PASS'


@pytest.mark.parametrize('event', ['JRAへ転入', 'JRAへ転出', 'JRAより転出'])
@pytest.mark.parametrize('insert_before', ['2026/03/01', '2025/12/05'])
def test_transfer_events_are_preserved_separately_and_do_not_consume_starts(event, insert_before):
    from bs4 import BeautifulSoup
    html = Path('tests/fixtures/horse_history.html').read_text()
    soup = BeautifulSoup(html, 'html.parser')
    target = next(tr for tr in soup.select('tr') if insert_before in tr.get_text())
    fragment = BeautifulSoup(f'<tr><td>2026/01/01</td><td>{event}</td></tr>', 'html.parser')
    target.insert_before(fragment.tr)
    issues = []
    rows = JRAParser('https://jra.jp').parse_horse_last5(
        str(soup), 'R', 'H', 'A', '', before_date='2026-03-01', issue_sink=issues)
    assert len(rows) == 5
    assert rows[-1]['date'] == '2025/09/12'
    saved = [issue for issue in issues if issue.code == 'non_race_transfer_event']
    assert len(saved) == 1
    assert event in saved[0].context['raw_text']


def test_explicit_empty_race_history_does_not_parse_other_summary_tables():
    html = '''<table><caption>平地レース合計</caption><tr><th>日付</th><th>距離</th>
    <th>着順</th></tr><tr><td>unknown</td><td>1400</td><td>1</td></tr></table>
    <table><caption class="h3"><div class="main">出走レース</div></caption><tbody>
    <tr><td><div class="caution no_data">該当するデータがありません。</div></td></tr></tbody></table>'''
    issues = []
    rows = JRAParser('https://jra.jp').parse_horse_last5(
        html, 'R', 'H', 'A', '', before_date='2026-10-24', issue_sink=issues)
    assert rows == []
    assert issues[0].code == 'history_no_data_reported'
    assert '該当するデータがありません。' in issues[0].context['raw_text']
