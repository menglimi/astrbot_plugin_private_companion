"""Offline contracts for the round-1 image generation protocol fixes.

Covers audit findings F-02 (``response_format`` / ``quality``), F-03 (the image
tool deadline versus the configured endpoint timeout) and F-04 (the final
request URL).  No real network is used: ``aiohttp.ClientSession`` and
``aiohttp.FormData`` are replaced by recording fakes, and configuration tests
use a temporary on-disk store.
"""

import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest import mock

import aiohttp

from astrbot_host.native_extensions.discovery import declarations_for_manifest
from astrbot_host.native_extensions.image_native import output_routing
from astrbot_host.native_extensions.image_native.adapter import PLUGIN_ID
from astrbot_host.native_extensions.image_native.extension import ImageExtensionAPI
from astrbot_host.native_extensions.image_native.output_configuration import (
    MAX_TASK_TIMEOUT_SECONDS, settings as output_settings)
from astrbot_host.native_extensions.image_native.output_routing import (
    XAI_MAX_REFERENCE_IMAGES, OnlineImageBackend, Rejected, effective_platform, resolve_platform,
    xai_files_url)
from astrbot_host.native_extensions.image_native.types import (MAX_REFERENCE_IMAGES as NATIVE_REFERENCE_IMAGES,
                                                              ImageInvocation, NativeImageError, ReferenceImage)
from companion_v2.capabilities.native import parse_manifest
from companion_v2.conversation.media.models import MAX_REFERENCE_IMAGES as CORE_REFERENCE_IMAGES
from companion_v2.legacy.image_configuration_migration import convert_image_configuration
from companion_v2.product import PRODUCT_VERSION

PNG_BYTES = b"\x89PNG\r\n\x1a\n-synthetic-image-body"
DOWNLOADED_BYTES = b"\x89PNG\r\n\x1a\n-downloaded-image-body"


# --------------------------------------------------------------------------- #
# Recording aiohttp stand-ins: exact request capture, zero real sockets.
# --------------------------------------------------------------------------- #

class _Content:
    def __init__(self, payload):
        self.payload = payload

    async def iter_chunked(self, size):
        for index in range(0, len(self.payload), size):
            yield self.payload[index:index + size]


class FakeResponse:
    def __init__(self, payload=b"", status=200):
        self.status = status
        self.payload = payload
        self.content = _Content(payload)

    async def read(self):
        return self.payload


class _RequestCall:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *exc_info):
        return False


class _RecordingForm:
    """Stands in for ``aiohttp.FormData`` and keeps the multipart fields."""

    def __init__(self):
        self.fields = []

    def add_field(self, name, value, *, filename=None, content_type=None):
        self.fields.append({"name": name, "value": value, "filename": filename,
                            "content_type": content_type})

    def field_names(self):
        return [field["name"] for field in self.fields]


class FakeHTTP:
    """Canned responses plus a record of every aiohttp call."""

    def __init__(self):
        self.calls = []
        self.sessions = []
        self.replies = {}

    def reply(self, method, responder):
        self.replies[method.upper()] = responder

    def session(self, *, timeout=None, trust_env=None):
        self.sessions.append({"timeout": timeout, "trust_env": trust_env})
        return _FakeSession(self, timeout)

    def call(self, method):
        calls = [item for item in self.calls if item["method"] == method.upper()]
        if not calls:
            raise AssertionError(f"no {method} request was recorded: {self.calls!r}")
        return calls[-1]

    def _respond(self, method, url, kwargs):
        self.calls.append({"method": method.upper(), "url": url, "kwargs": kwargs})
        responder = self.replies.get(method.upper())
        if responder is None:
            raise AssertionError(f"unexpected {method} request to {url}")
        return responder(url, kwargs)


class _FakeSession:
    def __init__(self, http, timeout):
        self.http = http
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def post(self, url, **kwargs):
        return _RequestCall(self.http._respond("POST", url, kwargs))

    def delete(self, url, **kwargs):
        return _RequestCall(self.http._respond("DELETE", url, kwargs))

    def get(self, url, **kwargs):
        return _RequestCall(self.http._respond("GET", url, kwargs))


def json_reply(entries):
    return FakeResponse(json.dumps({"data": entries}).encode("utf-8"))


def b64_reply(payload=PNG_BYTES):
    return json_reply([{"b64_json": base64.b64encode(payload).decode("ascii")}])


def url_reply(url="https://images.example.test/result.png"):
    return json_reply([{"url": url}])


