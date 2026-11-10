"""V1「生活说说」：低频、按概率把角色自己的生活发到 QQ 空间。

V1 的对应配置是 ``enable_qzone_life_publish`` +
``qzone_life_publish_min_interval_hours`` + ``qzone_life_publish_probability``
+ ``qzone_life_publish_max_daily``。V2 已经有一套完整的空间发布链路
（``qzone/service.py``：capability、凭据、policy、operation 账本、未知结果
不重放），缺的只是「什么时候写、写什么、什么时候发」这一层。

分工和评论扫描一致：本模块只做**判定与文本准备**，不发网络请求。
真正的发布交给 ``QzoneService.publish``，间隔/每日上限/去重仍由
``qzone/policy.py`` 在发布时再检查一次。

两条纪律：
* **先记账再调用模型**：模型调用和发布都可能耗时或失败，先写 ``attempt``
  标记，失败或结果未知都不会在下一个检查周期立刻重来。
* **同一次尝试只有一个操作键**：``operation_key`` 由「角色 + 尝试时刻」派生，
  未知结果不会被当成新操作重发。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from companion_v2.execution.models import digest, normalize_utc

from .service import local_day
from .topics import QzoneScene, QzoneTopic


KIND = "qzone.life_publish.v1"
ATTEMPT_KEY = "life_publish_attempt"
TOPIC = QzoneTopic.LIFE_DOCUMENTARY
SCENE = QzoneScene.ROLE_ACTIVITY
MAX_TEXT_CHARS = 300
MAX_PROFILE_CHARS = 1200
MAX_DIARY_CHARS = 800
MAX_STYLE_CHARS = 512


@dataclass(frozen=True)
class Decision:
    due: bool
    reason: str
    day: str


def _moment(value):
    try:
        return normalize_utc(datetime.fromisoformat(str(value)), field="qzone life publish clock")
    except (TypeError, ValueError):
        return None


def _last_attempt(state):
    """The most recent publish **or** attempt, so a failure still spaces the retry."""
    values = [_moment(state.last_action_at.get("publish")), _moment(state.last_action_at.get(ATTEMPT_KEY))]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def decide(settings, state, *, now, day=None):
    """Whether this check should try one post. Pure: no clock, no network, no model."""
    at = normalize_utc(now, field="qzone life publish clock")
    day = day or local_day(at)
    if not settings.get("enabled"):
        return Decision(False, "qzone_disabled", day)
    if not settings.get("publish_enabled"):
        return Decision(False, "publish_disabled", day)
    if not settings.get("life_publish_enabled"):
        return Decision(False, "life_publish_disabled", day)
    try:
        cap = max(1, int(settings.get("life_publish_max_daily") or 1))
    except (TypeError, ValueError):
        cap = 1
    if state.publish_day == day and state.publish_count >= cap:
        return Decision(False, "daily_limit", day)
    try:
        hours = max(1, int(settings.get("life_publish_min_interval_hours") or 24))
    except (TypeError, ValueError):
        hours = 24
    last = _last_attempt(state)
    if last is not None and at - last < timedelta(hours=hours):
        return Decision(False, "interval", day)
    return Decision(True, "due", day)


def should_attempt(settings, draw):
    """One probability roll. ``0`` never posts, ``1`` always posts."""
    try:
        probability = float(settings.get("life_publish_probability") or 0.0)
    except (TypeError, ValueError):
        probability = 0.0
    probability = min(1.0, max(0.0, probability))
    if probability <= 0:
        return False
    if probability >= 1:
        return True
    return draw < probability


def operation_key(owner, day, attempt_at):
    """Stable for one attempt, distinct across attempts and roles."""
    return "qzone-life:" + digest([KIND, str(owner), str(day),
                                   normalize_utc(attempt_at, field="qzone life publish clock").isoformat()])


def _clean(value, limit):
    text = " ".join(str(value or "").split())
    return text[:limit]


def profile_line(profile):
    """One bounded line of stable character facts for the drafting prompt."""
    if not isinstance(profile, dict):
        return ""
    parts = []
    for key, label in (("name", "姓名"), ("gender", "性别"), ("species", "种族"),
                       ("personality", "性格"), ("occupation", "身份"),
                       ("hobbies", "爱好"), ("daily_habits", "日常")):
        value = _clean(profile.get(key), 160)
        if value:
            parts.append(f"{label}：{value}")
    return _clean("；".join(parts), MAX_PROFILE_CHARS)


def build_request(profile, diary, *, now, style=""):
    """The drafting prompt: pure text in, pure text out."""
    at = normalize_utc(now, field="qzone life publish clock")
    system = ("你就是这个人，用自己的口吻写 QQ 空间说说。只写你真实经历过或正在经历的"
              "日常、感受和观察，不编造没有发生的事，不提 AI、模型、插件或提示词，"
              "不写系统说明，不把计划写成已经发生。")
    facts = profile_line(profile)
    fragments = []
    if facts:
        fragments.append("角色资料：" + facts)
    memory = _clean(diary, MAX_DIARY_CHARS)
    if memory:
        fragments.append("最近日记（仅作生活素材，不要照抄，也不要提到日记本身）：" + memory)
    fragments.append("现在是 " + at.astimezone().strftime("%Y-%m-%d %H:%M"))
    extra = _clean(style, MAX_STYLE_CHARS)
    if extra:
        fragments.append("表达风格参考（只影响语气和用词，不改变事实）：" + extra)
    fragments.append("写一条 60 到 150 字的空间说说，第一人称，具体、有生活感。"
                     "只输出正文本身，不要标题、引号、Markdown、话题标签或解释。")
    return {"system": system, "prompt": "\n\n".join(fragments)}


def parse_draft(text):
    """Strip fences/quotes and enforce the publish contract's char limit."""
    value = str(text or "").strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    for opening, closing in (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’")):
        if len(value) >= 2 and value.startswith(opening) and value.endswith(closing):
            value = value[1:-1].strip()
            break
    value = "\n".join(line.strip() for line in value.splitlines() if line.strip())
    if not value:
        raise ValueError("empty draft")
    if len(value) > MAX_TEXT_CHARS:
        raise ValueError("draft exceeds the public text limit")
    return value


__all__ = ["ATTEMPT_KEY", "Decision", "KIND", "MAX_TEXT_CHARS", "SCENE", "TOPIC",
           "build_request", "decide", "operation_key", "parse_draft", "profile_line",
           "should_attempt"]
