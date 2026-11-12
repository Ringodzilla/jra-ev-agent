"""CLI gate tests for the funnel runner.

The AP-0 gate is code-enforced (repo AGENTS.md section 12): with a
pending plan the runner must refuse before loading any race.  The
write-plan mode builds the manifest from summary metadata only and is
checked against the known pool composition (667 races).
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import funnel_data as fd
import run_funnel_screening as rfs


class WritePlanTests(unittest.TestCase):
    def test_manifest_counts_match_known_pool(self):
        manifest = fd.build_manifest()
        self.assertEqual(manifest['counts']['total'], 667)
        self.assertEqual(manifest['counts']['by_surface'],
                         {'芝': 438, 'ダート': 229})
        self.assertEqual(manifest['counts']['by_source'],
                         {'migrated_20260502_20260927': 298,
                          'expansion_20251001_20260430': 369})
        self.assertEqual(manifest['counts']['by_pool_block'],
                         {'train': 550, 'reference': 117})
        fd.verify_manifest(manifest)

    def test_plan_carries_mandated_statements(self):
        with TemporaryDirectory() as tmp:
            plan = rfs.write_plan(Path(tmp))
            self.assertEqual(plan['approval']['status'], 'pending')
            self.assertFalse(plan['production_eligible'])
            text = json.dumps(plan, ensure_ascii=False)
            self.assertIn('19', text)
            self.assertIn('UP-02', text)
            self.assertIn('CAL-02', text)
            self.assertEqual(plan['confirmation_pool']['blocks']
                             ['test']['by_surface']['ダート'], 19)
            self.assertFalse(plan['confirmation_pool']['opened'])


class ApprovalGateTests(unittest.TestCase):
    def test_pending_plan_refuses_run(self):
        with TemporaryDirectory() as tmp:
            plan = rfs.write_plan(Path(tmp))
            plan_path = Path(tmp) / 'research_plan.json'
            self.assertEqual(plan['approval']['status'], 'pending')
            with self.assertRaises(SystemExit):
                rfs.run(plan_path)

    def test_approved_plan_with_tampered_manifest_refuses(self):
        with TemporaryDirectory() as tmp:
            rfs.write_plan(Path(tmp))
            plan_path = Path(tmp) / 'research_plan.json'
            plan = json.loads(plan_path.read_text(encoding='utf-8'))
            plan['approval']['status'] = 'approved'
            plan_path.write_text(json.dumps(plan, ensure_ascii=False),
                                 encoding='utf-8')
            manifest_path = Path(tmp) / 'screening_pool_manifest.json'
            manifest = json.loads(manifest_path.read_text(
                encoding='utf-8'))
            manifest['counts'] = {'total': 1}
            manifest_path.write_text(json.dumps(manifest,
                                                ensure_ascii=False),
                                     encoding='utf-8')
            with self.assertRaises((SystemExit, fd.Blocked)):
                rfs.run(plan_path)


if __name__ == '__main__':
    unittest.main()
