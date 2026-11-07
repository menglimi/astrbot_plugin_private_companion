"""Asynchronous original-role reply generation for the local conversation profile."""

from dataclasses import replace
import asyncio
import json
import sqlite3
from logging import getLogger
from time import perf_counter

from companion_v2.execution.models import TaskError, TaskResult, digest
from companion_v2.model_gateway.models import ModelCompletion
from companion_v2.model_gateway.service import LocalModelGateway
from companion_v2.expression import learning as style_learning
from .style_context import EXPRESSION_STYLE_INSTRUCTION

from .foreground import GroundedDraftReviewer, ModelDraft, PromptMaterial
from .messages import ConversationMessages, DELIVERY_KIND
from .media.reply import reply_manifest
from .segmentation import SegmentationPolicy
from .reply_settings import (group_safety_settings, validate as validate_settings,
                              voice_policy as captured_voice_policy)
from .reply_preparation import observed_statements, prepare_reply
from .text_processing import text_policy
from .voice_reply import (VoiceReplyPolicy, capability_instruction as voice_capability_instruction,
                          eligible, voice_policy_for_turn)
from companion_v2.delivery.media import payload_hash
from companion_v2.foundation.refs import ScopeKind


logger = getLogger(__name__)
MODEL_FAILURE_NOTICE = "刚刚我这边卡住了，没能接住你的消息。你再发我一次，我马上接着聊。"

# 可公开的失败分类：只说对方能判断或转告管理员的原因，不带内部码、堆栈或请求细节。
# 目的不是解释实现，而是让群友/用户知道「等一等」「换个模型」还是「找管理员」。
MODEL_FAILURE_REASONS = {
    "model_budget_exhausted": "（今天模型额度用完了，管理员补充额度后就能继续）",
    "model_budget_configuration": "（模型预算配置有问题，管理员在「模型与连接」里检查一下）",
    "model_budget_policy_changed": "（预算策略刚调整过，稍后再发一次）",
    "model_provider_unavailable": "（当前没有可用的文本模型，管理员检查一下模型连接）",
    "model_route_invalid": "（模型路由配置无效，管理员检查「模型与连接」）",
    "model_plan_missing": "（这次没有生成可用的模型请求计划，管理员检查模型配置）",
    "model_task_unavailable": "（这个任务暂时没有可用模型）",
    "model_task_unregistered": "（这个任务还没接入模型）",
    "model_capability_mismatch": "（所选模型不支持这次请求需要的能力）",
    "model_output_limit": "（输出上限设置超出了模型能力）",
    "model_output_truncated": "（模型把话说到一半被截断了，再发一次我重新组织）",
    "model_refused": "（模型拒绝了这次请求，换个说法或稍后再试）",
    "model_timeout": "（模型响应超时了）",
    "model_transport_failed": "（模型连接中断了）",
    "host_model_limit_input": "（这轮材料超出了模型的输入上限）",
    "host_model_limit_output": "（输出上限超出了模型能力）",
}


def model_failure_notice(code):
    """One public sentence: the friendly fallback plus the actionable reason."""
    return MODEL_FAILURE_NOTICE + MODEL_FAILURE_REASONS.get(code, "")


def single_part(text):
    return (text,)


def _add_materials_before_tool_results(request, materials):
    additions = tuple(materials)
    if not additions:
        return request
    current = request.context.materials
    insertion = next((index for index, material in enumerate(current)
                      if material.owner == "conversation.tools"
                      and material.purpose == "actual_tool_results"), len(current))
    context = replace(request.context,
                      materials=current[:insertion] + additions + current[insertion:])
    return replace(request, context=context)


def _without_expression_style_materials(request):
    materials = tuple(material for material in request.context.materials
                      if material.purpose not in ("expression_style_context", "expression_style_rule"))
    instruction = request.instruction
    suffix = "\n" + EXPRESSION_STYLE_INSTRUCTION
    instruction = instruction.replace(suffix, "", 1)
    return replace(request, instruction=instruction, context=replace(request.context, materials=materials))


def _filter_expression_style_materials(request, decision, *, max_rules=None, allowed_ids=None):
    materials = []
    selected = 0
    for material in request.context.materials:
        if material.purpose != "expression_style_rule":
            materials.append(material)
            continue
        if max_rules is not None and selected >= max_rules:
            continue
        try:
            payload = json.loads(material.payload)
            if allowed_ids is not None and payload.get("signal_id") not in allowed_ids:
                continue
            applicability = style_learning.normalize_applicability(payload.get("applicability"))
        except (TypeError, ValueError, AttributeError):
            continue
        if style_learning.laya_intent_matches(applicability, decision):
            materials.append(material)
            selected += 1
    if not selected:
        materials = [material for material in materials if material.purpose != "expression_style_context"]
        suffix = "\n" + EXPRESSION_STYLE_INSTRUCTION
        return replace(request, instruction=request.instruction.replace(suffix, "", 1),
                       context=replace(request.context, materials=tuple(materials)))
    else:
        updated = []
        for material in materials:
            if material.purpose == "expression_style_context" and material.payload:
                details = json.loads(material.payload)
                details["rules_injected"] = selected
                details["selection_partial"] = selected < details.get("rules_available", selected)
                material = replace(material, payload=json.dumps(details, ensure_ascii=False,
                    sort_keys=True, separators=(",", ":")))
            updated.append(material)
        materials = updated
    return replace(request, context=replace(request.context, materials=tuple(materials)))


