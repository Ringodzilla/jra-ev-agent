from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests

from analysis.ev import (
    _safe_int,
    _short_sprint_front_density_adjustment,
    _to_float as ev_to_float,
    build_feature_rows,
    compute_ev,
    load_rows,
    save_ev,
)
from jra_scraper.config import ScrapeConfig
from jra_scraper.data_repair import MissingHistoryRepairAction
from jra_scraper.live_snapshot import (
    LiveSnapshotCollector,
    LiveSnapshotDeadlineExceeded,
    _as_utc as snapshot_as_utc,
    _dedupe_odds,
    _odds_displayed_at,
    _race_from_config,
)
from jra_scraper.scraper import JRAScraper
from src.agents.analyzer import AnalyzerAgent
from src.agents.bet_builder import BetBuilderAgent
from src.agents.data_collector import DataCollectorAgent, OverseasDataCollectorAgent, _read_csv, _read_json
from src.agents.ev_calculator import EVCalculatorAgent
from src.agents.race_utils import _race_id_from_config, _race_order_from_configs, _to_float as race_to_float
from src.agents.settings import WorkflowSettings
from src.agents.simulator import SimulatorAgent
from src.betting import build_tickets, save_tickets
from src.deadline import _aware_jst, race_post_datetime
from src.evaluator import save_outputs
from src.feature_engineering import (
    _condition_confidence,
    _condition_match_score,
    _country_value_score,
    _fmt_float,
    _front_running_score,
    _parse_timestamp,
    _recency_weights,
    _relative_slope,
    _safe_int as feature_safe_int,
    _surface_from_distance_field,
    _weighted_mean,
    summarize_live_odds_rows,
)
from src.features import build_features
from src.model import (
    ModelWeights,
    _apply_probability_caps,
    _blend_probabilities,
    _market_divergence_shrink,
    _market_probs,
    _monte_carlo_win_probs,
    _normalize_probs,
    _predict_live_odds,
    _predict_structural_odds,
    _probability_cap,
    _softmax,
    _to_float as model_to_float,
)
from src.track_bias import (
    _closing_deficiency_adjustment,
    _frame_profile_matches,
    _load_course_learning_config,
    _pace_profile_matches,
    _profile_specificity,
    _to_float as bias_to_float,
    course_pace_adjustment,
    learned_frame_adjustment,
)
from strategy.live_odds import (
    _is_true,
    _to_float as odds_to_float,
    build_live_odds_lookup,
    live_combo_key,
    live_odds_value,
)
from strategy.portfolio import (
    _to_float as portfolio_to_float,
    portfolio_ev,
    portfolio_no_gami,
    portfolio_summary,
    ticket_max_return_if_hit,
    ticket_point_count,
    ticket_return_if_hit,
    ticket_stake_unit,
    with_adjusted_stake,
)
from strategy.win5 import (
    _candidate_allocations,
    _default_max_points,
    _normalized_entropy,
    _ordered_race_groups,
    _popularity_mix,
    _to_float as win5_to_float,
    _under_10_fixed_risk,
)
from jra_scraper.validation import (
    _normalize_body_weight_status,
    _normalize_date,
    _normalize_horse_id,
    _normalize_passing_order,
    _normalize_time,
    _safe_int as validation_safe_int,
)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _scrape_config(tmp_path: Path) -> ScrapeConfig:
    return ScrapeConfig(
        output_csv=tmp_path / "race_last5.csv",
        entries_csv=tmp_path / "entries.csv",
        odds_snapshots_csv=tmp_path / "odds.csv",
        combo_odds_csv=tmp_path / "combo.csv",
        raw_dir=tmp_path / "raw",
        state_path=tmp_path / "state.json",
        quality_report_path=tmp_path / "quality.json",
        missing_history_requests_path=tmp_path / "missing.json",
        manual_history_template_csv=tmp_path / "manual-template.csv",
        manual_history_csv=tmp_path / "manual.csv",
        stages_dir=tmp_path / "stages",
    )


