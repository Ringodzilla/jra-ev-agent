---
name: jra-14-retrospective
description: JRA順序付き評価の公式結果回顧工程。retrospective_reviewerの責務だけを実施し工程JSONを保存する。
---

# 公式結果回顧

共通契約と実行順は [工程仕様](../../docs/skill_workflow.md) を読む。担当役割は `retrospective_reviewer`。他工程の値を黙って修正しない。

- 前提・入力JSON: レース終了後の公式結果原典、固定済み発走前run、任意の購入記録。
- 出力artifact JSONの必須内容: `official_result, settlement, scenario_comparison, data_quality_findings, model_limitations`
- 合格条件: 公式原文SHA256・払戻・着順・通過順を保存し収支を再計算。想定条件の成否を事実と照合。結果入力は予測から分離。
- 停止条件: 映像未確認の出遅れ原因や敗因を断定しない。一敗で較正判定しない。事前の欠損説明の不備を隠さない。

入力・出力のパス、SHA256、取得/評価時刻、コード検査の実結果を共通 envelope に残す。検査未実行を PASS としない。
