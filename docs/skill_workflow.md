# 順序付き競馬評価スキル仕様

## 適用範囲と実行の意味

競馬分析・再計算・買い目・購入申告・回顧の作業を工程別技能と成果物検査に分ける。`skills/` をこのリポジトリの工程指示の正本とする。Codex は該当 SKILL.md を読んでその役割を実施する。Python が Markdown を順に読むだけでは技能実行・公式収集・分析・意味的レビューの完了にならない。ランナーは工程JSONの順序・入力整合・承認・検査結果を検証する。未実装の検査は未実装と表示する。

初回受付と収集後に監査し、分析・再計算前には入力が変わっていないことを検証する。入力変更時は監査から新runで再実行し、過去承認の対象範囲を再照合する。

## 工程順序

| 順序 | 技能 | 責任役割 |
|---|---|---|
| 01 | [受付](../skills/jra-01-intake/SKILL.md) | orchestrator |
| 02 | [公式データ収集](../skills/jra-02-official-collection/SKILL.md) | data_collector |
| 03 | [欠損・補完監査](../skills/jra-03-integrity-audit/SKILL.md) | data_integrity_auditor |
| 04 | [補完承認判定](../skills/jra-04-approval-gate/SKILL.md) | approval_gate |
| 05 | [能力・特徴量評価](../skills/jra-05-ability-analysis/SKILL.md) | analyzer |
| 06 | [展開シミュレーション](../skills/jra-06-pace-simulation/SKILL.md) | simulator |
| 07 | [確率再計算](../skills/jra-07-probability-calculation/SKILL.md) | simulator |
| 08 | [確率・EV再計算](../skills/jra-08-ev-calculation/SKILL.md) | ev_calculator |
| 09 | [買い目構築](../skills/jra-09-ticket-building/SKILL.md) | bet_builder |
| 10 | [独立レビュー](../skills/jra-10-review/SKILL.md) | reviewer |
| 11 | [最終判定](../skills/jra-11-final-decision/SKILL.md) | decision_gate |
| 12 | [報告](../skills/jra-12-report/SKILL.md) | reporter |
| 13 | [個人購入記録](../skills/jra-13-purchase-record/SKILL.md) | purchase_recorder |
| 14 | [公式結果回顧](../skills/jra-14-retrospective/SKILL.md) | retrospective_reviewer |

01〜12は事前評価の必須順。13は購入申告時だけ、14は発走後だけ実行する。既存役割の順序 `data_collector → analyzer → simulator → ev_calculator → bet_builder → reviewer` は維持し、間に品質ゲートを置く。途中からの要求も未完了の依存工程を省略しない。13/14を01〜12の前提にしない。

## 共通JSON契約

以下は技能実行の監査記録の論理契約。実装のschemaとキーが異なる場合は対応を明示し、証拠内容を失わない。

```json
{
  "schema_version": 1,
  "step_id": "intake",
  "skill_name": "jra-01-intake",
  "status": "PASS",
  "input_artifacts": [{"path": "/absolute/input.json", "sha256": "digest"}],
  "output_artifacts": [{"path": "/absolute/output.json", "sha256": "digest"}],
  "checks": [{"id": "check-id", "status": "PASS", "evidence": "/absolute/evidence.json"}],
  "reason_codes": [],
  "next_action": "collection"
}
```

`status` は `PASS | FAIL | BLOCKED | SKIPPED`。未確認はBLOCKEDとしreason_codesにUNVERIFIEDを記録する。実行履歴には成果物そのもののSHA256と前工程のSHA256も記録し、別run・別レース・別入力の合格結果を転用しない。実行主体やSKILL読了は検査証拠の代わりにならない。検査が検証できない内容を文字列の自己申告だけで合格にしない。

欠損・補完の詳細は既存 `jra-data-integrity-audit` が正とする。監査で列挙する承認は人間ユーザーの発言を根拠にし、生成器・ランナー・他エージェントが作る `APPROVED` だけでは承認証拠にしない。補完の対象、方法、値、用途、入力範囲とユーザー発言の範囲を一致させる。承認を求める場合は対象一覧と影響、停止を要求するSKILL.mdへのリンク、該当規則を示して返答を待つ。確認不要な原典再取得は進める。

必須入力の不足や未承認補完があれば04で停止する。任意項目の欠損も報告から除かない。環境モデル未昇格による `environment_score=0` は環境補正未適用であり、通過順欠損からの中立値採用とは区別する。

## 再実行・評価・最終判定

各工程はコードによる構造・数値検査とCodexによる原典・来歴・意味の評価をそれぞれ記録する。前工程合格が全て揃うまで後工程を実行済み扱いにしない。機械検査がPASSでも精度が検証された意味ではない。

reviewer NG時のみ、その `fix` で指定した修正を対象に再実行する。修正の影響工程以降の既存PASSを無効化し新runを作る。追加の公式観測による通常更新も新しい入力として監査し、旧runを上書きしない。無効化した買い目は `withheld_tickets` / `invalidated_tickets` として保持し、正式ticketsに戻さない。

既存の締切・予算・鮮度・正式GO窓を緩めない。既定のGO窓はT-6からT-5締切まで。11の工程検査が完了しても、判定結果がNO_GOであることは正常に成立する。停止時は未実施工程を明記し、NO_GOと空の正式ticketsを返す。

研究試算も欠損・補完承認ゲートを省略しない。未昇格環境モデルは研究専用runと出力先へ分離し本番へ昇格扱いしない。学習・昇格は独立の既存環境モデル規約と検証コードに従い、回顧工程が自動昇格させない。

報告・note公開準備は12の成果物作成まで。外部公開、購入、メッセージ送信の権限をこの仕様から付与しない。

## 実行モード

`config/skill_workflow.json` の工程IDとmode定義を実行時の正とする。事前予測はintakeからreport、直前更新は更新した入力に対応する監査からreport、購入記録はpurchase、結果回顧はretrospectiveを対象とする。モード外の工程はSKIPPEDの理由を記録できるが、依存工程をSKIPPEDにして通過しない。

step_id は順に intake, collection, integrity_audit, approval_gate, analysis, simulation, probability, ev, tickets, review, decision, report, purchase, retrospective。

## 現在のコード対応と限界

公開CLIは機械ゲートを必須実行する。`src/skill_workflow.py` が工程順・証拠hash・数値条件を検査し、`src/data_integrity.py` が入力監査を担当する。低レベル旧Python APIは既存テスト・呼出元との互換のため自動有効化しない。運用は公開CLIを使い、APIを使う場合は `enforce_skill_gates=True` を必須とする。ゲート未使用のmanifestには `LEGACY_LIBRARY_API_NOT_GATED` を記録する。

07と08は現状同一のEVCalculatorAgentが計算した成果物を、それぞれ確率条件とEV条件で分けて検査する。技能の責務は分離するが、まだ別々の数値計算器ではない。10は既存の定量reviewerであり、LLMによる独立レビュー実行を意味しない。11の事前runは候補監査で正式判断はNO_GO、直前runのみ既存のT-6〜T-5ゲートでGOを判断する。

原典照合は保存HTMLの再解析との比較であり、同じparser自身の見落としを独立に証明するものではない。未検証範囲を監査limitationsに残し、原典の欠落や矛盾を承認で上書きしない。13と14は現時点ではCodexが工程スキルを読み既存記録・結果登録コマンドで行い、汎用ランナーの自動実行対象ではない。

機械ゲート有効時はreviewer NGでも処理完了と記録し、買い目を無効化して報告する。修復は無言の全工程ループを行わず、reviewer指示を確認して新しいrunへ進む。途中で検査が止まった場合は既存候補を流用しない。
