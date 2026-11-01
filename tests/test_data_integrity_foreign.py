"""Foreign sources enter the same hash, date and observation gates as JRA."""
import hashlib

from src.data_integrity import verify_source_artifacts
from src.skill_stage_contracts import stage_checks


def test_foreign_observation_is_reparsed_and_forged_date_is_rejected():
    html = '''<div class="cell date">2026年4月26日 シャティン競馬場</div>
    <table><tr><td class="place">2</td><td class="horse">マスカレードボール(JPN)</td>
    <td class="weight">57.0</td><td class="pop">2</td></tr></table>'''
    artifact = dict(kind='overseas_jra_result', url='https://jra.jp/result', html=html,
                    sha256=hashlib.sha256(html.encode()).hexdigest(), race_id='r',
                    horse_id='h', horse_name='マスカレードボール', date='2026-04-26')
    row = dict(race_id='r', horse_id='h', horse_name='マスカレードボール',
               date='2026-04-26', target_race_date='2026-11-01', position='2',
               popularity='2', weight='57')
    collected = dict(rows=[row], entries=[row], quality_report={},
                     source_audit={'artifacts': [artifact]})
    assert verify_source_artifacts(collected)['status'] == 'PASS'
    checks = stage_checks('collection', collected,
                          {'intake': {'race_configs': [{'race_id': 'r'}]}}, mode='prediction')
    assert all(checks.values())
    row['popularity'] = '1'
    check = verify_source_artifacts(collected)
    assert check['status'] == 'MISMATCH'
    assert any(item['field'] == 'popularity' for item in check['mismatches'])
    artifact['date'] = '2026-04-27'
    assert verify_source_artifacts(collected)['status'] == 'UNVERIFIED'