def test_analysis_file_helpers_and_empty_paths(tmp_path: Path) -> None:
    source = tmp_path / "rows.csv"
    _write_csv(source, ["horse", "odds"], [{"horse": "A", "odds": "2.0"}])
    assert load_rows(source) == [{"horse": "A", "odds": "2.0"}]
    assert build_feature_rows([]) == []
    assert compute_ev([]) == []

    empty = tmp_path / "empty.csv"
    save_ev([], empty)
    assert empty.read_text(encoding="utf-8") == ""

    output = tmp_path / "nested" / "ev.csv"
    save_ev([{"horse": "A", "ev": "1.2"}], output)
    assert load_rows(output) == [{"horse": "A", "ev": "1.2"}]
    assert _safe_int("bad") == 999
    assert ev_to_float(object(), 7.0) == 7.0


def test_short_sprint_non_closer_reaches_neutral_tail() -> None:
    assert (
        _short_sprint_front_density_adjustment(
            {"target_surface": "芝", "target_distance": "1200", "front_rate": "0.1", "closing_strength": "0.2"},
            pace_mix_high=0.5,
            front_density=0.7,
            front_competitor_count=4,
        )
        == 0.0
    )


def test_repair_action_returns_immediately_for_complete_history(tmp_path: Path) -> None:
    action = MissingHistoryRepairAction(tmp_path / "manual.csv", tmp_path / "template.csv")
    horse = SimpleNamespace(race_id="R", horse_id="H", horse_name="Horse", horse_url="https://horse")
    rows = [{"run_index": str(index)} for index in range(1, 6)]
    result = action.build_result(horse, rows=rows, reason="recovered", source_counts={"cache": 5})
    assert result.rows == rows
    assert not result.requires_manual_input
    assert result.repair_actions[0]["status"] == "repaired"


def test_live_odds_invalid_rows_and_key_edges() -> None:
    rows = [
        {"race_id": "", "bet_type": "win", "combination": "1"},
        {"race_id": "R", "bet_type": "", "combination": "1"},
        {"race_id": "R", "bet_type": "win", "combination": "", "captured_at": "2"},
        {"race_id": "R", "bet_type": "win", "combination": "1", "captured_at": "2"},
        {"race_id": "R", "bet_type": "win", "combination": "1", "captured_at": "1"},
    ]
    assert build_live_odds_lookup(rows)["R"][("win", "1")]["captured_at"] == "2"
    assert build_live_odds_lookup(
        [{"race_id": "R", "bet_type": "win", "combination": "1", "snapshot_id": "s", "snapshot_complete": False}]
    ) == {}
    assert _is_true(True)
    assert not _is_true(False)
    assert live_combo_key("win", ["bad", "0"]) == ""
    assert live_combo_key("umatan", ["2", "1"]) == "2>1"
    assert live_combo_key("wide", ["2", "1"]) == "1-2"
    assert live_combo_key("place", ["2", "1"]) == "2"
    assert live_odds_value({}) == 0.0
    assert odds_to_float(object(), 3.0) == 3.0


def test_portfolio_empty_formation_and_invalid_values() -> None:
    formation = {
        "ticket_shape": "formation",
        "point_count": "0",
        "points": ["1>2>3", "1>3>2"],
        "stake": "500",
        "stake_per_point": "200",
        "trifecta_odds_min": "3",
        "trifecta_odds_max": "5",
    }
    assert portfolio_ev([]) == 0.0
    assert not portfolio_no_gami([])
    assert ticket_point_count(formation) == 2
    assert ticket_stake_unit(formation) == 200
    assert ticket_return_if_hit(formation) == 600
    assert ticket_max_return_if_hit(formation) == 1000
    adjusted = with_adjusted_stake(formation, 550)
    assert adjusted["stake"] == 400
    assert adjusted["stake_per_point"] == 200
    assert adjusted["min_return_if_hit"] == 600
    assert adjusted["max_return_if_hit"] == 1000
    assert ticket_max_return_if_hit({"stake": "100", "win_odds": "2"}) == 200
    assert portfolio_summary([])["no_gami"] is False
    assert portfolio_to_float(object(), 4.0) == 4.0


