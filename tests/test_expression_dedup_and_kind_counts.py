"""Expression learning deduplication, reversible consolidation, and kind-level pagination."""

from datetime import timedelta
import json
import unittest

from companion_v2.execution.models import digest, encode, stamp
from companion_v2.expression import learning, rule_details, rule_support, style_rotation
from companion_v2.expression.style_management import DEDUPE_ACTOR, ExpressionStyleManagement
from companion_v2.expression.style_service import (
    RULE_READ_LIMIT,
    _build_existing_pattern_index,
    _compact_expression_form,
)
from tests import test_expression_v1_pipeline as pipeline
from tests.test_expression_v1_pipeline import ExpressionFixture, RULE, materials
from tests.test_host_application import HostFixture
from tests.test_role_tool_loop import answered


class ExpressionDedupePrimitivesTests(unittest.TestCase):
    def test_compact_expression_form_preserves_concrete_form_keys(self):
        form = {
            "kind": "style",
            "situation": "聊到熟悉话题时",
            "pattern": "懂的都懂",
            "avoid": "对方认真求助时不要敷衍",
        }
        compact = _compact_expression_form(form)
        self.assertEqual(compact, form)

    def test_pattern_normalization_and_duplicate_matching(self):
        self.assertEqual(learning.normalized_pattern_key("[事件]，懂的都懂！"), "懂的都懂")
        self.assertEqual(learning.template_pattern_key("[甲]不是[乙]"), learning.template_pattern_key("[X]不是[Y]"))
        self.assertEqual(learning.template_pattern_key("[X]"), "")

        base = {"kind": "style", "situation": "彼此有共识时", "pattern": "懂的都懂", "avoid": ""}
        placeholder_variant = {
            "kind": "style", "situation": "聊到熟悉的事情并顺口接话", "pattern": "[某事]，懂的都懂！", "avoid": "",
        }
        different_phrase = {"kind": "style", "situation": "彼此有共识时", "pattern": "有内味了", "avoid": ""}
        different_kind = {"kind": "grammar", "situation": "彼此有共识时", "pattern": "懂的都懂", "avoid": ""}
        self.assertTrue(learning.forms_match_duplicate(base, placeholder_variant))
        self.assertFalse(learning.forms_match_duplicate(base, different_phrase))
        self.assertFalse(learning.forms_match_duplicate(base, different_kind))

        tpl_a = {"kind": "style", "situation": "打趣时", "pattern": "[A]不是[B]", "avoid": ""}
        tpl_b = {"kind": "style", "situation": "轻松调侃时", "pattern": "{X}不是{Y}", "avoid": ""}
        self.assertTrue(learning.forms_match_duplicate(tpl_a, tpl_b))

        gram_a = {
            "kind": "grammar",
            "situation": "回应对方进展时",
            "pattern": "先用简短的话确认，再补一句说明",
            "avoid": "",
        }
        gram_b = {
            "kind": "grammar",
            "situation": "听完分享后接话",
            "pattern": "先用简短的话确认，再补充一句说明",
            "avoid": "",
        }
        self.assertTrue(learning.forms_match_duplicate(
            gram_a, gram_b,
            left_handles=("input:h1", "input:h2"),
            right_handles=("input:h2", "input:h3"),
            same_scope=True,
        ))
        self.assertFalse(learning.forms_match_duplicate(
            gram_a, gram_b,
            left_handles=("input:h1",),
            right_handles=("input:h9",),
            same_scope=True,
        ))

    def test_pattern_index_covers_rules_beyond_read_limit_and_omits_when_empty(self):
        self.assertIsNone(_build_existing_pattern_index([], {}, covered_ids=set(), sample_texts=[]))
        rows = [
            {"signal_id": f"sig-{index}", "state": "adopted" if index < 2 else "rejected"}
            for index in range(4)
        ]
        forms = {
            "sig-0": {"form": {"kind": "style", "situation": "s0", "pattern": "已在窗口里", "avoid": ""}},
            "sig-1": {"form": {"kind": "style", "situation": "s1", "pattern": "懂的都懂", "avoid": ""}},
            "sig-2": {"form": {"kind": "style", "situation": "s2", "pattern": "拒绝过的说法", "avoid": ""}},
            "sig-3": {"form": {"kind": "style", "situation": "s3", "pattern": "懂的都懂！", "avoid": ""}},
        }
        index = _build_existing_pattern_index(
            rows, forms, covered_ids={"sig-0"}, sample_texts=["这事真的懂的都懂"],
        )
        self.assertEqual(index, {"active": ["懂的都懂"], "excluded": ["拒绝过的说法"]})


