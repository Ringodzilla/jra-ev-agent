"""Registry consistency tests for the Issue #40 hypothesis funnel.

The structured registry in ``funnel_registry`` must cover exactly the
hypotheses of the markdown original (the plan pins the markdown's
SHA-256), family by family, and the 2026-11-10 UP-02 / UP-08 ruling
(diagnostic is report-only) must be reflected in the kill sets.
"""
from pathlib import Path
import unittest

import funnel_registry as fr

REGISTRY_MD = Path(__file__).resolve().parents[4] / 'jra-funnel' \
    / 'hypothesis_registry.md'


class RegistryConsistencyTests(unittest.TestCase):
    def test_sixty_unique_hypotheses(self):
        ids = [spec['id'] for spec in fr.HYPOTHESES]
        self.assertEqual(len(ids), 60)
        self.assertEqual(len(set(ids)), 60)
        self.assertEqual(set(ids), set(fr.BY_ID))

    def test_family_counts(self):
        counts = {}
        for spec in fr.HYPOTHESES:
            counts[spec['family']] = counts.get(spec['family'], 0) + 1
        self.assertEqual(counts, fr.FAMILY_COUNTS)

    @unittest.skipUnless(
        REGISTRY_MD.exists(),
        'markdown original lives outside the repository (~/workspace/jra-funnel)')
    def test_matches_markdown_original(self):
        parsed = fr.parse_registry_markdown(REGISTRY_MD)
        structured = {}
        for spec in fr.HYPOTHESES:
            structured.setdefault(spec['family'], []).append(spec['id'])
        self.assertEqual({k: sorted(v) for k, v in parsed.items()},
                         {k: sorted(v) for k, v in structured.items()})

    def test_up_ruling_reflected(self):
        # 2026-11-10 ruling: the final-odds-band diagnostic of UP-02 and
        # UP-08 is report-only; only the standard kills K1-K7 apply and
        # no diagnostic-based kill key may exist in their specs.
        for hid in ('UP-02', 'UP-08'):
            spec = fr.BY_ID[hid]
            self.assertEqual(set(spec['kills']),
                             {'K1', 'K2', 'K3', 'K7'})
            self.assertNotIn('diagnostic_kill', spec)

    def test_diagnostic_hypotheses_never_advance(self):
        for hid in ('UP-01', 'UP-07'):
            spec = fr.BY_ID[hid]
            self.assertEqual(spec['kind'], 'diagnostic')
            self.assertEqual(spec['grid'], ())

    def test_held_pace_hypotheses_keep_reasons(self):
        for hid in ('PACE-03', 'PACE-06', 'PACE-07'):
            spec = fr.BY_ID[hid]
            self.assertEqual(spec['kind'], 'held')
            self.assertTrue(spec['hold_reason'])

    def test_every_fittable_hypothesis_has_grid_and_control(self):
        for spec in fr.HYPOTHESES:
            if spec['kind'] in ('held', 'diagnostic'):
                continue
            self.assertTrue(spec['grid'], spec['id'])
            self.assertIn(spec['control'],
                          ('base_form', 'cal01', 'no_env', 'env_t04'),
                          spec['id'])

    def test_single_difference_kinds_only(self):
        kinds = {spec['kind'] for spec in fr.HYPOTHESES}
        self.assertNotIn('composite', kinds)


if __name__ == '__main__':
    unittest.main()
