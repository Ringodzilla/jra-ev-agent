# 海外走の収集経路と予測入力監査

2026-11-09 に GitHub の `main` (`3d2aa3b`) を基準に確認。対象は Issue #39 のワークストリーム A。表の実績は過去走単位の収集・再解析を示す。

| 管轄・出典 | パーサ | 収集パス | 実データ検証 |
| --- | --- | --- | --- |
| フランス・France Galop | `france_tracking_pdf` | `DataCollectorAgent → JRAPipeline → _apply_france_history`。馬名・同定 URL のマップが必要 | 既報の2025年凱旋門賞に加え、2025-09-07 フォワ賞のビザンチンドリームで収集・原典再解析を確認 |
| 香港・HKJC | `hkjc_history_sectionals` | `DataCollectorAgent → JRAPipeline → _apply_hkjc_history` を追加。5走埋込済みの分岐にも接続 | 2026-04-26 沙田9R QE2世Cのマスカレードボールで収集・原典再解析を確認 |
| 英国・BHA | 汎用パーサ未実装。承認付き `parse_imperial_proxy_observation` は既存 | 現地の本番収集経路なし | 2025年ジャパンカップのダノンデサイルで `SOURCE_ROUTE_UNAVAILABLE` を確認 |
| アイルランド・HRI | 汎用パーサ未実装 | 現地の本番収集経路なし | 調査のみ。対象のレース単位で個別タイムと区間を再解析できる一次資料の組合せは未確定 |
| UAE | 現地パーサ未実装 | 現地の本番収集経路なし | ダノンデサイルのドバイシーマクラシックで `SOURCE_ROUTE_UNAVAILABLE` を確認 |
| 米国・サウジアラビア・カタール等 | 現地パーサ未実装 | 現地の本番収集経路なし | カタールは収集経路なしの回帰テストあり。米・サウジは実データ監査未確認 |
| 地方競馬・NAR | `nar_history_result` | 監査で渡されたアーティファクトを再解析。専用の自動取得経路は確認できず | パーサの回帰テストあり |
| JRA の海外発売レース結果 | `overseas_jra_result` | 監査入力と HKJC／フランスの独立同定資料に利用 | QE2世Cで同定に使用。結果表の着差欄は時計に使わない |

## フォワ賞

