"""完全模拟发送的主动消息端到端用例：真链路，只替换两个外部端口。

真跑的部分（全程不打桩）：

* ``NewsContactEvents.select`` → ``ContactSchedule._plan`` 的契机分支
  （``companion_v2/proactive/scheduling.py:256``）→ ``NewsContactEvents.prepare`` 的真实取源、
  真实拒收条件与真实 ``SourceRecord`` 发布（``news_events.py:308``）→
  ``ProactiveContacts.contact`` 的压制、预算、草稿封存与分片（``service.py:380``、``:537``）
  → ``OutboundDelivery.queue/deliver`` 的排程、三态、回执、幂等与重启恢复
  （``delivery/service.py:159``、``:222``）。
* 新闻出处由 ``news_events.news_provenance`` 从来源提取，单独封存并绑定本地提交哈希。
  主动正文不带来源页脚；追问时从已确认投递的同一条记录取出处。

被替换的外部端口只有两个：

1. **新闻来源**：``RecordedNewsPort`` 回放 ``fixtures/proactive/news_feed_sample.json``
   指向的**真实录制样本**（一次真实 ``GET https://daily.juya.uk/rss.xml`` 的原始响应体，
   见 ``fixtures/proactive/README.md``），并交给**真解析器**
   ``astrbot_host.news_parsing.parse_rss_news``。``SyntheticNewsPort`` 用的则是
   **手写近似样本，非录制**，只用于录制样本覆盖不到的断言（固定文案的渲染态与拒收条件）；
   它与录制样本走同一个来源端口契约，但字节不来自任何真实站点。每个用例的 docstring
   标明自己用的是哪一种。

2. **平台发送**：``RecordingSender`` 实现真 ``LocalSender`` 接口，记录"如果平台可用，
   实际会发出去的那份 envelope"，并按用例编程返回 ``delivered`` / ``not_sent`` /
   ``unknown`` / 超时。它是这条链上唯一的外部副作用点（``delivery/service.py:225``）。

时钟与样本时效
--------------
``NewsContactEvents.prepare`` 只接受 ``0 <= at - published_at <= 24h`` 的条目
（``news_events.py:354``）。录制样本的绝对时间与本套用例的确定时钟基线相差
``fixtures/proactive/news_feed_sample.json`` 里 ``clock_alignment.shift_seconds`` 秒，
所以消费录制样本的用例先把时钟 ``set`` 到清单里的 ``article.published_at`` 再往前推进，
而不是硬编码某个日期；原始响应体一个字节都没有改写。样本整体过期后重新录制即可。
"""

import asyncio
import base64
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import unittest

from astrbot_host.news import DEFAULT_AI_NEWS_FEED_LABEL
from astrbot_host.news_parsing import parse_rss_news
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.conversation.media.models import ImageDescriptor, PromptImage
from companion_v2.conversation.reply_settings import GROUP as PRESENTATION_GROUP
from companion_v2.conversation.text_processing import TEXT_DEFAULTS
from companion_v2.delivery.components import build_component_plan
from companion_v2.delivery.media import ImageDeliveryEnvelope, image_manifest, payload_hash
from companion_v2.delivery.models import DeliveryReceipt, LocalSender
from companion_v2.delivery.plan import DeliveryContent
from companion_v2.delivery.service import DeliveryRequest
from companion_v2.execution.models import ProviderBinding, digest, stamp
from companion_v2.foundation.refs import SourceRef, VersionRef
from companion_v2.proactive.external_materials import ParseBatch
from companion_v2.proactive.configuration import GROUP
from companion_v2.proactive.news_events import SOURCE_OWNER as NEWS_SOURCE_OWNER
from companion_v2.proactive.news_events import news_provenance
from companion_v2.proactive.news_sources import NewsFetchError
from tests.contact_schedule_support import ContactScheduleFixture
from tests.test_management_access import INSTALLATION, ROLE, SCOPE
from tests.test_role_tool_loop import answered
from tests.test_semantic_integration import SemanticPort

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "proactive"
SAMPLE = json.loads((FIXTURES / "news_feed_sample.json").read_text(encoding="utf-8"))
RECORDED_PATH = FIXTURES / SAMPLE["sample"]
RECORDED_BYTES = RECORDED_PATH.read_bytes() if RECORDED_PATH.is_file() else None
RECORDED_ARTICLE = SAMPLE["article"]
# 清单保存真实录制的绝对时间；用例回放时把整条时间轴平移，但保持条目之间的相对间隔。
RECORDED_LEAD = datetime.fromisoformat(SAMPLE["recorded_at"]) - datetime.fromisoformat(
    RECORDED_ARTICLE["published_at"])
# 回放锚点（＝平移后的"录制时刻"）相对宿主建站时刻往后挪一个 `RECORDED_LEAD`，
# 于是样本里最新一条正好落在建站的那一刻：既不晚于当前时钟，又在 24h 取源窗口
# 的零龄端；其余条目都比它早 23 小时以上，正好检验窗口。
RECORDED_ALIGNMENT_LEAD = RECORDED_LEAD
# 同一份录制样本跨用例复用；fingerprint 固定，_settings 的 settings_hash 才稳定
# （settings_hash 参与契机 id、来源 id 与 marker 校验，见 news_events.py:65）。
RECORDED_FINGERPRINT = "recorded-juya-rss-20261004"
SYNTHETIC_FINGERPRINT = "synthetic-news-v1"
# 近似样本的发布时刻相对建站时刻的提前量。
SYNTHETIC_LEAD = timedelta(minutes=10)

SPLIT_MARKER = "<<PRIVATE_COMPANION_SPLIT>>"
PICTURE_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXmEAAAAASUVORK5CYII=")
PICTURE = ImageDescriptor(0, "image/png", 1, 1, len(PICTURE_BYTES),
                          hashlib.sha256(PICTURE_BYTES).hexdigest())


class Article:
    """``ParseBatch`` 的条目：字段名与 ``external_materials.NewsItem`` 一致。"""

    def __init__(self, source, identifier, title, url, summary, published_at):
        self.source, self.identifier, self.title = source, identifier, title
        self.url, self.summary, self.published_at = url, summary, published_at


