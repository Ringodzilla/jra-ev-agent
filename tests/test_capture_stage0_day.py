"""Stage 0 day-level capture driver: discovery, discipline, status.

No live network: the program page is a fixture replicating the real JRA
markup quirk (each meeting title div.main FOLLOWS its table), the scraper
and the capture runner are test doubles.
"""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from scripts.capture_stage0_day import (
    collect_status,
    decide_action,
    fetch_race_links,
    load_config_races,
    main,
    merge_targets,
    parse_program_page,
    resolve_source_urls,
)

JST = timezone(timedelta(hours=9))
DATE = "2026-11-15"

PROGRAM_HTML = """
<html><body>
<div class="cell">
<table class="basic narrow-xy">
<tr><th>レース 番号</th><th>レース名・条件</th><th>発走時刻</th></tr>
<tr><td>1 レース</td><td>2歳未勝利 1,400 （ダ）</td><td>9時45分</td></tr>
<tr><td>10 レース</td><td>オーロカップ（L） 3歳以上オープン 1,400 （芝）ハンデ</td><td>14時50分</td></tr>
<tr><td>11 レース</td><td>第30回 武蔵野ステークス（GⅢ） 3歳以上オープン 1,600 （ダ）定量</td><td>15時30分</td></tr>
</table>
</div>
<div class="inner"><div class="main">5回東京4日</div></div>
<div class="cell">
<table class="basic narrow-xy">
<tr><th>レース 番号</th><th>レース名・条件</th><th>発走時刻</th></tr>
<tr><td>9 レース</td><td>黄菊賞 2歳1勝クラス 2,000 （芝）</td><td>14時20分</td></tr>
<tr><td>11 レース</td><td>第51回 エリザベス女王杯（GⅠ） 3歳以上オープン 2,200 （芝・外）（牝）定量</td><td>15時40分</td></tr>
</table>
</div>
<div class="inner"><div class="main">5回京都4日</div></div>
<div class="cell">
<table class="basic narrow-xy">
<tr><th>レース 番号</th><th>レース名・条件</th><th>発走時刻</th></tr>
<tr><td>8 レース</td><td>第27回 新潟ジャンプステークス（J・GⅢ） 3歳以上オープン 3,250 （障）別定</td><td>13時50分</td></tr>
</table>
</div>
<div class="inner"><div class="main">3回福島4日</div></div>
</body></html>
"""


def test_parse_program_page_finds_graded_races_with_post_times():
    targets = parse_program_page(PROGRAM_HTML, DATE)
    by_id = {t["race_id"]: t for t in targets}
    assert set(by_id) == {"20261115_東京_11", "20261115_京都_11", "20261115_福島_08"}
    tokyo = by_id["20261115_東京_11"]
    assert tokyo["grade"] == "G3" and tokyo["track"] == "東京"
    assert tokyo["surface"] == "ダート" and tokyo["distance"] == "1600m"
    assert tokyo["post_time"] == "15:30"
    kyoto = by_id["20261115_京都_11"]
    assert kyoto["grade"] == "G1" and kyoto["race_name"] == "第51回エリザベス女王杯（GⅠ）"
    assert kyoto["surface"] == "芝" and kyoto["distance"] == "2200m"
    assert kyoto["post_time"] == "15:40"
    jump = by_id["20261115_福島_08"]
    assert jump["grade"] == "G3" and jump["surface"] == "障害"
    assert jump["distance"] == "3250m" and jump["post_time"] == "13:50"
    # Listed (L) and ungraded races are not targets.
    assert all("オーロカップ" not in t["race_name"] for t in targets)
    assert all("黄菊賞" not in t["race_name"] for t in targets)


def _config_dir(tmp_path, entries, name="race.json"):
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / name).write_text(json.dumps(entries, ensure_ascii=False),
                                   encoding="utf-8")
    return config_dir


