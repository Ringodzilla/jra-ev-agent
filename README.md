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
  * 対象レースの最新状態は `target_weather, target_track_condition, target_conditions_captured_at` として過去走列から分離
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
  * `--force-rebuild` では対象レース・オッズHTMLを再取得し、最新の天候・馬場状態を保存
* **Incremental + idempotent pipeline**

  * `pipeline_state.json` の `processed_race_ids` で既処理レースをスキップ
  * CSV再実行時も `row_id` 重複排除でデータ重複なし
* **JRA actual odds for cross-bet optimization**

  * `accessO.html` から単勝・複勝・枠連・馬連・ワイド・馬単・三連複・三連単の実オッズを取得
  * 実オッズは `data/processed/live_combo_odds.csv` に `race_id, bet_type, combination, odds, odds_min, odds_max, captured_at, source_cname` で保存
  * 買い目生成はJRA実オッズを優先し、取得できない券種・組合せのみ推定オッズへフォールバック
* **Portfolio EV + no-gami guard**

  * 通常候補は券種ごとのEV閾値を満たしたものだけを採用
  * 単勝候補には固定の勝率下限を置かず、JRA実オッズとのEVで評価する。高オッズ・根拠薄の候補は、履歴件数と整合度に応じた連続的な市場縮約で抑制する
  * 券種別の候補上限は確率補正・EV再計算・閾値判定の後に適用し、除外された候補の枠を次順位で再充填する
  * 保険候補はJRA実オッズあり、EV下限あり、追加後のポートフォリオEVが1.0以上、的中時回収が総投資額以上の場合だけ採用
  * 最終買い目は的中時に総投資額を下回る組み合わせを pruning して、ガミりやすい構成を避ける
* **Verified note artifact output**

  * note本文は `report/note.md`、提出用Markdown artifact は `report/note_artifact.md` に同期出力
  * レース別の再現用artifactは `report/races/<race_id>/` にローカル生成し、別レース実行による上書きを避ける
  * `report/races/` と `report/win5/` は生成物としてGit管理対象外
  * `publish_payload.json` には `artifact_markdown_path`、`artifact_exists`、`artifact_size_bytes`、`artifact_synced` を保存
  * publish前検証では本文Markdownとartifact Markdownの不一致・空ファイル・未生成をエラーとして扱う
* **Reviewer-first ticket safety**

  * `reviewer` が `NG` の場合、正式な `tickets` は空にし、候補は `invalidated_tickets` として参考扱いへ降格
  * JRA実オッズで単勝EV閾値を満たす馬が明示的な候補集合から漏れた場合、`missing_eligible_win_candidates` を記録して `NG` にする
  * ハイペース時は前受け同士のワイドを減点し、枠連は枠内の弱馬ノイズを補正して過大評価を抑える
* **Result label accumulation**

  * 振り返りJSONから `data/processed/result_labels.csv` にJRA風払戻ラベルを蓄積
  * アルゴリズム変更は単発レースの印象ではなく、蓄積ラベルを使った固定評価で判断する

## Architecture

* `jra_scraper/scraper.py`: HTTP, retry/backoff, raw cache, cache-only再処理
* `jra_scraper/parser.py`: JRA/JRADB構造の解析と列マッピング
* `jra_scraper/validation.py`: 型正規化・ID付与・重複除去・5件上限
* `jra_scraper/pipeline.py`: 増分更新、状態管理、CSV出力
* `jra_scraper/live_snapshot.py`: 直前用の出馬表・馬体重・天候・馬場・全券種オッズ取得（過去走は取得しない）
* `src/agents/`: data collector / analyzer / simulator / EV calculator / bet builder / reviewer / article writer の役割別実装
* `src/react_workflow.py`: 役割別agentの実行順序、再実行条件、stage artifactの検証だけを担当するオーケストレーター
* `analysis/ev.py`: EV算出
* `strategy/betting.py`: 買い目生成
* `report/note.py`: note用Markdown生成
* `src/deadline.py`: 発走時刻からT-5出力締切と実行モードを算出
* `src/final_workflow.py`: 事前分析と最新スナップショットを統合し、最終 `GO / NO_GO` を判定

既存コード向けの互換性を保つため、agent classと `WorkflowSettings` は
`src.react_workflow` からも再公開しています。新規コードでは役割の所有箇所が明確になる
`src.agents` からのimportを推奨します。

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

## Race-day final prediction (T-5 deadline)

直前処理では全履歴を取り直しません。通常パイプラインで事前に作った
`pipeline_run.json` または `race_last5.csv` を基礎データとして再利用し、JRAから次だけを更新します。

1. 出走馬・騎手・斤量・馬体重
2. 天候・馬場状態
3. 単勝、複勝、枠連、馬連、ワイド、馬単、三連複、三連単の同一時点オッズ
4. 能力再計算、シミュレーション、EV、買い目、reviewer の順で再実行

事前分析（レースの十分前）:

```bash
python3 scripts/run_pipeline.py --config-path config/final_prediction.example.json
```

直前確定:

```bash
python3 scripts/run_final_prediction.py \
  --config-path config/final_prediction.example.json \
  --baseline-path report/races/20260808_札幌_11/pipeline_run.json
```