France Galop の公式番組表と追跡 PDF は2025-09-07 パリロンシャン2R、2400m、9番 BYZANTINE DREAM で一致。JRA の公式プロフィールは登録 ID `pw01dud002021105587/8F`、和名、英字名、同日のフォワ賞、仏・芝2400m、1着を掲載している。追跡 PDF をバイト列の SHA256 で検証し、公式プロフィールは URL の登録 ID と SHA256、和英馬名、日付、レース、距離、着順を再解析で照合する。この経路はフォワ賞に限定し、他レースは従来どおり JRADB 結果表を使う。馬名や結果を手入力する既存の履歴 CSV の用途とは異なるため、既存の同定 URL マップを利用した。 [France Galop 公式番組表](https://www7.france-galop.com/pgm/2025_09_07_0001.pdf) · [France Galop 追跡 PDF](https://www7.france-galop.com/Casaques/Tracking/20250907LON02_last_times_fr.pdf) · [JRA 競走馬情報](https://www.jra.go.jp/JRADB/accessU.html?CNAME=pw01dud002021105587%2F8F)

原典は `time=148.32` 秒、最終600m `33.09` 秒。実データの予測入力監査では時計と最終600mが `OBSERVED`。JRA の既存時計表示は 148.3 秒で、既存の入力正規化は最終600mを 33.1 秒として監査する。距離標での位置はコーナー通過順に転記せず、`passing_order` は `SOURCE_FIELD_MISSING`。限定監査のゲートは `BLOCKED_UNAPPROVED_IMPUTATION`。

## 香港

HKJC の200m区間4本のうち末尾3本を合算。マスカレードボールは `11.29 + 10.72 + 10.95 = 32.96` 秒、個別時計は `2:00.80`。JRA 結果表と日付、開催地、競走、距離、馬番、着順、登録 ID を照合した。400m境界の順位は別項目に保存し、監査の `passing_order` は `SOURCE_FIELD_MISSING`。時計と最終600mは `OBSERVED`、限定監査は `BLOCKED_UNAPPROVED_IMPUTATION`。登録済みレース以外は同定経路未登録として記録する。 [HKJC 区間時計](https://racing.hkjc.com/en-us/local/information/archive/displaysectionaltime?RaceNo=9&racedate=26%2F04%2F2026) · [JRA 結果](https://www.jra.go.jp/JRADB/accessSS.html?CNAME=pk01sde011022026010109%2F30)

## 英国・アイルランド

英国は BHA の公式レース時計が勝馬の決勝線通過までを指す。勝馬以外の個別時計には別の根拠が必要。アイルランドは HRI の2025-09-13 Leopardstown を含む公式フォームブックを確認したが、該当レースの馬別実測ラップは確認できなかった。Racing TV／RaceiQ は馬別のハロン区間を公開している一方、カランダガンについて JRA の登録 ID に結び付く形で公式の時計・区間・独立同定を一式確保できなかった。現段階では両国の本番パーサ追加を見送る。既存の明示承認・原典再解析付き 603.504m プロキシは別経路。 [BHA 時計の定義](https://www.britishhorseracing.com/regulation/handicapping-tools/) · [HRI 公式フォームブック](https://www.hri-ras.ie/getmedia/eae8c6e7-df5a-4ed3-9c2e-add1f0393fbc/Formbook-18th-September-2025.pdf) · [Racing TV 区間の定義](https://www.racingtv.com/raceiq/sectionals-tab)

## ジャパンカップ想定ドライラン

2025-11-30 東京12R を対象に、発走前に公表されたカランダガンの JRA プロフィール PDF から直近5走を転記し、同じレースのダノンデサイルは JRADB 競走馬情報を再解析した。カランダガンの時計は PDF で確認できるが、出馬表に JRADB 登録馬 ID のリンクがなく、PDF は現行監査の再解析対象外。従って原典 SHA256 を保存した上で、監査結果は `SOURCE_UNVERIFIED` のまま保持した。 [JRA 出馬表](https://www.jra.go.jp/keiba/thisweek/2025/1130_1/syutsuba.html) · [カランダガン全成績](https://www.jra.go.jp/news/gaikokuba/2025/pdf/jc01_s.pdf) · [ダノンデサイルの JRADB 履歴](https://www.jra.go.jp/JRADB/accessU.html?CNAME=pw01dud002021105143%2FBF)

| カランダガンの過去走 | JRA掲載時計 | 収集経路の判定 |
| --- | --- | --- |
| 2025-10-18 英チャンピオンS | 123.1秒 | 英国の本番経路なし |
| 2025-07-26 キングジョージVI世＆QES | 149.7秒 | 英国の本番経路なし |
| 2025-06-29 サンクルー大賞 | 148.2秒 | 仏経路あり。独立同定未解決 |
| 2025-06-06 コロネーションC | 156.2秒 | 英国の本番経路なし |
| 2025-04-05 ドバイシーマクラシック | 147.2秒 | UAEの本番経路なし |

ダノンデサイルは英国・UAEで時計が `OBSERVED`、通過順など計10項目が `SOURCE_ROUTE_UNAVAILABLE`。JRA の既存上がりフォールバックは `SUBSTITUTED` として検出され、採用していない。両馬とも `purpose=prediction` の監査ゲートは `BLOCKED_UNAPPROVED_IMPUTATION`。対象は海外履歴を含む2頭の切り出しであり、全頭収集や2026年の出走確定馬の監査は未実施。原典、収集 JSON、監査 JSON は元の作業ディレクトリの `report/races/foreign_history_validation_20261109/` に保存。

## 設定・再検証

`data/manual/foreign_horse_names.json` は登録馬 ID と公式英字馬名を対応付ける。`foreign_identity_sources.json` と `hkjc_history_sources.json` は過去走日・レース名をキーに公式 URL を指定する。HKJC では現地レース番号も指定する。実行時は出典を再解析し、同定に成功した値だけを既存履歴へ取り込む。元資料の値はアーティファクトに残る。

`scripts/check_quality.sh` と全テスト、`coverage report` を実行。対象の回帰テストには誤 ID・馬名・日付・距離・着順、無効な香港の時計、出典未登録・取得失敗を含む。時計の例外メソッドと監査の合格条件は変更していない。