def download_reply(payload=DOWNLOADED_BYTES):
    return FakeResponse(payload)


def endpoint(**overrides):
    value = {"id": "primary", "name": "Primary", "enabled": True, "platform": "openai",
             "model": "gpt-image-1", "width": 1024, "height": 1024,
             "base_url": "https://api.example.test/v1", "api_key": "test-key",
             "timeout_seconds": 60, "quality": "", "response_format": "",
             "max_reference_images": 1, "size_label": "1024x1024"}
    value.update(overrides)
    return value


def legacy_endpoint(**overrides):
    """An endpoint document from before the protocol fields existed."""
    value = {key: item for key, item in endpoint().items()
             if key not in {"quality", "response_format"}}
    value.update(overrides)
    return value


def invocation(operation="generate", prompt="A blue cup on a white table."):
    return ImageInvocation("scope-1", "request-1", "invocation-1", "origin-1", operation, prompt)


def reference():
    return ReferenceImage("ref-1", b"synthetic-reference", "source-1", "0" * 64)


class HTTPBackedTest(unittest.IsolatedAsyncioTestCase):
    """Run the real backend against recording aiohttp stand-ins."""

    def setUp(self):
        self.http = FakeHTTP()
        session_patch = mock.patch.object(output_routing.aiohttp, "ClientSession", self.http.session)
        form_patch = mock.patch.object(output_routing.aiohttp, "FormData", _RecordingForm)
        session_patch.start()
        form_patch.start()
        self.addCleanup(form_patch.stop)
        self.addCleanup(session_patch.stop)

    async def execute(self, config, operation="generate", references=()):
        backend = OnlineImageBackend(config)

        async def before_dispatch():
            return None

        return await backend.execute(invocation(operation), tuple(references), before_dispatch)

    def request_json(self):
        call = self.http.call("POST")
        self.assertIn("json", call["kwargs"], call["kwargs"])
        return call["kwargs"]["json"]

    def posted_form(self):
        call = self.http.call("POST")
        self.assertIn("data", call["kwargs"], call["kwargs"])
        return call["kwargs"]["data"]


