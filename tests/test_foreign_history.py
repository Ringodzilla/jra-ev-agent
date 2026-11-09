"""Foreign result adapters preserve observed units and reject incompatible pages."""
from copy import deepcopy

import pytest

from jra_scraper.foreign_history import parse_foreign_history_artifact


NAR = '''<p>2026年4月8日 川崎 天候：晴</p><table>
<tr><th>上がり 3F</th><th>コーナー 通過順</th></tr>
<tr><td class="a">11</td><td class="horseName"><a>ホウオウビスケッツ</a></td>
<td class="g">57.0</td><td class="k">2:18.7</td><td class="m">41.9</td>
<td class="n">3-3-7-8</td><td class="o">5</td><td class="p">10.5</td></tr></table>'''
HKJC = '''<p>Meeting Date:26/04/2026, Sha Tin</p><div id="Race9">
<div class="Race"><p class="f_fl"><span>Group One - 2000M - Turf - A - Good To Firm</span><span>THE FWD QEII CUP</span></p></div>
<table class="race_table"><tbody><tr><td>2</td><td>1</td><td><a>MASQUERADE BALL (L919)</a></td>
<td><p class="f_clear"><span class="f_fl">7</span></p><p>26.93</p></td>
<td><p class="f_clear"><span class="f_fl">8</span></p><p>25.03</p></td>
<td><p class="f_clear"><span class="f_fl">8</span></p><p>24.25</p></td>
<td><p class="f_clear"><span class="f_fl">8</span></p><p class="sectional_200">22.92
<span class="color_blue2"><span>11.63</span><span>11.29</span></span></p></td>
<td><p class="f_clear"><span class="f_fl">2</span></p><p class="sectional_200">21.67
<span class="color_blue2"><span>10.72</span><span>10.95</span></span></p></td>
<td><img/></td><td>2:00.80</td></tr></tbody></table></div>'''
JRA = '''<div class="cell date">2026年4月26日 シャティン競馬場</div><table><tr>
<td class="place">2</td><td class="horse">マスカレードボール<span>(JPN)</span></td>
<td class="weight">57.0</td><td class="time">1</td><td class="pop">2</td></tr></table>'''


def artifact(kind):
    hosts = {'nar_history_result': 'www.keiba.go.jp', 'hkjc_history_sectionals': 'racing.hkjc.com',
             'overseas_jra_result': 'jra.jp'}
    result = {'kind': kind, 'url': 'https://' + hosts[kind] + '/result', 'race_id': 'target',
            'horse_id': 'official-jra-id', 'horse_name': 'ホウオウビスケッツ' if kind == 'nar_history_result' else 'マスカレードボール',
            'date': '2026-04-08' if kind == 'nar_history_result' else '2026-04-26',
            'official_horse_name': 'MASQUERADE BALL', 'race_number': 9, 'expected_distance': 2000}
    if kind == 'hkjc_history_sectionals':
        result['identity_source'] = identity_source()
    return result



def identity_source(*, france=False):
    import hashlib
    if france:
        text = '<div class="cell date">2025年10月5日 パリロンシャン競馬場</div><div class="cell course">2,400メートル</div><h2>凱旋門賞</h2>'
        name, number, place = 'クロワデュノール', '15', '14'
    else:
        text = '<div class="cell date">2026年4月26日 シャティン競馬場</div><div class="cell course">2,000メートル</div><h2>クイーンエリザベス2世カップ</h2>'
        name, number, place = 'マスカレードボール', '1', '2'
    text += f'<table><tr><td class="place">{place}</td><td class="num"><span class="cell num">{number}</span></td><td class="horse"><a href="/JRADB/accessU.html?CNAME=official-jra-id">{name}</a></td></tr></table>'
    return {'html': text, 'sha256': hashlib.sha256(text.encode()).hexdigest(), 'url': 'https://www.jra.go.jp/JRADB/result'}

