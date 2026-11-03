"""Optional, Dashboard-only persona design; no chat intake or runtime schema changes."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from dataclasses import dataclass
import inspect
import json
import logging
import re

from astrbot.api.web import request
from companion_v2.adapters.astrbot.native import configured_default_persona_id, persona_instruction
from companion_v2.execution.models import TaskError, require
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.persona_designer.protocol import MAX_DOCUMENT_BYTES, document, model_request, model_response
from companion_v2.persona_designer.store import DraftStore
from .bootstrap_provider import (provider_identifier, supported_text_provider,
                                 text_provider_catalog, text_provider_predicate)
from .management_identity import dashboard_operator
from .persona_catalog import available_personas


logger = logging.getLogger(__name__)
PREFIX = "/astrbot_plugin_private_companion/persona-designer"
MODEL_TIMEOUT = 120
REQUEST_ID = re.compile(r"[a-f0-9]{32}\Z")


@dataclass
class DesignJob:
    identifier: str
    task: asyncio.Task
    stopped: asyncio.Event


class PersonaDesignerWebAPI:
    def __init__(self, controller):
        self.controller = controller
        self.store = DraftStore(controller.data_root)
        self.jobs = {}
        self.closed = False

    def close(self):
        self.closed = True
        for job in tuple(self.jobs.values()):
            job.stopped.set()
            job.task.cancel()

    def routes(self):
        return [(PREFIX + suffix, self._safe(handler), methods, description) for suffix, handler, methods, description in (
            ("/options", self.options, ["GET"], "List persona designer model and persona choices"),
            ("/persona", self.persona, ["GET"], "Read one AstrBot persona without changing it"),
            ("/turn", self.turn, ["POST"], "Continue a client-owned persona design conversation"),
            ("/cancel", self.cancel, ["POST"], "Cancel this administrator's design request"),
            ("/drafts", self.drafts, ["GET"], "List explicitly saved persona drafts"),
            ("/draft", self.draft, ["GET"], "Load one explicitly saved persona draft"),
            ("/drafts", self.save, ["POST"], "Explicitly save a recoverable persona draft"),
        )]

    def _safe(self, handler):
        async def respond():
            try:
                owner = self._owner()
                return {"status": "ok", "data": await handler(owner)}
            except TaskError as error:
                return {"status": "error", "message": error.code, "reason": str(error),
                        "data": {"code": error.code}}
            except (ValueError, TypeError, UnicodeError):
                return {"status": "error", "message": "persona_designer_invalid",
                        "reason": "这次设计请求格式不完整，请检查后重试。", "data": {"code": "persona_designer_invalid"}}
            except Exception as error:
                # Provider/storage exceptions may contain credentials, URLs or persona text.
                logger.warning("[CompanionV2] 新的她请求失败 operation=%s error_type=%s status=%s",
                               handler.__name__, type(error).__name__,
                               getattr(error, "status_code", None) or getattr(error, "status", None))
                return {"status": "error", "message": "persona_designer_unavailable",
                        "reason": "新的她暂时未能完成这次操作，当前编辑内容仍保留，可以稍后重试。",
                        "data": {"code": "persona_designer_unavailable"}}
        return respond

    def _owner(self):
        controller = self.controller
        if self.closed or getattr(controller, "_terminated", False):
            raise TaskError("persona_designer_stopped", "功能已停止，请重新打开插件页面。")
        username = request.username
        if (type(username) is not str or not username.strip() or username != username.strip()
                or len(username) > 128 or username.startswith("api_key:")):
            raise TaskError("dashboard_identity_required", "请从已登录的 AstrBot 管理界面打开此页。")
        runtime = getattr(controller, "runtime", None)
        if runtime is not None:
            operator = dashboard_operator(runtime, username, at=controller.now())
            require(ObjectAccessPolicy(runtime).feature_access(operator,
                scope=ScopeRef(runtime.installation_id, "companion", ScopeKind.SYSTEM, "page.management"),
                action="memory.manage", observed_at=controller.now()))
        return sha256(username.encode("utf-8")).hexdigest()

    @staticmethod
    def _query(allowed=()):
        query = request.query
        if set(query.keys()) - set(allowed) or any(len(query.getlist(key)) != 1 for key in query.keys()):
            raise TaskError("persona_designer_invalid", "请求参数不完整，请重新选择。")
        return dict(query)

    @staticmethod
    async def _body(allowed):
        if request.query:
            raise TaskError("persona_designer_invalid", "请使用页面中的操作按钮。")
        raw = await request.body()
        if len(raw) > MAX_DOCUMENT_BYTES + 4096:
            raise TaskError("persona_designer_invalid", "设计内容超过本次操作的长度上限。")

        def unique(pairs):
            values = {}
            for key, value in pairs:
                if key in values:
                    raise ValueError("duplicate field")
                values[key] = value
            return values

        data = json.loads(raw, object_pairs_hook=unique)
        if type(data) is not dict or set(data) != set(allowed):
            raise TaskError("persona_designer_invalid", "设计请求缺少必要内容。")
        return data

    def _providers(self):
        expected = text_provider_predicate(getattr(self.controller, "native_types", None))
        providers = {}
        for provider in list(self.controller.context.get_all_providers() or []):
            try:
                if supported_text_provider(provider, expected) and callable(getattr(provider, "text_chat", None)):
                    identifier = provider_identifier(provider)
                    if identifier:
                        providers[identifier] = provider
            except Exception:
                continue
        return providers

    async def options(self, owner):
        self._query()
        context = self.controller.context
        models = text_provider_catalog(context, text_provider_predicate(getattr(self.controller, "native_types", None)))
        loaded = self._providers()
        models = [item for item in models if item["id"] in loaded]
        personas, selected, persona_issue = [], "", ""
        try:
            names = await asyncio.wait_for(available_personas(context), timeout=5)
            try:
                selected = configured_default_persona_id(context, None)
            except (AttributeError, TypeError):
                # Older host doubles/configuration surfaces may have no umo keyword.
                config = context.get_config()
                selected = (config.get("provider_settings") or {}).get("default_personality", "default")
            manager = getattr(context, "persona_manager", None)
            if selected == "default":
                record = getattr(manager, "selected_default_persona_v3", None)
                if isinstance(record, dict) and record.get("name") in names:
                    selected = record["name"]
            if selected not in names:
                selected = ""
            names.sort(key=lambda name: (name != selected, name.casefold()))
            personas = [{"id": name, "name": name, "default": name == selected} for name in names]
        except Exception:
            persona_issue = "人设列表暂时未能读取，仍可手动输入或从零设计。"
        return {"models": models, "personas": personas, "default_persona_id": selected,
                "persona_issue": persona_issue}

    async def persona(self, owner):
        query = self._query(("id",))
        identifier = query.get("id")
        if type(identifier) is not str or not identifier or len(identifier) > 200:
            raise TaskError("persona_designer_invalid", "请先选择一份人设。")
        context = self.controller.context
        if identifier not in await asyncio.wait_for(available_personas(context), timeout=5):
            raise TaskError("persona_designer_persona_missing", "这份人设已不在 AstrBot 列表中，请重新选择。")
        manager = context.persona_manager
        record = None
        for name in ("get_persona_v3_by_id", "get_persona"):
            getter = getattr(manager, name, None)
            if callable(getter):
                record = getter(identifier)
                if inspect.isawaitable(record):
                    record = await asyncio.wait_for(record, timeout=5)
                if record is not None:
                    break
        if record is None:
            record = next((item for item in getattr(manager, "personas_v3", [])
                           if isinstance(item, dict) and item.get("name") == identifier), None)
        if not isinstance(record, dict):
            record = vars(record) if record is not None else {}
        text = persona_instruction(record)
        if len(text) > 30000:
            raise TaskError("persona_designer_source_large", "这份人设较长，请手动选取需要优化的部分。")
        return {"id": identifier, "name": record.get("name") or identifier, "text": text}

    async def _text_chat(self, provider, *, prompt, system):
        """Ask the selected host model, with one conservative 400 retry.

        A provider that exposes a smaller output ceiling than the designer's
        desired 8000 tokens rejects the whole turn with a bare 400.  Nothing was
        generated in that case, so retrying once at 4096 is safe and keeps the
        page usable instead of reporting the generic "temporarily unavailable".
        """
        limit = 8000
        config = getattr(provider, "provider_config", None)
        if isinstance(config, dict):
            declared = config.get("max_tokens")
            if (isinstance(declared, int) and not isinstance(declared, bool)
                    and 0 < declared < limit):
                limit = declared
        try:
            return await provider.text_chat(prompt=prompt, system_prompt=system, contexts=[], max_tokens=limit)
        except Exception as error:
            if (type(error).__name__ not in ("BadRequestError", "InvalidRequestError", "InvalidArgument")
                    or limit <= 4096):
                raise
            logger.warning("[CompanionV2] 新的她模型拒绝输出上限 max_tokens=%s，降载到 4096 重试", limit)
            try:
                return await provider.text_chat(prompt=prompt, system_prompt=system, contexts=[], max_tokens=4096)
            except Exception as second:
                if type(second).__name__ in ("BadRequestError", "InvalidRequestError", "InvalidArgument"):
                    raise TaskError(
                        "persona_designer_model_rejected",
                        "模型没有接受这次设计请求；可以换一个文本模型，或缩短人设/对话后重试。",
                    ) from second
                raise

    async def turn(self, owner):
        data = await self._body(("request_id", "action", "state"))
        identifier = data["request_id"]
        if type(identifier) is not str or not REQUEST_ID.fullmatch(identifier):
            raise TaskError("persona_designer_invalid", "请重新发起本轮设计。")
        if owner in self.jobs or len(self.jobs) >= 2:
            raise TaskError("persona_designer_busy", "还有一轮设计正在生成，请等待完成或先停止生成。")
        state = document(data["state"])
        provider = self._providers().get(state["provider_id"])
        if provider is None:
            raise TaskError("persona_designer_model_missing", "所选文本模型尚未加载，请重新选择模型。")
        system, prompt = model_request(state, data["action"])
        task = asyncio.create_task(self._text_chat(provider, prompt=prompt, system=system),
                                   name="companion:persona-designer:" + identifier)
        job = DesignJob(identifier, task, asyncio.Event())
        self.jobs[owner] = job

        def completed(done):
            # A provider that suppresses cancellation keeps its slot until it
            # actually exits; repeated timeouts cannot accumulate unbounded work.
            if not done.cancelled():
                done.exception()
            if self.jobs.get(owner) is job:
                self.jobs.pop(owner, None)

        task.add_done_callback(completed)
        stopped = asyncio.create_task(job.stopped.wait())
        try:
            ready, _ = await asyncio.wait((task, stopped), timeout=MODEL_TIMEOUT,
                                          return_when=asyncio.FIRST_COMPLETED)
            if job.stopped.is_set() or task.cancelled():
                raise TaskError("persona_designer_cancelled", "本轮生成已停止，设计内容仍保留。")
            if task not in ready:
                raise TaskError("persona_designer_timeout", "本轮生成超时，设计内容仍保留，可以重试。")
            response = task.result()
            self._owner()
            if self._providers().get(state["provider_id"]) is not provider:
                raise TaskError("persona_designer_model_changed", "模型已重新加载，请重新发起本轮设计。")
            return model_response(getattr(response, "completion_text", response))
        finally:
            stopped.cancel()
            if not task.done():
                task.cancel()

    async def cancel(self, owner):
        data = await self._body(("request_id",))
        identifier = data["request_id"]
        if type(identifier) is not str or not REQUEST_ID.fullmatch(identifier):
            raise TaskError("persona_designer_invalid", "这轮生成的标识无效。")
        job = self.jobs.get(owner)
        cancelled = bool(job and job.identifier == identifier)
        if cancelled:
            job.stopped.set()
            job.task.cancel()
        return {"cancelled": cancelled}

    async def drafts(self, owner):
        query = self._query(("before",))
        before = int(query.get("before", 0))
        if not 0 <= before <= 2**63 - 1:
            raise ValueError("invalid page")
        return await asyncio.to_thread(self.store.list, owner, before)

    async def draft(self, owner):
        query = self._query(("id",))
        return await asyncio.to_thread(self.store.load, owner, query.get("id"))

    async def save(self, owner):
        data = await self._body(("id", "title", "state"))
        state = document(data["state"])
        return await asyncio.to_thread(self.store.save, owner, data["id"], data["title"], state)
