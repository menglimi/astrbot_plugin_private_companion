"""群聊文本化捕获留下的视频位置：缓存契约与请求钩子的补挂开关。

两段被测代码：

- ``astrbot_host/core_reply_video_sources.py``：进程内五元组缓存（写入/取出/过期/淘汰）。
- ``main.py`` 的 ``_attach_core_reply_videos``：按**既有**视频理解开关决定补挂，
  并在任何判断之前就把位置消费掉。
"""

from datetime import datetime, timezone
import importlib
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot_host import core_reply_video_sources as sources
from companion_v2.adapters.astrbot.native import NativeConversationInput, NativeVideoLocator
from companion_v2.execution.models import TaskContext
from companion_v2.foundation.refs import AudienceRef, ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, ResolvedIdentity, RoleBinding
from companion_v2.identity_policy.personas import ConversationRoute
from tests.test_main_no_reply import ENTRY
from tests.test_management_access import NOW


INSTALLATION = "install-c6"
AT = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)

#: main.py 用相对导入（``from .astrbot_host.core_reply_video_sources import ...``），
#: 所以补挂点用的是插件包下的那份模块。入口测试把 main.py 加载成合成包的子模块，
#: 这里必须取同一份实例，否则缓存会分成两个互不相干的表。
PLUGIN_PACKAGE = ENTRY.__name__.rsplit(".", 1)[0]
PLUGIN_SOURCES = importlib.import_module(PLUGIN_PACKAGE + ".astrbot_host.core_reply_video_sources")
PLUGIN_LOADER = PLUGIN_PACKAGE + ".astrbot_host.video.loader.load_native_video"


def group_context(group_id="300", sender_id="aiocqhttp-user:200", role_id="role-a",
                  platform="qq-main", account_id="100"):
    """与 GroupAuthority 产出同构的群上下文（真实数据类，不是 NS）。"""
    scope = ScopeRef(INSTALLATION, platform, ScopeKind.GROUP, group_id, "bot-a", account_id,
                     "conversation-" + group_id, group_id)
    speaker = SubjectRef(INSTALLATION, SubjectKind.HUMAN, sender_id)
    collective = SubjectRef(INSTALLATION, SubjectKind.GROUP, "conversation-group:" + group_id)
    role = SubjectRef(INSTALLATION, SubjectKind.BOT, role_id)
    principal = AuthenticatedHostPrincipal(INSTALLATION, "astrbot-native", platform, sender_id,
                                           "bot-a", account_id)
    actor = ResolvedIdentity(principal, speaker, "host-multi-person:" + sender_id, 1, 1)
    return TaskContext(actor, RoleBinding(scope, role, 1), AudienceRef(scope, (speaker, collective)))


def group_capture(context, event_id="group-message"):
    """与 NativeGroupBridge.capture_text_only 产出同构的群输入快照。"""
    return NativeConversationInput(event_id, context.actor.principal,
                                   ConversationRoute(context.scope, group_id=context.scope.native_scope_id),
                                   AudienceRef(context.scope, context.audience.recipients),
                                   "看这个", AT, context.role_binding.role, "你是陪伴角色。")


class FakeVideo:
    type = "Video"

    def __init__(self, *, file="", url=""):
        self.file, self.url = file, url


class FakePlain:
    type = "Plain"

    def __init__(self, text):
        self.text = text


