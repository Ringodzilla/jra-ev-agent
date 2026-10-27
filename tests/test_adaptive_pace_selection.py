"""Statistical confirmation, rather than raw plurality, controls mainline transfer."""
from analysis.adaptive_pace import simulate_until_pace_advantage
from analysis.pace_candidates import build_pace_candidates
from strategy.pace_selection import select_confirmed_pace_candidates


def data(labels):
    return dict(horse_ids=['A','B'],current_odds=[2.,4.],draws=[
        dict(trial_id=str(i),horse_ids=['A','B'],pace=pace,
             base_probabilities=[.7,.3] if pace=='high' else [.3,.7],
             environment_logits=[0.,0.],market_log_odds_response=[0.,0.]) for i,pace in enumerate(labels)],
        seed=11,manifest={'purpose':'research','lineage':{'source':'synthetic'}},
        initial_iterations=100,batch_iterations=900,max_iterations=10000,alpha=.05,min_gap=.03)


def candidates(result):
    entries=[dict(race_id='R',horse_id=h,horse_number=str(i)) for i,h in enumerate(['A','B'],1)]
    combos=[dict(race_id='R',bet_type='win',combination=str(i),odds=o,snapshot_id='S',captured_at='2026-10-27T14:00:00+09:00') for i,o in [(1,2.),(2,4.)]]
    return build_pace_candidates(result,entries,combos)


def test_only_confirmed_pace_is_transferred_to_candidate_selection():
    result=simulate_until_pace_advantage(**data(['high']*6+['mid']*3+['slow']))
    assert result['adaptive']['additional_iterations']>0
    assert result['mainline']['pace']=='high'
    assert result['mainline']['statistical_confirmation'] is True
    built=candidates(result)
    assert built['selected_pace']=='high'
    selection=select_confirmed_pace_candidates(built['candidates'],result,.04,1.05)
    assert selection['eligible_candidates'][0]['horse_numbers']==['1']


def test_inconclusive_even_with_more_trials_cannot_fix_a_mainline():
    result=simulate_until_pace_advantage(**data(['high']*51+['mid']*49))
    assert result['adaptive']['iterations']==10000
    assert result['adaptive']['additional_iterations']==9900
    assert result['mainline']['statistical_confirmation'] is False
    assert result['conditional']['high']['summary'] is not None
    built=candidates(result)
    assert built['selected_pace'] is None
    selection=select_confirmed_pace_candidates(built['candidates'],result,.04,1.05)
    assert selection['eligible_candidates']==[]
    assert all(r['reason']=='PACE_NOT_SELECTED' for r in selection['evaluations'])
