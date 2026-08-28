import unittest

from src.react_workflow import ReviewerAgent, WorkflowSettings
from strategy.betting import (
    _annotate_candidate_selection,
    _max_horse_stake_dependency_ratio,
    _optimize_portfolio_stakes,
    _select_optimized_tickets,
)


def _wide(combo, *, stake=100, ev=1.20, odds=12.0, hit_prob=0.10):
    numbers = combo.split("-")
    return {
        "race_id": "r_safety",
        "bet_type": "wide",
        "horse_number": combo,
        "horse_numbers": numbers,
        "horse_name": combo,
        "stake": stake,
        "hit_prob": str(hit_prob),
        "win_prob": str(hit_prob),
        "predicted_odds": str(odds),
        "wide_odds_est": str(odds),
        "ev": str(ev),
        "ev_current": str(ev),
        "confidence": "1.0",
        "odds_source": "jra_live",
    }


class TestBetBuilderSafety(unittest.TestCase):
    def test_optimizer_keeps_top_win_probability_horse(self):
        tickets = [
            _wide("2-3", ev=1.40),
            _wide("1-4", ev=1.05),
            _wide("5-6", ev=1.30),
        ]

        selected = _select_optimized_tickets(
            tickets,
            per_race_limit=2,
            prefer_wide=True,
            force_win_standout=False,
            min_portfolio_ev=1.0,
            required_horse_number="1",
        )

        self.assertTrue(selected)
        self.assertTrue(any("1" in ticket["horse_numbers"] for ticket in selected))

    def test_optimizer_fails_closed_without_top_horse_candidate(self):
        selected = _select_optimized_tickets(
            [_wide("2-3", ev=1.40), _wide("4-5", ev=1.30)],
            per_race_limit=2,
            prefer_wide=True,
            force_win_standout=False,
            min_portfolio_ev=1.0,
            required_horse_number="1",
        )

        self.assertEqual([], selected)

    def test_stake_optimizer_caps_non_core_horse_dependency(self):
        tickets = [
            _wide("15-8", ev=1.30, hit_prob=0.11),
            _wide("1-8", ev=1.20, hit_prob=0.12),
            _wide("4-14", ev=1.18, hit_prob=0.13),
            _wide("1-14", ev=1.15, hit_prob=0.14),
            _wide("15-13", ev=1.28, hit_prob=0.11),
        ]

        allocated = _optimize_portfolio_stakes(
            tickets,
            bankroll_per_race=1000,
            min_portfolio_ev=1.0,
            max_horse_stake_dependency_ratio=0.60,
            stake_dependency_exempt_horse_numbers={"1", "3", "8"},
        )

        ratio = _max_horse_stake_dependency_ratio(
            allocated,
            exempt_horse_numbers={"1", "3", "8"},
        )
        self.assertLessEqual(ratio, 0.60)

    def test_single_ticket_dependency_is_explicitly_not_a_diversification_metric(self):
        ratio = _max_horse_stake_dependency_ratio(
            [_wide("15-8", stake=100)],
            exempt_horse_numbers=set(),
        )

        self.assertEqual(0.0, ratio)

    def test_candidates_record_selection_and_rejection_reasons(self):
        candidates = [_wide("1-2"), _wide("3-4"), _wide("5-6")]
        annotated = _annotate_candidate_selection(
            candidates,
            selected_tickets=[candidates[0]],
            eligible_tickets=[candidates[0], candidates[1]],
            selection_pool=[candidates[0], candidates[1]],
        )

        by_combo = {row["horse_number"]: row for row in annotated}
        self.assertTrue(by_combo["1-2"]["selected"])
        self.assertEqual("selected_portfolio", by_combo["1-2"]["selection_reason"])
        self.assertEqual("portfolio_optimization", by_combo["3-4"]["non_selection_reason"])
        self.assertEqual("below_minimum_ev", by_combo["5-6"]["non_selection_reason"])

    def test_reviewer_rejects_amount_weighted_non_core_dependency(self):
        ev_rows = [
            {
                "race_id": "r_safety",
                "horse_number": str(number),
                "horse_name": f"H{number}",
                "win_prob": str(probability),
                "current_odds": "12.0",
                "predicted_odds": "12.0",
                "ev_current": "1.2",
                "ev_predicted": "1.2",
            }
            for number, probability in [
                (1, 0.30),
                (2, 0.25),
                (3, 0.20),
                (4, 0.10),
                (5, 0.08),
                (15, 0.07),
            ]
        ]
        tickets = [
            _wide("15-8", stake=400, ev=1.20, odds=12.0, hit_prob=0.10),
            _wide("15-13", stake=300, ev=1.20, odds=12.0, hit_prob=0.10),
            _wide("1-4", stake=100, ev=1.20, odds=12.0, hit_prob=0.10),
            _wide("2-5", stake=100, ev=1.20, odds=12.0, hit_prob=0.10),
            _wide("4-6", stake=100, ev=1.20, odds=12.0, hit_prob=0.10),
        ]
        reviewer = ReviewerAgent(
            WorkflowSettings(max_horse_stake_dependency_ratio=0.60)
        )

        review = reviewer.run(
            {
                "quality_report": {"issues_by_severity": {}},
                "entries": [{"horse_number": row["horse_number"]} for row in ev_rows],
            },
            scenario_rows=ev_rows,
            ev_rows=ev_rows,
            ticket_plan={"tickets": tickets},
            attempt=0,
        )

        self.assertEqual("NG", review["status"])
        self.assertIn("horse stake dependency ratio", review["reason"])


if __name__ == "__main__":
    unittest.main()
