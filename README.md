# jra-ev-agent

JRAのレースデータを取得し、**EVモデリングに直接使える整形済みCSV**を生成し、
さらに **買い目生成** と **note投稿準備** まで再現可能に実行する継続運用向けの `jra-ev-agent` です。

## Config-driven execution

レースURLはコードにハードコードせず `config/races.json` で管理します。

`config/races.json` の各要素は以下キーを持ちます:

* `race_name`
* `race_date`
* `track`
* `race_number`
* `source_url`
* `output_slug`
* `note_tags`

任意で以下も指定できます:

* `post_time`
* `surface`
* `distance`
* `note_title`

`post_time / surface / distance` がある場合、note の記事タイトルは
`【4月12日（日） 中山 9R 14:15発走｜印西特別】競馬予想 ダート2400m`
のような固定フォーマットで自動生成されます。

## What this pipeline now guarantees

* **Structured output columns**

  * `date, race_name, course, distance, position, time, weight, jockey`
  * `pace, last_3f, track_condition, weather, passing_order, odds, popularity`
* **Unique identifiers**

  * `race_id` = `YYYYMMDD_track_raceNo`
  * `horse_id` = URL由来ID（取得できない場合は馬名正規化）
  * `row_id` = 安定ハッシュ（冪等更新用）
* **Normalization**

  * `distance` / `position` / `popularity` は数値抽出
  * `time` / `pace` は秒 or 数値へ正規化
  * `weight` / `last_3f` / `odds` は数値化
  * `date` は `YYYY-MM-DD`
* **Raw persistence + reprocess**

  * 取得HTMLは `data/raw/` へ保存
  * `--reprocess-raw` でfetchせず再処理可能
* **Incremental + idempotent pipeline**

  * `pipeline_state.json` の `processed_race_ids` で既処理レースをスキップ
  * CSV再実行時も `row_id` 重複排除でデータ重複なし
* **JRA actual odds for cross-bet optimization**

  * `accessO.html` から単勝・複勝・枠連・馬連・ワイド・馬単・三連複・三連単の実オッズを取得
  * 実オッズは `data/processed/live_combo_odds.csv` に `race_id, bet_type, combination, odds, odds_min, odds_max, captured_at, source_cname` で保存
  * 買い目生成はJRA実オッズを優先し、取得できない券種・組合せのみ推定オッズへフォールバック
* **Portfolio EV + no-gami guard**

  * 通常候補は券種ごとのEV閾値を満たしたものだけを採用
  * 保険候補はJRA実オッズあり、EV下限あり、追加後のポートフォリオEVが1.0以上、的中時回収が総投資額以上の場合だけ採用
  * 最終買い目は的中時に総投資額を下回る組み合わせを pruning して、ガミりやすい構成を避ける
* **Verified note artifact output**

  * note本文は `report/note.md`、提出用Markdown artifact は `report/note_artifact.md` に同期出力
  * レース別の再現用artifactは `report/races/<race_id>/` に保存し、別レース実行による上書きを避ける
  * `publish_payload.json` には `artifact_markdown_path`、`artifact_exists`、`artifact_size_bytes`、`artifact_synced` を保存
  * publish前検証では本文Markdownとartifact Markdownの不一致・空ファイル・未生成をエラーとして扱う
* **Reviewer-first ticket safety**

  * `reviewer` が `NG` の場合、正式な `tickets` は空にし、候補は `invalidated_tickets` として参考扱いへ降格
  * ハイペース時は前受け同士のワイドを減点し、枠連は枠内の弱馬ノイズを補正して過大評価を抑える
* **Result label accumulation**

  * 振り返りJSONから `data/processed/result_labels.csv` にJRA風払戻ラベルを蓄積
  * アルゴリズム変更は単発レースの印象ではなく、蓄積ラベルを使った固定評価で判断する

## Architecture

* `jra_scraper/scraper.py`: HTTP, retry/backoff, raw cache, cache-only再処理
* `jra_scraper/parser.py`: JRA/JRADB構造の解析と列マッピング
* `jra_scraper/validation.py`: 型正規化・ID付与・重複除去・5件上限
* `jra_scraper/pipeline.py`: 増分更新、状態管理、CSV出力
* `analysis/ev.py`: EV算出
* `strategy/betting.py`: 買い目生成
* `report/note.py`: note用Markdown生成

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## One-command analysis pipeline

```bash
python scripts/run_pipeline.py
```

このコマンドで以下を順に実行します:

1. スクレイピング（`source_url` を直接入力として使用）
2. 構造化CSV更新（`data/processed/race_last5.csv`）
3. EV算出（`data/processed/race_ev.csv`）
4. 買い目生成
5. note Markdown生成（`report/note.md`）
6. note artifact Markdown同期（`report/note_artifact.md`）
7. publish payload生成（`report/publish_payload.json`）

## WIN5 mode

WIN5は通常の単レース馬券とは別に、5レース分の勝率からフォーメーションを生成します。

```bash
python scripts/run_win5.py --config-path config/win5_races.json --mode win5_under_10 --max-points 10
```

通常エントリポイントからも実行できます。

```bash
python scripts/run_pipeline.py --config-path config/win5_races.json --mode win5_compact --win5-max-points 60
```

WIN5モード:

* `win5_under_10`: 10点以下向け。固定レースを増やし、勝率上位中心で絞る
* `win5_compact`: 20〜60点程度。的中率と点数を両立する標準モード
* `win5_balanced`: 100〜500点程度。混戦レースを広げる
* `win5_value`: 荒れ指数と単勝EVをやや強めに見る

