---
name: jra-04-approval-gate
description: JRA順序付き評価の補完承認判定工程。approval_gateの責務だけを実施し工程JSONを保存する。
---

# 補完承認判定

共通契約と実行順は [工程仕様](../../docs/skill_workflow.md) を読む。担当役割は `approval_gate`。他工程の値を黙って修正しない。

- 前提・入力JSON: 03 の監査JSONとユーザーの直接の明示承認証拠。
- 出力artifact JSONの必須内容: `approved_imputations, blocked_imputations, approval_evidence, scope`
- 合格条件: 実際に使う全補完について馬・走・項目・値・方法・用途・対象入力が承認範囲と一致。必須欠損なし。
- 停止条件: NOT_APPROVED または UNVERIFIED は停止。具体的な補完一覧と影響を提示して返答待ち。分析依頼・購入報告・沈黙を承認にしない。

入力・出力のパス、SHA256、取得/評価時刻、コード検査の実結果を共通 envelope に残す。検査未実行を PASS としない。

## 承認対象を限定する

- ユーザー確認は実際に採用する未承認補完の対象・方法・値・用途に限定する。補完がなく既存の監査・承認条件を満たせば、追加確認を求めず次へ進む。
- 高オッズ、着順のばらつき、モデルの精度未検証をデータ補完の承認問題へすり替えない。原典回収は既存の依頼範囲で進める。
