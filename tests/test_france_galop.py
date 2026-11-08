"""France Galop collector: probing, artifact envelope and fail-closed
outcomes, all with fake fetchers (no network)."""
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_foreign_history import FRANCE, identity_source

from jra_scraper import france_galop
from jra_scraper.france_galop import (
    build_france_artifact, collect_france_history, extract_summary_text,
    identity_fetcher_from_map, is_france_history_row, load_identity_sources,
    parse_summary_header, probe_race, tracking_pdf_url, venue_for_row)
from jra_scraper.foreign_history import parse_foreign_history_artifact


PDF_BYTES = b'fake-france-tracking-pdf'
ARC_URL = 'https://www7.france-galop.com/Casaques/Tracking/20251005LON05_last_times_fr.pdf'


@pytest.fixture
def pdf_text(monkeypatch):
    """Make both the collector's extractor and the parser's re-extraction
    see the FRANCE fixture text, like tests/test_foreign_history.py does."""
    import pypdf
    page = SimpleNamespace(extract_text=Mock(return_value=FRANCE))
    reader = SimpleNamespace(pages=[page])
    monkeypatch.setattr(pypdf, 'PdfReader', lambda source: reader)
    return page


def arc_fetch(url):
    return PDF_BYTES if url == ARC_URL else None


def arc_row(**overrides):
    row = {'race_id': 'target', 'horse_id': 'official-jra-id',
           'horse_name': 'クロワデュノール', 'horse_number': '2', 'run_index': '3',
           'date': '2025年10月5日', 'race_name': '凱旋門賞', 'course': '仏',
           'distance': '2400', 'last_3f_status': 'NOT_OBSERVED'}
    row.update(overrides)
    return row


def good_identity(date_iso, race_name, horse_name):
    return identity_source(france=True)


def test_tracking_pdf_url_uses_verified_pattern():
    assert tracking_pdf_url('2025-10-05', 'LON', 5) == ARC_URL
    assert tracking_pdf_url('2025-09-07', 'LON', 2) == \
        'https://www7.france-galop.com/Casaques/Tracking/20250907LON02_last_times_fr.pdf'


def test_parse_summary_header_reads_venue_race_distance_and_french_date():
    assert parse_summary_header(FRANCE) == {
        'expected_course': 'PARISLONGCHAMP', 'race_number': 5,
        'race_title': "QATAR PRIX DE L'ARC DE TRIOMPHE",
        'distance': 2400, 'date_iso': '2025-10-05'}
    with pytest.raises(ValueError):
        parse_summary_header('no header here')


def test_venue_resolution_requires_explicit_marker_or_verified_bare_france_race():
    assert venue_for_row({'course': 'ロンシャン'}) == ('PARISLONGCHAMP', 'LON')
    assert venue_for_row({'course': 'パリロンシャン'}) == ('PARISLONGCHAMP', 'LON')
    assert venue_for_row({'course': 'シャンティイ'}) == ('CHANTILLY', 'CHA')
    assert venue_for_row({'course': 'ドーヴィル'}) == ('DEAUVILLE', 'DEA')
    assert venue_for_row({'course': 'クレールフォンテーヌ'}) == ('CLAIREFONTAINE', 'CLA')
    # Bare '仏' is ambiguous: only verified race names resolve.
    assert venue_for_row(arc_row()) == ('PARISLONGCHAMP', 'LON')
    assert venue_for_row(arc_row(race_name='フォワ賞')) == ('PARISLONGCHAMP', 'LON')
    assert venue_for_row(arc_row(race_name='不明な賞')) is None
    assert venue_for_row({'course': '京都', 'race_name': '京都大賞典'}) is None


def test_is_france_history_row_skips_observed_and_non_french_rows():
    assert is_france_history_row(arc_row())
    assert is_france_history_row(arc_row(course='ロンシャン'))
    assert not is_france_history_row(arc_row(last_3f_status='OBSERVED'))
    assert not is_france_history_row(arc_row(course='京都', race_name='京都大賞典'))
    assert not is_france_history_row(arc_row(race_name='不明な賞'))