def _Replayed(item, published_at):
    """真解析结果，仅发布时间按回放偏移平移过。"""
    return Article(item.source, item.identifier, item.title, item.url, item.summary, published_at)


class RecordedNewsPort:
    """新闻来源端口的**录制回放**：真解析器解析真实响应体，只换掉网络这一跳。

    ``alignment`` 是回放的时间轴锚点：录制清单里的"录制时刻"落到这一刻，
    于是清单里最新一条的发布时间落在 ``alignment - RECORDED_LEAD``。
    """

    kind = "rss"
    source_label = DEFAULT_AI_NEWS_FEED_LABEL
    fingerprint = RECORDED_FINGERPRINT

    def __init__(self, *, alignment, failures=()):
        self.alignment = alignment
        self.calls = 0
        self.failures = list(failures)

    @property
    def published_at(self):
        """回放后最新一条的发布时间，也就是本用例的样本基准。"""
        return self.alignment - RECORDED_LEAD

    async def fetch(self):
        if RECORDED_BYTES is None:
            raise unittest.SkipTest("recorded public RSS body is intentionally excluded from release packages")
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        batch = parse_rss_news(RECORDED_BYTES, source_label=self.source_label)
        shift = self.alignment - datetime.fromisoformat(SAMPLE["recorded_at"])
        # 只平移发布时间：字节、标题、摘要、链接都是真实录制内容。
        return ParseBatch(tuple(_Replayed(item, item.published_at + shift if item.published_at else None)
                                for item in batch.items), batch.issues)


class SyntheticNewsPort:
    """**手写近似样本，非录制**：固定文案，用于渲染态与拒收条件断言。"""

    kind = "json"
    fingerprint = SYNTHETIC_FINGERPRINT

    def __init__(self, clock, *, label="示例媒体"):
        self.clock, self.label, self.calls, self.batches = clock, label, 0, []

    @property
    def source_label(self):
        return self.label

    async def fetch(self):
        self.calls += 1
        rows = self.batches[min(self.calls - 1, len(self.batches) - 1)]
        if isinstance(rows, Exception):
            raise rows
        batch = rows() if callable(rows) else rows
        return ParseBatch(tuple(batch), ())

    def article(self, identifier="story-a", *, age=timedelta(minutes=10), title="有日期的资讯",
                url=None, summary="公开摘要，不是完整报道"):
        return Article(self.label, identifier, title, url or ("https://news.example/" + identifier),
                       summary, self.clock.now() - age)


class RecordingSender:
    """真 sender 接口：记录 envelope，可按用例编程返回三态或超时。

    记录项就是"如果平台可用，实际会发出去的那份 envelope"：目标对象、正文、分片与顺序、
    图片/TTS 渲染态、回执引用。渲染态使用生产同款 ``build_component_plan``
    （``companion_v2/adapters/astrbot/onebot.py:144``），断言的是真渲染结果，
    不是测试自己拼的期望值。
    """

    def __init__(self, binding, *, now=None, supports_images=True):
        self.binding = binding
        self.now = now
        self.supports_images = supports_images
        self.envelopes = []
        self.attempts = []

    def local_sender(self):
        return LocalSender(self.binding, self.send, supports_images=self.supports_images)

    def receipt(self, envelope, status="delivered"):
        return DeliveryReceipt(envelope.part_id, envelope.operation_id, envelope.context.key,
            envelope.payload_hash, self.binding, status, status + ":" + envelope.part_id,
            "platform:" + envelope.part_id if status in ("delivered", "accepted") else None,
            self.now() if status == "delivered" else None)

    @staticmethod
    def target(envelope):
        scope = envelope.context.scope
        return {"platform": scope.platform, "bot_id": scope.bot_id, "account_id": scope.account_id,
                "kind": scope.kind.value, "native_scope_id": scope.native_scope_id,
                "conversation_id": scope.conversation_id,
                "recipients": tuple(subject.subject_id for subject in envelope.context.audience.recipients)}

    def render(self, envelope):
        images = envelope.images if isinstance(envelope, ImageDeliveryEnvelope) else ()
        position = envelope.image_position if isinstance(envelope, ImageDeliveryEnvelope) else "after_text"
        # 语音分量的形状与真 sender 交给组件计划的一致：一个已渲染的 OneBot 记录。
        segment = ({"type": "record", "data": {"file": "rendered-voice:" + envelope.part_id}}
                   if envelope.voice is not None else None)
        plan = build_component_plan(
            envelope.text, images, segment,
            position=(envelope.voice.position if envelope.voice is not None else "after_media"),
            image_position=position,
            voice_only=(envelope.voice is not None and envelope.voice.delivery_mode == "voice_only"),
            speech=(envelope.voice.speech if envelope.voice is not None else None),
            order=(envelope.plan.order if envelope.plan is not None else ()),
            mentions=(envelope.plan.mentions if envelope.plan is not None else ()),
            faces=(envelope.plan.faces if envelope.plan is not None else ()),
        )
        return {"kinds": plan.kinds, "text_hidden": plan.text_hidden,
                "components": tuple((component.kind, component.value if component.kind == "text" else None)
                                    for component in plan.components)}

    def record(self, envelope):
        return {
            "target": self.target(envelope),
            "text": envelope.text,
            "payload_hash": envelope.payload_hash,
            "sequence": envelope.sequence,
            "part_id": envelope.part_id,
            "operation_id": envelope.operation_id,
            "origin_key": envelope.context.key,
            "sender": (envelope.sender.capability, envelope.sender.provider, envelope.sender.generation),
            "plan": envelope.plan.document() if envelope.plan is not None else None,
            "voice": envelope.voice.document() if envelope.voice is not None else None,
            "poke": envelope.poke.document() if envelope.poke is not None else None,
            "images": tuple(image_manifest(image) for image in
                            (envelope.images if isinstance(envelope, ImageDeliveryEnvelope) else ())),
            "image_position": (envelope.image_position if isinstance(envelope, ImageDeliveryEnvelope)
                               else "after_text"),
            "render": self.render(envelope),
            "envelope": envelope,
        }

    async def send(self, envelope):
        """生产 sender 的调用顺序：先过派发栅栏，再做平台调用，最后给回执。"""
        record = self.record(envelope)
        if envelope.before_dispatch is None:
            record.update(status="not_sent", refusal="no_dispatch_fence")
            self.attempts.append(record)
            return self.receipt(envelope, "not_sent")
        try:
            envelope.before_dispatch()
        except Exception as error:  # 撤销、暂停、任务被替换都发生在这里
            record.update(status="not_sent", refusal=getattr(error, "code", type(error).__name__))
            self.attempts.append(record)
            return self.receipt(envelope, "not_sent")
        self.envelopes.append(record)
        behavior = getattr(self, "behavior", None)
        try:
            status = "delivered" if behavior is None else behavior(envelope)
        except (TimeoutError, asyncio.TimeoutError):
            status = "timeout"
        except Exception as error:
            status = "raised:" + type(error).__name__
        if status == "timeout":
            # 平台超时既可能返回 unknown，也可能直接抛出；这里选抛出，让
            # ``OutboundDelivery.deliver`` 的 except 分支把它记成 unknown。
            record["status"] = "timeout"
            self.attempts.append(record)
            raise TimeoutError("platform did not answer before the local deadline")
        if status in ("delivered", "accepted", "unknown", "not_sent"):
            record["status"] = status
            self.attempts.append(record)
            return self.receipt(envelope, status)
        raise ValueError("recording sender received an unsupported programmed outcome")

    def news_envelopes(self, app):
        """这条链实际发出的 envelope：按后台投递行自己的 owner 认领。

        新闻主动正文不附来源和原文页脚，所以依据封存投递 owner 识别。
        普通回复走的是另一条投递路径
        （``delivery_id`` 不以 ``outbound-`` 开头），所以按 owner 就能分开。
        """
        claimed = {row["delivery_id"] for row in app.runtime.read_all(
            "SELECT delivery_id FROM background_deliveries WHERE owner = ?", (GROUP,))}
        return tuple(record for record in self.envelopes if record["part_id"] in claimed)

    def delivered(self):
        return tuple(record for record in self.attempts if record["status"] == "delivered")


