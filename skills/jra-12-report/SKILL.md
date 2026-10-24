---
name: jra-12-report
description: JRA順序付き評価の報告工程。reporterの責務だけを実施し工程JSONを保存する。
---

# 報告

共通契約と実行順は [工程仕様](../../docs/skill_workflow.md) を読む。担当役割は `reporter`。他工程の値を黙って修正しない。

- 前提・入力JSON: 11 の確定判定と全検査結果。
- 出力artifact JSONの必須内容: `decision_summary, data_quality, limitations, artifact_links`
- 合格条件: 件数充足・項目欠損・補完と承認・モデル制約・オッズ時刻を別表示。ファイル存在を実確認してリンクする。
- 停止条件: 欠損なし・保存済み・検証済みを証拠なしに宣言しない。無効化候補を正式買い目として表示しない。

入力・出力のパス、SHA256、取得/評価時刻、コード検査の実結果を共通 envelope に残す。検査未実行を PASS としない。