def test_legacy_betting_and_evaluator_outputs(tmp_path: Path) -> None:
    with patch("src.betting.generate_tickets", return_value={"tickets": [{"bet_type": "win"}]}) as generate:
        assert build_tickets([], [{"horse": "A"}], bankroll_per_race=2000, min_ev=1.2, max_bets=3) == [
            {"bet_type": "win"}
        ]
    assert generate.call_args.kwargs["prefer_wide"] is False

    tickets_path = tmp_path / "nested" / "tickets.json"
    save_tickets([{"horse": "A"}], tickets_path)
    assert json.loads(tickets_path.read_text(encoding="utf-8")) == [{"horse": "A"}]

    probs = [
        {"race_id": "R", "horse_id": "2", "horse_number": "2", "horse_name": "B", "win_prob": "0.3", "win_odds": "4", "ev_win": "1.2"},
        {"race_id": "R", "horse_id": "1", "horse_number": "1", "horse_name": "A", "win_prob": "0.7", "win_odds": "1.5", "ev_win": "1.05"},
    ]
    save_outputs([{"race_id": "R"}], probs, [{"horse": "B"}], tmp_path / "result")
    assert (tmp_path / "result" / "predictions.csv").exists()
    assert (tmp_path / "result" / "ev_ranking.csv").read_text(encoding="utf-8").splitlines()[1].split(",")[1] == "2"
    assert json.loads((tmp_path / "result" / "tickets.json").read_text(encoding="utf-8")) == [{"horse": "B"}]
    save_outputs([], [], [], tmp_path / "empty-result")


def test_legacy_features_reads_and_sorts_csvs(tmp_path: Path) -> None:
    history = tmp_path / "history.csv"
    entries = tmp_path / "entries.csv"
    odds = tmp_path / "odds.csv"
    _write_csv(history, ["horse_id", "position"], [{"horse_id": "H1", "position": "1"}])
    _write_csv(
        entries,
        [
            "race_id", "horse_id", "horse_name", "horse_number", "assigned_weight", "current_popularity",
            "current_jockey", "target_track", "target_race_date", "target_surface", "target_distance",
            "target_weather", "target_track_condition", "target_conditions_captured_at", "horse_country",
        ],
        [
            {"race_id": "R", "horse_id": "H2", "horse_name": "B", "horse_number": "2"},
            {"race_id": "R", "horse_id": "H1", "horse_name": "A", "horse_number": "1"},
        ],
    )
    _write_csv(odds, ["race_id", "horse_id", "win_odds"], [{"race_id": "R", "horse_id": "H1", "win_odds": "2.5"}])
    with patch("src.features.summarize_history_rows", return_value={("H1",): {"count": 1}}), patch(
        "src.features.build_feature_row", side_effect=lambda current, summary: {**current, "summary": summary}
    ):
        rows = build_features(str(history), str(entries), str(odds))
    assert [row["horse_number"] for row in rows] == ["1", "2"]
    assert rows[0]["current_odds"] == "2.5"
    assert rows[1]["summary"] == {}


def test_deadline_invalid_format_and_naive_conversion() -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        race_post_datetime({"race_date": "not-a-date", "post_time": "bad"})
    assert _aware_jst(datetime(2026, 1, 1, 12, 0)).tzinfo is not None


