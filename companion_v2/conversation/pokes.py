"""Policy and supervised model response for native poke notifications."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from math import isfinite

from companion_v2.conversation.foreground import BackgroundPromptTrigger, PromptMaterial, PromptRequest, SealedForegroundContext
from companion_v2.execution.models import (Lane, TaskContext, TaskDefinition, TaskError, TaskRegistration, TaskResult,
    TaskSpec, digest, require)
from companion_v2.execution.runner import ExactTaskRunner
from companion_v2.execution.service import outcome
from companion_v2.model_gateway.models import ModelTaskProfile


DEFAULT_REPLIES = ("在呢，怎么啦？", "收到你的戳戳啦。", "我在这里。")
MAX_BACK_TIMES = 5
MODEL_REPLY_KIND = "conversation.poke_reply"
MODEL_REPLY_OWNER = "conversation.pokes"
MODEL_REPLY_PROMPT = (
    "把这次戳一戳当作轻量互动，按角色个性与当下状态自然回应，可以俏皮、亲近、犯困或简短接住。不推断对方为什么戳、当时的情绪或你们的关系，不编造刚发生的事；保持一条适合直接发送的简短正文，不解释选择或复述规则，最终输出遵循本轮宿主提供的回复格式。\n"
    "输出必须是 JSON 对象，格式：{\"text\": \"回复正文\", \"claims\": [{\"id\": \"c1\", \"kind\": \"subjective\", \"statement\": \"与正文相同的一句话\", \"evidence\": null}]}。"
    "text 与 statement 都不要句号；不要输出 JSON 之外的任何文字。"
)
CONTEXT_STREAM_PROMPT = (
    "拍一拍发生几秒后，你看到了群里最近的聊天记录（附在通知文本里）。"
    "请判断这段记录里有没有需要你接的内容：有人在问问题（不管是不是直接问你的）、"
    "有话题明显没聊完、有人提到你或点你名字，就回一句把那个接住；"
    "如果话题已经被别人接住、只是普通闲聊互怼、或与你无关，只输出 [SKIP]，不要输出任何别的内容。"
    "需要回应时保持一条适合直接发送的简短正文，按角色个性与当下状态自然说话，"
    "单条内部不换行，不解释选择或复述规则，最终输出遵循本轮宿主提供的回复格式。\n"
    "输出必须是 JSON 对象，格式：{\"text\": \"回复正文\", \"claims\": [{\"id\": \"c1\", \"kind\": \"subjective\", \"statement\": \"与正文相同的一句话\", \"evidence\": null}]}。"
    "text 与 statement 都不要句号；判[SKIP]时输出 JSON：{\"text\": \"[SKIP]\", \"claims\": [{\"id\": \"c1\", \"kind\": \"subjective\", \"statement\": \"[SKIP]\", \"evidence\": null}]}。"
    "不要输出 JSON 之外的任何文字。"
)


def poke_reply_profile(provider, *, max_output_tokens=1024):
    return ModelTaskProfile(MODEL_REPLY_KIND, Lane.BACKGROUND, provider,
        "依据已配置角色身份回应当前戳一戳互动。", max_output_tokens=max_output_tokens,
        capabilities=("text",), timeout_seconds=15)


@dataclass(frozen=True)
class PokePolicy:
    enabled: bool = False
    trigger_probability: float = 1.0
    replies: tuple[str, ...] = DEFAULT_REPLIES
    back_probability: float = 0.0
    back_times: int = 1
    model_reply_enabled: bool = False
    model_reply_probability: float = 0.3
    context_reply_enabled: bool = False
    context_delay_seconds: int = 5

    @classmethod
    def parse(cls, value):
        if value is None:
            value = {}
        if type(value) is not dict or set(value) - {
                "enabled", "trigger_probability", "replies", "back_probability", "back_times",
                "model_reply_enabled", "model_reply_probability",
                "context_reply_enabled", "context_delay_seconds"}:
            raise ValueError("poke settings contain unknown fields")
        enabled = value.get("enabled", False)
        replies = value.get("replies", list(DEFAULT_REPLIES))
        model_reply_enabled = value.get("model_reply_enabled", False)
        context_reply_enabled = value.get("context_reply_enabled", False)
        context_delay_seconds = value.get("context_delay_seconds", 5)
        if (type(enabled) is not bool or type(model_reply_enabled) is not bool
                or type(context_reply_enabled) is not bool
                or type(context_delay_seconds) is not int or not 0 <= context_delay_seconds <= 30
                or type(replies) is not list or not 1 <= len(replies) <= 20):
            raise ValueError("poke switch or replies are invalid")
        if any(type(item) is not str or not item.strip() or len(item) > 120 for item in replies):
            raise ValueError("poke replies must be short nonempty strings")

        def probability(name, default):
            number = value.get(name, default)
            if type(number) not in (int, float) or not isfinite(number) or not 0 <= number <= 1:
                raise ValueError("poke probability must be between zero and one")
            return float(number)

        back_times = value.get("back_times", 1)
        if type(back_times) is not int or not 1 <= back_times <= MAX_BACK_TIMES:
            raise ValueError("poke back count must be between one and five")

        return cls(enabled, probability("trigger_probability", 1.0), tuple(item.strip() for item in replies),
                   probability("back_probability", 0.0), back_times, model_reply_enabled,
                   probability("model_reply_probability", 0.3), context_reply_enabled, context_delay_seconds)

    @staticmethod
    def _fraction(event_key, purpose):
        return int(digest([event_key, purpose])[:13], 16) / 0x10000000000000

    def reply_for(self, event_key):
        if not self.enabled or self._fraction(event_key, "trigger") >= self.trigger_probability:
            return None
        index = int(digest([event_key, "reply"])[:13], 16) % len(self.replies)
        return self.replies[index]

    def use_model_reply(self, event_key):
        return (self.model_reply_enabled
                and self._fraction(event_key, "model_reply") < self.model_reply_probability)

    def poke_back(self, event_key):
        return self._fraction(event_key, "back") < self.back_probability


@dataclass(frozen=True)
class PokeReplyJob:
    context: TaskContext
    role_instruction: str
    event_key: str
    mode: str = "reply"
    recent_context: str = ""


class PokeReplyModel:
    """Generate one ephemeral response without recording a human conversation turn."""

    def __init__(self, runtime, *, now):
        self.runtime, self.now = runtime, now
        self.app = self.tasks = self.gateway = self.runner = None
        self.pending = {}
        self.results = {}
        self.invocations = set()
        self.closed = False
        self.registrations = (TaskRegistration(TaskDefinition(
            MODEL_REPLY_KIND, MODEL_REPLY_OWNER, "conversation.reply", Lane.BACKGROUND,
            timeout_seconds=20, max_attempts=1, concurrency=4), self.validate),)

    def attach(self, app):
        if app.runtime is not self.runtime:
            raise ValueError("poke model task must share its original runtime")
        self.app, self.tasks, self.gateway = app, app.tasks, app.gateway
        self.runner = ExactTaskRunner(self.tasks, frozenset({MODEL_REPLY_KIND}), self.generate,
                                      waiting=app.background_waiting)

    @outcome
    def validate(self, spec, at):
        self.app.require_running()
        job = self.pending.get(spec.task_id)
        if self.closed or job is None or job.context != spec.context or digest(job) != spec.request_ref:
            raise TaskError("poke_model_input_unavailable", "the original poke context is unavailable")
        require(self.tasks.check_context_access(job.context, action="conversation.reply", observed_at=at))
        return True

    async def reply(self, context, role_instruction, event_key, *, mode: str = "reply", recent_context: str = ""):
        if self.closed or self.runner is None:
            raise TaskError("poke_model_unavailable", "the supervised poke model is unavailable")
        if mode not in ("reply", "context"):
            raise TaskError("poke_model_context_invalid", "the poke reply mode is invalid")
        if not isinstance(context, TaskContext) or not isinstance(role_instruction, str) or not role_instruction.strip():
            raise TaskError("poke_model_context_invalid", "the original role context is unavailable")
        task_id = ("poke-context-reply-" if mode == "context" else "poke-reply-") + digest(event_key)
        job = PokeReplyJob(context, role_instruction, event_key, mode, str(recent_context or "").strip())
        self.pending[task_id] = job
        invocation = asyncio.current_task()
        self.invocations.add(invocation)
        try:
            now = self.tasks.now()
            submitted = require(self.tasks.submit(TaskSpec(task_id, context, MODEL_REPLY_KIND, digest(job),
                event_key, now, now + timedelta(seconds=20))))
            if submitted.state != "created":
                return None
            result = await self.runner.run(context, task_id)
            if (not result.is_success or result.unwrap().task is None
                    or result.unwrap().task.state != "succeeded"):
                return None
            return self.results.pop(task_id, None)
        finally:
            self.pending.pop(task_id, None)
            self.results.pop(task_id, None)
            self.invocations.discard(invocation)

    async def generate(self, run):
        job = self.pending[run.task.task_id]
        at = self.tasks.now()
        trigger = BackgroundPromptTrigger("poke-" + digest(job.event_key), job.context.actor.principal,
                                          job.context.scope, at)
        notice_text = "当前聊天中收到了一次戳一戳提示。"
        role_state = getattr(self.app, "role_state", None)
        if role_state is not None:
            try:
                view = role_state.read(job.context, at=at, injection=True)
                values = view.get("values") if isinstance(view, dict) else None
                if isinstance(values, dict):
                    parts = []
                    if isinstance(values.get("energy"), int):
                        parts.append(f"精力={values['energy']}")
                    for key, label in (("sleep", "作息"), ("busy", "状态")):
                        if isinstance(values.get(key), str) and values[key].strip():
                            parts.append(f"{label}={values[key].strip()[:60]}")
                    mood_val = values.get("mood")
                    if isinstance(mood_val, str) and mood_val.strip():
                        parts.append(f"心情={mood_val.strip()[:60]}")
                    elif isinstance(mood_val, dict) and isinstance(mood_val.get("description"), str) and mood_val["description"].strip():
                        parts.append(f"心情={mood_val['description'].strip()[:60]}")
                    if parts:
                        notice_text += "（角色当前自身状态参考：" + "，".join(parts) + "）"
            except Exception:
                pass
        recent_text = str(getattr(job, "recent_context", "") or "").strip()
        if recent_text:
            notice_text += "\n" + recent_text
        material = PromptMaterial(MODEL_REPLY_OWNER, "native_poke_notice", notice_text)
        context_mode = job.mode == "context"
        prompt_text = CONTEXT_STREAM_PROMPT if context_mode else MODEL_REPLY_PROMPT
        request = PromptRequest(trigger, job.role_instruction,
            SealedForegroundContext((material,), (), ()), prompt_text, job.context.role_binding)
        try:
            completion = await self.gateway.generate(run, request)
        except TaskError:
            # Reasoning models occasionally ignore the JSON wire contract; one
            # immediate retry keeps the stream usable without weakening it.
            completion = await self.gateway.generate(run, request)
        draft = completion.draft
        text = draft.text.strip() if draft is not None and draft.action == "text" else ""
        if context_mode and (text == "[SKIP]" or text.upper() == "SKIP"):
            run.validate()
            self.results[run.task.task_id] = ""
            return TaskResult("succeeded", result_ref=digest(""))
        if not text or len(text) > 240:
            if completion.call_id:
                self.gateway.ledger.discard(completion.call_id)
            raise TaskError("poke_model_output_invalid", "the poke response was empty or too long")
        run.validate()
        self.results[run.task.task_id] = text
        return TaskResult("succeeded", result_ref=digest(text))

    @property
    def active_handles(self):
        return ((self.runner.active_handles if self.runner is not None else ())
                + tuple("poke-model-" + str(id(task)) for task in self.invocations))

    async def close(self):
        self.closed = True
        if self.runner is not None:
            await self.runner.close()
        if self.invocations:
            await asyncio.wait(tuple(self.invocations), timeout=0.25)
