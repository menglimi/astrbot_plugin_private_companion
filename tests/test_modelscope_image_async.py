"""ModelScope's asynchronous image task protocol is polled, not reported as unconfirmed."""
import asyncio
import json
import unittest
from unittest import mock

from astrbot_host.native_extensions.image_native import output_routing
from astrbot_host.native_extensions.image_native.output_routing import OnlineImageBackend
from astrbot_host.native_extensions.image_native.types import ImageInvocation, NativeImageError

PNG = b"\x89PNG\r\n\x1a\n-modelscope-image"


class _Content:
    def __init__(self, payload):
        self.payload = payload

    async def iter_chunked(self, size):
        for index in range(0, len(self.payload), size):
            yield self.payload[index:index + size]


class _Response:
    def __init__(self, payload=b"", status=200):
        self.status, self.payload = status, payload
        self.content = _Content(payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _Session:
    def __init__(self, http, timeout=None, trust_env=None):
        self.http = http

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def post(self, url, **kwargs):
        return self.http.respond("POST", url, kwargs)

    def get(self, url, **kwargs):
        return self.http.respond("GET", url, kwargs)


class _HTTP:
    def __init__(self, get_payloads):
        self.get_payloads = list(get_payloads)
        self.calls = []

    def session(self, *, timeout=None, trust_env=None):
        return _Session(self, timeout, trust_env)

    def respond(self, method, url, kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        if method == "POST":
            return _Response(json.dumps({"task_id": "task-1"}).encode())
        if url.endswith("/tasks/task-1"):
            return _Response(json.dumps(self.get_payloads.pop(0)).encode())
        return _Response(PNG)

    def call(self, method):
        return next(call for call in self.calls if call["method"] == method)


def endpoint(**overrides):
    value = {"id": "modelscope-1", "name": "魔搭", "enabled": True, "platform": "modelscope",
             "model": "Qwen/Qwen-Image", "width": 1024, "height": 1024,
             "size_label": "1024x1024", "quality": "", "response_format": "",
             "max_reference_images": 16, "base_url": "https://api-inference.modelscope.cn/v1",
             "api_key": "secret", "timeout_seconds": 30, "poll_interval_seconds": 1,
             "ratio": "", "custom_headers": {}}
    value.update(overrides)
    return value


class ModelScopeAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, http, config=None, sleep=None):
        sleep = sleep or mock.AsyncMock()
        with mock.patch.object(output_routing.aiohttp, "ClientSession", http.session), \
             mock.patch.object(output_routing.asyncio, "sleep", new=sleep):
            backend = OnlineImageBackend(config or endpoint())
            self.assertEqual(backend.operations, ("generate",))
            invocation = ImageInvocation("scope", "request", "invocation", "origin", "generate", "a cat")
            return await backend.execute(invocation, (), mock.AsyncMock())

    async def test_submit_uses_async_header_and_polls_task_then_downloads(self):
        http = _HTTP([
            {"task_status": "RUNNING"},
            {"task_status": "SUCCEED", "output_images": ["https://cdn.example.test/out.png"]},
        ])
        result = await self.execute(http)
        self.assertEqual(result.images, (PNG,))
        post = http.call("POST")
        self.assertEqual(post["kwargs"]["headers"]["X-ModelScope-Async-Mode"], "true")
        self.assertEqual(post["kwargs"]["json"], {"model": "Qwen/Qwen-Image", "prompt": "a cat"})
        task_get = http.call("GET")
        self.assertEqual(task_get["kwargs"]["headers"]["X-ModelScope-Task-Type"], "image_generation")
        self.assertEqual(result.receipt["task_id"], "task-1")
        self.assertEqual(result.receipt["task_status"], "SUCCEED")

    async def test_failed_task_is_a_confirmed_failure_not_unknown(self):
        http = _HTTP([{"task_status": "FAILED", "error": "quota"}])
        with self.assertRaises(NativeImageError) as caught:
            await self.execute(http)
        self.assertEqual(caught.exception.code, "backend_result_failed")
        self.assertFalse(caught.exception.unconfirmed)

    async def test_interval_is_read_from_endpoint_configuration(self):
        http = _HTTP([
            {"task_status": "RUNNING"},
            {"task_status": "SUCCEEDED", "output_images": ["https://cdn.example.test/out.png"]},
        ])
        sleep = mock.AsyncMock()
        await self.execute(http, endpoint(poll_interval_seconds=3), sleep=sleep)
        sleep.assert_awaited_with(3)


if __name__ == "__main__":
    unittest.main()