def test_nar_uses_official_sectional_corner_and_market_columns():
    row = parse_foreign_history_artifact(NAR, artifact('nar_history_result'))[0]
    assert row['last_3f'] == '41.9' and row['last_3f_status'] == 'OBSERVED'
    assert row['passing_order'] == '3-3-7-8'
    assert row['weather'] == '晴' and row['odds'] == '10.5' and row['popularity'] == '5'
    assert row['date'] == '2026-04-08' and row['horse_id'] == 'official-jra-id'
    assert 'weather' not in parse_foreign_history_artifact(NAR.replace('天候：晴', ''), artifact('nar_history_result'))[0]


def test_hkjc_exact_individual_last600m_does_not_relabel_400m_positions_as_corners():
    row = parse_foreign_history_artifact(HKJC, artifact('hkjc_history_sectionals'))[0]
    assert row['last_3f'] == '32.96'
    assert row['running_positions_at_400m_boundaries'] == ['7', '8', '8', '8', '2']
    assert 'passing_order' not in row
    a = artifact('hkjc_history_sectionals')
    del a['expected_distance']
    assert parse_foreign_history_artifact(HKJC, a)[0]['last_3f'] == '32.96'


def test_jra_overseas_does_not_treat_margin_as_elapsed_seconds():
    row = parse_foreign_history_artifact(JRA, artifact('overseas_jra_result'))[0]
    assert row['position'] == '2' and row['popularity'] == '2'
    assert 'time' not in row and 'last_3f' not in row


@pytest.mark.parametrize('kind,html', [('nar_history_result', NAR), ('hkjc_history_sectionals', HKJC),
                                     ('overseas_jra_result', JRA)])
def test_official_identity_date_and_host_are_bound(kind, html):
    a = artifact(kind)
    for changes in ({'url': 'https://evil.example/result'}, {'date': '2026-04-27'},
                    {'horse_name': '別馬', 'official_horse_name': 'OTHER HORSE'}):
        changed = deepcopy(a)
        changed.update(changes)
        with pytest.raises(ValueError):
            parse_foreign_history_artifact(html, changed)
    with pytest.raises(ValueError):
        parse_foreign_history_artifact(html, dict(a, kind='unknown'))


@pytest.mark.parametrize('original,replacement', [
    ('2026年4月8日', 'unknown date'), ('上がり 3F', '平均1F'),
    ('41.9', '未測定'), ('2:18.7', 'unknown'), ('3-3-7-8', '?'),
])
def test_nar_rejects_missing_unit_date_or_observation(original, replacement):
    with pytest.raises(ValueError):
        parse_foreign_history_artifact(NAR.replace(original, replacement), artifact('nar_history_result'))


@pytest.mark.parametrize('original,replacement', [
    ('Meeting Date:26/04/2026', 'Unknown meeting date'), ('2000M', '2100M'),
    ('2000M', '400M'), ('2000M', 'NO-DISTANCE'), ('2000M', '2400M'),
    ('11.63', '11.64'), ('<span>11.29</span>', ''),
    ('<td><img/></td>', ''), ('class="sectional_200"', 'class="unknown"'),
])
def test_hkjc_rejects_unknown_interval_geometry_and_inconsistent_components(original, replacement):
    a = artifact('hkjc_history_sectionals')
    del a['expected_distance']
    with pytest.raises(ValueError):
        parse_foreign_history_artifact(HKJC.replace(original, replacement), a)


def test_hkjc_rejects_conflicting_expected_distance():
    with pytest.raises(ValueError):
        parse_foreign_history_artifact(HKJC, dict(artifact('hkjc_history_sectionals'), expected_distance=1600))


FRANCE = '''Statistiques Tracking
PARISLONGCHAMP
C5 - QATAR PRIX DE L'ARC DE TRIOMPHE - 2400m
dimanche 5 octobre 2025 - 00:00
Temps Derniers Parcouru
officiel 600m vs. 1er
15 CROIX DU NORD 14 65,16 00:12.20 01:27.93 1 01:40.70 1 01:53.19 1 02:05.40 2 02:17.68 8 02:30.81 00:37.61 -0,35
'''


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
    return a, page, reader


