"""Engine tests for the Issue #40 hypothesis funnel (synthetic data only).

Properties: the generalized penalized fit reduces to
``condition_fit._fit_penalized`` in the base case; the environment
design matches ``analysis.environment_model.design_matrix`` for the
ENV-T05 full linear form; every fittable registry hypothesis produces a
valid probability distribution on synthetic races; final odds never
enter a fit (poison property at the fit level).
"""
import math
import unittest

import numpy as np
from scipy.special import logsumexp

import condition_fit as cf
import funnel_engine as fe
import funnel_registry as fr

BANDS = {1200: '短距離（〜1400m）', 1600: 'マイル（1500–1800m）',
         2000: '中距離（1900–2200m）', 2500: '長距離（2300m〜）'}


def make_race(index, surface='芝', venue='東京', distance=1600, n=8,
              winner=0, date='2026-01-05'):
    rows = []
    for i in range(n):
        rows.append({
            'horse_number': str(i + 1),
            'ability_score': 0.6 + 0.1 * math.sin(index + i),
            'course_score': 0.4 + 0.05 * i,
            'pace_score': 0.5 + 0.04 * math.cos(index - i),
            'weight_score': 0.1 * ((i % 3) - 1),
            'jockey_score': 0.3 + 0.03 * i,
            'front_rate': 0.15 * (i % 4),
            'closing_strength': 0.4 + 0.1 * (i % 3),
            'speed_score': 0.5 + 0.05 * math.sin(2 * i + index),
            'track_condition_score': 0.5,
        })
    weights = cf.COMPONENT_WEIGHTS
    scores = np.array([math.fsum(weights[k] * float(r[k]) for k in weights)
                       for r in rows])
    probs = np.exp(scores - logsumexp(scores))
    for j, (row, prob) in enumerate(zip(rows, probs)):
        row['win_prob'] = float(prob)
        row['S_base'] = float(scores[j])
    odds = {str(i + 1): float(3 + ((index + i) % 9)) for i in range(n)}
    odds[str(winner + 1)] = 15.0
    market = np.array([1.0 / odds[str(i + 1)] for i in range(n)])
    if surface == '芝':
        environment = {'cushion': 9.0 + (index % 7) * 0.3,
                       'moisture_goal': 9.0 + (index % 5),
                       'moisture_corner4': 9.5 + (index % 4),
                       'weather': '晴' if index % 3 else '曇',
                       'track_condition': '良'}
    else:
        environment = {'cushion': None,
                       'moisture_goal': 8.0 + (index % 6),
                       'moisture_corner4': 8.5 + (index % 5),
                       'weather': '晴', 'track_condition': '良'}
    return {'race_id': f'R{index:03d}', 'date': date, 'split': 'train',
            'pool_block': 'train', 'surface': surface, 'venue': venue,
            'distance': distance, 'band': BANDS[distance], 'rows': rows,
            'environment': environment, 'winners': [str(winner + 1)],
            'win_payouts': {str(winner + 1): odds[str(winner + 1)] * 100},
            'final_odds': odds, 'popularity': {},
            'market_probs': (market / market.sum()).tolist()}


def make_train(count=24, surface=None):
    races = []
    venues = ['東京', '京都', '中山', '阪神']
    distances = [1200, 1600, 2000, 2500]
    for i in range(count):
        races.append(make_race(
            i, surface=surface or ('芝' if i % 2 == 0 else 'ダート'),
            venue=venues[i % 4], distance=distances[i % 4],
            winner=i % 8, date=f'2026-01-{1 + i // 2:02d}'))
    return races


class PenalizedFitTests(unittest.TestCase):
    def test_reduces_to_condition_fit(self):
        rng = np.random.default_rng(7)
        designs, offsets, targets = [], [], []
        for _ in range(12):
            designs.append(rng.normal(size=(6, 5)))
            offsets.append(rng.normal(size=6))
            target = np.zeros(6)
            target[int(rng.integers(6))] = 1.0
            targets.append(target)
        penalties = np.full(5, 10.0)
        mine = fe.penalized_fit(designs, offsets, targets, penalties)
        theirs = cf._fit_penalized(designs, offsets, targets, penalties)
        np.testing.assert_allclose(mine, theirs, atol=1e-6)

    def test_prior_and_bounds(self):
        races = make_train(10, surface='芝')
        model = fe.fit_global(races, prior_raw={
            'ability_score': 2.25, 'course_score': 0.18,
            'pace_score': 0.86, 'weight_score': -0.18,
            'jockey_score': 0.16}, prior_penalty=10.0)
        probs = fe.model_probs(races[0], model)
        self.assertAlmostEqual(float(probs.sum()), 1.0, places=10)
        bounded = fe.fit_global(races, bounds_raw={
            'ability_score': (0.0, None)}, ridge=1.0)
        self.assertGreaterEqual(
            bounded['adjust_raw'][cf.COMPONENTS.index('ability_score')],
            -1e-9)