def test_probe_matches_by_distance_and_runner(pdf_text):
    probe = probe_race(arc_fetch, '2025-10-05', 'LON', distance=2400,
                       official_horse_name='CROIX DU NORD')
    assert probe['race_number'] == 5 and probe['url'] == ARC_URL
    assert probe['pdf_bytes'] == PDF_BYTES and probe['text'] == FRANCE
    assert probe['header']['distance'] == 2400


def test_probe_rejects_distance_mismatch_and_absent_runner(pdf_text):
    assert probe_race(arc_fetch, '2025-10-05', 'LON', distance=2000,
                      official_horse_name='CROIX DU NORD') is None
    assert probe_race(arc_fetch, '2025-10-05', 'LON', distance=2400,
                      official_horse_name='OTHER HORSE') is None


def test_probe_continues_past_fetch_failures(pdf_text):
    def flaky(url):
        if 'LON01' in url:
            raise OSError('network down')
        return arc_fetch(url)
    probe = probe_race(flaky, '2025-10-05', 'LON', distance=2400,
                       official_horse_name='CROIX DU NORD')
    assert probe['race_number'] == 5
    assert probe_race(lambda url: None, '2025-10-05', 'LON', distance=2400,
                      official_horse_name='CROIX DU NORD') is None


def _probe():
    return {'race_number': 5, 'url': ARC_URL, 'pdf_bytes': PDF_BYTES,
            'text': FRANCE, 'header': parse_summary_header(FRANCE)}


def test_build_artifact_shape_and_sha_conventions(tmp_path, pdf_text):
    identity = identity_source(france=True)
    raw_identity = {'url': identity['url'], 'html': identity['html']}  # no sha yet
    artifact = build_france_artifact(
        probe=_probe(), store_dir=tmp_path, race_id='target',
        horse_id='official-jra-id', horse_name='クロワデュノール',
        official_horse_name='CROIX DU NORD', date_iso='2025-10-05',
        expected_course='PARISLONGCHAMP', expected_distance=2400,
        identity_source=raw_identity)
    assert artifact['kind'] == 'france_tracking_pdf' and artifact['url'] == ARC_URL
    assert artifact['html'] == FRANCE
    assert artifact['sha256'] == hashlib.sha256(FRANCE.encode()).hexdigest()
    assert artifact['pdf_sha256'] == hashlib.sha256(PDF_BYTES).hexdigest()
    assert artifact['pdf_page'] == 0 and artifact['date'] == '2025-10-05'
    assert artifact['race_number'] == 5 and artifact['expected_distance'] == 2400
    from pathlib import Path
    pdf_path = Path(artifact['pdf_path'])
    assert pdf_path.is_absolute() and pdf_path.read_bytes() == PDF_BYTES
    assert pdf_path.name == 'france_tracking_2025-10-05_LON05.pdf'
    assert artifact['identity_source']['sha256'] == \
        hashlib.sha256(identity['html'].encode()).hexdigest()
    # The built envelope is exactly what the existing parser consumes.
    row = parse_foreign_history_artifact(artifact['html'], artifact)[0]
    assert row['last_3f'] == '37.61' and row['time'] == '150.81'


def test_collect_success_observed_with_time(tmp_path, pdf_text):
    result = collect_france_history(
        arc_row(), official_horse_name='CROIX DU NORD', fetch_bytes=arc_fetch,
        fetch_identity=good_identity, store_dir=tmp_path)
    attempt = result['attempt']
    assert attempt['outcome'] == 'observed' and attempt['route'] == 'france_galop_tracking'
    assert attempt['fields'] == ['last_3f', 'time']
    assert attempt['attempted_fields'] == ['last_3f', 'time']
    assert attempt['race_id'] == 'target' and attempt['horse_number'] == '2'
    assert attempt['run_index'] == '3' and attempt['date'] == '2025年10月5日'
    assert result['observation']['last_3f'] == '37.61'
    assert result['observation']['time'] == '150.81'
    assert result['artifact'] is not None
    assert attempt['artifact_sha256'] == result['artifact']['pdf_sha256']


def test_collect_no_venue_is_jurisdiction_not_france(tmp_path):
    result = collect_france_history(
        arc_row(course='京都', race_name='京都大賞典'),
        official_horse_name='CROIX DU NORD', fetch_bytes=arc_fetch,
        fetch_identity=good_identity, store_dir=tmp_path)
    assert result['artifact'] is None and result['observation'] is None
    assert result['attempt']['outcome'] == 'jurisdiction_not_france'


