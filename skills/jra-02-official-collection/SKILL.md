---
name: jra-02-official-collection
description: JRA順序付き評価の公式データ収集工程。data_collectorの責務だけを実施し工程JSONを保存する。
---

# 公式データ収集

共通契約と実行順は [工程仕様](../../docs/skill_workflow.md) を読む。担当役割は `data_collector`。他工程の値を黙って修正しない。

- 前提・入力JSON: 01 の PASS。公式出馬表・全履歴・環境・8券種オッズ。
- 出力artifact JSONの必須内容: `race_info, horses, raw_artifacts, history_counts, environment, odds`
- 合格条件: 取得元URL・取得時刻・原文SHA256を保存し、取得不能項目を明示。取得成功と完全性を分ける。
- 停止条件: 別日値・別馬値・推定値で埋めない。結果データを発走前入力へ混ぜない。

入力・出力のパス、SHA256、取得/評価時刻、コード検査の実結果を共通 envelope に残す。検査未実行を PASS としない。
