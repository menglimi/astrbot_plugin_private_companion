"""NMC alert fallback: city-name matching, severity and bounded times."""

import unittest
from datetime import datetime, timezone

from tests import astrbot_data_root  # noqa: F401 - isolate the SDK before import
from astrbot_host.nmc_alerts import CHINA_TZ, normalize_nmc_alerts
from companion_v2.agenda.planning_config import WeatherLocation


def payload(*titles):
    return {"data": {"page": {"list": [
        {"alertid": f"id-{index}", "issuetime": "2026/11/06 17:50", "title": title,
         "url": f"/publish/alarm/{index}.html"}
        for index, title in enumerate(titles)
    ]}}}


class NMCAlertTests(unittest.TestCase):
    def setUp(self):
        self.location = WeatherLocation("东莞", 22.98, 114.02, "", "")
        self.now = datetime(2026, 11, 6, 18, 0, tzinfo=CHINA_TZ)

    def test_matching_city_title_becomes_a_moderate_alert(self):
        observation = normalize_nmc_alerts(payload(
            "广东省东莞市气象台发布暴雨黄色预警信号",
            "广东省惠州市气象台发布暴雨黄色预警信号",
        ), self.location, self.now)
        self.assertEqual(len(observation.alerts), 1)
        alert = observation.alerts[0]
        self.assertEqual(alert.identifier, "id-0")
        self.assertEqual(alert.severity, "moderate")
        self.assertEqual(alert.sender_name, "广东省东莞市气象台")
        self.assertEqual(alert.issued_at.tzinfo, CHINA_TZ)
        self.assertGreater(alert.expires_at, alert.issued_at)

    def test_unrelated_place_does_not_invent_an_alert(self):
        observation = normalize_nmc_alerts(payload(
            "广东省惠州市气象台发布暴雨黄色预警信号",
        ), self.location, self.now)
        self.assertEqual(observation.alerts, ())

    def test_place_with_city_suffix_matches_without_it(self):
        location = WeatherLocation("东莞市", 22.98, 114.02, "", "")
        observation = normalize_nmc_alerts(payload(
            "广东省东莞市气象台发布暴雨黄色预警信号",
        ), location, self.now)
        self.assertEqual(len(observation.alerts), 1)


if __name__ == "__main__":
    unittest.main()
