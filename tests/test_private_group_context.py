from types import SimpleNamespace
from datetime import datetime, timezone
import json
import unittest

from companion_v2.foundation.refs import ScopeKind
from astrbot_host.multi_session import MultiHostSession


class _Manager:
    def __init__(self):
        self.umo = ""

    async def get_curr_conversation_id(self, umo):
        self.umo = umo
        return "private-conversation"

    async def get_conversation(self, umo, cid):
        return SimpleNamespace(history=json.dumps([
            {"role": "user", "content": "昨晚说的那本书我读完了"},
            {"role": "assistant", "content": "那正好，今天想听你说说最喜欢哪一段。"},
            {"role": "user", "content": "这句不该出现在别的群"},
        ]))


class _Settings:
    def __init__(self, mode):
        self.mode = mode

    def read(self, context):
        return {"person.private_group_context_mode": self.mode,
                "person.private_group_context_chars": 600}


class PrivateGroupContextTests(unittest.IsolatedAsyncioTestCase):
    def _session(self, mode, manager=None):
        session = object.__new__(MultiHostSession)
        session.bridge = SimpleNamespace(context=SimpleNamespace(conversation_manager=manager or _Manager()))
        session.now = lambda: datetime.now(timezone.utc)
        session.application = SimpleNamespace(messages=SimpleNamespace(
            acquaintance=SimpleNamespace(settings=_Settings(mode))))
        return session

    def _context(self):
        return SimpleNamespace(
            scope=SimpleNamespace(kind=ScopeKind.GROUP, platform="default"),
            actor=SimpleNamespace(principal=SimpleNamespace(native_account_id="995051631")),
        )

    async def test_base_mode_does_not_read_private_dialogue(self):
        manager = _Manager()
        session = self._session("base", manager)
        self.assertEqual(await session._private_group_context(self._context(), None), "")
        self.assertEqual(manager.umo, "")

    async def test_summary_mode_reads_only_the_speakers_private_conversation(self):
        manager = _Manager()
        session = self._session("summary", manager)
        text = await session._private_group_context(self._context(), None)
        self.assertIn("用户：昨晚说的那本书我读完了", text)
        self.assertIn("角色：那正好", text)
        self.assertIn("这句不该出现在别的群", text)
        self.assertEqual(manager.umo, "default:FriendMessage:995051631")

    async def test_missing_conversation_yields_no_material(self):
        class Empty(_Manager):
            async def get_curr_conversation_id(self, umo):
                return None
        session = self._session("transparent", Empty())
        self.assertEqual(await session._private_group_context(self._context(), None), "")


if __name__ == "__main__":
    unittest.main()
