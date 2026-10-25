"""Isolated simulator-only research baseline; requires hash-bound PASS inputs."""
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
sys.path.insert(0, str(ROOT))
from src.agents.simulator import SimulatorAgent
from src.data_integrity import data_input_sha256


def ref(path):
    return {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def read_bound(item):
    path = Path(item['path'])
    assert ref(path)['sha256'] == item['sha256'], str(path)
    return json.loads(path.read_text())


def softmax(values):
    assert values and all(math.isfinite(x) for x in values)
    maximum = max(values)
    exp = [math.exp(x - maximum) for x in values]
    return [x / math.fsum(exp) for x in exp]


def identity(row):
    return tuple(str(row[k]) for k in ('race_id', 'horse_id', 'horse_number', 'horse_name'))


def main():
    source = BASE / 'analyzer_scores_20261024T233700856259Z' / 'summary.json'
    summary = json.loads(source.read_text())
    assert summary['status'] == 'PASS' and summary['races'] == 3 and summary['horses'] == 31
    output = BASE / ('simulator_baseline_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir()
    artifacts = []
    total = 0
    for item in summary['artifacts']:
        score = read_bound(item)
        assert score['status'] == 'PASS' and not score['defaults_applied']
        normalized = read_bound(score['input_artifact'])
        audit = read_bound(score['audit_artifact'])
        assert 'audit_run_20261024T233527917676Z' in score['audit_artifact']['path']
        assert audit['gate'] == 'PASS' and audit['model_ready'] is True
        assert audit['collected_audit']['gate'] == 'PASS'
        assert audit['collected_audit']['unapproved_finding_count'] == 0
        bound = data_input_sha256(normalized, normalized['race_configs'])
        assert bound == score['input_sha256'] == audit['collected_audit']['input_sha256']
        for code in score['code_artifacts']:
            assert ref(Path(code['path']))['sha256'] == code['sha256']
        features = score['feature_rows']
        expected = {identity(row) for row in normalized['entries']}
        assert len(expected) == len(features) == len({identity(row) for row in features})
        assert expected == {identity(row) for row in features}
        for row in features:
            for key in ('front_rate', 'closing_strength', 'ability_score', 'course_score', 'consistency',
                        'weight_score', 'jockey_score', 'target_distance'):
                assert key in row and math.isfinite(float(row[key]))
        scenario = SimulatorAgent().run(features)['scenario_rows']
        assert expected == {identity(row) for row in scenario}
        assert len(scenario) == len(features)
        weights = score['component_weights']
        assert score['simulator_contract']['temperature'] == 1
        for original, row in zip(features, scenario):
            assert identity(original) == identity(row)
            assert all(row[key] == original[key] for key in weights if key != 'pace_score')
        def logits(pace_key):
            return [math.fsum(float(weight) * float(row[pace_key if key == 'pace_score' else key])
                              for key, weight in weights.items()) for row in scenario]
        base_scores = logits('pace_score')
        p_base = softmax(base_scores)
        p_final = softmax([math.log(x) for x in p_base])
        by_scenario = {name: softmax(logits('pace_' + name)) for name in ('high', 'mid', 'slow')}
        assert abs(math.fsum(p_final) - 1) < 1e-12
        assert all(math.isfinite(x) and 0 < x <= 1 for x in p_final)
        assert all(abs(x-y) < 1e-14 for x,y in zip(p_base,p_final))
        assert p_base == softmax(logits('pace_score'))
        rows = []
        for index, row in enumerate(scenario):
            rows.append(dict(row, S_base=base_scores[index], p_base=p_base[index], p_final=p_final[index],
                             win_prob=p_final[index], environment_score=0.0,
                             environment_adjustment_applied=False,
                             environment_model_status='not_applied_research_baseline',
                             environment_unapplied_reason='Unpromoted environment coefficients are not applied.',
                             high=by_scenario['high'][index], mid=by_scenario['mid'][index],
                             slow=by_scenario['slow'][index], final=p_final[index]))
        result = {
            'record_type': 'isolated_research_simulator_baseline', 'role': 'simulator', 'status': 'PASS',
            'variant': 'exact_structural_softmax_temperature1_no_mc_no_market',
            'race_id': score['race_id'], 'input_sha256': bound,
            'input_artifacts': [item, score['input_artifact'], score['audit_artifact']],
            'code_artifacts': [ref(Path(__file__)), ref(ROOT/'src/agents/simulator.py'),
                               ref(ROOT/'analysis/ev.py'), ref(ROOT/'src/track_bias.py')],
            'component_weights': weights, 'rows': rows,
            'scenario_weights': {name: float(scenario[0]['pace_mix_'+name]) for name in ('high','mid','slow')},
            'probability_lineage': {
                'S_base': 'sum(component_weights[k] * simulator_row[k])',
                'pace_score': 'existing simulate_race_scenarios weighted high/mid/slow pace fits; existing four-decimal serialization retained',
                'scenario_probabilities': 'Softmax(sum(weights[k]*components[k]), replacing pace_score by pace_high/mid/slow)',
                'p_base': 'Softmax(S_base)', 'p_final': 'Softmax(log(p_base)+environment_score)',
                'environment_score': 0, 'temperature': 1,
                'final_combination': 'Softmax of blended pace score; not mixture of scenario probabilities',
                'mc': False, 'market': False,
            },
            'checks': {'input_hash_binding': True, 'complete_horse_universe': True,
                       'finite_positive_probabilities': True, 'probability_sum': math.fsum(p_final),
                       'independent_recalculation': True, 'environment_zero_invariant': True},
            'environment': score['environment'], 'environment_provenance': score['environment_provenance'],
            'pre_race_capture': False, 'production_learning_eligible': False,
            'ev_calculated': False, 'fit_performed': False, 'production_model_changed': False,
        }
        path = output / (score['numeric_race_id']+'_baseline.json')
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        artifacts.append(ref(path))
        total += len(rows)
    assert total == 31
    final = {'status':'PASS','role':'simulator','races':3,'horses':total,'artifacts':artifacts,
             'analyzer_summary':ref(source),'ev_calls':0,'fit_calls':0,'production_model_changed':False}
    (output/'summary.json').write_text(json.dumps(final,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'summary':str(output/'summary.json'),'races':3,'horses':total}))


if __name__ == '__main__':
    main()
