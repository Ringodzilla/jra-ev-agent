---
name: jra-01-intake
description: JRA順序付き評価の受付工程。orchestratorの責務だけを実施し工程JSONを保存する。
---

# 受付

共通契約と実行順は [工程仕様](../../docs/skill_workflow.md) を読む。担当役割は `orchestrator`。他工程の値を黙って修正しない。

- 前提・入力JSON: ユーザー依頼、race_id、公式URL、発走時刻、予算・用途。予算不明は不明と記録し購入額を創作しない。
- 出力artifact JSONの必須内容: `race_context, purpose, cutoff, source_urls`
- 合格条件: 対象レース・用途・発走日時を原典またはユーザー提供値として識別できる。
- 停止条件: 対象の競合・発走日時未確認。正式評価を継続しない。

入力・出力のパス、SHA256、取得/評価時刻、コード検査の実結果を共通 envelope に残す。検査未実行を PASS としない。