def test_load_config_races_filters_by_date_and_skips_bad_files(tmp_path):
    config_dir = _config_dir(tmp_path, [
        {"race_name": "エリザベス女王杯", "race_date": DATE, "track": "京都",
         "race_number": 11, "post_time": "15:40", "surface": "芝",
         "distance": "2200m",
         "source_url": "https://www.jra.go.jp/JRADB/accessD.html?CNAME=x"},
        {"race_name": "別日", "race_date": "2026-11-14", "track": "東京",
         "race_number": 11, "source_url": "https://www.jra.go.jp/JRADB/accessD.html?CNAME=y"},
    ])
    (config_dir / "broken.json").write_text("{not json", encoding="utf-8")
    targets = load_config_races(config_dir, DATE)
    assert len(targets) == 1
    assert targets[0]["race_id"] == "20261115_京都_11"
    assert targets[0]["origin"] == "config"
    assert targets[0]["source_url"].endswith("CNAME=x")


def test_merge_targets_config_wins_and_keeps_grade():
    schedule = parse_program_page(PROGRAM_HTML, DATE)
    configs = [{"race_id": "20261115_京都_11", "race_name": "エリザベス女王杯",
                "race_date": DATE, "track": "京都", "race_number": 11, "grade": "",
                "surface": "芝", "distance": "2200m", "post_time": "15:40",
                "source_url": "https://www.jra.go.jp/JRADB/accessD.html?CNAME=x",
                "origin": "config", "config_path": "/tmp/c.json"}]
    merged = merge_targets(schedule, configs)
    kyoto = next(t for t in merged if t["race_id"] == "20261115_京都_11")
    assert kyoto["grade"] == "G1"
    assert kyoto["source_url"].endswith("CNAME=x")
    assert kyoto["origins"] == ["schedule", "config"]


def test_resolve_source_urls_by_id_and_by_key():
    targets = [{"race_id": "20261115_東京_11", "race_date": DATE, "track": "東京",
                "race_number": 11, "source_url": ""},
               {"race_id": "20261115_京都_11", "race_date": DATE, "track": "京都",
                "race_number": 11, "source_url": ""}]
    links = [SimpleNamespace(race_id="20261115_東京_11", race_url="https://x/1",
                             race_date=DATE, track="東京", race_number="11"),
             SimpleNamespace(race_id="other", race_url="https://x/2",
                             race_date=DATE, track="京都", race_number="11")]
    resolve_source_urls(targets, links)
    assert targets[0]["source_url"] == "https://x/1"
    assert targets[1]["source_url"] == "https://x/2"


def _target(post_time="15:40"):
    return {"race_id": "20261115_京都_11", "race_date": DATE, "track": "京都",
            "race_number": 11, "post_time": post_time}


def _snap(captured_at):
    return {"race_id": "20261115_京都_11", "captured_at": captured_at}


def test_decide_action_pre_race_and_post_time():
    target = _target()
    morning = datetime(2026, 11, 15, 8, 0, tzinfo=JST)
    assert decide_action(target, morning, "morning", []) == ("capture", "")
    at_post = datetime(2026, 11, 15, 15, 40, tzinfo=JST)
    assert decide_action(target, at_post, "refresh", []) == ("skip", "past_post_time")
    after = datetime(2026, 11, 15, 16, 5, tzinfo=JST)
    assert decide_action(target, after, "refresh", []) == ("skip", "past_post_time")
    assert decide_action(_target(""), morning, "morning", []) == (
        "skip", "post_time_unknown")


def test_decide_action_morning_idempotency():
    target = _target()
    now = datetime(2026, 11, 15, 9, 0, tzinfo=JST)
    earlier = [_snap("2026-11-15T08:00:00+09:00")]
    assert decide_action(target, now, "morning", earlier) == (
        "skip", "already_captured_morning")
    # An afternoon snapshot does not block a (late) morning-mode decision.
    afternoon = [_snap("2026-11-15T13:00:00+09:00")]
    later = datetime(2026, 11, 15, 14, 0, tzinfo=JST)
    assert decide_action(target, later, "morning", afternoon) == ("capture", "")


def test_decide_action_refresh_dedupe_window():
    target = _target()
    now = datetime(2026, 11, 15, 15, 0, tzinfo=JST)
    recent = [_snap("2026-11-15T14:50:00+09:00")]
    assert decide_action(target, now, "refresh", recent) == (
        "skip", "recent_snapshot_exists")
    stale = [_snap("2026-11-15T14:30:00+09:00")]
    assert decide_action(target, now, "refresh", stale) == ("capture", "")


