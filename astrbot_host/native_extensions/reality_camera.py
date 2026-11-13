"""Gate one Reality camera observation on a direct request in the current private turn."""

import json
import re

from companion_v2.capabilities.native_reads import check_no_credentials
from companion_v2.capabilities.privacy import is_core
from companion_v2.conversation.messages import event_from_document
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind


ACTION = "reality.camera.observe"
_CAMERA_NAMED = re.compile(r"(?:摄像头|相机|camera|webcam)", re.I)
_OBSERVATION_REQUEST = re.compile(r"(?:看(?:看|一下|下)?|瞧(?:瞧|一下)?|检查|确认|判断|拍(?:一张|一下)?)", re.I)
_IMPLICIT_SELF_REQUEST = re.compile(
    r"(?:看(?:看|一下|下)?|瞧(?:瞧|一下)?)(?:我|下我|一下我)"
    r"(?:现在|有没有|是否|在不在|在干|在做|在吃|吃|喝|睡|忙)", re.I)


def explicit_camera_request(text):
    compact = re.sub(r"\s+", "", str(text or "")).casefold()
    return bool(compact and (
        _CAMERA_NAMED.search(compact) and _OBSERVATION_REQUEST.search(compact)
        or _IMPLICIT_SELF_REQUEST.search(compact)
    ))


class RealityCameraGuard:
    def __init__(self, registry, application):
        self.registry = registry
        self.application = application

    def _latest_input(self, invocation):
        context = invocation.context
        # Camera is private-only for ordinary members.  The installation's core
        # administrator may explicitly ask in a group they own; that exception
        # is identity-bound and still requires the latest message to name the
        # camera, so a stale or third-party group turn cannot open it.
        if context.scope.kind is not ScopeKind.PRIVATE and not is_core(
                self.application.runtime, context.actor.subject):
            raise TaskError("reality_camera_private_required", "camera observations require a private conversation")
        messages = self.application.messages
        from companion_v2.conversation.native_tool_sources import input_row, native_invocation, native_turn
        if native_turn(invocation.parent_task_id):
            native_invocation(self.application.runtime, invocation)
            row = input_row(messages, context, invocation.parent_task_id)
            from companion_v2.conversation.messages import source_from_document
            reference = source_from_document(json.loads(row["source_json"]))
            source = messages._read_source(reference, context, "text", self.registry.now())
            if not explicit_camera_request(source.payload):
                raise TaskError("reality_camera_explicit_request_required",
                                "the latest private message must explicitly ask for a camera observation")
            return row["observation_id"], row["content_hash"], source.content_hash
        batches = self.application.runtime.read_all(
            "SELECT batch_id FROM conversation_batches WHERE generation_task_id = ? AND origin_key = ? LIMIT 2",
            (invocation.parent_task_id, context.key))
        if len(batches) != 1:
            raise TaskError("reality_camera_request_invalid", "the active private turn could not be identified")
        batch = messages.batch(batches[0]["batch_id"])
        if (batch["origin_key"] != context.key
                or batch["generation_task_id"] != invocation.parent_task_id
                or batch["state"] not in ("sealed", "ready")):
            raise TaskError("reality_camera_request_invalid", "camera requests must belong to the active private turn")
        row = self.application.runtime.read_one(
            """SELECT event_key, event_json, event_hash, withdrawn FROM conversation_inputs
               WHERE batch_id = ? ORDER BY position DESC LIMIT 1""",
            (batch["batch_id"],))
        if row is None or row["withdrawn"]:
            raise TaskError("reality_camera_request_invalid", "the current private message is unavailable")
        try:
            event = event_from_document(json.loads(row["event_json"]), context)
            if (event.scoped_key != row["event_key"] or event.scope != context.scope
                    or event.principal != context.actor.principal):
                raise ValueError("input identity changed")
            source = messages._read_source(event.source, context, event.capability, self.registry.now())
            if source.content_hash != event.input_hash:
                raise ValueError("input material changed")
        except TaskError:
            raise
        except (TypeError, ValueError, KeyError, UnicodeError) as error:
            raise TaskError("reality_camera_request_invalid", "the original private message could not be verified") from error
        if not explicit_camera_request(source.payload):
            raise TaskError("reality_camera_explicit_request_required",
                            "the latest private message must explicitly ask for a camera observation")
        return row["event_key"], row["event_hash"], source.content_hash

    def preflight(self, invocation):
        return self._latest_input(invocation)

    def recheck(self, invocation, stamp):
        if self._latest_input(invocation) != stamp:
            raise TaskError("reality_camera_request_changed", "the original camera request changed before capture")


class RealityCameraConsumer:
    def __init__(self, registry):
        self.registry = registry

    def adopt(self, invocation, native):
        result = native.result
        if (type(result) is not dict or set(result) != {
                "status", "captured", "answer_available", "purpose", "observation", "message",
                "final_response_instruction"}
                or result["status"] not in ("success", "observation_uncertain")
                or result["captured"] is not True
                or type(result["answer_available"]) is not bool
                or type(result["purpose"]) is not str
                or type(result["observation"]) is not dict
                or type(result["message"]) is not str
                or type(result["final_response_instruction"]) is not str):
            raise TaskError("reality_camera_result_invalid", "Reality returned an invalid camera observation")
        if (result["status"] == "success") != result["answer_available"]:
            raise TaskError("reality_camera_result_invalid", "camera certainty does not match its receipt")
        check_no_credentials(result, action=ACTION)
        if any(key in json.dumps(result, ensure_ascii=False).casefold()
               for key in ("data:image/", "jpeg_bytes", "image_base64", "preview_data_url")):
            raise TaskError("reality_camera_result_invalid", "camera originals cannot enter conversation context")
        return self.registry._source_response(invocation, native)


__all__ = ["ACTION", "RealityCameraConsumer", "RealityCameraGuard", "explicit_camera_request"]
