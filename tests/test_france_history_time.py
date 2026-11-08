"""France tracking total-time emission (Issue #39-A, P4 France part).

``time`` is values[-2] of the tracking row, emitted only when the seven
cumulative values are strictly increasing AND finish - T3 (the 600m-to-go
cumulative, values[-5]) reproduces the declared last-600m aggregate within
0.05s. Guard failures omit ``time`` silently (fail closed, last_3f still
parses); a row with other than exactly 8 clock values keeps the parser's
pre-existing behaviour -- it raises ValueError for the changed schema --
which is pinned here rather than silently changed.
"""
import pytest

from test_foreign_history import FRANCE, identity_source

from jra_scraper.foreign_history import parse_foreign_history_artifact


@pytest.fixture
def france_source(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    from unittest.mock import Mock
    import pypdf
    pdf = tmp_path / 'source.pdf'
    pdf.write_bytes(b'unit-test-pdf-binary')
    page = SimpleNamespace(extract_text=Mock(return_value=FRANCE))
    reader = SimpleNamespace(pages=[page])
    monkeypatch.setattr(pypdf, 'PdfReader', lambda path: reader)
    a = {'kind': 'france_tracking_pdf', 'url': 'https://www7.france-galop.com/Tracking/source.pdf',
         'pdf_path': str(pdf), 'pdf_sha256': hashlib.sha256(pdf.read_bytes()).hexdigest(), 'pdf_page': 0,
         'date': '2025-10-05', 'race_id': 'target', 'horse_id': 'official-jra-id',
         'horse_name': 'クロワデュノール', 'official_horse_name': 'CROIX DU NORD',
         'expected_course': 'PARISLONGCHAMP', 'race_number': 5, 'expected_distance': 2400,
         'identity_source': identity_source(france=True)}
    return a, page


def _parse(france_source, text):
    a, page = france_source
    page.extract_text.return_value = text
    return parse_foreign_history_artifact(text, a)[0]


def test_france_emits_total_time_when_row_is_consistent(france_source):
    # Fixture row: finish cumulative 02:30.81, T3 01:53.19, declared last
    # 600m 00:37.61; 150.81 - 113.19 = 37.62, within 0.05 of 37.61.
    row = _parse(france_source, FRANCE)
    assert row['time'] == '150.81'
    assert row['time_source'] == 'official_france_galop_tracking_total'
    assert row['last_3f'] == '37.61' and row['last_3f_status'] == 'OBSERVED'


def test_france_omits_time_when_cumulative_not_strictly_increasing(france_source):
    # Second-to-last cumulative collapses onto the previous one; the
    # last-600 cross-check (which uses values[-5]) is unaffected, so only
    # the monotonic guard can be what withholds the time.
    changed = FRANCE.replace('02:17.68', '02:05.40')
    row = _parse(france_source, changed)
    assert 'time' not in row and 'time_source' not in row
    assert row['last_3f'] == '37.61'


def test_france_omits_time_when_last600_cross_check_fails(france_source):
    # Cumulative values stay strictly increasing; the declared aggregate no
    # longer matches finish - T3, so only the cross-check withholds time.
    changed = FRANCE.replace('00:37.61', '00:30.00')
    row = _parse(france_source, changed)
    assert 'time' not in row and 'time_source' not in row
    assert row['last_3f'] == '30.00'


@pytest.mark.parametrize('changed', [
    FRANCE.replace(' 00:12.20', ''),          # 7 clock values
    FRANCE.replace('00:37.61', '00:37.61 00:01.00'),  # 9 clock values
])
def test_france_wrong_value_count_keeps_existing_schema_error(france_source, changed):
    # Pre-existing behaviour pinned: exactly-8 is a schema check in the
    # parser, so 7 or 9 values raise instead of yielding a partial row.
    a, page = france_source
    page.extract_text.return_value = changed
    with pytest.raises(ValueError, match='schema changed'):
        parse_foreign_history_artifact(changed, a)
