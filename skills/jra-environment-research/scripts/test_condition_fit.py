"""Numerical tests for the Issue #37 condition-coefficient research fit.

All fixtures are artificial.  The properties under test: repo metric parity,
market isolation (final odds are labels/benchmark only), shrinkage of thin
segments toward the global fit, dead-heat target handling, loader hash
verification, and fit determinism.
"""
import gzip
import hashlib
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

import condition_fit as cf


def make_race(race_id, venue, band, winner_index, n_horses=4, ability_edge=0.4,
              date='2026-06-06', environment=None):
    rows = []
    for i in range(n_horses):
        ability = 0.9 if i == winner_index else 0.9 - ability_edge
        rows.append({
            'horse_number': str(i + 1),
            'ability_score': ability, 'course_score': 0.5,
            'pace_score': 0.5, 'weight_score': 0.0, 'jockey_score': 0.5,
            'front_rate': 0.25 * i, 'closing_strength': 0.5,
            'speed_score': 0.5, 'track_condition_score': 0.5,
            'win_prob': 1.0 / n_horses,
        })
    odds = {str(i + 1): (2.0 if i == winner_index else 5.0 + i)
            for i in range(n_horses)}
    market = np.array([1.0 / odds[str(i + 1)] for i in range(n_horses)])
    return {
        'race_id': race_id, 'date': date, 'split': 'train', 'venue': venue,
        'distance': 1600, 'band': band, 'rows': rows,
        'environment': environment or {
            'cushion': 9.5, 'turf_moisture_goal': 10.0,
            'turf_moisture_corner4': 10.5, 'weather': '晴',
            'track_condition': '良'},
        'winners': [str(winner_index + 1)],
        'win_payouts': {str(winner_index + 1): odds[str(winner_index + 1)] * 100},
        'final_odds': odds, 'popularity': {},
        'market_probs': (market / market.sum()).tolist(),
    }


def make_train(n_a=30, n_b=1):
    races = []
    for i in range(n_a):
        races.append(make_race(f'A{i}', '東京', 'マイル（1500–1800m）', 0))
    for i in range(n_b):
        races.append(make_race(f'B{i}', '小倉', 'マイル（1500–1800m）', 0))
    return races


class MetricParityTests(unittest.TestCase):
    def test_base_metrics_match_repo_evaluator(self):
        from analysis.environment_model import evaluate_model
        races = [make_race('r1', '東京', 'マイル（1500–1800m）', 2),
                 make_race('r2', '京都', '中距離（1900–2200m）', 0)]
        mine = cf.evaluate(races, lambda r: cf.variant_probs(r, None))
        theirs = evaluate_model(races)
        for key in ('log_loss', 'brier', 'top1_hit_rate',
                    'win_top1_policy_roi'):
            self.assertAlmostEqual(mine[key], theirs[key], places=12)

    def test_dead_heat_target_halves(self):
        race = make_race('dh', '東京', 'マイル（1500–1800m）', 0)
        race['winners'] = ['1', '2']
        race['win_payouts'] = {'1': 200.0, '2': 500.0}
        target = cf._targets(race)
        self.assertAlmostEqual(float(target.sum()), 1.0)
        self.assertAlmostEqual(float(target[0]), 0.5)
        metrics = cf.evaluate([race], lambda r: cf.variant_probs(r, None))
        expected = -0.5 * (math.log(0.25) + math.log(0.25))
        self.assertAlmostEqual(metrics['log_loss'], expected, places=12)


class MarketIsolationTests(unittest.TestCase):
    def test_final_odds_never_enter_the_fit(self):
        train = make_train(n_a=12, n_b=3)
        model = cf.fit_variant(train, 'linear', 10.0, 1.0,
                               use_env=True, use_segments=True)
        metrics = cf.evaluate(train, lambda r: cf.variant_probs(r, model))
        market_before = cf.evaluate(train, cf.market_probs)
        for race in train:  # poison the label-side odds fields only
            race['final_odds'] = {k: v * (1.0 + 0.3 * int(k))
                                  for k, v in race['final_odds'].items()}
            raw = np.array([1.0 / race['final_odds'][str(i + 1)]
                            for i in range(len(race['rows']))])
            race['market_probs'] = (raw / raw.sum()).tolist()
        refit = cf.fit_variant(train, 'linear', 10.0, 1.0,
                               use_env=True, use_segments=True)
        np.testing.assert_allclose(model['stage1']['component_adjust'],
                                   refit['stage1']['component_adjust'])
        np.testing.assert_allclose(model['stage1']['env_coefficients'],
                                   refit['stage1']['env_coefficients'])
        for kind in ('venue', 'band'):
            for name in model['stage2']['deviations'][kind]:
                np.testing.assert_allclose(
                    model['stage2']['deviations'][kind][name],
                    refit['stage2']['deviations'][kind][name])
        metrics_after = cf.evaluate(train, lambda r: cf.variant_probs(r, refit))
        self.assertEqual(metrics, metrics_after)
        market_after = cf.evaluate(train, cf.market_probs)
        self.assertNotEqual(market_before['log_loss'],
                            market_after['log_loss'])


