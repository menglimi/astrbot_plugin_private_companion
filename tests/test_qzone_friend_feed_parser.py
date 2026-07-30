# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from quart import Quart

from astrbot_plugin_private_companion.page_api_qzone import PrivateCompanionPageApiQzoneMixin
from astrbot_plugin_private_companion.qzone_recent_parser import (
    parse_qzone_h5_index_html,
    parse_recent_feeds,
)


def _legacy_feed(*, uin: int, key: str, text: str, appid: str = "311") -> dict:
    return {
        "uin": uin,
        "key": key,
        "appid": appid,
        "typeid": "0",
        "abstime": 1784772000,
        "nickname": f"用户{uin}",
        "html": (
            f'<li data-uin="{uin}" data-key="{key}">'
            f'<div class="f-info">{text}</div>'
            '<div class="img-box"><img src="https://example.com/preview.jpg" '
            'data-original="https://example.com/original.jpg"></div>'
            "</li>"
        ),
    }


def _h5_feed(*, uin: int, cellid: str, text: str, appid: int = 311) -> dict:
    return {
        "comm": {
            "time": 1784772001,
            "appid": appid,
            "feedstype": 0,
            "curlikekey": f"https://user.qzone.qq.com/{uin}/mood/{cellid}",
            "orglikekey": f"https://user.qzone.qq.com/{uin}/mood/{cellid}",
            "ugcrightkey": cellid,
        },
        "userinfo": {"uin": uin, "nickname": f"好友{uin}"},
        "id": {"cellid": cellid},
        "summary": {"summary": text, "hasmore": False},
        "pic": {
            "picdata": [
                {
                    "photourl": {
                        "small": {"width": 240, "height": 180, "url": "https://example.com/small.jpg"},
                        "origin": {"width": 1920, "height": 1080, "url": "https://example.com/full.jpg"},
                    }
                }
            ]
        },
        "like": {"isliked": True, "num": 3},
    }


class QzoneFriendFeedParserTests(unittest.TestCase):
    def test_legacy_data_data_list_is_kept(self) -> None:
        payload = {"data": {"data": [_legacy_feed(uin=10001, key="legacy-1", text="旧接口好友动态")]}}

        posts = parse_recent_feeds(payload)

        self.assertEqual(1, len(posts))
        self.assertEqual(10001, posts[0].uin)
        self.assertEqual("旧接口好友动态", posts[0].text)
        self.assertEqual(["https://example.com/original.jpg"], posts[0].images)

    def test_nested_vfeeds_parses_multiple_friends(self) -> None:
        payload = {
            "code": 0,
            "data": {
                "feedpage": {
                    "vFeeds": [
                        _h5_feed(uin=10001, cellid="friend-a", text="第一位好友"),
                        _h5_feed(uin=10002, cellid="friend-b", text="第二位好友"),
                    ]
                }
            },
        }
        diagnostics: dict = {}

        posts = parse_recent_feeds(payload, diagnostics)

        self.assertEqual([10001, 10002], [post.uin for post in posts])
        self.assertEqual(2, diagnostics["candidate_count"])
        self.assertEqual(2, diagnostics["parsed_count"])
        self.assertIn("data.feedpage.vfeeds", diagnostics["containers"])

    def test_structured_h5_media_uses_preview_and_original(self) -> None:
        posts = parse_recent_feeds({"vFeeds": [_h5_feed(uin=10003, cellid="photo-1", text="带图动态")]})

        self.assertEqual(1, len(posts))
        self.assertTrue(posts[0].liked)
        self.assertEqual("https://example.com/small.jpg", posts[0].image_items[0]["preview_url"])
        self.assertEqual("https://example.com/full.jpg", posts[0].image_items[0]["full_url"])

    def test_non_311_friend_feed_is_not_silently_discarded(self) -> None:
        payload = {"feeds": [_h5_feed(uin=10004, cellid="app-feed", text="其他可展示动态", appid=202)]}

        posts = parse_recent_feeds(payload)

        self.assertEqual(1, len(posts))
        self.assertEqual("202", posts[0].appid)
        self.assertEqual("其他可展示动态", posts[0].text)

    def test_mixed_self_and_friends_does_not_collapse_to_self(self) -> None:
        payload = {
            "items": [
                _legacy_feed(uin=90000, key="self", text="自己的说说"),
                _legacy_feed(uin=10005, key="friend", text="好友的说说"),
                _legacy_feed(uin=10005, key="friend", text="重复数据"),
            ]
        }
        diagnostics: dict = {}

        posts = parse_recent_feeds(payload, diagnostics)

        self.assertEqual([90000, 10005], [post.uin for post in posts])
        self.assertEqual(1, diagnostics["skipped_duplicate"])

    def test_h5_index_extracts_token_and_frontpage_data(self) -> None:
        html = """
        <html><body><script type="application/javascript">
        window.shine0callback = function () { return "abc123def456"; };
        var FrontPage = new Loader({data: {
          "code": 0,
          "data": {"vFeeds": [
            {"comm": {"time": 1784772002, "appid": 311, "feedstype": 0},
             "userinfo": {"uin": 10006, "nickname": "好友"},
             "id": {"cellid": "h5-index"},
             "summary": {"summary": "首页好友动态"}}
          ]}
        }});
        </script></body></html>
        """

        parsed = parse_qzone_h5_index_html(html)
        posts = parse_recent_feeds(parsed["payload"])

        self.assertEqual("abc123def456", parsed["token"])
        self.assertEqual(1, len(posts))
        self.assertEqual(10006, posts[0].uin)

    def test_invalid_candidate_is_reported_in_diagnostics(self) -> None:
        diagnostics: dict = {}
        payload = {"list": [{"uin": 10007, "content": "缺少动态标识", "id": {}}]}

        posts = parse_recent_feeds(payload, diagnostics)

        self.assertEqual([], posts)
        self.assertEqual(0, diagnostics["candidate_count"])
        self.assertEqual(0, diagnostics["parsed_count"])


