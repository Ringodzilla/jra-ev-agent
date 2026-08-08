from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class ParserIssue:
    stage: str
    severity: str
    code: str
    message: str
    context: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class RaceLink:
    race_id: str
    race_name: str
    race_url: str
    race_date: str = ""
    track: str = ""
    race_number: str = ""
    target_surface: str = ""
    target_distance: str = ""
    target_weather: str = ""
    target_track_condition: str = ""


@dataclass
class HorseEntry:
    race_id: str
    race_name: str
    horse_id: str
    horse_name: str
    horse_url: str
    frame_number: str = ""
    horse_number: str = ""
    current_jockey: str = ""
    assigned_weight: str = ""
    current_body_weight: str = ""
    body_weight_change: str = ""
    body_weight_status: str = "unpublished"
    current_odds: str = ""
    current_popularity: str = ""
    target_track: str = ""
    target_race_date: str = ""
    target_race_number: str = ""
    target_surface: str = ""
    target_distance: str = ""
    target_weather: str = ""
    target_track_condition: str = ""
    target_conditions_captured_at: str = ""
    horse_country: str = ""
    embedded_history: list[dict[str, str]] = field(default_factory=list)