class GeneratePayloadTests(HTTPBackedTest):
    async def test_default_openai_generation_omits_response_format_and_quality(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())

        result = await self.execute(endpoint())

        body = self.request_json()
        self.assertEqual(body, {"model": "gpt-image-1", "prompt": "A blue cup on a white table.",
                                "n": 1, "size": "1024x1024"})
        self.assertNotIn("response_format", body)
        self.assertNotIn("quality", body)
        self.assertEqual(result.images, (PNG_BYTES,))
        self.assertEqual(self.http.call("POST")["url"],
                         "https://api.example.test/v1/images/generations")

    async def test_legacy_dall_e_opt_in_is_the_only_source_of_response_format(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())

        # A dall-e endpoint keeps its explicit legacy vocabulary; the official
        # GPT Image family is covered by tests/test_r1_image_round1fix.py.
        await self.execute(endpoint(model="dall-e-3", response_format="b64_json", quality="hd"))

        body = self.request_json()
        self.assertEqual(body["response_format"], "b64_json")
        self.assertEqual(body["quality"], "hd")

    async def test_explicit_quality_is_sent_without_inventing_a_response_format(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())

        await self.execute(endpoint(quality="auto"))

        body = self.request_json()
        self.assertEqual(body["quality"], "auto")
        self.assertNotIn("response_format", body)

    async def test_minimax_keeps_its_own_generation_protocol(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())

        await self.execute(endpoint(platform="minimax", model="image-01", base_url="https://api.minimax.chat/v1",
                                    quality="hd", response_format="b64_json"))

        body = self.request_json()
        self.assertEqual(body, {"model": "image-01", "prompt": "A blue cup on a white table.", "n": 1,
                                "aspect_ratio": "1:1", "response_format": "base64"})
        self.assertEqual(self.http.call("POST")["url"], "https://api.minimax.chat/v1/image_generation")

    async def test_xai_generation_replaces_the_openai_size_with_a_native_ratio(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())

        await self.execute(endpoint(platform="openai", model="grok-imagine-image",
                                    base_url="https://api.x.ai/v1", width=1536, height=1024))

        body = self.request_json()
        self.assertEqual(body, {"model": "grok-imagine-image",
                                "prompt": "A blue cup on a white table.",
                                "n": 1, "aspect_ratio": "3:2"})
        self.assertNotIn("size", body)
        self.assertEqual(self.http.call("POST")["url"],
                         "https://api.x.ai/v1/images/generations")

    async def test_xai_regional_host_uses_the_same_native_protocol(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())

        await self.execute(endpoint(model="grok-imagine-image",
                                    base_url="https://us.api.x.ai/v1"))

        body = self.request_json()
        self.assertEqual(body.get("aspect_ratio"), "1:1")
        self.assertNotIn("size", body)

    async def test_xai_canvas_without_an_exact_ratio_sends_no_dimensions(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())

        await self.execute(endpoint(model="grok-imagine-image",
                                    base_url="https://api.x.ai/v1", width=1365, height=1024))

        body = self.request_json()
        self.assertNotIn("size", body)
        self.assertNotIn("aspect_ratio", body)

    async def test_lookalike_host_keeps_the_openai_size(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())

        await self.execute(endpoint(model="grok-imagine-image",
                                    base_url="https://api.notx.ai/v1"))

        self.assertIn("size", self.request_json())

    async def test_b64_json_and_url_responses_are_both_accepted(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())
        inline = await self.execute(endpoint())
        self.assertEqual(inline.images, (PNG_BYTES,))

        self.http.reply("POST", lambda url, kwargs: url_reply())
        self.http.reply("GET", lambda url, kwargs: download_reply())
        self.http.calls.clear()
        remote = await self.execute(endpoint())

        self.assertEqual(remote.images, (DOWNLOADED_BYTES,))
        self.assertEqual(self.http.call("GET")["url"], "https://images.example.test/result.png")
        self.assertEqual(self.http.call("GET")["kwargs"]["allow_redirects"], False)

    async def test_edit_multipart_omits_the_response_format_by_default(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())

        await self.execute(endpoint(), operation="edit", references=[reference()])

        form = self.posted_form()
        self.assertEqual(form.field_names(), ["model", "prompt", "n", "size", "image"])
        self.assertEqual(self.http.call("POST")["url"], "https://api.example.test/v1/images/edits")

    async def test_dual_reference_upload_order_and_roles_reach_the_provider_prompt(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())
        references = [
            ReferenceImage("pose-ref", b"pose-reference", "pose-source", "1" * 64, "pose"),
            ReferenceImage("identity-ref", b"identity-reference", "identity-source", "2" * 64, "identity"),
        ]

        await self.execute(endpoint(max_reference_images=2), operation="edit", references=references)

        form = self.posted_form()
        self.assertEqual(form.field_names(), ["model", "prompt", "n", "size", "image[]", "image[]"])
        prompt = form.fields[1]["value"]
        self.assertIn("image 1 = pose", prompt)
        self.assertIn("image 2 = identity", prompt)
        self.assertLess(prompt.index("image 1 = pose"), prompt.index("image 2 = identity"))
        self.assertTrue(prompt.endswith("User request:\nA blue cup on a white table."))
        self.assertEqual([field["value"] for field in form.fields if field["name"] == "image[]"],
                         [b"pose-reference", b"identity-reference"])

    async def test_edit_multipart_honours_the_legacy_opt_in(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())

        await self.execute(endpoint(model="dall-e-3", response_format="b64_json"), operation="edit",
                           references=[reference()])

        self.assertEqual(self.posted_form().field_names(),
                         ["model", "prompt", "n", "size", "response_format", "image"])
        self.assertEqual(self.http.call("POST")["kwargs"]["data"].fields[4]["value"], "b64_json")

    async def test_rejected_endpoint_keeps_the_existing_rejection_contract(self):
        self.http.reply("POST", lambda url, kwargs: FakeResponse(b"{}", status=400))

        with self.assertRaises(Rejected):
            await self.execute(endpoint())