def _prepend_instruction(request, instruction):
    addition = instruction.strip()
    if not addition:
        return request
    return replace(request, instruction=addition + "\n" + request.instruction)


def _promote_instruction_suffix(request, suffix):
    if not suffix or not request.instruction.endswith(suffix):
        return request
    return replace(request, instruction=suffix + request.instruction[:-len(suffix)])


def _preserve_turn_instruction(request, original):
    """Keep this turn's instructions while adding newly available owner guidance."""
    if request.instruction.startswith(original.instruction):
        return request
    sealed = set(original.instruction.splitlines())
    additions = "\n".join(line for line in request.instruction.splitlines() if line not in sealed)
    return replace(request, instruction=original.instruction + ("\n" + additions if additions else ""))


def voice_input_text(request):
    current = tuple(material.payload for material in request.context.materials
                    if material.purpose == "current_source")
    if current:
        return "\n".join(current)
    return "\n".join(material.payload for material in request.context.materials
                     if material.purpose.startswith("merged_input:"))


class LocalReplyGenerator:
    def __init__(self, messages: ConversationMessages, gateway: LocalModelGateway, *, split=single_part,
                 segmentation=SegmentationPolicy(), voice_policy=None, send_stream_part=None):
        if messages.tasks is not gateway.tasks or not callable(split):
            raise ValueError("reply generation requires the original gateway and a trusted segmentation policy")
        self.messages = messages
        self.gateway = gateway
        self.split = split
        if not isinstance(segmentation, SegmentationPolicy):
            raise ValueError("reply segmentation needs a validated policy")
        self.segmentation = segmentation
        if voice_policy is not None and not isinstance(voice_policy, VoiceReplyPolicy):
            raise ValueError("reply voice needs a validated policy")
        self.voice_policy = voice_policy
        self.send_stream_part = send_stream_part
        self.reviewer = GroundedDraftReviewer()

    @staticmethod
    def _laya_facts(request):
        event = request.event
        text = voice_input_text(request)
        kind = "text" if text else ("image" if request.images else "audio" if request.audios else "video")
        facts = {"message_kind": kind, "user_text": text[:12000],
                 "scope_kind": event.scope.kind.value}
        scope_account = str(event.scope.account_id or "")

        def sender_is_bot(sender):
            return sender is not None and str(sender) == scope_account

        # The authenticated principal carries the native sender id.  This is
        # a host fact, so a bot-authored event can be ruled out without asking
        # a semantic model to identify the sender from message text.
        principal_sender = getattr(event.principal, "native_account_id", None)
        if sender_is_bot(principal_sender):
            facts["is_bot_message"] = True

        def contains_bot_sender(value, depth=0):
            if depth > 4:
                return False
            if sender_is_bot(getattr(value, "sender_id", None)):
                return True
            for name in ("quotes", "forwards", "nodes"):
                for child in getattr(value, name, ()) or ():
                    if contains_bot_sender(child, depth + 1):
                        return True
            return False

        if any(contains_bot_sender(item) for name in ("quotes", "forwards")
               for item in (getattr(event, name, ()) or ())):
            facts["quoted_bot_message"] = True
        # These are optional event facts supplied by the host.  If a host does
        # not expose one, Laya never gets to invent it.
        for name in ("is_bot_message", "sender_is_bot", "quoted_bot_message", "quotes_bot_message",
                     "has_bot_quote", "referenced_bot_message", "reply_to_bot_message"):
            value = getattr(event, name, None)
            if type(value) is bool:
                facts[name] = facts.get(name, False) or value
        for material in getattr(getattr(request, "context", None), "materials", ()):
            if getattr(material, "purpose", None) == "role_state":
                try:
                    values = json.loads(material.payload).get("simulation", {}).get("values", {})
                    summary = {}
                    if isinstance(values.get("energy"), int):
                        summary["energy"] = values["energy"]
                    for key in ("sleep", "busy"):
                        if isinstance(values.get(key), str) and values[key].strip():
                            summary[key] = values[key].strip()[:80]
                    mood_val = values.get("mood")
                    if isinstance(mood_val, str) and mood_val.strip():
                        summary["mood"] = mood_val.strip()[:80]
                    elif isinstance(mood_val, dict) and isinstance(mood_val.get("description"), str):
                        summary["mood"] = mood_val["description"].strip()[:80]
                    if summary:
                        facts["role_state_summary"] = summary
                except Exception:
                    pass
                break
        return facts

    async def _laya_decision(self, run, request):
        resolver = getattr(self.messages, "laya_decision_resolver", None)
        if not callable(resolver):
            return None
        try:
            service = resolver(run.task.spec.context)
            if service is None:
                return None
            facts = self._laya_facts(request)
            set_trace = getattr(service, "set_trace", None)
            if callable(set_trace):
                set_trace(event_ref=run.task.spec.request_ref, source="conversation")
            logger.debug("[CompanionV2][开发者追踪] Laya 事件事实 batch=%s facts=%r",
                         run.task.spec.request_ref, facts)
            result = await service.decide(facts)
            logger.debug("[CompanionV2][开发者追踪] Laya 决策 batch=%s result=%r",
                         run.task.spec.request_ref, result)
            return result if isinstance(result, dict) and result.get("status") == "ok" else None
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Laya is an optional advisory model.  A transport or contract
            # failure must leave the ordinary reply route available.
            logger.warning("[CompanionV2] Laya 决策未采用 batch=%s code=%s reason=%s",
                           run.task.spec.request_ref, getattr(error, "code", type(error).__name__),
                           getattr(error, "reason", str(error))[:1000])
            return None

    def _commit_laya_silence(self, run, *, action):
        if action not in ("wait", "silent"):
            raise ValueError("only a Laya wait or silent decision can suppress a reply")
        batch_id = run.task.spec.request_ref
        run.validate()
        with self.messages.runtime.transaction() as connection:
            current = self.messages.request(run)
            if "silent" not in current.reply_actions:
                return None
            row = self.messages.batch(batch_id)
            if row["state"] != "sealed" or row["call_id"] is not None:
                raise TaskError("message_manifest_exists", "original reply cannot be generated twice")
            manifest = reply_manifest(batch_id, None, (), action="silent")
            connection.execute("""UPDATE conversation_batches SET state = 'ready', revision = revision + 1,
                call_id = NULL, manifest_hash = ?, part_count = 0, presentation_action = 'silent'
                WHERE batch_id = ?""", (manifest, batch_id))
        participation = getattr(self.messages.tools, "group_participation", None)
        if participation is not None:
            participation.record_no_reply(run.task.spec.context, batch_id)
        logger.info("[CompanionV2] Laya 决策跳过主模型 batch=%s action=%s", batch_id, action)
        return TaskResult("succeeded", result_ref=batch_id)

    def _private_failure_notice(self, run, error, stream_reply):
        if not (isinstance(error, sqlite3.IntegrityError)
                or isinstance(error, TaskError) and error.code.startswith("model_")):
            return None
        context = run.task.spec.context
        if context.scope.kind is not ScopeKind.PRIVATE or stream_reply is not None and stream_reply.published:
            return None
        run.validate()
        batch_id = run.task.spec.request_ref
        batch = self.messages.batch(batch_id)
        if batch["state"] != "sealed" or batch["call_id"] is not None:
            return None
        if self.messages.runtime.read_one(
                "SELECT 1 FROM conversation_parts WHERE batch_id = ? LIMIT 1", (batch_id,)):
            return None
        delivery = self.messages.tasks.registrations[DELIVERY_KIND].definition
        remaining = (run.task.spec.deadline_at - self.messages.tasks.now()).total_seconds()
        if remaining < delivery.timeout_seconds:
            return None
        code = error.code if isinstance(error, TaskError) else "model_settlement_failed"
        logger.warning("[CompanionV2] 私聊模型回复未完成，改发本地收尾提示 batch=%s failure=%s",
                       batch_id, code)
        return ModelCompletion(None, ModelDraft(model_failure_notice(code), ()), False)

    def _request(self, run, segmentation=None, streaming=None, voice=None, presentation=None, base_request=None,
                 image_output=None):
        request = base_request or self.messages.request(run)
        request = replace(request, streaming=streaming, streaming_mode=(segmentation or self.segmentation).streaming_mode)
        instruction = (segmentation or self.segmentation).instruction
        from .media.output_settings import effective_settings, prompt_instruction
        instruction = (instruction + "\n" + prompt_instruction(
            image_output if image_output is not None else
            effective_settings(self.messages.runtime, run.task.spec.context))).strip()
        if presentation is not None and presentation.instruction:
            instruction = (instruction + "\n" + presentation.instruction).strip()
        if voice is not None:
            instruction = (instruction + "\n" + voice_capability_instruction(voice)
                           + ("\n" + voice.instruction if voice.instruction else "")).strip()
        return replace(request, instruction=instruction + "\n" + request.instruction) if instruction else request

    async def generate(self, run, *, attention_note: str = ""):
        if run.supervisor is not self.messages.tasks:
            raise ValueError("reply generation must use the original supervisor")
        started = perf_counter()
        sealed = json.loads(self.messages.batch(run.task.spec.request_ref)["contributions_json"])
        captured = sealed.get("reply_settings")
        segmentation = validate_settings(captured["settings"]) if captured is not None else self.segmentation
        presentation = text_policy(captured["settings"] if captured is not None else {})
        streaming = (captured["settings"]["streaming"]
                     if captured is not None and captured.get("streaming_explicit", True) else None)
        image_position = captured["settings"].get("image_position", "after_text") if captured is not None else "after_text"
        voice = (captured_voice_policy(captured["settings"])
                 if captured is not None and "voice_enabled" in captured["settings"] else self.voice_policy)
        logger.info("[CompanionV2] 开始生成回复 batch=%s segmentation=%s llm_segmenting=%s mode=%s min_chars=%d max_parts=%d",
                    run.task.spec.request_ref, segmentation.enabled, segmentation.llm_controlled,
                    segmentation.rule_mode, segmentation.min_segment_chars, segmentation.max_segments)
        semantic = getattr(self.messages, "semantics", None)
        if semantic is not None:
            await semantic.prepare_turn(run)
        base_request = self.messages.request(run)
        from companion_v2.expression.configuration import read_settings as expression_settings
        expression_policy = expression_settings(self.messages.runtime, run.task.spec.context)
        expression_review = (sealed.get("expression_style", {}).get("style_review_enabled", True)
            and expression_policy.style_review_enabled
            and any(item.purpose == "expression_style_rule" for item in base_request.context.materials))
        streaming_before_style = streaming
        if expression_review:
            # No part may leave before the complete wording has been reviewed.
            streaming = False
        input_text = voice_input_text(base_request)
        voice = voice_policy_for_turn(voice, input_text)
        from .media.output_settings import effective_settings
        image_output = effective_settings(self.messages.runtime, run.task.spec.context)
        request = self._request(run, segmentation, streaming, voice, presentation, base_request,
                                image_output=image_output)
        laya = await self._laya_decision(run, _without_expression_style_materials(request))
        decision = laya.get("decision") if isinstance(laya, dict) and isinstance(laya.get("decision"), dict) else {}
        laya_action = decision.get("action")
        if laya_action in ("silent", "wait"):
            silent_result = self._commit_laya_silence(run, action=laya_action)
            if silent_result is not None:
                return silent_result
            laya = None
        captured_style = sealed.get("expression_style") or {}
        rule_limit = min(captured_style.get("rules_per_turn", expression_policy.rules_per_turn),
                         expression_policy.rules_per_turn)
        request = _filter_expression_style_materials(request, decision if laya is not None else {},
                                                     max_rules=rule_limit)
        selected_style_ids = frozenset(json.loads(material.payload)["signal_id"]
            for material in request.context.materials if material.purpose == "expression_style_rule")
        expression_review = (sealed.get("expression_style", {}).get("style_review_enabled", True)
            and expression_policy.style_review_enabled
            and any(item.purpose == "expression_style_rule" for item in request.context.materials))
        if not expression_review:
            streaming = streaming_before_style
        caption_material = None
        caption_service = getattr(self.messages, "context_image_caption", None)
        if caption_service is not None and captured is not None:
            caption = await caption_service.summarize(run, run.task.spec.request_ref, captured["settings"])
            if caption:
                caption_material = PromptMaterial("conversation.context_image_caption", "historical_image_caption",
                    "以下是对仍获授权的群历史原图所作的临时摘要，仅用于理解上下文；它不是来源证据，也不是持久事实：" + caption)
                request = _add_materials_before_tool_results(request, (caption_material,))
                request = replace(request,
                    instruction=request.instruction + "\n历史图片摘要只作临时辅助；不要把摘要当作当前画面、原图证据或持久记忆。")
        if caption_material is not None:
            request = _promote_instruction_suffix(request,
                "\n历史图片摘要只作临时辅助；不要把摘要当作当前画面、原图证据或持久记忆。")
        narration_service = getattr(self.messages, "image_narration", None)
        narration_materials = ()
        image_reply_instruction = ""
        if narration_service is not None and captured is not None:
            same_image_route = narration_service.uses_reply_model(
                run.task.spec.context, request, captured["settings"])
            if same_image_route:
                image_reply_instruction = narration_service.reply_instruction(captured["settings"])
                if image_reply_instruction:
                    request = _prepend_instruction(request, image_reply_instruction)
                narration = ""
            else:
                narration = await narration_service.summarize(
                    run, run.task.spec.request_ref, captured["settings"])
            if narration:
                narration_materials = (PromptMaterial("conversation.image_narration", "current_image_narration",
                    "以下是本轮图片的临时视觉转述，可能有识别误差；原图仍是主要依据：" + narration),)
                request = _add_materials_before_tool_results(request, narration_materials)
                request = replace(request,
                    instruction=request.instruction + "\n图片转述仅作辅助线索；与原图冲突时以原图为准，不从图片确认人物身份、时间、地点或画外事实。")
        if narration_materials:
            request = _promote_instruction_suffix(request,
                "\n图片转述仅作辅助线索；与原图冲突时以原图为准，不从图片确认人物身份、时间、地点或画外事实。")
        video_materials = ()
        direct_video = False
        if request.videos:
            from .media.video.narration import (
                VideoNarration, VIDEO_INSTRUCTION,
            )
            video_service = getattr(self.messages, "video_narration", None)
            direct_video = (video_service is not None and captured is not None
                and video_service.can_use_reply_model(run, request, captured["settings"]))
            if direct_video:
                request = _prepend_instruction(request, video_service.reply_instruction(captured["settings"]))
                request = replace(request, native_video_input=True)
            elif video_service is not None and captured is not None:
                try:
                    narrations = await video_service.summarize(
                        run, run.task.spec.request_ref, captured["settings"])
                except Exception as error:
                    logger.warning("[CompanionV2] 视频原生转述未完成 reason=%s",
                                   error.code if isinstance(error, TaskError) else type(error).__name__)
                    narrations = tuple(VideoNarration(video.source,
                        "视频转述失败，尚未读到视频内容。", False) for video in request.videos)
            else:
                narrations = tuple(VideoNarration(video.source,
                    "没有可用的原生视频理解模型，尚未读取视频。", False) for video in request.videos)
            if not direct_video:
                video_materials = tuple(PromptMaterial("conversation.video_narration", "current_video_narration",
                    ("原生视频转述：" if item.available else "视频状态：") + item.text, item.source)
                    for item in narrations)
                frame_sources = {frame.source for video in request.videos for frame in video.frames}
                request = replace(request,
                    context=replace(request.context, materials=tuple(
                        material for material in request.context.materials if material.source not in frame_sources
                    )),
                    videos=(), native_video_input=False)
                request = _add_materials_before_tool_results(request, video_materials)
                request = replace(request,
                    instruction=request.instruction + "\n" + VIDEO_INSTRUCTION)
                request = _promote_instruction_suffix(request, "\n" + VIDEO_INSTRUCTION)
        if laya is not None:
            segment_mode = decision.get("segment_mode")
            if segment_mode in ("single", "keep_together"):
                segmentation = replace(segmentation, enabled=False, llm_controlled=False,
                                       streaming_mode="buffered")
                streaming = False
            elif segment_mode == "natural" and segmentation.enabled:
                segmentation = replace(segmentation, llm_controlled=True, streaming_mode="buffered")
                streaming = False
            guidance = {
                "brief": "简洁回应，保留完成任务所需的信息。",
                "normal": "按自然对话长度回应，不刻意拉长。",
                "detailed": "在确有帮助时补充必要背景，避免重复。",
                "serious": "保持认真、清楚和克制的语气。",
                "playful": "可以轻松活泼，但不要牺牲清楚和事实边界。",
            }.get(decision.get("reply_style"), "")
            intent_guidance = {
                "answer": "优先回答用户实际提出的问题。",
                "acknowledge": "先接住用户表达，再决定是否需要补充内容。",
                "ask": "用自然、必要的追问推进对话。",
                "comfort": "优先提供真诚、具体的安慰与支持。",
                "explain": "把必要的原因或背景解释清楚。",
                "summarize": "围绕已有内容作准确归纳，不添加新事实。",
            }.get(decision.get("reply_intent"), "")
            form_guidance = {
                "text": "优先使用文字表达。",
                "voice": "语音形式较合适；仅在当前配置和能力支持时使用，否则用文字完成回应。",
                "text_voice": "文字与语音都可能合适；仅在当前配置和能力支持时使用语音。",
                "image": "图片形式可能合适；仅在当前授权能力能完成时使用，否则用文字说明。",
            }.get(decision.get("reply_form"), "")
            action_guidance = {
                "reply": "当前适合回应用户的表达。",
                "interject": "结合群聊语境自然加入话题，不要打断正在进行的关键信息交换。",
                "clarify": "如果信息仍不足以可靠回应，先提出简洁的澄清问题。",
            }.get(decision.get("action"), "")
            turn_guidance = {
                "complete": "Laya 判断用户当前表达已经完整。",
                "unfinished": "Laya 判断用户可能还会补充；结合上下文优先等待，不要抢答半句话。",
                "uncertain": "Laya 无法确定用户是否说完；结合上下文谨慎判断，必要时再澄清。",
            }.get(decision.get("turn_state"), "")
            tool_guidance = {
                "memory": "如确有必要，只考虑本轮已提供且获授权的记忆能力。",
                "vision": "如确有必要，只考虑本轮已提供且获授权的视觉能力。",
                "tts": "如确有必要，只考虑本轮已配置且可用的语音能力。",
                "image": "如确有必要，只考虑本轮已授权且可用的图片能力。",
                "external": "如确有必要，只考虑本轮已注册且获授权的工具。",
            }.get(decision.get("tool_need"), "")
            guidance = " ".join(value for value in (
                turn_guidance, intent_guidance, guidance, form_guidance,
                action_guidance, tool_guidance) if value)
            if guidance:
                request = _prepend_instruction(request, "Laya 语义分类给出的表达建议（只作为约束参考）：" + guidance)
        # Laya can change presentation policy after the first request was
        # assembled. Keep the session's sealed request aligned with the
        # continuation fields used by the generation loop.
        if attention_note:
            request = replace(request, instruction=request.instruction + "\n" + attention_note)
        request = replace(request, streaming=streaming, streaming_mode=segmentation.streaming_mode)
        tools = self.messages.tools
        participation = getattr(tools, "group_participation", None)
        tool_catalog, tool_filters = (tools.catalog_for_with_diagnostics(
            run.task.spec.context, run.task.spec.request_ref) if tools else ((), ()))
        # Pass the catalog unchanged.  ``gateway.open_session()`` is the single
        # authority on whether this round may offer tools: it decides from the
        # authoritative resolved plan (not a second route prediction), records
        # the degradation in the plan audit, and returns the effective catalog
        # in the session.  Do not pre-filter here: a prediction that sees a
        # different provider chain can silently remove tools from a plan that
        # actually supports them (and would read the configuration twice).
        stream_reply = None
        history = ()
        completion = None
        local_failure_notice = False
        # The exact request of the last model round. Reviews and delivery must
        # judge the materials the model actually received, not a later rebuild
        # of the sealed turn (which offers the whole candidate pool).
        turn_request = request
        try:
            try:
                session = self.gateway.open_session(run, request, tools=tool_catalog, tool_filters=tool_filters)
            except (TaskError, sqlite3.IntegrityError) as error:
                completion = self._private_failure_notice(run, error, stream_reply)
                if completion is None:
                    raise
                local_failure_notice = True
                voice = None
                # 这一轮没有进入模型循环，所以循环里的 current 从未赋值。
                # 兜底文案仍要按本轮封存的请求判断可用的 expression action。
                current = turn_request
            if (not local_failure_notice and segmentation.enabled and segmentation.streaming_mode == "incremental"
                    and self.send_stream_part is not None and captured is not None and streaming is not False
                    and not (voice is not None and eligible(voice, input_text=input_text,
                        kind=run.task.spec.context.scope.kind.value, batch_id=run.task.spec.request_ref))):
                from .streaming_reply import StreamingReply
                stream_settings = dict(captured["settings"])
                if run.task.spec.context.scope.kind is ScopeKind.PRIVATE:
                    state_owner = getattr(self.messages, "role_state_context", None)
                    if state_owner is not None:
                        try:
                            simulation = state_owner.service.read(run.task.spec.context,
                                at=self.messages.tasks.now(), injection=True)
                            from companion_v2.role_work.state import humanized
                            values = simulation.get("values") if isinstance(simulation.get("values"), dict) else {}
                            if isinstance(values.get("energy"), int):
                                stream_settings.setdefault("role_energy", values["energy"])
                            if humanized.is_sleepy(simulation):
                                stream_settings.setdefault("role_sleepy", True)
                        except Exception:
                            pass
                stream_reply = StreamingReply(self.messages, run, segmentation, presentation,
                                              stream_settings, self.send_stream_part)
            while not local_failure_notice:
                current = self._request(run, segmentation, streaming, voice, presentation,
                                        image_output=image_output)
                if caption_material is not None:
                    current = _add_materials_before_tool_results(current, (caption_material,))
                    current = replace(current,
                        instruction=current.instruction + "\n历史图片摘要只作临时辅助；不要把摘要当作当前画面、原图证据或持久记忆。")
                    current = _promote_instruction_suffix(current,
                        "\n历史图片摘要只作临时辅助；不要把摘要当作当前画面、原图证据或持久记忆。")
                if image_reply_instruction:
                    current = _prepend_instruction(current, image_reply_instruction)
                if narration_materials:
                    current = _add_materials_before_tool_results(current, narration_materials)
                    current = replace(current,
                        instruction=current.instruction + "\n图片转述仅作辅助线索；与原图冲突时以原图为准，不从图片确认人物身份、时间、地点或画外事实。")
                    current = _promote_instruction_suffix(current,
                        "\n图片转述仅作辅助线索；与原图冲突时以原图为准，不从图片确认人物身份、时间、地点或画外事实。")
                if video_materials:
                    from .media.video.narration import VIDEO_INSTRUCTION
                    frame_sources = {frame.source for video in current.videos for frame in video.frames}
                    current = replace(current,
                        context=replace(current.context, materials=tuple(
                            material for material in current.context.materials if material.source not in frame_sources
                        )),
                        videos=(), native_video_input=False)
                    current = _add_materials_before_tool_results(current, video_materials)
                    current = replace(current,
                        instruction=current.instruction + "\n" + VIDEO_INSTRUCTION)
                    current = _promote_instruction_suffix(current, "\n" + VIDEO_INSTRUCTION)
                elif direct_video:
                    current = _prepend_instruction(current, video_service.reply_instruction(captured["settings"]))
                    current = replace(current, native_video_input=True)
                current = _preserve_turn_instruction(current, request)
                current = _filter_expression_style_materials(current, decision if laya is not None else {},
                                                             max_rules=rule_limit,
                                                             allowed_ids=selected_style_ids)
                if session.round_no == 8:
                    current = replace(current, instruction=current.instruction +
                        "\n本轮工具已结束。按已有实际结果自然回复，说明未完成部分；不要声称未执行的操作已成功。")
                if tools is not None:
                    history = tools.refresh_history(self.messages.batch(run.task.spec.request_ref), run.task.spec.context,
                                                    current, history, self.messages.tasks.now())
                expression_context = self.messages.expression_style_context
                if expression_context is not None:
                    try:
                        expression_context.stage_request(current, run.task.spec.context,
                            batch_id=run.task.spec.request_ref, at=self.messages.tasks.now())
                    except Exception as error:
                        logger.warning("[CompanionV2] 表达使用记录暂未写入 error_type=%s", type(error).__name__)
                model_started = perf_counter()
                round_no = session.round_no + 1
                logger.debug("[CompanionV2][开发者追踪] 回复提示词 batch=%s round=%d role_instruction=%r instruction=%r materials=%r history=%r",
                             run.task.spec.request_ref, round_no, current.role_instruction, current.instruction,
                             tuple(material.payload for material in current.context.materials), history)
                logger.info("[CompanionV2] 回复模型调用开始 batch=%s round=%d preparation_ms=%d",
                            run.task.spec.request_ref, round_no, round((model_started - started) * 1000))
                turn_request = current
                generating = session.generate(current, history=history,
                    on_reply_prefix=stream_reply.observe if stream_reply is not None else None,
                    reply_prefix_ready=stream_reply.ready if stream_reply is not None else None,
                    owner_refresh=(lambda original, request, previous, fresh: tools.validate_owner_refresh(
                        run.task.spec.context, run.task.spec.request_ref, original, request, previous, fresh)) if tools else None,
                    media_refresh=lambda original, request: self.messages.generated_images.validate_refresh(
                        run.task.spec.request_ref, run.task.spec.context, self.messages.tasks.now(), original, request))
                try:
                    completion = await stream_reply.generate(generating) if stream_reply is not None else await generating
                except (TaskError, sqlite3.IntegrityError) as error:
                    completion = self._private_failure_notice(run, error, stream_reply)
                    if completion is None:
                        raise
                    local_failure_notice = True
                    voice = None
                if local_failure_notice:
                    logger.info("[CompanionV2] 本地故障收尾提示已准备 batch=%s", run.task.spec.request_ref)
                else:
                    logger.info("[CompanionV2] 回复模型调用结束 batch=%s round=%d elapsed_ms=%d tools=%d",
                                run.task.spec.request_ref, round_no, round((perf_counter() - model_started) * 1000),
                                len(completion.tool_calls))
                logger.debug("[CompanionV2][开发者追踪] 回复模型结果 batch=%s round=%d call_id=%s draft=%r action=%s claims=%r tool_calls=%r",
                             run.task.spec.request_ref, round_no, completion.call_id,
                             completion.draft.text if completion.draft is not None else None,
                             getattr(completion.draft, "action", None) if completion.draft is not None else None,
                             getattr(completion.draft, "claims", None) if completion.draft is not None else None,
                             completion.tool_calls)
                # Validate the exact materials used for this round again before
                # another owner command or a sendable manifest can be created.
                current = self.messages.request(run)
                if completion.draft is not None:
                    if participation is not None:
                        participation.allow_reply(run.task.spec.context, run.task.spec.request_ref)
                    break
                if tools is None:
                    raise TaskError("model_tools_unavailable", "this reply has no registered tool owners")
                for call in completion.tool_calls:
                    run.validate()
                    history += (await tools.execute_async(run, completion.call_id, call),)
                    if participation is not None and participation.finish_if_skipped(
                            run.task.spec.context, run.task.spec.request_ref):
                        return TaskResult("succeeded", result_ref=run.task.spec.request_ref)
            if stream_reply is not None and await stream_reply.finish(completion.call_id, completion.draft):
                logger.info("[CompanionV2] 流式回复生成完成 batch=%s parts=%d elapsed_ms=%d",
                            run.task.spec.request_ref, len(stream_reply.parts), round((perf_counter() - started) * 1000))
                return TaskResult("succeeded", result_ref=run.task.spec.request_ref)
            draft_text = completion.draft.text
            if (not local_failure_notice and expression_review
                    and getattr(completion.draft, "action", "text") in ("text", "voice_only")):
                review = getattr(self.messages, "expression_draft_review", None)
                if review is not None:
                    before_review = draft_text
                    # Only the rules this round actually carried may shape the review;
                    # the whole candidate pool is not part of the reply.
                    draft_text = await review.review(run, turn_request, completion.draft)
                    logger.debug("[CompanionV2][开发者追踪] 正文处理 batch=%s stage=expression_review before=%r after=%r",
                                 run.task.spec.request_ref, before_review, draft_text)
            action = getattr(completion.draft, "action", "text")
            if action == "text" and draft_text == "" and getattr(completion.draft, "images", ()):
                action = "image"
            if action not in current.reply_actions:
                raise TaskError("reply_action_unavailable", "the selected expression action is unavailable in this turn")
            safety = group_safety_settings(captured["settings"] if captured is not None else {})
            if (draft_text and run.task.spec.context.scope.kind is ScopeKind.GROUP
                    and safety["enable_group_reality_promise_guard"]):
                before_guard = draft_text
                draft_text, _ = presentation.reality_promise_guard(
                    draft_text, statements=observed_statements(completion.draft))
                logger.debug("[CompanionV2][开发者追踪] 正文处理 batch=%s stage=reality_promise_guard before=%r after=%r",
                             run.task.spec.request_ref, before_guard, draft_text)
            forbidden = (presentation.forbidden_word_guard(
                draft_text, scope_kind=run.task.spec.context.scope.kind.value)
                if draft_text else None)
            logger.debug("[CompanionV2][开发者追踪] 正文处理 batch=%s stage=forbidden_word_guard blocked=%s body=%r",
                         run.task.spec.request_ref, forbidden is not None, draft_text)
            if forbidden is not None:
                raise TaskError("forbidden_word_blocked", "reply contains a configured forbidden word")
            if action in ("silent", "poke"):
                if draft_text != "" or completion.draft.claims or getattr(completion.draft, "images", ()):
                    raise TaskError("reply_action_invalid", "non-message actions cannot contain reply content")
                prepared = None
                parts = () if action == "silent" else ("",)
                speech = ()
                candidate_reasons = () if action == "silent" else ("empty_part",)
                image_only = False
            elif action == "image":
                if draft_text != "" or completion.draft.claims:
                    raise TaskError("reply_action_invalid", "image-only replies cannot contain text or claims")
                image_only = True
                prepared, parts = None, ("",)
                speech = ("",)
                candidate_reasons = ("empty_part",)
            else:
                image_only = False
                try:
                    prepared = prepare_reply(draft_text, segmentation, presentation, voice,
                        statements=observed_statements(completion.draft), split=self.split)
                except ValueError as error:
                    raise TaskError("message_parts_invalid", "reply contains no bounded visible text") from error
                parts = prepared.parts
                logger.debug("[CompanionV2][开发者追踪] 正文处理 batch=%s stage=prepare_reply action=%s normalized=%r parts=%r",
                             run.task.spec.request_ref, action, draft_text, parts)
                if action == "voice_only":
                    if voice is None:
                        raise TaskError("reply_voice_unavailable", "voice-only action requires configured voice delivery")
                    speech = tuple(part.strip() for part in parts)
                    if any(not item or len(item) > voice.max_speech_chars for item in speech):
                        raise TaskError("reply_voice_unavailable", "voice-only speech exceeds the configured limit")
                    candidate_reasons = ("planned",) * len(parts)
                else:
                    speech = ("",) * len(parts)
                    candidate_reasons = ("voice_disabled",) * len(parts)
                    if voice is not None:
                        if eligible(voice, input_text=input_text, kind=run.task.spec.context.scope.kind.value,
                                    batch_id=run.task.spec.request_ref):
                            decisions = prepared.speech(voice)
                            speech = tuple(item[0] for item in decisions)
                            candidate_reasons = tuple(item[1] for item in decisions)
                        else:
                            candidate_reasons = ("trigger_skipped",) * len(parts)
            if (not isinstance(parts, tuple) or len(parts) > 64 or action != "silent" and not parts or
                    any(not isinstance(part, str) or (not part.strip() and not image_only and action != "poke")
                        for part in parts) or
                    sum(map(len, parts)) > 65536):
                raise TaskError("message_parts_invalid", "reply presentation must produce bounded visible parts")
            with self.messages.runtime.transaction() as connection:
                current = self.messages.request(run)
                if not self.reviewer.review(completion.draft, current.context,
                                            observed_at=self.messages.tasks.now()).approved:
                    raise TaskError("draft_not_grounded", "declared evidence was not in the original sealed materials")
                batch_id = run.task.spec.request_ref
                row = self.messages.batch(batch_id)
                if row["state"] != "sealed" or row["call_id"] is not None:
                    raise TaskError("message_manifest_exists", "original reply cannot be generated twice")
                if action not in current.reply_actions:
                    raise TaskError("reply_action_unavailable", "the selected expression action is no longer available")
                images = (() if action in ("silent", "poke", "voice_only")
                          else self.messages.reply_images.select(completion.draft, current))
                if image_only and not images:
                    raise TaskError("reply_images_unavailable", "image-only replies require an authorized image")
                if action == "voice_only" and images:
                    raise TaskError("reply_action_invalid", "voice-only replies cannot include images")
                image_sequence = len(parts)
                if semantic is not None and images and not image_only and len(parts) < 64:
                    mode = semantic.reactions.delivery_mode(run.task.spec.context, batch_id, images)
                    if mode == "separate_before":
                        parts = ("",) + parts
                        speech = ("",) + speech
                        candidate_reasons = ("empty_part",) + candidate_reasons
                        image_sequence = 1
                    elif mode == "separate_after":
                        parts = parts + ("",)
                        speech = speech + ("",)
                        candidate_reasons = candidate_reasons + ("empty_part",)
                        image_sequence = len(parts)
                manifest = reply_manifest(batch_id, completion.call_id, parts, images,
                                          image_position=image_position, action=action)
                visible_parts = tuple(part for part in parts if part.strip())
                logger.info("[CompanionV2] 回复正文已通过校验 batch=%s action=%s parts=%d elapsed_ms=%d 分段正文=%r",
                            batch_id, action, len(visible_parts), round((perf_counter() - started) * 1000), visible_parts)
                for sequence, text in enumerate(parts, 1):
                    part_id = "part-" + digest([batch_id, sequence])
                    connection.execute("""INSERT INTO conversation_parts
                        (part_id, batch_id, sequence, text, payload_hash, state)
                        VALUES (?, ?, ?, ?, ?, 'pending')""", (part_id, batch_id, sequence, text,
                            payload_hash(text, images if sequence == image_sequence else (), image_position=image_position)))
                    reason = candidate_reasons[sequence - 1]
                    connection.execute("""INSERT INTO conversation_voice_diagnostics
                        (part_id, candidate_reason, render_state, component_order_json, text_hidden)
                        VALUES (?, ?, ?, '[]', 0)""", (part_id, reason,
                            "pending" if reason == "planned" else "not_applicable"))
                    if speech and speech[sequence - 1]:
                        policy = voice
                        connection.execute("""INSERT INTO conversation_voice_parts
                            (part_id, speech, provider_id, delivery_mode, position, timeout_seconds, cooldown_seconds)
                            VALUES (?, ?, ?, ?, ?, ?, ?)""", (part_id, speech[sequence - 1],
                            policy.provider_for_language(run.task.spec.context.scope.kind.value), "voice_only" if action == "voice_only" else policy.delivery_mode,
                            policy.position,
                            policy.timeout_seconds, policy.cooldown(run.task.spec.context.scope.kind.value)))
                if images:
                    image_part_id = "part-" + digest([batch_id, image_sequence])
                    self.messages.reply_images.publish(row, run.task.spec.context, image_part_id, images,
                                                       self.messages.tasks.now(), image_position=image_position)
                    # A group image is reviewed before it may reach the collective
                    # audience. The verdict is registered with the sealed manifest.
                    self.messages.image_review.register(run.task.spec.context, batch_id, image_part_id, images,
                                                        image_position=image_position)
                connection.execute("""UPDATE conversation_batches SET state = 'ready', revision = revision + 1,
                    call_id = ?, manifest_hash = ?, part_count = ?, presentation_action = ? WHERE batch_id = ?""",
                    (completion.call_id, manifest, len(parts), action, batch_id))
            return TaskResult("succeeded", result_ref=batch_id)
        except BaseException as error:
            logger.debug("[CompanionV2][开发者追踪] 回复处理失败 batch=%s error_type=%s",
                         run.task.spec.request_ref, type(error).__name__, exc_info=True)
            if stream_reply is not None:
                await stream_reply.interrupt()
            if completion is not None and completion.call_id:
                self.gateway.ledger.discard(completion.call_id)
            raise
