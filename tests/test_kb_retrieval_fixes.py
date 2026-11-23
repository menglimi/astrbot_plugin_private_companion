"""Regression tests for V2 knowledge-base retrieval fixes on the AstrBot host boundary."""

from __future__ import annotations

from tests import astrbot_data_root  # noqa: F401 - isolate SDK data root before import

import json
from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from astrbot.core.agent.message import TextPart
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.provider.entities import ProviderRequest

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA, prepare_core_reply_event
from astrbot_host.core_reply_mentions import (
    BARE_MENTION_SENTINEL_PROMPT,
    prepare_bare_mention_request,
    restore_group_mentions,
)
from astrbot_host.core_reply_perception import (
    inject_core_reply_perception_context,
    request_has_host_kb_results,
)
from astrbot_host.core_reply_telemetry import _request_signature
from astrbot_host.core_reply_tools import (
    KB_TOOL_INSTRUCTION,
    LEAN_PRESERVED_HOST_TOOLS,
    close_core_reply_tools,
    inject_core_reply_tools,
)
from companion_v2.conversation.group_timing import SELF_MENTION
from companion_v2.conversation.tool_loop import LocalRoleTools
from companion_v2.execution.models import TaskContext, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind
from companion_v2.identity_policy.group_audience import group_audience
from companion_v2.identity_policy.personas import ConversationRoute
from tests.test_astrbot_core_reply import FakeEvent
from tests.test_conversation_messages import MessageFixture
from tests.test_management_access import NOW, SCOPE


async def _dummy_handler(_ctx, **_kwargs):
    return "ok"