class FakeScraper:
    def __init__(self, program_html):
        self.program_html = program_html

    def fetch(self, url, raw_name=None, **kwargs):
        return self.program_html

    def fetch_relative(self, path, raw_name=None, **kwargs):
        return ""

    def fetch_post(self, url, data, raw_name=None, **kwargs):
        return ""


RACE_LIST_HTML = """
<html><body>
<a href="/JRADB/accessD.html?CNAME=pw01dde0105202605031120261114/3E">東京11R 武蔵野ステークス 馬番確定</a>
<a href="/JRADB/accessD.html?CNAME=pw01dde0108202605031120261114/1C">京都11R デイリー杯2歳ステークス 馬番確定</a>
<a href="/JRADB/accessD.html?CNAME=pw01dde0108202605041120261115/4C">京都11R エリザベス女王杯 馬番確定</a>
</body></html>
"""


class ListScraper:
    def __init__(self, html):
        self.html = html
        self.calls = []

    def fetch_post(self, url, data, raw_name=None, **kwargs):
        self.calls.append((url, data))
        return self.html


def test_fetch_race_links_posts_doaction_cname_and_parses_dates(tmp_path):
    client = ListScraper(RACE_LIST_HTML)
    links = fetch_race_links(client, tmp_path)
    assert client.calls[0][1] == {"CNAME": "pw01dli00/F3"}
    by_id = {link.race_id: link for link in links}
    # The calendar date is the LAST 8-digit run of the CNAME; the first
    # run is the meeting code (2026 + round 05 + day 03/04).
    assert by_id["20261114_東京_11"].race_date == "2026-11-14"
    assert by_id["20261114_京都_11"].race_date == "2026-11-14"
    assert by_id["20261115_京都_11"].race_date == "2026-11-15"
    targets = [{"race_id": "20261114_東京_11", "race_date": "2026-11-14",
                "track": "東京", "race_number": 11, "source_url": ""}]
    resolve_source_urls(targets, links)
    assert targets[0]["source_url"].endswith("20261114/3E")


def test_fetch_race_links_empty_when_post_fails(tmp_path):
    assert fetch_race_links(ListScraper(""), tmp_path) == []
    assert fetch_race_links(ListScraper(None), tmp_path) == []


def _usable_snapshot(race_id, date, post_time, now):
    return {
        "race_id": race_id, "date": date, "value_type": "observed",
        "eligible_for_learning": True, "collection_complete": True,
        "status": "COMPLETE",
        "captured_at": now.isoformat(),
        "cutoff_at": f"{date}T{post_time}:00+09:00",
        "weather_captured_at": now.isoformat(),
        "cushion_measured_at": f"{date}T07:00:00+09:00",
        "moisture_measured_at": f"{date}T05:30:00+09:00",
        "observation_path": "",
    }


def _fake_capture_runner(targets, root, now):
    results = {}
    for target in targets:
        snap = _usable_snapshot(target["race_id"], target["race_date"],
                                target["post_time"], now)
        folder = root / "report/environment_observations" / target["race_id"]
        folder.mkdir(parents=True, exist_ok=True)
        name = now.strftime("%Y%m%dT%H%M%S%f") + ".json"
        (folder / name).write_text(json.dumps(snap, ensure_ascii=False),
                                   encoding="utf-8")
        snap["observation_path"] = str(folder / name)
        results[target["race_id"]] = snap
    return results


def _seed_config(tmp_path):
    _config_dir(tmp_path, [{
        "race_name": "エリザベス女王杯", "race_date": DATE, "track": "京都",
        "race_number": 11, "post_time": "15:40", "surface": "芝",
        "distance": "2200m",
        "source_url": "https://www.jra.go.jp/JRADB/accessD.html?CNAME=x",
    }])