class ProactiveEndToEndFixture(ContactScheduleFixture):
    """固定时钟、临时数据根、合成宿主；两个外部端口在此注入。"""

    def setUp(self):
        super().setUp()
        self.binding = ProviderBinding("message.delivery", "recording", "1", "recording-1")
        self.recorder = RecordingSender(self.binding, now=self.clock.now)
        self.sender = self.recorder.local_sender()

    # -- 夹具辅助 ---------------------------------------------------------

    def sync(self, app, seconds):
        """推进确定时钟并让驱动重新评估。"""
        self.clock.advance(timedelta(seconds=seconds))
        app.notify()

    async def news_host(self, build, *, lead=timedelta(0)):
        """起一个只留新闻契机通道的宿主，让"没有 envelope"只可能来自被测分支。

        宿主按确定时钟 ``NOW`` 建成（最近一轮对话的 ``created_at`` 就取这一刻），
        取源端口再对齐到 ``NOW + lead``，于是样本基准总在对话之后、又在 24h 取源
        窗口之内。确定时钟不能回拨，这里也就不需要回拨。

        ``build`` 是 ``alignment -> 端口`` 的工厂，返回值里带上真正的端口对象，
        用例可以继续读它的 ``calls``。
        """
        alignment = self.clock.now() + lead
        port = build(alignment)
        host, batch = await self.scheduled(news_port=port)
        app = host.application
        # 显式指定来源种类：默认 ``host`` 会让生产路由去解析宿主默认源，而这里要
        # 断言的正是"契约相同的替身端口"。地址只用于通过 news_url 的形状校验。
        self.settings(app, {"news_share_enabled": True, "chat.enabled": False,
                            "news_source_kind": "json",
                            "news_source_url": "https://news.example/feed",
                            "news_check_interval_minutes": 15}).unwrap()
        return host, batch, app, port

    def recorded_port(self, *, failures=()):
        """录制回放端口：调用前先算好对齐时刻与样本基准。"""
        if RECORDED_BYTES is None:
            self.skipTest("recorded public RSS body is intentionally excluded from release packages")
        alignment = self.clock.now() + RECORDED_ALIGNMENT_LEAD
        port = RecordedNewsPort(alignment=alignment, failures=list(failures))
        return (lambda _alignment: port), port.published_at, port

    def synthetic_port(self):
        """近似端口：最新一条相对建站时刻提前 ``SYNTHETIC_LEAD``。"""
        alignment = self.clock.now() + SYNTHETIC_LEAD
        port = SyntheticNewsPort(self.clock)
        port.batches = [lambda: [port.article()]]
        return (lambda _alignment: port), alignment, port

    def presentation_settings(self, app, changes):
        context = self.contact_context()
        target = ConfigTarget(INSTALLATION, ConfigLayer.WINDOW, ROLE, context.scope)
        snapshot = app.configuration.read(self.important, target=target, group_id=PRESENTATION_GROUP,
                                          observed_at=self.clock.now()).unwrap()
        return app.configuration.patch(self.important, target=target, group_id=PRESENTATION_GROUP,
            expected_edit_token=snapshot.edit_token,
            changes={PRESENTATION_GROUP + "." + key: value for key, value in changes.items()},
            operation=self.operation(context.scope, owner=PRESENTATION_GROUP),
            observed_at=self.clock.now()).unwrap()

    # -- 读取辅助 ---------------------------------------------------------

    def queued_contacts(self, app):
        return app.runtime.read_all("SELECT * FROM proactive_contacts ORDER BY created_at, contact_id", ())

    def delivery_rows(self, app):
        return app.runtime.read_all("""SELECT * FROM background_deliveries
            ORDER BY CAST(json_extract(request_json, '$.sequence') AS INTEGER), delivery_id""", ())

    def attempts(self, app, task_id):
        return tuple(dict(row) for row in app.runtime.read_all(
            "SELECT attempt_no, generation, slot_held FROM execution_attempts WHERE task_id = ? "
            "ORDER BY attempt_no", (task_id,)))

    def batch_created(self, app):
        row = app.runtime.read_one(
            "SELECT created_at FROM conversation_batches ORDER BY created_at LIMIT 1", ())
        return datetime.fromisoformat(row["created_at"])

    def news_source(self, app, saved):
        """从封存的 automatic_origin 里取回新闻来源，按正常授权读一遍。"""
        document = saved["sources"][-1]
        reference = SourceRef(document["source_id"], NEWS_SOURCE_OWNER, SCOPE,
            VersionRef(document["version"]["lineage_id"], document["version"]["revision"]),
            datetime.fromisoformat(document["issued_at"]),
            datetime.fromisoformat(document["expires_at"]) if document["expires_at"] else None)
        return json.loads(app.proactive.sources.read(reference, resolved=self.important, capability="text",
                                                     observed_at=self.clock.now(),
                                                     authorizer=app.tasks.identity).unwrap().payload)

    # -- 断言辅助 ---------------------------------------------------------

    async def delivered_parts(self, app, count, *, seconds=60):
        """等到这条链的每一片都拿到可信回执，且尝试的槽位已经释放。

        ``CompanionApplication._drive`` 把 ``advance_deliveries`` 限流到每秒一次
        （``application.py:736``），所以下一片不是立刻入队；这里按状态轮询，
        不按机器速度计时。返回每一片的回执引用，供用例继续断言。
        """
        await self.until(lambda: (len(self.delivery_rows(app)) == count
                                  and all(row["state"] == "delivered" for row in self.delivery_rows(app))
                                  and all(attempt["slot_held"] == 0
                                          for row in self.delivery_rows(app)
                                          for attempt in self.attempts(app, row["task_id"]))
                                  and not app.contact_schedule.active_handles), seconds=seconds)
        return tuple(row["receipt_ref"] for row in self.delivery_rows(app))

    def assert_no_contact_delivery(self, app):
        """这条链没有产生任何投递：没有新闻 envelope，也没有后台投递行。"""
        self.assertEqual(self.recorder.news_envelopes(app), ())
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) FROM background_deliveries", ())[0], 0)

    def assert_sent_news(self, record, *, source, published, url):
        self.assertEqual(record["target"]["kind"], "private")
        self.assertEqual(record["target"]["recipients"], (self.important.subject.subject_id,))
        self.assertEqual(record["target"]["native_scope_id"], SCOPE.native_scope_id)
        self.assertEqual(record["sender"], (self.binding.capability, self.binding.provider,
                                            self.binding.generation))
        self.assertNotIn(url, record["text"])
        self.assertNotIn("来源：", record["text"])
        self.assertNotIn("原文：", record["text"])
        self.assertEqual(record["payload_hash"], digest(record["text"]))