class _QzonePagePlugin:
    def __init__(self, h5_payload: dict, legacy_payload: dict | None = None) -> None:
        self.h5_payload = h5_payload
        self.legacy_payload = legacy_payload or {}
        self.legacy_calls = 0

    async def _qzone_get_cookies(self, _event) -> str:
        return "uin=o90000; p_skey=test"

    def _qzone_context_from_cookies(self, _cookie_header: str) -> dict:
        return {"uin": 90000, "gtk": 12345, "qzonetoken": "", "cookie_header": "uin=o90000; p_skey=test"}

    async def _qzone_h5_index_snapshot(self, _event, **_kwargs) -> dict:
        return {"token": "token123", "payload": self.h5_payload}

    async def _qzone_request(self, *_args, **_kwargs) -> dict:
        self.legacy_calls += 1
        return self.legacy_payload


class _QzonePageHarness(PrivateCompanionPageApiQzoneMixin):
    def __init__(self, plugin: _QzonePagePlugin) -> None:
        self.plugin = plugin

    @staticmethod
    def _single_line(value, _limit: int) -> str:
        return str(value or "").strip()

    @staticmethod
    def _clamp_int(value, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _ok(payload: dict) -> dict:
        return payload

    @staticmethod
    def _error(message: str) -> dict:
        return {"error": message}

    @staticmethod
    def _qzone_page_post_payload(post, **_kwargs) -> dict:
        return {"uin": post.uin, "tid": post.tid, "content": post.text}


class QzoneFriendFeedPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_h5_friend_feed_does_not_make_legacy_request(self) -> None:
        plugin = _QzonePagePlugin(
            {"vFeeds": [_h5_feed(uin=10008, cellid="h5-friend", text="H5 好友动态")]}
        )
        harness = _QzonePageHarness(plugin)
        app = Quart(__name__)

        async with app.test_request_context("/qzone/feed?scope=friends&page=1"):
            result = await harness.get_qzone_feed()

        self.assertEqual(0, plugin.legacy_calls)
        self.assertEqual([10008], [item["uin"] for item in result["items"]])
        self.assertEqual("h5", result["feed_source"])

    async def test_h5_own_only_uses_legacy_and_merges_friend(self) -> None:
        plugin = _QzonePagePlugin(
            {"vFeeds": [_h5_feed(uin=90000, cellid="self-only", text="自己的说说")]},
            {"data": {"data": [_legacy_feed(uin=10009, key="legacy-friend", text="旧接口好友动态")]}},
        )
        harness = _QzonePageHarness(plugin)
        app = Quart(__name__)

        async with app.test_request_context("/qzone/feed?scope=friends&page=1"):
            result = await harness.get_qzone_feed()

        self.assertEqual(1, plugin.legacy_calls)
        self.assertEqual([90000, 10009], [item["uin"] for item in result["items"]])
        self.assertEqual("h5+legacy", result["feed_source"])


if __name__ == "__main__":
    unittest.main()