class VideoSourceCacheTests(unittest.TestCase):
    def setUp(self):
        sources.reset()
        self.addCleanup(sources.reset)

    def test_a_capture_is_consumed_exactly_once(self):
        key = sources.key_from_context(group_context())
        self.assertTrue(sources.remember(key, (NativeVideoLocator(3, "https://example.invalid/a.mp4"),)))
        taken = sources.take(key)
        self.assertEqual(tuple((item.position, item.locator) for item in taken),
                         ((3, "https://example.invalid/a.mp4"),))
        self.assertEqual(sources.take(key), (), "a second turn must not re-attach the same video")

    def test_another_group_sender_or_role_never_sees_this_capture(self):
        key = sources.key_from_context(group_context())
        sources.remember(key, (NativeVideoLocator(0, "a"),))
        for other in (group_context(group_id="301"),
                      group_context(sender_id="aiocqhttp-user:201"),
                      group_context(role_id="role-b"),
                      group_context(account_id="101"),
                      group_context(platform="qq-second")):
            self.assertNotEqual(sources.key_from_context(other), key)
            self.assertEqual(sources.take(sources.key_from_context(other)), ())
        self.assertEqual(len(sources.take(key)), 1, "the original window still owns its own capture")

    def test_positions_expire_after_the_ttl(self):
        key = sources.key_from_context(group_context())
        sources.remember(key, (NativeVideoLocator(0, "a"),), at=100.0)
        self.assertEqual(len(sources.take(key, at=100.0 + sources.TTL_SECONDS)), 1)
        sources.remember(key, (NativeVideoLocator(0, "a"),), at=100.0)
        self.assertEqual(sources.take(key, at=100.0 + sources.TTL_SECONDS + 1.0), (),
                         "a carried video must not survive past its short window")

    def test_the_oldest_capture_is_evicted_at_the_capacity_bound(self):
        keys = [sources.key_from_context(group_context(group_id=str(1000 + index)))
                for index in range(sources.MAX_ENTRIES + 1)]
        for index, key in enumerate(keys):
            sources.remember(key, (NativeVideoLocator(0, "v" + str(index)),), at=50.0)
        self.assertEqual(sources.take(keys[0], at=50.0), (), "the least recently used capture is dropped first")
        self.assertEqual(len(sources.take(keys[-1], at=50.0)), 1)

    def test_the_key_is_the_five_host_identifiers(self):
        context = group_context()
        key = sources.key_from_context(context)
        self.assertEqual(key, ("qq-main", "100", "300", "aiocqhttp-user:200", "role-a"))
        self.assertEqual(len(key), 5)
        # 缺任何一个都不产生键：宁可这一轮不记，也不写一个会撞的键。
        for name in ("platform_id", "account_id", "group_id", "sender_id", "role_id"):
            fields = dict(zip(("platform_id", "account_id", "group_id", "sender_id", "role_id"), key))
            fields[name] = ""
            self.assertIsNone(sources.source_key(**fields))
        private_scope = ScopeRef(INSTALLATION, "qq-main", ScopeKind.PRIVATE, "200", "bot-a", "100",
                                 "conversation-a", "200")
        private = TaskContext(context.actor, RoleBinding(private_scope, context.role_binding.role, 1),
                              AudienceRef(private_scope, (context.actor.subject,)))
        self.assertIsNone(sources.key_from_context(private), "a private window never carries a group video")

    def test_the_capture_side_and_the_context_side_agree_on_the_key(self):
        context = group_context()
        self.assertEqual(sources.key_from_capture(group_capture(context)),
                         sources.key_from_context(context),
                         "the write point and the attach point must produce the same five identifiers")

    def test_text_only_components_yield_positions_in_capture_order(self):
        event = NS(message_obj=NS(message=[
            FakePlain("看这个"),
            FakeVideo(url="https://example.invalid/first.mp4"),
            FakeVideo(file="base64://QUJD", url="https://example.invalid/ignored.mp4"),
        ]))
        positions = sources.video_positions(event, video_type=FakeVideo)
        self.assertEqual(tuple((item.position, item.locator) for item in positions),
                         ((1, "https://example.invalid/first.mp4"), (2, "base64://QUJD")),
                         "capture() prefers a base64:// file over the url, and keeps component positions")
        self.assertEqual(sources.video_positions(event, video_type=None), (),
                         "a host without the Video component type has nothing to remember")

    def test_a_component_reader_failure_is_not_fatal(self):
        def broken(_event):
            raise RuntimeError("host reader failed")

        event = NS(message_obj=NS(message=[FakeVideo(url="https://example.invalid/v.mp4")]))
        self.assertEqual(sources.video_positions(event, video_type=FakeVideo, component_reader=broken), ())