def test_main_dry_run_discovers_without_writing(tmp_path, capsys):
    _seed_config(tmp_path)
    now = datetime(2026, 11, 15, 8, 0, tzinfo=JST)
    rc = main(["--date", DATE, "--dry-run"], now=now,
              scraper=FakeScraper(PROGRAM_HTML), root=tmp_path)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    grades = {t["race_id"]: t["grade"] for t in out["targets"]}
    assert grades["20261115_京都_11"] == "G1"
    assert not (tmp_path / "report").exists()


def test_main_capture_then_morning_rerun_is_idempotent(tmp_path, capsys):
    _seed_config(tmp_path)
    now = datetime(2026, 11, 15, 8, 0, tzinfo=JST)
    rc = main(["--date", DATE, "--mode", "morning"], now=now,
              scraper=FakeScraper(PROGRAM_HTML),
              capture_runner=_fake_capture_runner, root=tmp_path)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    captured_ids = {c["race_id"] for c in out["captured"]}
    assert captured_ids == {"20261115_京都_11"}
    skip_reasons = {s["race_id"]: s["reason"] for s in out["skipped"]}
    assert skip_reasons["20261115_東京_11"] == "source_url_unavailable"
    folder = tmp_path / "report/environment_observations/20261115_京都_11"
    assert len(list(folder.glob("*.json"))) == 1

    rc = main(["--date", DATE, "--mode", "morning"], now=now,
              scraper=FakeScraper(PROGRAM_HTML),
              capture_runner=_fake_capture_runner, root=tmp_path)
    assert rc == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["captured"] == []
    reasons2 = {s["race_id"]: s["reason"] for s in out2["skipped"]}
    assert reasons2["20261115_京都_11"] == "already_captured_morning"
    assert len(list(folder.glob("*.json"))) == 1

    status = collect_status(tmp_path)
    assert status["races_with_observations"] == 1
    assert status["snapshots"] == 1
    assert status["learning_eligible_races"] == 1
    assert status["eligible_pool_progress"]["remaining"] == 139
    assert status["split_progress"]["train"] == {"current": 0, "minimum": 100}


def test_main_not_race_day_records_skips(tmp_path, capsys):
    _seed_config(tmp_path)
    # Asked on 2026-11-16 for the 2026-11-15 races: discovery still works,
    # but no capture may happen for a non-race day.
    now = datetime(2026, 11, 16, 8, 0, tzinfo=JST)
    rc = main(["--date", DATE, "--mode", "morning"], now=now,
              scraper=FakeScraper(PROGRAM_HTML),
              capture_runner=_fake_capture_runner, root=tmp_path)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["captured"] == []
    assert {s["reason"] for s in out["skipped"]} == {"not_race_day"}


def _dirt_usable_snapshot(race_id, date, post_time, now):
    # Shape recorded by capture_environment for a dirt race: the dirt
    # contract requires dirt moisture and carries no cushion measurement.
    snap = _usable_snapshot(race_id, date, post_time, now)
    del snap["cushion_measured_at"]
    snap["dirt_moisture_goal"] = 2.5
    snap["dirt_moisture_corner4"] = 2.0
    snap["required_fields_for_surface"] = [
        "weather", "track_condition", "weather_captured_at",
        "moisture_measured_at", "dirt_moisture_goal", "dirt_moisture_corner4"]
    return snap


def _fake_dirt_capture_runner(targets, root, now):
    results = {}
    for target in targets:
        snap = _dirt_usable_snapshot(target["race_id"], target["race_date"],
                                     target["post_time"], now)
        folder = root / "report/environment_observations" / target["race_id"]
        folder.mkdir(parents=True, exist_ok=True)
        name = now.strftime("%Y%m%dT%H%M%S%f") + ".json"
        (folder / name).write_text(json.dumps(snap, ensure_ascii=False),
                                   encoding="utf-8")
        snap["observation_path"] = str(folder / name)
        results[target["race_id"]] = snap
    return results


