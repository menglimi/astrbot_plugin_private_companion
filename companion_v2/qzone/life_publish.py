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
import json

from companion_v2.execution.models import digest, normalize_utc

from .service import local_day
from .topics import QzoneScene, QzoneTopic


KIND = "qzone.life_publish.v1"
ATTEMPT_KEY = "life_publish_attempt"
RETRY_KEY = "life_publish_retry_at"
TOPIC = QzoneTopic.LIFE_DOCUMENTARY
SCENE = QzoneScene.ROLE_ACTIVITY
MAX_TEXT_CHARS = 300
MAX_PROFILE_CHARS = 1200
MAX_DIARY_CHARS = 800
MAX_STYLE_CHARS = 512
MAX_RECENT_POSTS = 5
MAX_RECENT_POST_CHARS = 120
MAX_LIFE_ITEMS = 6
MAX_ALBUM_CANDIDATES = 8
MAX_IMAGE_PROMPT_CHARS = 1000


@dataclass(frozen=True)
class Decision:
    due: bool
    reason: str
    day: str


@dataclass(frozen=True)
class LifeDraft:
    text: str
    event_summary: str = ""
    image_source: str = "none"
    album_image_id: str = ""
    image_prompt: str = ""
    include_character: bool = False


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
    if (settings.get("daily_limits_enabled", True) and state.publish_day == day
            and state.publish_count >= cap):
        return Decision(False, "daily_limit", day)
    # A failed draft/publish gets a short, explicit retry window instead of
    # occupying the full publishing interval.  Unknown publish results use a
    # long retry window (set by the host), so they are still never replayed.
    retry_at = _moment(state.last_action_at.get(RETRY_KEY))
    last_publish = _moment(state.last_action_at.get("publish"))
    if retry_at is not None and (last_publish is None or last_publish < retry_at):
        if at < retry_at:
            return Decision(False, "retry_wait", day)
        return Decision(True, "due", day)
    try:
        hours = max(1, int(settings.get("life_publish_min_interval_hours") or 6))
    except (TypeError, ValueError):
        hours = 6
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


def _recent_posts_block(recent_publish_texts):
    if not recent_publish_texts or isinstance(recent_publish_texts, (str, bytes)):
        return ""
    snippets = []
    for item in tuple(recent_publish_texts)[-MAX_RECENT_POSTS:]:
        cleaned = _clean(item, MAX_RECENT_POST_CHARS)
        if cleaned:
            snippets.append(f"- {cleaned}")
    return "\n".join(snippets)


def _diary_block(diary):
    if not diary:
        return ""
    if isinstance(diary, str):
        return _clean(diary, MAX_DIARY_CHARS)
    if isinstance(diary, (list, tuple)):
        lines = []
        used = 0
        for item in diary[:3]:
            if isinstance(item, dict):
                day = _clean(item.get("local_date"), 20)
                body = _clean(item.get("body"), 400)
                line = f"[{day}] {body}".strip() if day and body else body
            else:
                line = _clean(item, 400)
            if not line:
                continue
            if used + len(line) > MAX_DIARY_CHARS:
                remain = MAX_DIARY_CHARS - used
                if remain > 20:
                    lines.append(line[:remain])
                break
            lines.append(line)
            used += len(line)
        return "\n".join(lines)
    return _clean(diary, MAX_DIARY_CHARS)


def _activities_block(activities):
    if not activities or isinstance(activities, (str, bytes)):
        return ""
    lines = []
    for item in tuple(activities)[:MAX_LIFE_ITEMS]:
        if isinstance(item, dict):
            state = _clean(item.get("state"), 24)
            desc = _clean(item.get("description"), 140)
            checkpoint = _clean(item.get("checkpoint"), 100)
            reflection = _clean(item.get("reflection"), 120)
            parts = [f"[{state}]" if state else "", desc]
            if checkpoint:
                parts.append(f"进度：{checkpoint}")
            if reflection:
                parts.append(f"感想：{reflection}")
            line = " ".join(part for part in parts if part)
        else:
            line = _clean(item, 180)
        if line:
            lines.append(f"- {line}")
    return "\n".join(lines)


def _schedules_block(schedules):
    if not schedules or isinstance(schedules, (str, bytes)):
        return ""
    lines = []
    for item in tuple(schedules)[:MAX_LIFE_ITEMS]:
        if isinstance(item, dict):
            start = _clean(item.get("start"), 32)
            end = _clean(item.get("end"), 32)
            title = _clean(item.get("title"), 100)
            detail = _clean(item.get("detail"), 120)
            state = _clean(item.get("state"), 24)
            window = f"{start}~{end}" if start and end else start
            parts = [f"[{state}]" if state else "", window, title]
            if detail:
                parts.append(f"({detail})")
            line = " ".join(part for part in parts if part)
        else:
            line = _clean(item, 180)
        if line:
            lines.append(f"- {line}")
    return "\n".join(lines)


def _album_block(album_candidates):
    if not album_candidates or isinstance(album_candidates, (str, bytes)):
        return ""
    lines = []
    for item in tuple(album_candidates)[:MAX_ALBUM_CANDIDATES]:
        if not isinstance(item, dict):
            continue
        image_id = _clean(item.get("image_id"), 180)
        if not image_id:
            continue
        created = _clean(item.get("created_at"), 32)
        summary = _clean(item.get("summary"), 120)
        lines.append(f"- image_id={image_id} | 时间={created} | 说明={summary or '相册图片'}")
    return "\n".join(lines)