class CoreReplyVideoAttachTests(unittest.IsolatedAsyncioTestCase):
    """`_attach_core_reply_videos` 的开关与一次性语义（请求钩子里真正跑的那段）。"""

    def setUp(self):
        PLUGIN_SOURCES.reset()
        self.addCleanup(PLUGIN_SOURCES.reset)
        self.plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)

    def prepare(self, **settings):
        context = group_context()
        values = {"video_understanding_enabled": True,
                  "video_understanding_group_enabled": True}
        values.update(settings)
        application = NS(reply_settings=NS(capture=lambda _context, _at: {"settings": values}))
        self.plugin.staging = NS(now=lambda: NOW, session=NS(application=application))
        key = PLUGIN_SOURCES.key_from_context(context)
        self.assertTrue(PLUGIN_SOURCES.remember(key, (NativeVideoLocator(0, "https://example.invalid/v.mp4"),)))
        return context, key

    async def test_the_group_scope_switch_alone_can_deny_the_attachment(self):
        context, key = self.prepare(video_understanding_group_enabled=False)
        request = NS(extra_user_content_parts=[])
        with patch(PLUGIN_LOADER) as loader:
            await self.plugin._attach_core_reply_videos({"context": context}, request)
        loader.assert_not_called()
        self.assertEqual(request.extra_user_content_parts, [],
                         "a window that turned group video understanding off must not get the video")
        self.assertEqual(PLUGIN_SOURCES.take(key), (),
                         "a refused attachment still consumes the capture, so the next turn never retries it")

    async def test_the_window_switch_alone_can_deny_the_attachment(self):
        context, key = self.prepare(video_understanding_enabled=False)
        request = NS(extra_user_content_parts=[])
        with patch(PLUGIN_LOADER) as loader:
            await self.plugin._attach_core_reply_videos({"context": context}, request)
        loader.assert_not_called()
        self.assertEqual(request.extra_user_content_parts, [])
        self.assertEqual(PLUGIN_SOURCES.take(key), ())

    async def test_unreadable_settings_mean_no_attachment(self):
        context = group_context()
        key = PLUGIN_SOURCES.key_from_context(context)
        self.assertTrue(PLUGIN_SOURCES.remember(key, (NativeVideoLocator(0, "https://example.invalid/v.mp4"),)))

        def broken(_context, _at):
            raise RuntimeError("configuration is unavailable")

        self.plugin.staging = NS(now=lambda: NOW,
                                 session=NS(application=NS(reply_settings=NS(capture=broken))))
        request = NS(extra_user_content_parts=[])
        with patch(PLUGIN_LOADER) as loader:
            await self.plugin._attach_core_reply_videos({"context": context}, request)
        loader.assert_not_called()
        self.assertEqual(request.extra_user_content_parts, [],
                         "an unreadable switch is not permission: the documented default is True, not consent")

    async def test_an_already_injected_request_is_left_alone(self):
        context, key = self.prepare()
        existing = NS(type="audio_url", audio_url=NS(url="data:video/mp4;base64,QUJD"))
        request = NS(extra_user_content_parts=[existing])
        with patch(PLUGIN_LOADER) as loader:
            await self.plugin._attach_core_reply_videos({"context": context}, request)
        loader.assert_not_called()
        self.assertEqual(request.extra_user_content_parts, [existing],
                         "another video plugin already put a video block in this request")
        self.assertEqual(PLUGIN_SOURCES.take(key), ())

    async def test_a_permitted_turn_gets_one_temporary_video_part(self):
        context, key = self.prepare()
        request = NS(extra_user_content_parts=[])
        video = NS(descriptor=NS(mime_type="video/mp4", duration_ms=1000, frames=(), retained_bytes=4),
                   content=b"vid")

        async def load(_locator):
            return video

        with patch(PLUGIN_LOADER, load):
            await self.plugin._attach_core_reply_videos({"context": context, "event_ref": "event-1"}, request)

        self.assertEqual(len(request.extra_user_content_parts), 1)
        part = request.extra_user_content_parts[0]
        self.assertEqual(part.type, "audio_url", "the core has no video part type; audio_url carries the data URI")
        self.assertTrue(part.audio_url.url.startswith("data:video/mp4;base64,"))
        self.assertTrue(part._no_save, "the bytes are provider-facing only and must not enter history")
        self.assertEqual(PLUGIN_SOURCES.take(key), ())

    async def test_a_private_turn_never_carries_a_group_video(self):
        context = group_context()
        key = PLUGIN_SOURCES.key_from_context(context)
        self.assertTrue(PLUGIN_SOURCES.remember(key, (NativeVideoLocator(0, "https://example.invalid/v.mp4"),)))
        scope = ScopeRef(INSTALLATION, "qq-main", ScopeKind.PRIVATE, "200", "bot-a", "100",
                         "conversation-a", "200")
        private = TaskContext(context.actor, RoleBinding(scope, context.role_binding.role, 1),
                              AudienceRef(scope, (context.actor.subject,)))
        self.plugin.staging = NS(now=lambda: NOW, session=NS(application=None))
        request = NS(extra_user_content_parts=[])
        await self.plugin._attach_core_reply_videos({"context": private}, request)
        self.assertEqual(request.extra_user_content_parts, [])
        self.assertEqual(len(PLUGIN_SOURCES.take(key)), 1,
                         "a private turn must not consume a group window's capture")