def test_france_uses_official_aggregate_and_reproduces_extraction_from_binary(france_source):
    a, page, _ = france_source
    row = parse_foreign_history_artifact(FRANCE, a)[0]
    assert row['last_3f'] == '37.61' and row['last_3f_status'] == 'OBSERVED'
    assert row['sectional_transformation'] == 'official_Derniers_600m_aggregate'
    assert 'passing_order' not in row
    page.extract_text.assert_called_once_with(extraction_mode='layout')


def test_france_rejects_text_or_pdf_tampering(france_source):
    a, _, _ = france_source
    with pytest.raises(ValueError, match='PDF bytes'):
        parse_foreign_history_artifact(FRANCE, dict(a, pdf_sha256='invalid'))
    with pytest.raises(ValueError, match='extracted text'):
        parse_foreign_history_artifact(FRANCE.replace('37.61', '36.00'), a)


@pytest.mark.parametrize('original,replacement', [
    ('5 octobre 2025', 'unknown date'), ('PARISLONGCHAMP', 'OTHERCOURSE'),
    ('C5 - ', 'C6 - '), ('2400m', '2000m'), ('C5 - ', 'unknown race '),
    ('Temps Derniers Parcouru', 'Temps Unverified Parcouru'), ('officiel 600m', 'officiel 200m'),
    ('CROIX DU NORD', 'OTHER HORSE'), ('00:37.61', ''),
])
def test_france_rejects_unbound_race_units_and_missing_runner(france_source, original, replacement):
    a, page, _ = france_source
    changed = FRANCE.replace(original, replacement)
    page.extract_text.return_value = changed
    with pytest.raises(ValueError):
        parse_foreign_history_artifact(changed, a)


def test_france_requires_summary_page(france_source):
    a, _, reader = france_source
    with pytest.raises(ValueError, match='summary page'):
        parse_foreign_history_artifact(FRANCE, dict(a, pdf_page=1))
    reader.pages = []
    with pytest.raises(ValueError, match='summary page'):
        parse_foreign_history_artifact(FRANCE, a)


@pytest.mark.parametrize('field,value', [('horse_name', '別馬'), ('horse_id', 'other-registered-id')])
def test_hkjc_cannot_relabel_an_english_horse_to_another_registered_identity(field, value):
    with pytest.raises(ValueError):
        parse_foreign_history_artifact(HKJC, dict(artifact('hkjc_history_sectionals'), **{field: value}))


@pytest.mark.parametrize('old,new', [
    ('2026年4月26日', '2026年4月27日'), ('シャティン', '他競馬場'),
    ('クイーンエリザベス2世カップ', '別の競走'), ('2,000メートル', '1,600メートル'),
    ('2,000メートル', '距離不明'), ('class="cell num">1<', 'class="cell num">5<'),
    ('class="place">2<', 'class="place">5<'),
])
def test_hkjc_requires_independent_jra_date_venue_event_distance_number_and_finish(old, new):
    import hashlib
    a = artifact('hkjc_history_sectionals')
    a['identity_source']['html'] = a['identity_source']['html'].replace(old, new)
    a['identity_source']['sha256'] = hashlib.sha256(a['identity_source']['html'].encode()).hexdigest()
    with pytest.raises(ValueError):
        parse_foreign_history_artifact(HKJC, a)


def test_jra_identity_source_must_be_hash_bound_and_official():
    for key, value in [('sha256', 'wrong'), ('url', 'https://unofficial.example/result')]:
        a = artifact('hkjc_history_sectionals')
        a['identity_source'][key] = value
        with pytest.raises(ValueError):
            parse_foreign_history_artifact(HKJC, a)


@pytest.mark.parametrize('old,new', [('Sha Tin', 'Unknown Venue'), ('Sha Tin', '???'),
                                   ('<span>THE FWD QEII CUP</span>', '')])
def test_hkjc_does_not_invent_venue_or_race_translation(old, new):
    with pytest.raises(ValueError):
        parse_foreign_history_artifact(HKJC.replace(old, new), artifact('hkjc_history_sectionals'))