class XaiProtocolTests(HTTPBackedTest):
    """Grok Imagine's native contract: no ``size``, Files API edits, capped refs."""

    @staticmethod
    def file_reply(file_id):
        return FakeResponse(json.dumps({"id": file_id, "object": "file"}).encode("utf-8"))

    async def test_generation_sends_native_ratio_and_resolution(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())

        result = await self.execute(endpoint(platform="xai", model="grok-imagine-image",
                                             base_url="https://api.x.ai/v1",
                                             width=1536, height=1024, size_label="2k"))

        body = self.request_json()
        self.assertEqual(body, {"model": "grok-imagine-image",
                                "prompt": "A blue cup on a white table.",
                                "n": 1, "aspect_ratio": "3:2", "resolution": "2k"})
        self.assertNotIn("size", body)
        self.assertEqual(self.http.call("POST")["url"],
                         "https://api.x.ai/v1/images/generations")
        self.assertEqual(result.images, (PNG_BYTES,))

    async def test_canvas_without_an_exact_ratio_leaves_dimensions_to_the_provider(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())

        await self.execute(endpoint(platform="xai", model="grok-imagine-image",
                                    base_url="https://api.x.ai/v1",
                                    width=1365, height=1024, size_label="1024x1365"))

        body = self.request_json()
        self.assertNotIn("size", body)
        self.assertNotIn("aspect_ratio", body)
        self.assertNotIn("resolution", body)

    async def test_quality_only_reaches_the_grok_imagine_2_model(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())

        await self.execute(endpoint(platform="xai", model="grok-imagine-image",
                                    base_url="https://api.x.ai/v1", quality="low"))
        self.assertNotIn("quality", self.request_json())

        self.http.calls.clear()
        await self.execute(endpoint(platform="xai", model="grok-imagine-image-2.0",
                                    base_url="https://api.x.ai/v1", quality="low"))
        self.assertEqual(self.request_json()["quality"], "low")

    async def test_edit_uploads_references_then_posts_files_api_ids(self):
        uploads, deletes = [], []

        def responder(url, kwargs):
            if url.endswith("/files"):
                uploads.append({"url": url, "kwargs": kwargs})
                return self.file_reply("file_primary")
            return b64_reply()

        self.http.reply("POST", responder)
        self.http.reply("DELETE", lambda url, kwargs: deletes.append(url) or FakeResponse(b"{}"))

        result = await self.execute(endpoint(platform="xai", model="grok-imagine-image",
                                             base_url="https://api.x.ai/v1",
                                             width=1536, height=1024,
                                             max_reference_images=5),
                                    operation="edit", references=[reference()])

        self.assertEqual(uploads[0]["url"], "https://api.x.ai/v1/files")
        self.assertIn("Authorization", uploads[0]["kwargs"]["headers"])
        form = uploads[0]["kwargs"]["data"]
        self.assertEqual(form.field_names(), ["file"])
        self.assertEqual(form.fields[0]["content_type"], "image/png")
        body = self.request_json()
        self.assertEqual(body["image"], {"file_id": "file_primary"})
        self.assertNotIn("size", body)
        # Edits keep the source framing; the saved canvas is a generate-only hint.
        self.assertNotIn("aspect_ratio", body)
        self.assertEqual(self.http.call("POST")["url"], "https://api.x.ai/v1/images/edits")
        self.assertEqual(deletes, ["https://api.x.ai/v1/files/file_primary"])
        self.assertEqual(result.images, (PNG_BYTES,))

    async def test_multi_reference_edit_uses_the_images_array(self):
        ids = iter(("file_first", "file_second"))
        uploads = []

        def responder(url, kwargs):
            if url.endswith("/files"):
                uploads.append(url)
                return self.file_reply(next(ids))
            return b64_reply()

        self.http.reply("POST", responder)
        self.http.reply("DELETE", lambda url, kwargs: FakeResponse(b"{}"))
        references = [reference(), ReferenceImage("ref-2", b"second-reference", "source-2", "1" * 64)]

        await self.execute(endpoint(platform="xai", model="grok-imagine-image",
                                    base_url="https://api.x.ai/v1", max_reference_images=5),
                           operation="edit", references=references)

        body = self.request_json()
        self.assertEqual(body["images"], [{"file_id": "file_first"}, {"file_id": "file_second"}])
        self.assertNotIn("image", body)
        self.assertEqual(len(uploads), 2)

    async def test_edit_reference_limit_is_five_even_when_sixteen_is_saved(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())
        references = [ReferenceImage(f"ref-{index}", b"reference", f"source-{index}", "0" * 64)
                      for index in range(6)]

        with self.assertRaises(Rejected):
            await self.execute(endpoint(platform="xai", model="grok-imagine-image",
                                        base_url="https://api.x.ai/v1", max_reference_images=16),
                               operation="edit", references=references)

        self.assertEqual(self.http.calls, [])
        effective = endpoint(platform="xai", model="grok-imagine-image",
                             base_url="https://api.x.ai/v1", max_reference_images=16)
        self.assertEqual(OnlineImageBackend(effective).operations, ("generate", "edit"))
        self.assertEqual(XAI_MAX_REFERENCE_IMAGES, 5)

    async def test_zero_references_disables_the_edit_operation(self):
        value = endpoint(platform="xai", model="grok-imagine-image",
                         base_url="https://api.x.ai/v1", max_reference_images=0)

        self.assertEqual(OnlineImageBackend(value).operations, ("generate",))

    async def test_upload_refusal_is_a_definitive_endpoint_rejection(self):
        self.http.reply("POST", lambda url, kwargs: FakeResponse(b'{"error":"bad file"}', status=400))

        with self.assertRaises(Rejected):
            await self.execute(endpoint(platform="xai", model="grok-imagine-image",
                                        base_url="https://api.x.ai/v1", max_reference_images=5),
                               operation="edit", references=[reference()])

        self.assertEqual(self.http.call("POST")["url"], "https://api.x.ai/v1/files")

    async def test_upload_transport_failure_does_not_cross_the_edit_dispatch_fence(self):
        self.http.reply("POST", lambda url, kwargs: (_ for _ in ()).throw(aiohttp.ClientError()))
        events = []

        async def before_dispatch():
            events.append("dispatch")

        async def read_before_upload():
            events.append("read")

        before_dispatch.read = read_before_upload
        backend = OnlineImageBackend(endpoint(platform="xai", model="grok-imagine-image",
                                              base_url="https://api.x.ai/v1", max_reference_images=5))
        with self.assertRaises(Rejected) as raised:
            await backend.execute(invocation("edit"), (reference(),), before_dispatch)

        self.assertTrue(raised.exception.switchable)
        self.assertEqual(events, ["read"])
        self.assertEqual(self.http.call("POST")["url"], "https://api.x.ai/v1/files")

    async def test_upload_server_error_can_switch_without_retrying_an_edit(self):
        self.http.reply("POST", lambda url, kwargs: FakeResponse(b'{"error":"unavailable"}', status=503))

        with self.assertRaises(Rejected) as raised:
            await self.execute(endpoint(platform="xai", model="grok-imagine-image",
                                        base_url="https://api.x.ai/v1", max_reference_images=5),
                               operation="edit", references=[reference()])

        self.assertTrue(raised.exception.switchable)
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual([call["url"] for call in self.http.calls], ["https://api.x.ai/v1/files"])

    async def test_xai_host_auto_detection_and_legacy_openai_projection(self):
        self.assertEqual(resolve_platform({"platform": "auto", "base_url": "https://api.x.ai/v1",
                                           "model": "grok-imagine-image"}), "xai")
        self.assertEqual(resolve_platform({"platform": "auto", "base_url": "https://us.api.x.ai/v1",
                                           "model": "grok-imagine-image"}), "xai")
        self.assertEqual(effective_platform({"platform": "openai", "base_url": "https://api.x.ai/v1",
                                             "model": "grok-imagine-image"}), "xai")
        self.assertEqual(effective_platform({"platform": "openai",
                                             "base_url": "https://api.example.test/v1",
                                             "model": "gpt-image-1"}), "openai")
        plugin = SimpleNamespace(config={"native_v2": {"output": {
            "enabled": True, "backend": "online_api",
            "endpoints": [{"id": "grok", "enabled": True, "platform": "openai",
                           "base_url": "https://api.x.ai/v1", "model": "grok-imagine-image",
                           "width": 1024, "height": 1024}]}}})
        self.assertEqual(output_settings(plugin)["endpoints"][0]["platform"], "xai")

    def test_files_url_derives_from_a_root_or_a_complete_images_address(self):
        self.assertEqual(xai_files_url("https://api.x.ai/v1"), "https://api.x.ai/v1/files")
        self.assertEqual(xai_files_url("https://api.x.ai/v1/"), "https://api.x.ai/v1/files")
        self.assertEqual(xai_files_url("https://api.x.ai/v1/images/generations"),
                         "https://api.x.ai/v1/files")
        self.assertEqual(xai_files_url("https://api.x.ai/v1/images/edits?api-version=3"),
                         "https://api.x.ai/v1/files?api-version=3")
        self.assertEqual(xai_files_url("https://api.x.ai/v1", "file_primary"),
                         "https://api.x.ai/v1/files/file_primary")