出力は `report/stages/05_bet_builder.json` に `bet_type=win5`、`legs`、`points`、`estimated_hit_prob`、`tickets` として保存されます。
note artifact はモードごとに `report/win5/<date>/<mode>/note.md` へ保存されます。

WIN5の振り返りは、実際の5頭を渡して選択カバー率とrank5カバー率で評価します。

```bash
python scripts/evaluate_win5.py \
  --plan-json report/win5/20260606/win5_under_10/stages/05_bet_builder.json \
  --result-numbers 8 4 6 2 9
```

`scripts/append_result_labels.py` は `result.win5.numbers` を `式別=WIN5` のラベルとして保存できます。

## Publishing phase (separated)

```bash
python scripts/publish_note.py
```

* 分析フェーズと投稿フェーズを分離しています
* このリポジトリでは安全のため実投稿は行わず、`report/publish_preview.txt` を生成する dry-run 実装です


## Codex optimization loop (fixed-eval workflow)

Codex に改善を回させる場合は、以下を固定して運用します。

- 憲法ファイル: `CODEX_STRATEGY.md`
- 実行プロンプト雛形: `CODEX_TASK_PROMPT.md`
- 固定評価: `python scripts/evaluate_strategy.py --input data/processed/race_last5.csv`

評価スクリプトは **変更しない前提** で、戦略ロジック側（特徴量・閾値・資金配分）のみを小さな差分で改善してください。


### Keep/revert automation

- 初回（baseline作成）:
  - `bash scripts/run_codex_experiment.sh data/processed/race_last5.csv`
- 変更後（候補評価 + keep/revert判定）:
  - `HYPOTHESIS="..." FILES_CHANGED="analysis/ev.py" bash scripts/run_codex_experiment.sh data/processed/race_last5.csv`

判定結果は `experiments/*.json` に保存され、**validation ROI を主指標**として keep/revert を決定します。

このスクリプトは実行時に `scripts/check_feature_leakage.py` を呼び、`result` / `payout` / `future_*` などのリーク疑いキーワードを事前検査します。

keep の場合は `report/baseline_eval.json` を更新し、revert の場合は baseline を維持します。

運用詳細は `RUNBOOK.md` を参照してください。

### Evaluation result labels

`scripts/evaluate_strategy.py` は単勝だけでなく、複勝・ワイド・枠連・馬連・馬単・三連複・三連単の払戻ラベルを評価できます。

JRA風の縦持ちCSV例:

```csv
race_id,式別,組番,馬番,払戻金
r1,単勝,,1,250
r1,複勝,,1,140
r1,ワイド,1-2,,580
r1,枠連,1-2,,820
r1,馬連,1-2,,940
r1,馬単,1-2,,2400
r1,三連複,1-2-3,,2250
r1,三連単,1-2-3,,8200
```

旧形式の `race_id,horse_number,win_payout` も引き続き読めます。評価結果には `bet_type_breakdown`、`ticket_hit_rate`、`result_bet_types_available`、`label_status` が出力されます。


### Multi-agent workflow

- System prompt: `MULTI_AGENT_SYSTEM_PROMPT.md`
- Initialize experiment role templates:
  - `bash scripts/init_multi_agent_experiment.sh 2026-04-09_001`
- Keep role outputs and ownership lock under `experiments/<id>/`.


### Subagents + worktree + orchestrator

- Agent profiles: `agents/data_collector.md`, `agents/analyzer.md`, `agents/simulator.md`, `agents/ev_calculator.md`, `agents/bet_builder.md`, `agents/reviewer.md`
- Local routing guide: `AGENTS.md`
- Worktree setup:
  - `bash scripts/setup_worktrees.sh ..`
- Role orchestration (Agent SDK):
  - `python scripts/orchestrator.py --race "中山11R 皐月賞"`

運用ルールとして、reviewer が `NG` を返した場合のみ bet_builder を reviewer指示で再実行します。

## Existing scripts

* `scripts/run_example.py`: スクレイプ実行例
* `scripts/run_analysis.py`: 分析実行例（単体）
* `scripts/run_pipeline.py`: 構成駆動の本番用エントリポイント
* `scripts/publish_note.py`: note投稿準備 / dry-run

## CSV schema (`race_last5.csv`)

* `row_id`
* `race_id`
* `horse_id`
* `horse_name`
* `run_index`
* `date`
* `race_name`
* `course`
* `distance`
* `position`
* `time`
* `weight`
* `jockey`
* `pace`
* `last_3f`
* `track_condition`
* `weather`
* `passing_order` (4角通過順)
* `odds`
* `popularity`

## Testing

```bash
python -m unittest discover -s tests -v
```

## Review and CI gates

Before committing or pushing changes, run the local review gate:

```bash
bash scripts/preflight.sh
```

This prints the working-tree review summary, checks feature leakage, and runs the full unit test suite.

To install local Git hooks that surface review context before commit and block push on failing local CI:

```bash
bash scripts/install_git_hooks.sh
```

Remote CI is also configured in `.github/workflows/ci.yml` for pushes to `main` and pull requests.

## Result label accumulation

After creating a race review JSON, append its payout labels for fixed evaluation:

```bash
python scripts/append_result_labels.py --review-json report/aoi_stakes_20260530_review.json
```

The output is idempotently appended to `data/processed/result_labels.csv`, which can be passed to strategy evaluation as labels.
