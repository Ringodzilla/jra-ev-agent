# 🏇 AI競馬予測 v3.1 Codex System

## 0. 目的

本システムの目的は以下：

- 実馬券における期待値（EV）の最大化
- 再現性のある意思決定の自動化
- データ → 分析 → 投資判断までの完全自動化

本システムは単一AIではなく、
**役割分離されたマルチエージェント構造で動作する**

---

## 1. システム構造

本プロジェクトは2層構造：

### データ層
- JRAデータ取得
- race_last5.csv生成

### 意思決定層
- 能力評価
- 展開分析
- EV算出
- 馬券生成

---

## 2. エージェント構成（競馬専用）

### 🧾 data_collector
責務：
- レース情報取得
- CSV生成

出力（JSON）:
{
  "race_info": {},
  "horses": []
}

禁止：
- 分析
- 推測

---

### 📊 analyzer
責務：
- 能力点（100点）
- コース補正
- 展開補正
- 斤量補正
- 騎手補正

出力：
{
  "scores": [
    {
      "horse": "",
      "ability": 0,
      "course": 0,
      "pace": 0,
      "weight": 0,
      "jockey": 0,
      "S": 0
    }
  ]
}

---

### 🔄 simulator
責務：
- ペース別シミュレーション

シナリオ：
- ハイ
- ミドル
- スロー

出力：
{
  "probabilities": [
    {
      "horse": "",
      "high": 0,
      "mid": 0,
      "slow": 0,
      "final": 0
    }
  ]
}

---

### 💰 ev_calculator
責務：
- EV計算

計算式：
EV = 勝率 × オッズ

出力：
{
  "ev_table": [
    {
      "horse": "",
      "win_prob": 0,
      "odds": 0,
      "ev": 0
    }
  ]
}

---

### 🎯 bet_builder
責務：
- 買い目生成
- 全券種候補の横断最適化

対象券種：
- 単勝
- 複勝
- ワイド
- 枠連
- 馬連
- 馬単
- 三連複
- 三連単

出力：
{
  "core": [],
  "partner": [],
  "long": [],
  "bet_types_considered": [],
  "candidate_counts": {},
  "tickets": [
    {
      "bet_type": "",
      "horse_number": "",
      "horse_name": "",
      "hit_prob": 0,
      "predicted_odds": 0,
      "ev": 0,
      "stake": 0
    }
  ]
}

---

### 🔍 reviewer
責務：
- 実戦評価

チェック：
- EV妥当性
- 展開整合性
- 過剰人気の排除

出力：
{
  "status": "OK | NG",
  "reason": "",
  "fix": ""
}

---

## 3. ワークフロー

必ず以下順序：

1. data_collector
2. analyzer
3. simulator
4. ev_calculator
5. bet_builder
6. reviewer

---

## 4. 再実行ルール

- reviewerがNGの場合のみ再実行
- 修正はreviewerの指示のみ反映

---

## 5. JSON I/Oルール（最重要）

- 全出力はJSON
- 曖昧表現禁止
- 推測禁止

---

## 6. EVモデル（必須）

以下に従う：

S = 能力 + コース + 展開 + 斤量 + 騎手

勝率：
Softmax使用

EV：
EV = 勝率 × オッズ

---

## 7. 実戦ルール

- 単勝EVと連系EVは分離
- 前受け馬を過小評価しない
- 小回りで差し馬を過大評価しない
- 4角位置を重視

---

## 8. 禁止事項

- 人気順トレース
- 上がり最速信仰
- データなし推測
- 複数役割の混在

---

## 9. テストルール

- 必ず再計算を実行
- 出力整合性チェック必須

---

## 10. 最重要ルール

「役割を跨ぐな」

役割違反はすべて無効とする