class ShrinkageTests(unittest.TestCase):
    def test_thin_segment_shrinks_harder(self):
        train = make_train(n_a=30, n_b=1)
        stage1 = cf.fit_stage1(train, 'linear', 10.0)
        stage2 = cf.fit_stage2(train, stage1, 10.0, 1.0)
        big = np.linalg.norm(stage2['deviations']['venue']['東京'])
        thin = np.linalg.norm(stage2['deviations']['venue']['小倉'])
        self.assertLess(thin, big)
        self.assertLess(stage2['shrinkage_factors']['venue']['小倉'],
                        stage2['shrinkage_factors']['venue']['東京'])

    def test_huge_multiplier_returns_to_global(self):
        train = make_train(n_a=8, n_b=2)
        stage1 = cf.fit_stage1(train, 'linear', 10.0)
        stage2 = cf.fit_stage2(train, stage1, 10.0, 1e8)
        for kind in ('venue', 'band'):
            for deviation in stage2['deviations'][kind].values():
                self.assertLess(float(np.linalg.norm(deviation)), 1e-4)

    def test_fit_is_deterministic(self):
        train = make_train(n_a=10, n_b=2)
        first = cf.fit_variant(train, 'linear', 10.0, 1.0,
                               use_env=True, use_segments=True)
        second = cf.fit_variant(train, 'linear', 10.0, 1.0,
                                use_env=True, use_segments=True)
        np.testing.assert_array_equal(first['stage1']['component_adjust'],
                                      second['stage1']['component_adjust'])
        np.testing.assert_array_equal(first['stage1']['env_coefficients'],
                                      second['stage1']['env_coefficients'])


class LoaderTests(unittest.TestCase):
    def _fixture(self, base: Path):
        rid = '20260606_東京_01'
        target = dict(race_id=rid, horse_id='h1', horse_name='人工1',
                      horse_number='1', frame_number='1',
                      target_track='東京', target_surface='芝',
                      target_distance='1600', target_track_condition='良',
                      target_weather='晴', target_race_date='2026-06-06',
                      assigned_weight='55', current_jockey='人工騎手')
        entries, rows = [], []
        for horse in (1, 2):
            entry = dict(target, horse_id=f'h{horse}',
                         horse_name=f'人工{horse}', horse_number=str(horse),
                         frame_number=str(horse))
            entries.append(entry)
            for run in (1, 2):
                rows.append(dict(
                    entry, run_index=str(run), date=f'2026-05-{10 + run:02d}',
                    position=str(horse), time=str(94 + horse),
                    distance='1600', last_3f=str(33 + horse),
                    passing_order=f'{horse}-{horse}', weight='55',
                    course='東京', history_surface='芝',
                    track_condition='良', jockey='人工騎手',
                    popularity=str(horse), odds=str(2 + horse)))
        audit_dir = base / 'audit'
        audit_dir.mkdir()
        normalized = audit_dir / f'{rid}_normalized.json'
        normalized.write_text(json.dumps({'rows': rows, 'entries': entries},
                                         ensure_ascii=False), encoding='utf-8')
        digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        audit = {
            'race_id': rid, 'environment_gate': 'PASS',
            'surface_gate': 'PASS', 'split': 'train',
            'segment': {'surface': '芝', 'venue': '東京', 'distance': 1600,
                        'band': 'マイル（1500–1800m）'},
            'post_time': '2026-06-06T15:00:00+09:00',
            'environment': {
                'cushion': {'measured_at': '2026-06-06T07:00:00+09:00',
                            'cushion': 9.6},
                'moisture': {'measured_at': '2026-06-06T06:00:00+09:00',
                             'turf_moisture_goal': 10.2,
                             'turf_moisture_corner4': 10.4}},
            'normalized_artifact': {'path': str(normalized),
                                    'sha256': digest(normalized)},
            'official_nonstarters': [{'horse_number': '3',
                                      'horse_name': '人工3'}],
        }
        audit_path = audit_dir / f'{rid}_audit.json'
        audit_path.write_text(json.dumps(audit, ensure_ascii=False),
                              encoding='utf-8')
        summary = {'races': [{
            'race_id': rid, 'split': 'train',
            'segment': audit['segment'],
            'research_admission': {'status': 'ADMITTED_RESEARCH'},
            'audit': {'path': str(audit_path), 'sha256': digest(audit_path)},
        }]}
        summary_path = base / 'summary.json'
        summary_path.write_text(json.dumps(summary, ensure_ascii=False),
                                encoding='utf-8')
        dataset = base / 'dataset'
        dataset.mkdir()
        outcome = {
            'race_id': rid,
            'observations': {'rows': [
                {'horse_number': '1', 'position': '2', 'popularity': '2'},
                {'horse_number': '2', 'position': '1', 'popularity': '1'},
                {'horse_number': '3', 'position': '取消', 'popularity': '',
                 'horse_name': '人工3'}]},
            'final_odds': {'field_universe_match': True, 'rows': [
                {'horse_number': '1', 'odds': '3.5'},
                {'horse_number': '2', 'odds': '1.9'},
                {'horse_number': '3', 'odds': '9.9'}]},
        }
        with gzip.open(dataset / 'outcomes_2026-06.jsonl.gz', 'wt',
                       encoding='utf-8') as handle:
            handle.write(json.dumps(outcome, ensure_ascii=False) + '\n')
        return summary_path, dataset, audit_path

    def test_loader_builds_race_and_labels(self):
        with TemporaryDirectory() as tmp:
            summary_path, dataset, _ = self._fixture(Path(tmp))
            races = cf.load_pool(summary_path, dataset)
            self.assertEqual(len(races), 1)
            race = races[0]
            self.assertEqual(race['winners'], ['2'])
            self.assertEqual(race['win_payouts'], {'2': 190.0})
            self.assertAlmostEqual(sum(race['market_probs']), 1.0)
            for row in race['rows']:
                self.assertNotIn('current_odds', row)
                self.assertIn('ability_score', row)

    def test_loader_blocks_on_tampered_audit(self):
        with TemporaryDirectory() as tmp:
            summary_path, dataset, audit_path = self._fixture(Path(tmp))
            audit_path.write_text(audit_path.read_text(encoding='utf-8')
                                  + ' ', encoding='utf-8')
            with self.assertRaises(cf.Blocked):
                cf.load_pool(summary_path, dataset)


if __name__ == '__main__':
    unittest.main()
