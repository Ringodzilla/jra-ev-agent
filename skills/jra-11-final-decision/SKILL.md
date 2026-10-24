---
name: jra-11-final-decision
description: JRA順序付き評価の最終判定工程。decision_gateの責務だけを実施し工程JSONを保存する。
---

# 最終判定

共通契約と実行順は [工程仕様](../../docs/skill_workflow.md) を読む。担当役割は `decision_gate`。他工程の値を黙って修正しない。

- 前提・入力JSON: 10 OK、現在JST時刻、既存deadline設定。
- 出力artifact JSONの必須内容: `decision, reason_codes, tickets, withheld_tickets, deadline_state`
- 合格条件: 既存の正式GO窓（既定T-6以上T-5未満）と出力締切を満たし、全ゲートPASSならGO候補。実行時のコード設定を正とする。
- 停止条件: 窓前・締切後・未確認・NGはNO_GO、正式ticketsは空。NO_GOを技能完了失敗と混同しない。

入力・出力のパス、SHA256、取得/評価時刻、コード検査の実結果を共通 envelope に残す。検査未実行を PASS としない。
