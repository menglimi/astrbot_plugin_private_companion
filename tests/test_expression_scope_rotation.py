"""Expression rules learned before a conversation rotation stay reviewable."""

import json
import unittest

from astrbot_host.management_identity import dashboard_operator
from companion_v2.execution.models import digest, stamp
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.foundation.refs import RoleOwnerRef
from tests.multi_host_support import MultiFixture, multi_config


class ExpressionScopeRotationTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_rules_from_the_previous_conversation_remain_readable(self):
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))
        app = await self.start_host()
        first, _ = await self.accepted(text="第一条")
        self.native.conversations["qq-main"] = "conversation-b"
        second, _ = await self.accepted(identifier="event-2", text="第二条")
        self.assertNotEqual(digest(first.scope), digest(second.scope))

        owner = RoleOwnerRef(second.scope.installation_id, second.scope.bot_id,
                             second.role_binding.role.subject_id)
        owner_key, old_scope = digest(owner), digest(first.scope)
        at = stamp(app.tasks.now())
        window = json.dumps({"opened_at": at, "closed_at": at, "handles": [], "characters": 0,
                             "source_kind": "shared_pack"})
        with app.runtime.transaction() as connection:
            connection.execute("INSERT INTO expression_style_runs VALUES (?,?,?,?,?,?,?,?,?)",
                               ("run-old", owner_key, old_scope, window, "hash", "旧记录", "decided", at, at))
            connection.execute("""INSERT INTO expression_style_signals
                (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal, confidence,
                 evidence_json, state, created_at, decided_at, decided_by, decision_key, ordinal)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("sig-old", "run-old", owner_key, old_scope, "diction", "旧观察", "旧提案", 0.5,
                 "[]", "pending", at, None, None, None, 0))

        management = ExpressionStyleManagement(app)
        row = management._row("sig-old", owner, second)
        self.assertEqual(row["signal_id"], "sig-old")
        operator = dashboard_operator(app.runtime, "tester", at=app.tasks.now())
        result = management.list(operator=operator, context=second, state="pending")
        self.assertTrue(result.is_success, result.failure)
        self.assertIn("sig-old", [item["signal_id"] for item in result.unwrap()["items"]])


if __name__ == "__main__":
    unittest.main()
