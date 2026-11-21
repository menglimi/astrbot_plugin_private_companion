"""Programmatic Bilibili live turns on AstrBot's public agent service."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


LIVE_TURN_KIND = "bili_live_auto_reply"


def _live_turn_data(event: Any) -> Mapping[str, Any]:
    metadata = event.get_extra("agent_metadata", {})
    companion = metadata.get("companion") if isinstance(metadata, Mapping) else None
    return companion if isinstance(companion, Mapping) and companion.get("kind") == LIVE_TURN_KIND else {}


def is_bili_live_agent_turn(event: Any) -> bool:
    return bool(_live_turn_data(event))


def prepare_bili_live_agent_event(event: Any) -> None:
    """Keep the recall query on the wakeup event while the SDK input owns the prompt."""
    data = _live_turn_data(event)
    if not data:
        return
    event.set_extra("bili_live_auto_reply", True)
    event.set_extra("bili_live_events", data.get("events", []))
    recall_query = data.get("recall_query")
    if not isinstance(recall_query, str) or not recall_query.strip():
        return
    from astrbot.api.message_components import Plain

    event.message_str = recall_query
    event.message_obj.message_str = recall_query
    event.message_obj.raw_message = recall_query
    event.message_obj.message = [Plain(recall_query)]


async def run_bili_live_agent_turn(
    context: Any, *, umo: str, conversation_id: str, prompt: str,
    recall_query: str, events: list[Any], source_event: Any,
) -> list[tuple[str, str, Any]] | None:
    """Return only framework-prepared outputs; the caller owns platform delivery."""
    from astrbot.api.agents import PlainInput, TurnStatus, chain_from_content

    metadata = {"companion": {
        "kind": LIVE_TURN_KIND,
        "recall_query": recall_query,
        "events": [
            {
                "type": str(event.event_type),
                "username": str(event.username),
                "content": str(event.content),
                "event_id": str(event.event_id),
            }
            for event in events
        ],
    }}
    run = await context.agent.run_turn(
        umo=umo,
        input_message=[PlainInput(text=prompt)],
        send_to_user=False,
        source_event=source_event,
        session_id=conversation_id,
        metadata=metadata,
    )
    outputs = []
    async for runtime_event in run.events():
        if runtime_event.type != "output.ready":
            continue
        output = runtime_event.payload
        outputs.append((output.target_umo, output.source, chain_from_content(output.content)))
    result = await run.result()
    if result.status != TurnStatus.COMPLETED:
        return None
    return outputs
