import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import refresh


INDEX = b"""
<form name="stForm">
  <input type="hidden" name="is_searching" value="0">
  <select id="Select1"><option value="0">All</option><option value="27">Home</option></select>
  <script>var new_types = new Array (new Array(27,173,'Home, design'), []);</script>
</form>
"""
RESULT = b"""
<form name="stForm">
  <input type="hidden" name="cmb_main_type" value="27">
  <input type="hidden" name="cmb_second_type_i" value="173">
  <input type="hidden" name="cmb_region2" value="0">
  <table id="object_table"><tbody>
    <tr onclick="mclick(1);"><td><strong class="title">First</strong></td><td>10%</td></tr>
    <tr onclick="mclick(2);"><td>Second</td><td>20%</td></tr>
  </tbody></table>
</form>
"""


class CrawlIndexTest(unittest.TestCase):
    @patch.object(refresh.time, "sleep")
    @patch.object(refresh, "fetch", side_effect=[INDEX, RESULT])
    def test_reads_merchants_from_the_public_form(self, fetch, _sleep):
        stores = refresh.crawl_index(None, "Home", "Home, design")
        self.assertEqual(list(stores), ["1", "2"])
        self.assertEqual(stores["1"]["discounts"], ["10%"])
        self.assertEqual(stores["1"]["categories"], [["Home", "Home, design"]])
        posted = fetch.call_args.args[2]
        self.assertEqual((posted["is_searching"], posted["second_type"], posted["region"]), ("0", "173", "0"))

    @patch.object(refresh, "fetch", side_effect=[INDEX, RESULT.replace(b'value="173"', b'value="42"')])
    def test_rejects_results_for_the_wrong_category(self, _fetch):
        with self.assertRaisesRegex(RuntimeError, "wrong filters"):
            refresh.crawl_index(None, None, None)

    @patch.object(refresh.time, "sleep")
    @patch.object(refresh, "fetch", side_effect=[INDEX, "<font>אין תוצאות המתאימות לחיפוש</font>"])
    def test_accepts_the_explicit_no_results_page(self, _fetch, _sleep):
        self.assertEqual(refresh.crawl_index(None, None, None), {})

    @patch.object(refresh.time, "sleep")
    def test_retries_connection_failure_and_surfaces_rate_limit(self, sleep):
        client = Mock()
        limited = HTTPError(refresh.INDEX_URL, 429, "Too Many Requests", {}, None)
        client.open.side_effect = [ConnectionResetError(), limited]
        with self.assertRaises(HTTPError) as error:
            refresh.fetch(client, refresh.INDEX_URL, {"second_type": "173"})
        self.assertIs(error.exception, limited)
        self.assertEqual(client.open.call_count, 2)
        sleep.assert_called_once_with(2)