class ExpressionDedupAndManagementTests(ExpressionFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.expression_profiles()
        self.model_behavior = self.expression_model

    def _seed_signal(self, app, context, *, signal_id, run_id, state, form, observation="合成观察",
                     proposal="合成建议", dimension="diction", handles=(), source_kind="shared_pack",
                     offset_seconds=0, injections=0, decided_by=None):
        owner_key = digest(app.expression_samples.role_owner(context))
        scope_key = digest(context.scope)
        created = app.tasks.now() + timedelta(seconds=offset_seconds)
        at = stamp(created)
        window = {
            "opened_at": at, "closed_at": at, "handles": list(handles),
            "characters": 0, "source_kind": source_kind,
        }
        if source_kind == "conversation_input":
            window["context_key"] = context.key
            if handles:
                window["texts"] = {handle: f"样本内容-{handle}" for handle in handles}
        with app.runtime.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO expression_style_runs VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, owner_key, scope_key, encode(window), "hash-" + run_id, "合成批次", "decided", at, at),
            )
            connection.execute("""INSERT INTO expression_style_signals
                (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal,
                 applicability_json, confidence, evidence_json, state, created_at, decided_at,
                 decided_by, decision_key, ordinal)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal,
                 encode(learning.normalize_applicability({"channels": ["private"]})),
                 0.8, encode(list(handles)), state, at, at if state != "pending" else None,
                 decided_by or ("dashboard:test" if state != "pending" else None),
                 "dec:" + signal_id if state != "pending" else None, 0))
            if form is not None:
                rule_details.write(app.runtime, signal_id, form, at=created, actor="learning_model",
                                   operation_key=run_id)
            if injections > 0:
                style_rotation.add_usage(
                    app.runtime, owner_key=owner_key, scope_key=scope_key,
                    signal_id=signal_id, delta=injections, last_injected_at=created,
                    connection=connection,
                )

    async def test_commit_dedup_merges_normalized_active_and_blocks_rejected_resurrection(self):
        host = await self.start()
        app = host.application
        context = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True, "style_review_enabled": False})

        adopted_form = {"kind": "style", "situation": "聊到彼此心照不宣的话题", "pattern": "懂的都懂", "avoid": ""}
        rejected_form = {"kind": "style", "situation": "随便敷衍对方", "pattern": "行吧行吧", "avoid": ""}
        # Seed a previously rejected rule in the same scope family so commit dedupe must suppress it
        self._seed_signal(
            app, context, signal_id="sig-prior-rejected", run_id="run-prior-rejected", state="rejected",
            form=rejected_form, observation="敷衍短语", proposal="随口敷衍", offset_seconds=-120,
        )

        async def first_round(envelope):
            if envelope.original.output_format == "role_expression_learning":
                source = json.loads(materials(envelope, "role_expression_samples")[0].payload)
                handles = [item["handle"] for item in source["samples"][:2]]
                return answered(json.dumps({"summary": "首轮候选", "signals": [
                    {**RULE, "observation": "默契短语", "proposal": "轻松带过",
                     "expression": adopted_form, "evidence_handles": handles},
                ]}, ensure_ascii=False))
            return await self.expression_model(envelope)

        self.model_behavior = first_round
        manager = await self.learn_now(app, context)
        pending = manager.list(operator=self.core, context=context, state="pending").unwrap()["items"]
        self.assertEqual(len(pending), 1)
        manager.command(
            operator=self.core, context=context, signal_id=pending[0]["signal_id"],
            action="adopt", view_token=pending[0]["view_token"], operation_key="adopt-base-form",
        ).unwrap()

        self.configure_expression(app, {"private_learning_enabled": False})
        batch = host.receive(self.event("dedupe-new-msg", text="哈哈这件事懂的都懂，行吧行吧。")).unwrap()
        self.advance(app)
        await self.done(app, batch)
        self.configure_expression(app, {"private_learning_enabled": True})

        seen_payload = {}

        async def second_round(envelope):
            if envelope.original.output_format == "role_expression_learning":
                source = json.loads(materials(envelope, "role_expression_samples")[0].payload)
                seen_payload.update(source)
                handles = [item["handle"] for item in source["samples"][:2]]
                return answered(json.dumps({"summary": "次轮重复变体", "signals": [
                    {**RULE, "observation": "换了个情境描述", "proposal": "同样带过",
                     "expression": {"kind": "style", "situation": "不用多解释时", "pattern": "[某事]，懂的都懂！", "avoid": ""},
                     "evidence_handles": handles},
                ]}, ensure_ascii=False))
            return await self.expression_model(envelope)

        self.model_behavior = second_round
        await self.learn_now(app, context)

        # Verify _compact_expression_form actually sent pattern in existing_rules
        sent_patterns = [rule.get("expression", {}).get("pattern") for rule in seen_payload.get("existing_rules", [])]
        self.assertIn("懂的都懂", sent_patterns)

        # Active normalized duplicate merges new support handles without inserting a second rule
        self.assertEqual(manager.list(operator=self.core, context=context, state="pending").unwrap()["items"], [])
        adopted = manager.list(operator=self.core, context=context, state="adopted").unwrap()["items"]
        self.assertEqual(len(adopted), 1)
        self.assertGreater(len(adopted[0]["evidence"]), 2, "active duplicate merges new support handles")

        # Next round tries to resurrect the rejected rule with punctuation variant -> suppressed
        self.configure_expression(app, {"private_learning_enabled": False})
        batch2 = host.receive(self.event("dedupe-new-msg-2", text="行吧行吧，那就这样。")).unwrap()
        self.advance(app)
        await self.done(app, batch2)
        self.configure_expression(app, {"private_learning_enabled": True})

        async def third_round(envelope):
            if envelope.original.output_format == "role_expression_learning":
                source = json.loads(materials(envelope, "role_expression_samples")[0].payload)
                handles = [item["handle"] for item in source["samples"][:2]]
                return answered(json.dumps({"summary": "尝试复活已拒绝规则", "signals": [
                    {**RULE, "observation": "再次提取已拒绝表达", "proposal": "再试试敷衍",
                     "expression": {"kind": "style", "situation": "无奈回应时", "pattern": "行吧行吧！", "avoid": ""},
                     "evidence_handles": handles},
                ]}, ensure_ascii=False))
            return await self.expression_model(envelope)

        self.model_behavior = third_round
        await self.learn_now(app, context)
        self.assertEqual(manager.list(operator=self.core, context=context, state="pending").unwrap()["items"], [])

    async def test_prepare_prefers_fresh_uncited_samples_when_enough_exist(self):
        host = await self.start()
        app = host.application
        context = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True, "min_samples": 3})

        first_cited = []

        async def first_model(envelope):
            if envelope.original.output_format == "role_expression_learning":
                source = json.loads(materials(envelope, "role_expression_samples")[0].payload)
                first_cited.extend(item["handle"] for item in source["samples"][:2])
                return answered(json.dumps({"summary": "首轮", "signals": [{**RULE,
                    "expression": {"kind": "style", "situation": "日常", "pattern": "先记一条", "avoid": ""},
                    "evidence_handles": first_cited}]}, ensure_ascii=False))
            return await self.expression_model(envelope)

        self.model_behavior = first_model
        await self.learn_now(app, context)
        self.assertEqual(len(first_cited), 2)

        instruction = app.state_maintenance.bindings[context.key][1]
        prepared = app.expression_style.prepare(context, instruction, app.tasks.now())
        self.assertIsNotNone(prepared)
        next_handles = {item.handle for item in prepared["samples"].samples}
        self.assertFalse(next_handles & set(first_cited), "previously cited handles are excluded when fresh samples suffice")
        self.assertGreaterEqual(len(next_handles), 3)

    async def test_dedupe_rules_preview_and_reversible_consolidation_across_all_states(self):
        host = await self.start()
        app = host.application
        context = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": False})

        # Adopted duplicate pair: keep higher-injection sig-a-hi, retire & merge sig-a-lo
        self._seed_signal(
            app, context, signal_id="sig-a-hi", run_id="run-a-1", state="adopted",
            form={"kind": "style", "situation": "彼此有共识", "pattern": "懂的都懂", "avoid": ""},
            injections=5, offset_seconds=-90,
        )
        self._seed_signal(
            app, context, signal_id="sig-a-lo", run_id="run-a-2", state="adopted",
            form={"kind": "style", "situation": "不用展开解释", "pattern": "[事件]懂的都懂！", "avoid": ""},
            injections=3, offset_seconds=-80,
        )
        # Pending duplicate of already-adopted rule + pending duplicate pair
        self._seed_signal(
            app, context, signal_id="sig-p-adopted-dup", run_id="run-p-1", state="pending",
            form={"kind": "style", "situation": "默契接话", "pattern": "懂的都懂~", "avoid": ""},
            offset_seconds=-70,
        )
        self._seed_signal(
            app, context, signal_id="sig-p-1", run_id="run-p-2", state="pending",
            form={"kind": "grammar", "situation": "回应问题", "pattern": "先简短回应，再补一句说明", "avoid": ""},
            offset_seconds=-60,
        )
        self._seed_signal(
            app, context, signal_id="sig-p-2", run_id="run-p-3", state="pending",
            form={"kind": "grammar", "situation": "接住提问", "pattern": "先简短回应再补一句说明！", "avoid": ""},
            offset_seconds=-50,
        )
        # Retired duplicates + rejected duplicates
        self._seed_signal(
            app, context, signal_id="sig-r-1", run_id="run-r-1", state="retired",
            form={"kind": "style", "situation": "感叹时", "pattern": "好家伙", "avoid": ""},
            offset_seconds=-40,
        )
        self._seed_signal(
            app, context, signal_id="sig-r-2", run_id="run-r-2", state="retired",
            form={"kind": "style", "situation": "惊讶时", "pattern": "好家伙！", "avoid": ""},
            offset_seconds=-30,
        )
        self._seed_signal(
            app, context, signal_id="sig-x-1", run_id="run-x-1", state="rejected",
            form={"kind": "style", "situation": "敷衍时", "pattern": "行吧行吧", "avoid": ""},
            offset_seconds=-20,
        )
        self._seed_signal(
            app, context, signal_id="sig-x-2", run_id="run-x-2", state="rejected",
            form={"kind": "style", "situation": "随口应和", "pattern": "行吧行吧~", "avoid": ""},
            offset_seconds=-10,
        )

        manager = ExpressionStyleManagement(app)
        before_rows = app.runtime.read_one("SELECT COUNT(*) AS n FROM expression_style_signals", ())["n"]
        preview = manager.dedupe_rules(operator=self.core, context=context, dry_run=True).unwrap()
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["duplicate_count"], 5)
        self.assertEqual(preview["by_state"], {"adopted": 1, "pending": 2, "retired": 1, "rejected": 1})
        self.assertEqual(preview["merged_count"], 0)
        # Dry run must not mutate any rows
        self.assertEqual(
            manager.list(operator=self.core, context=context, state="adopted").unwrap()["counts"]["adopted"], 2,
        )

        applied = manager.dedupe_rules(
            operator=self.core, context=context, dry_run=False, operation_key="op-dedupe-1",
        ).unwrap()
        self.assertFalse(applied["replayed"])
        self.assertEqual(applied["merged_count"], 5)

        # Replay with same operation_key is idempotent
        replayed = manager.dedupe_rules(
            operator=self.core, context=context, dry_run=False, operation_key="op-dedupe-1",
        ).unwrap()
        self.assertTrue(replayed["replayed"])
        self.assertEqual(replayed["merged_count"], 5)

        # Zero physical DB deletions; audit markers recorded
        after_rows = app.runtime.read_one("SELECT COUNT(*) AS n FROM expression_style_signals", ())["n"]
        self.assertEqual(after_rows, before_rows)
        merge_audit = rule_details.read_merge(app.runtime, "sig-a-lo")
        self.assertIsNotNone(merge_audit)
        self.assertEqual(merge_audit["merged_into"], "sig-a-hi")
        self.assertEqual(merge_audit["previous_state"], "adopted")

        # Adopted tab kept sig-a-hi with merged injections (5 + 3 = 8)
        adopted_page = manager.list(operator=self.core, context=context, state="adopted").unwrap()
        self.assertEqual([item["signal_id"] for item in adopted_page["items"]], ["sig-a-hi"])
        self.assertEqual(adopted_page["items"][0]["injections"], 8)
        self.assertEqual(adopted_page["counts"], {"adopted": 1, "pending": 1, "retired": 1, "rejected": 1})

        # Second scan finds zero remaining duplicates
        second_preview = manager.dedupe_rules(operator=self.core, context=context, dry_run=True).unwrap()
        self.assertEqual(second_preview["duplicate_count"], 0)
        self.assertEqual(second_preview["group_count"], 0)

    async def test_list_returns_authoritative_kind_counts_and_supports_kind_pagination(self):
        host = await self.start()
        app = host.application
        context = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": False})

        for index in range(8):
            self._seed_signal(
                app, context, signal_id=f"style-{index}", run_id=f"run-s-{index}", state="adopted",
                form={"kind": "style", "situation": f"情境{index}", "pattern": f"表达方式{index}", "avoid": ""},
                dimension="diction", offset_seconds=-200 + index,
            )
        for index in range(5):
            self._seed_signal(
                app, context, signal_id=f"grammar-{index}", run_id=f"run-g-{index}", state="adopted",
                form={"kind": "grammar", "situation": f"句式情境{index}", "pattern": f"句法结构{index}", "avoid": ""},
                dimension="sentence_rhythm", offset_seconds=-100 + index,
            )

        manager = ExpressionStyleManagement(app)
        page1 = manager.list(operator=self.core, context=context, state="adopted", limit=4).unwrap()
        self.assertEqual(len(page1["items"]), 4)
        self.assertEqual(page1["kind_counts"], {"style": 8, "grammar": 5})
        self.assertEqual(page1["total_count"], 13)
        self.assertIsNotNone(page1["next_cursor"])

        g1 = manager.list(operator=self.core, context=context, state="adopted", kind="grammar", limit=3).unwrap()
        self.assertEqual(g1["kind_counts"], {"style": 8, "grammar": 5})
        self.assertEqual(len(g1["items"]), 3)
        self.assertTrue(all(item["expression"]["kind"] == "grammar" for item in g1["items"]))
        self.assertIsNotNone(g1["next_cursor"])

        g2 = manager.list(
            operator=self.core, context=context, state="adopted",
            kind="grammar", limit=3, after_id=g1["next_cursor"],
        ).unwrap()
        self.assertEqual(len(g2["items"]), 2)
        self.assertIsNone(g2["next_cursor"])
        all_grammar_ids = [item["signal_id"] for item in g1["items"]] + [item["signal_id"] for item in g2["items"]]
        self.assertEqual(len(set(all_grammar_ids)), 5)

        bad_kind = manager.list(operator=self.core, context=context, state="adopted", kind="unknown")
        self.assertEqual(bad_kind.failure.code, "expression_query_invalid")


class _QueryMap(dict):
    def getlist(self, key):
        return [self[key]] if key in self else []


class ExpressionStyleWebAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_private_reply_api_forwards_kind_and_dedupe(self):
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        from astrbot_host.reply_api import ReplyWebAPI
        from companion_v2.foundation.outcome import Outcome

        calls = []

        class _FakeManager:
            def __init__(self, _app):
                pass

            def list(self, **kwargs):
                calls.append(("list", kwargs))
                return Outcome.success({
                    "items": [], "state": kwargs["state"], "next_cursor": None,
                    "counts": {}, "kind_counts": {"style": 2, "grammar": 1}, "total_count": 3,
                })

            def preview_dedupe(self, **kwargs):
                calls.append(("preview_dedupe", kwargs))
                return Outcome.success({"dry_run": True, "group_count": 1, "duplicate_count": 1, "merged_count": 0})

            def dedupe_rules(self, **kwargs):
                calls.append(("dedupe_rules", kwargs))
                return Outcome.success({"dry_run": False, "group_count": 1, "duplicate_count": 1, "merged_count": 1})

        class _Lock:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        controller = NS(_lifecycle_lock=_Lock(), _control_lock=_Lock())
        api = ReplyWebAPI(controller)
        api._snapshot = lambda **kwargs: (NS(application=object(), profile=NS(target="ctx-1")), object(), "now")

        async def _empty_body():
            return b""

        with patch("astrbot_host.reply_api.ExpressionStyleManagement", _FakeManager), \
                patch("astrbot_host.reply_api.dashboard_operator", return_value="op-1"):
            with patch("astrbot_host.reply_api.request", NS(
                username="admin", query=_QueryMap(state="adopted", kind="grammar", limit="10"), body=_empty_body,
            )):
                res = await api.style()
            self.assertEqual(res["status"], "ok")
            self.assertEqual(calls[-1][0], "list")
            self.assertEqual(calls[-1][1]["kind"], "grammar")

            async def _preview_body():
                return json.dumps({"dry_run": True}).encode("utf-8")

            with patch("astrbot_host.reply_api.request", NS(username="admin", query=_QueryMap(), body=_preview_body)):
                preview_res = await api.style_dedupe()
            self.assertEqual(preview_res["status"], "ok")
            self.assertEqual(calls[-1][0], "preview_dedupe")

            async def _apply_body():
                return json.dumps({"dry_run": False, "operation_key": "op-web-1"}).encode("utf-8")

            with patch("astrbot_host.reply_api.request", NS(username="admin", query=_QueryMap(), body=_apply_body)):
                apply_res = await api.style_dedupe()
            self.assertEqual(apply_res["status"], "ok")
            self.assertEqual(calls[-1][0], "dedupe_rules")
            self.assertEqual(calls[-1][1]["operation_key"], "op-web-1")

            async def _bad_body():
                return json.dumps({"dry_run": False}).encode("utf-8")

            with patch("astrbot_host.reply_api.request", NS(username="admin", query=_QueryMap(), body=_bad_body)):
                bad_res = await api.style_dedupe()
            self.assertEqual(bad_res["status"], "error")

    def test_group_reply_api_routes_include_dedupe_and_empty_page_carries_kind_counts(self):
        from astrbot_host.group_reply_api import GroupReplyWebAPI

        api = GroupReplyWebAPI(None, "target-g", "role-a")
        paths = {path for path, _, _, _ in api.routes("/group/reply")}
        self.assertIn("/group/reply/style/dedupe", paths)
        empty = api._empty_style_page("role_binding_missing")
        self.assertEqual(empty["kind_counts"], {"style": 0, "grammar": 0})
        self.assertEqual(empty["total_count"], 0)


if __name__ == "__main__":
    unittest.main()
