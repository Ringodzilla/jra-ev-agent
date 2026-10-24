---
name: jra-06-pace-simulation
description: JRA順序付き評価の展開シミュレーション工程。simulatorの責務だけを実施し工程JSONを保存する。
---

# 展開シミュレーション

共通契約と実行順は [工程仕様](../../docs/skill_workflow.md) を読む。担当役割は `simulator`。他工程の値を黙って修正しない。

- 前提・入力JSON: 05 PASS。脚質・通過順の根拠と特徴量来歴。
- 出力artifact JSONの必須内容: `probabilities, scenario_weights, scenario_conditions, probability_lineage`
- 合格条件: ハイ・ミドル・スローを区別しシナリオ重みと最終確率の来歴を保存。想定条件と観測事実を分離。
- 停止条件: 通過順の中立値を実績と説明しない。能力評価や環境係数をこの工程で変更しない。

入力・出力のパス、SHA256、取得/評価時刻、コード検査の実結果を共通 envelope に残す。検査未実行を PASS としない。
