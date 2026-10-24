---
name: jra-05-ability-analysis
description: JRA順序付き評価の能力・特徴量評価工程。analyzerの責務だけを実施し工程JSONを保存する。
---

# 能力・特徴量評価

共通契約と実行順は [工程仕様](../../docs/skill_workflow.md) を読む。担当役割は `analyzer`。他工程の値を黙って修正しない。

- 前提・入力JSON: 04 PASS と固定入力。
- 出力artifact JSONの必須内容: `scores, feature_lineage, model_version, environment_score, environment_reason`
- 合格条件: 能力・コース・展開・斤量・騎手の根拠と各馬特徴量を保存。環境は昇格済みモデルのみ適用し、ない場合は0と理由を保存。
- 停止条件: 未承認の中立値、除外、重み再配分で欠損を迂回しない。未昇格研究は別runとし同じ承認ゲートを通す。

入力・出力のパス、SHA256、取得/評価時刻、コード検査の実結果を共通 envelope に残す。検査未実行を PASS としない。
