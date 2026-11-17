from datetime import datetime, timezone
from types import SimpleNamespace
import json
import unittest
from unittest.mock import AsyncMock, patch

from companion_v2.adapters.astrbot.tools import QzoneToolPort
from companion_v2.conversation.external_tools import ExternalToolInvocation
from companion_v2.execution.models import TaskError
from companion_v2.qzone.capability import PUBLISH_ACTION, READ_ACTION, tool_descriptors


NOW = datetime(2026, 11, 16, 0, 0, tzinfo=timezone.utc)


def invocation_for(action, arguments):
    descriptor = next(item for item in tool_descriptors() if item.tool.action == action)
    context = SimpleNamespace(key="origin-qzone", scope=SimpleNamespace())
    return ExternalToolInvocation(
        invocation_id="invocation-qzone",
        parent_task_id="task-qzone",
        tool_call_id="call-qzone",
        context=context,
        descriptor=descriptor,
        arguments_json=json.dumps(arguments),
        deadline_at=NOW,
    )


def publish_invocation():
    return invocation_for(PUBLISH_ACTION,
                          {"text": "今天很好", "topic": "life_documentary", "scene": "role_activity",
                           "confidence": 0.9, "image_ids": ["imgart_example"]})


class QzoneToolPortFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_resolution_failure_is_returned_with_stable_error_code(self):
        service = SimpleNamespace(now=lambda: NOW)
        service.publish = AsyncMock()
        application = SimpleNamespace(qzone=service)
        port = QzoneToolPort(object())
        port.application = application
        invocation = publish_invocation()

        with patch("companion_v2.qzone.images.album_images",
                   side_effect=TaskError("qzone_image_invalid", "图片尚未通过读取校验")):
            response = await port.invoke("qzone:" + PUBLISH_ACTION, invocation)

        self.assertEqual((response.state, response.effect, response.error_code),
                         ("failed", "not_committed", "qzone_image_invalid"))
        self.assertIsNotNone(response.receipt_ref)
        service.publish.assert_not_awaited()

    async def test_unknown_delivery_is_returned_with_stable_error_code(self):
        service = SimpleNamespace(now=lambda: NOW)
        service.publish = AsyncMock(side_effect=TaskError(
            "qzone_delivery_unknown", "说说是否发出未知；不会自动重发"))
        application = SimpleNamespace(qzone=service)
        port = QzoneToolPort(object())
        port.application = application
        invocation = publish_invocation()

        with patch("companion_v2.qzone.images.album_images", return_value=((), ())):
            response = await port.invoke("qzone:" + PUBLISH_ACTION, invocation)

        self.assertEqual((response.state, response.effect, response.error_code),
                         ("unknown", "unknown", "qzone_delivery_unknown"))
        self.assertEqual(response.retry_disposition, "external_effect_unknown")
        self.assertIsNone(response.receipt_ref)

    async def test_topic_confirmation_failure_has_receipt_and_requires_human_action(self):
        service = SimpleNamespace(now=lambda: NOW)
        service.publish = AsyncMock(side_effect=TaskError(
            "qzone_topic_confirmation_required", "需要人工确认主题和场景"))
        application = SimpleNamespace(qzone=service)
        port = QzoneToolPort(object())
        port.application = application

        with patch("companion_v2.qzone.images.album_images", return_value=((), ())):
            response = await port.invoke("qzone:" + PUBLISH_ACTION, publish_invocation())

        self.assertEqual((response.state, response.effect, response.error_code,
                          response.retry_disposition),
                         ("failed", "not_committed", "qzone_topic_confirmation_required",
                          "human_confirmation_required"))
        self.assertIsNotNone(response.receipt_ref)

    async def test_unresolved_operation_maps_to_unknown_and_cannot_retry(self):
        service = SimpleNamespace(now=lambda: NOW)
        service.publish = AsyncMock(side_effect=TaskError(
            "qzone_operation_unresolved", "原操作结果未知"))
        application = SimpleNamespace(qzone=service)
        port = QzoneToolPort(object())
        port.application = application

        with patch("companion_v2.qzone.images.album_images", return_value=((), ())):
            response = await port.invoke("qzone:" + PUBLISH_ACTION, publish_invocation())

        self.assertEqual((response.state, response.effect, response.error_code,
                          response.retry_disposition),
                         ("unknown", "unknown", "qzone_operation_unresolved",
                          "external_effect_unknown"))
        self.assertIsNone(response.receipt_ref)

    async def test_read_failure_keeps_a_non_effectful_contract_and_error_code(self):
        service = SimpleNamespace(now=lambda: NOW)
        service.scan = AsyncMock(side_effect=TaskError("qzone_auth_blocked", "请刷新 QQ 空间凭据"))
        application = SimpleNamespace(qzone=service)
        port = QzoneToolPort(object())
        port.application = application
        invocation = invocation_for(READ_ACTION, {"scope": "friends", "limit": 1, "detail": False})

        response = await port.invoke("qzone:" + READ_ACTION, invocation)

        self.assertEqual((response.state, response.effect, response.error_code),
                         ("failed", "none", "qzone_auth_blocked"))
        self.assertIsNotNone(response.receipt_ref)


if __name__ == "__main__":
    unittest.main()
