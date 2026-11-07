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
from unittest.mock import AsyncMock, patch

from astrbot.core.agent.message import AudioURLPart, TextPart

from astrbot_host import core_reply_video_sources as sources
from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
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
PLUGIN_FETCH = PLUGIN_PACKAGE + ".astrbot_host.video.loader.fetch_native_video_bytes"
PLUGIN_HOST_READ = PLUGIN_PACKAGE + ".astrbot_host.video.loader.read_host_resolved_video_bytes"
PLUGIN_DECODE = PLUGIN_PACKAGE + ".astrbot_host.video.loader.decode_video"
PLUGIN_NATIVE_ROUTE = PLUGIN_PACKAGE + ".astrbot_host.video.core_understanding.select_native_video_routes"
PLUGIN_SUMMARIZE = PLUGIN_PACKAGE + ".astrbot_host.video.core_understanding.summarize_original_video"
PLUGIN_INSPECT = PLUGIN_PACKAGE + ".astrbot_host.video.container.inspect_container"
ORIGINAL_VIDEO = b"complete-original-mp4"


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


def private_context():
    group = group_context()
    scope = ScopeRef(INSTALLATION, "qq-main", ScopeKind.PRIVATE, "200", "bot-a", "100",
                     "conversation-private", "200")
    return TaskContext(group.actor, RoleBinding(scope, group.role_binding.role, 1),
                       AudienceRef(scope, (group.actor.subject,)))


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
        self.assertEqual(len(sources.peek(key)), 1)
        self.assertEqual(len(sources.peek(key)), 1, "routing must not consume the attachment")
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
        self.assertEqual(sources.peek(key, at=100.0 + sources.TTL_SECONDS + 1.0), ())

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
        inspect = patch(PLUGIN_INSPECT, return_value=NS(duration_ms=1000))
        inspect.start()
        self.addCleanup(inspect.stop)

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

    async def test_video_routes_to_loaded_image_fallback_before_request_build(self):
        context, key = self.prepare()
        text_provider = NS(provider_config={"modalities": ["text"]})
        image_provider = NS(provider_config={"modalities": ["text", "image"]})
        providers = {"chat": text_provider, "vision": image_provider}
        host_config = {"provider_settings": {"default_image_caption_provider_id": "missing"},
                       "agent_runner": {"config": {"model": {"fallback_provider_ids": ["vision"]}}}}
        self.plugin.staging.context = NS(
            get_provider_by_id=lambda provider_id: providers.get(provider_id),
            get_all_providers=lambda: list(providers.values()),
            get_config=lambda **_kwargs: host_config,
            get_using_provider_async=AsyncMock(return_value=text_provider))
        extras = {"selected_provider": "chat", CORE_REPLY_CONTEXT_EXTRA: {
            "prepared": {"context": context, "event_ref": "group-video-route"}}}
        event = NS(message_obj=NS(message=[FakeVideo(url="https://example.invalid/new.mp4")]),
                   unified_msg_origin="qq:group:300", get_extra=extras.get,
                   set_extra=extras.__setitem__)
        await self.plugin._route_core_video_to_image_provider(event)
        self.assertEqual(extras["selected_provider"], "vision")
        self.assertEqual(len(PLUGIN_SOURCES.peek(key)), 1)

    async def test_video_route_respects_window_switch(self):
        context, _key = self.prepare(video_understanding_group_enabled=False)
        extras = {CORE_REPLY_CONTEXT_EXTRA: {"prepared": {"context": context}}}
        event = NS(message_obj=NS(message=[FakeVideo(url="https://example.invalid/new.mp4")]),
                   get_extra=extras.get, set_extra=extras.__setitem__)
        self.plugin.staging.context = NS(get_provider_by_id=lambda _id: self.fail("provider lookup"))
        await self.plugin._route_core_video_to_image_provider(event)
        self.assertNotIn("selected_provider", extras)

    async def test_the_group_scope_switch_alone_can_deny_the_attachment(self):
        context, key = self.prepare(video_understanding_group_enabled=False)
        request = NS(extra_user_content_parts=[])
        with patch(PLUGIN_FETCH) as loader:
            await self.plugin._attach_core_reply_videos({"context": context}, request)
        loader.assert_not_called()
        self.assertEqual(request.extra_user_content_parts, [],
                         "a window that turned group video understanding off must not get the video")
        self.assertEqual(PLUGIN_SOURCES.take(key), (),
                         "a refused attachment still consumes the capture, so the next turn never retries it")

    async def test_the_window_switch_alone_can_deny_the_attachment(self):
        context, key = self.prepare(video_understanding_enabled=False)
        request = NS(extra_user_content_parts=[])
        with patch(PLUGIN_FETCH) as loader:
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
        with patch(PLUGIN_FETCH) as loader:
            await self.plugin._attach_core_reply_videos({"context": context}, request)
        loader.assert_not_called()
        self.assertEqual(request.extra_user_content_parts, [],
                         "an unreadable switch is not permission: the documented default is True, not consent")

    async def test_an_audio_url_video_still_gets_frames(self):
        context, key = self.prepare()
        existing = NS(type="audio_url", audio_url=NS(url="data:video/mp4;base64,QUJD"))
        request = NS(extra_user_content_parts=[existing])
        original = ORIGINAL_VIDEO
        with (patch(PLUGIN_FETCH, AsyncMock(return_value=original)) as fetch,
              patch(PLUGIN_DECODE, AsyncMock(return_value=self.sampled_video())) as decode):
            await self.plugin._attach_core_reply_videos({"context": context}, request)
        fetch.assert_awaited_once()
        decode.assert_awaited_once_with(original, 0)
        self.assertIs(request.extra_user_content_parts[0], existing)
        self.assertEqual(len([part for part in request.extra_user_content_parts if part.type == "image_url"]), 2)
        self.assertEqual(PLUGIN_SOURCES.take(key), ())

    async def test_a_permitted_turn_gets_temporary_video_frames(self):
        context, key = self.prepare()
        request = NS(extra_user_content_parts=[])
        original = ORIGINAL_VIDEO
        provider = object()
        event = NS(get_extra=lambda _key: None)
        with (patch(PLUGIN_FETCH, AsyncMock(return_value=original)) as fetch,
              patch(PLUGIN_NATIVE_ROUTE, return_value=((provider, "gemini-test"),)),
              patch(PLUGIN_SUMMARIZE, AsyncMock(side_effect=RuntimeError("native failed"))) as summarize,
              patch(PLUGIN_DECODE, AsyncMock(return_value=self.sampled_video())) as decode):
            await self.plugin._attach_core_reply_videos(
                {"context": context, "event_ref": "event-1"}, request, event=event)
        fetch.assert_awaited_once()
        self.assertEqual(summarize.await_args.args[:3], (original, provider, "gemini-test"))
        decode.assert_awaited_once_with(original, 0)
        images = [part for part in request.extra_user_content_parts if part.type == "image_url"]
        self.assertEqual(len(images), 2)
        self.assertTrue(all(part._no_save for part in request.extra_user_content_parts))
        self.assertEqual(PLUGIN_SOURCES.take(key), ())

    async def test_group_video_prefers_original_mp4_without_decoding(self):
        context, key = self.prepare()
        request = NS(extra_user_content_parts=[])
        original = ORIGINAL_VIDEO
        provider = object()
        event = NS(get_extra=lambda _key: None)
        with (patch(PLUGIN_FETCH, AsyncMock(return_value=original)) as fetch,
              patch(PLUGIN_NATIVE_ROUTE, return_value=((provider, "gemini-test"),)),
              patch(PLUGIN_SUMMARIZE, AsyncMock(return_value="完整视频内容")) as summarize,
              patch(PLUGIN_DECODE, AsyncMock()) as decode):
            await self.plugin._attach_core_reply_videos(
                {"context": context, "event_ref": "event-1"}, request, event=event)
        fetch.assert_awaited_once()
        self.assertEqual(summarize.await_args.args[:3], (original, provider, "gemini-test"))
        decode.assert_not_awaited()
        from astrbot_host.core_reply_perception import video_request_state
        self.assertEqual(video_request_state(request), "native_summary")
        self.assertTrue(all(part._no_save for part in request.extra_user_content_parts))
        self.assertEqual(PLUGIN_SOURCES.take(key), ())

    async def test_second_native_model_succeeds_before_frame_fallback(self):
        context, _key = self.prepare()
        request = NS(extra_user_content_parts=[])
        first, second = object(), object()
        event = NS(get_extra=lambda _key: None)
        with (patch(PLUGIN_FETCH, AsyncMock(return_value=ORIGINAL_VIDEO)),
              patch(PLUGIN_NATIVE_ROUTE, return_value=((first, "gemini-1"), (second, "gemini-2"))),
              patch(PLUGIN_SUMMARIZE, AsyncMock(side_effect=[RuntimeError("503"), "完整视频内容"])) as summarize,
              patch(PLUGIN_DECODE, AsyncMock()) as decode):
            await self.plugin._attach_core_reply_videos({"context": context}, request, event=event)
        self.assertEqual(summarize.await_count, 2)
        self.assertEqual(summarize.await_args_list[0].args[:3], (ORIGINAL_VIDEO, first, "gemini-1"))
        self.assertEqual(summarize.await_args_list[1].args[:3], (ORIGINAL_VIDEO, second, "gemini-2"))
        self.assertTrue(all(40 < call.args[3]["video_understanding_wait_seconds"] <= 45
                            for call in summarize.await_args_list))
        decode.assert_not_awaited()
        from astrbot_host.core_reply_perception import video_request_state
        self.assertEqual(video_request_state(request), "native_summary")

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

    def current_turn(self, **settings):
        context = private_context()
        values = {"video_understanding_enabled": True,
                  "video_understanding_private_enabled": True}
        values.update(settings)
        application = NS(reply_settings=NS(capture=lambda _context, _at: {"settings": values}))
        self.plugin.staging = NS(now=lambda: NOW, context=None, session=NS(application=application))
        state = {"prepared": {"context": context, "event_ref": "private-video"}}
        event = NS(get_extra=lambda _key: state)
        request = NS(extra_user_content_parts=[TextPart(text=(
            "[Video Attachment: name clip.mp4, path C:\\host\\temp\\clip.mp4]"))])
        return event, request

    @staticmethod
    def sampled_video():
        frames = (NS(content=b"first-png"), NS(content=b"last-png"))
        descriptor = NS(position=0, mime_type="video/mp4", duration_ms=1000, retained_bytes=24,
                        frame_count=12, frames=(NS(index=0), NS(index=11)))
        return NS(descriptor=descriptor, content=b"video", frames=frames)

    async def test_private_video_supplies_temporary_ordered_frames(self):
        event, request = self.current_turn()
        original = ORIGINAL_VIDEO
        with (patch(PLUGIN_HOST_READ, AsyncMock(return_value=original)) as reader,
              patch(PLUGIN_DECODE, AsyncMock(return_value=self.sampled_video())) as decode):
            await self.plugin.restate_companion_core_video_state(event, request)
            await self.plugin.restate_companion_core_video_state(event, request)
        reader.assert_awaited_once_with("C:\\host\\temp\\clip.mp4")
        decode.assert_awaited_once_with(original, 0)
        from astrbot_host.core_reply_perception import video_request_state
        self.assertEqual(video_request_state(request), "sampled_frames")
        images = [part for part in request.extra_user_content_parts if part.type == "image_url"]
        self.assertEqual([part.image_url.id for part in images],
                         ["companion-video-frame:0:0", "companion-video-frame:0:11"])
        self.assertTrue(all(part._no_save for part in request.extra_user_content_parts[1:]))

    async def test_private_video_adds_frames_even_with_an_external_audio_url_video(self):
        event, request = self.current_turn()
        existing = AudioURLPart(audio_url=AudioURLPart.AudioURL(url="data:video/mp4;base64,AAAA"))
        request.extra_user_content_parts.append(existing)
        original = ORIGINAL_VIDEO
        with (patch(PLUGIN_HOST_READ, AsyncMock(return_value=original)) as reader,
              patch(PLUGIN_DECODE, AsyncMock(return_value=self.sampled_video()))):
            await self.plugin.restate_companion_core_video_state(event, request)
        reader.assert_awaited_once()
        from astrbot_host.core_reply_perception import video_request_state
        self.assertEqual(video_request_state(request), "sampled_frames")
        self.assertIs(request.extra_user_content_parts[1], existing)

    async def test_private_video_respects_the_window_switch(self):
        event, request = self.current_turn(video_understanding_private_enabled=False)
        with patch(PLUGIN_HOST_READ, AsyncMock()) as loader:
            await self.plugin.restate_companion_core_video_state(event, request)
        loader.assert_not_awaited()
        self.assertEqual(len(request.extra_user_content_parts), 1)

    async def test_private_video_prefers_original_mp4_without_decoding(self):
        event, request = self.current_turn()
        original = ORIGINAL_VIDEO
        provider = object()
        with (patch(PLUGIN_HOST_READ, AsyncMock(return_value=original)) as reader,
              patch(PLUGIN_NATIVE_ROUTE, return_value=((provider, "gemini-test"),)),
              patch(PLUGIN_SUMMARIZE, AsyncMock(return_value="完整视频内容")) as summarize,
              patch(PLUGIN_DECODE, AsyncMock()) as decode):
            await self.plugin.restate_companion_core_video_state(event, request)
        reader.assert_awaited_once()
        self.assertEqual(summarize.await_args.args[:3], (original, provider, "gemini-test"))
        decode.assert_not_awaited()
        from astrbot_host.core_reply_perception import video_request_state
        self.assertEqual(video_request_state(request), "native_summary")
        self.assertTrue(all(part._no_save for part in request.extra_user_content_parts[1:]))

    async def test_current_group_video_takes_precedence_over_a_pending_previous_video(self):
        context, key = self.prepare()
        state = {"prepared": {"context": context, "event_ref": "new-group-video"}}
        event = NS(get_extra=lambda _key: state)
        request = NS(extra_user_content_parts=[TextPart(text=(
            "[Video Attachment: name new.mp4, path C:\\host\\temp\\new.mp4]"))])
        original = ORIGINAL_VIDEO
        with (patch(PLUGIN_HOST_READ, AsyncMock(return_value=original)) as current_loader,
              patch(PLUGIN_FETCH, AsyncMock()) as previous_loader,
              patch(PLUGIN_DECODE, AsyncMock(return_value=self.sampled_video()))):
            await self.plugin.restate_companion_core_video_state(event, request)
        current_loader.assert_awaited_once_with("C:\\host\\temp\\new.mp4")
        previous_loader.assert_not_awaited()
        self.assertEqual(PLUGIN_SOURCES.take(key), ())
        from astrbot_host.core_reply_perception import video_request_state
        self.assertEqual(video_request_state(request), "sampled_frames")
