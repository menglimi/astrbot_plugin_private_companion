"""Turn one bounded phone observation into exactly one declared action.

The decider owns no device and no permission: it only asks the model for the
next action and validates the JSON contract.  Callers pass a ``generate``
coroutine, which keeps the prompt testable and lets the same decider serve a
reply turn or a scheduled action turn.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Sequence
import json

from .adb import UiElement
from .session import PhoneAction, PhoneObservation, PhoneStep, parse_action

INSTRUCTION = """你在操作一台已获授权、开启开发者模式的 Android 手机。你的目标在 goal 里。
每一步只能做一件事，并输出一个 JSON 对象；不要输出解释、Markdown 或第二个对象。
可用动作：
{"action":"tap_text","text":"界面上的可见文字"}  按文字点击（优先用这个）
{"action":"tap","x":540,"y":1200}                按坐标点击（仅当节点没有文字）
{"action":"launch","package":"com.example.app"}   启动应用（需要完整包名）
{"action":"type","text":"..."}                    在当前输入框输入可打印 ASCII
{"action":"key","key":"BACK"}                     HOME/BACK/RECENTS/ENTER/POWER 等
{"action":"wait_text","text":"...","seconds":5}   等某段文字出现
{"action":"done","reason":"完成依据"}             目标已达成
{"action":"fail","reason":"为什么做不到"}         无法继续
规则：
1. 截图已附上：红线是 100px 网格，边缘数字是坐标；绿框是可点节点，框内 [n] 与 observation 里的 [n] 对应。
2. 只依据截图或 observation 里真实出现的节点行动，不臆造按钮、包名或界面。
3. 有文字节点时优先 tap_text；坐标用于没有文字的图标或区域，按网格读数填写 x/y。
4. 一次只做一个动作；做完后你会收到新的截图和 observation。
5. 如果上一步显示"操作后画面没有变化"，不要重复同一个点击：可能被弹窗挡住、控件不可点或系统安全设置未开。先用 BACK 关掉弹窗，或换一个目标，两次无效就 fail。
6. 网页/应用里出现"打开 App""是否继续"之类弹窗时，先点取消/关闭或按 BACK，不要继续滑动底层页面。
7. 不要按 POWER 键；需要点亮屏幕时用 WAKEUP，系统会自己保证屏幕已亮。
8. 目标完成后立刻 done，并用一句理由说明你依据哪段可见文字确认的。
9. 连续两次失败、界面明显不相关或需要密码/支付/安装时，用 fail 停下，不要猜。
"""


class DecisionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _extract_json(text: str) -> dict:
    value = (text or "").strip()
    if value.startswith("```"):
        lines = [line for line in value.splitlines() if not line.strip().startswith("```")]
        value = "\n".join(lines).strip()
    try:
        payload = json.loads(value)
    except (TypeError, ValueError) as error:
        raise DecisionError("phone_decision_invalid", "the model did not return a JSON object") from error
    if type(payload) is not dict:
        raise DecisionError("phone_decision_invalid", "the model did not return a JSON object")
    if "action" in payload and "kind" not in payload:
        payload["kind"] = payload.pop("action")
    if "kind" not in payload:
        raise DecisionError("phone_decision_invalid", "the decision is missing its action kind")
    return payload


def parse_decision(text: str) -> PhoneAction:
    payload = _extract_json(text)
    try:
        return parse_action(payload)
    except ValueError as error:
        raise DecisionError("phone_decision_invalid", str(error)) from error


def decision_prompt(goal: str, observation: PhoneObservation, history: Sequence[PhoneStep]) -> str:
    lines = [f"goal: {goal}", "", "observation:", observation.prompt_text(), ""]
    if history:
        lines.append("history:")
        for step in history[-4:]:
            outcome = "成功" if step.ok else f"失败({step.code})"
            lines.append(f"- 第{step.step}步 {step.action.document()} -> {outcome}: {step.detail}")
    else:
        lines.append("history: 这是第一步。")
    lines.extend(["", 'step: 只输出一个 JSON 动作对象。'])
    return "\n".join(lines)


class ModelPhoneDecider:
    """One JSON action per observation, with exactly one repair attempt."""

    def __init__(self, generate: Callable[[str], Awaitable[str]]) -> None:
        self._generate = generate

    async def decide(self, goal: str, observation: PhoneObservation,
                     history: Sequence[PhoneStep]) -> PhoneAction:
        prompt = decision_prompt(goal, observation, history)
        first = await self._generate(INSTRUCTION + "\n\n" + prompt)
        try:
            return parse_decision(first)
        except DecisionError as error:
            repair = (prompt + "\n\n上一次输出不合约：" + str(error) +
                      "\n只重发一个符合契约的 JSON 对象，不要解释。")
            second = await self._generate(INSTRUCTION + "\n\n" + repair)
            try:
                return parse_decision(second)
            except DecisionError as final:
                raise DecisionError("phone_decision_invalid", str(final)) from final

    async def run(self, session):
        """Drive one session with this decider; the goal comes from the session."""

        async def decide(observation, history):
            return await self.decide(session.goal, observation, history)

        return await session.run_async(decide)
