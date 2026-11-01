from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from companion_v2.execution.models import digest
from companion_v2.foundation.refs import ScopeKind
from companion_v2.proactive.balance_events import SOURCE_OWNER as BALANCE_OWNER
from companion_v2.proactive.material_pool import (read_active_material_pool, read_material_pool,
                                                  read_proactive_contact_traces, read_role_material_pool,
                                                  read_shared_material_pool)
from companion_v2.proactive.news_events import SOURCE_OWNER as NEWS_OWNER
from companion_v2.proactive.official_alert_events import SOURCE_OWNER as ALERT_OWNER


NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def record(owner, source_id, payload, *, expires_at=None):
    return NS(
        reference=NS(owner=owner, source_id=source_id,
                     version=NS(lineage_id=source_id + "-lineage", revision=1),
                     issued_at=NOW - timedelta(minutes=5), expires_at=expires_at),
        payload=payload,
        content_hash=digest(payload),
    )


def alert_payload(alert, *, city="目标城市"):
    return json.dumps({"event": "role_location_official_weather_alert", "provider": "qweather",
                       "role_location": "角色所在地", "alert_city": city, "alert_area": "目标镇街",
                       "alert": alert, "attributions": [],
                       "source_url": "https://www.qweather.com/"}, ensure_ascii=False)