class EndpointUrlTests(HTTPBackedTest):
    async def test_generate_url_matrix(self):
        cases = {
            "https://api.example.test/v1": "https://api.example.test/v1/images/generations",
            "https://api.example.test/v1/": "https://api.example.test/v1/images/generations",
            "https://api.example.test/v1/images/generations":
                "https://api.example.test/v1/images/generations",
            "https://api.example.test/v1/images/edits": "https://api.example.test/v1/images/generations",
            "https://api.example.test/v1/images/edits/": "https://api.example.test/v1/images/generations",
            "https://api.example.test/openai/deployments/d/images/generations?api-version=2024-02-01":
                "https://api.example.test/openai/deployments/d/images/generations?api-version=2024-02-01",
            "https://api.example.test/v1/images/generations?api-version=3":
                "https://api.example.test/v1/images/generations?api-version=3",
            "https://api.example.test/openai/v1": "https://api.example.test/openai/v1/images/generations",
        }
        self.http.reply("POST", lambda url, kwargs: b64_reply())
        for base, expected in cases.items():
            with self.subTest(base=base):
                self.http.calls.clear()
                await self.execute(endpoint(base_url=base))
                self.assertEqual(self.http.call("POST")["url"], expected)

    async def test_edit_url_matrix(self):
        cases = {
            "https://api.example.test/v1": "https://api.example.test/v1/images/edits",
            "https://api.example.test/v1/": "https://api.example.test/v1/images/edits",
            "https://api.example.test/v1/images/edits": "https://api.example.test/v1/images/edits",
            "https://api.example.test/v1/images/generations": "https://api.example.test/v1/images/edits",
            "https://api.example.test/v1/images/edits?api-version=7":
                "https://api.example.test/v1/images/edits?api-version=7",
            "https://api.example.test/openai/deployments/d/images/generations?api-version=3":
                "https://api.example.test/openai/deployments/d/images/edits?api-version=3",
        }
        self.http.reply("POST", lambda url, kwargs: b64_reply())
        for base, expected in cases.items():
            with self.subTest(base=base):
                self.http.calls.clear()
                await self.execute(endpoint(base_url=base), operation="edit", references=[reference()])
                self.assertEqual(self.http.call("POST")["url"], expected)

    async def test_minimax_full_endpoint_is_not_appended_twice(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())
        cases = {
            "https://api.minimax.chat/v1": "https://api.minimax.chat/v1/image_generation",
            "https://api.minimax.chat/v1/": "https://api.minimax.chat/v1/image_generation",
            "https://api.minimax.chat/v1/image_generation": "https://api.minimax.chat/v1/image_generation",
            "https://api.minimax.chat/v1/image_generation?group_id=1":
                "https://api.minimax.chat/v1/image_generation?group_id=1",
        }
        for base, expected in cases.items():
            with self.subTest(base=base):
                self.http.calls.clear()
                await self.execute(endpoint(platform="minimax", model="image-01", base_url=base))
                self.assertEqual(self.http.call("POST")["url"], expected)

    async def test_http_timeout_uses_the_configured_endpoint_value(self):
        self.http.reply("POST", lambda url, kwargs: b64_reply())

        await self.execute(endpoint(timeout_seconds=900))

        self.assertEqual(self.http.sessions[-1]["timeout"].total, 900)
        self.assertIs(self.http.sessions[-1]["trust_env"], False)


