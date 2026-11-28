"""Telegram private attachments, converted by the installed AstrBot adapter, on the core reply path."""

import asyncio
import base64
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from tests import astrbot_data_root  # noqa: F401 - isolate the SDK before import
from telegram import Update
from astrbot.core.astr_main_agent import collect_initial_request
from astrbot.core.message.components import File, Plain, Reply
from tests.audio_support import audio_bytes
from tests.test_conversation_images import image_bytes
from tests.test_telegram_platform import USER_ID, TelegramFixture


BOT_ID = 424242
REQUEST_CONFIG = NS(provider_wake_prefix="", provider_settings={}, max_quoted_fallback_images=0)


def photo(file_id):
    return [dict(file_id=file_id, file_unique_id=file_id, width=3, height=2)]


class Interrupted:
    """A body that stops after ``received`` bytes."""

    def __init__(self, received):
        self.received = received


class Stalled(Interrupted):
    """A body that stops sending after ``received`` bytes and never finishes."""


class FileServer:
    """Telegram's file endpoint for aiohttp: one body per file ID, named by the last path segment."""

    def __init__(self, bodies):
        self.bodies, self.requests, self.sessions, self.headers = bodies, [], [], []

    def __call__(self, *args, **kwargs):
        self.sessions.append(kwargs)
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        self.requests.append(url.rsplit("/", 1)[-1])
        self.headers.append(kwargs.get("headers"))
        return _Response(self.bodies[url.rsplit("/", 1)[-1]])

    def serving(self):
        session = patch("astrbot_host.media.aiohttp.ClientSession", self)
        connector = patch("astrbot_host.media.aiohttp.TCPConnector")
        return _Both(session, connector)


class _Both:
    def __init__(self, *managers):
        self.managers = managers

    def __enter__(self):
        for manager in self.managers:
            manager.__enter__()

    def __exit__(self, *exc):
        for manager in reversed(self.managers):
            manager.__exit__(*exc)


class _Response:
    status, headers, content_length = 200, {}, None

    def __init__(self, body):
        self.body, self.content = body, self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def iter_chunked(self, size):
        if isinstance(self.body, Interrupted):
            yield b"x" * self.body.received
            if isinstance(self.body, Stalled):
                await asyncio.Event().wait()
            raise OSError("synthetic interrupted body")
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]


def parts(payload):
    # AstrBot sends text alone as a string and anything else as content parts.
    content = payload["content"]
    return [{"type": "text", "text": content}] if isinstance(content, str) else content


def images(payload):
    return [base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
            for part in parts(payload) if part["type"] == "image_url"]


def text(payload):
    return "\n".join(part.get("text", "") for part in parts(payload))


class TelegramMediaFixture(TelegramFixture):
    async def asyncSetUp(self):
        await self.start_host()
        bot = self.platform.client
        self.token = bot.token
        bot.username = "synthetic_bot"
        bot.base_file_url = f"https://api.telegram.org/file/bot{bot.token}"
        bot.get_file = AsyncMock(side_effect=lambda file_id, **_: NS(file_path=f"{bot.base_file_url}/media/{file_id}"))
        self.target = self.controller.settings.target("tg-owner")
        self.bridge = self.controller.session.bridge

    async def convert(self, number=1, **fields):
        update = Update.de_json({"update_id": number, "message": {
            "message_id": number, "date": 1700000000, "chat": {"type": "private", "id": int(USER_ID)},
            "from": {"id": int(USER_ID), "is_bot": False, "first_name": "Synthetic"}, **fields}}, self.platform.client)
        return await self.platform.convert_message(update, NS(bot=self.platform.client))

    def event(self, message):
        event = self.platform.create_event(message)
        event.created_at = self.clock.now().timestamp()
        return event

    async def album(self, *items):
        updates = []
        for number, fields in enumerate(items, 1):
            update = (await self.convert(number, media_group_id="album", **fields)).raw_message
            updates.append((update, NS(bot=self.platform.client)))
        self.platform.media_group_cache = {"album": {"items": updates}}
        self.platform.handle_msg = AsyncMock()
        await self.platform.process_media_group("album")
        return self.event(self.platform.handle_msg.await_args.args[0])

    async def collect(self, event, server):
        """Prepare through the managed reply entry, then let AstrBot collect its provider request."""
        with server.serving(), patch("astrbot.core.astr_main_agent._get_session_conv",
                                     AsyncMock(return_value=NS(history="[]"))):
            (await self.controller.session.prepare_core_reply(event, self.target)).unwrap()
            request, _ = await collect_initial_request(event, NS(), REQUEST_CONFIG)
        return request, await request.assemble_context()

    def settings(self, **values):
        from companion_v2.conversation.media.understanding import settings
        return patch.object(self.bridge, "file_settings", lambda scope, role: settings(values))

    def assert_no_token(self, *values):
        for value in values:
            self.assertNotIn(self.token, value if isinstance(value, str) else repr(value))


