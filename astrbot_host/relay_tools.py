"""Supervised first-party tool port for audited cross-window and cross-group relaying."""

from __future__ import annotations

from datetime import datetime
import asyncio
import json
import re
from time import time
from types import SimpleNamespace

from companion_v2.adapters.astrbot.tools import AstrBotToolDeclaration
from companion_v2.conversation.native_tool_sources import input_row
from companion_v2.conversation.relay import plan_relay
from companion_v2.conversation.relay_policy import RelayRequest
from companion_v2.conversation.relay_tools import (DATASET_OWNER, PROPOSE_ACTION,
                                                   PROPOSE_MANY_ACTION, SEND_ACTION,
                                                   SEND_MANY_ACTION, tool_descriptors)
from companion_v2.conversation.external_tools import ExternalToolInvocation, ExternalToolResponse
from companion_v2.execution.models import TaskError, digest, encode, require
from companion_v2.foundation.refs import OperationRef, ScopeKind, SourceRef, VersionRef
from companion_v2.sources.models import SourceRecord
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository

from .private_notice import prepare_relay_transport
from .relay_dispatch import relay_group, relay_private
from .relay_pending import add_task


OWNER = "conversation.relay"
_PRIVATE_DESTINATION = re.compile(r"[0-9]{1,20}\Z")
_GROUP_DESTINATION = re.compile(r"[^\s]{1,128}\Z")
_CONFIRMATIONS = frozenset(("确认", "确认发送", "确认递话", "发吧", "就按这段发"))


