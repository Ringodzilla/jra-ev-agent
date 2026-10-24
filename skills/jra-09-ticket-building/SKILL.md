---
name: jra-09-ticket-building
description: JRA順序付き評価の買い目構築工程。bet_builderの責務だけを実施し工程JSONを保存する。
---

# 買い目構築

共通契約と実行順は [工程仕様](../../docs/skill_workflow.md) を読む。担当役割は `bet_builder`。他工程の値を黙って修正しない。

- 前提・入力JSON: 08 PASS、ユーザー予算、券種方針。
- 出力artifact JSONの必須内容: `core, partner, long, bet_types_considered, candidate_counts, tickets`
- 合格条件: 8券種横断候補と除外理由、点数・総額・実オッズ・ポートフォリオ評価を保存。予算内。
- 停止条件: 確率やオッズを改変しない。欠損券種を無断で推定しない。出力は候補で正式推奨にしない。

入力・出力のパス、SHA256、取得/評価時刻、コード検査の実結果を共通 envelope に残す。検査未実行を PASS としない。
