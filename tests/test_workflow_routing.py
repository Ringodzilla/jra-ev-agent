import unittest

from src.react_workflow import ReactiveRaceWorkflow, _downgrade_ticket_plan_for_review


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

    def test_reviewer_ng_downgrades_tickets_to_invalidated_references(self):
        plan = {
            "tickets": [
                {
                    "race_id": "r1",
                    "bet_type": "wide",
                    "horse_number": "1-2",
                    "horse_name": "A - B",
                    "stake": 400,
                    "ev_current": "1.20",
                }
            ],
            "races": [
                {
                    "race_id": "r1",
                    "tickets": [
                        {
                            "race_id": "r1",
                            "bet_type": "wide",
                            "horse_number": "1-2",
                            "horse_name": "A - B",
                            "stake": 400,
                            "ev_current": "1.20",
                        }
                    ],
                }
            ],
            "portfolio_summary": {"total_stake": 400},
        }

        downgraded = _downgrade_ticket_plan_for_review(plan, {"status": "NG", "reason": "risk"})

        self.assertEqual([], downgraded["tickets"])
        self.assertEqual("invalidated_by_reviewer", downgraded["ticket_status"])
        self.assertEqual(1, len(downgraded["invalidated_tickets"]))
        self.assertEqual(0, downgraded["portfolio_summary"]["total_stake"])
        self.assertEqual([], downgraded["races"][0]["tickets"])


if __name__ == "__main__":
    unittest.main()