class RelayToolPort:
    """Keep configuration policy, operation audit and the authenticated OneBot route together."""

    def __init__(self, bridge, *, native_types, enabled=True):
        if bridge is None or not callable(getattr(bridge, "client", None)):
            raise ValueError("relay tools need the original authenticated account bridge")
        if native_types is None or not isinstance(getattr(native_types, "reverse_api", None), type):
            raise ValueError("relay tools need the installed reverse-WebSocket API type")
        self.bridge, self.native_types = bridge, native_types
        self.application = None
        self._group_list_cache = {}
        self._member_cache = {}
        self._declarations = (tuple(AstrBotToolDeclaration("relay:" + item.tool.action, item)
                                    for item in tool_descriptors()) if enabled else ())

    def declarations(self):
        return self._declarations

    def current_declaration(self, host_tool_id):
        if self.application is None:
            return None
        return next((item for item in self._declarations if item.host_tool_id == host_tool_id), None)

    def provider_admission(self, host_tool_id):
        declaration = next((item for item in self._declarations if item.host_tool_id == host_tool_id), None)
        if declaration is None:
            return None

        def admit(context, at):
            application = self.application
            if application is None or application.state != "running":
                raise TaskError("relay_host_unavailable", "跨窗口递话当前不可用")
            return application.tasks.authorize_context(context, action="conversation.reply", observed_at=at)

        return admit

    def attach(self, application):
        if self.application is not None and self.application is not application:
            raise ValueError("relay tool port can attach only once")
        if getattr(application, "relay_settings", None) is None:
            raise ValueError("relay settings must be assembled before the relay tool port")
        self.application = application
        return self

    async def invoke(self, host_tool_id, invocation: ExternalToolInvocation) -> ExternalToolResponse:
        declaration = next((item for item in self._declarations if item.host_tool_id == host_tool_id), None)
        if declaration is None or declaration.descriptor != invocation.descriptor:
            raise TaskError("relay_provider_changed", "递话工具声明已变化")
        arguments = json.loads(invocation.arguments_json)
        action = declaration.descriptor.tool.action
        if action == PROPOSE_ACTION:
            arguments = await self._prepare_propose_mention(invocation, arguments)
            return self._propose(invocation, arguments)
        if action == PROPOSE_MANY_ACTION:
            prepared = await self._prepare_propose_many(invocation, arguments)
            return self._propose_many(invocation, prepared)
        if action == SEND_ACTION:
            return await self._send(invocation, arguments)
        if action == SEND_MANY_ACTION:
            return await self._send_many(invocation, arguments)
        raise TaskError("relay_action_unknown", "未知递话动作")

    def _app(self):
        if self.application is None or self.application.state != "running":
            raise TaskError("relay_host_unavailable", "跨窗口递话当前不可用")
        return self.application

    def _source(self, invocation, result):
        application = self._app()
        at = application.now()
        payload = encode(result)
        reference = SourceRef("relay:" + invocation.invocation_id, DATASET_OWNER, invocation.context.scope,
            VersionRef(invocation.invocation_id, 1), at,
        )
        return SourceRecord(reference, invocation.context.audience, digest(payload), payload)

    def _response(self, invocation, result, *, state="succeeded", effect="none", operation_id=None,
                  receipt_ref=None, error_code=None):
        return ExternalToolResponse(invocation.invocation_id, invocation.context.key,
            invocation.arguments_hash, invocation.descriptor.binding, state, effect=effect,
            source=self._source(invocation, result) if state == "succeeded" else None,
            operation_id=operation_id, receipt_ref=receipt_ref, error_code=error_code)

    def _input(self, invocation):
        application = self._app()
        row = input_row(application.messages, invocation.context, invocation.parent_task_id)
        from companion_v2.conversation.messages import source_from_document
        reference = source_from_document(json.loads(row["source_json"]))
        record = application.messages._read_source(reference, invocation.context, "text", application.now())
        return row, record.payload

    def _settings(self, invocation):
        application = self._app()
        return application.relay_settings.read(invocation.context, application.now())

    def _actor_is_core(self, invocation, settings):
        from companion_v2.capabilities.privacy import is_core
        actor_id = invocation.context.actor.principal.native_account_id
        return bool(
            is_core(self._app().runtime, invocation.context.actor.subject)
            or (settings.get("core_target_id") and actor_id == settings.get("core_target_id"))
        )

    def _plan(self, invocation, destination_kind, destination_id, text, mention_id="",
              relay_style="", delay_until_seen=False, need_receipt=False,
              confirm_before_report=False):
        kind = (destination_kind if isinstance(destination_kind, str) and destination_kind.strip()
                else "private").strip().lower()
        if type(destination_id) is not str:
            raise TaskError("relay_target_invalid", "递话目标必须是文本")
        destination_id = destination_id.strip()
        if kind == "private":
            if _PRIVATE_DESTINATION.fullmatch(destination_id) is None:
                raise TaskError("relay_target_invalid", "私聊目标必须是有效的数字账号 ID")
        elif kind == "group":
            if _GROUP_DESTINATION.fullmatch(destination_id) is None:
                raise TaskError("relay_target_invalid", "群聊目标不能为空")
        else:
            raise TaskError("relay_target_invalid", "递话目标类型必须是 private 或 group")
        mention = mention_id.strip() if isinstance(mention_id, str) else ""
        settings = self._settings(invocation)
        actor_id = invocation.context.actor.principal.native_account_id
        actor_is_core = self._actor_is_core(invocation, settings)
        style = relay_style if relay_style in {"original", "soft", "persona"} else (
            settings.get("default_style") if settings.get("default_style") in {"original", "soft", "persona"}
            else "persona")
        request = RelayRequest(kind, destination_id, text, actor_id, mention, style,
                               bool(delay_until_seen), bool(need_receipt),
                               bool(confirm_before_report))
        plan = plan_relay(request, settings, actor_is_core=actor_is_core,
                          actor_id=invocation.context.actor.subject.subject_id,
                          source_label=invocation.context.scope.native_scope_id)
        return settings, request, plan

    @staticmethod
    def _operation_id(invocation, event_id, plan):
        token = digest(["conversation.relay.v1", invocation.context.key, event_id,
                        invocation.context.actor.subject, plan.destination_kind,
                        plan.destination_id, plan.mention_id, plan.relay_style,
                        plan.delay_until_seen, plan.need_receipt,
                        plan.confirm_before_report, digest(plan.text)])
        return "relay-" + token

    @staticmethod
    def _parameter_hash(invocation, plan):
        context = invocation.context
        actor = context.actor
        return digest({
            "source_context": context.key,
            "source_scope": context.scope,
            "actor": [actor.principal, actor.subject, actor.binding_id, actor.binding_revision],
            "role": context.role_binding,
            "audience": plan.decision.audience,
            "needs_confirm": plan.needs_confirm,
            "destination_kind": plan.destination_kind,
            "destination": plan.destination_id,
            "mention": plan.mention_id,
            "relay_style": plan.relay_style,
            "delay_until_seen": plan.delay_until_seen,
            "need_receipt": plan.need_receipt,
            "confirm_before_report": plan.confirm_before_report,
            "text_hash": digest(plan.text),
        })

    @staticmethod
    def _part_id(plan):
        return plan.destination_kind + ":" + plan.destination_id

    def _reserve(self, invocation, plan, operation_id):
        application = self._app()
        at = application.now()
        operation = OperationRef(operation_id, OWNER, invocation.context.scope,
                                 VersionRef(operation_id, 1), operation_id)
        outbox_hash = digest({"destination_kind": plan.destination_kind,
                              "destination": plan.destination_id, "mention": plan.mention_id,
                              "text": plan.text, "scope": invocation.context.scope,
                              "audience": plan.decision.audience})
        receipt = require(OperationRepository(application.runtime).reserve(
            operation,
            parameter_hash=self._parameter_hash(invocation, plan),
            observed_at=at,
            outbox_part_id=self._part_id(plan),
            outbox_payload_hash=outbox_hash,
        ))
        return receipt, operation, outbox_hash

    @staticmethod
    def _setting_values(value):
        if isinstance(value, str):
            return {item.strip() for item in value.replace("[", "").replace("]", "")
                    .replace(chr(34), "").split(",") if item.strip()}
        return {str(item).strip() for item in value or () if str(item).strip()}

    def _native_api(self, context):
        client = self.bridge.client(context.scope)
        route = SimpleNamespace(
            kind=ScopeKind.GROUP,
            platform_id=context.scope.platform,
            login=context.scope.account_id,
            peer=str(context.scope.native_scope_id),
            client=client,
        )
        return prepare_relay_transport(SimpleNamespace(native_types=self.native_types), route)

    def _cache_get(self, cache, key):
        item = cache.get(key)
        if not isinstance(item, tuple) or len(item) != 2 or item[0] <= time():
            cache.pop(key, None)
            return None
        return item[1]

    def _cache_put(self, cache, key, value, minutes):
        cache[key] = (time() + max(1, int(minutes)) * 60, value)
        return value

    async def _resolve_group_target(self, context, hint, allowed_groups, cache_minutes=60):
        text = hint.strip() if isinstance(hint, str) else ""
        if ":GroupMessage:" in text:
            text = text.rsplit(":GroupMessage:", 1)[1].strip()
        if not text:
            raise TaskError("relay_target_invalid", "群聊目标不能为空")
        if _PRIVATE_DESTINATION.fullmatch(text):
            return text
        allowed = self._setting_values(allowed_groups)
        if text in allowed:
            return text
        cache_key = (context.scope.platform, context.scope.account_id)
        groups = self._cache_get(self._group_list_cache, cache_key)
        if groups is None:
            try:
                api = self._native_api(context)
                groups = await api.call_action("get_group_list", self_id=context.scope.account_id)
            except Exception:  # noqa: BLE001 - read failure is reported, not retried
                raise TaskError("relay_target_unavailable", "无法读取群列表，请直接提供群号") from None
            self._cache_put(self._group_list_cache, cache_key, groups, cache_minutes)
        if not isinstance(groups, list):
            raise TaskError("relay_target_unavailable", "平台没有返回群列表，请直接提供群号")
        detail = []
        for item in groups:
            if not isinstance(item, dict):
                continue
            group_id = str(item.get("group_id") or "").strip()
            group_name = str(item.get("group_name") or item.get("name") or "").strip()
            if group_id and (not allowed or group_id in allowed):
                detail.append((group_id, group_name))
        exact = [group_id for group_id, name in detail if text == group_id or text == name]
        hits = exact or [group_id for group_id, name in detail
                         if text and (text in group_id or text in name)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise TaskError("relay_target_ambiguous", "匹配到多个群聊，请补充群号")
        raise TaskError("relay_target_not_found", "没有找到匹配的群聊，请补充群号")

    def _network_aliases(self, context, group_id):
        """Read known group names for a target without inventing identities."""
        try:
            from companion_v2.relationships.group_network import route_owner_key
            role = context.role_binding.role.subject_id
            owner = route_owner_key(context.scope.installation_id, context.scope.platform,
                                    context.scope.bot_id, context.scope.account_id,
                                    group_id, role)
            network = self._app().messages.group_network.read_owner(owner)
        except Exception:  # noqa: BLE001 - optional local evidence must not block platform data
            return {}
        result = {}
        members = network.get("members") if isinstance(network, dict) else None
        if not isinstance(members, dict):
            return result
        for account_id, item in members.items():
            if not isinstance(item, dict):
                continue
            aliases = [str(entry.get("value") or "").strip()
                       for entry in item.get("aliases", ()) if isinstance(entry, dict)]
            result[str(account_id)] = tuple(alias for alias in aliases if alias)
        return result

    async def _resolve_group_mention(self, context, group_id, hint, cache_minutes=60,
                                     prefer_relationship_names=True):
        text = hint.strip() if isinstance(hint, str) else ""
        if not text:
            return ""
        if _PRIVATE_DESTINATION.fullmatch(text):
            return text
        if not group_id.isdigit():
            raise TaskError("relay_mention_unavailable",
                            "非数字群号无法读取群成员列表，请直接提供群友 QQ 号")
        cache_key = (context.scope.platform, context.scope.account_id, group_id)
        members = self._cache_get(self._member_cache, cache_key)
        if members is None:
            try:
                api = self._native_api(context)
                members = await api.call_action("get_group_member_list",
                    self_id=context.scope.account_id, group_id=int(group_id))
            except Exception:  # noqa: BLE001 - platform read failure is reported, not retried
                raise TaskError("relay_mention_unavailable",
                                "无法读取群成员列表，请直接提供群友 QQ 号") from None
            self._cache_put(self._member_cache, cache_key, members, cache_minutes)
        if not isinstance(members, list):
            raise TaskError("relay_mention_unavailable",
                            "平台没有返回群成员列表，请直接提供群友 QQ 号")
        detail = []
        for item in members:
            if not isinstance(item, dict):
                continue
            user_id = str(item.get("user_id") or "").strip()
            if not user_id:
                continue
            detail.append((user_id, str(item.get("nickname") or "").strip(),
                           str(item.get("card") or "").strip()))
        network = self._network_aliases(context, group_id)
        network_exact = [user_id for user_id, aliases in network.items()
                         if text == user_id or text in aliases]
        if prefer_relationship_names:
            if len(network_exact) == 1:
                return network_exact[0]
            if len(network_exact) > 1:
                raise TaskError("relay_mention_ambiguous", "匹配到多个群友，请补充群友 QQ 号")
        exact = [user_id for user_id, nickname, card in detail
                 if text == user_id or text == nickname or text == card]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise TaskError("relay_mention_ambiguous", "匹配到多个群友，请补充群友 QQ 号")
        if not prefer_relationship_names:
            if len(network_exact) == 1:
                return network_exact[0]
            if len(network_exact) > 1:
                raise TaskError("relay_mention_ambiguous", "匹配到多个群友，请补充群友 QQ 号")
        hits = [user_id for user_id, nickname, card in detail
                if any(text and text in value for value in (nickname, card))]
        hits.extend(user_id for user_id, aliases in network.items()
                    if any(text and text in alias for alias in aliases) and user_id not in hits)
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise TaskError("relay_mention_ambiguous", "匹配到多个群友，请补充群友 QQ 号")
        raise TaskError("relay_mention_not_found", "没有找到要 @ 的群友，请补充群友 QQ 号")

    async def _prepare_propose_many(self, invocation, arguments):
        raw = arguments.get("destination_ids")
        if not isinstance(raw, str):
            raise TaskError("relay_targets_invalid", "多目标递话需要 destination_ids 文本")
        targets = []
        for item in raw.replace("，", ",").replace("\n", ",").split(","):
            value = item.strip()
            if value and value not in targets:
                targets.append(value)
        settings = self._settings(invocation)
        limit = settings.get("multi_target_limit", 5)
        if not targets:
            raise TaskError("relay_targets_invalid", "多目标递话至少需要一个目标")
        if len(targets) > limit:
            raise TaskError("relay_targets_limit", f"一次最多准备 {limit} 个递话目标")
        base = {key: value for key, value in arguments.items() if key != "destination_ids"}
        prepared = []
        for target in targets:
            item = {**base, "destination_id": target}
            prepared.append(await self._prepare_propose_mention(invocation, item))
        return prepared

    def _propose_many(self, invocation, prepared):
        results = []
        for one in prepared:
            response = self._propose(invocation, one)
            payload = json.loads(response.source.payload) if response.source is not None else {
                "state": response.state, "error_code": response.error_code}
            results.append(payload)
        states = [item.get("state") for item in results]
        state = ("awaiting_confirmation" if "awaiting_confirmation" in states else
                 "ready" if states and all(item == "ready" for item in states) else "partial")
        return self._response(invocation, {
            "state": state, "targets": results,
            "confirmation_phrase": "确认发送" if state == "awaiting_confirmation" else "",
        })

    async def _send_many(self, invocation, arguments):
        operations = arguments.get("operations")
        if not isinstance(operations, list) or not operations:
            raise TaskError("relay_operations_invalid", "多目标发送需要 operations 列表")
        settings = self._settings(invocation)
        limit = settings.get("multi_target_limit", 5)
        if len(operations) > limit:
            raise TaskError("relay_targets_limit", f"一次最多发送 {limit} 个递话目标")
        results = []
        for item in operations:
            if not isinstance(item, dict):
                results.append({"state": "invalid", "error_code": "relay_operation_invalid"})
                continue
            one = dict(item)
            one["confirmed"] = arguments.get("confirmed") is True
            try:
                response = await self._send(invocation, one)
                payload = (json.loads(response.source.payload) if response.source is not None
                           else {"state": response.state, "error_code": response.error_code})
                payload["operation_id"] = response.operation_id or one.get("operation_id")
                results.append(payload)
            except TaskError as error:
                results.append({"state": "failed", "error_code": error.code,
                                "operation_id": one.get("operation_id")})
        states = [item.get("state") for item in results]
        overall = ("completed" if states and all(
            state in {"sent", "scheduled", "already_sent"} for state in states) else "partial")
        return self._response(invocation, {"state": overall, "targets": results})

    async def _prepare_propose_mention(self, invocation, arguments):
        kind = (arguments.get("destination_kind", "private") if isinstance(arguments.get("destination_kind"), str)
                else "private").strip().lower()
        destination_id = arguments.get("destination_id")
        mention = arguments.get("mention", "")
        if kind == "group" and isinstance(destination_id, str):
            settings = self._settings(invocation)
            cache_minutes = settings.get("member_cache_minutes", 60)
            destination_id = await self._resolve_group_target(
                invocation.context, destination_id, settings.get("allowed_groups", ""), cache_minutes)
            arguments = {**arguments, "destination_id": destination_id}
            resolved = await self._resolve_group_mention(
                invocation.context, destination_id, mention, cache_minutes,
                settings.get("prefer_relationship_names") is not False)
            if resolved:
                arguments = {**arguments, "mention": resolved}
        return arguments

    def _propose(self, invocation, arguments):
        destination_kind = arguments.get("destination_kind", "private")
        destination_id, text = arguments.get("destination_id"), arguments.get("text")
        if type(text) is not str:
            raise TaskError("relay_text_invalid", "递话正文必须是文本")
        kind = (destination_kind if isinstance(destination_kind, str) else "private").strip().lower()
        mention = arguments.get("mention", "")
        mention_id = mention.strip() if kind == "group" and isinstance(mention, str) else ""
        relay_style = arguments.get("style", "")
        delay_until_seen = arguments.get("delay_until_seen") is True
        need_receipt = arguments.get("need_receipt") is True
        confirm_before_report = arguments.get("confirm_before_report") is True
        settings, request, plan = self._plan(invocation, kind, destination_id, text, mention_id,
            relay_style, delay_until_seen, need_receipt, confirm_before_report)
        if not plan.allowed:
            return self._response(invocation, {"state": "denied", "code": plan.decision.code,
                                               "reason": plan.decision.reason, "audit": plan.audit})
        row, _ = self._input(invocation)
        operation_id = self._operation_id(invocation, row["event_id"], plan)
        receipt, operation, _ = self._reserve(invocation, plan, operation_id)
        if receipt.state is OperationState.UNKNOWN:
            return self._response(invocation, {"state": "unknown", "operation_id": operation_id,
                                               "instruction": "结果未知，禁止自动重发"})
        if receipt.state is OperationState.COMPLETED:
            outbox = require(OperationRepository(self.application.runtime).outbox(
                operation_id, self._part_id(plan)))
            state = ("already_sent" if outbox.state == "accepted" and outbox.receipt_ref
                     else "expired" if outbox.state == "cancelled" else "unresolved")
            return self._response(invocation, {"state": state, "operation_id": operation_id,
                                               "receipt_ref": outbox.receipt_ref if state == "already_sent" else None})
        if receipt.state is not OperationState.ACCEPTED:
            raise TaskError("relay_operation_unresolved", "递话操作状态无法确认")
        outbox = require(OperationRepository(self.application.runtime).outbox(
            operation_id, self._part_id(plan)))
        if outbox.state != "pending":
            if outbox.state == "unknown":
                return self._response(invocation, {"state": "unknown", "operation_id": operation_id,
                                                   "instruction": "结果未知，禁止自动重发"})
            raise TaskError("relay_operation_unresolved", "递话 outbox 状态与操作不一致")
        awaiting = plan.needs_confirm
        return self._response(invocation, {
            "state": "awaiting_confirmation" if awaiting else "ready",
            "operation_id": operation_id,
            "destination_kind": plan.destination_kind,
            "destination_id": plan.destination_id,
            "mention_id": plan.mention_id,
            "style": plan.relay_style,
            "delay_until_seen": plan.delay_until_seen,
            "need_receipt": plan.need_receipt,
            "confirm_before_report": plan.confirm_before_report,
            "text": plan.text,
            "requires_confirmation": awaiting,
            "confirmation_phrase": "确认发送" if awaiting else "",
            "audit": plan.audit,
        })

    @staticmethod
    def _confirmation_matches(text):
        normalized = text.strip().rstrip("。！？!?；;").strip()
        return normalized in _CONFIRMATIONS

    def _load_pending(self, invocation, operation_id, plan):
        application = self._app()
        if type(operation_id) is not str or not operation_id.startswith("relay-"):
            raise TaskError("relay_confirmation_invalid", "确认编号无效")
        operations = OperationRepository(application.runtime)
        receipt = require(operations.get(operation_id))
        operation = receipt.operation
        if (operation.owner != OWNER or operation.scope != invocation.context.scope
                or operation.idempotency_key != operation_id):
            raise TaskError("relay_confirmation_scope", "确认编号不属于当前窗口")
        verified, _, _ = self._reserve(invocation, plan, operation_id)
        return operations, verified

    def _route(self, context, plan):
        bridge = self.bridge
        if callable(getattr(bridge, "target_for_scope", None)):
            bridge.target_for_scope(context.scope)
        elif callable(getattr(bridge, "account", None)):
            bridge.account(context.scope)
        else:
            raise TaskError("relay_account_unavailable", "当前窗口没有可验证的 OneBot 账号")
        kind = ScopeKind.GROUP if plan.destination_kind == "group" else ScopeKind.PRIVATE
        peer = plan.destination_id
        client = bridge.client(context.scope)
        platform_bridge = (getattr(bridge, "groups", bridge)
                           if context.scope.kind is ScopeKind.GROUP else bridge)
        platform = None
        try:
            if callable(getattr(platform_bridge, "platform", None)):
                platform = platform_bridge.platform(context.scope)
        except Exception:  # noqa: BLE001 - raw OneBot transport may still be available
            platform = None
        platform_name = ""
        try:
            if callable(getattr(platform_bridge, "platform_name", None)):
                platform_name = platform_bridge.platform_name(context.scope)
        except Exception:  # noqa: BLE001 - platform name is only a transport hint
            platform_name = ""
        route = SimpleNamespace(
            kind=kind,
            platform_id=context.scope.platform,
            bot_id=context.scope.bot_id,
            login=context.scope.account_id,
            sender=context.actor.principal.native_account_id,
            peer=peer,
            session_id=peer,
            client=client,
            platform=platform,
            platform_name=platform_name,
        )
        controller = SimpleNamespace(native_types=self.native_types)
        transport = prepare_relay_transport(controller, route)
        return controller, route, transport

    async def _send(self, invocation, arguments):
        operation_id = arguments.get("operation_id")
        destination_kind = arguments.get("destination_kind", "private")
        destination_id, text = arguments.get("destination_id"), arguments.get("text")
        mention = arguments.get("mention", "")
        relay_style = arguments.get("style", "")
        delay_until_seen = arguments.get("delay_until_seen") is True
        need_receipt = arguments.get("need_receipt") is True
        confirm_before_report = arguments.get("confirm_before_report") is True
        confirmed = arguments.get("confirmed") is True
        if type(text) is not str:
            raise TaskError("relay_text_invalid", "递话正文必须是文本")
        kind = (destination_kind if isinstance(destination_kind, str) else "private").strip().lower()
        if kind == "group" and isinstance(destination_id, str):
            settings = self._settings(invocation)
            cache_minutes = settings.get("member_cache_minutes", 60)
            destination_id = await self._resolve_group_target(
                invocation.context, destination_id, settings.get("allowed_groups", ""), cache_minutes)
            if isinstance(mention, str):
                mention = await self._resolve_group_mention(
                    invocation.context, destination_id, mention, cache_minutes,
                    settings.get("prefer_relationship_names") is not False)
        settings, request, plan = self._plan(invocation, kind, destination_id, text, mention,
            relay_style, delay_until_seen, need_receipt, confirm_before_report)
        if not plan.allowed:
            return self._response(invocation, {"state": "denied", "code": plan.decision.code,
                                               "reason": plan.decision.reason, "audit": plan.audit},
                                  state="failed", effect="not_committed",
                                  receipt_ref="relay-denied:" + digest([operation_id, plan.decision.code]),
                                  error_code=plan.decision.code)
        if plan.needs_confirm:
            row, input_text = self._input(invocation)
            if not confirmed or not self._confirmation_matches(input_text):
                return self._response(invocation, {"state": "awaiting_confirmation",
                    "operation_id": operation_id, "confirmation_phrase": "确认发送"},
                    state="failed", effect="not_committed",
                    receipt_ref="relay-confirmation-required:" + digest(operation_id or ""),
                    error_code="relay_confirmation_required")
        else:
            row = None
        operations, receipt = self._load_pending(invocation, operation_id, plan)
        if plan.needs_confirm and row is not None and (
                datetime.fromisoformat(row["observed_at"]) <= receipt.created_at):
            return self._confirmation_refused(invocation, operation_id)
        part_id = self._part_id(plan)
        if receipt.state is OperationState.COMPLETED:
            outbox = require(operations.outbox(operation_id, part_id))
            if outbox.state == "accepted" and outbox.receipt_ref:
                return self._response(invocation, {"state": "already_sent", "operation_id": operation_id,
                                                   "receipt_ref": outbox.receipt_ref, "audit": plan.audit}, state="succeeded",
                                      effect="committed", operation_id=operation_id,
                                      receipt_ref=outbox.receipt_ref)
            raise TaskError("relay_operation_unresolved", "递话操作已结束但没有成功发送回执")
        if receipt.state is not OperationState.ACCEPTED:
            raise TaskError("relay_result_unknown", "上次发送结果未知；不会再次发送")
        outbox = require(operations.outbox(operation_id, part_id))
        if outbox.state != "pending":
            raise TaskError("relay_result_unknown", "递话 outbox 已不在待发送状态；不会再次发送")
        if plan.delay_until_seen:
            if plan.destination_kind != "group" or not plan.mention_id:
                raise TaskError("relay_delay_invalid", "延迟转述只支持群聊，并需要指定目标群友")
            task_id = await asyncio.to_thread(add_task, self._app().runtime, {
                "kind": "group_wait", "state": "waiting",
                "group_id": plan.destination_id, "target_user_id": plan.mention_id,
                "mention_id": plan.mention_id, "text": plan.text,
                "source_kind": invocation.context.scope.kind.value,
                "source_id": invocation.context.scope.native_scope_id,
                "source_user": invocation.context.actor.principal.native_account_id,
                "operation_id": operation_id,
                "expires_at": time() + 24 * 3600,
                "signature": digest(["relay-group-wait", invocation.context.key,
                                     plan.destination_id, plan.mention_id, plan.text]),
            })
            receipt_ref = "relay-scheduled:" + task_id
            require(operations.transition_outbox(operation_id, part_id,
                expected_operation_state=OperationState.ACCEPTED,
                next_operation_state=OperationState.UNKNOWN,
                expected_outbox_state="pending", next_outbox_state="unknown",
                observed_at=self._app().now()))
            require(operations.transition_outbox(operation_id, part_id,
                expected_operation_state=OperationState.UNKNOWN,
                next_operation_state=OperationState.COMPLETED,
                expected_outbox_state="unknown", next_outbox_state="accepted",
                observed_at=self._app().now(), receipt_ref=receipt_ref))
            return self._response(invocation, {
                "state": "scheduled", "operation_id": operation_id,
                "task_id": task_id, "destination_kind": plan.destination_kind,
                "destination_id": plan.destination_id, "mention_id": plan.mention_id,
                "receipt_ref": receipt_ref, "audit": plan.audit},
                state="succeeded", effect="committed", operation_id=operation_id,
                receipt_ref=receipt_ref)
        if plan.need_receipt and plan.destination_kind != "private":
            raise TaskError("relay_receipt_invalid", "回复回执只支持私聊递话")
        controller, route, transport = self._route(invocation.context, plan)
        if invocation.before_dispatch is not None:
            invocation.before_dispatch()
        at = self._app().now()
        require(operations.transition_outbox(operation_id, part_id,
            expected_operation_state=OperationState.ACCEPTED, next_operation_state=OperationState.UNKNOWN,
            expected_outbox_state="pending", next_outbox_state="unknown", observed_at=at))
        try:
            sender = relay_group if plan.destination_kind == "group" else relay_private
            result = await sender(controller, None, request=request, settings=settings,
                actor_is_core=self._actor_is_core(invocation, settings),
                actor_id=invocation.context.actor.subject.subject_id,
                confirmed=confirmed or not plan.needs_confirm, base_route=route, transport=transport)
        except Exception:
            return self._response(invocation, {"state": "unknown", "operation_id": operation_id,
                                               "instruction": "结果未知，禁止自动重发"},
                                  state="unknown", effect="unknown", operation_id=operation_id,
                                  error_code="relay_delivery_unknown")
        message_id = result.get("receipt", {}).get("message_id") if isinstance(result, dict) else None
        receipt_ref = _onebot_receipt_ref(message_id)
        if not isinstance(result, dict) or result.get("state") != "sent" or receipt_ref is None:
            return self._response(invocation, {"state": "unknown", "operation_id": operation_id,
                                               "instruction": "没有获得 OneBot 成功回执，禁止自动重发"},
                                  state="unknown", effect="unknown", operation_id=operation_id,
                                  error_code="relay_delivery_unknown")
        require(operations.transition_outbox(operation_id, part_id,
            expected_operation_state=OperationState.UNKNOWN, next_operation_state=OperationState.COMPLETED,
            expected_outbox_state="unknown", next_outbox_state="accepted", observed_at=self._app().now(),
            receipt_ref=receipt_ref))
        if plan.need_receipt:
            await asyncio.to_thread(add_task, self._app().runtime, {
                "kind": "private_receipt", "state": "waiting_reply",
                "target_user_id": plan.destination_id,
                "source_kind": invocation.context.scope.kind.value,
                "source_id": invocation.context.scope.native_scope_id,
                "source_user": invocation.context.actor.principal.native_account_id,
                "question": plan.text, "sent_text": plan.text,
                "confirm_before_report": plan.confirm_before_report,
                "operation_id": operation_id,
                "expires_at": time() + 12 * 3600,
                "signature": digest(["relay-private-receipt", operation_id]),
            })
        return self._response(invocation, {"state": "sent", "operation_id": operation_id,
            "destination_kind": plan.destination_kind, "destination_id": plan.destination_id,
            "mention_id": plan.mention_id, "receipt_ref": receipt_ref, "audit": plan.audit},
            state="succeeded", effect="committed", operation_id=operation_id, receipt_ref=receipt_ref)

    def _confirmation_refused(self, invocation, operation_id):
        return self._response(invocation, {"state": "awaiting_confirmation",
            "operation_id": operation_id, "confirmation_phrase": "确认发送"},
            state="failed", effect="not_committed",
            receipt_ref="relay-confirmation-required:" + digest(operation_id or ""),
            error_code="relay_confirmation_required")


def _onebot_receipt_ref(message_id):
    if isinstance(message_id, bool) or type(message_id) not in (str, int):
        return None
    identifier = str(message_id).strip()
    if not identifier or len(identifier) > 496:
        return None
    return "onebot-message:" + identifier


__all__ = ["OWNER", "RelayToolPort"]