def test_agent_wrappers_and_race_id_edges(tmp_path: Path) -> None:
    with patch("src.agents.analyzer.build_feature_rows", return_value=[{"x": 1}]) as build:
        assert AnalyzerAgent().run([{"horse": "A"}], odds_snapshots=[{"odds": "2"}]) == {"feature_rows": [{"x": 1}]}
    build.assert_called_once()

    settings = WorkflowSettings()
    with patch("src.agents.bet_builder.generate_tickets", return_value={"tickets": []}) as generate:
        assert BetBuilderAgent(settings).run([], combo_odds=[]) == {"tickets": []}
    assert generate.called

    with patch("src.agents.ev_calculator.compute_ev", return_value=[{"ev": "1"}]) as compute:
        assert EVCalculatorAgent().run([{"x": 1}]) == {"ev_rows": [{"ev": "1"}]}
    assert compute.called
    with patch("src.agents.simulator.simulate_race_scenarios", return_value=[{"pace": "mid"}]):
        assert SimulatorAgent().run([]) == {"scenario_rows": [{"pace": "mid"}]}

    assert _race_id_from_config({"race_date": "2026-08-29", "track": "札幌", "race_number": "7"}) == "20260829_札幌_07"
    assert _race_id_from_config({}) == ""
    assert _race_order_from_configs([{"race_id": "R"}, {}]) == ["R"]
    assert race_to_float(None, 4.0) == 4.0
    assert race_to_float(object(), 5.0) == 5.0

    config = _scrape_config(tmp_path)
    assert _read_csv(config.entries_csv) == []
    config.entries_csv.write_text("", encoding="utf-8")
    assert _read_csv(config.entries_csv) == []
    assert _read_json(config.quality_report_path) == {}
    config.quality_report_path.write_text('{"ok": true}', encoding="utf-8")
    assert _read_json(config.quality_report_path) == {"ok": True}


def test_data_collector_runs_and_always_closes(tmp_path: Path) -> None:
    config = _scrape_config(tmp_path)
    _write_csv(config.entries_csv, ["horse"], [{"horse": "A"}])
    _write_csv(config.odds_snapshots_csv, ["odds"], [{"odds": "2"}])
    _write_csv(config.combo_odds_csv, ["combination"], [{"combination": "1-2"}])
    config.quality_report_path.write_text('{"complete": true}', encoding="utf-8")
    pipeline = Mock()
    pipeline.run.return_value = [{"history": "row"}]
    with patch("src.agents.data_collector.JRAPipeline", return_value=pipeline):
        payload = OverseasDataCollectorAgent(config).run([{"race_id": "R"}])
    assert payload["collector_key"] == "overseas"
    assert payload["aggressive_repair"] is True
    assert payload["entries"] == [{"horse": "A"}]
    pipeline.close.assert_called_once_with()

    pipeline.run.side_effect = RuntimeError("boom")
    pipeline.close.reset_mock()
    with patch("src.agents.data_collector.JRAPipeline", return_value=pipeline), pytest.raises(RuntimeError):
        DataCollectorAgent(config).run([])
    pipeline.close.assert_called_once_with()


def test_live_snapshot_error_and_helper_paths(tmp_path: Path) -> None:
    config = _scrape_config(tmp_path)
    now = datetime.fromisoformat("2026-08-29T05:00:00+00:00")
    collector = LiveSnapshotCollector(config, now=lambda: now)
    collector.scraper = Mock(config=config)
    collector.parser = Mock()
    collector.scraper.fetch.return_value = ""
    with pytest.raises(RuntimeError, match="detail refresh failed"):
        collector.collect(
            {"race_id": "R", "source_url": "https://race"},
            deadline_at=datetime.fromisoformat("2026-08-29T05:10:00+00:00"),
        )
    collector.close()
    collector.scraper.close.assert_called_once_with()

    assert _race_from_config(
        {"race_date": "2026-08-29", "track": "札幌", "race_number": "7", "source_url": "https://race"}
    ).race_id == "20260829_札幌_07"
    with pytest.raises(ValueError, match="source_url"):
        _race_from_config({})
    assert _odds_displayed_at("<p>no timestamp</p>", "2026-08-29") == ""
    assert _odds_displayed_at("<p>99時99分現在オッズ</p>", "2026-08-29") == ""
    assert _dedupe_odds(
        [
            {"race_id": "", "bet_type": "win", "combination": "1"},
            {"race_id": "R", "bet_type": "win", "combination": "1"},
            {"race_id": "R", "bet_type": "win", "combination": "1"},
        ]
    ) == [{"race_id": "R", "bet_type": "win", "combination": "1"}]
    assert snapshot_as_utc(datetime(2026, 1, 1)).tzinfo is not None


