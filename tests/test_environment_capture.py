from datetime import datetime
import json
from unittest.mock import Mock, patch

import pytest
from bs4 import BeautifulSoup

from jra_scraper.environment import (
    _cushion_history,
    _measurement_time,
    _moisture_history,
    capture_environment,
)
from jra_scraper.config import ScrapeConfig
from src.agents.data_collector import DataCollectorAgent


RACE = '<div class="cell baba"><li class="weather"><span class="txt">晴</span></li><li class="turf"><span class="txt">良</span></li></div>'
CUSHION = '<div title="京都"><div class="unit"><div class="time">10月18日（日曜）7時00分</div><div class="cushion">10.8</div></div></div>'
MOISTURE = '<div title="京都"><div class="unit"><div class="time">10月18日（日曜）6時00分</div><div class="turf"><span class="mg">6.8</span><span class="m4c">7.7</span></div><div class="dirt"><span class="mg">2.5</span><span class="m4c">2.0</span></div></div></div>'


def config():
    return {'race_name': '秋華賞', 'race_id': '20261018_京都_11', 'race_date': '2026-10-18',
            'track': '京都', 'race_number': 11, 'post_time': '15:40', 'surface': '芝',
            'source_url': 'https://www.jra.go.jp/JRADB/accessD.html?CNAME=test'}


def scraper(cushion=CUSHION, moisture=MOISTURE, race=RACE):
    fake = Mock()
    fake.fetch.side_effect = lambda url, **kwargs: (cushion if '_cushion' in url else
                                                    moisture if '_moist' in url else race)
    return fake


NOW = datetime.fromisoformat('2026-10-18T10:00:00+09:00')


def test_fresh_observations_are_required_and_saved_immutably(tmp_path):
    client = scraper()
    observation = capture_environment(client, config(), [], tmp_path, now=NOW)
    assert observation['eligible_for_learning'] and observation['status'] == 'COMPLETE'
    assert observation['weather'] == '晴' and observation['cushion'] == 10.8
    assert observation['turf_moisture_goal'] == 6.8
    assert observation['turf_moisture_goal_condition'] == ''
    assert observation['collection_complete']
    assert observation['required_fields_for_surface'][-2:] == [
        'turf_moisture_goal', 'turf_moisture_corner4'
    ]
    assert observation['cushion_history'] == [
        {'measured_at': '2026-10-18T07:00:00+09:00', 'cushion': 10.8}
    ]
    assert observation['moisture_history'][0]['turf_moisture_goal'] == 6.8
    assert len(observation['sources']) == 3
    assert client.fetch.call_count == 3
    assert all(call.kwargs['use_cache'] is False for call in client.fetch.call_args_list)
    cached = capture_environment(client, config(), [], tmp_path, cache_only=True)
    assert cached['captured_at'] == observation['captured_at']
    assert client.fetch.call_count == 3
    assert len(list(tmp_path.rglob('*.json'))) == 1
    old = [{'race_id': config()['race_id'], 'date': '2026-09-13', 'target_weather': '雨'}]
    capture_environment(scraper(), config(), old, tmp_path,
                        now=datetime.fromisoformat('2026-10-18T10:01:00+09:00'))
    case = json.loads(next(tmp_path.glob('*/cases/*.json')).read_text())
    assert case['data_collector']['rows'][0]['target_weather'] == '晴'
    assert old[0]['target_weather'] == '雨'


def test_previous_day_values_are_recorded_but_never_eligible_for_training(tmp_path):
    stale = capture_environment(scraper(CUSHION.replace('18日', '17日')), config(), [], tmp_path, now=NOW)
    assert not stale['eligible_for_learning'] and stale['cushion'] == 10.8
    assert 'same_day_measurements_required' in stale['eligibility_reasons']
    after = capture_environment(scraper(), config(), [], tmp_path,
                                now=datetime.fromisoformat('2026-10-18T16:00:00+09:00'))
    assert not after['eligible_for_learning'] and 'captured_after_post' in after['eligibility_reasons']
    unknown = capture_environment(scraper(), dict(config(), post_time=None), [], tmp_path, now=NOW)
    assert 'post_time_required' in unknown['eligibility_reasons']
    yesterday = capture_environment(scraper(), config(), [], tmp_path,
                                    now=datetime.fromisoformat('2026-10-17T10:00:00+09:00'))
    assert not yesterday['eligible_for_learning']
    assert yesterday['errors'] and 'same_day_observed_weather_required' in yesterday['eligibility_reasons']


