from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from analysis import environment_model as model


def races(count=30):
    output = []
    for i in range(count):
        rain = i % 2
        output.append({
            'race_id': f'R{i}', 'date': (date(2025, 1, 1) + timedelta(days=i)).isoformat(),
            'environment': {'weather': '雨' if rain else '晴', 'track_condition': '重' if rain else '良',
                            'cushion': 8 + 3 * (1 - rain), 'turf_moisture_goal': 6 + 8 * rain,
                            'turf_moisture_corner4': 7 + 8 * rain},
            'rows': [{'horse_number': str(horse), 'win_prob': 0.5, 'front_rate': float(horse == 1),
                      'closing_strength': float(horse == 2), 'speed_score': 0.5,
                      'track_condition_score': 0.5} for horse in (1, 2)],
            'winners': ['1' if rain else '2'], 'win_payouts': {'1' if rain else '2': 200},
        })
    return output


def test_shared_covariates_cancel_but_interactions_change_relative_probabilities():
    race = races()[0]
    matrix, names = model.design_matrix(race['rows'], race['environment'])
    assert np.allclose(matrix.sum(axis=0), 0)
    assert 'front_rate*cushion' in names and 'front_rate*moisture_goal' in names
    trained = model.fit_model(races()[:18], 'linear', 0.1)
    dry = model.probabilities(race, trained)
    wet = model.probabilities(dict(race, environment=races()[1]['environment']), trained)
    assert dry[1] > dry[0] and wet[0] > wet[1]
    assert dry.sum() == pytest.approx(1)
    same_horses = [dict(row, front_rate=0.5, closing_strength=0.5) for row in race['rows']]
    assert model.probabilities(dict(race, rows=same_horses), trained) == pytest.approx([0.5, 0.5])


def test_constant_environment_terms_are_not_falsely_identified():
    data = [dict(race, environment=races()[0]['environment']) for race in races()]
    fitted = model.fit_model(data, 'linear', 1)
    assert set(fitted['unsupported_environment_terms']) == set(model.ENV_FEATURES)
    assert fitted['coefficients'] == pytest.approx([0] * len(fitted['coefficients']))
    result = model.train_and_compare(data, min_train=1, min_validation=1, min_test=1)
    assert result['status'] == 'INSUFFICIENT_ENVIRONMENT_COVERAGE'


def test_chronological_split_never_splits_a_date_or_uses_test_for_fit():
    data = races(10)
    data[5]['date'] = data[4]['date']
    train, validation, test = model.chronological_blocks(data)
    assert max(r['date'] for r in train) < min(r['date'] for r in validation)
    assert max(r['date'] for r in validation) < min(r['date'] for r in test)
    result = model.train_and_compare(races())
    assert result['status'] == 'INSUFFICIENT_DATA'
    assert not result['production_model_changed']
    assert result['selected_model']['training_end_date'] < races()[24]['date']
    eligible = model.train_and_compare(races(), min_train=1, min_validation=1, min_test=1)
    assert eligible['status'] == 'ELIGIBLE_FOR_REVIEW'
    assert eligible['candidate']['test']['log_loss'] < eligible['baseline']['test']['log_loss']


def test_rejects_candidate_that_worsens_held_out_predictions():
    data = races()
    for race in data[18:]:
        race['winners'] = ['2' if race['winners'] == ['1'] else '1']
        race['win_payouts'] = {race['winners'][0]: 200}
    result = model.train_and_compare(data, min_train=1, min_validation=1, min_test=1)
    assert result['status'] == 'REJECTED'


def test_validation_errors_and_failed_optimizer(monkeypatch):
    race = races()[0]
    with pytest.raises(ValueError):
        model.environment_vector(race['environment'], 'invalid')
    with pytest.raises(ValueError):
        model.design_matrix([], race['environment'])
    for environment in (dict(race['environment'], weather=''), dict(race['environment'], cushion=float('nan')),
                        dict(race['environment'], turf_moisture_goal=-1)):
        with pytest.raises(ValueError):
            model.design_matrix(race['rows'], environment)
    with pytest.raises(ValueError):
        model.design_matrix([dict(r, speed_score=float('nan')) for r in race['rows']], race['environment'])
    with pytest.raises(ValueError):
        model.probabilities(dict(race, rows=[dict(r, win_prob=0) for r in race['rows']]))
    fitted = model.fit_model(races()[:5], 'quadratic', 1)
    with pytest.raises(ValueError):
        model.probabilities(race, dict(fitted, feature_names=[]))
    for data in (races(2), [races()[0]] * 3):
        with pytest.raises(ValueError):
            model.chronological_blocks(data)
    with pytest.raises(ValueError):
        model.evaluate_model([])
    with pytest.raises(ValueError):
        model.evaluate_model([dict(race, winners=['3'])])
    with pytest.raises(ValueError):
        model.fit_model([], 'linear', 1)
    with pytest.raises(ValueError):
        model.fit_model(races(), 'linear', -1)
    monkeypatch.setattr(model, 'minimize', lambda *args, **kwargs: SimpleNamespace(success=False))
    with pytest.raises(ValueError, match='failed'):
        model.fit_model(races(), 'linear', 1)
