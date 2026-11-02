"""Bind device work to V2's current private context and AstrBot providers."""

from __future__ import annotations

import asyncio
import base64
from datetime import timedelta
import json
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4

from companion_v2.conversation.external_tools import ExternalToolInvocation
from companion_v2.execution.models import TaskError, encode, require
from companion_v2.foundation.refs import ScopeKind
from companion_v2.sources.models import GovernanceState


class RealityHostContext:
    def __init__(self, controller, targets):
        self.controller, self.targets = controller, targets

    @staticmethod
    def origin(context):
        return f"{context.scope.platform}:FriendMessage:{context.scope.session_id}"

    def target(self, user_id, *, umo=""):
        if not isinstance(user_id, str) or not user_id.strip():
            raise TaskError("reality_user_required", "Reality requires the original private participant")
        resolved = self.targets._target_context(user_id.strip())
        if resolved is None:
            raise TaskError("reality_context_unavailable", "Reality has no current V2 private window")
        session, application, context = resolved
        registry = getattr(self.controller, "native_extensions", None)
        service = registry.active_service("reality") if registry is not None else None
        if (service is None or registry.application is not application
                or context.scope.kind is not ScopeKind.PRIVATE
                or context.actor.principal.native_account_id != user_id.strip()
                or context.scope.native_scope_id != user_id.strip()
                or (umo and umo != self.origin(context))):
            raise TaskError("reality_context_changed", "Reality must retain its active private window")
        authorize = getattr(session, "authorize", None)
        result = authorize(context) if callable(authorize) else session.require_delivery_authorization()
        if hasattr(result, "is_success"):
            require(result)
        return session, application, context, service

    def recheck(self, original, user_id, *, umo=""):
        current = self.target(user_id, umo=umo)
        if any(current[index] is not original[index] for index in (0, 1, 3)) or current[2] != original[2]:
            raise TaskError("reality_context_changed", "Reality's original persona or window changed")
        return current

    async def mobile_snapshot(self, session_token):
        """Read the paired user's snapshot through their active native capability."""
        user_id = self.targets._mobile_user(session_token)
        original = self.target(user_id)
        application, context = original[1:3]
        registry = self.controller.native_extensions
        declaration = self.targets._admitted(registry, application, context, "reality.snapshot.read")
        if declaration is None:
            raise TaskError("mobile_reality_not_authorized", "请先授权当前 V2 私聊目标读取手机摘要")

        def current():
            self.recheck(original, user_id)
            self.targets._recheck(session_token, user_id, context, application, registry, declaration)

        identity = "mobile-reality-" + uuid4().hex
        invocation = ExternalToolInvocation(identity, identity + "-parent", identity + "-call",
            context, declaration.descriptor, encode({}),
            self.controller.now() + timedelta(seconds=declaration.descriptor.timeout_seconds + 2),
            before_dispatch=current)
        response = await asyncio.wait_for(registry.invoke(declaration.host_tool_id, invocation),
            timeout=declaration.descriptor.timeout_seconds + 2)
        current()
        if (response.state != "succeeded" or response.effect != "none" or response.source is None
                or response.source.state is not GovernanceState.ACTIVE):
            raise TaskError("mobile_reality_snapshot_unavailable", "V2 手机摘要暂不可读取")
        result = json.loads(response.source.payload).get("native", {}).get("result", {})
        snapshot = result.get("snapshot")
        if type(snapshot) is not dict or snapshot.get("user_id") != user_id:
            raise TaskError("mobile_reality_snapshot_invalid", "V2 手机摘要身份不一致")
        return {"execution_path": "v2_native", "capability": "reality.snapshot.read",
                "state": response.state, "effect": response.effect,
                "receipt_ref": response.receipt_ref, "snapshot": snapshot}

    async def verified_persona(self, original, user_id, *, umo=""):
        self.recheck(original, user_id, umo=umo)
        context = original[2]
        persona = await self.controller.native_extensions.persona_guard(context.scope, context.role_binding)
        self.recheck(original, user_id, umo=umo)
        if (not isinstance(persona, dict) or not str(persona.get("prompt") or "").strip()
                or persona.get("bot_id") != context.scope.bot_id
                or persona.get("account_id") != context.scope.account_id):
            raise TaskError("reality_persona_unavailable", "Reality requires the verified V2 persona")
        return persona

    async def realtime(self, user_id, *, purpose="reality_touch", umo=""):
        from astrbot_host.core_reply_context import build_core_reply_prompt

        original = self.target(user_id, umo=umo)
        session, application, context, _service = original
        persona = await self.verified_persona(original, user_id, umo=umo)
        at = self.controller.now()
        native_input = SimpleNamespace(text=str(purpose), event_id="reality-context-" + uuid4().hex)
        background = build_core_reply_prompt(application, context, native_input, at=at).instruction
        history = application.proactive.history.conversation
        snapshot = history.capture(context, at, action="conversation.reply", limit=8, max_chars=2400)
        items = history.read(snapshot, context, at, action="conversation.reply")
        await self.verified_persona(original, user_id, umo=umo)
        recent_dialogue = "\n".join(f"{item['role']}：{item['text']}" for item in items)
        continuity = ("最近真实私聊，仅帮助理解称呼、语气和连续性；旧话不是新请求，"
                      "角色说过的话不证明用户已读或同意。\n" + recent_dialogue) if recent_dialogue else ""
        return {"available": True, "user_id": user_id, "context_key": context.key,
                "prompt": "\n\n".join(part for part in (persona["prompt"], background, continuity) if part),
                "recent_dialogue": recent_dialogue,
                "scene": background, "umo": self.origin(context)}

    async def generate(self, prompt, *, user_id, umo="", provider_id=None, max_tokens=160,
                       system_prompt="", task="reality_touch", timeout_seconds=30, timeout_key=None):
        original = self.target(user_id, umo=umo)
        runtime = original[3]
        host = self.controller.context
        origin = self.origin(original[2])
        provider = None
        if not provider_id:
            resolver = getattr(getattr(self.controller, "integration", None), "task_model_route", None)
            route = resolver(user_id, "reality.text") if callable(resolver) else {}
            for key in ("provider", "fallback"):
                candidate_id = str((route or {}).get(key) or "").strip()
                if not candidate_id:
                    continue
                candidate = host.get_provider_by_id(candidate_id)
                if candidate is not None and callable(getattr(candidate, "text_chat", None)):
                    provider, provider_id = candidate, candidate_id
                    break
        if provider_id and provider is None:
            provider = host.get_provider_by_id(provider_id)
        if provider is None:
            identifier = await host.get_current_chat_provider_id(origin)
            self.recheck(original, user_id, umo=umo)
            provider = host.get_provider_by_id(identifier)
            provider_id = identifier
        if provider is None or not callable(getattr(provider, "text_chat", None)):
            raise TaskError("reality_model_unavailable", "Reality's current AstrBot model is unavailable")
        if not system_prompt:
            system_prompt = (await self.realtime(user_id, purpose=task, umo=umo))["prompt"]
        await self.verified_persona(original, user_id, umo=umo)
        response, success, started = None, False, time.monotonic()
        try:
            response = await asyncio.wait_for(provider.text_chat(prompt=str(prompt), contexts=[],
                system_prompt=str(system_prompt), max_tokens=max(1, min(4096, int(max_tokens)))),
                timeout=max(1, min(120, float(timeout_seconds))))
            await self.verified_persona(original, user_id, umo=umo)
            if (getattr(response, "role", "assistant") != "assistant"
                    or getattr(response, "tools_call_name", ())):
                raise TaskError("reality_model_invalid", "Reality's text helper cannot execute tools")
            text = str(getattr(response, "completion_text", "") or "").strip()
            success = bool(text)
            return text
        finally:
            runtime._record_llm_usage(provider_id=provider_id or "", task=task, prompt=prompt,
                completion=getattr(response, "completion_text", "") if response is not None else "",
                elapsed_ms=round((time.monotonic() - started) * 1000), success=success,
                error="" if success else "model_result_unavailable", response=response)

    async def synthesize(self, text, *, user_id, umo="", provider_id="", timeout_seconds=None,
                         source="reality_touch", play_local=False):
        from astrbot_host.voice import NativeVoiceReply
        from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

        original = self.target(user_id, umo=umo)
        application, context, runtime = original[1:]
        await self.verified_persona(original, user_id, umo=umo)
        user = runtime._user(user_id, create=False)
        if not user or not runtime._reality_touch_audio_consented(user):
            raise TaskError("reality_audio_not_authorized", "Reality local voice requires this user's audio consent")
        from companion_v2.conversation.reply_settings import VOICE_FIELDS
        from companion_v2.conversation.voice_reply import VoiceReplyPolicy
        values = application.reply_settings.capture(context, self.controller.now())["settings"]
        policy = VoiceReplyPolicy(**{key: values["voice_" + key] for key in VOICE_FIELDS})
        configured = policy.provider_for_language() if policy is not None else ""
        timeout = timeout_seconds or (policy.timeout_seconds if policy is not None else 30)
        rendered = await NativeVoiceReply(self.controller.context).render(context, text,
            provider_id=provider_id or configured, timeout_seconds=timeout)
        await self.verified_persona(original, user_id, umo=umo)
        if not runtime._reality_touch_audio_consented(runtime._user(user_id, create=False) or {}):
            raise TaskError("reality_audio_not_authorized", "Reality local voice consent was revoked")
        if rendered is None:
            return {"success": False, "error": "V2 当前语音模型未返回音频"}
        encoded = rendered.get("data", {}).get("file", "")
        if not encoded.startswith("base64://"):
            return {"success": False, "error": "V2 语音结果无效"}
        audio = base64.b64decode(encoded[9:], validate=True)
        suffix = ".wav" if audio.startswith(b"RIFF") else ".ogg" if audio.startswith(b"OggS") else ".mp3"
        path = Path(get_astrbot_temp_path()) / ("v2-reality-" + uuid4().hex + suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, audio)
        try:
            await self.verified_persona(original, user_id, umo=umo)
            if not runtime._reality_touch_audio_consented(runtime._user(user_id, create=False) or {}):
                raise TaskError("reality_audio_not_authorized", "Reality local voice consent was revoked")
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return {"success": True, "audio_path": str(path), "temporary": True, "context_key": context.key}

    def cron(self):
        return getattr(self.controller.context, "cron_manager", None)

    async def delete_job(self, job_id):
        manager = self.cron()
        if manager is None or not callable(getattr(manager, "delete_job", None)):
            return False, "AstrBot 官方 Cron 不可用"
        registry = getattr(self.controller, "native_extensions", None)
        service = registry.service("reality") if registry is not None else None
        reminders = [reminder for user in (service.data.get("users", {}) if service is not None else {}).values()
            if isinstance(user, dict) for reminder in user.get("reality_touch_reminders", {}).values()
            if isinstance(reminder, dict) and reminder.get("job_id") == job_id]
        if not reminders:
            reader = getattr(getattr(manager, "db", None), "get_cron_job", None)
            job = await reader(job_id) if callable(reader) else None
            payload = getattr(job, "payload", None)
            if not isinstance(payload, dict) or payload.get("origin") != "companion_v2.reality":
                return False, "该 Cron 任务不属于 V2 现实提醒"
        try:
            await manager.delete_job(job_id)
        except Exception as error:
            return False, type(error).__name__
        return True, ""
