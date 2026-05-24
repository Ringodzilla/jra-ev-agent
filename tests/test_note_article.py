import unittest
import tempfile
from pathlib import Path

from report.note import build_note_article, generate_note_markdown, write_note
from src.react_workflow import ArticleWriterAgent


class TestNoteArticle(unittest.TestCase):
    def test_write_note_always_syncs_artifact_markdown(self):
        with tempfile.TemporaryDirectory() as td:
            note_path = Path(td) / "note.md"
            artifact_path = write_note(note_path, "# note body")

            self.assertTrue(note_path.exists())
            self.assertEqual(Path(td) / "note_artifact.md", artifact_path)
            self.assertTrue(artifact_path.exists())
            self.assertEqual(note_path.read_text(encoding="utf-8"), artifact_path.read_text(encoding="utf-8"))

    def test_generate_note_markdown_is_paste_ready(self):
        ev_rows = [
            {
                "race_id": "r1",
                "horse_id": "h10",
                "horse_name": "ナムラコスモス",
                "horse_number": "10",
                "win_prob": "0.053034",
                "current_odds": "21.1",
                "predicted_odds": "21.086224",
                "predicted_odds_source": "live",
                "ev_current": "1.119015",
            },
            {
                "race_id": "r1",
                "horse_id": "h1",
                "horse_name": "フェスティバルヒル",
                "horse_number": "1",
                "win_prob": "0.054268",
                "current_odds": "20.5",
                "predicted_odds": "20.638951",
                "predicted_odds_source": "live",
                "ev_current": "1.112499",
            },
        ]
        tickets = {
            "tickets": [
                {
                    "race_id": "r1",
                    "horse_id": "h10",
                    "horse_name": "ナムラコスモス",
                    "horse_number": "10",
                    "stake": 100,
                    "win_prob": "0.053034",
                    "win_odds": "21.1",
                    "ev_current": "1.119015",
                }
            ]
        }
        review = {"status": "OK", "reason": "quality gates passed"}
        quality_report = {"issue_count": 0, "live_snapshot_count": 8}
        race_config = {
            "race_name": "第86回桜花賞GⅠ",
            "race_date": "2026-04-12",
            "track": "阪神",
            "race_number": 11,
            "post_time": "15:40",
            "surface": "芝",
            "distance": "1600",
            "source_url": "https://example.test/race",
            "note_title": "第86回桜花賞GⅠ EV分析",
        }
        prediction_context = {
            "odds_captured_at_latest": "2026-04-12T03:55:21.821376+00:00",
        }

        markdown = generate_note_markdown(
            "第86回桜花賞GⅠ",
            ev_rows,
            tickets,
            review=review,
            quality_report=quality_report,
            race_config=race_config,
            prediction_context=prediction_context,
        )

        self.assertIn("# 【4月12日（日） 阪神 11R 15:40発走｜第86回桜花賞GⅠ】競馬予想 芝1600m", markdown)
        self.assertIn("## 結論", markdown)
        self.assertIn("## 買い目", markdown)
        self.assertIn("## AI評価上位", markdown)
        self.assertIn("単勝 10 ナムラコスモス 100円", markdown)
        self.assertIn("開催日: 2026-04-12", markdown)
        self.assertIn("予想時点: 2026-04-12 12:55 JST", markdown)
        self.assertIn("最終判定: 買い目提示OK", markdown)
        self.assertIn("出馬表と過去走の解析に大きな問題はありません。", markdown)

    def test_article_writer_outputs_hold_article_when_review_ng(self):
        agent = ArticleWriterAgent()
        article = agent.run(
            [
                {
                    "race_name": "第86回桜花賞GⅠ",
                    "race_date": "2026-04-12",
                    "track": "阪神",
                    "race_number": 11,
                    "post_time": "15:40",
                    "surface": "芝",
                    "distance": "1600",
                    "note_title": "第86回桜花賞GⅠ EV分析",
                }
            ],
            ev_rows=[
                {
                    "race_id": "r1",
                    "horse_id": "h7",
                    "horse_name": "アランカール",
                    "horse_number": "7",
                    "win_prob": "0.11",
                    "current_odds": "6.7",
                    "predicted_odds": "7.3",
                    "predicted_odds_source": "structural",
                    "ev_current": "0.737",
                }
            ],
            ticket_plan={
                "tickets": [
                    {
                        "race_id": "r1",
                        "bet_type": "wide",
                        "horse_id": "h7|h8",
                        "horse_name": "アランカール - リリージョワ",
                        "horse_number": "7-8",
                        "horse_names": ["アランカール", "リリージョワ"],
                        "horse_numbers": ["7", "8"],
                        "stake": 100,
                        "hit_prob": "0.188",
                        "wide_odds_est": "6.2",
                        "ev_current": "1.166",
                    }
                ]
            },
            review={"status": "NG", "reason": "predicted/current EV divergence detected"},
            quality_report={"issue_count": 0, "live_snapshot_count": 4},
            odds_snapshots=[
                {
                    "race_id": "r1",
                    "horse_number": "7",
                    "captured_at": "2026-04-12T03:55:21.821376+00:00",
                }
            ],
        )

        self.assertEqual("hold", article["status"])
        self.assertFalse(article["publish_ready"])
        self.assertIn("今回は購入見送りです。", article["markdown"])
        self.assertIn("最終判断は見送りですが、ロジック上はワイド 7-8 アランカール - リリージョワが参考候補として残っています。", article["markdown"])
        self.assertIn("最終判断は見送りですが、ロジック上の参考候補は残っています。", article["markdown"])
        self.assertIn("参考候補: ワイド 7-8 アランカール - リリージョワ 100円 / EV 1.166", article["markdown"])
        self.assertIn("直前のオッズ変動に対して期待値が安定しきらないため", article["markdown"])
        self.assertIn("予想時点: 2026-04-12 12:55 JST", article["markdown"])

    def test_build_note_article_returns_structured_metadata(self):
        article = build_note_article(
            "サンプルレース",
            ev_rows=[],
            tickets={"tickets": []},
            review={"status": "OK", "reason": "quality gates passed"},
            quality_report={"issue_count": 0, "live_snapshot_count": 0},
            race_config={
                "race_date": "2026-04-12",
                "track": "中山",
                "race_number": 9,
                "post_time": "14:15",
                "surface": "ダート",
                "distance": "2400",
            },
        )

        self.assertEqual("ready", article["status"])
        self.assertIn("markdown", article)
        self.assertIn("headline", article)
        self.assertEqual("【4月12日（日） 中山 9R 14:15発走｜サンプルレース】競馬予想 ダート2400m", article["title"])

    def test_build_note_article_uses_ev_row_race_metadata_when_config_is_sparse(self):
        article = build_note_article(
            "JRAレース",
            ev_rows=[
                {
                    "race_id": "r1",
                    "horse_id": "h1",
                    "horse_name": "A",
                    "horse_number": "1",
                    "target_race_date": "2026-05-17",
                    "target_track": "東京",
                    "target_race_number": "11",
                    "target_surface": "芝",
                    "target_distance": "1600",
                    "win_prob": "0.1",
                    "current_odds": "9.0",
                    "ev_current": "0.9",
                }
            ],
            tickets={"tickets": []},
            review={"status": "NG", "reason": "predicted/current EV divergence detected"},
            quality_report={"issue_count": 0, "live_snapshot_count": 0},
            race_config={"source_url": "https://example.test/race"},
        )

        self.assertEqual("【5月17日（日） 東京 11R｜JRAレース】競馬予想 芝1600m", article["title"])
        self.assertIn("開催日: 2026-05-17", article["markdown"])
        self.assertIn("開催情報: 東京11R", article["markdown"])

    def test_generate_note_markdown_handles_wide_ticket(self):
        markdown = generate_note_markdown(
            "サンプルレース",
            ev_rows=[
                {
                    "race_id": "r1",
                    "horse_id": "h1",
                    "horse_name": "A",
                    "horse_number": "1",
                    "win_prob": "0.22",
                    "current_odds": "4.2",
                    "predicted_odds": "4.0",
                    "predicted_odds_source": "live",
                    "ev_current": "0.924",
                }
            ],
            tickets={
                "tickets": [
                    {
                        "race_id": "r1",
                        "bet_type": "wide",
                        "horse_id": "h1|h2",
                        "horse_name": "A - B",
                        "horse_number": "1-2",
                        "horse_names": ["A", "B"],
                        "horse_numbers": ["1", "2"],
                        "stake": 200,
                        "hit_prob": "0.241",
                        "wide_odds_est": "5.8",
                        "ev_current": "1.3978",
                    }
                ]
            },
            review={"status": "OK", "reason": "quality gates passed"},
            quality_report={"issue_count": 0, "live_snapshot_count": 3},
        )

        self.assertIn("ワイド 1-2 A - B 200円", markdown)
        self.assertIn("推定ワイドオッズ 5.8倍", markdown)

    def test_generate_note_markdown_handles_exotic_tickets(self):
        markdown = generate_note_markdown(
            "サンプルレース",
            ev_rows=[],
            tickets={
                "tickets": [
                    {
                        "race_id": "r1",
                        "bet_type": "place",
                        "horse_id": "h1",
                        "horse_name": "A",
                        "horse_number": "1",
                        "stake": 200,
                        "hit_prob": "0.48",
                        "place_odds_est": "2.4",
                        "ev_current": "1.152",
                    },
                    {
                        "race_id": "r1",
                        "bet_type": "wakuren",
                        "horse_id": "frame:1|frame:2",
                        "horse_name": "1枠 - 2枠",
                        "horse_number": "1-2",
                        "frame_numbers": ["1", "2"],
                        "stake": 100,
                        "hit_prob": "0.15",
                        "wakuren_odds_est": "8.2",
                        "ev_current": "1.23",
                    },
                    {
                        "race_id": "r1",
                        "bet_type": "umaren",
                        "horse_id": "h1|h2",
                        "horse_name": "A - B",
                        "horse_number": "1-2",
                        "horse_names": ["A", "B"],
                        "horse_numbers": ["1", "2"],
                        "stake": 100,
                        "hit_prob": "0.12",
                        "umaren_odds_est": "9.4",
                        "ev_current": "1.128",
                    },
                    {
                        "race_id": "r1",
                        "bet_type": "umatan",
                        "horse_id": "h1|h2",
                        "horse_name": "A -> B",
                        "horse_number": "1->2",
                        "horse_names": ["A", "B"],
                        "horse_numbers": ["1", "2"],
                        "stake": 100,
                        "hit_prob": "0.052",
                        "umatan_odds_est": "24.0",
                        "ev_current": "1.248",
                    },
                    {
                        "race_id": "r1",
                        "bet_type": "sanrenpuku",
                        "horse_id": "h1|h2|h3",
                        "horse_name": "A - B - C",
                        "horse_number": "1-2-3",
                        "horse_names": ["A", "B", "C"],
                        "horse_numbers": ["1", "2", "3"],
                        "stake": 100,
                        "hit_prob": "0.06",
                        "trio_odds_est": "22.5",
                        "ev_current": "1.35",
                    },
                    {
                        "race_id": "r1",
                        "bet_type": "sanrentan",
                        "horse_id": "h1|h2|h3",
                        "horse_name": "A -> B -> C",
                        "horse_number": "1->2->3",
                        "horse_names": ["A", "B", "C"],
                        "horse_numbers": ["1", "2", "3"],
                        "stake": 100,
                        "hit_prob": "0.018",
                        "trifecta_odds_est": "82.0",
                        "ev_current": "1.476",
                    },
                ]
            },
            review={"status": "OK", "reason": "quality gates passed"},
            quality_report={"issue_count": 0, "live_snapshot_count": 3},
        )

        self.assertIn("複勝 1 A 200円", markdown)
        self.assertIn("推定複勝オッズ 2.4倍", markdown)
        self.assertIn("枠連 1-2 100円", markdown)
        self.assertIn("推定枠連オッズ 8.2倍", markdown)
        self.assertIn("馬連 1-2 A - B 100円", markdown)
        self.assertIn("推定馬連オッズ 9.4倍", markdown)
        self.assertIn("馬単 1→2 A → B 100円", markdown)
        self.assertIn("推定馬単オッズ 24.0倍", markdown)
        self.assertIn("三連複 1-2-3 A - B - C 100円", markdown)
        self.assertIn("推定三連複オッズ 22.5倍", markdown)
        self.assertIn("三連単 1→2→3 A → B → C 100円", markdown)
        self.assertIn("推定三連単オッズ 82.0倍", markdown)

    def test_generate_note_markdown_shows_reference_candidate_without_formal_ticket(self):
        markdown = generate_note_markdown(
            "サンプルレース",
            ev_rows=[
                {
                    "race_id": "r1",
                    "horse_id": "h1",
                    "horse_name": "A",
                    "horse_number": "1",
                    "win_prob": "0.18",
                    "current_odds": "5.1",
                    "predicted_odds": "5.8",
                    "predicted_odds_source": "structural",
                    "ev_current": "0.918",
                }
            ],
            tickets={
                "tickets": [],
                "wide": ["A - B"],
            },
            review={"status": "NG", "reason": "predicted/current EV divergence detected"},
            quality_report={"issue_count": 0, "live_snapshot_count": 3},
        )

        self.assertIn("ロジック上はワイド A - Bが参考候補として残っています。", markdown)
        self.assertIn("参考候補: ワイド A - B", markdown)
        self.assertIn("正式な買い目候補はありませんが、参考候補は 1 点あります。", markdown)


if __name__ == "__main__":
    unittest.main()