class EnvironmentDesignTests(unittest.TestCase):
    def test_t05_design_matches_repo_design_matrix(self):
        from analysis.environment_model import design_matrix
        race = make_race(3, surface='芝')
        cf_env = {'cushion': race['environment']['cushion'],
                  'turf_moisture_goal':
                      race['environment']['moisture_goal'],
                  'turf_moisture_corner4':
                      race['environment']['moisture_corner4'],
                  'weather': race['environment']['weather'],
                  'track_condition':
                      race['environment']['track_condition']}
        theirs, names = design_matrix(race['rows'], cf_env, 'linear')
        spec = fr.BY_ID['ENV-T05']['env']
        params = fe._transform_params([race], spec['variables'],
                                      spec['form'])
        env_names, _, _ = fe._env_columns([race], spec, params)
        mine, my_names = fe._env_design(race, spec, params, env_names)
        self.assertEqual(my_names, names)
        np.testing.assert_allclose(mine, theirs, atol=1e-12)
        varied = make_train(12, surface='芝')
        params = fe._transform_params(varied, spec['variables'],
                                      spec['form'])
        _, frozen, _ = fe._env_columns(varied, spec, params)
        self.assertNotIn('cushion', frozen)
        self.assertNotIn('moisture_goal', frozen)

    def test_transform_names(self):
        race = make_race(1, surface='芝')
        for form, expected in (
                ('hinge', ['cushion_lo', 'cushion_hi']),
                ('piecewise', ['cushion', 'cushion_pw1', 'cushion_pw2'])):
            spec = {'variables': ('cushion',), 'form': form,
                    'features': 'all4'}
            params = fe._transform_params([race], ('cushion',), form)
            names, _, _ = fe._env_columns([race], spec, params)
            self.assertEqual(names, expected)

    def test_dirt_has_no_cushion(self):
        values = fe.env_base_values(make_race(2, surface='ダート')
                                    ['environment'])
        self.assertIsNone(values['cushion'])
        self.assertIn('moisture_mean', values)


class AllHypothesesFitTests(unittest.TestCase):
    def test_every_fittable_hypothesis_produces_probabilities(self):
        both = make_train(24)
        turf = [r for r in both if r['surface'] == '芝']
        dirt = [r for r in both if r['surface'] == 'ダート']
        pools = {'both': both, '芝': turf, 'ダート': dirt}
        for spec in fr.HYPOTHESES:
            if spec['kind'] in ('held', 'diagnostic'):
                continue
            train = pools[spec['surface']]
            model = fe.fit_hypothesis(train, spec, spec['grid'][0])
            probs = fe.model_probs(train[0], model)
            self.assertTrue(np.isfinite(probs).all(), spec['id'])
            self.assertAlmostEqual(float(probs.sum()), 1.0, places=8,
                                   msg=spec['id'])
            self.assertFalse(model['production_eligible'])

    def test_fit_ignores_final_odds(self):
        train = make_train(12, surface='芝')
        spec = fr.BY_ID['CAL-01']
        first = fe.fit_hypothesis(train, spec, spec['grid'][0])
        for race in train:
            keys = list(race['final_odds'])
            values = list(race['final_odds'].values())
            race['final_odds'] = dict(zip(keys, values[::-1]))
        second = fe.fit_hypothesis(train, spec, spec['grid'][0])
        np.testing.assert_allclose(first['theta'], second['theta'],
                                   atol=1e-10)


class IsotonicTests(unittest.TestCase):
    def test_monotone_and_min_bin(self):
        rng = np.random.default_rng(3)
        ps = rng.uniform(0.01, 0.4, size=600)
        ys = (rng.uniform(size=600) < ps).astype(float)
        model = fe.fit_isotonic(ps, ys, 200)
        self.assertEqual(model['values'],
                         sorted(model['values']))
        probs_race = {'rows': [{'win_prob': float(p)} for p in
                              [0.05, 0.1, 0.2, 0.3]]}
        probs = fe._isotonic_probs(probs_race, model)
        self.assertAlmostEqual(float(probs.sum()), 1.0, places=10)


class SegmentTests(unittest.TestCase):
    def test_band_names(self):
        self.assertEqual(fe.band3_name(1400), '短距離（〜1600m）')
        self.assertEqual(fe.band3_name(2300), '長距離（2201m〜）')
        self.assertEqual(fe.band5_name(2600), '長距離+（2501m〜）')

    def test_segment_support_shape(self):
        train = make_train(24)
        model = fe.fit_hypothesis(train, fr.BY_ID['SEG-01'],
                                  fr.BY_ID['SEG-01']['grid'][0])
        support = model['funnel_meta']['support']
        self.assertTrue(support)
        for entry in support.values():
            self.assertIn('n', entry)
            self.assertIn('supported', entry)


if __name__ == '__main__':
    unittest.main()