@pytest.mark.parametrize('client', [scraper(cushion=None), scraper(moisture=None), scraper(race=None),
    scraper(cushion='<html/>'), scraper(cushion=CUSHION.replace('10.8', '99')),
    scraper(moisture=MOISTURE.replace('6.8', '101')), scraper(cushion=CUSHION.replace('7時00分', '未公表')),
    scraper(moisture=MOISTURE.replace('class="time"', 'class="other"'))])
def test_missing_or_invalid_values_fail_closed(client, tmp_path):
    result = capture_environment(client, config(), [], tmp_path, now=NOW)
    assert result['status'] == 'INCOMPLETE' and not result['eligible_for_learning']
    assert result['missing_fields'] or result['errors']
    saved = json.loads(next(tmp_path.rglob('*.json')).read_text())
    assert saved['value_type'] == 'observed'


def test_unsupported_and_missing_cache_do_not_call_network(tmp_path):
    client = scraper()
    assert capture_environment(client, config(), [], tmp_path, cache_only=True)['status'] == 'MISSING_CACHED_OBSERVATION'
    cfg = dict(config(), source_url='https://example.test/race')
    del cfg['race_id']
    assert capture_environment(client, cfg, [], tmp_path)['status'] == 'UNSUPPORTED_SOURCE'
    client.fetch.assert_not_called()
    with pytest.raises(ValueError):
        _measurement_time('unknown', 2026)


def test_dirt_requires_dirt_moisture_but_not_turf_cushion(tmp_path):
    result = capture_environment(
        scraper(cushion=None, race=RACE.replace('class="turf"', 'class="dirt"')),
        dict(config(), surface='ダート'),
        [],
        tmp_path,
        now=NOW,
    )
    assert result['collection_complete']
    assert 'cushion_fetch_failed' in result['nonblocking_errors']
    assert 'cushion' not in result['required_fields_for_surface']
    assert 'dirt_moisture_goal' in result['required_fields_for_surface']


def test_unknown_surface_and_invalid_history_items_are_auditable(tmp_path):
    unknown = capture_environment(
        scraper(), dict(config(), surface='海'), [], tmp_path, now=NOW
    )
    assert 'unsupported_surface: 海' in unknown['errors']
    soup = BeautifulSoup(
        '<div title="京都"><div class="unit"><div class="time">bad</div>'
        '<div class="cushion">99</div></div></div>',
        'html.parser',
    )
    course = soup.select_one('div[title]')
    assert _cushion_history(course, 2026) == []
    assert _moisture_history(course, 2026) == []
    bad_cushion = BeautifulSoup(
        '<div title="京都"><div class="unit"><div class="time">10月18日（日曜）7時00分</div>'
        '<div class="cushion">99</div></div></div>',
        'html.parser',
    ).select_one('div[title]')
    assert _cushion_history(bad_cushion, 2026) == []
    bad_moisture = BeautifulSoup(
        MOISTURE.replace('6.8', '101'), 'html.parser'
    ).select_one('div[title]')
    assert _moisture_history(bad_moisture, 2026) == []


def test_data_collector_updates_model_inputs_from_newly_observed_weather(tmp_path):
    cfg = ScrapeConfig(stages_dir=tmp_path / 'stages', entries_csv=tmp_path / 'entries.csv',
                       odds_snapshots_csv=tmp_path / 'odds.csv', combo_odds_csv=tmp_path / 'combo.csv',
                       quality_report_path=tmp_path / 'quality.json')
    pipeline = Mock()
    pipeline.run.return_value = [{'race_id': 'R', 'target_weather': '雨'}, {'race_id': 'OTHER'}]
    observed = {'race_id': 'R', 'weather': '晴', 'track_condition': '良', 'weather_captured_at': NOW.isoformat()}
    with patch('src.agents.data_collector.JRAPipeline', return_value=pipeline), patch(
        'src.agents.data_collector.capture_environment', return_value=observed
    ):
        result = DataCollectorAgent(cfg).run([{'race_id': 'R'}])
    assert result['rows'][0]['target_weather'] == '晴'
    assert result['rows'][0]['target_conditions_captured_at'] == NOW.isoformat()
    assert result['environment_snapshots'] == [observed]
    pipeline.close.assert_called_once()
