"""Observe actual room text without creating an AstrBot reply or QQ receipt."""

from uuid import uuid4

from .native_context import current_room_context, resolve_room_context


def received_input(runtime, room, text, *, turn_id, visual_context_used=False):
    transcript = {"event_id": "together-room-" + room.room_id + "-" + turn_id,
                  "user_text": text, "visual_context_used": visual_context_used}
    if room.native_v2 is not None and runtime.record_visible_turns:
        _schedule(runtime, room, text, kind="input", transcript=transcript)
    return transcript


def delivered_output(runtime, room, payload, *, transcript=None):
    if payload.get("type") not in {"bot_text", "audio", "tts_fallback"}:
        return
    text = str(payload.get("display_text") or payload.get("text") or "").strip()
    if not text:
        return
    if room.native_v2 is None:
        # Compatibility rooms do not have native observations; only the
        # automatic watch comment needs to reach the QQ conversation, and only
        # after the room actually accepted the payload.
        if getattr(runtime, "sync_astrbot_conversation", False) and payload.get("source") == "watch_comment":
            runtime._create_background_task(runtime._record_astrbot_assistant(room, text))
        return
    runtime._create_background_task(runtime._push_live_subtitle(text, source="together_companion"))
    if runtime.record_visible_turns:
        _schedule(runtime, room, text, kind="assistant", transcript=transcript)
    if runtime.sync_astrbot_conversation:
        async def sync():
            if await resolve_room_context(runtime, room) is not None:
                await runtime._record_astrbot_turns(room, (transcript or {}).get("user_text", ""), text,
                    visual_context_used=bool((transcript or {}).get("visual_context_used")))
        runtime._create_background_task(sync())


def _schedule(runtime, room, text, *, kind, transcript):
    at = runtime.controller.now()
    event_id = (transcript or {}).get("event_id") or "together-room-" + room.room_id + "-" + uuid4().hex
    identifier = event_id if kind == "input" else "together-room-output-" + uuid4().hex

    async def observe():
        resolved = await resolve_room_context(runtime, room)
        if resolved is None:
            return
        queue = getattr(resolved.session, "core_reply_observations", None)
        if queue is None:
            return

        def record():
            if current_room_context(runtime, room) != resolved:
                return None
            resolved.session.authorize(resolved.context)
            reference = queue.owner.capture_together(resolved.context, observation_id=identifier,
                event_id=event_id, text=text, at=at, kind=kind)
            resolved.application.notify()
            return reference

        queue.submit(record)

    runtime._create_background_task(observe())
