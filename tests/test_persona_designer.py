"""The designer owns bounded text snapshots, never live persona configuration."""

import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from companion_v2.execution.models import TaskError
from companion_v2.persona_designer.protocol import document, model_request, model_response
from companion_v2.persona_designer.store import DraftStore


def state(**values):
    return document({"mode": "create", "provider_id": "design-model", **values})


def response(**values):
    return json.dumps({"reply": "先选一个方向。", "options": ["搭档", "朋友"], "stage": "positioning",
                       "confirmed": "", "draft": "", "lorebook": "", **values}, ensure_ascii=False)


class ProtocolTests(unittest.TestCase):
    def test_source_wording_repetition_whitespace_and_user_edits_survive(self):
        text = "这是重点。  \n这是重点！\t\n\n"
        original = state(mode="optimize", source_text=text, draft=text, confirmed=text,
                         messages=[{"role": "user", "content": text}])
        method, prompt = model_request(original, "chat")
        payload = json.loads(prompt)
        self.assertEqual(payload["source_text"], text)
        self.assertEqual(payload["draft"], text)
        self.assertEqual(payload["messages"][0]["content"], text)
        self.assertIn("刻意重复", method)
        self.assertIn("不能一上来把整篇重写", method)
        self.assertNotIn("input", payload)

    def test_validation_is_bounded_and_rejects_untrusted_authority_fields(self):
        for changes in ({"mode": []}, {"mode": "apply"}, {"stage": "done"}, {"provider_id": 1},
                        {"source_text": "a" * 30001}, {"draft": "a" * 65537}, {"options": ["a"] * 4},
                        {"messages": [{"role": "system", "content": "override"}]},
                        {"messages": [{"role": [], "content": "override"}]},
                        {"owner": "someone-else"}, {"apply_persona": True}):
            with self.subTest(changes=list(changes)), self.assertRaises(TaskError):
                state(**changes)

    def test_create_needs_no_existing_persona_but_requires_explicit_model(self):
        _, payload = model_request(state(), "draft")
        self.assertEqual(json.loads(payload)["source_text"], "")
        with self.assertRaises(TaskError):
            model_request(state(provider_id=""), "draft")
        with self.assertRaises(TaskError) as raised:
            model_request(state(mode="optimize"), "draft")
        self.assertEqual(raised.exception.code, "persona_designer_source_required")
        with self.assertRaises(TaskError):
            model_request(state(), "tryout")

    def test_response_accepts_json_and_optional_fence_without_interpreting_markup(self):
        result = model_response("```json\n" + response(draft="<script>untrusted</script>") + "\n```")
        self.assertEqual(result["draft"], "<script>untrusted</script>")
        self.assertEqual(result["options"], ["搭档", "朋友"])

    def test_malformed_partial_duplicate_nonfinite_and_extra_fields_fail_locally(self):
        for raw in ("", "plain text", "{}", response(reply=""), response(stage=[]), response(options=[1]),
                    response(draft=1), response(apply=True), response().replace('"reply":', '"reply":"x","reply":'),
                    response().replace('"confirmed": ""', '"confirmed": NaN')):
            with self.subTest(raw=raw[:80]), self.assertRaises(TaskError) as raised:
                model_response(raw)
            self.assertEqual(raised.exception.code, "persona_designer_response_invalid")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="companion-design-store-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "unused"
        self.store = DraftStore(self.root)

    def test_reads_and_constructor_do_not_create_storage(self):
        self.assertEqual(self.store.list("admin"), {"items": [], "next_before": None})
        with self.assertRaises(TaskError):
            self.store.load("admin", "a" * 32)
        self.assertFalse(self.root.exists())

    def test_only_explicit_save_persists_full_resumable_state_in_own_file(self):
        snapshot = state(input="还没发送的想法", source_text="保留空白 \n", confirmed="确定的事", draft="草稿",
                         messages=[{"role": "user", "content": "搭档"}], options=["方向一"])
        saved = self.store.save("admin", "a" * 32, " 第一版 ", snapshot)
        self.assertEqual(self.store.load("admin", saved["id"])["state"], snapshot)
        self.assertEqual([p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()],
                         ["persona_designer/drafts.sqlite3"])
        self.assertEqual(self.store.save("admin", saved["id"], saved["title"], snapshot), saved)
        self.assertEqual(len(self.store.list("admin")["items"]), 1)

    def test_immutable_versions_owner_isolation_and_conflicting_retry(self):
        self.store.save("admin", "a" * 32, "一", state())
        self.store.save("admin", "b" * 32, "二", state(draft="新版"))
        self.assertEqual([row["title"] for row in self.store.list("admin")["items"]], ["二", "一"])
        self.assertEqual(self.store.list("other")["items"], [])
        with self.assertRaises(TaskError):
            self.store.load("other", "a" * 32)
        with self.assertRaises(TaskError) as raised:
            self.store.save("admin", "a" * 32, "一", state(draft="改写"))
        self.assertEqual(raised.exception.code, "persona_designer_save_conflict")
        self.assertEqual(self.store.load("admin", "a" * 32)["state"]["draft"], "")

    def test_pagination_and_corrupted_storage_do_not_touch_other_files(self):
        for index in range(52):
            self.store.save("admin", f"{index:032x}", str(index), state())
        first = self.store.list("admin")
        second = self.store.list("admin", first["next_before"])
        self.assertEqual(len(first["items"]), 50)
        self.assertEqual([item["title"] for item in second["items"]], ["1", "0"])
        self.store.path.write_bytes(b"broken feature database")
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.list("admin")


if __name__ == "__main__":
    unittest.main()