def test_collect_probe_miss_lists_probed_urls(tmp_path, pdf_text):
    result = collect_france_history(
        arc_row(), official_horse_name='CROIX DU NORD',
        fetch_bytes=lambda url: None, fetch_identity=good_identity,
        store_dir=tmp_path)
    assert result['artifact'] is None and result['observation'] is None
    assert result['attempt']['outcome'] == 'tracking_pdf_unavailable'
    assert len(result['attempt']['probed_urls']) == 9
    assert result['attempt']['probed_urls'][4] == ARC_URL


def test_collect_identity_missing_builds_no_artifact(tmp_path, pdf_text):
    result = collect_france_history(
        arc_row(), official_horse_name='CROIX DU NORD', fetch_bytes=arc_fetch,
        fetch_identity=lambda *args: None, store_dir=tmp_path)
    assert result['artifact'] is None and result['observation'] is None
    assert result['attempt']['outcome'] == 'identity_source_unavailable'
    assert result['attempt']['artifact_sha256'] is None
    assert list(tmp_path.iterdir()) == []  # PDF was probed, never stored


def test_collect_parse_failure_keeps_stored_artifact_and_error(tmp_path, pdf_text):
    def bad_identity(date_iso, race_name, horse_name):
        return {'url': 'https://www.jra.go.jp/JRADB/result', 'html': '<p>not a result page</p>'}
    result = collect_france_history(
        arc_row(), official_horse_name='CROIX DU NORD', fetch_bytes=arc_fetch,
        fetch_identity=bad_identity, store_dir=tmp_path)
    assert result['observation'] is None and result['artifact'] is not None
    assert result['attempt']['outcome'] == 'parse_failed'
    assert result['attempt']['reason']  # carries the parser's error message
    assert result['attempt']['artifact_sha256'] == result['artifact']['pdf_sha256']


def test_identity_map_loader_and_fetcher(tmp_path):
    assert load_identity_sources(tmp_path / 'missing.json') == {}
    path = tmp_path / 'identity.json'
    mapping = {'2025-10-05|凱旋門賞': {'url': 'https://www.jra.go.jp/JRADB/x'}}
    path.write_text(json.dumps(mapping), encoding='utf-8')
    assert load_identity_sources(path) == mapping

    fetch = identity_fetcher_from_map(mapping, lambda url: '<html>ok</html>')
    assert fetch('2025-10-05', '凱旋門賞', 'ビザンチンドリーム') == \
        {'url': 'https://www.jra.go.jp/JRADB/x', 'html': '<html>ok</html>'}
    assert fetch('2025-09-07', 'フォワ賞', 'ビザンチンドリーム') is None  # unmapped
    assert identity_fetcher_from_map(mapping, lambda url: None)(
        '2025-10-05', '凱旋門賞', 'ビザンチンドリーム') is None  # unfetchable

    def boom(url):
        raise OSError('down')
    assert identity_fetcher_from_map(mapping, boom)(
        '2025-10-05', '凱旋門賞', 'ビザンチンドリーム') is None


def test_extract_summary_text_rejects_a_pdf_with_no_pages(monkeypatch):
    import pypdf
    monkeypatch.setattr(pypdf, 'PdfReader',
                        lambda source: SimpleNamespace(pages=[]))
    with pytest.raises(ValueError, match='no pages'):
        extract_summary_text(PDF_BYTES)


def test_parse_summary_header_requires_race_line_and_french_date():
    with pytest.raises(ValueError, match='race line missing'):
        parse_summary_header('PARISLONGCHAMP\n5 octobre 2025')
    with pytest.raises(ValueError, match='date missing'):
        parse_summary_header('PARISLONGCHAMP\nC5 - PRIX TEST - 2400m')


def test_probe_skips_bytes_that_are_not_a_parseable_pdf():
    # Fetched bytes that fail summary extraction are skipped like a miss,
    # never treated as a candidate.
    assert probe_race(lambda url: b'this is not a pdf', '2025-10-05', 'LON',
                      distance=2400, official_horse_name='CROIX DU NORD') is None