class LeanWhitelistAndKbInstructionTests(MessageFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.group_scope = replace(
            SCOPE, kind=ScopeKind.GROUP, native_scope_id="group-kb-1",
            conversation_id="group-kb-conv", session_id="group-kb-sess",
        )
        self.personas.register_route(ConversationRoute(self.group_scope, group_id="group-kb-1"), observed_at=NOW)
        for actor in (self.core, self.standard):
            for action in ("text", "conversation.reply", "configuration.read",
                           "group.memory.read", "group.memory.write"):
                self.grant(actor, self.group_scope, action)
        role = self.identity.resolve_role(self.group_scope, observed_at=NOW).unwrap()
        self.admin_ctx = TaskContext(self.core, role, group_audience(self.group_scope, self.core.subject))
        self.member_ctx = TaskContext(self.standard, role, group_audience(self.group_scope, self.standard.subject))
        self.tools = LocalRoleTools(self.messages)
        self.app = NS(
            runtime=self.runtime, tasks=self.tasks, messages=self.messages, tools=self.tools,
            require_running=lambda: require(self.tasks.check_generation()),
        )
        self.addAsyncCleanup(close_core_reply_tools, self.app)

    def _inject(self, context, host_tools, *, profile="lean", event_id="evt-1"):
        event = FakeEvent()

        async def authorize(_prepared):
            return Outcome.success(True)

        prepared = {"context": context, "event_id": event_id, "group_tool_profile": profile}
        prepare_core_reply_event(event, prepared, authorize=authorize)
        req = NS(
            model="test-model", system_prompt="base persona", prompt="问一个设定", contexts=[],
            func_tool=ToolSet(tools=list(host_tools)) if host_tools is not None else None,
            extra_user_content_parts=[],
        )
        inject_core_reply_tools(event, req, application=self.app)
        return event, req

    def test_lean_group_mode_preserves_astr_kb_search_and_drops_other_host_tools(self):
        kb_tool = FunctionTool("astr_kb_search", "Query KB", {"type": "object"}, _dummy_handler)
        shell_tool = FunctionTool("astrbot_execute_shell", "Shell", {"type": "object"}, _dummy_handler)
        search_tool = FunctionTool("web_search_tavily", "Web", {"type": "object"}, _dummy_handler)

        _, req_admin = self._inject(self.admin_ctx, [shell_tool, kb_tool, search_tool], profile="lean", event_id="a1")
        _, req_member = self._inject(self.member_ctx, [search_tool, kb_tool, shell_tool], profile="lean", event_id="m1")

        admin_names = [tool.name for tool in req_admin.func_tool.tools]
        member_names = [tool.name for tool in req_member.func_tool.tools]
        self.assertIn("astr_kb_search", admin_names)
        self.assertNotIn("astrbot_execute_shell", admin_names)
        self.assertNotIn("web_search_tavily", admin_names)
        self.assertEqual(admin_names, member_names)
        self.assertEqual(_request_signature(req_admin)["tools_hash"], _request_signature(req_member)["tools_hash"])
        self.assertEqual(req_admin.system_prompt, req_member.system_prompt)
        self.assertIn(KB_TOOL_INSTRUCTION, req_admin.system_prompt)
        self.assertIn("astr_kb_search", LEAN_PRESERVED_HOST_TOOLS)

    def test_kb_tool_instruction_is_idempotent_and_omitted_without_kb_tool(self):
        kb_tool = FunctionTool("astr_kb_search", "Query KB", {"type": "object"}, _dummy_handler)
        event_with, req_with = self._inject(self.admin_ctx, [kb_tool], profile="full", event_id="with-kb")
        inject_core_reply_tools(event_with, req_with, application=self.app)
        self.assertEqual(req_with.system_prompt.count(KB_TOOL_INSTRUCTION), 1)

        _, req_without = self._inject(self.admin_ctx, [], profile="full", event_id="without-kb")
        self.assertNotIn(KB_TOOL_INSTRUCTION, req_without.system_prompt)


class BareMentionAndDoubleInjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_bare_mention_keeps_event_message_clean_and_restores_prompt_post_kb(self):
        event = FakeEvent()
        event.message_str = ""
        event.message_obj = NS(message=[], message_str="")
        event.unified_msg_origin = "onebot:GroupMessage:group-1"
        event.get_platform_id = lambda: "onebot"

        conv = NS(cid="conv-123", history="[]")
        conv_mgr = NS(
            get_curr_conversation_id=AsyncMock(return_value="conv-123"),
            get_conversation=AsyncMock(return_value=conv),
            new_conversation=AsyncMock(return_value="conv-new"),
        )
        native_ctx = NS(conversation_manager=conv_mgr, get_config=lambda **_kw: {"provider_settings": {}})

        admitted = await prepare_bare_mention_request(event, native_context=native_ctx)
        self.assertTrue(admitted)
        self.assertEqual(event.message_str, "")
        self.assertEqual(event.message_obj.message_str, "")
        self.assertIs(event.get_extra("enable_streaming"), False)

        req = event.get_extra("provider_request")
        self.assertIsInstance(req, ProviderRequest)
        self.assertEqual(req.prompt, BARE_MENTION_SENTINEL_PROMPT)
        self.assertEqual(req.prompt.strip(), "")
        self.assertIs(req.conversation, conv)

        # Simulate OnLLMRequestEvent running AFTER AstrBot's _apply_kb().
        prepared = {
            "group_trigger": {"mentioned": True, "bare_mention": True},
            "native_input": NS(text="", group_message_text=SELF_MENTION),
            "prompt_instruction": "背景",
            "prompt_context_instruction": "背景",
            "prompt_context_blocks": (),
        }
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {"prepared": prepared})
        await inject_core_reply_perception_context(event, req, native_context=native_ctx)
        self.assertEqual(req.prompt, SELF_MENTION)

    async def test_bare_mention_without_conversation_degrades_without_unbound_request(self):
        event = FakeEvent()
        event.message_str = ""
        event.message_obj = NS(message=[], message_str="")
        event.unified_msg_origin = "onebot:GroupMessage:group-2"
        event.get_platform_id = lambda: "onebot"

        # No conversation_manager -> returns False, disables streaming, does NOT attach conversation=None request.
        admitted = await prepare_bare_mention_request(event, native_context=None)
        self.assertFalse(admitted)
        self.assertIsNone(event.get_extra("provider_request"))
        self.assertIs(event.get_extra("enable_streaming"), False)
        self.assertEqual(event.message_str, "")

    def test_bare_mention_restore_handles_configured_prompt_prefix_template(self):
        event = FakeEvent()
        event.message_obj = NS(message=[])
        event.unified_msg_origin = "onebot:GroupMessage:group-1"
        native_ctx = NS(get_config=lambda **_kw: {"provider_settings": {"prompt_prefix": "[群聊] {{prompt}}"}})
        req = NS(prompt="[群聊]  ")
        restore_group_mentions(event, req, NS(text="", group_message_text=SELF_MENTION), native_ctx)
        self.assertEqual(req.prompt, f"[群聊] {SELF_MENTION}")

    async def test_double_injection_skips_official_kb_when_host_already_injected(self):
        official_section = {
            "title": "角色知识资料",
            "instruction": "知识说明",
            "materials": [
                {"kind": "knowledge_reference:worldbook-local", "content": "本地世界书条目"},
                {"kind": "knowledge_reference:astrbot:kb-1:doc-1:0", "content": "官方知识库重复内容"},
            ],
        }
        memory_section = {
            "title": "当前私聊记忆",
            "instruction": "记忆说明",
            "materials": [{"kind": "memory_summary", "content": "用户喜欢雨天"}],
        }
        prepared = {
            "prompt_instruction": "基础背景",
            "prompt_context_instruction": "基础背景",
            "prompt_context_blocks": (official_section, memory_section),
        }

        # Case 1: Host already injected [Related Knowledge Base Results]:
        event_dedup = FakeEvent()
        event_dedup.set_extra(CORE_REPLY_CONTEXT_EXTRA, {"prepared": prepared})
        req_with_host_kb = ProviderRequest(
            prompt="问一下设定",
            extra_user_content_parts=[
                TextPart(text="[Related Knowledge Base Results]:\n【知识 1】官方知识库内容").mark_as_temp(),
            ],
        )
        self.assertTrue(request_has_host_kb_results(req_with_host_kb))
        await inject_core_reply_perception_context(event_dedup, req_with_host_kb)
        v2_part = next(
            part.text for part in req_with_host_kb.extra_user_content_parts
            if part.text.startswith("<companion_v2_context>")
        )
        payload = json.loads(v2_part.removeprefix("<companion_v2_context>\n").removesuffix("\n</companion_v2_context>"))
        sections = payload["companion_background"]["sections"]
        all_kinds = [mat["kind"] for sec in sections for mat in sec.get("materials", ())]
        self.assertIn("knowledge_reference:worldbook-local", all_kinds)
        self.assertIn("memory_summary", all_kinds)
        self.assertNotIn("knowledge_reference:astrbot:kb-1:doc-1:0", all_kinds)

        # Case 2: Host did NOT inject KB results -> official KB section is kept.
        event_keep = FakeEvent()
        event_keep.set_extra(CORE_REPLY_CONTEXT_EXTRA, {"prepared": prepared})
        req_without_host_kb = ProviderRequest(prompt="问一下设定", extra_user_content_parts=[])
        self.assertFalse(request_has_host_kb_results(req_without_host_kb))
        await inject_core_reply_perception_context(event_keep, req_without_host_kb)
        v2_part_keep = next(
            part.text for part in req_without_host_kb.extra_user_content_parts
            if part.text.startswith("<companion_v2_context>")
        )
        payload_keep = json.loads(
            v2_part_keep.removeprefix("<companion_v2_context>\n").removesuffix("\n</companion_v2_context>")
        )
        kept_kinds = [
            mat["kind"] for sec in payload_keep["companion_background"]["sections"]
            for mat in sec.get("materials", ())
        ]
        self.assertIn("knowledge_reference:astrbot:kb-1:doc-1:0", kept_kinds)

    async def test_external_stt_populates_retrieval_query_when_supported(self):
        event = FakeEvent()
        event.set_extra("_gemini_stt_transcript", "请问冬马和纱在维也纳的音乐会是哪一章")
        event.set_extra("_gemini_stt_source", "hear_voice")
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {
            "prepared": {
                "prompt_instruction": "",
                "prompt_context_instruction": "",
                "prompt_context_blocks": (),
            }
        })
        req = NS(prompt="", retrieval_query=None, extra_user_content_parts=[], contexts=[])
        await inject_core_reply_perception_context(event, req)
        self.assertEqual(req.retrieval_query, "请问冬马和纱在维也纳的音乐会是哪一章")

    async def test_together_room_background_uses_async_official_kb_hits(self):
        from unittest.mock import patch
        from astrbot_host.native_extensions.together import native_context as together_ctx
        from companion_v2.knowledge.retrieval import KnowledgeCandidate

        fake_hit = KnowledgeCandidate(
            "astrbot:kb-1:doc-1:0", "维也纳音乐会剧情摘要",
            knowledge_base_id="kb-1", document_id="doc-1", document_name="剧情.md", chunk_index=0,
        )
        resolved = NS(session=NS(), application=NS(), context=NS())
        runtime = NS(enable_memory_context=True, controller=NS(now=lambda: NOW))
        observed = {}

        def fake_build(application, context, native_input, **kwargs):
            observed["text"] = native_input.text
            observed["official_knowledge_hits"] = kwargs.get("official_knowledge_hits")
            observed["include_official_knowledge"] = kwargs.get("include_official_knowledge")
            return NS(instruction="rendered-room-bg")

        with patch.object(together_ctx, "resolve_room_context", AsyncMock(return_value=resolved)), \
                patch("astrbot_host.knowledge_sources.prepare_official_knowledge_hits",
                      AsyncMock(return_value=(fake_hit,))) as prep_mock, \
                patch.object(together_ctx, "build_core_reply_prompt", side_effect=fake_build):
            result = await together_ctx.room_background(runtime, NS(), "聊聊维也纳音乐会")

        self.assertEqual(result, "rendered-room-bg")
        prep_mock.assert_awaited_once_with(resolved.application, resolved.context, "聊聊维也纳音乐会", include_official=True)
        self.assertEqual(observed["official_knowledge_hits"], (fake_hit,))
        self.assertIs(observed["include_official_knowledge"], True)

    async def test_knowledge_sources_candidates_has_no_shared_document_names_race(self):
        import sqlite3
        from pathlib import Path
        from tempfile import TemporaryDirectory
        import threading
        from unittest.mock import patch
        from astrbot_host.knowledge_sources import AstrBotKnowledgeSources, KnowledgeSourceReader
        from companion_v2.knowledge.parsing import KnowledgeChunk, ParseBatch

        with TemporaryDirectory(prefix="kb-doc-names-race-") as tmp:
            data_root = Path(tmp)
            kb_dir = data_root / "knowledge_base"
            kb_dir.mkdir(parents=True)
            db_path = kb_dir / "kb.db"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE knowledge_bases (kb_id TEXT, kb_name TEXT)")
            conn.execute("CREATE TABLE kb_documents (kb_id TEXT, doc_id TEXT, doc_name TEXT)")
            conn.execute("INSERT INTO knowledge_bases VALUES ('kb-a', '世界书A'), ('kb-b', '世界书B')")
            conn.execute("INSERT INTO kb_documents VALUES ('kb-a', 'doc-a', '档案A.md'), ('kb-b', 'doc-b', '档案B.md')")
            conn.commit()
            conn.close()

            sources = AstrBotKnowledgeSources(NS(kb_manager=NS(kb_db=NS(db_path=str(db_path)))))
            self.assertFalse(hasattr(sources, "_document_names"))

            barrier = threading.Barrier(2, timeout=5)

            def fake_rows(self_reader, source_id, limit=8):
                barrier.wait()
                kb_id = source_id.split(":", 1)[1]
                doc_id = "doc-a" if kb_id == "kb-a" else "doc-b"
                return [(kb_id, doc_id)]

            def fake_parse(rows, *, knowledge_base_id):
                kb_id, doc_id = rows[0]
                return ParseBatch(
                    items=(KnowledgeChunk(
                        text=f"内容-{kb_id}", knowledge_base_id=kb_id, document_id=doc_id,
                        document_name="", chunk_index=0,
                    ),),
                    issues=(),
                )

            results = {}

            def worker(key, kb_id):
                results[key] = sources.candidates([f"kb:{kb_id}"])

            with patch("astrbot_host.knowledge_sources.available_source_ids", return_value={"kb:kb-a", "kb:kb-b"}), \
                    patch.object(KnowledgeSourceReader, "_knowledge_rows", fake_rows), \
                    patch("astrbot_host.knowledge_sources.parse_knowledge_rows", side_effect=fake_parse):
                t1 = threading.Thread(target=worker, args=("a", "kb-a"))
                t2 = threading.Thread(target=worker, args=("b", "kb-b"))
                t1.start()
                t2.start()
                t1.join(5)
                t2.join(5)

            self.assertEqual(len(results["a"]), 1)
            self.assertEqual(len(results["b"]), 1)
            self.assertEqual(results["a"][0].document_name, "档案A.md")
            self.assertEqual(results["b"][0].document_name, "档案B.md")


if __name__ == "__main__":
    unittest.main()
