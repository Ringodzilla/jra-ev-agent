"""Isolated simulator-only research baseline; requires hash-bound PASS inputs."""
import argparse
import hashlib
import json
import math
import sys
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
    if not __debug__:
        raise RuntimeError('Python optimization disables required assertions; refuse execution')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-dir', type=Path, required=True)
    parser.add_argument('--analyzer-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    audit_dir, analyzer_dir, output = (args.audit_dir.resolve(), args.analyzer_dir.resolve(), args.output_dir.resolve())
    assert all(path.is_relative_to(BASE) for path in (audit_dir, analyzer_dir, output))
    assert not output.exists(), 'Existing output is forbidden'
    source = analyzer_dir / 'summary.json'
    summary = json.loads(source.read_text())
    audit_summary_path = audit_dir / 'summary.json'
    audit_summary = json.loads(audit_summary_path.read_text())
    assert audit_summary['batch_complete'] is True
    assert audit_summary['race_count'] == audit_summary['expected_race_count'] == 36
    assert len(list(audit_dir.glob('*_audit.json'))) == 36
    expected_races = set()
    expected_horses = 0
    for audit_path in sorted(audit_dir.glob('*_audit.json')):
        candidate = json.loads(audit_path.read_text())
        if candidate['gate'] == 'PASS' and candidate['model_ready'] is True:
            normalized_path = audit_path.with_name(audit_path.name.replace('_audit.json', '_normalized.json'))
            candidate_input = json.loads(normalized_path.read_text())
            assert {row['target_surface'] for row in candidate_input['entries']} == {'芝'}
            assert candidate['race_id'] not in expected_races
            expected_races.add(candidate['race_id'])
            expected_horses += len(candidate_input['entries'])
    assert expected_races and set(audit_summary['model_ready_race_ids']) == expected_races
    assert summary['status'] == 'PASS' and summary['races'] == len(expected_races)
    assert summary['horses'] == expected_horses
    scores_staged = [read_bound(item) for item in summary['artifacts']]
    assert len(scores_staged) == len(expected_races)
    assert {score['race_id'] for score in scores_staged} == expected_races
    assert len({item['path'] for item in summary['artifacts']}) == len(expected_races)
    assert all(Path(item['path']).resolve().parent == analyzer_dir for item in summary['artifacts'])
    output.mkdir(exist_ok=False)
    artifacts = []
    total = 0
    for item in summary['artifacts']:
        score = read_bound(item)
        assert score['status'] == 'PASS' and not score['defaults_applied']
        normalized = read_bound(score['input_artifact'])
        audit = read_bound(score['audit_artifact'])
        assert Path(score['audit_artifact']['path']).resolve().parent == audit_dir
        assert Path(score['input_artifact']['path']).resolve().parent == audit_dir
        assert audit['gate'] == 'PASS' and audit['model_ready'] is True
        assert audit['collected_audit']['gate'] == 'PASS'
        assert audit['collected_audit']['unapproved_finding_count'] == 0
        assert not audit['collected_audit']['findings']
        assert not audit['collected_audit']['verification_issues']
        assert not audit['source_issues'] and not audit['blocking_history_conflicts']
        assert {row['target_surface'] for row in normalized['entries']} == {'芝'}
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
        assert weights == {'ability_score': 0.4772727273, 'course_score': 0.1590909091,
                           'pace_score': 0.1818181818, 'weight_score': 0.0909090909,
                           'jockey_score': 0.0909090909}
        assert score['simulator_contract']['monte_carlo'] is False
        assert score['simulator_contract']['market_blending'] is False
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
        independent_scores = [sum(float(row[key])*weight for key,weight in weights.items()) for row in scenario]
        independent_exp = [math.exp(value) for value in independent_scores]
        independent_probs = [value/sum(independent_exp) for value in independent_exp]
        assert all(abs(x-y)<1e-12 for x,y in zip(p_final, independent_probs))
        assert all(abs(sum(values)-1)<1e-12 for values in by_scenario.values())
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
    assert total == expected_horses
    final = {'status':'PASS','role':'simulator','races':len(expected_races),'horses':total,'artifacts':artifacts,
             'analyzer_summary':ref(source),'audit_summary':ref(audit_summary_path),'ev_calls':0,'fit_calls':0,'production_model_changed':False}
    (output/'summary.json').write_text(json.dumps(final,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'summary':str(output/'summary.json'),'races':len(expected_races),'horses':total}))


if __name__ == '__main__':
    main()
