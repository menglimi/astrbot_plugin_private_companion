import unittest
from types import SimpleNamespace

from companion_v2.foundation.refs import ScopeKind
from astrbot_host.multi_session import MultiHostSession, MultiPrivateBridge


def target(target_id, participant="1001", bot_id="bot-a"):
    return SimpleNamespace(target_id=target_id, platform_id="aiocqhttp", account_id="bot-a",
                           participant_id=participant, bot_id=bot_id,
                           route_key=("aiocqhttp", "bot-a", participant))


def settings(targets):
    def private_target(platform, account, peer):
        exact = next((item for item in targets
                      if (item.platform_id, item.account_id, item.participant_id)
                      == (platform, account, peer)), None)
        if exact is not None:
            return exact
        # 真实实现会回落到通配目标；测试保持同样的语义。
        return next((item for item in targets if item.participant_id == "*"), None)

    return SimpleNamespace(targets=targets, private_target=private_target)


def scope(participant="1001"):
    return SimpleNamespace(kind=ScopeKind.PRIVATE, platform="aiocqhttp", account_id="bot-a",
                           native_scope_id=participant, bot_id="bot-a")


class BindingSyncTests(unittest.TestCase):
    def test_bridge_target_for_scope_follows_the_confirmed_mapping(self):
        wildcard = target("private-wildcard", participant="*")
        precise = target("setup-core-1001")
        bridge = object.__new__(MultiPrivateBridge)
        bridge.groups = SimpleNamespace(sync_settings=lambda value: None)
        bridge.settings = settings([wildcard])
        bridge.targets = {item.route_key: item for item in bridge.settings.targets}

        self.assertIs(bridge.target_for_scope(scope()), wildcard)
        bridge.sync_settings(settings([wildcard, precise]))
        self.assertIs(bridge.target_for_scope(scope()), precise)

    def test_session_admission_accepts_the_refreshed_route(self):
        wildcard = target("private-wildcard", participant="*")
        precise = target("setup-core-1001")
        bridge = object.__new__(MultiPrivateBridge)
        bridge.groups = SimpleNamespace(sync_settings=lambda value: None)
        bridge.settings = settings([wildcard])
        bridge.targets = {item.route_key: item for item in bridge.settings.targets}
        session = object.__new__(MultiHostSession)
        session.bridge = bridge
        session.settings = bridge.settings
        session.authority = SimpleNamespace(settings=bridge.settings)
        bridge.sync_settings = lambda value: MultiPrivateBridge.sync_settings(bridge, value)

        session.sync_settings(settings([wildcard, precise]))
        captured = SimpleNamespace(route=SimpleNamespace(scope=scope()))
        # 确认前：桥接仍解析出通配目标，与控制器持有的精确目标不一致。
        stale = SimpleNamespace(bridge=None)
        with self.assertRaises(Exception):
            MultiHostSession._require_core_reply_target(
                SimpleNamespace(bridge=SimpleNamespace(target_for_scope=lambda _: wildcard)),
                precise, captured)
        # 同步后：同一路由解析到刚确认的目标，准入放行。
        MultiHostSession._require_core_reply_target(session, precise, captured)


if __name__ == "__main__":
    unittest.main()
