# 成果物の保存・整理ポリシー

## 目的

予測の再現性を維持しながら、実行のたびに増える中間成果物でローカル容量が圧迫されない状態を作ります。削除を先に行わず、保持・archive・再生成可能の3区分で扱います。

## 保存区分

| 対象 | ローカル保持 | 期間後の扱い | 理由 |
|---|---:|---|---|
| ソースコード、設定、`data/manual/` | 無期限 | Gitで保持 | 再現と手動補正の正本 |
| `report/final_predictions/` | 365日 | 年単位でarchive | 最終判断の監査証跡 |
| `data/collected/` | 180日 | 月単位でarchive | 再分析に使う取得済みデータ |
| `report/races/` | 30日 | 月単位でarchive | 容量が大きい中間ステージ |
| `report/win5/` | 30日 | 月単位でarchive | 容量が大きい中間ステージ |
| `report/stages/` | 最新実行 | 次回成功後に旧版をarchive | 現在のパイプライン状態 |
| `output/`、`tmp/` | 7日 | 再生成できるものを整理 | 一時出力・デバッグ用途 |
| `.venv/`、cache、coverage | 必要期間のみ | 再生成 | 依存物・テスト生成物 |

## 監査

監査スクリプトは候補を表示するだけで、移動や削除は行いません。

```bash
python scripts/audit_artifact_storage.py
```

基準日や保持期間を試算する場合:

```bash
python scripts/audit_artifact_storage.py \
  --as-of 2026-08-29 \
  --retention-days 60
```

`archive-candidate` は即時削除対象ではありません。次の順番で扱います。

1. 日付とレースIDを確認する
2. 月単位のarchiveへ移す
3. archiveのファイル数とchecksumを確認する
4. 必要な再分析ができることを確認する
5. 元データを整理する

日付で判定できない `direct_*` などは `manual-review` とし、自動整理しません。

## 次の一歩

`python scripts/audit_artifact_storage.py --as-of 2026-08-29` を実行し、最初のarchive候補を確認します。