def test_main_capture_dirt_race_accumulates_uncounted(tmp_path, capsys):
    _config_dir(tmp_path, [{
        "race_name": "武蔵野ステークス", "race_date": DATE, "track": "東京",
        "race_number": 11, "post_time": "15:30", "surface": "ダート",
        "distance": "1600m",
        "source_url": "https://www.jra.go.jp/JRADB/accessD.html?CNAME=d",
    }])
    now = datetime(2026, 11, 15, 8, 0, tzinfo=JST)
    rc = main(["--date", DATE, "--mode", "morning"], now=now,
              scraper=FakeScraper(PROGRAM_HTML),
              capture_runner=_fake_dirt_capture_runner, root=tmp_path)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    captured_ids = {c["race_id"] for c in out["captured"]}
    assert captured_ids == {"20261115_東京_11"}
    # The dirt snapshot folds into the status aggregation as accumulated
    # but uncounted: docs define no dirt counting rule for the minimums.
    status = collect_status(tmp_path)
    assert status["learning_eligible_races"] == 0
    assert status["dirt_learning_eligible_races"] == 1
    assert status["dirt_learning_eligible_race_ids"] == ["20261115_東京_11"]
    assert status["dirt_counting"]["counted_toward_minimums"] is False
    assert status["dirt_counting"]["status"] == "accumulating_uncounted"
    assert status["eligible_pool_progress"] == {
        "current": 0, "minimum_total": 140, "remaining": 140}


def test_collect_status_separates_dirt_from_counted_pool(tmp_path):
    obs = tmp_path / "report/environment_observations"
    now = datetime(2026, 11, 15, 8, 0, tzinfo=JST)
    turf = _usable_snapshot("20261115_京都_11", DATE, "15:40", now)
    folder = obs / "20261115_京都_11"
    folder.mkdir(parents=True)
    (folder / "snap.json").write_text(json.dumps(turf), encoding="utf-8")
    dirt = _dirt_usable_snapshot("20261115_東京_11", DATE, "15:30", now)
    folder2 = obs / "20261115_東京_11"
    folder2.mkdir(parents=True)
    (folder2 / "snap.json").write_text(json.dumps(dirt), encoding="utf-8")
    status = collect_status(tmp_path)
    assert status["races_with_observations"] == 2
    assert status["learning_eligible_races"] == 1
    assert status["learning_eligible_race_ids"] == ["20261115_京都_11"]
    assert status["dirt_learning_eligible_races"] == 1
    assert status["eligible_pool_progress"]["current"] == 1
    assert status["eligible_pool_progress"]["remaining"] == 139


def test_collect_status_with_learning_status_file(tmp_path):
    obs = tmp_path / "report/environment_observations"
    good = _usable_snapshot("20261115_京都_11", DATE, "15:40",
                            datetime(2026, 11, 15, 8, 0, tzinfo=JST))
    folder = obs / "20261115_京都_11"
    folder.mkdir(parents=True)
    (folder / "snap.json").write_text(json.dumps(good), encoding="utf-8")
    bad = dict(good, race_id="20261115_東京_11", eligible_for_learning=False)
    folder2 = obs / "20261115_東京_11"
    folder2.mkdir(parents=True)
    (folder2 / "snap.json").write_text(json.dumps(bad), encoding="utf-8")
    # cases/ payloads are learning inputs, not snapshots; not counted.
    cases = folder / "cases"
    cases.mkdir()
    (cases / "case.json").write_text(json.dumps({"data_collector": {}}),
                                     encoding="utf-8")
    model_dir = tmp_path / "report/environment_model"
    model_dir.mkdir(parents=True)
    (model_dir / "learning_status.json").write_text(json.dumps({
        "status": "INSUFFICIENT_DATA", "generated_at": "2026-11-16T00:00:00+00:00",
        "blocks": {"train": ["a", "b"], "validation": ["c"], "test": []},
        "pending_environment": [{"race_id": "x"}], "excluded_races": [],
    }), encoding="utf-8")
    status = collect_status(tmp_path)
    assert status["races_with_observations"] == 2
    assert status["snapshots"] == 2
    assert status["learning_eligible_races"] == 1
    assert status["learning_status"]["dataset_races"] == {
        "train": 2, "validation": 1, "test": 0}
    assert status["split_progress"]["validation"] == {"current": 1, "minimum": 20}
