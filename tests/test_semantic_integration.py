import asyncio
import base64
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import io
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from PIL import Image

from astrbot_host.media import inspect_image
from companion_v2.conversation.media.animation import CapturedAnimation, PromptAnimation
from companion_v2.conversation.media.models import CapturedImage, ImageDescriptor
from companion_v2.conversation.media.reply import ImageReplyDraft
from companion_v2.execution.models import TaskError
from companion_v2.model_gateway.embedding import EmbeddingGateway, unit
from companion_v2.proactive.external_materials import NewsItem, ParseBatch
from companion_v2.storage.semantic import SemanticStore
from tests.test_host_application import HostFixture
from tests.test_role_tool_loop import requested, answered, call


class SemanticPort:
    def __init__(self):
        self.version = "model-1"
        self.calls = []
        self.failing = False
        self.rerank_failing = False
        self.rerank_scores = None
        self.analysis_images = []
        self.capture_suitable = True
        self.metadata_result = None

    def binding(self, identifier, kind):
        return kind + self.version

    def inventory(self):
        return {"embedding": [{"id": "test", "name": "test"}], "rerank": []}

    async def embed(self, identifier, texts):
        self.calls.extend(texts)
        if self.failing:
            raise RuntimeError("unavailable")
        return [[1., 0., 0.] if any(w in t for w in ("安慰", "抱抱")) else
                [0., 1., 0.] if "嘲笑" in t else [0., 0., 1.] for t in texts]

    async def analyze(self, identifier, prompt, images=()):
        self.analysis_images.append(tuple(images))
        if "判断图片是否适合作为独立的聊天表情素材" in prompt:
            if not self.capture_suitable:
                return {"suitable": False}
            return {"suitable": True, "name": "挥手", "description": "挥手回应", "visible_text": "你好",
                    "tags": "挥手", "emotions": "友好", "intents": "问候、回应招呼"}
        if "为聊天表情建立元数据" in prompt:
            return self.metadata_result or {"name": "挥手", "description": "两帧颜色交替的动画", "intents": "回应问候"}
        return {"fit": True, "reason": "与安慰意图贴合"} if images else []

    async def rerank(self, identifier, query, documents):
        if self.rerank_failing:
            raise RuntimeError("rerank unavailable")
        return self.rerank_scores if self.rerank_scores is not None else list(reversed(range(len(documents))))


def picture():
    content = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXmEAAAAASUVORK5CYII=")
    return CapturedImage(ImageDescriptor(0, "image/png", 1, 1, len(content), hashlib.sha256(content).hexdigest()), content)


def animation():
    output = io.BytesIO()
    first = Image.new("RGB", (8, 8), "red")
    second = Image.new("RGB", (8, 8), "green")
    first.save(output, format="GIF", save_all=True, append_images=[second], duration=[120, 180], loop=0)
    return inspect_image(output.getvalue(), 0)


class EmbeddingTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_isolated_by_owner_and_model(self):
        port = SemanticPort()
        gateway = EmbeddingGateway(port)
        await gateway.embed("a", ["抱抱"])
        await gateway.embed("a", ["抱抱"])
        self.assertEqual(len(port.calls), 1)
        await gateway.embed("b", ["抱抱"])
        port.version = "model-2"
        await gateway.embed("a", ["抱抱"])
        self.assertEqual(len(port.calls), 3)

    def test_reject_nonfinite_and_zero_vectors(self):
        for payload in ([0, 0], [float("nan")], [float("inf")], [True]):
            with self.assertRaises(ValueError):
                unit(payload)

    def test_feedback_columns_upgrade_existing_catalog(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "semantic-catalog.sqlite3"
            with closing(sqlite3.connect(path)) as db:
                with db:
                    db.execute("CREATE TABLE reaction_inputs (owner TEXT, event_key TEXT, actor TEXT, PRIMARY KEY(owner,event_key))")
                    db.execute("INSERT INTO reaction_inputs VALUES ('owner','event','speaker')")
                    db.execute("PRAGMA user_version=2")
            store = SemanticStore(Path(temporary))
            with store.db() as db:
                row = db.execute("SELECT * FROM reaction_inputs WHERE event_key='event'").fetchone()
                self.assertEqual((row["actor"], row["received_at"], row["signal"]),
                                 ("speaker", None, None))
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 8)

class SemanticConversationTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def test_foreground_query_embeds_once_without_waiting_for_index_lock(self):
        from unittest.mock import AsyncMock, patch
        host = await self.semantic_host()
        app = host.application
        batch = host.receive(self.event("semantic-query-once", text="想要安慰和抱抱")).unwrap()
        context = self.context(app, batch)
        lock = self.semantic.locks.setdefault(self.semantic.owner(context), asyncio.Lock())
        await lock.acquire()
        self.addCleanup(lock.release)
        with patch.object(self.semantic.memory, "prepare", AsyncMock(side_effect=AssertionError("foreground index fill"))), \
                patch.object(self.semantic, "schedule_memory"), \
                patch.object(app.memory.context, "enrich_semantic") as enrich:
            self.advance(app)
            self.assertEqual((await self.done(app, batch))["state"], "complete")
            enrich.assert_called_once()
        self.assertEqual(self.port.calls.count("想要安慰和抱抱"), 1)

    async def semantic_host(self, *, images=False, automatic_reactions=True, reaction_role_count=0):
        if images:
            self.sender = replace(self.sender, supports_images=True)
            self.providers = tuple(replace(p, capabilities=("text", "tools", "image")) for p in self.providers)
        host = self.host()
        self.port = SemanticPort()
        self.semantic = host.application.attach_semantics(self.port,
                                                          reaction_role_count=reaction_role_count)
        if images and automatic_reactions:
            settings = self.semantic.store.settings()
            self.semantic.store.configure({"reactions_trigger_probability": 1.0,
                                           "reactions_cooldown_seconds": 0}, settings["revision"])
        await self.start(host)
        return host

    async def test_reaction_collection_samples_direct_images_and_requires_visual_approval(self):
        host = await self.semantic_host(images=True, automatic_reactions=False)
        app = host.application
        batch = host.receive(self.event("capture-context")).unwrap()
        context = self.context(app, batch)
        settings = self.semantic.store.settings()
        self.assertFalse(settings["reactions_collection_private_enabled"])
        self.assertFalse(settings["reactions_collection_group_enabled"])
        settings = self.semantic.store.configure({
            "reactions_collection_private_enabled": True,
            "reactions_collection_private_probability": 0.0,
        }, settings["revision"])

        captured = picture()
        direct_source = object()
        prompt_image = SimpleNamespace(source=direct_source)
        reads = []
        app.messages.images.read = lambda current, events, at: (reads.append(events) or (prompt_image,))
        self.semantic.schedule_reaction_collection("capture-not-sampled", context,
            SimpleNamespace(event=SimpleNamespace(images=(captured.descriptor,)),
                context=SimpleNamespace(materials=(SimpleNamespace(purpose="input_image:current",
                    source=direct_source),))))
        self.assertEqual(reads, [], "a probability miss must not read the original image")

        settings = self.semantic.store.configure({"reactions_collection_private_probability": 1.0},
                                                  settings["revision"])
        self.assertEqual(settings["reactions_collection_private_probability"], 1.0)

        app.messages.images.read = lambda current, events, at: (prompt_image,)
        self.semantic.reactions.capture_prompt_image = lambda image: captured
        owner = self.semantic.ensure_reaction_catalog(context)

        def request(purpose):
            return SimpleNamespace(event=SimpleNamespace(images=(captured.descriptor,)),
                context=SimpleNamespace(materials=(SimpleNamespace(purpose=purpose, source=direct_source),)))

        self.port.capture_suitable = False
        self.semantic.schedule_reaction_collection("capture-unsuitable", context,
                                                    request("input_image:current"))
        pending = [task for key, task in self.semantic.background.items() if key.startswith("reaction-capture:")]
        await asyncio.gather(*pending)
        self.assertEqual(self.semantic.store.list(owner, "reaction"), [])

        reads = []
        app.messages.images.read = lambda current, events, at: (reads.append(events) or (prompt_image,))
        self.semantic.schedule_reaction_collection("capture-quoted", context, request("quoted_image:original"))
        self.assertEqual(reads, [], "quoted images must never be fetched for collection")

        self.port.capture_suitable = True
        self.semantic.schedule_reaction_collection("capture-approved", context, request("input_image:current"))
        pending = [task for key, task in self.semantic.background.items() if key.startswith("reaction-capture:")]
        await asyncio.gather(*pending)
        items = self.semantic.store.list(owner, "reaction")
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["payload"]["collection_source"], "private_capture")
        self.assertEqual(item["payload"]["analysis"], "auto_reviewed")
        self.assertEqual(item["payload"]["intents"], "问候、回应招呼")
        self.assertEqual(self.semantic.reactions.captured(item).content, captured.content)

        self.semantic.schedule_reaction_collection("capture-duplicate", context, request("input_image:current"))
        pending = [task for key, task in self.semantic.background.items() if key.startswith("reaction-capture:")]
        await asyncio.gather(*pending)
        self.assertEqual(len(self.semantic.store.list(owner, "reaction")), 1)
        totals = self.semantic.store.reaction_collection_summary(owner)["totals"]["private_capture"]
        self.assertEqual((totals["added"], totals["duplicate"]), (1, 1))

    async def test_visual_analysis_preserves_manual_fields_and_fills_the_rest(self):
        host = await self.semantic_host(images=True)
        owner = "manual-field-owner"
        item = self.semantic.reactions.import_image(owner, picture(),
            {"name": "人工名称", "description": "人工画面标注"})
        self.port.metadata_result = {"name": "模型名称", "description": "模型画面", "visible_text": "你好",
                                     "tags": "挥手", "emotions": "友好", "intents": "回应问候"}
        analyzed = await self.semantic.reactions.analyze(owner, item["id"])
        self.assertEqual(analyzed["payload"]["name"], "人工名称")
        self.assertEqual(analyzed["payload"]["description"], "人工画面标注")
        self.assertEqual(analyzed["payload"]["tags"], "挥手")
        self.assertEqual(analyzed["payload"]["intents"], "回应问候")

    async def test_visual_analysis_keeps_existing_metadata_when_model_omits_a_field(self):
        host = await self.semantic_host(images=True)
        owner = "sparse-analysis-owner"
        item, created = self.semantic.reactions.import_image_with_status(owner, picture(), {
            "name": "已识别名称", "description": "旧画面描述", "visible_text": "旧文字",
            "tags": "旧标签", "emotions": "旧情绪", "intents": "旧用途",
        }, collection_source="private_capture", auto_reviewed=True)
        self.assertTrue(created)
        self.port.metadata_result = {"description": "更新后的画面描述"}
        analyzed = await self.semantic.reactions.analyze(owner, item["id"])
        self.assertEqual(analyzed["payload"]["description"], "更新后的画面描述")
        self.assertEqual(analyzed["payload"]["visible_text"], "旧文字")
        self.assertEqual(analyzed["payload"]["intents"], "旧用途")

    async def test_legacy_reaction_import_keeps_migration_provenance(self):
        host = await self.semantic_host(images=True)
        owner = "legacy-reaction-owner"
        item, created = self.semantic.reactions.import_image_with_status(owner, picture(), {
            "name": "旧版挥手", "description": "V1 表情迁移",
        }, collection_source="legacy_migration")
        self.assertTrue(created)
        self.assertEqual(item["payload"]["collection_source"], "legacy_migration")
        self.assertEqual(item["payload"]["analysis"], "legacy_imported")
        self.assertIn("name", item["payload"]["manual_fields"])

    async def test_approved_collection_can_wait_for_manual_confirmation(self):
        await self.semantic_host(images=True, automatic_reactions=False)
        settings = self.semantic.store.settings()
        self.semantic.store.configure({"reactions_collection_mode": "confirm"}, settings["revision"])
        status, item = await self.semantic.reactions.collect_candidate(
            "confirm-owner", picture(), "private_capture", lambda: None)
        self.assertEqual(status, "pending_confirmation")
        self.assertFalse(item["enabled"])
        self.assertEqual(item["payload"]["collection_state"], "awaiting_confirmation")

    async def test_text_delivery_preserves_unresolved_reaction_usage_error(self):
        async def respond(_envelope):
            return answered("我在。")

        self.model_behavior = respond
        host = await self.semantic_host()
        app = host.application
        self.semantic.errors["reaction_usage"] = "reaction_usage_manifest_changed"
        batch = host.receive(self.event("text-only", text="在吗？")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual(self.semantic.errors["reaction_usage"], "reaction_usage_manifest_changed")

    async def test_memory_tool_generates_vectors_without_model_supplied_vector(self):
        phase = {"seed": True}
        async def respond(envelope):
            if not envelope.tool_history:
                if phase["seed"]:
                    handles = json.loads(next(m.payload for m in envelope.original.context.materials if m.purpose == "current_input_handles"))
                    return requested(call("save", "memory.proposal.submit", proposal={"operation": "add", "predicate": "饮品偏好",
                        "object": "不喝咖啡", "representation": "用户不喝咖啡", "evidence_handles": handles}))
                return requested(call("recall", "memory.query", query_intent="那种苦味饮料", kind="facts"))
            if not phase["seed"]:
                payload = json.loads(envelope.tool_history[-1].result_json)["payload"]
                self.assertEqual(payload["mode"], "hybrid")
                self.assertTrue(payload["items"])
                self.assertGreater(payload["vector_coverage"]["embedded"], 0)
            return answered("记得了。")
        self.model_behavior = respond
        host = await self.semantic_host()
        app = host.application
        first = host.receive(self.event("seed", text="我不喝咖啡")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, first))["state"], "complete")
        phase["seed"] = False
        second = host.receive(self.event("query", text="那种苦味饮料")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, second))["state"], "complete")

    async def test_private_turn_automatically_injects_semantic_fact(self):
        phase = {"seed": True}

        async def respond(envelope):
            if phase["seed"]:
                if not envelope.tool_history:
                    handles = json.loads(next(m.payload for m in envelope.original.context.materials
                                              if m.purpose == "current_input_handles"))
                    return requested(call("save", "memory.proposal.submit", proposal={
                        "operation": "add", "predicate": "饮品偏好", "object": "不喝咖啡",
                        "representation": "用户不喝咖啡", "evidence_handles": handles,
                    }))
                return answered("记好了。")
            material = next((m for m in envelope.original.context.materials if m.purpose == "current_memory"), None)
            self.assertIsNotNone(material)
            self.assertEqual(json.loads(material.payload)["object"], "不喝咖啡")
            return answered("记得，你平时不喝咖啡。")

        self.model_behavior = respond
        host = await self.semantic_host()
        app = host.application
        first = host.receive(self.event("semantic-seed", text="我不喝咖啡")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, first))["state"], "complete")
        phase["seed"] = False
        second = host.receive(self.event("semantic-followup", text="那种苦味饮料我平时喝吗？")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, second))["state"], "complete")

    async def test_ai_news_feed_is_fetched_on_demand_with_governed_citations(self):
        class NewsPort:
            fingerprint = "conversation-news-test-v1"
            source_label = "橘鸦AI早报"

            def __init__(self, published_at):
                self.published_at = published_at
                self.calls = 0

            async def fetch(self):
                self.calls += 1
                return ParseBatch((NewsItem("示例媒体", "news-1", "OpenAI 发布新模型",
                    "https://news.example/openai-release", "发布了新的 AI 模型与工具。", self.published_at),), ())

        host = await self.semantic_host()
        app = host.application
        port = NewsPort(self.clock.now() - timedelta(minutes=10))
        app.contact_schedule.news_events.port = port

        casual = host.receive(self.event("news-casual", text="我今天在整理桌面。" )).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, casual))["state"], "complete")
        self.assertEqual(port.calls, 0)

        batch = host.receive(self.event("news-query", text="AI 最近有什么新消息？能附上出处吗？")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual(port.calls, 1)
        material = next(item for item in self.called[-1].original.context.materials
                        if item.owner == "conversation.news")
        self.assertIsNotNone(material.source)
        item = json.loads(material.payload)["items"][0]
        self.assertEqual((item["source"], item["url"]),
                         ("示例媒体", "https://news.example/openai-release"))
        self.assertEqual(datetime.fromisoformat(item["published_at"]), port.published_at)
        context = self.context(app, batch)
        self.assertTrue(app.proactive.sources.validate_reference_for_audience(material.source,
            recipient=context.actor.subject, observed_at=self.clock.now()).is_success)
        preferences = app.proactive.preferences.read(context, observed_at=self.clock.now()).unwrap()
        diagnostic = app.contact_schedule.news_events.diagnostic(context, preferences)
        self.assertEqual(diagnostic["network_status"], "succeeded")
        self.assertEqual(diagnostic["item_count"], 1)

    async def test_ai_news_query_without_valid_recent_items_gets_no_memory_fallback(self):
        class EmptyNewsPort:
            fingerprint = "conversation-news-empty-v1"
            source_label = "橘鸦AI早报"

            def __init__(self):
                self.calls = 0

            async def fetch(self):
                self.calls += 1
                return ParseBatch((), ())

        async def respond(envelope):
            return answered("暂时没有可靠的近期资讯。")

        self.model_behavior = respond
        host = await self.semantic_host()
        app = host.application
        port = EmptyNewsPort()
        app.contact_schedule.news_events.port = port

        batch = host.receive(self.event("news-empty", text="AI 最近有什么新消息？")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual(port.calls, 1)
        envelope = self.called[-1].original
        self.assertNotIn("conversation.news", {item.owner for item in envelope.context.materials})
        self.assertIn("没有取得可验证的近期来源材料", envelope.instruction)
        context = self.context(app, batch)
        preferences = app.proactive.preferences.read(context, observed_at=self.clock.now()).unwrap()
        self.assertEqual(app.contact_schedule.news_events.diagnostic(context, preferences)["network_status"], "succeeded")

    async def test_private_turn_keeps_lexical_recall_when_embedding_fails(self):
        phase = {"seed": True}

        async def respond(envelope):
            if phase["seed"]:
                if not envelope.tool_history:
                    handles = json.loads(next(m.payload for m in envelope.original.context.materials
                                              if m.purpose == "current_input_handles"))
                    return requested(call("save", "memory.proposal.submit", proposal={
                        "operation": "add", "predicate": "饮品偏好", "object": "不喝咖啡",
                        "representation": "用户不喝咖啡", "evidence_handles": handles,
                    }))
                return answered("记好了。")
            material = next((m for m in envelope.original.context.materials if m.purpose == "current_memory"), None)
            self.assertIsNotNone(material)
            self.assertEqual(json.loads(material.payload)["object"], "不喝咖啡")
            return answered("我记得你不喝咖啡。")

        self.model_behavior = respond
        host = await self.semantic_host()
        app = host.application
        first = host.receive(self.event("lexical-seed", text="我不喝咖啡")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, first))["state"], "complete")
        self.port.failing = True
        phase["seed"] = False
        second = host.receive(self.event("lexical-followup", text="不喝咖啡吗？")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, second))["state"], "complete")

    async def test_private_turn_automatically_injects_semantic_experience(self):
        async def respond(envelope):
            material = next((m for m in envelope.original.context.materials if m.purpose == "current_experience"), None)
            if material is not None:
                self.assertIn("小桥", json.loads(material.payload)["statements"][0]["text"])
            return answered("记得那次走到了小桥边。")

        self.model_behavior = respond
        host = await self.semantic_host()
        app = host.application
        first = host.receive(self.event("experience-seed", text="我们在河边散步，走到了小桥边")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, first))["state"], "complete")
        context = self.context(app, first)
        row = app.runtime.read_one("SELECT event_json FROM conversation_inputs WHERE batch_id = ?", (first,))
        from companion_v2.memory.codec import source as source_from_json
        source = source_from_json(json.loads(row["event_json"])["source"])
        evidence = app.memory.writer.access.capture(context, source)
        saved = app.memory.experiences.save(context, view_id="river-walk", expected_revision=0,
            title="河边散步", statements=({"text": "那次散步走到了小桥边。", "evidence": (evidence,), "atoms": ()},))
        self.assertEqual(saved.unwrap()["state"], "saved")
        second = host.receive(self.event("experience-followup", text="那次户外活动最后到哪儿了？")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, second))["state"], "complete")

    async def test_selected_catalog_image_reaches_existing_delivery(self):
        async def respond(envelope):
            if not envelope.tool_history:
                return requested(call("react", "reaction.search", intent="想安慰一下"))
            images = envelope.original.images
            self.assertEqual(len(images), 1)
            value = answered("抱抱你。")
            return replace(value, draft=ImageReplyDraft("抱抱你。", (), images=(images[0].source,)))
        self.model_behavior = respond
        host = await self.semantic_host(images=True)
        app = host.application
        batch = host.receive(self.event("reaction", text="有点难过")).unwrap()
        context = self.context(app, batch)
        self.semantic.reactions.import_image(self.semantic.owner(context), picture(),
            {"name": "抱抱", "description": "伸手抱抱", "intents": "安慰"})
        self.semantic.errors["reaction_usage"] = "reaction_usage_manifest_changed"
        self.advance(app)
        state = await self.done(app, batch)
        self.assertEqual(state["state"], "complete", dict(state))
        self.assertEqual([(part.text, len(getattr(part, "images", ()))) for part in self.sent],
                         [("抱抱你。", 0), ("", 1)])
        self.assertEqual(len(self.sent[-1].images), 1)
        self.assertEqual(self.sent[-1].images[0].descriptor.content_hash, picture().descriptor.content_hash)
        history = app.messages.history(context).unwrap()["items"]
        self.assertEqual([(part["text"], len(part.get("images", ()))) for part in history],
                         [("抱抱你。", 0), ("", 1)])
        with self.semantic.store.db() as db:
            usage = db.execute("SELECT id,batch FROM uses").fetchall()
        self.assertEqual([(row["id"], row["batch"]) for row in usage], [(picture().descriptor.content_hash, batch)])
        self.assertNotIn("reaction_usage", self.semantic.errors)

    async def test_authorized_image_only_reply_delivers_no_text(self):
        async def respond(envelope):
            if not envelope.tool_history:
                return requested(call("react", "reaction.search", intent="只想用表情安慰"))
            image = envelope.original.images[0]
            value = answered("占位正文")
            return replace(value, draft=ImageReplyDraft("", (), action="image", images=(image.source,)))

        self.model_behavior = respond
        host = await self.semantic_host(images=True)
        app = host.application
        settings = self.semantic.store.settings()
        self.semantic.store.configure({"reactions_delivery_mode": "same_message"}, settings["revision"])
        batch = host.receive(self.event("image-only-reaction", text="有点难过")).unwrap()
        context = self.context(app, batch)
        self.semantic.reactions.import_image(self.semantic.owner(context), picture(),
            {"name": "抱抱", "description": "伸手抱抱", "intents": "安慰"})

        self.advance(app)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual([(part.text, len(getattr(part, "images", ()))) for part in self.sent], [("", 1)])
        history = app.messages.history(context).unwrap()["items"]
        self.assertEqual([(part["text"], len(part.get("images", ()))) for part in history], [("", 1)])
        self.assertEqual(self.sent[0].images[0].descriptor.content_hash, picture().descriptor.content_hash)

    async def test_reaction_can_precede_text_or_share_its_message(self):
        async def respond(envelope):
            if not envelope.tool_history:
                return requested(call("react", "reaction.search", intent="安慰"))
            image = envelope.original.images[0]
            return replace(answered("抱抱你。"), draft=ImageReplyDraft("抱抱你。", (), images=(image.source,)))

        self.model_behavior = respond
        host = await self.semantic_host(images=True)
        app = host.application
        settings = self.semantic.store.settings()
        self.semantic.store.configure({"reactions_delivery_mode": "separate_before"}, settings["revision"])
        first = host.receive(self.event("reaction-before", text="难过" )).unwrap()
        context = self.context(app, first)
        self.semantic.reactions.import_image(self.semantic.owner(context), picture(),
            {"name": "抱抱", "description": "温柔安慰", "intents": "安慰"})
        self.advance(app)
        self.assertEqual((await self.done(app, first))["state"], "complete")
        self.assertEqual([(part.text, len(getattr(part, "images", ()))) for part in self.sent],
                         [("", 1), ("抱抱你。", 0)])
        self.assertEqual([len(part.get("images", ())) for part in app.messages.history(context).unwrap()["items"]],
                         [1, 0])

        settings = self.semantic.store.settings()
        self.semantic.store.configure({"reactions_delivery_mode": "same_message"}, settings["revision"])
        second = host.receive(self.event("reaction-same", text="还是难过")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, second, seconds=8))["state"], "complete")
        self.assertEqual([(part.text, len(getattr(part, "images", ()))) for part in self.sent[-1:]],
                         [("抱抱你。", 1)])

    async def test_automatic_probability_zero_keeps_explicit_reaction_request(self):
        async def respond(envelope):
            if not any(tool.action == "reaction.search" for tool in envelope.tools):
                return answered("我在。")
            if not envelope.tool_history:
                return requested(call("react", "reaction.search", intent="安慰"))
            image = envelope.original.images[0]
            return replace(answered("抱抱你。"), draft=ImageReplyDraft("抱抱你。", (), images=(image.source,)))

        self.model_behavior = respond
        host = await self.semantic_host(images=True, automatic_reactions=False)
        app = host.application
        settings = self.semantic.store.settings()
        self.assertEqual((settings["reactions_trigger_probability"], settings["reactions_cooldown_seconds"]),
                         (0.2, 180))
        self.semantic.store.configure({"reactions_trigger_probability": 0}, settings["revision"])
        ordinary = host.receive(self.event("reaction-auto-zero", text="有点难过")).unwrap()
        context = self.context(app, ordinary)
        self.semantic.reactions.import_image(self.semantic.owner(context), picture(),
            {"name": "抱抱", "description": "温柔安慰", "intents": "安慰"})
        self.advance(app)
        self.assertEqual((await self.done(app, ordinary))["state"], "complete")
        self.assertNotIn("reaction.search", {tool.action for tool in self.called[-1].tools})
        self.assertNotIn("可以调用 reaction.search", self.called[-1].original.instruction)
        self.assertFalse(getattr(self.sent[-1], "images", ()))

        explicit = host.receive(self.event("reaction-explicit-zero", text="给我发表情包")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, explicit))["state"], "complete")
        self.assertIn("reaction.search", {tool.action for tool in self.called[-1].tools})
        self.assertEqual([(part.text, len(getattr(part, "images", ()))) for part in self.sent[-1:]],
                         [("抱抱你。", 1)])
        self.assertEqual([(part["text"], len(part.get("images", ()))) for part in
                          app.messages.history(context).unwrap()["items"][-1:]], [("抱抱你。", 1)])

    async def test_reaction_cooldown_uses_confirmed_delivery_and_survives_restart(self):
        async def respond(envelope):
            if not any(tool.action == "reaction.search" for tool in envelope.tools):
                return answered("我在。")
            if not envelope.tool_history:
                return requested(call("react", "reaction.search", intent="安慰"))
            image = envelope.original.images[0]
            return replace(answered("抱抱你。"), draft=ImageReplyDraft("抱抱你。", (), images=(image.source,)))

        self.model_behavior = respond
        host = await self.semantic_host(images=True)
        app = host.application
        settings = self.semantic.store.settings()
        self.semantic.store.configure({"reactions_cooldown_seconds": 180}, settings["revision"])
        first = host.receive(self.event("reaction-cooldown-first", text="有点难过")).unwrap()
        context = self.context(app, first)
        self.semantic.reactions.import_image(self.semantic.owner(context), picture(),
            {"name": "抱抱", "description": "温柔安慰", "intents": "安慰"})
        self.advance(app)
        self.assertEqual((await self.done(app, first, seconds=12))["state"], "complete")
        self.assertEqual(len(self.sent[-1].images), 1)

        second = host.receive(self.event("reaction-cooldown-second", text="还是有点难过")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, second, seconds=12))["state"], "complete")
        self.assertNotIn("reaction.search", {tool.action for tool in self.called[-1].tools})
        await app.close()
        restarted = self.host(runtime=self.open_runtime())
        self.semantic = restarted.application.attach_semantics(self.port)
        await self.start(restarted, 1, 1)
        app = restarted.application
        third = restarted.receive(self.event("reaction-cooldown-restart", text="再安慰我一下")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, third, seconds=12))["state"], "complete")
        self.assertNotIn("reaction.search", {tool.action for tool in self.called[-1].tools})

        explicit = restarted.receive(self.event("reaction-cooldown-explicit", text="给我发表情包")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, explicit, seconds=12))["state"], "complete")
        self.assertEqual(len(self.sent[-1].images), 1)
        self.clock.advance(timedelta(seconds=180))
        after = restarted.receive(self.event("reaction-cooldown-expired", text="有点难过")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, after, seconds=12))["state"], "complete")
        self.assertIn("reaction.search", {tool.action for tool in self.called[-1].tools})

    async def test_reaction_pause_single_request_and_resume_persist(self):
        host = await self.semantic_host(images=True)
        app = host.application
        paused = host.receive(self.event("reaction-pause", text="别再发表情包")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, paused))["state"], "complete")
        context = self.context(app, paused)
        self.assertNotIn("reaction.search", {t.action for t in self.semantic.catalog_for(context, paused)})
        self.assertIn("暂停自动发表情", self.called[-1].original.instruction)

        requested_batch = host.receive(self.event("reaction-request", text="给我发表情包")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, requested_batch))["state"], "complete")
        self.assertIn("reaction.search", {t.action for t in self.semantic.catalog_for(context, requested_batch)})
        self.assertTrue(self.semantic.reaction_preferences.state(context, requested_batch)[0])
        self.assertNotIn("reaction.search", {t.action for t in self.semantic.catalog_for(context)})

        await app.close()
        restarted = self.host(runtime=self.open_runtime())
        self.semantic = restarted.application.attach_semantics(self.port)
        await self.start(restarted, 1, 1)
        app = restarted.application
        resumed = restarted.receive(self.event("reaction-resume", text="恢复发表情包")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, resumed))["state"], "complete")
        context = self.context(app, resumed)
        self.assertIn("reaction.search", {t.action for t in self.semantic.catalog_for(context, resumed)})
        self.assertEqual(self.semantic.reaction_preferences.state(context, resumed), (False, False))

    async def test_delivered_reaction_feedback_is_once_per_asset_and_speaker(self):
        phase = {"send": True}

        async def respond(envelope):
            if not phase["send"]:
                return answered("收到。")
            if not envelope.tool_history:
                return requested(call("react", "reaction.search", intent="安慰"))
            image = envelope.original.images[0]
            return replace(answered("抱抱。"), draft=ImageReplyDraft("抱抱。", (), images=(image.source,)))

        self.model_behavior = respond
        host = await self.semantic_host(images=True)
        app = host.application
        first = host.receive(self.event("reaction-feedback-source", text="有点难过")).unwrap()
        context = self.context(app, first)
        owner = self.semantic.owner(context)
        catalog_owner = self.semantic.ensure_reaction_catalog(context)
        item = self.semantic.reactions.import_image(catalog_owner, picture(),
            {"name": "抱抱", "description": "温柔安慰", "intents": "安慰"})
        self.advance(app)
        self.assertEqual((await self.done(app, first))["state"], "complete")
        phase["send"] = False

        feedback_event = self.event("reaction-negative", text="刚才那张表情包不合适")
        feedback = host.receive(feedback_event).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, feedback))["state"], "complete")
        self.assertEqual(self.semantic.reaction_preferences.scores(context)[item["id"]], -1)
        self.assertEqual(host.receive(feedback_event).unwrap(), feedback)
        second = host.receive(self.event("reaction-repeat-feedback", text="这张表情包不错")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, second))["state"], "complete")
        self.assertEqual(self.semantic.reaction_preferences.scores(context)[item["id"]], -1)
        with self.semantic.store.db() as db:
            recorded = db.execute("SELECT feedback_signal FROM reaction_deliveries WHERE owner=? AND id=?",
                                  (owner, item["id"])).fetchone()
        self.assertEqual(recorded["feedback_signal"], "negative")

    async def test_reaction_preference_isolated_by_speaker_and_session(self):
        host = await self.semantic_host(images=True)
        app = host.application
        batch = host.receive(self.event("scope-pause", text="别再发表情包")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        context = self.context(app, batch)
        other_speaker = replace(context, actor=self.standard)
        other_scope = replace(context.scope, session_id="session-b")
        other_session = replace(context, role_binding=replace(context.role_binding, scope=other_scope),
            audience=replace(context.audience, scope=other_scope))
        preferences = self.semantic.reaction_preferences
        self.assertEqual(preferences.state(context)[0], True)
        self.assertEqual(preferences.state(other_speaker)[0], False)
        self.assertEqual(preferences.state(other_session)[0], False)
        self.assertEqual(len({preferences.actor(value) for value in
                              (context, other_speaker, other_session)}), 3)

    async def test_reaction_catalog_is_shared_by_role_and_promotes_legacy_window_data(self):
        host = await self.semantic_host(images=True)
        batch = host.receive(self.event("role-catalog", text="问候")).unwrap()
        context = self.context(host.application, batch)
        legacy_owner = self.semantic.owner(context)
        item = self.semantic.reactions.import_image(legacy_owner, picture(),
            {"name": "挥手", "description": "友好问候", "intents": "问候"})
        animated = self.semantic.reactions.import_image(legacy_owner, animation(),
            {"name": "挥手动画", "description": "动画问候", "intents": "问候"})
        offer = {"kind": "reaction", "items": [{"id": item["id"], "revision": item["revision"]}]}
        self.semantic.store.offer(batch, "legacy-reaction-call", legacy_owner, offer)

        other_scope = replace(context.scope, native_scope_id="native-other",
                              conversation_id="conversation-other", session_id="session-other")
        other_window = replace(context,
            role_binding=replace(context.role_binding, scope=other_scope),
            audience=replace(context.audience, scope=other_scope))
        owner = self.semantic.ensure_reaction_catalog(other_window)
        self.assertEqual(owner, self.semantic.reaction_owner(context))
        self.assertEqual(self.semantic.store.get(owner, "reaction", item["id"])["id"], item["id"])
        self.assertEqual(self.semantic.store.get(legacy_owner, "reaction", item["id"])["id"], item["id"])
        moved_animation = self.semantic.store.get(owner, "reaction", animated["id"])
        self.assertEqual(len(self.semantic.reactions.captured(moved_animation).frames), 2)
        self.assertEqual(dict(self.semantic.store.offers(batch, owner))["legacy-reaction-call"], offer)

        other_role = replace(context.role_binding.role, subject_id="persona-other")
        different_role = replace(other_window,
            role_binding=replace(other_window.role_binding, role=other_role))
        self.assertNotEqual(owner, self.semantic.reaction_owner(different_role))

    async def test_unclaimed_v1_reaction_catalog_is_copied_when_one_role_is_configured(self):
        host = await self.semantic_host(images=True, automatic_reactions=False, reaction_role_count=1)
        batch = host.receive(self.event("single-role-v1-catalog", text="问候")).unwrap()
        context = self.context(host.application, batch)
        source_owner = "unclaimed-v1-reactions"
        imported = self.semantic.reactions.import_image(source_owner, picture(),
            {"name": "历史表情", "description": "V1 迁入素材"}, collection_source="legacy_migration")
        manual = self.semantic.reactions.import_image("unclaimed-manual-reactions", animation(),
            {"name": "独立手动素材", "description": "不应跨 owner 合并"}, collection_source="manual")

        owner = self.semantic.ensure_reaction_catalog(context)

        self.assertEqual(self.semantic.store.get(owner, "reaction", imported["id"])["id"], imported["id"])
        self.assertEqual(self.semantic.store.get(source_owner, "reaction", imported["id"])["id"],
                         imported["id"], "recovery keeps the original catalog intact")
        with self.assertRaises(TaskError):
            self.semantic.store.get(owner, "reaction", manual["id"])
        with self.semantic.store.db() as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM reaction_owner_migrations WHERE source_owner=? AND target_owner=?",
                                             (source_owner, owner)).fetchone())

    async def test_unclaimed_v1_reaction_catalog_is_not_merged_when_multiple_roles_are_configured(self):
        host = await self.semantic_host(images=True, automatic_reactions=False, reaction_role_count=2)
        batch = host.receive(self.event("multi-role-v1-catalog", text="问候")).unwrap()
        context = self.context(host.application, batch)
        source_owner = "unclaimed-v1-reactions"
        imported = self.semantic.reactions.import_image(source_owner, picture(),
            {"name": "其他角色表情", "description": "V1 迁入素材"}, collection_source="legacy_migration")

        owner = self.semantic.ensure_reaction_catalog(context)

        with self.assertRaises(TaskError):
            self.semantic.store.get(owner, "reaction", imported["id"])
        self.assertEqual(self.semantic.store.get(source_owner, "reaction", imported["id"])["id"],
                         imported["id"])

    async def test_feedback_stays_on_latest_delivery_and_changes_selection(self):
        host = await self.semantic_host(images=True)
        app = host.application
        batch = host.receive(self.event("feedback-ranking", text="有点难过")).unwrap()
        context = self.context(app, batch)
        owner = self.semantic.owner(context)
        catalog_owner = self.semantic.ensure_reaction_catalog(context)
        first = self.semantic.reactions.import_image(catalog_owner, picture(),
            {"name": "抱抱", "description": "温柔安慰", "intents": "安慰"})
        alternative_bytes = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(alternative_bytes, format="PNG")
        second = self.semantic.reactions.import_image(catalog_owner, inspect_image(alternative_bytes.getvalue(), 0),
            {"name": "抱抱", "description": "温柔安慰", "intents": "安慰"})
        actor = self.semantic.reaction_preferences.actor(context)
        at = self.clock.now().timestamp()
        with self.semantic.store.db() as db:
            db.execute("INSERT INTO reaction_deliveries VALUES (?,?,?,?,?,NULL,NULL)",
                       (owner, actor, "old", second["id"], at - 20))
            db.execute("INSERT INTO reaction_deliveries VALUES (?,?,?,?,?,NULL,NULL)",
                       (owner, actor, "new", first["id"], at - 10))
        preferences = self.semantic.reaction_preferences
        preferences._observe(owner, actor, "negative-feedback", "刚才那张表情包不合适", at)
        preferences._observe(owner, actor, "repeat-feedback", "这张表情包不错", at + 1)
        with self.semantic.store.db() as db:
            rows = db.execute("SELECT id,feedback_signal FROM reaction_deliveries ORDER BY delivered_at").fetchall()
        self.assertEqual([(row["id"], row["feedback_signal"]) for row in rows],
                         [(second["id"], None), (first["id"], "negative")])
        self.assertEqual(preferences.scores(context), {first["id"]: -1})

        async def same_candidates(*_args, **_kwargs):
            return {"items": [{"id": first["id"], "score": 0.9},
                              {"id": second["id"], "score": 0.85}], "mode": "test"}

        self.semantic.search.search = same_candidates
        self.assertEqual((await self.semantic.reactions.search(context, batch, "ranked", "安慰"))
                         ["items"][0]["id"], second["id"])

    async def test_feedback_after_the_association_window_is_not_learned(self):
        host = await self.semantic_host(images=True)
        batch = host.receive(self.event("feedback-expired", text="有点难过")).unwrap()
        context = self.context(host.application, batch)
        owner = self.semantic.owner(context)
        catalog_owner = self.semantic.ensure_reaction_catalog(context)
        item = self.semantic.reactions.import_image(catalog_owner, picture(),
            {"name": "抱抱", "description": "温柔安慰", "intents": "安慰"})
        actor = self.semantic.reaction_preferences.actor(context)
        delivered_at = self.clock.now().timestamp()
        with self.semantic.store.db() as db:
            db.execute("INSERT INTO reaction_deliveries VALUES (?,?,?,?,?,NULL,NULL)",
                       (owner, actor, "expired-delivery", item["id"], delivered_at))
        from companion_v2.expression.reaction_preferences import REACTION_FEEDBACK_WINDOW_SECONDS
        self.semantic.reaction_preferences._observe(owner, actor, "expired-feedback",
            "刚才那张表情包不合适", delivered_at + REACTION_FEEDBACK_WINDOW_SECONDS + 1)
        self.assertEqual(self.semantic.reaction_preferences.scores(context), {})

    async def test_confirmed_reaction_is_deprioritized_on_the_next_reply(self):
        async def respond(envelope):
            if not envelope.tool_history:
                return requested(call("react", "reaction.search", intent="安慰"))
            image = envelope.original.images[0]
            return replace(answered("抱抱。"), draft=ImageReplyDraft("抱抱。", (), images=(image.source,)))

        self.model_behavior = respond
        host = await self.semantic_host(images=True)
        app = host.application
        first = host.receive(self.event("first-reaction", text="有些难过")).unwrap()
        context = self.context(app, first)
        owner = self.semantic.ensure_reaction_catalog(context)
        alternative_bytes = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(alternative_bytes, format="PNG")
        alternative = inspect_image(alternative_bytes.getvalue(), 0)
        for image in (picture(), alternative):
            self.semantic.reactions.import_image(owner, image,
                {"name": "抱抱", "description": "温柔安慰", "intents": "安慰"})
        self.advance(app)
        self.assertEqual((await self.done(app, first))["state"], "complete")
        initial_hash = self.sent[-1].images[0].descriptor.content_hash
        second = host.receive(self.event("second-reaction", text="还是有点难过")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, second))["state"], "complete")
        self.assertNotEqual(self.sent[-1].images[0].descriptor.content_hash, initial_hash)
        with self.semantic.store.db() as db:
            usage = db.execute("SELECT id,batch FROM uses ORDER BY batch").fetchall()
        self.assertEqual({row["batch"] for row in usage}, {first, second})

    async def test_late_reaction_receipt_accounts_once_after_catalog_revision(self):
        async def respond(envelope):
            if not envelope.tool_history:
                return requested(call("react", "reaction.search", intent="安慰"))
            image = envelope.original.images[0]
            return replace(answered("抱抱你。"), draft=ImageReplyDraft("抱抱你。", (), images=(image.source,)))

        async def accepted(envelope):
            return self.receipt(envelope, "accepted" if getattr(envelope, "images", ()) else "delivered")

        self.model_behavior, self.send_behavior = respond, accepted
        host = await self.semantic_host(images=True)
        app = host.application
        batch = host.receive(self.event("late-reaction", text="难过")).unwrap()
        context = self.context(app, batch)
        scope_owner = self.semantic.owner(context)
        owner = self.semantic.ensure_reaction_catalog(context)
        item = self.semantic.reactions.import_image(owner, picture(),
            {"name": "抱抱", "description": "温柔安慰", "intents": "安慰"})
        self.advance(app)
        self.assertEqual((await self.done(app, batch))["state"], "blocked")
        with self.semantic.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM uses").fetchone()[0], 0)
        delivered_at = self.clock.now()
        self.clock.advance(timedelta(seconds=1))
        self.semantic.reaction_preferences._observe(scope_owner, self.semantic.reaction_preferences.actor(context),
            "late-feedback", "刚才那张表情包不合适", self.clock.now().timestamp())
        self.assertEqual(self.semantic.reaction_preferences.scores(context), {})
        self.semantic.store.put(owner, "reaction", item["id"],
            {**item["payload"], "description": "更新后的说明"}, expected=item["revision"])
        self.receipt_now = lambda: delivered_at
        receipt = self.receipt(self.sent[-1])
        await app.close()
        restarted = self.host(runtime=self.open_runtime())
        self.semantic = restarted.application.attach_semantics(self.port)
        await self.start(restarted, 1, 1)
        app = restarted.application
        context = self.context(app, batch)
        self.assertEqual(app.record_receipt(receipt).unwrap(), "delivered")
        self.assertEqual(app.record_receipt(receipt).unwrap(), "delivered")
        app.reconcile_delivery(context, receipt.part_id).unwrap()
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        with self.semantic.store.db() as db:
            usage = db.execute("SELECT id,batch FROM uses").fetchall()
        self.assertEqual([(row["id"], row["batch"]) for row in usage], [(item["id"], batch)])
        self.assertEqual(self.semantic.reaction_preferences.scores(context), {item["id"]: -1})
        self.assertNotIn("reaction_usage", self.semantic.errors)

    async def test_no_provider_keeps_lexical_reactions_and_truthful_state(self):
        host = await self.semantic_host(images=True)
        batch = host.receive(self.event("lookup")).unwrap()
        context = self.context(host.application, batch)
        owner = self.semantic.ensure_reaction_catalog(context)
        self.semantic.reactions.import_image(owner, picture(), {"name": "抱抱", "description": "安慰"})
        self.port.failing = True
        result = await self.semantic.search.search(owner, "reaction", "抱抱", context=context)
        self.assertEqual(result["mode"], "lexical")
        self.assertEqual(result["fallback_reason"], "RuntimeError")
        self.assertEqual(len(result["items"]), 1)
        closed = await host.application.close()
        self.assertEqual(closed.pending_handles, ())

    async def test_animated_reaction_retains_original_frames_and_delivers_gif(self):
        async def respond(envelope):
            if not envelope.tool_history:
                return requested(call("react", "reaction.search", intent="回应问候"))
            selected = envelope.original.images
            self.assertEqual(len(selected), 1)
            self.assertIsInstance(selected[0], PromptAnimation)
            self.assertEqual(len(selected[0].frames), 2)
            return replace(answered("你好。"), draft=ImageReplyDraft("你好。", (), images=(selected[0].source,)))
        self.model_behavior = respond
        host = await self.semantic_host(images=True)
        app = host.application
        batch = host.receive(self.event("animated", text="你好呀")).unwrap()
        context = self.context(app, batch)
        original = animation()
        self.assertIsInstance(original, CapturedAnimation)
        imported = self.semantic.reactions.import_image(self.semantic.owner(context), original, {"name": "挥手"})
        analyzed = await self.semantic.reactions.analyze(self.semantic.owner(context), imported["id"])
        retained = self.semantic.reactions.captured(analyzed)
        self.assertEqual(retained.content, original.content)
        self.assertEqual([f.content for f in retained.frames], [f.content for f in original.frames])
        self.assertEqual(len(self.port.analysis_images[-1]), 2)
        self.advance(app)
        state = await self.done(app, batch)
        self.assertEqual(state["state"], "complete", dict(state))
        self.assertEqual(self.sent[-1].images[0].descriptor.mime_type, "image/gif")
        self.assertEqual(self.sent[-1].images[0].descriptor.content_hash, original.descriptor.content_hash)

    async def test_rerank_reorders_only_current_page_and_keeps_fallback_order(self):
        host = await self.semantic_host()
        batch = host.receive(self.event("rerank")).unwrap()
        context = self.context(host.application, batch)
        self.semantic.store.configure({"rerank_provider": "ranker"}, 0)
        payload = {"items": [{"id": "first", "evidence": ["a"]}, {"id": "second", "evidence": ["b"]}],
                   "next_cursor": "opaque", "mode": "hybrid"}
        self.port.rerank_scores = [0.1, 0.9]
        ranked = await self.semantic.memory.rerank(context, "相关经历", payload)
        self.assertEqual([item["id"] for item in ranked["items"]], ["second", "first"])
        self.assertEqual(ranked["next_cursor"], payload["next_cursor"])
        self.assertEqual(ranked["items"][0]["evidence"], ["b"])
        self.port.rerank_failing = True
        fallback = await self.semantic.memory.rerank(context, "相关经历", payload)
        self.assertEqual(fallback["items"], payload["items"])
        self.assertEqual(fallback["rerank_state"], "unavailable_kept_retrieval_order")

    async def test_timeout_decoupling_vision_review_fallback_and_consecutive_timeout_warning(self):
        from unittest.mock import patch

        class RecordingPort(SemanticPort):
            def __init__(self):
                super().__init__()
                self.analyze_calls = []
                self.timeout_providers = set()

            async def analyze(self, identifier, prompt, images=(), *, max_tokens=1400):
                self.analyze_calls.append({
                    "identifier": identifier,
                    "max_tokens": max_tokens,
                    "is_review": "判断候选表情是否适合" in prompt,
                })
                if identifier in self.timeout_providers:
                    raise TimeoutError("vision review timed out")
                if "判断图片是否适合作为独立的聊天表情素材" in prompt:
                    return {"suitable": True, "name": "开心", "description": "笑脸",
                            "visible_text": "", "tags": "开心", "emotions": "愉快", "intents": "打招呼"}
                if "为聊天表情建立元数据" in prompt:
                    return {"name": "开心", "description": "笑脸", "intents": "打招呼"}
                return {"fit": True, "reason": "通过"}

        host = await self.semantic_host(images=True, automatic_reactions=False)
        recording = RecordingPort()
        self.semantic.gateway.port = recording
        settings = self.semantic.store.settings()
        self.semantic.store.configure({
            "timeout_seconds": 6,
            "analysis_timeout_seconds": 42,
            "analysis_provider": "main-analysis",
            "vision_review_provider": "fast-review",
            "vision_review_fallback_provider": "backup-review",
            "vision_review_timeout_seconds": 14,
            "vision_review": True,
        }, settings["revision"])

        timeouts_seen = []
        real_wait_for = asyncio.wait_for

        async def spy_wait_for(coro, timeout=None):
            timeouts_seen.append(timeout)
            return await real_wait_for(coro, timeout)

        # collect_candidate uses analysis_provider + analysis_timeout_seconds (42), not timeout_seconds (6).
        with patch("companion_v2.expression.reactions.asyncio.wait_for", side_effect=spy_wait_for):
            status, item = await self.semantic.reactions.collect_candidate(
                "decouple-owner", picture(), "private_capture", lambda: None,
            )
            self.assertEqual(status, "added")
            self.assertEqual(recording.analyze_calls[-1]["identifier"], "main-analysis")
            self.assertEqual(timeouts_seen[-1], 42)

            # analyze() also uses analysis_timeout_seconds (42).
            await self.semantic.reactions.analyze("decouple-owner", item["id"])
            self.assertEqual(recording.analyze_calls[-1]["identifier"], "main-analysis")
            self.assertEqual(timeouts_seen[-1], 42)

            # _review() uses vision_review_provider, fails over to vision_review_fallback_provider,
            # enforces vision_review_timeout_seconds (14), and caps max_tokens=256.
            review_settings = SimpleNamespace(vision_verify_mode="always")
            candidate = {"id": item["id"], "basis": "embedding"}
            recording.timeout_providers.add("fast-review")
            review_res = await self.semantic.reactions._review(
                None, review_settings, candidate, item, "打招呼",
            )
            self.assertEqual(review_res["state"], "passed")
            self.assertEqual(recording.analyze_calls[-2]["identifier"], "fast-review")
            self.assertEqual(recording.analyze_calls[-2]["max_tokens"], 256)
            self.assertEqual(recording.analyze_calls[-1]["identifier"], "backup-review")
            self.assertEqual(recording.analyze_calls[-1]["max_tokens"], 256)
            self.assertEqual(timeouts_seen[-2:], [14, 14])

        # 3 consecutive vision review timeouts emit a structured warning.
        recording.timeout_providers.add("backup-review")
        with patch("companion_v2.expression.reactions.logger.warning") as warn_log:
            for _ in range(3):
                res = await self.semantic.reactions._review(
                    None, review_settings, candidate, item, "打招呼",
                )
                self.assertEqual(res["state"], "unavailable")
            self.assertGreaterEqual(warn_log.call_count, 1)
            self.assertTrue(any("连续超时" in str(call.args[0]) for call in warn_log.call_args_list))
            self.assertEqual(self.semantic.reactions._vision_review_consecutive_timeouts, 3)

        # Recovery resets the consecutive timeout counter.
        recording.timeout_providers.clear()
        ok_res = await self.semantic.reactions._review(
            None, review_settings, candidate, item, "打招呼",
        )
        self.assertEqual(ok_res["state"], "passed")
        self.assertEqual(self.semantic.reactions._vision_review_consecutive_timeouts, 0)

        # When wired to NativeSemanticModels (which accepts fallback_identifier & timeout_seconds),
        # _call_analyze delegates directly so default_analysis == fallback deduplicates by identity.
        from unittest.mock import AsyncMock
        from astrbot_host.semantic_models import NativeSemanticModels

        default_vision = SimpleNamespace(
            provider_config={"id": "default-vision", "modalities": ["text", "image"]},
            text_chat=AsyncMock(side_effect=TimeoutError("timed out")),
        )
        backup_vision = SimpleNamespace(
            provider_config={"id": "backup-vision", "modalities": ["text", "image"]},
            text_chat=AsyncMock(return_value=SimpleNamespace(completion_text='{"fit":true,"reason":"ok"}')),
        )
        native_port = NativeSemanticModels(
            SimpleNamespace(
                get_provider_by_id=lambda ident: {
                    "default-vision": default_vision, "backup-vision": backup_vision,
                }.get(ident),
                get_all_providers=lambda: [default_vision, backup_vision],
            ),
            default_analysis="default-vision",
        )
        self.semantic.gateway.port = native_port
        cur = self.semantic.store.settings()
        self.semantic.store.configure({
            "analysis_provider": "",
            "vision_review_provider": "",
            "vision_review_fallback_provider": "default-vision",
            "vision_review_timeout_seconds": 12,
        }, cur["revision"])
        dedup_res = await self.semantic.reactions._review(
            None, review_settings, candidate, item, "打招呼",
        )
        self.assertEqual(dedup_res["state"], "unavailable")
        default_vision.text_chat.assert_awaited_once()

        # Switching fallback to a distinct provider fails over cleanly through NativeSemanticModels.
        default_vision.text_chat.reset_mock()
        cur = self.semantic.store.settings()
        self.semantic.store.configure({
            "vision_review_fallback_provider": "backup-vision",
        }, cur["revision"])
        failover_res = await self.semantic.reactions._review(
            None, review_settings, candidate, item, "打招呼",
        )
        self.assertEqual(failover_res["state"], "passed")
        default_vision.text_chat.assert_awaited_once()
        backup_vision.text_chat.assert_awaited_once()
        self.assertEqual(backup_vision.text_chat.await_args.kwargs["max_tokens"], 256)

    async def test_reaction_collection_circuit_breaker_backoff_and_recovery(self):
        from unittest.mock import patch

        host = await self.semantic_host(images=True, automatic_reactions=False)
        app = host.application
        batch = host.receive(self.event("breaker-ctx")).unwrap()
        context = self.context(app, batch)
        settings = self.semantic.store.settings()
        self.semantic.store.configure({
            "reactions_collection_private_enabled": True,
            "reactions_collection_private_probability": 1.0,
        }, settings["revision"])

        captured = picture()
        direct_source = object()
        prompt_image = SimpleNamespace(source=direct_source)
        app.messages.images.read = lambda current, events, at: (prompt_image,)
        self.semantic.reactions.capture_prompt_image = lambda image: captured

        def make_request():
            return SimpleNamespace(
                event=SimpleNamespace(images=(captured.descriptor,)),
                context=SimpleNamespace(materials=(SimpleNamespace(
                    purpose="input_image:current", source=direct_source,
                ),)),
            )

        async def always_timeout(*args, **kwargs):
            raise TimeoutError("upstream timeout")

        with patch.object(self.port, "analyze", side_effect=always_timeout), \
             patch("companion_v2.expression.semantic_service.logger.warning") as warn_log:
            for idx in range(5):
                self.assertTrue(self.semantic.reaction_collection_allowed(context, f"turn-{idx}"))
                self.semantic.schedule_reaction_collection(f"breaker-fail-{idx}", context, make_request())
                pending = [t for k, t in self.semantic.background.items() if k.startswith("reaction-capture:")]
                await asyncio.gather(*pending)
            self.assertGreaterEqual(warn_log.call_count, 5)

        # After 5 consecutive failures, circuit opens for 5 minutes (300s) and blocks scheduling.
        status_open = self.semantic.status()
        circuit = status_open["reaction_collection_circuit"]
        self.assertTrue(circuit["open"])
        self.assertEqual(circuit["consecutive_failures"], 5)
        self.assertEqual(circuit["cooldown_remaining_seconds"], 300)
        self.assertEqual(circuit["reason"], "TimeoutError")
        self.assertFalse(self.semantic.reaction_collection_allowed(context, "turn-blocked"))
        self.assertEqual(self.semantic.errors.get("reaction_collection"), "reaction_collection_circuit_open")

        # After 300s, circuit closes again; another failure triggers exponential backoff (600s).
        self.clock.advance(timedelta(seconds=301))
        self.assertTrue(self.semantic.reaction_collection_allowed(context, "turn-after-300s"))
        with patch.object(self.port, "analyze", side_effect=always_timeout):
            self.semantic.schedule_reaction_collection("breaker-fail-6", context, make_request())
            pending = [t for k, t in self.semantic.background.items() if k.startswith("reaction-capture:")]
            await asyncio.gather(*pending)
        circuit_second = self.semantic.status()["reaction_collection_circuit"]
        self.assertTrue(circuit_second["open"])
        self.assertEqual(circuit_second["consecutive_failures"], 6)
        self.assertEqual(circuit_second["cooldown_remaining_seconds"], 600)

        # Manual reset_collection_failures clears breaker and failed runs.
        owner = self.semantic.ensure_reaction_catalog(context)
        reset_result = self.semantic.reset_collection_failures(owner)
        self.assertFalse(reset_result["circuit"]["open"])
        self.assertEqual(reset_result["circuit"]["consecutive_failures"], 0)
        self.assertGreaterEqual(reset_result["cleared_runs"], 6)
        self.assertTrue(self.semantic.reaction_collection_allowed(context, "turn-after-reset"))
        self.assertNotIn("reaction_collection", self.semantic.errors)

        # Re-open breaker and verify configure() also clears it immediately.
        self.semantic._reaction_collection_failures = 5
        self.semantic._reaction_collection_cooldown_until = self.clock.now().timestamp() + 300
        self.assertFalse(self.semantic.reaction_collection_allowed(context, "turn-blocked-2"))
        cur = self.semantic.store.settings()
        await self.semantic.configure({"analysis_timeout_seconds": 35}, cur["revision"])
        self.assertTrue(self.semantic.reaction_collection_allowed(context, "turn-after-cfg"))
        self.assertFalse(self.semantic.status()["reaction_collection_circuit"]["open"])
