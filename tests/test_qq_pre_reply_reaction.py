# -*- coding: utf-8 -*-
"""QQ 表情预回应（NapCat / OneBot v11）离线单测。

不启动 AstrBot、不连 QQ：用 fake event / fake bot 驱动宿主适配模块，
断言「只有命中回复才贴」「动作参数正确」「失败不影响回复」「收尾必撤销」。
"""

from __future__ import annotations

import asyncio
import unittest

from astrbot_host import qq_pre_reply_reaction as reaction


class _FakeBot:
    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    async def call_action(self, action: str, **params):
        self.calls.append((action, params))
        if self.fail:
            raise RuntimeError("napcat offline")
        return {"status": "ok", "retcode": 0, "data": {"result": True}}


class _FakeMessageObj:
    def __init__(self, message_id: str, raw: bool = True):
        self.message_id = message_id
        self.raw_message = (
            {"message_id": int(message_id), "time": 1700000000, "self_id": 10001}
            if raw else None
        )


class _FakeEvent:
    def __init__(self, bot, *, wake=True, core=True, platform="aiocqhttp",
                 stopped=False, raw=True, message_id="123456"):
        self.bot = bot
        self.is_wake = wake
        self._stopped = stopped
        self._platform = platform
        self.message_obj = _FakeMessageObj(message_id, raw)
        self._extras: dict = {}
        if core:
            self.set_extra(reaction.CORE_REPLY_CONTEXT_EXTRA_DEFAULT,
                           {"prepared": {"event_ref": "trial"}})

    def get_platform_name(self):
        return self._platform

    def is_stopped(self):
        return self._stopped

    def get_extra(self, key=None, default=None):
        if key is None:
            return self._extras
        return self._extras.get(key, default)

    def set_extra(self, key, value):
        self._extras[key] = value


def _config(**overrides):
    settings = {"enabled": True}
    settings.update(overrides)
    return {"qq_pre_reply_reaction": settings}


async def _settle():
    for _ in range(8):
        await asyncio.sleep(0)


class QQPreReplyReactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await reaction.cancel_all_pre_reply_reactions()

    async def test_hit_reaction_set_then_clear(self):
        bot, event = _FakeBot(), _FakeEvent(_FakeBot())
        event.bot = bot
        self.assertTrue(await reaction.apply_pre_reply_reaction(
            event, config=_config(), core_extra_key=reaction.CORE_REPLY_CONTEXT_EXTRA_DEFAULT))
        await _settle()
        self.assertEqual(bot.calls, [(reaction.ACTION, {
            "message_id": 123456, "emoji_id": 212, "set": True, "emoji_type": "1"})])
        self.assertTrue(await reaction.clear_pre_reply_reaction(event))
        self.assertEqual([params["set"] for _, params in bot.calls], [True, False])
        self.assertEqual(bot.calls[1][1]["message_id"], 123456)
        self.assertFalse(await reaction.clear_pre_reply_reaction(event))  # 幂等

    async def test_not_wake_is_ignored(self):
        bot = _FakeBot()
        event = _FakeEvent(bot, wake=False)
        self.assertFalse(await reaction.apply_pre_reply_reaction(event, config=_config()))
        await _settle()
        self.assertEqual(bot.calls, [])

    async def test_companion_scope_ignores_event_without_context(self):
        bot = _FakeBot()
        event = _FakeEvent(bot, core=False)
        self.assertFalse(await reaction.apply_pre_reply_reaction(
            event, config=_config(scope="companion")))
        await _settle()
        self.assertEqual(bot.calls, [])

    async def test_default_scope_all_covers_non_companion_reply(self):
        # 真机回归：is_wake=True 但陪伴小屋未接管（core_reply_context 缺失）时，
        # 默认 all 仍然要在等待 LLM 期间贴表情，这是需求本身。
        bot = _FakeBot()
        event = _FakeEvent(bot, core=False)
        self.assertTrue(await reaction.apply_pre_reply_reaction(event, config=_config()))
        await _settle()
        self.assertEqual(bot.calls[0][1]["set"], True)

    async def test_disabled_non_onebot_bad_emoji_and_stopped(self):
        cases = [
            ("disabled", _config(enabled=False), {}),
            ("non_onebot", _config(), {"platform": "telegram"}),
            ("bad_emoji", _config(emoji_id="not-a-number"), {}),
            ("stopped", _config(), {"stopped": True}),
        ]
        for name, config, kwargs in cases:
            with self.subTest(name=name):
                bot = _FakeBot()
                event = _FakeEvent(bot, **kwargs)
                self.assertFalse(await reaction.apply_pre_reply_reaction(event, config=config))
                await _settle()
                self.assertEqual(bot.calls, [])

    async def test_napcat_failure_is_isolated(self):
        bot = _FakeBot(fail=True)
        event = _FakeEvent(bot)
        self.assertTrue(await reaction.apply_pre_reply_reaction(event, config=_config()))
        await _settle()
        self.assertEqual(len(bot.calls), 1)  # 尝试过贴
        self.assertFalse(await reaction.clear_pre_reply_reaction(event))  # 没贴上就不撤
        self.assertEqual(len(bot.calls), 1)

    async def test_scope_all_and_keep_reaction(self):
        bot = _FakeBot()
        event = _FakeEvent(bot, core=False)  # 别的插件触发的 LLM 回复
        self.assertTrue(await reaction.apply_pre_reply_reaction(
            event, config=_config(scope="all", clear_after_reply=False)))
        await _settle()
        self.assertEqual(len(bot.calls), 1)
        self.assertFalse(await reaction.clear_pre_reply_reaction(event))  # 配置要求保留
        self.assertEqual(len(bot.calls), 1)

    async def test_idempotent_apply_and_fast_reply_race(self):
        bot = _FakeBot()
        event = _FakeEvent(bot)
        self.assertTrue(await reaction.apply_pre_reply_reaction(event, config=_config()))
        self.assertFalse(await reaction.apply_pre_reply_reaction(event, config=_config()))
        # 模拟「模型特别快、回复发出时贴表情的后台任务还没跑完」
        self.assertTrue(await reaction.clear_pre_reply_reaction(event))
        self.assertEqual([params["set"] for _, params in bot.calls], [True, False])

    async def test_message_id_falls_back_to_astrbot_message(self):
        bot = _FakeBot()
        event = _FakeEvent(bot, raw=False, message_id="987654")
        self.assertTrue(await reaction.apply_pre_reply_reaction(event, config=_config()))
        await _settle()
        self.assertEqual(bot.calls[0][1]["message_id"], 987654)
        await reaction.clear_pre_reply_reaction(event)

    async def test_timeout_helper_clears_when_no_reply(self):
        bot = _FakeBot()
        event = _FakeEvent(bot)
        self.assertTrue(await reaction.apply_pre_reply_reaction(event, config=_config()))
        await _settle()
        await reaction._timeout_clear(event, 0.01)
        self.assertEqual([params["set"] for _, params in bot.calls], [True, False])

    def test_read_settings_defaults_and_clamps(self):
        settings = reaction.read_settings({})
        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["emoji_id"], 212)
        self.assertEqual(settings["scope"], "all")
        self.assertEqual(reaction.read_settings(_config(timeout_seconds=0))["timeout_seconds"], 5.0)


if __name__ == "__main__":
    unittest.main()
