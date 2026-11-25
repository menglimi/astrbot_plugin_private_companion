"""Bounded perceive -> decide -> act loop for one phone goal.

The session has no model and no scheduler of its own: a caller supplies the
decision function.  That keeps the loop testable, and lets the same bounded
runner serve a reply turn, a scheduled action turn or a diagnostic.  Every
step is recorded with the observation digest it was based on, and the loop
stops on its own after a step budget, a wall-clock deadline, a declared
``done``/``fail`` or two consecutive failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import time
from typing import Callable, Sequence

from .adb import AdbClient, AdbError, ImeTextInput, UiElement, select_elements, take_ime_restore_error

ACTION_KINDS = frozenset({"launch", "tap_text", "tap", "swipe", "type", "key", "wait_text", "force_stop",
                          "done", "fail"})
# Stopping an app is destructive; the operator must add it to the allowlist.
DEFAULT_ALLOWED = frozenset(ACTION_KINDS - {"force_stop"})
MUTATING = frozenset({"launch", "tap_text", "tap", "swipe", "type", "key", "force_stop"})
MAX_STEPS = 20
MAX_SECONDS = 180.0
MAX_ELEMENTS_IN_PROMPT = 24
SETTLE_SECONDS = 0.6
LAUNCH_SETTLE_SECONDS = 10.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class PhoneAction:
    kind: str
    text: str = ""
    x: int | None = None
    y: int | None = None
    package: str = ""
    key: str = ""
    seconds: float = 0.0
    reason: str = ""
    to_x: int | None = None
    to_y: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in ACTION_KINDS:
            raise ValueError("phone action must name a declared kind")
        if self.kind == "tap" and (self.x is None or self.y is None):
            raise ValueError("tap requires coordinates")
        if self.kind == "swipe" and None in (self.x, self.y, self.to_x, self.to_y):
            raise ValueError("swipe requires start and end coordinates")
        if self.kind in ("tap_text", "type") and not self.text.strip():
            raise ValueError("this action requires text")
        if self.kind == "launch" and not self.package.strip():
            raise ValueError("launch requires a package")
        if self.kind == "force_stop" and not self.package.strip():
            raise ValueError("force_stop requires a package")
        if self.kind == "key" and not self.key.strip():
            raise ValueError("key requires a key name")
        if self.kind == "wait_text" and self.seconds and not 0.5 <= self.seconds <= 30:
            raise ValueError("wait seconds must be between 0.5 and 30")

    def document(self) -> dict:
        value = {"kind": self.kind}
        for name in ("text", "x", "y", "to_x", "to_y", "package", "key", "seconds", "reason"):
            item = getattr(self, name)
            if item not in ("", None) and (name != "seconds" or item):
                value[name] = item
        return value


def parse_action(payload: dict) -> PhoneAction:
    """Accept only the declared JSON object; unknown keys are a hard error."""
    if type(payload) is not dict:
        raise ValueError("phone action must be a JSON object")
    allowed = {"kind", "text", "x", "y", "to_x", "to_y", "package", "key", "seconds", "reason"}
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError("phone action contains unknown fields: " + ", ".join(sorted(unknown)))
    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in ACTION_KINDS:
        raise ValueError("phone action kind is not declared")
    integer = lambda name: payload.get(name) if type(payload.get(name)) is int else None
    number = payload.get("seconds")
    return PhoneAction(
        kind=kind,
        text=str(payload.get("text") or "").strip()[:500],
        x=integer("x"), y=integer("y"),
        to_x=integer("to_x"), to_y=integer("to_y"),
        package=str(payload.get("package") or "").strip()[:160],
        key=str(payload.get("key") or "").strip()[:32],
        seconds=float(number) if type(number) in (int, float) else 0.0,
        reason=str(payload.get("reason") or "").strip()[:160],
    )


@dataclass(frozen=True)
class PhoneObservation:
    step: int
    width: int
    height: int
    elements: tuple[UiElement, ...]
    screenshot_sha256: str
    screenshot_bytes: int
    screenshot: bytes = field(default=b"", repr=False)
    stable: bool = True
    observed_at: str = field(default_factory=_now)
    ui_dump_degraded: bool = False
    ui_dump_error: str = ""

    def prompt_text(self, *, screenshot_attached: bool | None = None, annotated: bool = True) -> str:
        lines = [f"屏幕 {self.width}x{self.height}，第 {self.step} 步，截图摘要 {self.screenshot_sha256[:12]}。"]
        attached = bool(self.screenshot) if screenshot_attached is None else bool(screenshot_attached)
        if attached:
            if not annotated:
                lines.append("已附上当前手机原始截图。")
            elif self.ui_dump_degraded:
                lines.append("已附上截图：红色网格每 100px 一条，数字是坐标；本页未能读取控件树，未画节点框，请直接根据截图和坐标网格判断并操作。")
            elif self.stable:
                lines.append("已附上截图：红色网格每 100px 一条，数字是坐标；绿框为可点节点，框内 [n] 是节点序号。")
            else:
                lines.append("已附上截图：红色网格每 100px 一条，数字是坐标；本次画面不稳定，未画节点框，以截图为准。")
        elif self.screenshot:
            lines.append("警告：本次截图未能成功编码附带，当前没有可查看的画面图像；请仅依据下方文字节点判断，或如实说明无法读取屏幕画面。")
        if not self.stable and not self.ui_dump_degraded:
            lines.append("警告：截图与 UI 树在采集期间不一致（画面仍在变化），必要时先 wait 再操作。")
        shown = [item for item in self.elements if item.label][:MAX_ELEMENTS_IN_PROMPT]
        if not shown:
            if self.ui_dump_degraded:
                lines.append("当前页面未能读取控件树（常见于动态界面、WebView 或系统限制），请直接查看截图内容并通过坐标操作。")
            else:
                lines.append("当前没有可读文本节点。")
        for item in shown:
            tag = "可点" if item.clickable else "只读"
            lines.append(f"[{item.index}] {tag} {item.label!r} 中心={item.center} 包={item.package.split('.')[-1]}")
        return "\n".join(lines)


@dataclass(frozen=True)
class PhoneStep:
    step: int
    action: PhoneAction
    ok: bool
    detail: str
    code: str = ""
    observation_sha256: str = ""
    started_at: str = field(default_factory=_now)

    def document(self) -> dict:
        return {"step": self.step, "action": self.action.document(), "ok": self.ok,
                "detail": self.detail, "code": self.code,
                "observation": self.observation_sha256[:16], "at": self.started_at}


@dataclass(frozen=True)
class PhoneOutcome:
    state: str
    summary: str
    steps: tuple[PhoneStep, ...]

    def document(self) -> dict:
        return {"state": self.state, "summary": self.summary,
                "steps": [step.document() for step in self.steps]}


_RECOVERABLE_UI_DUMP_ERRORS = frozenset({
    "adb_ui_failed",
    "adb_ui_invalid",
    "adb_timeout",
    "adb_failed",
    "adb_output_too_large",
})


class PhoneSession:
    """One goal, one device, a hard step/deadline budget."""

    def __init__(self, client: AdbClient, *, goal: str, max_steps: int = MAX_STEPS,
                 max_seconds: float = MAX_SECONDS, allowed: Sequence[str] | None = None,
                 ime: str = "off", ime_restore_seconds: int = 30) -> None:
        goal = (goal or "").strip()
        if not goal:
            raise ValueError("a phone session needs a goal")
        if not 1 <= int(max_steps) <= MAX_STEPS:
            raise ValueError("phone session steps must stay within the declared budget")
        if not 5 <= float(max_seconds) <= 600:
            raise ValueError("phone session deadline must be between 5 and 600 seconds")
        if ime not in ("off", "adbkeyboard"):
            raise ValueError("phone session unicode input must name a declared backend")
        if type(ime_restore_seconds) is not int or not 5 <= ime_restore_seconds <= 120:
            raise ValueError("IME restore delay must be between 5 and 120 seconds")
        kinds = frozenset(allowed or DEFAULT_ALLOWED)
        if not kinds or not kinds <= ACTION_KINDS:
            raise ValueError("phone session actions must be declared kinds")
        self.client, self.goal = client, goal
        self.max_steps, self.max_seconds = int(max_steps), float(max_seconds)
        self.allowed = kinds
        self.ime = ime
        self.ime_restore_seconds = ime_restore_seconds
        self._started = 0.0

    def observe(self, step: int, *, read_only: bool = False) -> PhoneObservation:
        if read_only:
            self.client.require_readable_screen()
        else:
            error = take_ime_restore_error(self.client)
            if error is not None:
                raise error
            self.client.ensure_interactive()
        width, height = self.client.screen_size()
        first = self.client.screenshot()
        second = first
        elements, ui_dump_error = self._screen_elements()
        stable = False
        if not ui_dump_error:
            second = self.client.screenshot()
            stable = first == second
            if not stable:
                # Retry the tree against the newer frame before drawing node boxes.
                elements, ui_dump_error = self._screen_elements()
                if not ui_dump_error:
                    third = self.client.screenshot()
                    stable = third == second
                    second = third
        if read_only:
            self.client.require_readable_screen()
        return PhoneObservation(step, width, height, elements,
                                hashlib.sha256(second).hexdigest(), len(second), second, stable,
                                ui_dump_degraded=bool(ui_dump_error), ui_dump_error=ui_dump_error)

    def _screen_elements(self) -> tuple[tuple[UiElement, ...], str]:
        try:
            return self.client.ui_dump(), ""
        except AdbError as error:
            if error.code in _RECOVERABLE_UI_DUMP_ERRORS:
                return (), error.code
            raise

    def execute(self, action: PhoneAction) -> str:
        if action.kind not in self.allowed:
            raise AdbError("phone_action_forbidden", "this action is not allowed for the current session")
        if action.kind in MUTATING:
            self.client.ensure_interactive()
        if action.kind == "launch":
            return self.client.launch(action.package)
        if action.kind == "force_stop":
            return self.client.force_stop(action.package)
        if action.kind == "tap_text":
            element = self.client.tap_text(action.text)
            return f"点击 {element.label!r} @ {element.center}"
        if action.kind == "tap":
            self.client.tap(int(action.x or 0), int(action.y or 0))
            return f"点击 ({action.x},{action.y})"
        if action.kind == "swipe":
            self.client.swipe(action.x, action.y, action.to_x, action.to_y)
            return f"滑动 ({action.x},{action.y}) → ({action.to_x},{action.to_y})"
        if action.kind == "type":
            if all(32 <= ord(char) <= 126 for char in action.text):
                self.client.text(action.text)
                return f"输入 {len(action.text)} 个字符"
            if self.ime != "adbkeyboard":
                raise AdbError("adb_text_unsupported",
                               "non-ASCII text needs unicode_input=adbkeyboard in phone.json")
            return ImeTextInput(self.client, restore_seconds=self.ime_restore_seconds).type(action.text)
        if action.kind == "key":
            self.client.key(action.key)
            return f"按键 {action.key.upper()}"
        if action.kind == "wait_text":
            element = self.client.wait_text(action.text, timeout=action.seconds or 10.0)
            return f"等到 {element.label!r}"
        return ""

    def run(self, decide: Callable[[PhoneObservation, tuple[PhoneStep, ...]], PhoneAction]) -> PhoneOutcome:
        """Run until done/failed/budget; the decider never touches the device."""
        self._started = time.monotonic()
        steps: list[PhoneStep] = []
        observation = self.observe(0)
        while len(steps) < self.max_steps:
            if time.monotonic() - self._started >= self.max_seconds:
                return PhoneOutcome("exhausted", "行动超时，已停下", tuple(steps))
            action = decide(observation, tuple(steps))
            if not isinstance(action, PhoneAction):
                raise ValueError("the phone decider must return a PhoneAction")
            if action.kind == "done":
                steps.append(PhoneStep(len(steps) + 1, action, True, action.reason or "完成",
                                       observation_sha256=observation.screenshot_sha256))
                return PhoneOutcome("done", action.reason or "完成", tuple(steps))
            if action.kind == "fail":
                steps.append(PhoneStep(len(steps) + 1, action, False, action.reason or "放弃",
                                       code="phone_task_failed",
                                       observation_sha256=observation.screenshot_sha256))
                return PhoneOutcome("failed", action.reason or "放弃", tuple(steps))
            try:
                detail = self.execute(action)
                ok, code = True, ""
            except AdbError as error:
                detail, ok, code = str(error), False, error.code
            steps.append(PhoneStep(len(steps) + 1, action, ok, detail, code,
                                   observation.screenshot_sha256))
            try:
                if ok and action.kind in MUTATING:
                    time.sleep(LAUNCH_SETTLE_SECONDS if action.kind == "launch" else SETTLE_SECONDS)
                following = self.observe(len(steps))
            except AdbError as error:
                steps.append(PhoneStep(len(steps) + 1, PhoneAction("fail", reason="观察失败"),
                                       False, str(error), error.code, observation.screenshot_sha256))
                return PhoneOutcome("failed", "观察失败：" + str(error), tuple(steps))
            if ok and action.kind in MUTATING and _signature(observation) == _signature(following):
                steps[-1] = _ineffective(steps[-1])
                ok = False
            if not ok and len(steps) >= 2 and not steps[-2].ok:
                return PhoneOutcome("failed", "连续两次操作失败，已停下", tuple(steps))
            observation = following
        return PhoneOutcome("exhausted", "达到步数上限，已停下", tuple(steps))

    async def run_async(self, decide: Callable[[PhoneObservation, tuple[PhoneStep, ...]], object]) -> PhoneOutcome:
        """The same budget over async decisions; device I/O stays off the loop."""
        import asyncio

        self._started = time.monotonic()
        steps: list[PhoneStep] = []
        observation = await asyncio.to_thread(self.observe, 0)
        while len(steps) < self.max_steps:
            if time.monotonic() - self._started >= self.max_seconds:
                return PhoneOutcome("exhausted", "行动超时，已停下", tuple(steps))
            action = await decide(observation, tuple(steps))
            if not isinstance(action, PhoneAction):
                raise ValueError("the phone decider must return a PhoneAction")
            if action.kind == "done":
                steps.append(PhoneStep(len(steps) + 1, action, True, action.reason or "完成",
                                       observation_sha256=observation.screenshot_sha256))
                return PhoneOutcome("done", action.reason or "完成", tuple(steps))
            if action.kind == "fail":
                steps.append(PhoneStep(len(steps) + 1, action, False, action.reason or "放弃",
                                       code="phone_task_failed",
                                       observation_sha256=observation.screenshot_sha256))
                return PhoneOutcome("failed", action.reason or "放弃", tuple(steps))
            try:
                detail = await asyncio.to_thread(self.execute, action)
                ok, code = True, ""
            except AdbError as error:
                detail, ok, code = str(error), False, error.code
            steps.append(PhoneStep(len(steps) + 1, action, ok, detail, code,
                                   observation.screenshot_sha256))
            try:
                if ok and action.kind in MUTATING:
                    await asyncio.sleep(LAUNCH_SETTLE_SECONDS if action.kind == "launch" else SETTLE_SECONDS)
                following = await asyncio.to_thread(self.observe, len(steps))
            except AdbError as error:
                steps.append(PhoneStep(len(steps) + 1, PhoneAction("fail", reason="观察失败"),
                                       False, str(error), error.code, observation.screenshot_sha256))
                return PhoneOutcome("failed", "观察失败：" + str(error), tuple(steps))
            if ok and action.kind in MUTATING and _signature(observation) == _signature(following):
                steps[-1] = _ineffective(steps[-1])
                ok = False
            if not ok and len(steps) >= 2 and not steps[-2].ok:
                return PhoneOutcome("failed", "连续两次操作失败，已停下", tuple(steps))
            observation = following
        return PhoneOutcome("exhausted", "达到步数上限，已停下", tuple(steps))


def _signature(observation: PhoneObservation) -> tuple:
    return (observation.screenshot_sha256,
            tuple((item.index, item.label, item.bounds, item.clickable) for item in observation.elements))


def _ineffective(step: PhoneStep) -> PhoneStep:
    return replace(step, ok=False, code="phone_action_ineffective",
                   detail="操作后画面与节点树都没有变化：可能被弹窗挡住、控件不可点或系统安全设置拦截")


def describe_elements(elements: Sequence[UiElement], text: str) -> tuple[UiElement, ...]:
    """Expose the same ranking the tap action uses, for prompt builders."""
    return select_elements(elements, text)
