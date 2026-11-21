"""Bounded group-directed original reads, with frozen attribution and attachments."""

import asyncio

from companion_v2.conversation.forwards.models import ForwardIntakePolicy
from companion_v2.conversation.quotes.models import message_number
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind
from .history import extract_history_messages
from .native import account_number, native_id
from .quote_content import capture_quote_content


def mention_text(value, account, *, bot_ids=()):
    if value == "all":
        return "[提及全体成员]"
    try:
        mentioned = native_id(value)
    except ValueError:
        return "[提及不可用]"
    bot_ids = {str(item) for item in (*bot_ids, account) if item not in (None, "")}
    return "[提及机器人]" if mentioned in bot_ids else "[提及账号 " + mentioned + "]"


async def read_group_quote(locator, *, client, scope, reverse_api_type, image_loader, audio_loader=None,
                           video_loader=None, intake=ForwardIntakePolicy()):
    try:
        if scope.kind is not ScopeKind.GROUP:
            raise TaskError("host_quote_scope_mismatch", "group history requires its original group route")
        identifier = message_number(locator.message_id)
        api, clients = client._api._wsr_api, client._wsr_api_clients
        connection = clients.get(scope.account_id)

        def current():
            if (type(api) is not reverse_api_type or api._api_clients is not clients or not connection
                    or client._api._wsr_api is not api or client._wsr_api_clients is not clients
                    or clients.get(scope.account_id) is not connection):
                raise TaskError("host_quote_account_changed", "original group account connection changed")

        current()
        result = await api.call_action("get_group_msg_history", self_id=scope.account_id,
            group_id=scope.native_scope_id, message_seq=identifier, count=1,
            reverse_order=False, parse_mult_msg=True)
        current()
        records = extract_history_messages(result)
        if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
            raise TaskError("host_quote_unavailable", "group-directed history must return one original message")
        raw = records[0]
        if (raw.get("message_type") != "group" or raw.get("anonymous") is not None
                or raw.get("sub_type") == "anonymous"
                or account_number(raw.get("self_id")) != scope.account_id
                or account_number(raw.get("group_id")) != scope.native_scope_id
                or message_number(raw.get("message_id")) != identifier):
            raise TaskError("host_quote_scope_mismatch", "quote is not the requested message in the original group")
        sender = account_number(raw.get("sender", {}).get("user_id"))
        if raw.get("user_id") is not None and account_number(raw["user_id"]) != sender:
            raise TaskError("host_quote_scope_mismatch", "quoted sender fields disagree")
        at = raw.get("time")
        sent_at = at if type(at) is int and 0 < at <= 253402300799 else None
        return await capture_quote_content(locator, sender, sent_at, raw.get("message"),
            current=current, image_loader=image_loader, audio_loader=audio_loader, video_loader=video_loader,
            mention=lambda value: mention_text(value, scope.account_id), intake=intake)
    except TaskError:
        raise
    except asyncio.TimeoutError:
        raise TaskError("host_quote_timeout", "group quote preparation did not complete in time") from None
    except Exception:
        raise TaskError("host_quote_unavailable", "original group quote could not be verified") from None
