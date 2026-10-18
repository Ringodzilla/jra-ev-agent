import math

import pytest

from analysis.candidate_ev import build_candidate_evaluations
from analysis.environment_scenarios import (
    cushion_course_adjustment,
    date_key,
    mix_candidate_evaluations,
    simulate_environment_mixture,
)
from analysis.ev import EVWeights


def histories():
    return [
        {"race_id": "R", "horse_id": f"H{horse}", "horse_number": str(horse),
         "horse_name": f"H{horse}", "frame_number": str(horse), "run_index": str(run),
         "date": f"2026-0{run}-01", "target_race_date": "2026-10-18",
         "course": "京都", "target_track": "京都", "history_surface": "芝", "target_surface": "芝",
         "distance": "2000", "target_distance": "2000", "position": str(position), "last_3f": "34.5",
         "track_condition": "良", "target_track_condition": "良", "weight": "55",
         "assigned_weight": "55", "passing_order": str(horse), "jockey": "J", "current_jockey": "J"}
        for horse in range(1, 5) for run, position in enumerate((horse, 5 - horse), 1)
    ]


def environments():
    return [{"id": "dry", "probability": 0.9, "weather": "晴", "track_condition": "良", "cushion": 10.8},
            {"id": "rain", "probability": 0.1, "weather": "雨", "track_condition": "稍重", "cushion": 9.8}]


def measurements():
    return [{"track": "京都", "date": "2026-01-01", "cushion": 10.8},
            {"track": "京都", "date": "2026-02-01", "cushion": 8.0}]


def test_cushion_uses_observed_performance_and_shrinks_distant_evidence():
    history = histories()[:2]
    fit = cushion_course_adjustment(history, measurements(), 10.8)
    mismatch = cushion_course_adjustment(history, measurements(), 8.0)
    assert fit["course_adjustment"] > 0 > mismatch["course_adjustment"]
    assert abs(cushion_course_adjustment(history, measurements(), 13.0)["course_adjustment"]) < abs(fit["course_adjustment"])
    assert fit["observed_starts"] == 2
    assert cushion_course_adjustment(history, measurements(), 100)["course_adjustment"] == 0
    assert cushion_course_adjustment(history, [], 10)["observed_starts"] == 0
    assert cushion_course_adjustment([dict(history[0], history_surface="ダート")], measurements(), 10)["observed_starts"] == 0
    assert cushion_course_adjustment([dict(history[0], position="中止")], measurements(), 10)["observed_starts"] == 0
    assert cushion_course_adjustment(history, measurements() + [dict(measurements()[0], cushion=None)], 10)["observed_starts"] == 2
    with pytest.raises(ValueError, match="before"):
        cushion_course_adjustment([dict(history[0], target_race_date="2026-01-01")], measurements(), 10)


@pytest.mark.parametrize("kwargs", [{"target_cushion": float('nan')}, {"target_cushion": 0},
                                    {"target_cushion": 10, "bandwidth": 0},
                                    {"target_cushion": 10, "prior_starts": -1}])
def test_invalid_cushion_parameters(kwargs):
    with pytest.raises(ValueError):
        cushion_course_adjustment([], [], **kwargs)


def test_invalid_observations_and_dates():
    assert date_key("2026年2月1日") == "2026-02-01"
    with pytest.raises(ValueError):
        date_key("missing")
    for extra in (dict(measurements()[0], cushion=-1), dict(measurements()[0], cushion=9.0)):
        with pytest.raises(ValueError):
            cushion_course_adjustment(histories(), measurements() + [extra], 10)


def test_environment_pace_mixture_conserves_mass_and_conditions_are_active():
    result = simulate_environment_mixture(histories(), environments(), measurements(),
                                          weights=EVWeights(monte_carlo_iterations=1),
                                          cushion_parameters={"strength": 0.4})
    assert len(result['components']) == 6
    assert sum(c['weight'] for c in result['components']) == pytest.approx(1)
    assert sum(r['win_prob'] for r in result['ev_rows']) == pytest.approx(1)
    for pace in ('high', 'mid', 'slow'):
        assert sum(r['by_pace'][pace] for r in result['ev_rows']) == pytest.approx(1)
    for component in result['components']:
        assert sum(float(r['win_prob']) for r in component['ev_rows']) == pytest.approx(1)
    assert result['components'][0]['ev_rows'][0]['target_weather'] == '晴'
    assert result['components'][3]['ev_rows'][0]['target_weather'] == '雨'
    odds = [{"race_id": "R", "bet_type": "sanrenpuku", "combination": "1-2-3", "odds": 20}]
    mixed = mix_candidate_evaluations(result['components'], odds)
    candidate = mixed['candidate_evaluations'][0]
    assert candidate['ev'] == pytest.approx(candidate['hit_prob'] * 20)
    assert sum(candidate['by_pace'].values()) == pytest.approx(candidate['hit_prob'])


def test_joint_probabilities_are_mixed_before_marginalization():
    def component(probs):
        return {"weight": 0.5, "pace": "high", "ev_rows": [
            {"race_id": "R", "horse_number": i, "win_prob": prob} for i, prob in enumerate(probs, 1)]}
    components = [component([0.7, 0.1, 0.1, 0.1]), component([0.1, 0.1, 0.1, 0.7])]
    odds = [{"race_id": "R", "bet_type": "sanrenpuku", "combination": "1-2-3", "odds": 20}]
    actual = mix_candidate_evaluations(components, odds)['candidate_evaluations'][0]['hit_prob']
    conditional = [build_candidate_evaluations(c['ev_rows'], odds)['candidate_evaluations'][0]['hit_prob'] for c in components]
    marginal = component([0.4, 0.1, 0.1, 0.4])['ev_rows']
    shortcut = build_candidate_evaluations(marginal, odds)['candidate_evaluations'][0]['hit_prob']
    assert actual == pytest.approx(sum(conditional) / 2)
    assert not math.isclose(actual, shortcut, abs_tol=1e-4)


def test_invalid_environment_and_component_inputs():
    for history, nodes in (([], environments()), (histories(), []),
                           (histories(), [dict(environments()[0], probability=0.3)]),
                           (histories(), [dict(environments()[0], probability=float('nan'))]),
                           (histories(), [dict(environments()[0], probability=0.5)] * 2),
                           (histories() + [dict(histories()[0], race_id='R2')], environments())):
        with pytest.raises(ValueError):
            simulate_environment_mixture(history, nodes, [])
    with pytest.raises(ValueError):
        mix_candidate_evaluations([], [])
    with pytest.raises(ValueError):
        mix_candidate_evaluations([{'weight': 1, 'pace': 'high', 'ev_rows': []}],
                                  [{'race_id': 'R', 'bet_type': 'win', 'combination': '1', 'odds': 2}])