def test_france_rejects_missing_pdf_and_invalid_pdf(france_source, monkeypatch):
    from pypdf.errors import PdfReadError
    import pypdf
    a, _, _ = france_source
    with pytest.raises(ValueError, match='cannot be read'):
        parse_foreign_history_artifact(FRANCE, dict(a, pdf_path=a['pdf_path'] + '.missing'))
    def invalid_pdf(path):
        raise PdfReadError('malformed primary source')
    monkeypatch.setattr(pypdf, 'PdfReader', invalid_pdf)
    with pytest.raises(ValueError, match='cannot be read'):
        parse_foreign_history_artifact(FRANCE, a)


@pytest.mark.parametrize('field,value', [('horse_name', '別馬'), ('horse_id', 'other-registered-id')])
def test_france_cannot_relabel_tracking_runner_to_another_registered_identity(france_source, field, value):
    a, _, _ = france_source
    with pytest.raises(ValueError):
        parse_foreign_history_artifact(FRANCE, dict(a, **{field: value}))


@pytest.mark.parametrize('old,new', [('class="cell num">15<', 'class="cell num">1<'),
                                   ('class="place">14<', 'class="place">1<'),
                                   ('2,400メートル', '2,000メートル')])
def test_france_requires_independent_jra_number_finish_and_distance(france_source, old, new):
    import hashlib
    a, _, _ = france_source
    a['identity_source']['html'] = a['identity_source']['html'].replace(old, new)
    a['identity_source']['sha256'] = hashlib.sha256(a['identity_source']['html'].encode()).hexdigest()
    with pytest.raises(ValueError):
        parse_foreign_history_artifact(FRANCE, a)


@pytest.fixture
def imperial_source(tmp_path):
    import hashlib
    from jra_scraper.foreign_history import parse_imperial_proxy_observation
    identity = identity_source()
    h = identity['html'].replace('2026年4月26日 シャティン', '2025年8月20日 ヨーク').replace('2,000メートル', '2,050メートル').replace('クイーンエリザベス2世カップ', 'インターナショナルステークス').replace('class="place">2<', 'class="place">2<').replace('class="cell num">1<', 'class="cell num">2<')
    identity.update(html=h, sha256=hashlib.sha256(h.encode()).hexdigest())
    url = 'https://www.racingtv.com/results/2025-08-20/york/1535'
    roster = ['Horse Information', '1st', 'Image', '1', '(1)', 'Other',
              '2nd', 'Image', '2', '(2)', 'Danon Decile']
    labels = [str(i) + 'f' for i in range(1, 11)] + ['Total Time']
    blocks = []
    for values in [['10.00'] * 10, ['10.00'] * 7 + ['11.22', '11.74', '13.04']]:
        for split in values:
            blocks.extend([split, '2nd | +0.10'])
        blocks.extend(['2m, 7.90s', 'FSP: 100.0%'])
    response = url + '\n' + '\n'.join('L%d: %s' % (i, x) for i, x in enumerate(roster + labels + blocks))
    path = tmp_path / 'imperial.json'
    path.write_text(__import__('json').dumps(response))
    item = {'horse_name': 'マスカレードボール', 'race_date': '2025-08-20', 'track': 'York',
            'official_horse_name': 'DANON DECILE', 'identity_source': identity,
            'source': {'path': str(path), 'url': url, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()},
            'individual_final_3_imperial_furlongs_splits_seconds': ['11.22', '11.74', '13.04'],
            'individual_final_3_imperial_furlongs_seconds': '36.00', 'section_distance_m': '603.504'}
    row = {'horse_id': 'official-jra-id', 'horse_name': 'マスカレードボール', 'race_id': 'target',
           'date': '2025年8月20日', 'distance': '2050'}
    return parse_imperial_proxy_observation, item, row, path, response


def test_imperial_proxy_is_reparsed_and_distinct_from_measured_600m(imperial_source):
    from decimal import Decimal
    parser, item, row, _, _ = imperial_source
    assert parser(item, row) == Decimal('36.00')
    row['date'] = '2025-08-20'
    assert parser(item, row) == Decimal('36.00')


@pytest.mark.parametrize('field,value', [('race_date', '2025-08-21'), ('track', 'Ascot'),
                                       ('horse_name', '別馬'), ('official_horse_name', 'Other'),
                                       ('section_distance_m', 600),
                                       ('individual_final_3_imperial_furlongs_seconds', 35),
                                       ('individual_final_3_imperial_furlongs_splits_seconds', ['11', '12', '13'])])
