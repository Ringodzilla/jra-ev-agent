import unittest

from src.react_workflow import ReactiveRaceWorkflow


class TestWorkflowRouting(unittest.TestCase):
    def test_resolve_collector_key_defaults_to_domestic(self):
        key = ReactiveRaceWorkflow.resolve_collector_key(
            [
                {
                    "race_name": "フローラS",
                    "track": "東京",
                    "source_url": "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dde...",
                }
            ]
        )
        self.assertEqual("domestic", key)

    def test_resolve_collector_key_detects_overseas_from_url(self):
        key = ReactiveRaceWorkflow.resolve_collector_key(
            [
                {
                    "race_name": "チャンピオンズマイル",
                    "track": "シャティン",
                    "source_url": "https://www.jra.go.jp/JRADB/accessSD.html?CNAME=pk01dde...",
                }
            ]
        )
        self.assertEqual("overseas", key)

    def test_resolve_collector_key_detects_overseas_from_track(self):
        key = ReactiveRaceWorkflow.resolve_collector_key(
            [
                {
                    "race_name": "クイーンエリザベス2世C",
                    "track": "シャティン",
                    "source_url": "https://example.com/no-marker",
                }
            ]
        )
        self.assertEqual("overseas", key)

    def test_resolve_collector_key_honors_explicit_mode(self):
        key = ReactiveRaceWorkflow.resolve_collector_key(
            [
                {
                    "race_name": "チャンピオンズマイル",
                    "track": "シャティン",
                    "source_url": "https://www.jra.go.jp/JRADB/accessSD.html?CNAME=pk01dde...",
                    "collector_mode": "domestic",
                }
            ]
        )
        self.assertEqual("domestic", key)


if __name__ == "__main__":
    unittest.main()
