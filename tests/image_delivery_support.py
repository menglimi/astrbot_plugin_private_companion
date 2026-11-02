"""Synthetic reply-image selections from host-offered evidence only."""

import json

from companion_v2.conversation.media.reply import OWNER
from tests.native_adapter_support import completion, final_text


def user_message(payload):
    return next((message for message in reversed(payload.get("messages", ()))
                 if message.get("role") == "user"), None)


def user_content(payload):
    message = user_message(payload)
    return message.get("content") if message is not None else None


def offered_images(payload):
    content = user_content(payload)
    result = []
    if isinstance(content, list):
        for part in content[1:]:
            if part["type"] == "text":
                handle = json.loads(part["text"]).get("sendable_image")
                if handle and handle not in result:
                    result.append(handle)
    return result


def image_reply(payload, *, text="把这张原图附在这里。", handles=None):
    value = json.loads(final_text(text))
    if text == "":
        value["action"] = "image"
        value["claims"] = []
    value["images"] = offered_images(payload) if handles is None else handles
    return completion(json.dumps(value, ensure_ascii=False))


async def attach_originals(payload, _):
    return image_reply(payload)


def intercept_image_delivery_verification(bridge, runtime, callback):
    verify_role = bridge.verify_role
    reached = False

    async def verify(*args, **kwargs):
        nonlocal reached
        if not reached and runtime.read_one("SELECT 1 FROM sources WHERE owner = ?", (OWNER,)):
            await callback(None)
            reached = True
        return await verify_role(*args, **kwargs)

    bridge.verify_role = verify
    return lambda: reached
