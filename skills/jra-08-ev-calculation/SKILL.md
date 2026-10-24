---
name: jra-08-ev-calculation
description: JRA順序付き評価の確率・EV再計算工程。ev_calculatorの責務だけを実施し工程JSONを保存する。
---

# 確率・EV再計算

共通契約と実行順は [工程仕様](../../docs/skill_workflow.md) を読む。担当役割は `ev_calculator`。他工程の値を黙って修正しない。

- 前提・入力JSON: 07 PASS、オッズsnapshotと公式時刻。
- 出力artifact JSONの必須内容: `ev_table, probability_checks, odds_lineage, formula_version`
- 合格条件: p_base=Softmax(S_base)、p_final=Softmax(log(p_base)+environment_score) の再計算・確率和・有限性を確認。EV=p×odds。単勝と連系の確率を区別。
- 停止条件: 古いオッズ、未確認確率、確率来歴不一致で停止。高EVを勝率の妥当性の証拠にしない。

入力・出力のパス、SHA256、取得/評価時刻、コード検査の実結果を共通 envelope に残す。検査未実行を PASS としない。