class TelegramPrivateMediaTests(TelegramMediaFixture, unittest.IsolatedAsyncioTestCase):
    async def test_album_photos_and_caption_reach_the_request_without_the_token(self):
        red, blue = image_bytes("red"), image_bytes("blue")
        event = await self.album(dict(photo=photo("p1"), caption="album caption"), dict(photo=photo("p2")))
        request, payload = await self.collect(event, FileServer({"p1": red, "p2": blue}))
        self.assertEqual(images(payload), [red, blue])
        self.assertIn("album caption", text(payload))
        with self.controller.runtime.read_only_snapshot() as db:
            rows = "\n".join(db.iterdump())
        self.assert_no_token(json.dumps(payload, ensure_ascii=False), event.message_obj.message, rows, request.prompt,
                             request.image_urls, request.extra_user_content_parts, request.contexts)

    async def test_photo_saved_by_astrbot_is_not_copied_again(self):
        from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

        event = self.event(await self.convert(photo=photo("p1"), caption="look"))
        # AstrBot's preprocessing has already saved the photo in its temp directory.
        saved = Path(get_astrbot_temp_path(), "preprocessed.png")
        saved.write_bytes(image_bytes("red"))
        (image,) = [part for part in event.message_obj.message if type(part).__name__ == "Image"]
        image.file = image.url = image.path = str(saved)
        before = set(Path(get_astrbot_temp_path()).iterdir())
        server = FileServer({})
        request, payload = await self.collect(event, server)
        self.assertEqual((server.requests, request.image_urls), ([], [str(saved)]))
        self.assertEqual(images(payload), [image_bytes("red")])
        self.assertEqual(set(Path(get_astrbot_temp_path()).iterdir()), before)

    async def test_self_hosted_file_endpoint_is_read_like_telegram_own(self):
        # telegram_file_base_url pointing at the operator's own Bot API server, on a private address.
        bot = self.platform.client
        bot.base_file_url = f"http://127.0.0.1:8081/file/bot{bot.token}"
        clip = dict(file_id="v1", file_unique_id="v1", width=64, height=48, duration=1)
        document = dict(document=dict(file_id="d1", file_unique_id="d1", file_name="notes.txt"))
        for kind, fields in (("video", dict(video=clip, caption="watch this")), ("document", document)):
            with self.subTest(kind=kind):
                event = self.event(await self.convert(**fields))
                server = FileServer({"v1": b"movie", "d1": b"body"})
                with self.settings(file_understanding_enabled=True):
                    _, payload = await self.collect(event, server)
                self.assertEqual(server.requests, ["v1" if kind == "video" else "d1"])
                self.assertNotIn("附件不可用", text(payload))
                self.assertNotIn("文件读取失败", text(payload))
                self.assert_no_token(json.dumps(payload, ensure_ascii=False))

    async def test_telegram_downloads_use_the_host_proxy_and_ask_for_identity(self):
        # AstrBot puts its http_proxy setting in the environment; its own Telegram downloads honor it.
        clip = dict(file_id="v1", file_unique_id="v1", width=64, height=48, duration=1)
        event = self.event(await self.convert(video=clip, caption="watch this"))
        server = FileServer({"v1": b"movie"})
        await self.collect(event, server)
        self.assertEqual(server.requests, ["v1"])
        self.assertEqual([session["trust_env"] for session in server.sessions], [True])
        self.assertEqual(server.headers, [{"Accept-Encoding": "identity"}])

    async def test_local_mode_bot_api_server_files_are_read_from_disk(self):
        # A local-mode Bot API server hands over absolute paths on a disk it shares with AstrBot.
        # getFile goes to telegram_api_base_url, so that alone can point at the server.
        bot = self.platform.client
        public_file, public_api = bot.base_file_url, f"https://api.telegram.org/bot{bot.token}"
        configs = {"file endpoint": (f"http://127.0.0.1:8081/file/bot{bot.token}", public_api),
                   "api endpoint": (public_file, f"http://127.0.0.1:8081/bot{bot.token}")}
        with TemporaryDirectory() as shared:
            files = {"p1": image_bytes("red"), "v1": b"movie", "d1": b"body"}
            for name, body in files.items():
                Path(shared, name).write_bytes(body)
            bot.get_file = AsyncMock(side_effect=lambda file_id, **_: NS(file_path=str(Path(shared, file_id))))
            clip = dict(file_id="v1", file_unique_id="v1", width=64, height=48, duration=1)
            document = dict(document=dict(file_id="d1", file_unique_id="d1", file_name="notes.txt"))
            cases = [(config, kind, fields) for config in configs
                     for kind, fields in (("photo", dict(photo=photo("p1"))), ("video", dict(video=clip)),
                                          ("document", document))]
            for config, kind, fields in cases:
                bot.base_file_url, bot.base_url = configs[config]
                with self.subTest(config=config, kind=kind):
                    event = self.event(await self.convert(**fields, caption="look"))
                    server = FileServer({})
                    with self.settings(file_understanding_enabled=True):
                        request, payload = await self.collect(event, server)
                    self.assertEqual(server.requests, [])
                    self.assertNotIn("附件不可用", text(payload))
                    self.assertNotIn("文件读取失败", text(payload))
                    if kind == "photo":
                        self.assertEqual(images(payload), [image_bytes("red")])

    async def test_local_files_stay_closed_for_telegram_own_server(self):
        from astrbot_host.telegram_private_media import _file_endpoint

        bot = self.platform.client
        event = self.event(await self.convert(text="hello"))
        # The same public host, however the operator spelled it, never reads local paths.
        for spelling in ("https://api.telegram.org", "https://API.telegram.org", "https://api.telegram.org:443"):
            bot.base_file_url, bot.base_url = f"{spelling}/file/bot{bot.token}", f"{spelling}/bot{bot.token}"
            with self.subTest(spelling=spelling):
                self.assertEqual(_file_endpoint(event), (bot.base_file_url, False))

    async def test_only_a_file_path_carrying_the_token_is_trusted(self):
        from astrbot_host.telegram_private_media import _file_endpoint

        bot = self.platform.client
        event = self.event(await self.convert(text="hello"))
        bot.base_url = f"https://api.telegram.org/bot{bot.token}"
        bot.base_file_url = f"http://127.0.0.1:8081/file/bot{bot.token}"
        self.assertEqual(_file_endpoint(event), (bot.base_file_url, True))
        # A gateway that takes the token in a query: nothing under its path is trusted.
        bot.base_file_url = f"https://gateway.example/download?file_base=bot{bot.token}"
        self.assertEqual(_file_endpoint(event), (None, True))

    async def test_local_read_refuses_a_fifo_without_blocking(self):
        from astrbot_host.media import fetch_attachment, trusted_endpoint
        from companion_v2.execution.models import TaskError

        with TemporaryDirectory() as shared:
            fifo = Path(shared, "pipe")
            os.mkfifo(fifo)
            with trusted_endpoint("http://127.0.0.1:8081/file/bot1:x", local_files=True):
                with self.assertRaises(TaskError):
                    await asyncio.wait_for(fetch_attachment(str(fifo), maximum_bytes=1024), 5)

    async def test_album_beyond_the_turn_limit_keeps_the_caption_and_admitted_photo(self):
        red, blue = image_bytes("red"), image_bytes("blue")
        event = await self.album(dict(photo=photo("p1"), caption="album caption"), dict(photo=photo("p2")))
        with patch("companion_v2.conversation.media.models.MAX_TURN_BYTES", len(red) + 5):
            _, payload = await self.collect(event, FileServer({"p1": red, "p2": blue}))
        self.assertEqual(images(payload), [red])
        self.assertIn("album caption", text(payload))
        self.assertIn("附件超过读取上限", text(payload))

    async def test_slow_attachment_keeps_earlier_photos_and_notes_the_timeout(self):
        red = image_bytes("red")
        event = await self.album(dict(photo=photo("p1"), caption="slow album"), dict(photo=photo("p2")),
                                 dict(photo=photo("p3")))
        server = FileServer({"p1": red, "p2": Stalled(4), "p3": red})
        with patch("astrbot_host.telegram_private_media.MEDIA_PREPARE_TIMEOUT", 0.5):
            _, payload = await self.collect(event, server)
        self.assertEqual(images(payload), [red])
        self.assertEqual(server.requests, ["p1", "p2"])
        self.assertIn("slow album", text(payload))
        self.assertIn("附件读取超时", text(payload))

    async def test_interrupted_download_still_spends_the_turn_allowance(self):
        red = image_bytes("red")
        event = await self.album(dict(photo=photo("p1"), caption="caption"), dict(photo=photo("p2")))
        with patch("companion_v2.conversation.media.models.MAX_TURN_BYTES", len(red) + 5), self.assertLogs(
                "astrbot_host.telegram_private_media", "INFO") as logs:
            _, payload = await self.collect(event, FileServer({"p1": Interrupted(10), "p2": red}))
        # Without the ten bytes already received, the second photo would still fit.
        self.assertEqual(images(payload), [])
        self.assertIn("附件不可用", text(payload))
        self.assertIn("附件超过读取上限", text(payload))
        self.assert_no_token(" ".join(logs.output))

    async def test_download_that_cannot_be_saved_still_spends_the_turn_allowance(self):
        clip = dict(width=64, height=48, duration=1)
        event = await self.album(dict(video=dict(clip, file_id="v1", file_unique_id="v1"), caption="clips"),
                                 dict(video=dict(clip, file_id="v2", file_unique_id="v2")))
        server = FileServer({"v1": b"x" * 10, "v2": b"y" * 10})
        # The disk fills up while the first download is saved; the second would still fit otherwise.
        with patch("companion_v2.conversation.media.models.MAX_TURN_BYTES", 15), \
                patch("astrbot_host.telegram_private_media._temporary_path", side_effect=OSError("disk full")):
            _, payload = await self.collect(event, server)
        self.assertIn("附件不可用", text(payload))
        self.assertIn("附件超过读取上限", text(payload))

    async def test_slow_save_is_held_to_the_turn_deadline(self):
        from astrbot_host import telegram_private_media

        real = telegram_private_media._temporary_path

        def stalled(*args):
            time.sleep(0.4)
            return real(*args)

        clip = dict(file_id="v1", file_unique_id="v1", width=64, height=48, duration=1)
        event = self.event(await self.convert(video=clip, caption="watch this"))
        with patch("astrbot_host.telegram_private_media.MEDIA_PREPARE_TIMEOUT", 0.15), \
                patch("astrbot_host.telegram_private_media._temporary_path", stalled):
            _, payload = await self.collect(event, FileServer({"v1": b"movie"}))
        self.assertIn("附件读取超时", text(payload))
        self.assertIn("watch this", text(payload))

    async def test_save_that_finishes_after_the_timeout_leaves_no_file(self):
        import threading
        from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
        from astrbot_host import telegram_private_media

        gate, real = threading.Event(), telegram_private_media.NamedTemporaryFile

        def held(*args, **kwargs):
            gate.wait(5)
            return real(*args, **kwargs)

        clip = dict(file_id="v1", file_unique_id="v1", width=64, height=48, duration=1)
        event = self.event(await self.convert(video=clip, caption="watch this"))
        temp = Path(get_astrbot_temp_path())
        with patch("astrbot_host.telegram_private_media.MEDIA_PREPARE_TIMEOUT", 0.15), \
                patch("astrbot_host.telegram_private_media.NamedTemporaryFile", held):
            _, payload = await self.collect(event, FileServer({"v1": b"movie"}))
            self.assertIn("附件读取超时", text(payload))
            before = set(temp.iterdir())
            # AstrBot cleans the event up while the abandoned write is still pending.
            event.cleanup_temporary_local_files()
            gate.set()
            await asyncio.sleep(0.5)
        self.assertEqual(set(temp.iterdir()) - before, set())

    async def test_photo_locator_runs_off_the_event_loop(self):
        import threading
        from astrbot_host import telegram_private_media

        real, threads = telegram_private_media.image_locator, []

        def watched(*args):
            threads.append(threading.current_thread() is threading.main_thread())
            return real(*args)

        event = self.event(await self.convert(photo=photo("p1"), caption="look"))
        with patch("astrbot_host.telegram_private_media.image_locator", watched):
            _, payload = await self.collect(event, FileServer({"p1": image_bytes("red")}))
        self.assertEqual(images(payload), [image_bytes("red")])
        self.assertEqual(threads, [False])

    async def test_document_follows_the_window_file_setting(self):
        document = dict(document=dict(file_id="d1", file_unique_id="d1", file_name="notes.txt"), caption="please read")
        for name, values, body, expected in (
                ("disabled", {}, b"body", "文件理解未启用"),
                ("enabled", {"file_understanding_enabled": True}, b"body", "File Attachment: name notes.txt"),
                ("too large", {"file_understanding_enabled": True, "file_understanding_max_size_mb": 1},
                 b"x" * (1024 ** 2 + 1), "文件超过读取大小上限")):
            with self.subTest(name):
                event = self.event(await self.convert(**document))
                server = FileServer({"d1": body})
                with self.settings(**values):
                    _, payload = await self.collect(event, server)
                self.assertIn(expected, text(payload))
                self.assertIn("please read", text(payload))
                self.assertEqual(server.requests, [] if name == "disabled" else ["d1"])
                files = [part for part in event.message_obj.message if isinstance(part, File)]
                if name == "enabled":
                    self.assertEqual(Path(await files[0].get_file()).read_bytes(), b"body")
                else:
                    self.assertEqual(files, [])
                self.assert_no_token(json.dumps(payload, ensure_ascii=False))

    async def test_video_and_video_document_reach_the_video_placeholders(self):
        from astrbot_host.core_reply_perception import video_placeholders

        clip = dict(file_id="v1", file_unique_id="v1", width=64, height=48, duration=1)
        for kind, fields in (("video", dict(video=clip, caption="watch this")),
                             ("document", dict(document=dict(file_id="v1", file_unique_id="v1",
                                                             file_name="trip.mp4"), caption="watch this")),
                             # AstrBot names an unnamed document with random hex; the file path keeps .mp4.
                             ("unnamed document", dict(document=dict(file_id="v1.mp4", file_unique_id="v1"),
                                                       caption="watch this"))):
            with self.subTest(kind=kind):
                event = self.event(await self.convert(**fields))
                request, payload = await self.collect(event, FileServer({"v1": b"movie", "v1.mp4": b"movie"}))
                (item,) = video_placeholders(request, include_quoted=False)
                self.assertEqual(item.location, "path")
                self.assertEqual(Path(item.value).read_bytes(), b"movie")
                self.assertIn("watch this", text(payload))
                event.cleanup_temporary_local_files()
                self.assertFalse(Path(item.value).exists())
                self.assert_no_token(json.dumps(payload, ensure_ascii=False))

    async def test_voice_note_keeps_its_caption_and_goes_to_astrbot_unchanged(self):
        from astrbot_host.telegram_private_media import restore_voice_caption

        async def download(url, path):
            Path(path).write_bytes(audio_bytes())

        with patch("astrbot.core.platform.sources.telegram.tg_adapter.download_file", download):
            message = await self.convert(voice=dict(file_id="a1", file_unique_id="a1", duration=1),
                                         caption="listen to this")
        event = self.event(message)
        restore_voice_caption(event)
        (record,) = [part for part in event.message_obj.message if type(part).__name__ == "Record"]
        original = record.path
        # A voice note is not held to the audio limit: AstrBot's WAV would hit 5 MiB near 55 seconds.
        with patch("astrbot_host.telegram_private_media.MAX_AUDIO_BYTES", 100):
            request, payload = await self.collect(event, FileServer({}))
        self.assertIn("listen to this", text(payload))
        self.assertEqual(request.audio_urls, [original])

    async def test_voice_caption_survives_astrbot_speech_to_text(self):
        from astrbot_host.telegram_private_media import restore_voice_caption

        async def download(url, path):
            Path(path).write_bytes(audio_bytes())

        with patch("astrbot.core.platform.sources.telegram.tg_adapter.download_file", download):
            message = await self.convert(
                voice=dict(file_id="a1", file_unique_id="a1", duration=1), caption="listen to this",
                caption_entities=[dict(type="text_link", offset=0, length=6, url="https://example.org/trail")])
        # AstrBot's preprocessing replaces the recording with its transcript before the plugin runs;
        # the transcript may happen to contain the caption's words.
        message.message[:] = [Plain(text="please listen to this")]
        event = self.event(message)
        for _ in range(2):
            restore_voice_caption(event)
        texts = [part.text for part in event.message_obj.message if isinstance(part, Plain)]
        self.assertEqual(texts, ["please listen to this", "listen (https://example.org/trail) to this"])

    async def test_slow_recording_read_notes_the_timeout(self):
        async def download(url, path):
            Path(path).write_bytes(audio_bytes())

        def slow(*args):
            time.sleep(0.3)
            return audio_bytes()

        with patch("astrbot.core.platform.sources.telegram.tg_adapter.download_file", download):
            message = await self.convert(audio=dict(file_id="a1", file_unique_id="a1", duration=1), caption="my song")
        event = self.event(message)
        with patch("astrbot_host.telegram_private_media.MEDIA_PREPARE_TIMEOUT", 0.05), \
                patch("astrbot_host.telegram_private_media._read_temp_record", slow):
            request, payload = await self.collect(event, FileServer({}))
        self.assertEqual(request.audio_urls, [])
        self.assertIn("附件读取超时", text(payload))
        self.assertIn("my song", text(payload))

    async def test_music_over_the_audio_limit_keeps_its_caption(self):
        async def download(url, path):
            Path(path).write_bytes(audio_bytes())

        for limit, kept in ((100, 0), (1024 ** 2, 1)):
            with self.subTest(limit=limit):
                with patch("astrbot.core.platform.sources.telegram.tg_adapter.download_file", download):
                    message = await self.convert(audio=dict(file_id="a1", file_unique_id="a1", duration=1),
                                                 caption="my song")
                event = self.event(message)
                with patch("astrbot_host.telegram_private_media.MAX_AUDIO_BYTES", limit):
                    request, payload = await self.collect(event, FileServer({}))
                self.assertIn("my song", text(payload))
                self.assertEqual(len(request.audio_urls), kept)
                self.assertEqual("附件超过读取上限" in text(payload), not kept)
                if kept:
                    # The admitted copy belongs to the event and goes with its other files.
                    event.cleanup_temporary_local_files()
                    self.assertFalse(Path(request.audio_urls[0]).exists())

    async def test_sticker_is_recorded_as_a_sticker(self):
        sticker = dict(file_id="s1", file_unique_id="s1", type="regular", width=48, height=48,
                       is_animated=False, is_video=False, emoji="😀")
        event = self.event(await self.convert(sticker=sticker))
        captured = (await self.bridge.capture_core_reply(event)).unwrap()
        self.assertIn("[贴纸 😀]", captured.text)
        self.assertNotIn("Sticker:", captured.text)
        _, payload = await self.collect(event, FileServer({"s1": image_bytes("green")}))
        self.assertEqual(images(payload), [image_bytes("green")])

    async def test_quoted_photo_is_read_after_the_direct_photo(self):
        red, blue = image_bytes("red"), image_bytes("blue")
        original = {"message_id": 7, "date": 1700000000, "chat": {"type": "private", "id": int(USER_ID)},
                    "from": {"id": BOT_ID, "is_bot": True, "first_name": "Bot"}, "photo": photo("q1")}
        event = self.event(await self.convert(2, photo=photo("p1"), caption="compare", reply_to_message=original))
        with patch("companion_v2.conversation.media.models.MAX_TURN_BYTES", len(red) + 5):
            _, payload = await self.collect(event, FileServer({"p1": red, "q1": blue}))
        self.assertEqual(images(payload), [red])
        (reply,) = [part for part in event.message_obj.message if isinstance(part, Reply)]
        self.assertIn("附件超过读取上限", reply.chain[0].text)

    async def test_quoted_media_is_read_only_from_the_update_own_reply(self):
        original = {"message_id": 7, "date": 1700000000, "chat": {"type": "private", "id": int(USER_ID)},
                    "from": {"id": int(USER_ID), "is_bot": False, "first_name": "Synthetic"}, "photo": photo("q1")}
        event = self.event(await self.convert(2, text="look", reply_to_message=original))
        (reply,) = [part for part in event.message_obj.message if isinstance(part, Reply)]
        reply.sender_id = "987654321"
        server = FileServer({"q1": image_bytes()})
        _, payload = await self.collect(event, server)
        self.assertEqual((server.requests, images(payload)), ([], []))
        self.assertEqual(reply.chain[0].text, "[附件不可用]")

    async def test_quoted_video_follows_the_window_setting(self):
        original = {"message_id": 7, "date": 1700000000, "chat": {"type": "private", "id": int(USER_ID)},
                    "from": {"id": int(USER_ID), "is_bot": False, "first_name": "Synthetic"},
                    "video": dict(file_id="v1", file_unique_id="v1", width=64, height=48, duration=1)}
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                event = self.event(await self.convert(2, text="what is this", reply_to_message=original))
                server = FileServer({"v1": b"movie"})
                with self.settings(video_understanding_quoted_enabled=enabled):
                    _, payload = await self.collect(event, server)
                (reply,) = [part for part in event.message_obj.message if isinstance(part, Reply)]
                self.assertEqual(server.requests, ["v1"] if enabled else [])
                if enabled:
                    self.assertIn("Video Attachment in quoted message", text(payload))
                else:
                    self.assertEqual(reply.chain[0].text, "[引用视频理解未启用，本轮未读取]")
                self.assert_no_token(json.dumps(payload, ensure_ascii=False))

    async def test_credentials_left_in_the_provider_request_are_removed(self):
        from astrbot.core.agent.message import ImageURLPart, TextPart, ThinkPart, ToolCall
        from astrbot.core.provider.entities import ProviderRequest
        from tests.test_main_no_reply import ENTRY

        event = self.event(await self.convert(text="hello"))
        url = f"{self.platform.client.base_file_url}/media/v1"
        inline = ImageURLPart(image_url=ImageURLPart.ImageURL(url="data:image/png;base64,AAAA", id=url))
        signed = ThinkPart(think="thinking", encrypted=url)
        call = ToolCall(id="c1", function=ToolCall.FunctionBody(name="look", arguments=json.dumps({"url": url})),
                        extra_content={"refs": (url,)})
        history = [{"role": "user", "content": url},
                   {"role": "assistant", "content": "", "tool_calls": [call, {
                       "id": "c2", "type": "function", "function": {"name": "look", "arguments": "{}"},
                       "extra_content": {url: True}}]}]
        request = ProviderRequest(prompt=url, contexts=history, image_urls=[url],
                                  extra_user_content_parts=[TextPart(text=url), ImageURLPart(
                                      image_url=ImageURLPart.ImageURL(url=url)), inline, signed])
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        for _ in range(2):
            await plugin.protect_companion_telegram_request(event, request)
        texts = [part.text for part in request.extra_user_content_parts if isinstance(part, TextPart)]
        # The image fetched by URL and the signed thinking part are each replaced once.
        self.assertEqual(texts.count("[附件不可用]"), 2)
        self.assertEqual(request.image_urls, [])
        self.assertIn(inline, request.extra_user_content_parts)
        self.assertIsInstance(request.contexts[1]["tool_calls"][0], ToolCall)
        self.assertNotIn(self.token, request.contexts[1]["tool_calls"][0].model_dump_json())
        self.assert_no_token(request.prompt, request.contexts, request.extra_user_content_parts,
                             (await request.assemble_context())["content"])

    async def test_messages_assembled_after_the_request_hooks_are_cleaned(self):
        from astrbot.core.agent.message import Message, TextPart, ThinkPart, dump_messages_with_checkpoints
        from tests.test_main_no_reply import ENTRY

        event = self.event(await self.convert(text="hello"))
        # A local-mode Bot API server keeps files under a directory named by the token; AstrBot
        # rebuilds its image notice from that cached path after every request hook has run.
        notice = f"[Image Attachment: original path /srv/telegram-bot-api/{self.token}/photos/f.jpg; not included]"
        turn_only = TextPart(text="<companion_v2_native_memory>this turn only</companion_v2_native_memory>")
        user = Message(role="user", content=[TextPart(text="hello"), turn_only.mark_as_temp(),
                                             TextPart(text=notice).mark_as_temp()])
        signed = Message(role="assistant", content=[ThinkPart(think="ok", encrypted=f"sig-{self.token}")])
        run_context = NS(messages=[Message(role="system", content="be kind"), user, signed])
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        await plugin.protect_companion_telegram_messages(event, run_context)
        self.assertEqual(run_context.messages[0].content, "be kind")
        self.assert_no_token(run_context.messages, [message.model_dump_json() for message in run_context.messages])
        # Cleaned in place: AstrBot's turn-only marks still keep this turn's context out of history.
        self.assertIs(run_context.messages[1], user)
        self.assertEqual([getattr(part, "_no_save", False) for part in user.content], [False, True, True])
        saved = json.dumps(dump_messages_with_checkpoints(run_context.messages), ensure_ascii=False)
        self.assertNotIn("this turn only", saved)
        self.assertEqual([type(part).__name__ for part in run_context.messages[2].content], ["TextPart"])


if __name__ == "__main__":
    unittest.main()
