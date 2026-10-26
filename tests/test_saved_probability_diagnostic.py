"""Diagnostics distinguish a flat model from evidence of longshot skill."""
from copy import deepcopy
import json
import math
import sys

import pytest

from scripts.diagnose_saved_probabilities import diagnose, main


def fixture():
    return ([dict(race_id='R', horse_number=str(i), horse_name=str(i), win_prob=p, current_odds=o)
             for i, p, o in [(1, .5, 2), (2, .25, 5), (3, .25, 8)]],
            [dict(horse_number=str(i), place=i) for i in [1, 2, 3]])


def test_metrics_against_hand_computed_scores_and_no_mutation():
    rows, result = fixture()
    original = deepcopy(rows)
    report = diagnose(rows, result, race_id='R')
    assert report['metrics']['saved_model']['winner_log_loss'] == pytest.approx(math.log(2))
    assert report['metrics']['saved_model']['multiclass_brier_sum'] == pytest.approx(.375)
    assert report['metrics']['uniform']['multiclass_brier_sum'] == pytest.approx(2 / 3)
    assert report['high_ev_comparison']['overlap_count'] == 2
    assert rows == original


@pytest.mark.parametrize('fault', ['duplicate', 'wrong_race', 'partial', 'tie', 'negative', 'nan', 'sum', 'odds'])
def test_invalid_saved_predictions_or_results_are_not_silently_filled(fault):
    rows, result = fixture()
    if fault == 'duplicate': rows[1]['horse_number'] = '1'
    if fault == 'wrong_race': rows[0]['race_id'] = 'other'
    if fault == 'partial': result.pop()
    if fault == 'tie': result[1]['place'] = 1
    if fault == 'negative': rows[0]['win_prob'] = -.1
    if fault == 'nan': rows[0]['win_prob'] = float('nan')
    if fault == 'sum': rows[0]['win_prob'] = .4
    if fault == 'odds': rows[0]['current_odds'] = 1
    with pytest.raises(ValueError):
        diagnose(rows, result, race_id='R')


def test_cli_verifies_provenance_and_start_cutoff(tmp_path, monkeypatch, capsys):
    import hashlib
    rows, result = fixture()
    run = tmp_path / 'run'
    run.mkdir()
    ev = run / '04_ev_calculator.json'
    decision = run / 'final_decision.json'
    retrospective = tmp_path / 'review.json'
    output = tmp_path / 'out.json'
    ev.write_text(json.dumps(dict(ev_rows=rows)))
    d = dict(race_id='R', issued_at='2026-10-25T14:53:00+09:00', post_time='2026-10-25T15:40:00+09:00')
    decision.write_text(json.dumps(d))
    r = dict(race_id='R', official_result=dict(finishers=result), input_artifacts=[
        dict(path=str(p.resolve()), sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in [ev, decision]])
    retrospective.write_text(json.dumps(r))
    monkeypatch.setattr(sys, 'argv', ['diagnose', '--run-dir', str(run), '--retrospective', str(retrospective), '--output', str(output)])
    main()
    assert json.loads(capsys.readouterr().out)['high_ev_comparison']['model_count'] == 2
    ev.write_text('{}')
    with pytest.raises(ValueError, match='provenance'): main()
    d['issued_at'] = d['post_time']
    decision.write_text(json.dumps(d))
    with pytest.raises(ValueError, match='pre-start'): main()
