from copy import deepcopy
import math
import pytest
from scripts.build_body_weight_panel import build_panel


def snapshots():
    entry=dict(race_id='R',horse_id='H',horse_number='1',body_weight_status='unpublished',
               body_weight_captured_at='2026-10-25T14:00:00+09:00')
    before=dict(completed_at='2026-10-25T14:01:00+09:00',entries=[entry],odds_snapshots=[
        dict(race_id='R',horse_number='1',current_odds='20',captured_at='2026-10-25T14:00:30+09:00')])
    after=deepcopy(before)
    after['completed_at']='2026-10-25T14:32:00+09:00'
    after['entries'][0].update(body_weight_status='published',body_weight_captured_at='2026-10-25T14:30:00+09:00',current_body_weight='500',body_weight_change='-8')
    after['odds_snapshots'].append(dict(race_id='R',horse_number='1',current_odds='15',captured_at='2026-10-25T14:31:00+09:00'))
    return before,after


def run(before,after):
    return build_panel(before,after,post_time='2026-10-25T15:40:00+09:00')


def test_actual_window_is_descriptive_and_does_not_use_future_quote():
    before,after=snapshots()
    after['odds_snapshots'].append(dict(race_id='R',horse_number='1',current_odds='1.1',captured_at='2026-10-25T16:00:00+09:00'))
    result=run(before,after)
    assert result['measured_count']==1
    assert result['horses'][0]['log_odds_change']==pytest.approx(math.log(.75))
    assert 'NOT_CAUSAL' in result['kind']


@pytest.mark.parametrize('fault',['order','duplicate','lineup','body_time','weight'])
def test_invalid_source_pair_is_not_filled(fault):
    before,after=snapshots()
    if fault=='order':after['completed_at']=before['completed_at']
    if fault=='duplicate':before['entries']*=2
    if fault=='lineup':after['entries'][0]['horse_id']='other'
    if fault=='body_time':after['entries'][0]['body_weight_captured_at']='2026-10-25T16:00:00+09:00'
    if fault=='weight':after['entries'][0]['current_body_weight']='nan'
    with pytest.raises(ValueError):run(before,after)


@pytest.mark.parametrize('fault',['unpublished','missing_quote','ambiguous_quote','no_new_quote'])
def test_unmeasured_is_null_with_reason(fault):
    before,after=snapshots()
    if fault=='unpublished':after['entries'][0]['body_weight_status']='unpublished'
    if fault=='missing_quote':before['odds_snapshots']=[]
    if fault=='ambiguous_quote':after['odds_snapshots'].append(dict(after['odds_snapshots'][-1],current_odds='16'))
    if fault=='no_new_quote':after['odds_snapshots'].pop()
    result=run(before,after)
    assert result['measured_count']==0
    assert result['horses'][0]['log_odds_change'] is None
    assert result['horses'][0]['reason']


def test_later_snapshot_does_not_prove_quote_is_after_publication():
    before, after = snapshots()
    after['odds_snapshots'][-1]['captured_at'] = '2026-10-25T14:29:00+09:00'
    row = run(before, after)['horses'][0]
    assert row['status'] == 'OBSERVED_SNAPSHOT_PRICE_CHANGE'
    assert row['later_quote_confirmed_after_publication'] is False


@pytest.mark.parametrize('field', ['post', 'snapshot', 'body', 'quote'])
def test_naive_time_is_not_accepted_as_an_event_time(field):
    before, after = snapshots()
    post = '2026-10-25T15:40:00+09:00'
    if field == 'post': post = '2026-10-25T15:40:00'
    if field == 'snapshot': before['completed_at'] = '2026-10-25T14:01:00'
    if field == 'body': before['entries'][0]['body_weight_captured_at'] = '2026-10-25T14:00:00'
    if field == 'quote': before['odds_snapshots'][0]['captured_at'] = '2026-10-25T14:00:30'
    with pytest.raises(ValueError, match='UTC offset'):
        build_panel(before, after, post_time=post)
