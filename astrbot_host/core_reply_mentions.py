"""Restore group mention positions in the host-owned, persisted user message."""

from collections.abc import Mapping
import re

from companion_v2.conversation.group_timing import SELF_MENTION


def restore_group_mentions(event, request, native_input, native_context):
    original = getattr(native_input, "group_message_text", None)
    plain = getattr(native_input, "text", None)
    prompt = getattr(request, "prompt", None)
    if not isinstance(original, str) or not isinstance(plain, str) or not isinstance(prompt, str):
        return
    if original == plain:
        return
    config = {}
    getter = getattr(native_context, "get_config", None)
    if callable(getter):
        config = getter(umo=getattr(event, "unified_msg_origin", None))
    provider = config.get("provider_settings", {}) if isinstance(config, Mapping) else {}
    prefixes = list(config.get("wake_prefix", ())) if getattr(event, "is_at_or_wake_command", False) else []
    prefixes.append(provider.get("wake_prefix", ""))
    variants = [(plain, original), (plain.strip(), original.strip())]
    if not plain.strip() and any(
            getattr(getattr(part, "type", None), "value", getattr(part, "type", None))
            in {"Reply", "Image", "Record", "Video", "File", "Forward"}
            for part in getattr(getattr(event, "message_obj", None), "message", ())):
        variants.append(("<attachment>", original))
    # Strip only configured command/provider prefixes. Keep a leading native
    # self mention when the command prefix followed that mention in the source.
    for prefix in prefixes:
        if not isinstance(prefix, str) or not prefix:
            continue
        for before, after in tuple(variants):
            if not before.startswith(prefix):
                continue
            leading = re.match(r"(?:" + re.escape(SELF_MENTION) + r"\s*)*", after).end()
            if after[leading:].startswith(prefix):
                remainder = after[:leading] + after[leading + len(prefix):]
                variants.extend(((before[len(prefix):], remainder),
                                 (before[len(prefix):].strip(), remainder.strip())))
    template = provider.get("prompt_prefix", "")
    for before, after in tuple(variants):
        if template:
            variants.append((template.replace("{{prompt}}", before), template.replace("{{prompt}}", after))
                if "{{prompt}}" in template else (template + before, template + after))
    for before, after in variants:
        if prompt == before:
            request.prompt = after
            return