def test_live_snapshot_odds_failures_and_deadline_budgets(tmp_path: Path) -> None:
    config = _scrape_config(tmp_path)
    now = datetime.fromisoformat("2026-08-29T05:00:00+00:00")
    deadline = datetime.fromisoformat("2026-08-29T05:10:00+00:00")
    collector = LiveSnapshotCollector(config, now=lambda: now)
    collector.scraper = Mock(config=config)
    collector.parser = Mock()
    race = _race_from_config({"race_id": "R", "race_date": "2026-08-29", "source_url": "https://race"})

    collector.parser.extract_initial_odds_cname.return_value = ""
    issues: list[object] = []
    assert collector._collect_odds(
        race, "race", snapshot_id="s", deadline_at=deadline, emit_reserve_seconds=10, issues=issues
    ) == ([], [])
    assert issues[0].code == "odds_link_missing"

    collector.parser.extract_initial_odds_cname.return_value = "same"
    collector.scraper.fetch_post.return_value = ""
    issues = []
    rows, official = collector._collect_odds(
        race, "race", snapshot_id="s", deadline_at=deadline, emit_reserve_seconds=10, issues=issues
    )
    assert rows == [] and official == []
    assert [issue.code for issue in issues] == ["odds_page_unavailable"]
    assert collector.scraper.fetch_post.call_count == 1

    collector._now = lambda: deadline
    with pytest.raises(LiveSnapshotDeadlineExceeded, match="budget exhausted"):
        collector._ensure_budget(deadline, 0)
    collector._now = lambda: datetime.fromisoformat("2026-08-29T05:09:59.500000+00:00")
    with pytest.raises(LiveSnapshotDeadlineExceeded, match="insufficient time"):
        collector._ensure_budget(deadline, 0)


def _response(content: bytes, *, text: str = "fallback") -> Mock:
    response = Mock()
    response.content = content
    response.apparent_encoding = "utf-8"
    response.text = text
    response.raise_for_status.return_value = None
    return response


def test_scraper_decode_fetch_cache_and_network_paths(tmp_path: Path) -> None:
    config = _scrape_config(tmp_path)
    config.max_retries = 2
    config.delay_seconds = 0
    config.ensure_dirs()
    scraper = JRAScraper(config)

    assert scraper._decode_japanese_html(_response("日本".encode("euc_jp"))) == "日本"
    assert scraper._decode_japanese_html(_response("日本".encode("shift_jis"))) == "日本"
    assert scraper._decode_japanese_html(_response(b"\xff", text="decoded fallback")) == "decoded fallback"

    scraper.memory_cache["https://memory"] = "memory"
    assert scraper.fetch("https://memory") == "memory"
    disk_path = config.raw_dir / "disk.html"
    disk_path.write_text("disk", encoding="utf-8")
    assert scraper.fetch("https://disk", "disk.html") == "disk"
    assert scraper.fetch("https://miss", "miss.html", cache_only=True) is None

    scraper.session = Mock()
    scraper.session.get.return_value = _response("成功".encode("euc_jp"))
    with patch("jra_scraper.scraper.time.sleep"):
        assert scraper.fetch("https://success", "success.html", use_cache=False) == "成功"
    assert (config.raw_dir / "success.html").read_text(encoding="utf-8") == "成功"

    scraper.session.get.side_effect = requests.RequestException("network")
    with patch("jra_scraper.scraper.time.sleep"):
        assert scraper.fetch("https://failure", "failure.html", use_cache=False) is None
    assert scraper.session.get.call_count == 3

    with patch.object(scraper, "fetch", return_value="relative") as fetch:
        assert scraper.fetch_relative("/path", cache_only=True) == "relative"
    assert fetch.call_args.args[0].endswith("/path")
    assert scraper._resolve_raw_path("https://hash", None).name.startswith("url_")
    scraper.close()


