import asyncio
import base64
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image

from astrbot_host.media import PublicImageResolver, fetch_image, inspect_image, load_native_image, public_url
from companion_v2.adapters.astrbot.native import NativeImageLocator
from companion_v2.conversation.media.models import MAX_IMAGE_BYTES
from companion_v2.execution.models import TaskError
from tests.test_conversation_images import image_bytes


class StaticImageLoaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_png_jpeg_webp_bytes_and_dimensions_are_preserved(self):
        for kind in ("PNG", "JPEG", "WEBP"):
            data = image_bytes(kind=kind)
            captured = await load_native_image(NativeImageLocator(3, "base64://" + base64.b64encode(data).decode()))
            self.assertEqual(captured.content, data)
            self.assertEqual((captured.descriptor.width, captured.descriptor.height, captured.descriptor.position), (3, 2, 3))

    async def test_unsupported_encoding_invalid_bytes_and_size_have_explicit_results(self):
        cases = ((image_bytes(kind="BMP"), "host_image_format_unsupported"),
                 (b"not an image", "host_image_invalid"),
                 (b"a" * (MAX_IMAGE_BYTES + 1), "host_image_limit"))
        for data, code in cases:
            with self.assertRaises(TaskError) as caught:
                inspect_image(data, 0)
            self.assertEqual(caught.exception.code, code)
        with self.assertRaises(TaskError):
            await load_native_image(NativeImageLocator(0, "base64://broken!"))

    async def test_network_message_paths_and_private_urls_never_trigger_fetch(self):
        for locator in ("file:///C:/secret", "C:/secret", "../../secret", "https://127.0.0.1/image",
                        "http://169.254.169.254/image", "http://[::1]/image", "ftp://public.example/image",
                        "https://user:pass@public.example/image", "https://public.example:8000/image"):
            with self.subTest(locator=locator), patch("astrbot_host.media.aiohttp.ClientSession") as session:
                with self.assertRaises(TaskError):
                    await load_native_image(NativeImageLocator(0, locator))
                session.assert_not_called()
        self.assertEqual(public_url("https://public.example/image?download=1"), "https://public.example/image?download=1")

    async def test_only_host_resolved_absolute_cache_files_are_read_locally(self):
        source = image_bytes("green")
        with TemporaryDirectory() as directory:
            cached = Path(directory) / "quoted-image.png"
            cached.write_bytes(source)
            with patch("astrbot_host.media.aiohttp.ClientSession") as session:
                captured = await load_native_image(NativeImageLocator(4, str(cached), host_resolved=True))
                session.assert_not_called()
            self.assertEqual(captured.content, source)
            self.assertEqual(captured.descriptor.position, 4)

            # The same existing file remains forbidden without the trusted marker.
            with self.assertRaises(TaskError) as caught:
                await load_native_image(NativeImageLocator(0, str(cached)))
            self.assertEqual(caught.exception.code, "host_image_location_invalid")

    async def test_dns_result_used_by_connector_rejects_private_or_mixed_addresses(self):
        resolver = PublicImageResolver()
        self.addAsyncCleanup(resolver.close)
        for addresses in (["127.0.0.1"], ["8.8.8.8", "10.0.0.1"], ["::ffff:127.0.0.1"]):
            with patch("aiohttp.ThreadedResolver.resolve", AsyncMock(return_value=[{"host": v} for v in addresses])):
                with self.assertRaises(TaskError):
                    await resolver.resolve("public.example")
        with patch("aiohttp.ThreadedResolver.resolve", AsyncMock(return_value=[{"host": "8.8.8.8"}])):
            self.assertEqual(await resolver.resolve("public.example"), [{"host": "8.8.8.8"}])

    async def test_transparent_proxy_fake_ip_answers_are_public_but_literals_are_not(self):
        """Clash/mihomo fake-ip answers are proxy routes, not local services."""
        resolver = PublicImageResolver()
        self.addAsyncCleanup(resolver.close)
        for address in ("198.18.0.22", "198.19.255.254", "::ffff:198.18.0.22"):
            with self.subTest(address=address), patch(
                    "aiohttp.ThreadedResolver.resolve", AsyncMock(return_value=[{"host": address}])):
                self.assertEqual(await resolver.resolve("multimedia.nt.qq.com.cn"), [{"host": address}])
        for addresses in (["198.18.0.22", "10.0.0.1"], ["198.18.0.22", "127.0.0.1"]):
            with self.subTest(addresses=addresses), patch(
                    "aiohttp.ThreadedResolver.resolve", AsyncMock(return_value=[{"host": v} for v in addresses])):
                with self.assertRaises(TaskError):
                    await resolver.resolve("public.example")
        with self.assertRaises(TaskError) as caught:
            public_url("https://198.18.0.22/image")
        self.assertEqual(caught.exception.code, "host_image_location_invalid")

    async def test_obfuscated_numeric_hosts_cannot_bypass_the_checked_resolver(self):
        for host in ("2130706433", "127.1", "0177.0.0.1", "127.0.0.1.", "１２７.０.０.１"):
            with self.subTest(host=host), patch("astrbot_host.media.aiohttp.ClientSession") as session:
                with self.assertRaises(TaskError) as caught:
                    await load_native_image(NativeImageLocator(0, "http://" + host + "/image"))
                self.assertEqual(caught.exception.code, "host_image_location_invalid")
                session.assert_not_called()
        self.assertEqual(public_url("https://8.8.8.8/image"), "https://8.8.8.8/image")

    async def test_fetch_revalidates_redirects_and_stream_length(self):
        class Content:
            async def iter_chunked(self, _):
                yield b"x" * MAX_IMAGE_BYTES
                yield b"x"
        class Response:
            status, headers, content_length, content = 200, {}, None, Content()
            async def __aenter__(self): return self
            async def __aexit__(self, *_): pass
        class Session:
            def __init__(self, response): self.response, self.calls = response, []
            async def __aenter__(self): return self
            async def __aexit__(self, *_): pass
            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return self.response
        response = Response()
        session = Session(response)
        with patch("astrbot_host.media.aiohttp.TCPConnector"), patch("astrbot_host.media.aiohttp.ClientSession", return_value=session):
            with self.assertRaises(TaskError) as caught:
                await fetch_image("https://public.example/image")
            self.assertEqual(caught.exception.code, "host_image_limit")
            response.status, response.headers = 302, {"Location": "http://127.0.0.1/secret"}
            with self.assertRaises(TaskError) as caught:
                await fetch_image("https://public.example/image")
            self.assertEqual(caught.exception.code, "host_image_location_invalid")
        self.assertEqual(len(session.calls), 2)

    async def test_cancelled_loader_propagates_without_partial_success(self):
        with patch("astrbot_host.media.fetch_image", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await load_native_image(NativeImageLocator(0, "https://public.example/image"))
