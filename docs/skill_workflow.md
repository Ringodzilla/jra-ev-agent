# 順序付き競馬評価スキル仕様

## 適用範囲と実行の意味

競馬分析・再計算・買い目・購入申告・回顧の作業を工程別技能と成果物検査に分ける。`skills/` をこのリポジトリの工程指示の正本とする。Codex は該当 SKILL.md を読んでその役割を実施する。Python が Markdown を順に読むだけでは技能実行・公式収集・分析・意味的レビューの完了にならない。ランナーは工程JSONの順序・入力整合・承認・検査結果を検証する。未実装の検査は未実装と表示する。

初回受付と収集後に監査し、分析・再計算前には入力が変わっていないことを検証する。入力変更時は監査から新runで再実行し、過去承認の対象範囲を再照合する。

## 目的に沿った実行

成果は、馬の強弱と条件差を市場から独立して評価し、その確率と価格の差を根拠に予算内で買う／見送る判断を期限内に確定すること。データ・承認・来歴の検査は判断の前提であり、工程PASS・テスト件数・見送り自体を予測精度の実績にしない。

- 取得可能な重要データは回収し、未使用・未接続・未測定・未取得を分ける。未承認補完や結果漏洩は防ぎ、検査を省く口実に目的を使わない。
- 能力根拠、展開条件、スコア尺度と勝率の差、高EVの発生理由を担当工程で評価する。平坦な勝率で高オッズの馬が一律に高EVになる問題を購入段階の足切りで隠さない。
- 高オッズ・低人気・着順のばらつき自体を拒否理由にしない。実装済み条件を明示して適用し、未検証の「安全性」閾値を追加しない。診断指標を新しいNO_GO条件にしない。
- 窓前の事前評価、条件未達による見送り、締切を逃した未完了を区別する。継続作業は最新入力から判断・報告まで進め、実行手段がないのに後日の自動更新を約束しない。
- 機械検査済みの証拠は参照し、独立レビューは根拠・モデル・意思決定の妥当性へ集中する。NGでも正式買い目を無効化したNO_GOと理由の報告まで行う。
- 改善は基本形を比較対象に、芝・ダート×距離と時系列を分けて検証する。単一レースに合う係数への変更や市場への追従で結果を整えない。

### データに基づく市場と異なる評価

analyzerは観測された能力・条件差の根拠と反証を示し、simulatorはその優位が成立・失効する展開を示す。ev_calculatorは固定された構造確率と同時点価格を比較し、bet_builderは候補を既存条件で選定する。人気と違うこと自体を得点にせず、人気と違うことを除外理由にもしない。

市場に織り込まれていないという説明は検証対象の仮説とする。発走前の報告には、具体的な馬・根拠データ・有利な条件・崩れる条件・価格差と、モデルに未接続の情報を区別して残す。データが支持しない仮説は創作しない。回顧では同じ仮説を結果へ照合し、仮説の成否、勝率推定、買い目の収支を分ける。モデルと一律勝率で高EV集合が同じなら、独自の穴馬発見という実績にしない。

これは分析・報告の方針であり、新しい数値係数・購入閾値・機械停止ゲートの追加ではない。過去予測を後から修正せず、改善は共通基本形を比較対象にして検証する。

総合受付は [jra-race-ev-analysis](../skills/jra-race-ev-analysis/SKILL.md)、項目監査は [jra-data-integrity-audit](../skills/jra-data-integrity-audit/SKILL.md)、終了済み環境研究は [jra-environment-research](../skills/jra-environment-research/SKILL.md) を正本とする。01〜14と環境研究のインストール先はリポジトリへのリンク、総合受付と項目監査はインストール済みコピーも同期する。スキル指示の変更は今後の実行へ適用し、過去runの技能SHAや予測成果物を上書きしない。

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

## 工程省略を止める実行時検査

01〜12は `src/skill_stage_contracts.py` の工程別検査を必須とする。呼出元のチェックが全てtrueでも、必須成果物・行の対応・確率・EV・買い目の来歴が一致しなければ通過しない。空のチェック、true以外の真偽値代用品、別runの成果物、保存後の成果物や前工程記録の改変を拒否する。実行ID・レース・設定・入力・前工程のSHA256を束縛し、BLOCKED後の同runでの続行を禁止する。

原典に値が保存されているのに収集結果が空なら、未使用の特徴量であっても `BLOCKED_ACQUISITION_INCOMPLETE` として収集工程へ差し戻す。`src/acquisition_coverage.py` は原典とSHA256を照合し、取得省略、原典未確認、取得失敗、原典上の未測定を区別する。未取得の自己申告だけでは取得不能と認定しない。この検査自体はネットワーク再取得を代行しない。

機械検査は成果物と数値の検証であり、LLMが指示を理解して実施したことの証明ではない。原典の意味、モデルの妥当性、独立した判断は担当役割のレビューとして別に残す。13・14の手動分岐を自動検査済みとは扱わない。工程ゲートの攻撃テストを品質検査に含め、検査の削除や緩和も通常の変更と同じくレビューする。

確率工程は保存された展開と受付時の重みから既存計算器で値を再検算し、EV工程は候補を再構成して照合する。レビュー工程も保存設定に基づく定量検査と締切・鮮度検査を再実施する。これらは検算であり、新しい予測の発行や未承認係数の適用を意味しない。時計と原典は通常の公開ワークフローが供給する入力であり、任意のコード改変や入力自体の偽造を防ぐ隔離環境ではない。

## 環境モデル研究の別分岐

終了済みレースの研究学習は `config/skill_workflow.json` の `research_branch` と
[研究スキル](../skills/jra-environment-research/SKILL.md) を使う。01〜07の技能と役割を対応させ、
その後にanalyzerの研究fit、reviewerの独立レビュー、reporterの報告を置く。
EV・買い目・投資判断・購入は対象外として未実行を記録する。これは本番用SkillSequenceの
prediction/finalモードではなく、専用ResearchSequenceによる証拠と順序の検査である。
未実装の数値検査はBLOCKEDとし、現在の取得網羅性・補完承認・全馬集合のゲートを緩めない。
事後再構成・最低件数・未実施の検証とtestを報告し、本番適用や共有学習状態の変更を行わない。

## スキル改訂の独立したユーザー視点チェック

既存reviewerは、レースの工程10に加えてスキル改訂レビューを担当する。改訂者と別の担当者が [改訂レビュー基準](../skills/jra-10-review/references/user_intent.md) で、ユーザーの実際の目的、データの計算への接続、高EV候補の保持、最終判断までの挙動を確認し、修正後に再確認する。この作業はスキル変更時に行い、レース実行へ新しい停止ゲートは追加しない。