def test_scraper_post_cache_network_and_failure_paths(tmp_path: Path) -> None:
    config = _scrape_config(tmp_path)
    config.max_retries = 2
    config.delay_seconds = 0
    config.ensure_dirs()
    scraper = JRAScraper(config)
    url = "https://post"
    data = {"CNAME": "x"}
    key = f"POST {url} {sorted(data.items())}"

    scraper.memory_cache[key] = "memory"
    assert scraper.fetch_post(url, data) == "memory"
    scraper.memory_cache.clear()
    (config.raw_dir / "post.html").write_text("disk", encoding="utf-8")
    assert scraper.fetch_post(url, data, "post.html") == "disk"
    assert scraper.fetch_post(url, {"CNAME": "missing"}, "missing.html", cache_only=True) is None

    scraper.session = Mock()
    scraper.session.post.return_value = _response("成功".encode("euc_jp"))
    with patch("jra_scraper.scraper.time.sleep"):
        assert scraper.fetch_post(url, data, "success.html", use_cache=False) == "成功"
    scraper.session.post.side_effect = requests.RequestException("network")
    with patch("jra_scraper.scraper.time.sleep"):
        assert scraper.fetch_post(url, data, "failure.html", use_cache=False) is None


def test_feature_engineering_defensive_helpers() -> None:
    assert summarize_live_odds_rows([{"race_id": "", "horse_number": ""}]) == {}
    assert _weighted_mean([], [], default=9.0) == 9.0
    assert _front_running_score("not-a-position") == 0.5
    assert _surface_from_distance_field("障3000") == "障害"
    assert _condition_match_score({"良": 1}, "unknown") == 0.5
    assert _condition_match_score({"unknown": 1}, "良") == 0.5
    assert _condition_confidence({"良": 1}, "unknown") == 0.0
    assert _condition_confidence({"unknown": 1}, "良") == 0.0
    assert _parse_timestamp("").tzinfo is not None
    assert _parse_timestamp("bad").tzinfo is not None
    assert _parse_timestamp("2026-01-01T00:00:00").tzinfo is not None
    point = _parse_timestamp("2026-01-01T00:00:00+00:00")
    assert _relative_slope([(0.0, point), (1.0, point)]) == 0.0
    assert len(_recency_weights(10)) == 10
    assert _fmt_float(float("inf")) == "0"
    assert _country_value_score("AUS", is_overseas=True) == 0.06
    assert _country_value_score("XYZ", is_overseas=True) == 0.03
    assert _country_value_score("", is_overseas=True) == 0.0
    assert feature_safe_int(object()) == 999


def test_model_empty_single_iteration_and_calibration_edges() -> None:
    weights = ModelWeights(monte_carlo_iterations=1)
    assert _monte_carlo_win_probs([], [], weights, race_id="R") == ([], [], [])
    probs, stds, calibration = _monte_carlo_win_probs(
        [{"model_score": "1", "current_odds": "2"}], [1.0], weights, race_id="R"
    )
    assert probs == [1.0] and stds == [0.0] and calibration
    assert _market_probs([{"current_odds": ""}]) == []
    assert _blend_probabilities([], [], [], weights) == ([], [])
    normalized, no_market = _blend_probabilities([{}], [2.0], [], weights)
    assert normalized == [1.0] and no_market[0]["market_prob"] == 0.0
    assert _predict_structural_odds({}, current_odds=0.0, fair_odds=2.0) == 0.0
    assert _predict_live_odds({"odds_latest": "0"}, current_odds=0.0, fallback_odds=3.0) == 3.0
    assert _normalize_probs([0.0, -1.0]) == [0.0, 0.0]
    assert _apply_probability_caps([0.5], []) == [0.5]
    assert _apply_probability_caps([0.5, 0.5], [0.0, 0.0]) == [0.0, 0.0]
    assert _apply_probability_caps([0.5, 0.5], [0.2, 0.3]) == pytest.approx([0.4, 0.6])
    assert _apply_probability_caps([0.0, 0.0], [0.5, 0.5]) == [0.5, 0.5]
    assert sum(_apply_probability_caps([0.99, 0.01], [0.2, 1.0])) == pytest.approx(1.0)
    assert _market_divergence_shrink(0.0, 0.2, 0.4, band="favorite") == 0.4
    assert _probability_cap({}, market_prob=0.0, band="favorite") == 1.0
    assert _softmax([]) == []
    assert model_to_float(object(), 7.0) == 7.0