class ProactiveDeliveryEndToEndTests(ProactiveEndToEndFixture, unittest.IsolatedAsyncioTestCase):
    """用例 a：新闻契机走完整条链路并送达。"""

    async def test_recorded_news_opportunity_reaches_the_platform_envelope(self):
        """真录制样本：select → _plan → prepare → contact → 排程 → envelope → delivered。"""
        build, base, port = self.recorded_port()
        _host, _batch, app, _ = await self.news_host(build, lead=RECORDED_ALIGNMENT_LEAD)
        self.sync(app, 60)
        app.contact_schedule.advance()
        await self.automatic_delivery(app)
        await self.until(lambda: not app.contact_schedule.active_handles)

        # 1) 真取源与真选择：录制样本被真解析器读成 10 条，24h 窗口只放行最新一条。
        self.assertEqual(port.calls, 1)
        self.assertEqual(len(SAMPLE["items"]), 10)
        self.assertEqual(base, self.batch_created(app))
        self.assertEqual(port.published_at, base)
        # 回放样本的窗口关系：最新一条正好零龄；下一条已经在 23 小时以外，
        # 而 24h 取源窗口是硬上限，所以这条链只会拿到最新那一条。
        shift = base - datetime.fromisoformat(RECORDED_ARTICLE["published_at"])
        replayed = [datetime.fromisoformat(item["published_at"]) + shift
                    for item in SAMPLE["items"] if item["published_at"]]
        self.assertEqual(replayed[0], base)
        self.assertGreaterEqual(base - replayed[1], timedelta(hours=23))
        self.assertGreaterEqual(base - replayed[-1], timedelta(hours=24))
        contacts = self.queued_contacts(app)
        self.assertEqual(len(contacts), 1)
        saved = json.loads(contacts[0]["record_json"])
        self.assertEqual(saved["purpose"], "news")
        self.assertEqual(saved["decision"], "send")
        self.assertIn("news_event", saved["automatic_origin"])
        self.assertNotIn("news_pending", saved["automatic_origin"])

        # 2) 契机确实来自录制样本里最新那条，而不是别的通道。
        poll = json.loads(app.runtime.read_one("SELECT event_json FROM proactive_news_polls", ())["event_json"])
        self.assertEqual(saved["automatic_origin"]["news_event"]["event_id"], poll["id"])
        self.assertEqual(saved["automatic_origin"]["news_event"]["settings_hash"], poll["settings_hash"])
        self.assertEqual(poll["article"]["url"], RECORDED_ARTICLE["url"].rstrip("/"))
        self.assertEqual(poll["article"]["published_at"],
                         stamp(datetime.fromisoformat(RECORDED_ARTICLE["published_at"]) + shift))

        # 3) envelope 内容：目标、正文（含程序追加的归因）、分片、来源。
        sent = self.recorder.news_envelopes(app)
        self.assertEqual(len(sent), 1)
        record = sent[0]
        self.assert_sent_news(record, source=RECORDED_ARTICLE["source"], published=base,
                              url=RECORDED_ARTICLE["url"].rstrip("/"))
        saved = json.loads(contacts[0]["record_json"])
        self.assertEqual(saved["news_provenance"]["source"], RECORDED_ARTICLE["source"])
        self.assertEqual(saved["news_provenance"]["url"], RECORDED_ARTICLE["url"].rstrip("/"))
        self.assertEqual(datetime.fromisoformat(saved["news_provenance"]["published_at"]), base)
        self.assertEqual(record["text"], contacts[0]["body"])
        self.assertEqual((record["sequence"], record["plan"]), (0, None))

        # 4) delivery 状态与回执：一条后台投递、一次尝试、一条回执。
        rows = self.delivery_rows(app)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["state"], "delivered")
        self.assertEqual(row["owner"], "proactive.contact")
        self.assertEqual(row["payload_hash"], record["payload_hash"])
        self.assertEqual(row["receipt_ref"], "delivered:" + record["part_id"])
        self.assertEqual(row["platform_ref"], "platform:" + record["part_id"])
        self.assertEqual(row["origin_key"], contacts[0]["origin_key"])
        receipts = app.runtime.read_all("SELECT * FROM background_delivery_receipts", ())
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["receipt_ref"], row["receipt_ref"])
        self.assertEqual(json.loads(receipts[0]["receipt_json"])["status"], "delivered")
        self.assertEqual(self.attempts(app, row["task_id"]),
                         ({"attempt_no": 1, "generation": 1, "slot_held": 0},))
        status = app.proactive.observe(self.contact_context(), contacts[0]["task_id"]).unwrap()
        self.assertEqual((status["delivery"], status["receipt_ref"], status["skip_reason"]),
                         ("delivered", row["receipt_ref"], None))
        # 归因仍然只按来源提供的字段生成。
        attributed = news_provenance({
            "event": "external_news_item", "source": RECORDED_ARTICLE["source"],
            "published_at": poll["article"]["published_at"],
            "url": poll["article"]["url"], "title": poll["article"]["title"],
            "summary": poll["article"]["summary"]})
        self.assertEqual(saved["news_provenance"], attributed)
        # 内部回执和归因均不挤进主动正文，归因保留在封存记录里供追问。
        self.assertIsNone(record["plan"])
        self.assertNotIn("_receipt", record["text"])
        self.assertNotIn("proactive.news", record["text"])
        self.assertEqual(record["text"], contacts[0]["body"])

    async def test_suppression_when_the_conversation_has_not_gone_idle(self):
        """用例 b1（刚聊过）：确定时钟还没走完 idle，链路停在契机分支之前。"""
        build, base, port = self.recorded_port()
        _host, _batch, app, _ = await self.news_host(build, lead=RECORDED_ALIGNMENT_LEAD)
        self.settings(app, {"idle_seconds": 900}).unwrap()
        app.contact_schedule.advance()
        await asyncio.sleep(0.05)
        self.assertEqual(port.calls, 0)
        self.assertEqual(self.queued_contacts(app), ())
        self.assert_no_contact_delivery(app)
        # 原因可读：这一轮的 idle 门槛是 900s，而最近一轮对话之后还没过那么久。
        self.assertEqual(self.preferences(app).values["idle_seconds"], 900)
        elapsed = (self.clock.now() - self.batch_created(app)).total_seconds()
        self.assertGreaterEqual(elapsed, 0)
        self.assertLess(elapsed, 900)

    async def test_suppression_during_quiet_hours_names_the_reason(self):
        """用例 b2（免打扰）：idle 已过但 quiet 窗口覆盖当前时刻，偏好直接给出原因。"""
        build, base, port = self.recorded_port()
        _host, _batch, app, _ = await self.news_host(build, lead=RECORDED_ALIGNMENT_LEAD)
        self.settings(app, {"idle_seconds": 60, "quiet_enabled": True, "quiet_timezone": "UTC",
                            "quiet_start": "00:00", "quiet_end": "23:59"}).unwrap()
        self.assertEqual(self.preferences(app).reason("news"), "proactive_quiet_hours")
        self.sync(app, 600)
        app.contact_schedule.advance()
        await asyncio.sleep(0.05)
        self.assertEqual(port.calls, 0)
        self.assertEqual(self.queued_contacts(app), ())
        self.assert_no_contact_delivery(app)

    async def test_suppression_when_the_delivery_budget_is_exhausted(self):
        """用例 b3（超频）：窗口与免打扰都放行，但 24h 联系配额判定拒绝。"""
        build, base, port = self.recorded_port()
        _host, _batch, app, _ = await self.news_host(build, lead=RECORDED_ALIGNMENT_LEAD)
        self.settings(app, {"idle_seconds": 60, "max_contacts_24h": 0}).unwrap()
        context = self.contact_context()
        self.assertIsNone(self.preferences(app).reason("news"))
        self.sync(app, 60)
        with self.assertRaises(Exception) as caught:
            app.proactive.budget.contacts(context, self.preferences(app), self.clock.now())
        self.assertEqual(getattr(caught.exception, "code", ""), "contact_delivery_budget")
        app.contact_schedule.advance()
        await asyncio.sleep(0.05)
        self.assertEqual(port.calls, 0)
        self.assertEqual(self.queued_contacts(app), ())
        self.assert_no_contact_delivery(app)

    async def test_explicit_failure_is_one_bounded_attempt_and_a_failed_receipt(self):
        """用例 c1：明确失败 → 有界重试（该投递任务只允许一次尝试），状态与回执都是 failed。"""
        build, base, port = self.recorded_port()
        _host, _batch, app, _ = await self.news_host(build, lead=RECORDED_ALIGNMENT_LEAD)
        self.recorder.behavior = lambda _envelope: "not_sent"
        self.sync(app, 60)
        app.contact_schedule.advance()
        await self.until(lambda: self.delivery_rows(app)
                         and self.delivery_rows(app)[0]["state"] == "failed")
        await self.until(lambda: not app.contact_schedule.active_handles)

        rows = self.delivery_rows(app)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # 有界：投递任务定义本身就是 max_attempts=1（delivery/service.py:101），
        # 所以"重试"的边界就是"只尝试一次、且不重放"。
        definition = json.loads(app.runtime.read_one(
            "SELECT record_json FROM execution_tasks WHERE task_id = ?",
            (row["task_id"],))["record_json"])["definition"]
        self.assertEqual((definition["max_attempts"], definition["timeout_seconds"]), (1, 30))
        await self.until(lambda: self.attempts(app, row["task_id"])
                         == ({"attempt_no": 1, "generation": 1, "slot_held": 0},), seconds=30)
        self.assertEqual(len(self.recorder.news_envelopes(app)), 1)
        self.assertEqual(app.runtime.read_one(
            "SELECT state FROM execution_tasks WHERE task_id = ?", (row["task_id"],))[0], "failed")
        self.assertEqual(row["receipt_ref"], "not_sent:" + row["delivery_id"])
        # 这条链上没有一片拿到过 delivered：那条"明确失败"的投递只回 not_sent。
        self.assertEqual([record["status"] for record in self.recorder.news_envelopes(app)], ["not_sent"])
        # 再等一个完整机会窗口，仍然只有那一次尝试。
        self.sync(app, 1800)
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.recorder.news_envelopes(app)), 1)
        self.assertEqual(len(self.attempts(app, row["task_id"])), 1)

    async def test_platform_timeout_is_unknown_and_a_restart_never_replays_it(self):
        """用例 c2：平台超时 → unknown，重启后不重放，晚到的可信回执仍然算数。"""
        build, base, port = self.recorded_port()
        _host, _batch, app, _ = await self.news_host(build, lead=RECORDED_ALIGNMENT_LEAD)
        self.recorder.behavior = lambda _envelope: "timeout"
        self.sync(app, 60)
        app.contact_schedule.advance()
        await self.until(lambda: self.delivery_rows(app)
                         and self.delivery_rows(app)[0]["state"] == "unknown")
        await self.until(lambda: not app.contact_schedule.active_handles)

        rows = self.delivery_rows(app)
        self.assertEqual([row["state"] for row in rows], ["unknown"])
        self.assertEqual(len(self.recorder.news_envelopes(app)), 1)
        self.assertIsNone(rows[0]["receipt_ref"])
        self.assertIsNone(rows[0]["platform_ref"])
        self.assertEqual(app.runtime.read_one("SELECT state FROM operations WHERE operation_id = ?",
                                              (rows[0]["operation_id"],))["state"], "unknown")
        self.assertIsNone(app.runtime.read_one(
            "SELECT * FROM background_delivery_receipts WHERE delivery_id = ?", (rows[0]["delivery_id"],)))
        envelope = self.recorder.news_envelopes(app)[0]["envelope"]

        await app.close()
        restarted = (await self.start(self.host(runtime=self.open_runtime(), news_port=port),
                                      task_generation=1, model_generation=1)).application
        restarted.contact_schedule.advance()
        self.sync(restarted, 600)
        await asyncio.sleep(0.05)
        # 重启不重放：还是同一条 unknown，没有第二个 envelope。
        self.assertEqual(len(self.recorder.news_envelopes(app)), 1)
        self.assertEqual([row["state"] for row in self.delivery_rows(restarted)], ["unknown"])
        self.assertEqual(restarted.outbound.recover_dispatches(limit=32).unwrap(), ())

        # 晚到的可信回执把它落成 delivered，而不是第三次发送。
        self.assertEqual(restarted.record_receipt(self.recorder.receipt(envelope)).unwrap(), "delivered")
        self.assertEqual([row["state"] for row in self.delivery_rows(restarted)], ["delivered"])
        self.assertEqual(len(self.recorder.news_envelopes(app)), 1)

    async def test_same_idempotency_key_with_another_payload_is_refused(self):
        """用例 d：同 key 不同内容被拒，且原来的那条投递不受影响。

        投递身份 ``delivery_id`` 派生自 ``owner + logical_key``（``delivery/service.py:55``），
        而 ``logical_key`` 同时就是投递要读回的草稿身份。所以"同 key 不同内容"在这个
        端口上唯一可能的形态就是**同一片草稿换了 payload**；改配置/时间会换 key，
        由 owner 校验（``delivery_owner_changed``）负责拦住，两条都断言。
        """
        build, base, port = self.recorded_port()
        _host, _batch, app, _ = await self.news_host(build, lead=RECORDED_ALIGNMENT_LEAD)
        self.sync(app, 60)
        app.contact_schedule.advance()
        await self.automatic_delivery(app)
        await self.until(lambda: not app.contact_schedule.active_handles)

        rows = self.delivery_rows(app)
        self.assertEqual(len(rows), 1)
        original = DeliveryRequest.restore(rows[0]["request_json"])
        # 1) 原样重排：幂等命中，返回同一条任务，不新增投递。
        self.assertEqual(app.outbound.queue(original).unwrap().task_id, rows[0]["task_id"])
        self.assertEqual(len(self.delivery_rows(app)), 1)
        # 2) 同 key 不同内容：拒绝。
        forged_payload = replace(original, payload_hash="another-payload")
        self.assertEqual(forged_payload.delivery_id, original.delivery_id)
        self.assertEqual(app.outbound.queue(forged_payload).failure.code, "delivery_idempotency_conflict")
        # 3) 同一 key 上改时间同样是"同 key 不同内容"：key 不变，仍由 idempotency 守卫拒绝。
        #    递减 deadline 既保持"deadline 晚于起点"的请求不变式，又确实换掉了请求内容。
        forged_timing = replace(original, deadline_at=original.deadline_at - timedelta(seconds=30))
        self.assertEqual(forged_timing.delivery_id, original.delivery_id)
        self.assertEqual(app.outbound.queue(forged_timing).failure.code, "delivery_idempotency_conflict")
        # 4) 改 ``logical_key`` 会换 key，于是落到另一条草稿身份上，由 owner 校验拒绝。
        forged_key = replace(original, logical_key="forged-logical-key")
        self.assertNotEqual(forged_key.delivery_id, original.delivery_id)
        self.assertFalse(app.outbound.queue(forged_key).is_success)
        self.assertEqual(len(self.delivery_rows(app)), 1)
        self.assertEqual(len(self.recorder.news_envelopes(app)), 1)

    async def test_same_contact_opportunity_cannot_change_its_original_inputs(self):
        """用例 d（契机层）：同一个机会换一份念头/正文，被请求哈希拒绝。"""
        build, base, port = self.recorded_port()
        _host, _batch, app, _ = await self.news_host(build, lead=RECORDED_ALIGNMENT_LEAD)
        self.sync(app, 60)
        app.contact_schedule.advance()
        await self.automatic_delivery(app)
        await self.until(lambda: not app.contact_schedule.active_handles)

        saved = json.loads(self.queued_contacts(app)[0]["record_json"])
        marker = saved["automatic_origin"]["news_event"]
        events = app.contact_schedule.news_events
        context = self.contact_context()
        self.assertIsNone(events.validate(context, marker, self.clock.now()))
        for key, value in (("event_id", "news-forged"), ("settings_hash", "forged-settings")):
            with self.subTest(key=key):
                with self.assertRaises(Exception) as caught:
                    events.validate(context, {**marker, key: value}, self.clock.now())
                self.assertEqual(getattr(caught.exception, "code", ""), "contact_news_changed")
        self.assertEqual(len(self.recorder.news_envelopes(app)), 1)
        self.assertEqual(len(self.delivery_rows(app)), 1)

    async def test_source_failure_opens_a_durable_breaker_and_its_window_release_recovers(self):
        """用例 e：来源连续失败后熔断打开（保留期内不再尝试），窗口到期后恢复并送达。"""
        build, base, port = self.recorded_port(failures=[TimeoutError("no answer"),
            NewsFetchError("news_http_error", http_status=503)])
        _host, _batch, app, _ = await self.news_host(build, lead=RECORDED_ALIGNMENT_LEAD)
        self.sync(app, 60)
        app.contact_schedule.advance()
        await self.until(lambda: port.calls == 1 and not app.contact_schedule.active_handles)
        self.assertEqual(self.queued_contacts(app), ())
        self.assert_no_contact_delivery(app)

        row = app.runtime.read_one("SELECT * FROM proactive_news_polls", ())
        self.assertIsNone(row["checked_at"])
        self.assertIsNone(row["event_json"])
        self.assertEqual(datetime.fromisoformat(row["retry_after"]),
                         self.clock.now() + timedelta(minutes=5))
        events = app.contact_schedule.news_events
        diagnostic = events.diagnostic(self.contact_context(), self.preferences(app))
        self.assertEqual((diagnostic["network_status"], diagnostic["failure"]["code"]),
                         ("failed", "news_timeout"))

        # 熔断窗口内：不再尝试取源，也不产生 envelope。
        self.clock.advance(timedelta(minutes=4))
        app.notify()
        app.contact_schedule.advance()
        await asyncio.sleep(0.05)
        self.assertEqual(port.calls, 1)
        self.assertEqual(len(port.failures), 1)
        self.assert_no_contact_delivery(app)

        # 窗口到期：放行一次新的尝试，这次是带状态码的 HTTP 失败。
        self.clock.advance(timedelta(minutes=1, seconds=1))
        app.notify()
        app.contact_schedule.advance()
        await self.until(lambda: port.calls == 2 and not app.contact_schedule.active_handles)
        diagnostic = events.diagnostic(self.contact_context(), self.preferences(app))
        self.assertEqual((diagnostic["network_status"], diagnostic["failure"]["code"],
                          diagnostic["failure"]["http_status"]),
                         ("failed", "news_http_error", 503))
        self.assert_no_contact_delivery(app)

        # 再等一个窗口，来源恢复：链路自己走通并送达。
        self.clock.advance(timedelta(minutes=5, seconds=1))
        app.notify()
        app.contact_schedule.advance()
        await self.automatic_delivery(app)
        await self.until(lambda: not app.contact_schedule.active_handles)
        self.assertEqual(port.calls, 3)
        self.assertEqual([row["state"] for row in self.delivery_rows(app)], ["delivered"])
        self.assertEqual(len(self.recorder.news_envelopes(app)), 1)

    async def test_segmented_envelope_matches_the_sealed_parts_voice_and_poke_render(self):
        """用例 f（文本分支）：分片顺序与 TTS/戳一戳渲染态在 envelope 上与封存记录对齐。

        新闻来源用的是**手写近似样本，非录制**：这段断言要固定文案，录制样本的标题与
        摘要不在本用例的控制范围内。端口契约与录制样本相同。
        """
        build, base, port = self.synthetic_port()
        _host, _batch, app, _ = await self.news_host(build)

        async def segmented(_envelope):
            return answered(json.dumps({"decision": "send",
                "text": "第一句。\n" + SPLIT_MARKER + "\n第二句。",
                "voice_intent": True, "poke_intent": True}, ensure_ascii=False))

        self.model_behavior = segmented
        self.presentation_settings(app, {"enabled": True, "llm_controlled": True, "rules_enabled": False,
            "part_gap_mode": "fixed", "part_gap_ms": 0, **TEXT_DEFAULTS, "content_cleanup_enabled": False})
        self.settings(app, {"voice_action_enabled": True, "poke_action_enabled": True,
                            "voice_action_cooldown_minutes": 0, "poke_action_cooldown_minutes": 0,
                            "poke_action_daily_limit": 1}).unwrap()

        self.sync(app, 60)
        app.contact_schedule.advance()
        # 第二片由驱动按秒轮询的 ``advance_deliveries`` 排入（``application.py:736``），
        # 所以这里按状态轮询，并把每次轮询之间让出事件循环的时间放宽一些。
        await self.delivered_parts(app, 2, seconds=120)

        sent = self.recorder.news_envelopes(app)
        self.assertEqual(len(sent), 2)
        contacts = self.queued_contacts(app)
        self.assertEqual(len(contacts), 1)
        saved = json.loads(contacts[0]["record_json"])
        parts = saved["parts"]
        self.assertEqual(saved["news_provenance"], news_provenance(self.news_source(app, saved)))
        # 封存记录就是实际发送的文本；出处单独封存，主动气泡只包含正文。
        expected = [part["text"] for part in parts]
        self.assertEqual(len(expected), 2)
        self.assertEqual(expected[0], "第一句。")
        self.assertEqual(expected[1], "第二句。")
        # 封存记录里逐片保存的 payload 哈希必须就是每片实际发出的那份。
        self.assertEqual([part["payload_hash"] for part in parts],
                         [payload_hash(text, ()) for text in expected])

        # 分片与顺序：逐条与封存记录对齐，没有程序追加的出处页脚。
        self.assertEqual([record["sequence"] for record in sent], [0, 1])
        self.assertEqual([record["text"] for record in sent], expected)
        self.assertEqual([record["payload_hash"] for record in sent],
                         [payload_hash(text, ()) for text in expected])
        self.assertNotIn("来源：", sent[0]["text"])
        self.assertNotIn("来源：", sent[1]["text"])
        self.assertNotIn(saved["news_provenance"]["url"], sent[1]["text"])
        self.assertIsNone(sent[0]["plan"])
        # 渲染态：正文在前、语音紧跟、没有隐藏正文；两片都不带图片。
        self.assertEqual([record["render"]["kinds"] for record in sent], [("text",), ("text", "voice")])
        self.assertEqual([record["render"]["text_hidden"] for record in sent], [False, False])
        self.assertEqual([record["voice"] for record in sent], [None, saved["voice"]])
        self.assertEqual([record["poke"] for record in sent], [None, saved["poke"]])
        self.assertEqual((sent[1]["voice"]["speech"], sent[1]["voice"]["position"],
                          sent[1]["voice"]["delivery_mode"]),
                         (saved["voice"]["speech"], saved["voice"]["position"],
                          saved["voice"]["delivery_mode"]))
        self.assertEqual([record["images"] for record in sent], [(), ()])
        self.assertEqual([record["image_position"] for record in sent], ["after_text", "after_text"])
        rows = self.delivery_rows(app)
        self.assertEqual([row["state"] for row in rows], ["delivered", "delivered"])
        self.assertEqual([row["payload_hash"] for row in rows],
                         [payload_hash(text, ()) for text in expected])

    async def test_image_reaction_envelope_keeps_original_position_and_hash(self):
        """用例 f（图片分支）：真反应图选图 → 真 ImageDeliveryEnvelope 的渲染态与回执。

        新闻来源仍是**手写近似样本，非录制**；图片走的是生产路径上的
        ``semantics.reactions.select_for_contact`` → ``contact_image``
        （``service.py:669``、``:708``），不是测试自己拼的 envelope。
        """
        build, base, port = self.synthetic_port()
        port.batches = [lambda: [port.article(title="可用的表情素材")]]
        _host, _batch, app, _ = await self.news_host(build)
        self.semantic = app.attach_semantics(SemanticPort())
        self.semantic.store.configure({"reactions_proactive_enabled": True,
                                       "reactions_trigger_probability": 1.0,
                                       "reactions_cooldown_seconds": 0}, 0)
        from companion_v2.conversation.media.output_settings import GROUP as IMAGE_OUTPUT_GROUP
        target = ConfigTarget(INSTALLATION, ConfigLayer.ROLE, ROLE, bot_id=SCOPE.bot_id)
        snapshot = app.configuration.read(self.core, target=target, group_id=IMAGE_OUTPUT_GROUP,
                                          observed_at=self.clock.now()).unwrap()
        app.configuration.patch(self.core, target=target, group_id=IMAGE_OUTPUT_GROUP,
            expected_edit_token=snapshot.edit_token,
            changes={IMAGE_OUTPUT_GROUP + ".proactive_probability": 1.0,
                     IMAGE_OUTPUT_GROUP + ".proactive_max_daily": 1},
            operation=self.operation(target.authorization_scope, owner=IMAGE_OUTPUT_GROUP),
            observed_at=self.clock.now()).unwrap()
        item = self.semantic.reactions.import_image(self.semantic.owner(self.contact_context()),
            _picture(), {"name": "安慰", "description": "安慰", "intents": "安慰"})

        async def with_reaction(_envelope):
            return answered(json.dumps({"decision": "send",
                "text": "看到这条资讯，想到这张图。", "reaction_intent": "安慰"},
                ensure_ascii=False))

        self.model_behavior = with_reaction
        self.sync(app, 60)
        app.contact_schedule.advance()
        await self.delivered_parts(app, 2)

        sent = self.recorder.news_envelopes(app)
        self.assertEqual(len(sent), 2)
        text_record, record = sent
        contacts = self.queued_contacts(app)
        self.assertEqual(len(contacts), 1)
        saved = json.loads(contacts[0]["record_json"])
        # 默认分开：正文一条，表情图单独一条空文本段。
        self.assertEqual(len(saved["parts"]), 2)
        self.assertEqual(saved["parts"][0]["text"], contacts[0]["body"])
        self.assertEqual(saved["parts"][1]["text"], "")
        self.assertEqual(saved["reaction"]["manifest"], record["images"][0])
        self.assertEqual(text_record["images"], ())
        self.assertEqual(text_record["render"]["kinds"], ("text",))
        self.assertEqual(record["images"][0]["descriptor"]["content_hash"], item["id"])
        self.assertIsInstance(record["envelope"], ImageDeliveryEnvelope)
        self.assertEqual(len(record["images"]), 1)
        self.assertEqual(record["text"], "")
        self.assertEqual(record["render"]["kinds"], ("image",))
        self.assertEqual(record["payload_hash"], payload_hash("", (record["envelope"].images)))
        # 封存、投递行与回执指向同一条图片 payload。
        rows = self.delivery_rows(app)
        row = rows[1]
        self.assertEqual(row["payload_hash"], record["payload_hash"])
        self.assertEqual(row["state"], "delivered")
        self.assertEqual(json.loads(app.runtime.read_one(
            "SELECT receipt_json FROM background_delivery_receipts WHERE delivery_id = ?",
            (row["delivery_id"],))["receipt_json"])["payload_hash"], record["payload_hash"])
        self.assertEqual(text_record["text"], contacts[0]["body"])
        self.assertNotIn("来源：", text_record["text"])
        self.assertEqual(saved["news_provenance"]["source"], "示例媒体")


def _picture():
    from companion_v2.conversation.media.models import CapturedImage
    return CapturedImage(PICTURE, PICTURE_BYTES)


if __name__ == "__main__":
    unittest.main()
