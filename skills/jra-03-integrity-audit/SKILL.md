---
name: jra-03-integrity-audit
description: JRA順序付き評価の欠損・補完監査工程。data_integrity_auditorの責務だけを実施し工程JSONを保存する。
---

# 欠損・補完監査

共通契約と実行順は [工程仕様](../../docs/skill_workflow.md) を読む。担当役割は `data_integrity_auditor`。他工程の値を黙って修正しない。

- 前提・入力JSON: 02 の収集成果物、実際の parser と特徴量計算経路。
- 出力artifact JSONの必須内容: `history_counts, field_missingness, imputations, approval_checks, gate`
- 合格条件: 既存 jra-data-integrity-audit の監査を実施。件数・項目・来歴・承認を独立検査し、全補完の分岐と下流用途を特定。
- 停止条件: 原典・実行証拠・補完来歴が未確認なら UNVERIFIED。件数充足だけで PASS にしない。

入力・出力のパス、SHA256、取得/評価時刻、コード検査の実結果を共通 envelope に残す。検査未実行を PASS としない。監査の詳細は [項目監査スキル](../jra-data-integrity-audit/SKILL.md) を読み、同等の項目別・来歴別監査を実施する。

## 監査の目的と範囲

- 観測の欠損・未承認補完・計算不整合と、モデルの識別力不足・未検証を別の問題として報告する。入力監査PASSを予測精度の証明にしない。
- 確認済み証拠は再利用し、入力・コード・承認範囲が変わった部分を再検査する。取得省略は具体的な項目・原典を収集担当へ差し戻し、一般的な不安を新しい停止条件にしない。