def test_imperial_declared_identity_or_values_cannot_replace_raw_observation(imperial_source, field, value):
    parser, item, row, _, _ = imperial_source
    item[field] = value
    with pytest.raises(ValueError):
        parser(item, row)


@pytest.mark.parametrize('field,value', [('horse_name', '別馬'), ('horse_id', 'wrong-id'),
                                       ('distance', '2000'), ('date', '2025-08-21')])
def test_imperial_row_must_match_registered_history(imperial_source, field, value):
    parser, item, row, _, _ = imperial_source
    row[field] = value
    with pytest.raises(ValueError):
        parser(item, row)


@pytest.mark.parametrize('change', ['url', 'hash', 'response_type', 'response_url'])
def test_imperial_source_response_is_bound(imperial_source, change):
    import hashlib, json
    parser, item, row, path, response = imperial_source
    if change == 'url':
        item['source']['url'] = 'https://evil.example/result'
    elif change == 'hash':
        item['source']['sha256'] = 'wrong'
    else:
        response = {} if change == 'response_type' else response.replace(item['source']['url'], 'https://wrong.example')
        path.write_text(json.dumps(response))
        item['source']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        parser(item, row)


@pytest.mark.parametrize('old,new', [
    ('Horse Information', 'Other Header'), ('10f', '11f'), ('Total Time', 'No Total Time'),
    ('(2)', '(?)'), ('L8: 2', 'L8: unknown'), ('L6: 2nd', 'L6: 3rd'),
    ('L10: Danon Decile', 'L10: Other'), ('FSP: 100.0%', 'FSP missing'),
    ('2m, 7.90s', 'bad total'), ('2nd | +0.10', 'missing position'),
])
def test_imperial_raw_table_schema_must_be_complete(imperial_source, old, new):
    import hashlib, json
    parser, item, row, path, response = imperial_source
    assert old in response
    response = response.replace(old, new)
    path.write_text(json.dumps(response))
    item['source']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        parser(item, row)


@pytest.mark.parametrize("change", ["none", "id", "name", "english", "date", "country", "distance", "finish"])
def test_prix_foy_uses_bilingual_jra_registered_profile(france_source, change):
    import hashlib

    artifact, page, _ = france_source
    text = FRANCE.replace("QATAR PRIX DE L'ARC DE TRIOMPHE", "QATAR PRIX FOY")
    page.extract_text.return_value = text
    html = ('<h1><span class="txt"><span>競走馬情報</span>クロワデュノール'
            '<span class="name_en">Croix du Nord（JPN）</span></span></h1><table><tr>'
            '<td class="date">2025年10月5日</td><td>仏</td><td class="race">フォワ賞</td>'
            '<td>芝2400</td><td></td><td></td><td></td><td>14</td>'
            + '<td></td>' * 6 + '</tr></table>')
    replacements = {"name": ("クロワデュノール", "別馬"), "english": ("Croix du Nord", "Other"),
                    "date": ("10月5日", "10月6日"), "country": ("仏", "英"),
                    "distance": ("芝2400", "芝2000"), "finish": ("<td>14", "<td>1")}
    if change in replacements:
        html = html.replace(*replacements[change])
    horse_id = "wrong-id" if change == "id" else artifact["horse_id"]
    artifact["identity_source"] = {
        "url": f"https://www.jra.go.jp/JRADB/accessU.html?CNAME={horse_id}",
        "html": html, "sha256": hashlib.sha256(html.encode()).hexdigest(),
    }
    if change == "none":
        assert parse_foreign_history_artifact(text, artifact)[0]["last_3f"] == "37.61"
    else:
        with pytest.raises(ValueError, match="Prix Foy"):
            parse_foreign_history_artifact(text, artifact)


def test_hkjc_requires_the_individual_elapsed_time():
    with pytest.raises(ValueError, match="individual elapsed time"):
        parse_foreign_history_artifact(
            HKJC.replace("2:00.80", "1 1/4"), artifact("hkjc_history_sectionals")
        )
