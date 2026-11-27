"""Read-only presentation conditions; no port calls, binding, scheduling or retries."""

from companion_v2.execution.models import TaskError, stamp


def describe_presentation(app, context, *, settings, effective):
    coordinator = app.presence

    def availability(option, action):
        if not settings[option]:
            return {"state": "disabled", "reason": "presentation_disabled"}
        if coordinator is None:
            return {"state": "unavailable", "reason": "presence_port_missing"}
        if not effective[option]["allowed"]:
            return {"state": "blocked", "reason": effective[option]["reason"]}
        if coordinator.closed:
            return {"state": "paused", "reason": "host_not_running"}
        try:
            coordinator._allowed(context, option, action)
        except TaskError as error:
            return {"state": "paused" if error.code == "host_not_running" else "blocked", "reason": error.code}
        return {"state": "ready", "reason": None}

    typing = availability("typing_enabled", "platform.typing")
    typing["refresh_seconds"] = settings["typing_refresh_seconds"]
    if typing["state"] == "ready":
        turn = coordinator.active_turn(context)
        route = coordinator.route(context)
        worker = coordinator.typing.get(route)
        typing["batch_id"] = turn["batch_id"] if turn else None
        typing["turn_state"] = turn["state"] if turn else None
        if turn is None:
            typing["state"] = "idle"
        elif coordinator.suppressed.get(route) == turn["batch_id"]:
            typing.update(state="suppressed", reason="typing_batch_suppressed")
        elif worker and worker[0].key == context.key and not worker[2].done() and not worker[2].cancelling():
            typing["state"] = "refreshing"
        else:
            typing["state"] = "waiting"

    def status_diagnostic(option, intent_field):
        status = availability(option, "platform.presence")
        status["shadowed_by"] = None
        if status["state"] != "ready":
            return status
        account = coordinator.account(context)
        owners = coordinator._candidates().get(account, [])
        if len(owners) > 1:
            status.update(state="blocked", reason="multiple_presence_owners")
            return status
        try:
            intent = coordinator.desired_status(context)
            status.update(intent)
            desired = intent["desired"]
            if desired is None:
                status["state"] = "blocked" if intent["reason"] != "presence_no_public_wording" else "idle"
            elif intent_field not in desired:
                if "wording" in desired:
                    shadowed_by = "calendar_plan" if desired.get("basis") == "calendar_plan" else "custom_status"
                elif "mode" in desired:
                    shadowed_by = "native_reply" if desired.get("basis") == "native_reply" else "online_status"
                else:
                    shadowed_by = None
                status.update(state="idle", desired=None, reason=None, shadowed_by=shadowed_by)
            else:
                role = coordinator.role_key(context)
                result_key = coordinator.result_key(context)
                bound = tuple(item for item in coordinator.contexts.values()
                              if item.key == context.key or coordinator.role_key(item) == role)
                if not bound:
                    status.update(state="waiting", reason="presence_context_unbound")
                else:
                    latest = coordinator.latest_status(context)
                    if latest and latest["desired_hash"] == coordinator.intent_hash(desired):
                        # Same-role windows share one account-level presentation result.
                        known = coordinator.contexts.get(latest["context_key"])
                        same_role = known is not None and coordinator.result_key(known) == result_key
                        status["state"] = "recorded" if latest["context_key"] == context.key or same_role else "deduplicated"
                    else:
                        status["state"] = "waiting"
        except TaskError as error:
            status.update(state="blocked", reason=error.code)
        return status

    custom = status_diagnostic("presence_enabled", "wording")
    online = status_diagnostic("online_status_enabled", "mode")
    effective_channel = (
        "custom_status" if custom.get("desired") and "wording" in custom["desired"]
        else "online_status" if online.get("desired") and "mode" in online["desired"]
        else None
    )
    return {"observed_at": stamp(app.now()), "port": "installed" if coordinator else "preview",
            "priority": settings.get("presence_priority", "custom_first"),
            "effective_channel": effective_channel,
            "typing": typing, "custom_status": custom, "online_status": online}
