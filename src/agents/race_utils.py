from __future__ import annotations


def _race_order_from_configs(race_configs: list[dict[str, object]]) -> list[str]:
    order: list[str] = []
    for config in race_configs:
        race_id = _race_id_from_config(config)
        if race_id:
            order.append(race_id)
    return order


def _race_id_from_config(config: dict[str, object]) -> str:
    explicit = str(config.get("race_id", "")).strip()
    if explicit:
        return explicit

    race_date = str(config.get("race_date", "")).replace("-", "").strip()
    track = str(config.get("track", "")).strip()
    race_number = str(config.get("race_number", "")).strip()
    if race_date and track and race_number:
        return f"{race_date}_{track}_{int(_to_float(race_number)):02d}"
    return ""


def _to_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "None"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
