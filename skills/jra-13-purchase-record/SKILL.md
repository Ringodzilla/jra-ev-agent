---
name: jra-13-purchase-record
description: JRA順序付き評価の個人購入記録工程。purchase_recorderの責務だけを実施し工程JSONを保存する。
---

# 個人購入記録

共通契約と実行順は [工程仕様](../../docs/skill_workflow.md) を読む。担当役割は `purchase_recorder`。他工程の値を黙って修正しない。

- 前提・入力JSON: ユーザーの購入申告とレース識別。予測フロー完了とは独立した事後分岐。
- 出力artifact JSONの必須内容: `purchase, user_statement_evidence, unknown_fields, classification`
- 合格条件: 申告された馬・券種・金額だけを個人裁量として記録。時刻・確定オッズ不明はnull。保存先を確認。
- 停止条件: 申告から承認・モデルGO・購入時刻を捏造しない。自動購入を行わない。

入力・出力のパス、SHA256、取得/評価時刻、コード検査の実結果を共通 envelope に残す。検査未実行を PASS としない。