def build_request(profile, diary, *, now, style="", recent_publish_texts=(),
                  activities=(), schedules=(), album_candidates=(), can_generate_image=False):
    """Build the life-publish drafting prompt (plain text or event-matched image+text plan)."""
    at = normalize_utc(now, field="qzone life publish clock")
    system = ("你就是这个人，用自己的口吻写 QQ 空间说说。只写你真实经历过或正在经历的"
              "日常、感受和观察，不编造没有发生的事，不提 AI、模型、插件或提示词，"
              "不写系统说明，不把计划写成已经发生。")
    facts = profile_line(profile)
    fragments = []
    if facts:
        fragments.append("角色资料：" + facts)
    memory = _diary_block(diary)
    if memory:
        fragments.append("最近日记（仅作生活素材，不要照抄，也不要提到日记本身）：\n" + memory)
    activity_block = _activities_block(activities)
    if activity_block:
        fragments.append("最近生活活动（completed/active 为真实经历或正在进行；planned/paused/cancelled 未完成，不要写成已完成）：\n" + activity_block)
    schedule_block = _schedules_block(schedules)
    if schedule_block:
        fragments.append("角色近期日程安排（参考时间与事件；尚未到时间或未发生的计划不要写成已经经历过）：\n" + schedule_block)
    recent_block = _recent_posts_block(recent_publish_texts)
    if recent_block:
        fragments.append("最近已经发过的说说（请避开重复的话题、句式和用词，写点新的日常）：\n" + recent_block)
    album_block = _album_block(album_candidates)
    if album_block:
        fragments.append("当前角色相册可用图片（只有和选定事件贴合时才选用，不要硬凑）：\n" + album_block)
    fragments.append("现在是 " + at.astimezone().strftime("%Y-%m-%d %H:%M"))
    extra = _clean(style, MAX_STYLE_CHARS)
    if extra:
        fragments.append("表达风格参考（只影响语气和用词，不改变事实）：" + extra)
    if can_generate_image or album_block:
        sources = []
        if can_generate_image:
            sources.append("generate（为同一个事件生成一张同场景配图）")
        if album_block:
            sources.append("album（从上面相册挑一张与同一个事件贴合的既有图片）")
        sources.append("none（不配图，仅发纯文字）")
        fragments.append(
            "请先从上面的日记、生活活动或日程中挑一个具体且真实发生/正在经历的时刻，"
            "围绕这【同一个事件】写出 60 到 150 字的第一人称空间说说正文，并决定是否配图（"
            + "、".join(sources) + "），确保文案与配图画面一致。\n"
            "只输出一个 JSON 对象，不要 Markdown 代码块或额外解释，字段固定为：\n"
            '{"event_summary":"不超过60字的事件概要","text":"60到150字的空间说说正文",'
            '"image_source":"' + ("generate|album|none" if (can_generate_image and album_block)
                                  else "generate|none" if can_generate_image else "album|none") + '",'
            '"album_image_id":"选用 album 时填上面的 image_id，否则留空",'
            '"image_prompt":"选用 generate 时填同场景画面描述（不含文字水印或隐私信息），否则留空",'
            '"include_character":false}\n'
            "注意：只有角色本人需要出现在画面里（如散步、读书、自拍）时 include_character 才填 true，纯景物或静物填 false。"
        )
    else:
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


def _strip_json_fences(raw):
    value = str(raw or "").strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return value


def parse_plan(raw, *, allowed_album_ids=(), allow_generate=False):
    """Parse structured image+text JSON draft, falling back cleanly to plain text."""
    stripped = _strip_json_fences(raw)
    try:
        data = json.loads(stripped)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict) and "text" in data:
        text = parse_draft(data.get("text"))
        event_summary = _clean(data.get("event_summary"), 200)
        image_source = str(data.get("image_source") or "none").strip().lower()
        album_image_id = str(data.get("album_image_id") or "").strip()
        image_prompt = _clean(data.get("image_prompt"), MAX_IMAGE_PROMPT_CHARS)
        include_character = data.get("include_character") is True
        allowed = {str(item).strip() for item in (allowed_album_ids or ()) if str(item).strip()}
        if image_source == "album" and album_image_id in allowed:
            return LifeDraft(
                text=text,
                event_summary=event_summary,
                image_source="album",
                album_image_id=album_image_id,
                image_prompt=image_prompt,
                include_character=include_character,
            )
        if image_source in ("album", "generate") and allow_generate and image_prompt:
            return LifeDraft(
                text=text,
                event_summary=event_summary,
                image_source="generate",
                album_image_id="",
                image_prompt=image_prompt,
                include_character=include_character,
            )
        return LifeDraft(text=text, event_summary=event_summary)
    return LifeDraft(text=parse_draft(raw))


__all__ = ["ATTEMPT_KEY", "Decision", "KIND", "LifeDraft", "MAX_TEXT_CHARS", "SCENE", "TOPIC",
           "build_request", "decide", "operation_key", "parse_draft", "parse_plan", "profile_line",
           "should_attempt"]