class ProactiveMaterialPoolTests(unittest.TestCase):
    def project_alert(self, alert, *, city="目标城市"):
        sources = Mock()
        sources.list_readable_current.return_value = (
            record(ALERT_OWNER, "alert-1", alert_payload(alert, city=city)),)
        schedule = NS(contacts=NS(sources=sources), runtime=NS(read_one=Mock(return_value=None)),
                      tasks=NS(identity=object()))
        context = NS(key="private-a", scope=NS(kind=ScopeKind.PRIVATE, key="private-a"),
                     audience=NS(recipients=("recipient-a",)), actor=object())

        result = read_material_pool(schedule, context, observed_at=NOW, settings={})

        self.assertEqual(result["count"], 1)
        item = result["items"][0]
        self.assertEqual(item["source"], "官方气象预警")
        return item

    def test_official_alert_material_reads_stored_headline_and_chinese_severity(self):
        item = self.project_alert({"identifier": "alert-1", "headline": "大风蓝色预警",
            "event_name": "大风", "sender_name": "当地气象台", "severity": "moderate",
            "message_type": "alert", "supersedes": [], "description": "目标镇街发布大风预警。",
            "instruction": "注意防范"})

        self.assertEqual(item["title"], "大风蓝色预警")
        self.assertEqual(item["detail"], "目标镇街发布大风预警。")
        self.assertEqual(item["note"], "目标城市 · 较重")
        self.assertNotIn("moderate", item["note"])

    def test_official_alert_material_maps_each_severity_and_falls_back_to_event_name(self):
        for severity, label in (("minor", "一般"), ("moderate", "较重"), ("severe", "严重"),
                                ("extreme", "特别严重"), ("unknown", "未知")):
            with self.subTest(severity=severity):
                item = self.project_alert({"headline": "", "event_name": "大风",
                                           "severity": severity, "description": ""})
                self.assertEqual(item["title"], "大风")
                self.assertEqual(item["note"], "目标城市 · " + label)

    def test_official_alert_material_keeps_legacy_title_and_level_keys(self):
        item = self.project_alert({"title": "旧版预警标题", "level": "severe",
                                   "text": "旧版预警正文"})

        self.assertEqual(item["title"], "旧版预警标题")
        self.assertEqual(item["detail"], "旧版预警正文")
        self.assertEqual(item["note"], "目标城市 · 严重")

    def test_projects_current_sources_and_hides_balance_amount_by_default(self):
        news_payload = json.dumps({"title": "天气提醒", "summary": "下午可能有雨", "source": "天气台",
                                   "category": "趣事",
                                   "url": "https://news.example/story"})
        balance_payload = json.dumps({"level": "critical", "amount": 12.5, "currency": "CNY"})
        sources = Mock()
        sources.list_readable_current.return_value = (
            record(NEWS_OWNER, "news-1", news_payload, expires_at=NOW + timedelta(minutes=10)),
            record(BALANCE_OWNER, "balance-1", balance_payload),
        )
        schedule = NS(contacts=NS(sources=sources), runtime=NS(read_one=Mock(return_value=None)),
                      tasks=NS(identity=object()))
        context = NS(scope=NS(kind=ScopeKind.PRIVATE, key="private-a"),
                     audience=NS(recipients=("recipient-a",)), actor=object())

        result = read_material_pool(schedule, context, observed_at=NOW, settings={})

        self.assertEqual(result["count"], 2)
        news = next(item for item in result["items"] if item["source"] == "资讯源")
        self.assertEqual(news["title"], "天气提醒")
        self.assertEqual(news["category"], "趣事")
        self.assertEqual(news["detail"], "下午可能有雨")
        self.assertEqual(news["url"], "https://news.example/story")
        self.assertEqual(news["expires_at"], (NOW + timedelta(minutes=10)).isoformat())
        balance = next(item for item in result["items"] if item["source"] == "服务余额")
        self.assertNotIn("12.5", balance["detail"])

    def test_group_scope_returns_empty_without_reading_sources(self):
        schedule = object()
        context = NS(scope=NS(kind=ScopeKind.GROUP))

        result = read_material_pool(schedule, context, observed_at=NOW, settings={})

        self.assertEqual(result, {"observed_at": NOW.isoformat(), "items": [], "count": 0})

    def test_shared_pool_reads_only_the_public_discovery_projection(self):
        events = Mock()
        events.shared_materials.return_value = {"state": "ready", "checked_at": NOW.isoformat(),
            "failure": None, "items": [{"id": "shared-1", "category": "资讯",
                "purpose": "值得和朋友分享的见闻", "title": "有意思的发现", "detail": "公开摘要",
                "note": "示例媒体", "source": "共享资讯池", "captured_at": NOW.isoformat(),
                "expires_at": (NOW + timedelta(hours=1)).isoformat()}]}
        schedule = NS(news_events=events)
        context = NS(scope=NS(kind=ScopeKind.PRIVATE, key="private-a"))

        result = read_shared_material_pool(schedule, context, observed_at=NOW, settings={})

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["title"], "有意思的发现")
        self.assertEqual(result["state"], "ready")
        events.shared_materials.assert_called_once_with(context, {}, NOW)

    def test_shared_pool_never_projects_group_or_private_window_content(self):
        schedule = NS(news_events=Mock())
        context = NS(scope=NS(kind=ScopeKind.GROUP))

        result = read_shared_material_pool(schedule, context, observed_at=NOW, settings={})

        self.assertEqual(result["items"], [])
        schedule.news_events.shared_materials.assert_not_called()

    def test_role_pool_reads_only_shared_discoveries_without_a_window_context(self):
        settings = {"news_source_kind": "host"}
        events = Mock()
        item = {"id": "role-news-1", "category": "资讯", "title": "角色共享见闻",
                "captured_at": NOW.isoformat(), "source": "共享资讯池"}
        events.role_materials.return_value = {"state": "ready", "checked_at": NOW.isoformat(),
            "failure": None, "items": [item]}
        schedule = NS(news_events=events)

        result = read_role_material_pool(schedule, observed_at=NOW, settings=settings)

        self.assertEqual(result["items"], [{**item, "scope": "role"}])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["relationship_count"], 0)
        self.assertEqual(result["history_count"], 0)
        events.role_materials.assert_called_once_with(settings, NOW)

    def test_active_pool_unifies_relationship_materials_and_shared_discoveries(self):
        url = "https://news.example/story"
        sources = Mock()
        sources.list_readable_current.return_value = (record(NEWS_OWNER, "news-1", json.dumps({
            "title": "有出处的分享", "summary": "当前关系已有这条资讯", "source": "示例媒体", "url": url,
        }), expires_at=NOW + timedelta(minutes=10)),)
        events = Mock()
        events.shared_materials.return_value = {"state": "ready", "checked_at": NOW.isoformat(),
            "failure": None, "items": [
                {"id": "shared-1", "category": "资讯", "purpose": "分享见闻",
                 "title": "同一条资讯", "detail": "公开摘要", "note": "示例媒体",
                 "source": "共享资讯池", "captured_at": NOW.isoformat(), "expires_at": None, "url": url},
                {"id": "fun-1", "category": "趣事", "purpose": "分享见闻",
                 "title": "有意思的发现", "detail": "公开摘要", "note": "趣闻来源",
                 "source": "共享资讯池", "captured_at": (NOW - timedelta(minutes=1)).isoformat(),
                 "expires_at": None},
            ]}
        schedule = NS(contacts=NS(sources=sources), runtime=NS(read_one=Mock(return_value=None)),
                      tasks=NS(identity=object()), news_events=events)
        context = NS(scope=NS(kind=ScopeKind.PRIVATE, key="private-a"),
                     audience=NS(recipients=("recipient-a",)), actor=object())

        result = read_active_material_pool(schedule, context, observed_at=NOW, settings={})

        self.assertEqual(result["count"], 2)
        self.assertEqual({item["category"] for item in result["items"]}, {"资讯", "趣事"})
        self.assertEqual(sum(item.get("url") == url for item in result["items"]), 1)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["relationship_count"], 1)
        self.assertEqual(result["shared_count"], 2)

    def test_active_messages_are_projected_with_confirmed_per_part_usage(self):
        database = sqlite3.connect(":memory:")
        database.row_factory = sqlite3.Row
        database.executescript("""
            CREATE TABLE proactive_contacts(contact_id TEXT, origin_key TEXT, record_json TEXT,
                body TEXT, payload_hash TEXT, created_at TEXT);
            CREATE TABLE background_deliveries(owner TEXT, origin_key TEXT, request_json TEXT,
                payload_hash TEXT, state TEXT, receipt_ref TEXT, delivered_at TEXT);
        """)
        parts = [{"text": "第一段", "payload_hash": "part-hash-1"},
                 {"text": "第二段", "payload_hash": "part-hash-2"}]
        database.execute("INSERT INTO proactive_contacts VALUES (?, ?, ?, ?, ?, ?)", (
            "contact-1", "private-a", json.dumps({"decision": "send", "thought_summary": "分享一本书",
                "purpose": "chat", "automatic_origin": {"relationship_event": {}}, "parts": parts}),
            "第一段 第二段", "whole-hash-1", NOW.isoformat()))
        database.execute("INSERT INTO proactive_contacts VALUES (?, ?, ?, ?, ?, ?)", (
            "contact-2", "private-a", json.dumps({"decision": "send", "thought_summary": "分享一件趣事",
                "purpose": "news", "parts": [{"text": "还没确认", "payload_hash": "part-hash-3"}]}),
            "还没确认", "whole-hash-2", NOW.isoformat()))
        database.execute("INSERT INTO proactive_contacts VALUES (?, ?, ?, ?, ?, ?)", (
            "contact-3", "private-a", json.dumps({"decision": "send", "thought_summary": "分享一段见闻",
                "purpose": "news", "automatic_origin": {"news_event": {}},
                "parts": [{"text": "已经送达", "payload_hash": "part-hash-4"}]}),
            "已经送达", "whole-hash-3", (NOW - timedelta(minutes=10)).isoformat()))
        database.execute("INSERT INTO proactive_contacts VALUES (?, ?, ?, ?, ?, ?)", (
            "contact-4", "private-a", json.dumps({"decision": "send", "thought_summary": "时间尚未到的回执",
                "purpose": "news", "parts": [{"text": "时间尚未到", "payload_hash": "part-hash-5"}]}),
            "时间尚未到", "whole-hash-4", NOW.isoformat()))
        database.execute("INSERT INTO proactive_contacts VALUES (?, ?, ?, ?, ?, ?)", (
            "contact-5", "private-a", json.dumps({"decision": "skip", "thought_summary": "没有发出的念头",
                "purpose": "chat", "parts": [{"text": "没有真的发出", "payload_hash": "part-hash-6"}]}),
            "没有真的发出", "whole-hash-5", NOW.isoformat()))
        database.execute("INSERT INTO proactive_contacts VALUES (?, ?, ?, ?, ?, ?)", (
            "contact-6", "private-a", json.dumps({"decision": "send", "thought_summary": "早上想和你打个招呼",
                "purpose": "greeting", "automatic_origin": {"greeting_event": {}},
                "parts": [{"text": "早呀，今天也慢慢来。", "payload_hash": "part-hash-7"}]}),
            "早呀，今天也慢慢来。", "whole-hash-6", NOW.isoformat()))
        for contact_id, sequence, part_count, payload_hash, state, receipt_ref, delivered_at in (
            ("contact-1", 0, 2, "part-hash-1", "delivered", "receipt-1", NOW.isoformat()),
            ("contact-1", 1, 2, "part-hash-2", "pending", None, None),
            ("contact-2", 0, 1, "part-hash-3", "unknown", None, None),
            ("contact-3", 0, 1, "part-hash-4", "delivered", "receipt-4",
             (NOW - timedelta(minutes=8)).isoformat()),
            ("contact-4", 0, 1, "part-hash-5", "delivered", "receipt-5",
             (NOW + timedelta(minutes=1)).isoformat()),
        ):
            request = {"owner": "proactive.contact", "logical_key": contact_id,
                       "source_ref": contact_id, "sequence": sequence,
                       "part_count": part_count, "payload_hash": payload_hash}
            database.execute("INSERT INTO background_deliveries VALUES (?, ?, ?, ?, ?, ?, ?)", (
                "proactive.contact", "private-a", json.dumps(request), payload_hash,
                state, receipt_ref, delivered_at))

        class Runtime:
            @contextmanager
            def read_only_snapshot(self):
                yield database

            def read_one(self, *_args):
                return None

        sources = Mock()
        sources.list_readable_current.return_value = ()
        history = NS(interaction=Mock(return_value={
            "last_input_at": (NOW - timedelta(minutes=7)).isoformat(),
        }))
        schedule = NS(contacts=NS(sources=sources, history=history), runtime=Runtime(), tasks=NS(identity=object()))
        context = NS(key="private-a", scope=NS(kind=ScopeKind.PRIVATE, key="private-a"),
                     audience=NS(recipients=("recipient-a",)), actor=object())

        result = read_material_pool(schedule, context, observed_at=NOW, settings={})

        messages = {item["title"]: item for item in result["items"]}
        partial = messages["分享一本书"]
        self.assertEqual(partial["usage"]["status"], "partial")
        self.assertEqual(partial["usage"]["delivered_parts"], 1)
        self.assertEqual(partial["usage"]["total_parts"], 2)
        self.assertEqual(partial["usage"]["records"][0]["text"], "第一段")
        self.assertEqual(partial["usage"]["thought_origin"], "关系话题")
        self.assertNotIn("followup_observation", partial["usage"],
                         "a partial delivery is not treated as a complete contact")
        self.assertEqual(messages["分享一件趣事"]["usage"]["status"], "unconfirmed")
        self.assertEqual(messages["分享一件趣事"]["usage"]["delivered_parts"], 0)
        observation = messages["分享一段见闻"]["usage"]["followup_observation"]
        self.assertEqual(messages["分享一段见闻"]["usage"]["thought_origin"], "资讯分享")
        self.assertEqual(observation["status"], "input_after_delivery")
        self.assertEqual(observation["input_at"], (NOW - timedelta(minutes=7)).isoformat())
        self.assertEqual(messages["时间尚未到的回执"]["usage"]["followup_observation"]["status"], "unknown")
        self.assertEqual(messages["没有发出的念头"]["usage"]["status"], "not_sent")
        self.assertEqual(messages["没有发出的念头"]["category"], "未发送的念头")
        self.assertEqual(messages["没有发出的念头"]["source"], "主动念头记录")
        history.interaction.assert_called_once_with(context, NOW, action="proactive.contact")
        self.assertEqual(result["history_count"], 6)

        traces = read_proactive_contact_traces(schedule, ((context, "user-17", "澄溪"),), observed_at=NOW)
        greeting = next(item for item in traces["items"] if item["title"] == "早上想和你打个招呼")
        self.assertEqual(greeting["scope"], "window")
        self.assertEqual(greeting["trace_target"], "user-17")
        self.assertEqual(greeting["trace_role"], "澄溪")
        self.assertEqual(greeting["captured_at"], NOW.isoformat(), "draft time is recorded before delivery")
        self.assertEqual(greeting["usage"]["status"], "pending")
        self.assertEqual(greeting["usage"]["thought_origin"], "问候时机")

        other_context = NS(key="private-b", scope=NS(kind=ScopeKind.PRIVATE, key="private-b"),
                           audience=NS(recipients=("recipient-a",)), actor=object())
        other = read_material_pool(schedule, other_context, observed_at=NOW, settings={})
        self.assertFalse(any(item["category"] == "主动消息" for item in other["items"]))
        database.close()

    def test_daily_traces_include_local_day_boundaries_and_model_skips(self):
        database = sqlite3.connect(":memory:")
        database.row_factory = sqlite3.Row
        database.executescript("""
            CREATE TABLE proactive_contacts(contact_id TEXT, origin_key TEXT, record_json TEXT,
                body TEXT, payload_hash TEXT, created_at TEXT);
            CREATE TABLE background_deliveries(owner TEXT, origin_key TEXT, request_json TEXT,
                payload_hash TEXT, state TEXT, receipt_ref TEXT, delivered_at TEXT);
        """)
        documents = (
            ("before", {"decision": "send", "purpose": "chat", "thought_summary": "前一天"},
             "昨天的消息", "2026-10-06T15:59:59.999999+00:00"),
            ("at-start", {"decision": "send", "purpose": "chat", "thought_summary": "当天起点"},
             "当天起点消息", "2026-10-06T16:00:00+00:00"),
            ("skip", {"decision": "skip", "purpose": "chat", "thought_summary": "早上问候",
                      "skip_reason": "model_decision", "model_skip_reason": "no_natural_opening"},
             "", "2026-10-07T07:00:00+00:00"),
            ("at-end", {"decision": "send", "purpose": "chat", "thought_summary": "下一天"},
             "下一天消息", "2026-10-07T16:00:00+00:00"),
        )
        for contact_id, document, body, created_at in documents:
            database.execute("INSERT INTO proactive_contacts VALUES (?, ?, ?, ?, ?, ?)", (
                contact_id, "private-c", json.dumps(document), body, contact_id + "-hash", created_at))

        class Runtime:
            @contextmanager
            def read_only_snapshot(self):
                yield database

        context = NS(key="private-c", scope=NS(kind=ScopeKind.PRIVATE, key="private-c"),
                     audience=NS(recipients=("recipient-c",)), actor=object())
        schedule = NS(contacts=NS(history=NS(interaction=Mock(return_value=None))), runtime=Runtime())
        start = datetime(2026, 10, 6, 16, tzinfo=timezone.utc)
        end = datetime(2026, 10, 7, 16, tzinfo=timezone.utc)

        result = read_proactive_contact_traces(schedule, ((context, "user-c", "澄溪"),),
            observed_at=NOW, created_at_start=start, created_at_end=end)

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["total_count"], 2)
        self.assertEqual({item["title"] for item in result["items"]}, {"当天起点", "早上问候"})
        skipped = next(item for item in result["items"] if item["title"] == "早上问候")
        self.assertEqual(skipped["usage"]["status"], "not_sent")
        self.assertEqual(skipped["usage"]["total_parts"], 0)
        self.assertEqual(skipped["detail"], "")
        self.assertEqual(skipped["skip_reason"], "model_decision")
        self.assertEqual(skipped["model_skip_reason"], "no_natural_opening")
        self.assertEqual(len({item["trace_window_id"] for item in result["items"]}), 1)
        self.assertNotIn("private-c", result["items"][0]["trace_window_id"])
        database.close()
