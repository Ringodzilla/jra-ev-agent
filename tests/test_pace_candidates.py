"""Conditional ticket probabilities preserve nonlinear per-trial ranking events."""
from copy import deepcopy
import math
import pytest
from analysis.pace_conditioning import simulate_pace_conditioned
from analysis.pace_candidates import build_pace_candidates
from analysis.candidate_ev import build_candidate_evaluations
from strategy.pace_selection import select_pace_candidates


def fixture():
    entries=[dict(race_id='R',horse_id=h,horse_number=str(i),frame_number=str(i)) for i,h in enumerate(['A','B','C'],1)]
    draws=[dict(trial_id=str(i),pace=pace,horse_ids=['A','B','C'],base_probabilities=ps,
        environment_logits=[0.,0.,0.],market_log_odds_response=[0.,0.,0.]) for i,(pace,ps) in enumerate([
            ('high',[.8,.15,.05]),('high',[.2,.2,.6]),('mid',[.3,.4,.3]),('slow',[.1,.8,.1])])]
    result=simulate_pace_conditioned(horse_ids=['A','B','C'],current_odds=[2.,3.,10.],draws=draws,
        seed=7,iterations=200,manifest={'purpose':'research','lineage':{'source':'synthetic'}},selected_pace='high')
    combos=[dict(race_id='R',bet_type=kind,combination=combo,odds=odds,snapshot_id='S',captured_at='2026-10-27T14:00:00+09:00') for kind,combo,odds in [
        ('win','1',3.),('win','2',3.),('place','1',1.1),('wide','1-2',2.),('wakuren','1-2',2.),
        ('umaren','1-2',4.),('umatan','1>2',5.),('sanrenpuku','1-2-3',1.1),('sanrentan','1>2>3',9.)]]
    return result,entries,combos


def test_pace_mc_to_all_ticket_types_to_fixed_selection():
    result,entries,combos=fixture()
    before=deepcopy(result)
    output=build_pace_candidates(result,entries,combos)
    assert {c['bet_type'] for c in output['candidates']}=={'win','place','wide','wakuren','umaren','umatan','sanrenpuku','sanrentan'}
    high=select_pace_candidates(output['candidates'],'high',.01,1.05)
    slow=select_pace_candidates(output['candidates'],'slow',.01,1.05)
    high_keys={c['candidate_key'] for c in high['eligible_candidates']}
    slow_keys={c['candidate_key'] for c in slow['eligible_candidates']}
    assert 'R|win|1' in high_keys and 'R|win|1' not in slow_keys
    assert 'R|win|2' in slow_keys and 'R|win|2' not in high_keys
    assert high['formal_tickets']==[] and high['formal_go_issued'] is False
    candidates={c['candidate_key']:c for c in output['candidates']}
    frame=candidates['R|wakuren|1-2']
    assert frame['number_kind']=='frame' and frame['horse_numbers']==[] and frame['frame_numbers']==['1','2']
    assert frame['source_snapshot_id']=='S' and frame['source_captured_at']=='2026-10-27T14:00:00+09:00'
    trials=[t for t in result['marginal']['trials'] if t['pace']=='high']
    oracle=[]
    for t in trials:
        rows=[dict(r,win_prob=p) for r,p in zip(entries,t['win_probabilities'])]
        oracle.append(next(c['hit_prob'] for c in build_candidate_evaluations(rows,combos)['candidate_evaluations'] if c['key']=='R|umaren|1-2'))
    assert candidates['R|umaren|1-2']['pace_metrics']['high']['hit_probability']==pytest.approx(sum(oracle)/len(oracle))
    assert result==before


@pytest.mark.parametrize('fault',['order','duplicate_number','race','empty_trials','source_changed','pace','length','bad_odds','nonfinite_ev'])
def test_invalid_trial_or_quote_lineage_is_rejected(fault):
    result,entries,combos=fixture()
    if fault=='order':entries.reverse()
    if fault=='duplicate_number':entries[1]['horse_number']='1'
    if fault=='race':entries[1]['race_id']='X'
    if fault=='empty_trials':result['marginal']['trials']=[]
    if fault=='source_changed':
        t=result['marginal']['trials'][0]
        same=next(x for x in result['marginal']['trials'][1:] if x['source_trial_id']==t['source_trial_id'])
        same['win_probabilities']=[.2,.4,.4]
    if fault=='pace':result['marginal']['trials'][0]['pace']='unknown'
    if fault=='length':result['marginal']['trials'][0]['win_probabilities']=[.5,.5]
    if fault=='bad_odds':combos[0]['odds']=-1
    if fault=='nonfinite_ev':combos[0]['odds']=math.inf
    with pytest.raises(ValueError):build_pace_candidates(result,entries,combos)


def test_absent_pace_remains_unmeasured_not_zero():
    result,entries,combos=fixture()
    result['marginal']['trials']=[t for t in result['marginal']['trials'] if t['pace']=='high']
    output=build_pace_candidates(result,entries,combos)
    assert output['candidates'][0]['pace_metrics']['slow']=={'status':'NOT_EVALUATED','reason':'NO_SAMPLED_TRIALS_FOR_PACE'}
