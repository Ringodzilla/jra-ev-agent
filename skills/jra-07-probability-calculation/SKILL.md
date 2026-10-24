---
name: jra-07-probability-calculation
description: JRA順序付き評価の確率再計算工程。simulatorの確率来歴と二段階Softmaxを検証する。
---

# 確率再計算

[工程仕様](../../docs/skill_workflow.md) の共通契約に従う。担当役割はsimulator。

- 入力JSON: 06の合格成果物と05の固定スコア、モデル版、環境補正。
- 出力JSON: probabilities, probability_checks, probability_lineage。
- 合格条件: p_base=Softmax(S_base)、p_final=Softmax(log(p_base)+environment_score) を再計算し、全頭の確率和・範囲・有限性と来歴を確認する。
- 停止条件: 入力不一致、未承認補完、未昇格係数の本番適用。補正を独自に変更しない。