class ImageConfigurationContractTests(unittest.TestCase):
    """Saved/loaded configuration keeps page, schema and defaults aligned."""

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.api = ImageExtensionAPI(data_root=self.root, config_path=self.root / "image-config.json",
                                     version=PRODUCT_VERSION)
        self.addCleanup(self.api.close)

    def save(self, **changes):
        current = self.api.output_configuration()
        return self.api.save_output_configuration(current["revision"], changes)

    def test_new_endpoints_default_to_the_server_quality_and_no_forced_response_format(self):
        # A fresh page draft omits both keys, so the backend default applies.
        draft = {key: value for key, value in endpoint().items()
                 if key not in {"quality", "response_format"}}
        saved = self.save(enabled=True, backend="online_api", endpoints=[draft])

        projected = saved["settings"]["endpoints"][0]
        self.assertEqual(projected["quality"], "")
        self.assertEqual(projected["response_format"], "")

    def test_explicit_quality_and_response_format_survive_save_and_reread(self):
        # A dall-e endpoint keeps a deliberate legacy choice.  GPT Image
        # normalization is covered by tests/test_r1_image_round1fix.py.
        saved = self.save(enabled=True, backend="online_api",
                          endpoints=[endpoint(model="dall-e-3", quality="standard", response_format="b64_json")])

        self.assertEqual(saved["settings"]["endpoints"][0]["quality"], "standard")
        self.assertEqual(saved["settings"]["endpoints"][0]["response_format"], "b64_json")
        reread = self.api.output_configuration()["settings"]["endpoints"][0]
        self.assertEqual(reread["quality"], "standard")
        self.assertEqual(reread["response_format"], "b64_json")

    def test_quality_vocabulary_accepts_server_default_and_known_values(self):
        for quality in ("", "auto", "low", "medium", "high", "standard", "hd"):
            with self.subTest(quality=quality):
                saved = self.save(enabled=False, backend="online_api",
                                  endpoints=[endpoint(model="dall-e-3", enabled=False, quality=quality)])
                self.assertEqual(saved["settings"]["endpoints"][0]["quality"], quality)

    def test_unknown_quality_or_response_format_is_rejected(self):
        for changes in ({"quality": "ultra"}, {"response_format": "url"}):
            with self.subTest(changes=changes):
                with self.assertRaises(NativeImageError) as error:
                    self.save(enabled=False, backend="online_api",
                              endpoints=[endpoint(enabled=False, **changes)])
                self.assertEqual(error.exception.code, "invalid_configuration")

    def test_public_projection_with_empty_quality_can_be_saved_back_unchanged(self):
        saved = self.save(enabled=True, backend="online_api", endpoints=[endpoint()])
        current = self.api.output_configuration()

        replayed = self.api.save_output_configuration(current["revision"], current["settings"])

        self.assertEqual(replayed["settings"]["endpoints"][0]["quality"], "")
        self.assertEqual(replayed["settings"]["endpoints"][0]["response_format"], "")
        self.assertEqual(replayed["revision"], current["revision"])

    def test_pre_existing_saved_choices_are_not_rewritten(self):
        config_path = self.root / "existing.json"
        config_path.write_text(json.dumps({"native_v2": {"enabled": True, "backend": "auto", "output": {
            "enabled": False, "backend": "auto", "sd_webui": {}, "comfyui": {},
            "endpoints": [endpoint(model="dall-e-3", quality="standard", response_format="b64_json")],
        }}}), encoding="utf-8")
        api = ImageExtensionAPI(data_root=self.root / "existing", config_path=config_path,
                                version=PRODUCT_VERSION)
        self.addCleanup(api.close)

        projected = api.output_configuration()["settings"]["endpoints"][0]
        self.assertEqual(projected["quality"], "standard")
        self.assertEqual(projected["response_format"], "b64_json")

    def test_manifest_timeout_declares_the_documented_ceiling(self):
        # The manifest is the immutable catalog identity (see
        # tests/test_r1_image_round1fix.py); it declares the documented maximum
        # and the live per-task budget comes from the saved configuration.
        self.save(enabled=True, backend="online_api", endpoints=[legacy_endpoint(timeout_seconds=900)])

        provider = self.api.companion_v2_native()
        tools = provider.manifest()["tools"]

        self.assertTrue(tools)
        for tool in tools:
            self.assertEqual(tool["timeout_seconds"], MAX_TASK_TIMEOUT_SECONDS)
        self.assertGreaterEqual(provider.wait_budget_seconds, 900)
        self.assertEqual(provider.task_timeout_seconds(), 900)

    def test_manifest_timeout_keeps_the_default_floor(self):
        provider = self.api.companion_v2_native()

        for tool in provider.manifest()["tools"]:
            self.assertEqual(tool["timeout_seconds"], MAX_TASK_TIMEOUT_SECONDS)
        self.assertEqual(provider.wait_budget_seconds, 300)
        self.assertEqual(provider.task_timeout_seconds(), 300)

    def test_startup_reads_the_saved_endpoint_timeout_before_any_save(self):
        # ``build_provider`` passes a wait budget taken from a top-level key that
        # the output configuration never sets, so the provider itself must derive
        # the budget from the saved endpoints.
        config_path = self.root / "startup.json"
        config_path.write_text(json.dumps({"native_v2": {"enabled": True, "backend": "auto", "output": {
            "enabled": True, "backend": "online_api", "sd_webui": {}, "comfyui": {},
            "endpoints": [legacy_endpoint(timeout_seconds=900)],
            "operation_routes": {"generate": ["online:primary"], "edit": []},
        }}}), encoding="utf-8")
        api = ImageExtensionAPI(data_root=self.root / "startup", config_path=config_path,
                                version=PRODUCT_VERSION)
        self.addCleanup(api.close)

        provider = api.companion_v2_native()

        self.assertEqual(provider.wait_budget_seconds, 900)
        self.assertEqual(provider.task_timeout_seconds(), 900)
        for tool in provider.manifest()["tools"]:
            self.assertEqual(tool["timeout_seconds"], MAX_TASK_TIMEOUT_SECONDS)

    def test_saved_timeout_raises_the_next_task_budget(self):
        self.save(enabled=True, backend="online_api", endpoints=[legacy_endpoint(timeout_seconds=1200)])

        manifest = parse_manifest(self.api.companion_v2_native().manifest(),
                                  plugin_id=PLUGIN_ID, version=PRODUCT_VERSION)
        declarations = declarations_for_manifest(manifest)

        self.assertEqual(len(declarations), 3)
        for declaration in declarations:
            self.assertEqual(declaration.descriptor.timeout_seconds, MAX_TASK_TIMEOUT_SECONDS)
            self.assertEqual(declaration.descriptor.definition().timeout_seconds,
                             MAX_TASK_TIMEOUT_SECONDS)
        self.assertEqual(self.api.companion_v2_native().task_timeout_seconds(), 1200)

    def test_saving_does_not_pin_the_wait_budget_above_the_configured_timeout(self):
        self.save(enabled=True, backend="online_api", endpoints=[legacy_endpoint(timeout_seconds=60)])

        provider = self.api.companion_v2_native()

        self.assertEqual(provider.wait_budget_seconds, 300)
        self.assertEqual(provider.task_timeout_seconds(), 300)
        for tool in provider.manifest()["tools"]:
            self.assertEqual(tool["timeout_seconds"], MAX_TASK_TIMEOUT_SECONDS)

    def test_local_backend_timeout_also_raises_the_task_budget(self):
        self.save(enabled=True, backend="sd_webui", endpoints=[],
                  sd_webui={"base_url": "http://127.0.0.1:7860", "timeout_seconds": 900,
                            "parameters": {}})

        provider = self.api.companion_v2_native()

        self.assertGreaterEqual(provider.wait_budget_seconds, 900)
        self.assertEqual(provider.task_timeout_seconds(), 900)
        for tool in provider.manifest()["tools"]:
            self.assertEqual(tool["timeout_seconds"], MAX_TASK_TIMEOUT_SECONDS)


