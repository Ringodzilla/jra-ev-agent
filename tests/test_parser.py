from pathlib import Path
import unittest

try:
    from jra_scraper.parser import JRAParser
    HAS_BS4 = True
except ModuleNotFoundError:
    HAS_BS4 = False


FIX = Path(__file__).parent / "fixtures"


@unittest.skipUnless(HAS_BS4, "beautifulsoup4 is required for parser tests")
class TestJRAParser(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = JRAParser("https://www.jra.go.jp")

    def test_parse_race_list_dedupes_and_extracts_structured_race_id(self):
        html = (FIX / "race_list.html").read_text(encoding="utf-8")
        races = self.parser.parse_race_list(html)
        self.assertEqual(2, len(races))
        self.assertTrue(races[0].race_id.startswith("20260301_中山_11"))

    def test_parse_race_detail_extracts_horses_and_ids(self):
        html = (FIX / "race_detail.html").read_text(encoding="utf-8")
        horses = self.parser.parse_race_detail(html, race_id="r1", race_name="11R")
        self.assertEqual(2, len(horses))
        self.assertEqual("サンプルホースA", horses[0].horse_name)
        self.assertTrue(horses[0].horse_id)
        self.assertEqual("1", horses[0].horse_number)

    def test_parse_race_detail_repairs_shifted_rows(self):
        html = """
        <html><body>
        <table class="race_table_01">
          <tr><th>枠</th><th>馬番</th><th>馬名</th><th>騎手</th><th>斤量</th><th>単勝</th></tr>
          <tr><td>1</td><td>1</td><td class="horse"><a href="/JRADB/accessU.html?CNAME=a1">サンプルホースA</a></td><td>戸崎</td><td>57.0</td><td>3.2</td></tr>
          <tr><td>1</td><td>2</td><td class="horse"><a href="/JRADB/accessU.html?CNAME=a2">サンプル</a></td><td>ホースB</td><td>ルメール</td><td>56</td><td>4.8</td></tr>
        </table>
        </body></html>
        """
        horses = self.parser.parse_race_detail(html, race_id="20260329_中山_11", race_name="11R")
        self.assertEqual(2, len(horses))
        self.assertEqual("サンプル ホースB", horses[1].horse_name)
        self.assertEqual("2", horses[1].horse_number)
        self.assertEqual("ルメール", horses[1].current_jockey)
        self.assertEqual("4.8", horses[1].current_odds)

    def test_parse_race_detail_extracts_embedded_history_and_popularity(self):
        html = """
        <html><body>
        <table class="race_table_01">
          <tr><th>枠</th><th>馬番</th><th>馬名 / 単勝オッズ(人気)</th><th>性齢 / 斤量 / 騎手</th></tr>
          <tr>
            <td class="waku">1</td>
            <td class="num">1</td>
            <td class="horse">
              <div class="name_line">
                <div class="name"><a href="/JRADB/accessU.html?CNAME=a1">サンプルホースA</a></div>
                <div class="odds"><div class="odds_line"><span class="num"><strong>3.2</strong></span><span class="pop_rank">(1<span>番人気</span>)</span></div></div>
              </div>
            </td>
            <td class="jockey">
              <p class="weight">55.0kg</p>
              <p class="jockey"><a href="#">戸崎 圭太</a></p>
            </td>
            <td class="past p1">
              <div class="date_line"><div class="date">2026年3月1日</div><div class="rc">阪神</div></div>
              <div class="race_line"><div class="name"><a href="/JRADB/accessS.html?CNAME=s1">チューリップ賞</a></div></div>
              <div class="place_line"><div class="place">2着</div><div class="num"><span class="pop">3<span>番人気</span></span></div></div>
              <div class="info_line1"><div class="jockey">戸崎 圭太</div><div class="weight">55.0kg</div></div>
              <div class="info_line2"><span class="dist">1600芝</span><p class="time">1:33.9</p><span class="condition">良</span></div>
              <div class="info_line3"><div class="corner_list"><ul><li>5</li><li>4</li></ul></div><div class="f3">3F 33.8</div></div>
            </td>
          </tr>
        </table>
        </body></html>
        """
        horses = self.parser.parse_race_detail(html, race_id="20260412_阪神_11", race_name="桜花賞")
        self.assertEqual(1, len(horses))
        self.assertEqual("1", horses[0].current_popularity)
        self.assertEqual("戸崎 圭太", horses[0].current_jockey)
        self.assertEqual("55.0", horses[0].assigned_weight)
        self.assertEqual(1, len(horses[0].embedded_history))
        self.assertEqual("33.8", horses[0].embedded_history[0]["last_3f"])
        self.assertEqual("3", horses[0].embedded_history[0]["popularity"])

    def test_parse_race_detail_extracts_header_metadata_for_direct_race_id(self):
        html = """
        <html><body>
        <div class="race_header">
          <div class="cell date">2026年5月17日（日曜） 2回東京8日</div>
          <div class="race_number"><img alt="11レース"></div>
          <span class="race_name">ヴィクトリアマイル</span>
          <div class="cell course">
            <span class="cap">コース：</span>1,600<span class="unit">メートル</span><span class="detail">（芝・左）</span>
          </div>
        </div>
        <table class="race_table_01">
          <tr><th>枠</th><th>馬番</th><th>馬名</th><th>騎手</th><th>斤量</th><th>単勝</th></tr>
          <tr><td>1</td><td>1</td><td class="horse"><a href="/JRADB/accessU.html?CNAME=a1">サンプルホースA</a></td><td>戸崎</td><td>57.0</td><td>3.2</td></tr>
        </table>
        </body></html>
        """
        horses = self.parser.parse_race_detail(html, race_id="direct_abc123", race_name="JRAレース")
        self.assertEqual(1, len(horses))
        self.assertEqual("ヴィクトリアマイル", horses[0].race_name)
        self.assertEqual("2026-05-17", horses[0].target_race_date)
        self.assertEqual("東京", horses[0].target_track)
        self.assertEqual("11", horses[0].target_race_number)
        self.assertEqual("芝", horses[0].target_surface)
        self.assertEqual("1600", horses[0].target_distance)

    def test_parse_race_detail_keeps_overseas_row_without_horse_link(self):
        html = """
        <html><body>
        <table class="basic">
          <tr>
            <th>馬番</th>
            <th>馬名 / 単勝オッズ(人気)</th>
            <th>生産国 性齢/毛色 負担重量 騎手名</th>
            <th>前走</th>
          </tr>
          <tr>
            <td class="num">2</td>
            <td class="horse">
              <div class="name_line">
                <div class="name"><div class="line"><div class="txt">ヴォイッジバブル</div></div></div>
                <div class="odds"><div class="odds_line"><span class="num"><strong>9.0</strong></span><span class="pop_rank">(4<span>番人気</span>)</span></div></div>
              </div>
            </td>
            <td class="jockey">
              <p class="code">AUS</p>
              <p class="weight">57.0kg</p>
              <p class="jockey">C.チャウ</p>
            </td>
            <td class="past p1">
              <div class="date_line"><div class="date">2026年4月6日</div><div class="rc">HK</div></div>
              <div class="race_line"><div class="name">チェアマンT</div></div>
              <div class="place_line"><div class="place">3着</div><div class="num"><span class="pop">4<span>番人気</span></span></div></div>
              <div class="info_line1"><div class="jockey">C.チャウ</div><div class="weight">58.0kg</div></div>
              <div class="info_line2"><span class="dist">1600芝</span><p class="time">1:33.8</p><span class="condition">良</span></div>
              <div class="info_line3"><div class="corner_list"><ul><li>6</li><li>7</li><li>4</li></ul></div><div class="f3"></div></div>
            </td>
          </tr>
        </table>
        </body></html>
        """
        horses = self.parser.parse_race_detail(html, race_id="20260426_シャティン_07", race_name="チャンピオンズマイル")
        self.assertEqual(1, len(horses))
        self.assertEqual("ヴォイッジバブル", horses[0].horse_name)
        self.assertTrue(horses[0].horse_id)
        self.assertEqual("", horses[0].horse_url)
        self.assertEqual("2", horses[0].horse_number)
        self.assertEqual("9.0", horses[0].current_odds)
        self.assertEqual("4", horses[0].current_popularity)
        self.assertEqual("C.チャウ", horses[0].current_jockey)
        self.assertEqual("57.0", horses[0].assigned_weight)
        self.assertEqual("AUS", horses[0].horse_country)
        self.assertEqual(1, len(horses[0].embedded_history))

    def test_parse_race_detail_raises_when_no_horses_found(self):
        html = "<html><body><table class='race_table_01'><tr><th>馬名</th></tr></table></body></html>"
        with self.assertRaisesRegex(ValueError, "No horses parsed"):
            self.parser.parse_race_detail(html, race_id="r1", race_name="11R")

    def test_parse_horse_last5_maps_structured_columns(self):
        html = (FIX / "horse_history.html").read_text(encoding="utf-8")
        rows = self.parser.parse_horse_last5(
            html,
            race_id="r1",
            horse_id="h1",
            horse_name="サンプルホースA",
            horse_url="https://www.jra.go.jp/JRADB/accessU.html?CNAME=x",
        )
        self.assertEqual(5, len(rows))
        self.assertEqual("1", rows[0]["run_index"])
        self.assertIn("pace", rows[0])
        self.assertIn("last_3f", rows[0])
        self.assertIn("track_condition", rows[0])
        self.assertIn("weather", rows[0])
        self.assertIn("passing_order", rows[0])
        self.assertIn("odds", rows[0])
        self.assertIn("popularity", rows[0])

    def test_parse_horse_last5_fills_last3f_fallback_when_missing(self):
        html = """
        <table>
          <tr><th>日付</th><th>レース名</th><th>距離</th><th>着順</th><th>人気</th></tr>
          <tr><td>2026/03/01</td><td>テスト特別</td><td>芝1800</td><td>2</td><td>3</td></tr>
        </table>
        """
        rows = self.parser.parse_horse_last5(
            html,
            race_id="r1",
            horse_id="h1",
            horse_name="サンプルホースA",
            horse_url="https://www.jra.go.jp/JRADB/accessU.html?CNAME=x",
        )
        self.assertEqual("36.0", rows[0]["last_3f"])


if __name__ == "__main__":
    unittest.main()
