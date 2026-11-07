"""One bounded, peer-directed OneBot history read, without importing the SDK."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from companion_v2.conversation.forwards.models import ForwardIntakePolicy
from companion_v2.conversation.quotes.models import message_number
from companion_v2.adapters.astrbot.native import NativeAudioLocator, NativeVideoLocator
from companion_v2.execution.models import TaskError

from .history import extract_history_messages
from .native import NativeImageLocator, account_number


@dataclass(frozen=True)
class NativeQuoteLocator:
    position: int
    message_id: str


def _host_temp_image(value):
    """Whether ``value`` is an existing file inside AstrBot's own temp directory.

    AstrBot's preprocess stage downloads every inbound image and rewrites the
    component's ``url`` and ``file`` to that absolute cache path. The image
    tools must keep refusing arbitrary network-message paths, but a file the
    host itself wrote under ``<data>/temp`` is already a trusted attachment.
    Resolve before comparing so ``..`` segments and symlinks cannot escape the
    directory, and fall back to the public-URL rule whenever the host module or
    the path is unavailable.
    """
    if type(value) is not str or not value or value.startswith(("base64://", "\\\\", "//")):
        return False
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
    except ImportError:
        return False
    try:
        path = Path(value)
        if not path.is_absolute():
            return False
        root = Path(get_astrbot_temp_path()).resolve()
        resolved = path.resolve()
        return resolved.is_file() and resolved.is_relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return False


def image_locator(position, file, url):
    # Inspect the protocol prefix before any path or filename operation. In
    # particular, keep base64 originals when the host also supplies a URL.
    value = file if type(file) is str and file.startswith("base64://") else url or file
    if type(value) is not str or not value:
        raise TaskError("host_image_unavailable", "image has no original host attachment")
    return NativeImageLocator(position, value, host_resolved=_host_temp_image(value))


def audio_locator(position, file, url):
    value = file if type(file) is str and file.startswith("base64://") else url or file
    if type(value) is not str or not value:
        raise TaskError("host_audio_unavailable", "voice has no original host attachment")
    return NativeAudioLocator(position, value)


def video_locator(position, file, url):
    value = file if type(file) is str and file.startswith("base64://") else url or file
    if type(value) is not str or not value:
        raise TaskError("host_video_unavailable", "video has no original host attachment")
    return NativeVideoLocator(position, value)


async def read_private_quote(locator, *, client, scope, reverse_api_type, image_loader, audio_loader=None,
                             video_loader=None, intake=ForwardIntakePolicy()):
    from .quote_content import capture_quote_content

    try:
        api = client._api._wsr_api
        clients = client._wsr_api_clients
        connection = clients.get(scope.account_id)
        def current():
            if (type(api) is not reverse_api_type or api._api_clients is not clients or not connection
                    or client._api._wsr_api is not api or client._wsr_api_clients is not clients
                    or clients.get(scope.account_id) is not connection):
                raise TaskError("host_quote_account_changed", "original quote account connection changed")
        current()
        # NapCat get_msg.user_id is the sender, including for outgoing messages.
        # get_friend_msg_history selects the peer separately and uses
        # getMsgsIncludeSelf. Verify the requested ID in its single result;
        # never fall back to unscoped get_msg, another account or a wider page.
        result = await api.call_action("get_friend_msg_history", self_id=scope.account_id,
            user_id=scope.native_scope_id, message_seq=message_number(locator.message_id), count=1,
            reverse_order=False, parse_mult_msg=True)
        current()
        records = extract_history_messages(result)
        if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
            raise TaskError("host_quote_unavailable", "peer-directed history did not return one original message")
        raw = records[0]
        if (raw.get("message_type") != "private" or raw.get("group_id") not in (None, "", 0, "0")
                or raw.get("sub_type", "friend") != "friend"
                or account_number(raw.get("self_id")) != scope.account_id
                or message_number(raw.get("message_id")) != locator.message_id):
            raise TaskError("host_quote_scope_mismatch", "quote is not the requested original private message")
        sender = account_number(raw.get("sender", {}).get("user_id"))
        if (sender not in (scope.native_scope_id, scope.account_id)
                or (raw.get("user_id") is not None and account_number(raw["user_id"]) != sender)):
            raise TaskError("host_quote_scope_mismatch", "quote sender does not belong to this private exchange")
        at = raw.get("time")
        sent_at = at if type(at) is int and 0 < at <= 253402300799 else None
        return await capture_quote_content(locator, sender, sent_at, raw.get("message"),
            current=current, image_loader=image_loader, audio_loader=audio_loader, video_loader=video_loader,
            intake=intake)
    except TaskError:
        raise
    except asyncio.TimeoutError:
        raise TaskError("host_quote_timeout", "quote preparation did not complete in time") from None
    except Exception:
        raise TaskError("host_quote_unavailable", "original private quote could not be verified") from None