`--baseline-path` を省略すると、対象 `race_id` と一致するレース別artifactまたは収集済みCSVを探索します。
実行モードは締切までの残り時間に応じて `normal / fast / emergency / too_late` へ切り替わります。
`emergency` でも既定の最小更新時間40秒と出力予約10秒を確保できない場合は、取得を開始せず `NO_GO` にします。
締切は既定で発走5分前です。締切到達時はハードウォッチドッグが処理を中断し、買い目を空にした
`NO_GO` を出力します。

`GO` には、以下をすべて満たす必要があります。

* 5分前締切内に完了
* 8券種が同一 `snapshot_id` で取得され、出走頭数から求めた全組番が欠損なし
* 公式オッズ、天候、馬場状態が鮮度上限内
* 馬体重が未発表ではない（JRA公式の「計不」は取得済み状態として区別）
  * 馬体重は現時点では取得完了と異常値の安全ゲートにのみ使用し、検証済み係数がないため能力点には加減算しない
* 事前分析と最新出馬表の頭数・馬IDが一致し、各馬について `min(5, 通算出走数)` 件の過去走がある
  * 2歳戦などで通算出走数が5未満の場合は、configの `career_starts_by_horse_number` に実数を指定する
  * 初出走馬は値を `0` とし、擬似過去走を作らず中立特徴量 `0.5` で最終計算へ含める
* `pipeline_run.json` 使用時は事前reviewerが `OK` で、stage manifestが一致
* reviewerが `OK`、買い目が存在し、フォーメーションを含む全購入点の組番がJRA実オッズに存在

買い目だけがreviewerの定量条件を満たさない場合は、指示された問題券だけを除外し、100円単位で残存券を再配分します。
修復後にportfolio EV、的中時の元返し割れ、馬依存度、上位馬カバー、JRA実オッズを再検証し、すべて合格した場合だけ `GO` へ戻します。
残額を安全に配分できない場合は無理に使い切りません。データ品質、締切、馬体重、オッズ鮮度のNGは修復対象外です。

いずれかが欠ける場合は `NO_GO` となり、`tickets` は必ず空になります。結果は
`report/final_predictions/<race_id>/<run_id>/final_decision.json` に保存され、最新結果は
`report/final_predictions/<race_id>/latest_decision.json` から参照できます。

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

- 署名鍵をリポジトリ外で生成・保管:
  - `export EVALUATION_INTEGRITY_KEY="$(openssl rand -hex 32)"`
- 初回（baseline作成）:
  - `bash scripts/run_codex_experiment.sh data/processed/race_last5.csv`
- 変更後（候補評価 + keep/revert判定）:
  - `HYPOTHESIS="..." FILES_CHANGED="analysis/ev.py" bash scripts/run_codex_experiment.sh data/processed/race_last5.csv`

同じbaseline系列では同じ署名鍵を使用します。鍵をソース、設定ファイル、ログへ保存してはいけません。

判定結果は `experiments/*.json` に保存され、**validation ROI を主指標**として keep/revert を決定します。

このスクリプトは実行時に `scripts/check_feature_leakage.py` を呼び、`result` / `payout` / `future_*` などのリーク疑いキーワードを事前検査します。

keep の場合は `report/baseline_eval.json` を更新し、revert の場合は baseline を維持します。

運用詳細は `RUNBOOK.md` を参照してください。

### Manual history fallback

出馬表と馬詳細ページを統合しても過去5走が揃わない場合、パイプラインは
`report/missing_history_requests.json` に要調査リストを、`report/manual_history_template.csv`
に補足用テンプレートを出力します。処理は停止せず、
欠損特徴量には中立値 `0.5` を使用します。

手動で確認できた過去走は `data/manual/horse_history_overrides.csv` に追加してください。
次回実行時に馬ID（未設定時は馬名）で照合され、自動的に過去走へマージされます。
対象レース当日以降の日付はリーク防止のため取り込まれません。

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

## Track bias priors

* `config/track_biases.json` stores researched course-bias priors for 小倉・福島・函館 dirt 1700m.
* `src.track_bias.track_bias_adjustment` maps `target_track`, `target_surface`, `target_distance`, `target_track_condition`, `frame_number`, and `front_rate` to a small additive pace prior.
* Feature rows expose `track_bias_score`, `track_bias_style`, `track_bias_strength`, and `track_bias_frame` for review/debugging.
* Unknown tracks/distances remain neutral, so existing races outside these profiles are unchanged.
* Validate the file with `python scripts/update_track_biases.py`.
* To refresh researched values, prepare a JSON patch with `sources` and/or `profiles`, then run `python scripts/update_track_biases.py --merge path/to/patch.json`. Matching profiles are replaced by `(track, surface, distance)`.

## Testing

```bash
python -m pytest -q
```

## Artifact storage

成果物の保持期間とarchive手順は `docs/storage_policy.md` を正本とします。古い成果物の候補確認は読み取り専用の監査スクリプトで行います。

```bash
python scripts/audit_artifact_storage.py
```

次の一歩: `archive-candidate` を月単位にまとめ、checksum確認後にだけ元データを整理します。

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
