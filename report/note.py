from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re


def build_note_article(
    race_name: str,
    ev_rows: list[dict[str, object]],
    tickets: dict[str, object],
    *,
    review: dict[str, object] | None = None,
    quality_report: dict[str, object] | None = None,
    race_config: dict[str, object] | None = None,
    prediction_context: dict[str, object] | None = None,
) -> dict[str, object]:
    review = review or {}
    quality_report = quality_report or {}
    race_config = race_config or {}
    prediction_context = prediction_context or {}

    title = _build_article_title(race_name, race_config, ev_rows)
    review_status = str(review.get("status", "UNKNOWN"))
    sorted_by_ev = _sort_rows(ev_rows, key="ev_current")
    sorted_by_prob = _sort_rows(ev_rows, key="win_prob")
    ticket_rows = list(tickets.get("tickets") or [])
    reference_candidate_labels = _reference_candidate_labels(ticket_rows, tickets)
    reference_candidate_details = _reference_candidate_details(ticket_rows, tickets)
    publish_ready = review_status == "OK"
    status = "ready" if publish_ready else "hold"
    prediction_timestamp = _resolve_prediction_timestamp(prediction_context, quality_report)
    headline = _headline(race_name, review_status, reference_candidate_labels, ticket_rows)

    markdown_lines = [
        f"# {title}",
        "",
        headline,
        "",
        "## レース概要",
        *_race_meta_lines(race_name, race_config, ev_rows, prediction_timestamp),
        "",
        "## 結論",
        *_conclusion_lines(review, ticket_rows, reference_candidate_details, sorted_by_ev),
        "",
        "## 印",
        *_mark_lines(ticket_rows, sorted_by_prob, sorted_by_ev),
        "",
        "## 買い目",
        *_ticket_lines(review, ticket_rows, reference_candidate_details),
        "",
        "## AI評価上位",
        *_ranking_lines(sorted_by_ev),
        "",
        "## データチェック",
        *_data_quality_lines(review, quality_report, ticket_rows, reference_candidate_details),
        "",
        "## メモ",
        *_risk_lines(review_status, sorted_by_prob, sorted_by_ev),
        "",
        "※ 最終判断は直前オッズと馬場状態を確認して行ってください。",
    ]
    markdown = "\n".join(markdown_lines)

    return {
        "status": status,
        "publish_ready": publish_ready,
        "title": title,
        "headline": headline,
        "ticket_count": len(ticket_rows),
        "review_status": review_status,
        "prediction_timestamp": prediction_timestamp,
        "markdown": markdown,
    }


def generate_note_markdown(
    race_name: str,
    ev_rows: list[dict[str, object]],
    tickets: dict[str, object],
    *,
    review: dict[str, object] | None = None,
    quality_report: dict[str, object] | None = None,
    race_config: dict[str, object] | None = None,
    prediction_context: dict[str, object] | None = None,
) -> str:
    article = build_note_article(
        race_name,
        ev_rows,
        tickets,
        review=review,
        quality_report=quality_report,
        race_config=race_config,
        prediction_context=prediction_context,
    )
    return str(article["markdown"])