def test_track_bias_zero_weight_and_match_rejections(tmp_path: Path) -> None:
    zero_frame = {
        "frame_adjustments": [
            {"scope": "zero", "confidence": 0, "sample_size": 10, "frame_bias": {"1": 0.1}}
        ]
    }
    assert learned_frame_adjustment(
        track="X", surface="芝", distance=1200, frame_number="1", learning_config=zero_frame
    )["learned_frame_bias"] == 0.0
    zero_pace = {
        "pace_adjustments": [
            {"scope": "zero", "confidence": 0, "sample_size": 10, "style_bias": {"front": -0.1}}
        ]
    }
    assert course_pace_adjustment(
        track="X", surface="芝", distance=1200, track_condition="良", pace_mix_high=0.8,
        front_rate=0.9, closing_strength=0.1, front_competitor_count=4, learning_config=zero_pace,
    )["course_pace_adjustment"] == 0.0

    with patch("src.track_bias.COURSE_LEARNING_CONFIG_PATH", tmp_path / "missing.json"):
        _load_course_learning_config.cache_clear()
        assert _load_course_learning_config()["pace_adjustments"] == []
    _load_course_learning_config.cache_clear()

    assert not _pace_profile_matches(
        {"enabled": False}, track="X", surface="芝", distance=1200, track_condition="良", pace_mix_high=1, front_competitor_count=5
    )
    assert not _pace_profile_matches(
        {"track_condition": "重"}, track="X", surface="芝", distance=1200, track_condition="良", pace_mix_high=1, front_competitor_count=5
    )
    assert not _pace_profile_matches(
        {"high_pace_min": 0.8}, track="X", surface="芝", distance=1200, track_condition="良", pace_mix_high=0.2, front_competitor_count=5
    )
    assert not _frame_profile_matches({"enabled": False}, track="X", surface="芝", distance=1200)
    assert not _frame_profile_matches({"distance": 1400, "distance_tolerance": 10}, track="X", surface="芝", distance=1200)
    assert _profile_specificity({"track_condition": "重"}) == 1
    assert _closing_deficiency_adjustment({}, style="front", closing_strength=0.0) == 0.0
    assert bias_to_float(object(), 8.0) == 8.0


def test_win5_private_edge_modes_and_helpers() -> None:
    assert _default_max_points("win5_under_10") == 10
    assert _default_max_points("win5_balanced") == 500
    assert _default_max_points("win5_value") == 120
    assert _ordered_race_groups([{"race_id": ""}]) == []
    assert _candidate_allocations("win5_compact", 1) == [(1, 1, 1, 1, 1)]
    assert _candidate_allocations("win5_balanced", 1) == [(1, 1, 1, 1, 1)]
    assert _candidate_allocations("win5_value", 1) == [(1, 1, 1, 1, 1)]
    assert _popularity_mix(
        [{"horses": [{"popularity": "5"}, {"popularity": "10"}]}]
    ) == {"favorite": 0, "mid": 1, "long": 1}
    assert _under_10_fixed_risk({"ranked": []}) == 0.0
    assert _normalized_entropy([1.0]) == 0.0
    assert win5_to_float(None, 2.0) == 2.0
    assert win5_to_float(object(), 3.0) == 3.0


def test_validation_fallback_normalizers() -> None:
    assert _normalize_horse_id("", " !!! ") == "unknown_horse"
    assert _normalize_date("2026/08/29 12:30") == "2026-08-29"
    assert _normalize_body_weight_status("計不", "") == "not_measured"
    assert _normalize_time("bad:time") == "bad:time"
    assert _normalize_passing_order("→") == ""
    assert validation_safe_int("bad") == 999