def test_build_artifact_requires_a_determinable_venue_code(tmp_path):
    probe = {'race_number': 5, 'url': 'https://example.invalid/tracking.pdf',
             'pdf_bytes': PDF_BYTES, 'text': FRANCE}
    with pytest.raises(ValueError, match='venue code cannot be determined'):
        build_france_artifact(
            probe=probe, store_dir=tmp_path, race_id='target',
            horse_id='official-jra-id', horse_name='クロワデュノール',
            official_horse_name='CROIX DU NORD', date_iso='2025-10-05',
            expected_course='UNVERIFIED COURSE', expected_distance=2400,
            identity_source=identity_source(france=True))
    assert list(tmp_path.iterdir()) == []  # nothing stored on failure


def test_collect_accepts_an_iso_row_date(tmp_path, pdf_text):
    result = collect_france_history(
        arc_row(date='2025-10-05'), official_horse_name='CROIX DU NORD',
        fetch_bytes=arc_fetch, fetch_identity=good_identity, store_dir=tmp_path)
    assert result['attempt']['outcome'] == 'observed'
    assert result['attempt']['date'] == '2025-10-05'
    assert result['observation']['last_3f'] == '37.61'


def test_collect_reports_unreadable_row_date_or_distance(tmp_path):
    def boom_identity(*args):
        raise AssertionError('identity must not be fetched for an unreadable row')

    for overrides in ({'date': '不明'}, {'distance': ''}):
        result = collect_france_history(
            arc_row(**overrides), official_horse_name='CROIX DU NORD',
            fetch_bytes=arc_fetch, fetch_identity=boom_identity, store_dir=tmp_path)
        assert result['artifact'] is None and result['observation'] is None
        assert result['attempt']['outcome'] == 'row_date_or_distance_unavailable'
        assert 'cannot be read exactly' in result['attempt']['reason']


def test_collect_identity_fetch_failure_is_reported_not_fatal(tmp_path, pdf_text):
    def down(date_iso, race_name, horse_name):
        raise RuntimeError('jradb unreachable')

    result = collect_france_history(
        arc_row(), official_horse_name='CROIX DU NORD', fetch_bytes=arc_fetch,
        fetch_identity=down, store_dir=tmp_path)
    assert result['artifact'] is None and result['observation'] is None
    assert result['attempt']['outcome'] == 'identity_source_unavailable'
    assert 'Identity fetch failed: jradb unreachable' in result['attempt']['reason']
    assert list(tmp_path.iterdir()) == []  # probed only, never stored


def test_collect_reparse_count_mismatch_is_parse_failed(tmp_path, pdf_text, monkeypatch):
    monkeypatch.setattr(france_galop, 'parse_foreign_history_artifact',
                        lambda html, artifact: [])
    result = collect_france_history(
        arc_row(), official_horse_name='CROIX DU NORD', fetch_bytes=arc_fetch,
        fetch_identity=good_identity, store_dir=tmp_path)
    assert result['observation'] is None and result['artifact'] is not None
    assert result['attempt']['outcome'] == 'parse_failed'
    assert 'reparsed to 0 observations, expected 1' in result['attempt']['reason']


def test_identity_map_loader_rejects_a_non_object_document(tmp_path):
    path = tmp_path / 'identity.json'
    path.write_text('[1, 2, 3]', encoding='utf-8')
    with pytest.raises(ValueError, match='must be a JSON object'):
        load_identity_sources(path)


def test_identity_fetcher_entry_without_url_is_unavailable():
    fetch_text = Mock(side_effect=AssertionError('must not fetch without a URL'))
    for entry in ({'url': ''}, {}):
        fetch = identity_fetcher_from_map({'2025-10-05|凱旋門賞': entry}, fetch_text)
        assert fetch('2025-10-05', '凱旋門賞', 'ビザンチンドリーム') is None
    fetch_text.assert_not_called()


def test_module_has_no_hard_network_dependency():
    # Fetchers are injectable callables; the module itself imports no HTTP
    # client for its logic paths.
    import inspect
    source = inspect.getsource(france_galop)
    assert 'requests' not in source and 'urllib' not in source