def write_note(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _headline(
    race_name: str,
    review_status: str,
    reference_candidates: list[str],
    ticket_rows: list[dict[str, object]],
) -> str:
    if review_status == "OK" and ticket_rows:
        main_names = "、".join(_ticket_label(ticket) for ticket in ticket_rows[:2] if _ticket_label(ticket))
        return f"{race_name}を再計算しました。現時点の妙味候補は{main_names}です。"
    if review_status == "OK":
        return f"{race_name}を再計算しました。買い目候補は出ていますが、条件を厳しめに見て絞り込んでいます。"
    if reference_candidates:
        candidate_names = "、".join(reference_candidates[:2])
        return (
            f"{race_name}を再計算しました。最終判断は見送りですが、"
            f"ロジック上は{candidate_names}が参考候補として残っています。"
        )
    return f"{race_name}を再計算しました。安全に買えるだけの裏付けがまだ弱いため、今回は見送り前提で整理します。"


def _build_article_title(
    race_name: str,
    race_config: dict[str, object],
    ev_rows: list[dict[str, object]],
) -> str:
    prefix = _build_title_prefix(race_name, race_config, ev_rows)
    condition = _build_title_condition(race_config, ev_rows)
    if prefix and condition:
        return f"{prefix}競馬予想 {condition}"
    if prefix:
        return f"{prefix}競馬予想"
    return str(race_config.get("note_title") or f"{race_name} AI予想")


def _build_title_prefix(
    race_name: str,
    race_config: dict[str, object],
    ev_rows: list[dict[str, object]],
) -> str:
    race_date = str(race_config.get("race_date", "")).strip() or _first_nonempty(ev_rows, "target_race_date")
    date_label = _format_race_date_label(race_date)
    track = str(race_config.get("track", "")).strip() or _first_nonempty(ev_rows, "target_track")
    race_number = str(race_config.get("race_number", "")).strip() or _first_nonempty(ev_rows, "target_race_number")
    post_time = _normalize_post_time(str(race_config.get("post_time") or race_config.get("start_time") or "").strip())

    parts = []
    if date_label:
        parts.append(date_label)
    if track:
        parts.append(track)
    if race_number:
        parts.append(f"{race_number}R")
    if post_time:
        parts.append(f"{post_time}発走")

    header = " ".join(parts).strip()
    if not header:
        return ""
    return f"【{header}｜{race_name}】"


def _build_title_condition(race_config: dict[str, object], ev_rows: list[dict[str, object]]) -> str:
    surface = (
        str(race_config.get("surface") or race_config.get("surface_label") or race_config.get("target_surface") or "").strip()
        or _first_nonempty(ev_rows, "target_surface")
    )
    distance = (
        str(race_config.get("distance") or race_config.get("distance_label") or race_config.get("target_distance") or "").strip()
        or _first_nonempty(ev_rows, "target_distance")
    )
    surface = _normalize_surface(surface)
    distance = _normalize_distance(distance)
    if surface and distance:
        return f"{surface}{distance}"
    if surface:
        return surface
    if distance:
        return distance
    return ""


def _race_meta_lines(
    race_name: str,
    race_config: dict[str, object],
    ev_rows: list[dict[str, object]],
    prediction_timestamp: str,
) -> list[str]:
    lines = [f"- レース名: {race_name}"]
    race_date = str(race_config.get("race_date", "")).strip() or _first_nonempty(ev_rows, "target_race_date")
    track = str(race_config.get("track", "")).strip() or _first_nonempty(ev_rows, "target_track")
    race_number = str(race_config.get("race_number", "")).strip() or _first_nonempty(ev_rows, "target_race_number")
    source_url = str(race_config.get("source_url", "")).strip()
    if race_date:
        lines.append(f"- 開催日: {race_date}")
    if track or race_number:
        lines.append(f"- 開催情報: {track}{race_number}R".strip())
    if prediction_timestamp:
        lines.append(f"- 予想時点: {prediction_timestamp}")
    if source_url:
        lines.append(f"- 参照元: {source_url}")
    return lines


def _conclusion_lines(
    review: dict[str, object],
    ticket_rows: list[dict[str, object]],
    reference_candidates: list[str],
    sorted_by_ev: list[dict[str, object]],
) -> list[str]:
    review_status = str(review.get("status", "UNKNOWN"))
    if review_status != "OK":
        lines = ["- 今回は購入見送りです。"]
        lines.extend(f"- {line}" for line in _humanize_review_reason_lines(review))
        lines.extend(_reference_candidate_lines(reference_candidates))
        return lines

    if ticket_rows:
        primary = ticket_rows[0]
        lines = [
            f"- 現時点の本線は {_ticket_summary(primary)} です。",
        ]
        if len(ticket_rows) >= 2:
            secondary = ticket_rows[1]
            lines.append(f"- 次点候補は {_ticket_summary(secondary)} です。")
        lines.append(f"- 購入点数は {len(ticket_rows)} 点です。")
        return lines

    if sorted_by_ev:
        top = sorted_by_ev[0]
        lines = [
            "- reviewer は OK ですが、運用条件に合う買い目が少ないため無理打ちは避けます。",
            f"- 評価上位は {top.get('horse_number')} {top.get('horse_name')} です。",
        ]
        lines.extend(_reference_candidate_lines(reference_candidates))
        return lines
    return ["- 今回は有効な評価データが不足しています。"]


def _mark_lines(
    ticket_rows: list[dict[str, object]],
    sorted_by_prob: list[dict[str, object]],
    sorted_by_ev: list[dict[str, object]],
) -> list[str]:
    marks = ["◎", "○", "▲", "☆"]
    selected: list[dict[str, object]] = []
    seen: set[str] = set()

    del ticket_rows

    for source in (sorted_by_prob, sorted_by_ev):
        for row in source:
            horse_id = str(row.get("horse_id", "")).strip()
            if not horse_id or horse_id in seen:
                continue
            seen.add(horse_id)
            selected.append(row)
            if len(selected) >= len(marks):
                break
        if len(selected) >= len(marks):
            break

    if not selected:
        return ["- 印を出すだけの十分な候補がありません。"]

    lines = []
    for mark, row in zip(marks, selected):
        lines.append(
            f"- {mark} {row.get('horse_number')} {row.get('horse_name')} "
            f"(勝率 {_fmt_pct(row.get('win_prob'))}, EV {_fmt_value(row.get('ev_current') or row.get('ev'))})"
        )
    return lines


def _ticket_lines(
    review: dict[str, object],
    ticket_rows: list[dict[str, object]],
    reference_candidates: list[str],
) -> list[str]:
    review_status = str(review.get("status", "UNKNOWN"))
    if review_status != "OK":
        lines = ["- 今回は見送りです。"]
        lines.extend(f"- {line}" for line in _humanize_review_reason_lines(review))
        lines.extend(_reference_candidate_lines(reference_candidates, prefix="- 参考候補: "))
        return lines

    if not ticket_rows:
        lines = ["- 期待値条件を満たす正式な買い目はありません。"]
        lines.extend(_reference_candidate_lines(reference_candidates, prefix="- 参考候補: "))
        return lines

    lines = []
    for ticket in ticket_rows:
        lines.append(_ticket_detail_line(ticket))
    return lines


def _ranking_lines(sorted_by_ev: list[dict[str, object]]) -> list[str]:
    if not sorted_by_ev:
        return ["- 評価対象なし"]

    lines = []
    for idx, row in enumerate(sorted_by_ev[:5], start=1):
        lines.append(
            f"- {idx}位: {row.get('horse_number')} {row.get('horse_name')}"
            f" / 勝率 {_fmt_pct(row.get('win_prob'))}"
            f" / 現在オッズ {_fmt_odds(row.get('current_odds'))}"
            f" / EV {_fmt_value(row.get('ev_current') or row.get('ev'))}"
            f" / 予測オッズ {_fmt_odds(row.get('predicted_odds'))}"
            f" ({row.get('predicted_odds_source', '')})"
        )
    return lines


def _data_quality_lines(
    review: dict[str, object],
    quality_report: dict[str, object],
    ticket_rows: list[dict[str, object]],
    reference_candidates: list[str],
) -> list[str]:
    issue_count = int(quality_report.get("issue_count", 0) or 0)
    repaired_row_count = int(quality_report.get("repaired_row_count", 0) or 0)
    live_snapshot_count = int(quality_report.get("live_snapshot_count", 0) or 0)
    missing_current_odds = int(quality_report.get("missing_current_odds_entries", 0) or 0)
    entry_count = int(quality_report.get("entry_count", 0) or 0)

    lines = [
        f"- 最終判定: {_humanize_review_status(str(review.get('status', 'UNKNOWN')))}",
    ]
    if issue_count == 0:
        lines.append("- 出馬表と過去走の解析に大きな問題はありません。")
    else:
        lines.append(f"- 解析上の注意点が {issue_count} 件あります。")

    if repaired_row_count > 0:
        lines.append(f"- 列ずれや表の乱れは {repaired_row_count} 行ぶん補正しています。")

    if entry_count > 0 and missing_current_odds == 0:
        lines.append("- 現在オッズは全頭分を取得できています。")
    elif entry_count > 0:
        lines.append(f"- 現在オッズは {entry_count} 頭中 {entry_count - missing_current_odds} 頭ぶん取得できています。")

    if live_snapshot_count > 0:
        lines.append(f"- 当日オッズの時系列は {live_snapshot_count} 件たまっています。")
    else:
        lines.append("- 当日オッズの時系列はまだ十分にたまっていません。")

    if ticket_rows:
        lines.append(f"- 運用条件を満たした買い目候補は {len(ticket_rows)} 点です。")
    elif reference_candidates:
        lines.append(f"- 正式な買い目候補はありませんが、参考候補は {len(reference_candidates)} 点あります。")
    else:
        lines.append("- 今回は運用条件を満たす買い目候補はありませんでした。")

    return lines


def _risk_lines(
    review_status: str,
    sorted_by_prob: list[dict[str, object]],
    sorted_by_ev: list[dict[str, object]],
) -> list[str]:
    lines = []
    if review_status != "OK":
        lines.append("- reviewer が NG のときは無理に買わず、直前の再取得を優先します。")
    if sorted_by_prob:
        top_prob = sorted_by_prob[0]
        lines.append(
            f"- 勝率最上位は {top_prob.get('horse_number')} {top_prob.get('horse_name')} "
            f"({_fmt_pct(top_prob.get('win_prob'))}) です。"
        )
    if sorted_by_ev:
        top_ev = sorted_by_ev[0]
        lines.append(
            f"- EV最上位は {top_ev.get('horse_number')} {top_ev.get('horse_name')} "
            f"(EV {_fmt_value(top_ev.get('ev_current') or top_ev.get('ev'))}) です。"
        )
    lines.append("- 発走直前はオッズが動くので、購入前に再計算するのが安全です。")
    return lines


def _sort_rows(rows: list[dict[str, object]], *, key: str) -> list[dict[str, object]]:
    return sorted(
        list(rows),
        key=lambda row: (_to_float(row.get(key) or row.get("ev") or 0.0), _to_float(row.get("win_prob"), 0.0)),
        reverse=True,
    )


def _fmt_pct(value: object) -> str:
    return f"{_to_float(value) * 100:.1f}%"


def _fmt_value(value: object) -> str:
    return f"{_to_float(value):.3f}"


def _fmt_odds(value: object) -> str:
    number = _to_float(value)
    if number <= 0:
        return "-"
    return f"{number:.1f}倍"


def _humanize_review_status(status: str) -> str:
    if status == "OK":
        return "買い目提示OK"
    if status == "NG":
        return "見送り"
    return status or "判定保留"


def _humanize_review_reason_lines(review: dict[str, object]) -> list[str]:
    raw_reason = str(review.get("reason", "")).strip()
    divergent_rows = list(review.get("divergent_rows") or [])
    horse_names = [str(row.get("horse_name", "")).strip() for row in divergent_rows if str(row.get("horse_name", "")).strip()]
    horse_names = horse_names[:3]

    lines: list[str] = []

    if "predicted/current EV divergence detected" in raw_reason:
        if horse_names:
            lines.append(
                f"上位候補のうち {'、'.join(horse_names)} は、モデル評価に対して現在オッズとのズレが大きめでした。"
            )
        lines.append("直前のオッズ変動に対して期待値が安定しきらないため、今回は無理に買わず見送ります。")

    if "current odds are missing for every entry" in raw_reason:
        lines.append("現在オッズを十分に取得できず、期待値を安全に計算できませんでした。")

    if "high severity parser issues" in raw_reason:
        lines.append("出馬表の解析に大きな不整合が残り、予想の前提が安定しませんでした。")

    if "ticket plan contains low-confidence or sub-threshold tickets" in raw_reason:
        lines.append("候補自体は出ましたが、的中率や期待値が運用基準に届きませんでした。")

    if "ticket plan overweights extreme longshots" in raw_reason:
        lines.append("人気薄に寄りすぎる形だったため、見送り寄りの判断にしています。")

    if "probability normalization drift detected" in raw_reason:
        lines.append("勝率のバランスに不自然さが残ったため、予想の信頼度を下げています。")

    if not lines and raw_reason:
        lines.append(raw_reason)

    if not lines:
        lines.append("現時点では無理に買うほどの裏付けが揃いませんでした。")

    return _dedupe_preserve_order(lines)


def _ticket_label(ticket: dict[str, object]) -> str:
    bet_type = str(ticket.get("bet_type", "win"))
    labels = {
        "win": "単勝",
        "place": "複勝",
        "wide": "ワイド",
        "wakuren": "枠連",
        "umaren": "馬連",
        "umatan": "馬単",
        "sanrenpuku": "三連複",
        "sanrentan": "三連単",
    }
    label = labels.get(bet_type, bet_type)
    if bet_type == "wide":
        return f"{label} {_ticket_horse_display(ticket)}"
    if bet_type in {"wakuren", "umaren", "umatan", "sanrenpuku", "sanrentan"}:
        return f"{label} {_ticket_horse_display(ticket)}"
    return f"{label} {_ticket_horse_display(ticket)}"


def _ticket_summary(ticket: dict[str, object]) -> str:
    label = _ticket_label(ticket)
    stake = int(_to_float(ticket.get("stake"), 0.0))
    ev = _fmt_value(ticket.get("ev_current") or ticket.get("ev"))
    return f"{label} {stake}円 / EV {ev}"


def _ticket_detail_line(ticket: dict[str, object]) -> str:
    bet_type = str(ticket.get("bet_type", "win"))
    label = _ticket_label(ticket)
    stake = int(_to_float(ticket.get("stake"), 0.0))
    probability = _fmt_pct(ticket.get("hit_prob") or ticket.get("wide_prob") or ticket.get("win_prob"))
    if bet_type == "place":
        odds_label = "推定複勝オッズ"
        odds_value = _fmt_odds(ticket.get("place_odds_est") or ticket.get("win_odds"))
        prob_label = "的中率"
    elif bet_type == "wide":
        odds_label = "推定ワイドオッズ"
        odds_value = _fmt_odds(ticket.get("wide_odds_est") or ticket.get("predicted_wide_odds") or ticket.get("win_odds"))
        prob_label = "的中率"
    elif bet_type == "wakuren":
        odds_label = "推定枠連オッズ"
        odds_value = _fmt_odds(ticket.get("wakuren_odds_est") or ticket.get("win_odds"))
        prob_label = "的中率"
    elif bet_type == "umaren":
        odds_label = "推定馬連オッズ"
        odds_value = _fmt_odds(ticket.get("umaren_odds_est") or ticket.get("win_odds"))
        prob_label = "的中率"
    elif bet_type == "umatan":
        odds_label = "推定馬単オッズ"
        odds_value = _fmt_odds(ticket.get("umatan_odds_est") or ticket.get("win_odds"))
        prob_label = "的中率"
    elif bet_type == "sanrenpuku":
        odds_label = "推定三連複オッズ"
        odds_value = _fmt_odds(ticket.get("trio_odds_est") or ticket.get("win_odds"))
        prob_label = "的中率"
    elif bet_type == "sanrentan":
        odds_label = "推定三連単オッズ"
        odds_value = _fmt_odds(ticket.get("trifecta_odds_est") or ticket.get("win_odds"))
        prob_label = "的中率"
    else:
        odds_label = "現在オッズ"
        odds_value = _fmt_odds(ticket.get("win_odds"))
        prob_label = "勝率"
    return (
        f"- {label} {stake}円"
        f" / {prob_label} {probability}"
        f" / {odds_label} {odds_value}"
        f" / EV {_fmt_value(ticket.get('ev_current') or ticket.get('ev'))}"
    )


def _reference_candidate_lines(
    reference_candidates: list[str],
    *,
    prefix: str = "- 参考候補: ",
) -> list[str]:
    if not reference_candidates:
        return []

    lines = ["- 最終判断は見送りですが、ロジック上の参考候補は残っています。"]
    for candidate in reference_candidates[:8]:
        lines.append(prefix + candidate)
    return lines


def _reference_candidates(
    ticket_rows: list[dict[str, object]],
    tickets: dict[str, object],
) -> list[str]:
    return _reference_candidate_labels(ticket_rows, tickets)


def _reference_candidate_labels(
    ticket_rows: list[dict[str, object]],
    tickets: dict[str, object],
) -> list[str]:
    candidates: list[str] = []
    if ticket_rows:
        return [_ticket_label(ticket) for ticket in ticket_rows[:6]]

    for key, label in (
        ("fukusho", "複勝"),
        ("wide", "ワイド"),
        ("wakuren", "枠連"),
        ("umaren", "馬連"),
        ("umatan", "馬単"),
        ("sanrenpuku", "三連複"),
        ("sanrentan", "三連単"),
        ("tansho", "単勝"),
    ):
        for raw in list(tickets.get(key) or [])[:2]:
            text = str(raw).strip()
            if not text:
                continue
            if not text.startswith(label):
                text = f"{label} {text}"
            candidates.append(text)

    return _dedupe_preserve_order(candidates[:8])


def _reference_candidate_details(
    ticket_rows: list[dict[str, object]],
    tickets: dict[str, object],
) -> list[str]:
    if ticket_rows:
        return [_ticket_summary(ticket) for ticket in ticket_rows[:6]]
    return _reference_candidate_labels(ticket_rows, tickets)


def _ticket_horse_display(ticket: dict[str, object]) -> str:
    bet_type = str(ticket.get("bet_type", "win"))
    if bet_type == "wakuren":
        frame_numbers = list(ticket.get("frame_numbers") or [])
        if len(frame_numbers) >= 2:
            return "-".join(str(number) for number in frame_numbers)
    if bet_type in {"wide", "umaren", "umatan", "sanrenpuku", "sanrentan"}:
        numbers = list(ticket.get("horse_numbers") or [])
        names = list(ticket.get("horse_names") or [])
        if len(numbers) >= 2 and len(names) >= 2:
            number_separator = "→" if bet_type in {"umatan", "sanrentan"} else "-"
            name_separator = " → " if bet_type in {"umatan", "sanrentan"} else " - "
            return f"{number_separator.join(str(number) for number in numbers)} {name_separator.join(str(name) for name in names)}"
    return f"{ticket.get('horse_number')} {ticket.get('horse_name')}"


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _to_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "None"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_prediction_timestamp(
    prediction_context: dict[str, object],
    quality_report: dict[str, object],
) -> str:
    candidates = [
        str(prediction_context.get("odds_captured_at_latest", "")).strip(),
        str(prediction_context.get("generated_at", "")).strip(),
        str(quality_report.get("generated_at", "")).strip(),
    ]
    for value in candidates:
        formatted = _format_timestamp(value)
        if formatted:
            return formatted
    return ""


def _format_timestamp(value: str) -> str:
    if not value:
        return ""
    normalized = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return value

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    jst = timezone(timedelta(hours=9))
    return dt.astimezone(jst).strftime("%Y-%m-%d %H:%M JST")


def _format_race_date_label(value: str) -> str:
    if not value:
        return ""
    normalized = value.strip().replace("/", "-")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return value
    weekdays = "月火水木金土日"
    return f"{dt.month}月{dt.day}日（{weekdays[dt.weekday()]}）"


def _normalize_post_time(value: str) -> str:
    if not value:
        return ""
    compact = value.strip().replace("時", ":").replace("分", "").replace("：", ":")
    match = re.search(r"(\d{1,2}):(\d{2})", compact)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    digits = re.sub(r"[^\d]", "", value)
    if len(digits) >= 3:
        hours = int(digits[:-2])
        minutes = digits[-2:]
        return f"{hours:02d}:{minutes}"
    return value


def _normalize_surface(value: str) -> str:
    if not value:
        return ""
    if "ダ" in value:
        return "ダート"
    if "芝" in value:
        return "芝"
    if "障" in value:
        return "障害"
    return value


def _normalize_distance(value: str) -> str:
    if not value:
        return ""
    digits = re.sub(r"[^\d]", "", value)
    if digits:
        return f"{int(digits)}m"
    return value


def _first_nonempty(rows: list[dict[str, object]], key: str) -> str:
    for row in rows:
        value = str(row.get(key, "")).strip()
        if value:
            return value
    return ""
