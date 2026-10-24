---
name: jra-10-review
description: JRA順序付き評価の独立レビュー工程。reviewerの責務だけを実施し工程JSONを保存する。
---

# 独立レビュー

共通契約と実行順は [工程仕様](../../docs/skill_workflow.md) を読む。担当役割は `reviewer`。他工程の値を黙って修正しない。

- 前提・入力JSON: 01〜09 の同一runの成果物、再計算ログ。
- 出力artifact JSONの必須内容: `status, reason, fix, evaluated_checks, evidence`
- 合格条件: 欠損・補完承認・モデル昇格・確率来歴・EV・展開整合・過剰人気・券種横断・予算・鮮度を検証し OK。機械検査結果を参照し残る意味を評価。
- 停止条件: NG または未確認は先へ進めない。修正対象と理由を指定。勝手な条件緩和禁止。

入力・出力のパス、SHA256、取得/評価時刻、コード検査の実結果を共通 envelope に残す。検査未実行を PASS としない。