class MigrationContractTests(unittest.TestCase):
    def migrated(self, **overrides):
        item = {"name": "Legacy", "platform": "openai", "base_url": "https://api.example.test/v1",
                "model": "image-v1", "timeout_seconds": 120}
        item.update(overrides)
        return convert_image_configuration({"photo_action_config": {
            "photo_generation_backend": "external",
            "external_image_api_endpoints": [item],
        }})

    def migrated_endpoint(self, **overrides):
        return self.migrated(**overrides).output_changes["endpoints"][0]

    def test_migration_does_not_invent_the_standard_quality(self):
        migrated = self.migrated_endpoint()

        self.assertEqual(migrated["quality"], "")
        self.assertEqual(migrated["response_format"], "")

    def test_explicit_legacy_quality_is_preserved(self):
        migrated = self.migrated_endpoint(quality="hd")

        self.assertEqual(migrated["quality"], "hd")

    def test_invalid_legacy_quality_falls_back_to_the_server_default(self):
        result = self.migrated(quality="ultra")
        migrated = result.output_changes["endpoints"][0]

        self.assertEqual(migrated["quality"], "")
        # 降级必须留痕：差异记录被删掉时，这里要能报出来。
        self.assertEqual([(item["field"], item["kind"]) for item in result.differences],
                         [("image.output.endpoints.1.quality", "invalid_value")])


class ReferenceLimitContractTests(unittest.TestCase):
    def test_provider_and_core_share_the_sixteen_reference_ceiling(self):
        self.assertEqual(NATIVE_REFERENCE_IMAGES, CORE_REFERENCE_IMAGES)

    def test_edit_binds_at_most_sixteen_references(self):
        base = dict(scope_key="scope", request_id="request", invocation_id="invocation",
                    origin_key="origin", operation="edit", prompt="edit")
        ImageInvocation(**base, reference_ids=tuple("r%d" % index for index in range(16)))
        with self.assertRaises(NativeImageError):
            ImageInvocation(**base, reference_ids=tuple("r%d" % index for index in range(17)))

    def test_legacy_reference_switch_migrates_to_the_sixteen_image_default(self):
        result = convert_image_configuration({"photo_action_config": {
            "photo_generation_backend": "external",
            "external_image_api_endpoints": [{
                "name": "Legacy", "platform": "openai", "base_url": "https://api.example.test/v1",
                "model": "image-v1", "timeout_seconds": 120}],
        }})

        self.assertEqual(result.output_changes["endpoints"][0]["max_reference_images"], 16)


if __name__ == "__main__":
    unittest.main()
