from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from analysis.ev import _short_sprint_front_density_adjustment
from jra_scraper.config import ScrapeConfig
from jra_scraper.live_snapshot import LiveSnapshotCollector
from jra_scraper.models import RaceLink
from jra_scraper.parser import JRAParser
from jra_scraper.pipeline import _coalesce_history_rows
from jra_scraper.validation import _normalize_date
from src.feature_engineering import summarize_live_odds_rows
from src.model import _apply_probability_caps
from src.track_bias import _find_profile
from strategy.live_odds import latest_complete_odds_rows
from strategy.portfolio import _to_float as portfolio_to_float, canonical_ticket_ev
from strategy.win5 import _default_max_points


def _config(tmp_path) -> ScrapeConfig:
    return ScrapeConfig(
        output_csv=tmp_path / "processed" / "history.csv",
        entries_csv=tmp_path / "processed" / "entries.csv",
        odds_snapshots_csv=tmp_path / "processed" / "odds.csv",
        combo_odds_csv=tmp_path / "processed" / "combo.csv",
        raw_dir=tmp_path / "raw",
        state_path=tmp_path / "processed" / "state.json",
        quality_report_path=tmp_path / "report" / "quality.json",
        missing_history_requests_path=tmp_path / "report" / "missing.json",
        manual_history_template_csv=tmp_path / "report" / "manual.csv",
        manual_history_csv=tmp_path / "manual" / "history.csv",
        stages_dir=tmp_path / "report" / "stages",
    )


def test_short_sprint_closer_and_small_helpers() -> None:
    adjustment = _short_sprint_front_density_adjustment(
        {
            "target_surface": "芝",
            "target_distance": "1200",
            "front_rate": "0.2",
            "closing_strength": "0.6",
        },
        pace_mix_high=0.6,
        front_density=0.6,
        front_competitor_count=4,
    )
    assert adjustment > 0
    assert _normalize_date("not-a-date") == "not/a/date"
    assert summarize_live_odds_rows([{"race_id": "R", "horse_number": ""}]) == {}
    assert _apply_probability_caps([0.0, 0.0], [0.0, 1.0]) == [0.0, 1.0]
    assert portfolio_to_float(None, 4.0) == 4.0
    assert portfolio_to_float(object(), 4.0) == 4.0
    assert _default_max_points("win5_compact") == 60


def test_live_odds_and_formation_integrity_rejection_edges() -> None:
    rows = [
        {"race_id": "", "snapshot_id": "S", "snapshot_complete": True},
        {"race_id": "B", "snapshot_id": "S", "snapshot_complete": True,
         "bet_type": "win", "combination": "1", "captured_at": "2026-01-01T00:00:00Z"},
        {"race_id": "A", "snapshot_id": "S", "snapshot_complete": True,
         "bet_type": "win", "combination": "1", "captured_at": "2026-01-01T00:00:00Z"},
    ]
    assert [row["race_id"] for row in latest_complete_odds_rows(rows)] == ["A", "B"]

    base = {
        "odds_source": "jra_live",
        "ticket_shape": "formation",
        "point_count": 1,
        "stake": 100,
        "stake_per_point": 100,
    }
    assert canonical_ticket_ev(dict(base, points=[])) == 0.0
    assert canonical_ticket_ev(dict(base, points=["bad"])) == 0.0
    assert canonical_ticket_ev(
        dict(base, points=[{"hit_prob": "0.1", "odds": "10", "odds_source": "estimated"}])
    ) == 0.0


def test_parser_invalid_cname_and_subunit_integer_odds() -> None:
    parser = JRAParser("https://jra.test")
    html = '<a onclick="open(\'/JRADB/accessO.html\')">単勝・複勝</a>'
    assert parser.extract_odds_cnames(html) == {}

    row = BeautifulSoup("<table><tr><td></td></tr></table>", "html.parser").tr
    parsed = parser._apply_entry_fallbacks(
        {"frame_number": "8", "horse_number": "9", "assigned_weight": "55"},
        row,
        ["1", "3.5"],
        False,
        allow_odds_fallback=True,
    )
    assert parsed["current_odds"] == "3.5"


def test_live_snapshot_skips_missing_and_repeated_odds_pages(tmp_path) -> None:
    now = datetime(2026, 8, 29, 5, 0, tzinfo=timezone.utc)
    collector = LiveSnapshotCollector(_config(tmp_path), now=lambda: now)
    collector.scraper = Mock()
    collector.scraper.fetch_post.return_value = "<html></html>"
    collector.parser = Mock()
    collector.parser.extract_initial_odds_cname.return_value = "same"
    collector.parser.parse_odds_page.return_value = []
    collector.parser.extract_odds_cnames.return_value = {"wakuren": "same"}

    rows, times = collector._collect_odds(
        RaceLink("R", "Race", "https://race", race_date="2026-08-29"),
        "<html></html>",
        snapshot_id="S",
        deadline_at=now + timedelta(minutes=10),
        emit_reserve_seconds=0,
        issues=[],
    )
    assert rows == [] and times == []
    assert collector.scraper.fetch_post.call_count == 1


def test_history_coalesce_and_track_profile_distance_skip() -> None:
    assert _coalesce_history_rows(
        {"run_index": "1", "jockey": "", "last_3f": "35", "last_3f_source": "observed"},
        {"run_index": "2", "jockey": "J", "last_3f": "36", "last_3f_source": "observed"},
    )["jockey"] == "J"

    config = {
        "profiles": [
            {"track": "札幌", "surface": "芝", "distance": 2000},
            {"track": "札幌", "surface": "芝", "distance": 1200, "name": "match"},
        ]
    }
    with patch("src.track_bias._load_config", return_value=config):
        assert _find_profile(track="札幌", surface="芝", distance=1200)["name"] == "match"
