"""Group model routes expose the global Laya settings without inventing group logs."""

from tests import astrbot_data_root  # noqa: F401 - isolate the SDK before import

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from astrbot_host import group_model_api as group_model_api_module
from astrbot_host.group_model_api import GroupModelWebAPI
from companion_v2.decision import LayaDecisionService, LayaSettingsStore
from companion_v2.decision.client import LayaClientError
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.storage.runtime import StorageRuntime


class GroupLayaAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_group_routes_read_and_save_global_settings(self):
        with TemporaryDirectory() as directory:
            controller = NS(data_root=Path(directory), _control_lock=asyncio.Lock())
            api = GroupModelWebAPI(controller, "group", "role")
            api._prepare = AsyncMock(return_value=("admin", NS(authorize=lambda _operator: Outcome.success(True)), "role"))
            routes = {path: methods for path, _, methods, _ in api.routes("/group/model")}
            self.assertEqual(routes["/group/model/laya"], ["GET"])
            self.assertEqual(routes["/group/model/laya/save"], ["POST"])

            with patch("astrbot_host.group_model_api.request", NS(query={})):
                status = await api.laya_status()
                logs = await api.laya_logs()
            self.assertEqual(status["status"], "ok")
            self.assertEqual(logs["data"], {"events": [], "available": False})

            body = json.dumps({"revision": status["data"]["revision"],
                               "changes": {"followup_confidence_threshold": 40}}).encode("utf-8")
            with patch("astrbot_host.group_model_api.request", NS(query={}, body=AsyncMock(return_value=body))):
                saved = await api.laya_save()
            self.assertEqual(saved["status"], "ok")
            self.assertEqual(saved["data"]["followup_confidence_threshold"], 40)
            self.assertEqual(api._prepare.await_count, 3)

    async def test_group_laya_rejects_an_operator_without_model_management_access(self):
        with TemporaryDirectory() as directory:
            controller = NS(data_root=Path(directory), _control_lock=asyncio.Lock())
            api = GroupModelWebAPI(controller, "group", "role")
            api._prepare = AsyncMock(return_value=("viewer", NS(authorize=lambda _operator:
                Outcome.failed("core_access_required", "administrator required")), "role"))
            with patch("astrbot_host.group_model_api.request", NS(query={})):
                result = await api.laya_status()
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["data"]["code"], "core_access_required")

    async def test_group_laya_logs_filter_strictly_by_group_scope_and_forward_auth_errors(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = StorageRuntime.open(data_root=root / "runtime", installation_id="inst-group-laya")
            try:
                group_a = ScopeRef("inst-group-laya", "onebot_qq", ScopeKind.GROUP, "103000519",
                                   "bot-1", "1001", "conv-a:v2-role:r1", "103000519")
                group_b = ScopeRef("inst-group-laya", "onebot_qq", ScopeKind.GROUP, "717000383",
                                   "bot-1", "1001", "conv-b:v2-role:r1", "717000383")
                private_p = ScopeRef("inst-group-laya", "onebot_qq", ScopeKind.PRIVATE, "153000127",
                                     "bot-1", "1001", "conv-p:v2-role:r1", "153000127")
                secret_key = "group-secret-key-xyz"
                store = LayaSettingsStore(root, "group-a", "role")
                store.update(store.raw()["revision"], {
                    "enabled": True, "mode": "remote",
                    "endpoint": "https://laya.test/v1/systemone", "api_key": secret_key,
                })

                # Record events for Group A (new + legacy), Group B, and Private P
                decision = {"action": "silent", "turn_state": "complete", "reply_intent": "answer",
                            "reply_style": "normal", "reply_form": "text", "segment_mode": "single",
                            "tool_need": "none", "reason_codes": []}
                svc_a = LayaDecisionService(store, runtime=runtime, origin_key="a-member-1", scope=group_a)
                svc_a.set_trace(event_ref="core-reply:group-a-new", source="conversation")
                svc_a._record(store.raw(), "ok", 14, decision=decision)

                svc_a_legacy = LayaDecisionService(store, runtime=runtime, origin_key="a-member-legacy")
                svc_a_legacy.set_trace(event_ref="core-reply:group-a-legacy", source="conversation")
                svc_a_legacy._record(store.raw(), "ok", 19, decision=decision)

                svc_b = LayaDecisionService(store, runtime=runtime, origin_key="b-member-1", scope=group_b)
                svc_b.set_trace(event_ref="core-reply:group-b-only", source="conversation")
                svc_b._record(store.raw(), "ok", 25, decision=decision)

                svc_priv = LayaDecisionService(store, runtime=runtime, origin_key="priv-user-1", scope=private_p)
                svc_priv.set_trace(event_ref="core-reply:priv-only", source="conversation")
                svc_priv._record(store.raw(), "ok", 11, decision=decision)

                from companion_v2.storage.core_reply_telemetry_schema import STATEMENTS as TELEMETRY_STATEMENTS

                with runtime.transaction() as connection:
                    for statement in TELEMETRY_STATEMENTS:
                        connection.execute(statement)
                    connection.execute(
                        """INSERT INTO astrbot_core_reply_observations
                           (turn_id, revision, role_key, context_key, scope_kind, context_json, received_at, observed_at, record_json)
                           VALUES ('core-reply:group-a-legacy', 1, 'role-key', 'a-member-legacy', 'group', ?, '2026-11-19T00:00:00+00:00', '2026-11-19T00:00:00+00:00', '{}')""",
                        (json.dumps({"audience": {"scope": group_a.document()}}),),
                    )

                controller = NS(data_root=root, _control_lock=asyncio.Lock(), runtime=runtime,
                                session=NS(application=NS(runtime=runtime)), last_session=None)
                manager = NS(
                    authorize=lambda _operator: Outcome.success(True),
                    application=NS(runtime=runtime),
                    context=NS(scope=group_a, key="group-a-mgmt-key"),
                )
                api = GroupModelWebAPI(controller, "group-a", "role")
                api._prepare = AsyncMock(return_value=("admin", manager, "role"))

                class RejectingClient:
                    def __init__(self, **_kwargs):
                        self.last_trace = None

                    async def probe(self, *, enabled_fields=None):
                        raise LayaClientError(
                            "laya_auth_failed",
                            f'Laya HTTP 403：{{"code":"MODEL_ACCESS_DENIED","message":"当前模型仅对灰度用户开放 {secret_key}"}}',
                        )

                    async def decide(self, _facts, *, enabled_fields=None):
                        raise LayaClientError(
                            "laya_auth_failed",
                            f'Laya HTTP 401：{{"error":{{"message":"invalid token {secret_key}"}}}}',
                        )

                with patch.object(group_model_api_module, "LayaDecisionClient", RejectingClient):
                    with patch("astrbot_host.group_model_api.request",
                               NS(query={}, body=AsyncMock(return_value=b"{}"))):
                        test_res = await api.laya_test()
                    with patch("astrbot_host.group_model_api.request",
                               NS(query={}, body=AsyncMock(return_value=b'{"facts":{"message_kind":"text","user_text":"hi"}}'))):
                        decide_res = await api.laya_decide()

                self.assertEqual(test_res["status"], "ok")
                self.assertEqual(test_res["data"]["status"], "failed")
                self.assertEqual(test_res["data"]["code"], "laya_auth_failed")
                self.assertEqual(
                    test_res["data"]["reason"],
                    "Laya HTTP 403 · MODEL_ACCESS_DENIED: 当前模型仅对灰度用户开放 <hidden>",
                )
                self.assertNotIn(secret_key, json.dumps(test_res, ensure_ascii=False))

                self.assertEqual(decide_res["status"], "ok")
                self.assertEqual(decide_res["data"]["status"], "failed")
                self.assertEqual(decide_res["data"]["code"], "laya_auth_failed")
                self.assertEqual(decide_res["data"]["reason"], "Laya HTTP 401 · invalid token <hidden>")
                self.assertNotIn(secret_key, json.dumps(decide_res, ensure_ascii=False))

                with patch("astrbot_host.group_model_api.request", NS(query={})):
                    logs = await api.laya_logs()
                self.assertEqual(logs["status"], "ok")
                self.assertEqual(logs["data"]["available"], True)
                self.assertEqual(logs["data"]["scope_kind"], "group")
                events = logs["data"]["events"]
                # Includes the 2 Group A conversation turns + Group A's probe & preview failures;
                # excludes Group B and Private P completely.
                self.assertEqual(len(events), 4)
                batch_ids = [ev["batch_id"] for ev in events]
                self.assertIn("core-reply:group-a-new", batch_ids)
                self.assertIn("core-reply:group-a-legacy", batch_ids)
                self.assertNotIn("core-reply:group-b-only", batch_ids)
                self.assertNotIn("core-reply:priv-only", batch_ids)
                self.assertEqual(events[0]["reason_code"], "laya_auth_failed")
                self.assertEqual(events[0]["reason"], "Laya HTTP 401 · invalid token <hidden>")
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
