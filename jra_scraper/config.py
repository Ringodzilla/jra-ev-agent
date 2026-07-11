from dataclasses import dataclass
from pathlib import Path


@dataclass
class ScrapeConfig:
    """Configuration for scraping and persistent pipeline operation."""

    base_url: str = "https://www.jra.go.jp"
    race_list_path: str = "/JRADB/accessS.html"

    output_csv: Path = Path("data/processed/race_last5.csv")
    entries_csv: Path = Path("data/processed/race_entries.csv")
    odds_snapshots_csv: Path = Path("data/processed/live_odds_snapshots.csv")
    combo_odds_csv: Path = Path("data/processed/live_combo_odds.csv")
    raw_dir: Path = Path("data/raw")
    state_path: Path = Path("data/processed/pipeline_state.json")
    quality_report_path: Path = Path("report/data_quality.json")
    missing_history_requests_path: Path = Path("report/missing_history_requests.json")
    manual_history_template_csv: Path = Path("report/manual_history_template.csv")
    manual_history_csv: Path = Path("data/manual/horse_history_overrides.csv")
    stages_dir: Path = Path("report/stages")

    timeout: int = 20
    max_retries: int = 3
    delay_seconds: float = 1.2

    def ensure_dirs(self) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.output_csv.parent.mkdir(parents=True, exist_ok=True)
        self.entries_csv.parent.mkdir(parents=True, exist_ok=True)
        self.odds_snapshots_csv.parent.mkdir(parents=True, exist_ok=True)
        self.combo_odds_csv.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.quality_report_path.parent.mkdir(parents=True, exist_ok=True)
        self.missing_history_requests_path.parent.mkdir(parents=True, exist_ok=True)
        self.manual_history_template_csv.parent.mkdir(parents=True, exist_ok=True)
        self.manual_history_csv.parent.mkdir(parents=True, exist_ok=True)
        self.stages_dir.mkdir(parents=True, exist_ok=True)
