"""Runtime state the Zone extension must remember: cooling down and spacing out.

Two lessons from the previous implementation are encoded here as decisions rather
than as call-site checks: a broken login must stop the extension *before* any
model spend (progressive cooldown, cleared as soon as a call succeeds), and a
public action must not repeat itself within its own interval or its daily cap.
The state is one ``memory_views`` row, so no schema migration is needed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
import json

from companion_v2.execution.models import digest, encode, stamp
from companion_v2.storage.runtime import StorageRuntime

from .topics import allowed_scenes, normalize_scene, normalize_topic, topic_spec, validate_confidence


KIND = "qzone.runtime.v1"
COOLDOWN_STEPS = (12.0, 24.0, 24.0 * 7)
FAILURE_MEMORY_SECONDS = 7 * 24 * 3600
SIMILARITY_THRESHOLD = 2
RECENT_PUBLISH_TEXTS = 8
PUBLISH_KINDS = ("publish",)


@dataclass(frozen=True)
class QzoneRuntimeState:
    auth_block_until: str = ""
    auth_failure_count: int = 0
    last_auth_failure_reason: str = ""
    last_auth_failure_source: str = ""
    last_auth_status: str = ""
    last_auth_failed_at: str = ""
    last_action_at: dict = field(default_factory=dict)
    publish_day: str = ""
    publish_count: int = 0
    topic_last_action_at: dict = field(default_factory=dict)
    topic_publish_day: dict = field(default_factory=dict)
    topic_publish_count: dict = field(default_factory=dict)
    recent_publish_texts: tuple = ()
    recent_publish_media: tuple = ()

    @classmethod
    def restore(cls, value):
        if not isinstance(value, dict):
            return cls()
        return cls(auth_block_until=str(value.get("auth_block_until") or ""),
                   auth_failure_count=int(value.get("auth_failure_count") or 0),
                   last_auth_failure_reason=str(value.get("last_auth_failure_reason") or "")[:200],
                   last_auth_failure_source=str(value.get("last_auth_failure_source") or "")[:40],
                   last_auth_status=str(value.get("last_auth_status") or "")[:20],
                   last_auth_failed_at=str(value.get("last_auth_failed_at") or ""),
                   last_action_at={str(key): str(item) for key, item in
                                    dict(value.get("last_action_at") or {}).items()},
                   publish_day=str(value.get("publish_day") or ""),
                   publish_count=int(value.get("publish_count") or 0),
                   topic_last_action_at={str(key): str(item) for key, item in
                                         dict(value.get("topic_last_action_at") or {}).items()},
                   topic_publish_day={str(key): str(item) for key, item in
                                      dict(value.get("topic_publish_day") or {}).items()},
                   topic_publish_count={str(key): int(item or 0) for key, item in
                                        dict(value.get("topic_publish_count") or {}).items()},
                   recent_publish_texts=tuple(str(item)[:400] for item in
                                               (value.get("recent_publish_texts") or ()))[:RECENT_PUBLISH_TEXTS],
                   recent_publish_media=tuple(str(item) for item in
                                              (value.get("recent_publish_media") or ()))[-RECENT_PUBLISH_TEXTS:])

    def document(self):
        return asdict(self)


def _moment(value):
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def block_reason(state, *, at):
    """Why no Zone call may start now; an expired cooldown answers with "" politely."""
    until = _moment(state.auth_block_until)
    if until is None or until <= at:
        return ""
    reason = state.last_auth_failure_reason or "QQ 空间登录状态异常"
    return f"{reason}（自动暂停到 {until:%m-%d %H:%M}）"


def note_failure(state, *, reason, source, at, recoverable=False):
    """Record one credential failure; a missing connection must not escalate."""
    if recoverable:
        return replace(state, last_auth_failure_reason=str(reason or "")[:200],
                       last_auth_failure_source=str(source or "")[:40], last_auth_status="missing")
    previous = _moment(state.last_auth_failed_at)
    count = state.auth_failure_count
    if previous is None or (at - previous).total_seconds() > FAILURE_MEMORY_SECONDS:
        count = 0
    count += 1
    hours = COOLDOWN_STEPS[0] if count == 1 else COOLDOWN_STEPS[1] if count == 2 else COOLDOWN_STEPS[2]
    return replace(state, auth_failure_count=count, last_auth_failed_at=stamp(at),
                   last_auth_failure_reason=str(reason or "")[:200],
                   last_auth_failure_source=str(source or "")[:40],
                   last_auth_status="stopped" if count >= 3 else "blocked",
                   auth_block_until=stamp(at + timedelta(hours=hours)))


def clear_failure(state):
    if not state.auth_block_until and not state.auth_failure_count:
        return state
    return replace(state, auth_block_until="", auth_failure_count=0, last_auth_failure_reason="",
                   last_auth_failure_source="", last_auth_status="ready")


def action_reason(state, settings, *, kind, at, day):
    """Spacing, daily cap and enablement for one public action."""
    if not settings.get("enabled"):
        return "QQ 空间能力已关闭"
    if kind == "like" and not settings.get("like_enabled"):
        return "点赞未开启"
    if kind == "comment" and not settings.get("comment_enabled"):
        return "回复未开启"
    if kind == "publish":
        if not settings.get("publish_enabled"):
            return "发空间未开启"
        if state.publish_day == day and state.publish_count >= int(settings.get("publish_max_daily") or 1):
            return "今天的说说已经发过了"
    last = _moment(state.last_action_at.get(kind))
    gap = timedelta(minutes=int(settings.get("action_min_interval_minutes") or 30))
    if last is not None and at - last < gap:
        return f"距离上次同类动作不到 {int(gap.total_seconds() // 60)} 分钟"
    return ""


def note_action(state, *, kind, at):
    return replace(state, last_action_at={**state.last_action_at, kind: stamp(at)})


def ngram_shared(left, right, *, size=3):
    """Shared 3-gram count, the previous implementation's duplicate measure."""
    first = _ngrams(left, size)
    second = _ngrams(right, size)
    return len(first & second)


def _ngrams(text, size):
    compact = "".join(str(text or "").split())
    if len(compact) < size:
        return {compact} if compact else set()
    return {compact[index:index + size] for index in range(len(compact) - size + 1)}


def topic_reason(state, settings, *, topic, scene, confidence, topic_confirmed, at, day):
    """Return the classified-post gate as ``(code, message)``.

    The defaults deliberately keep old in-process callers usable.  A caller that
    supplies a topic still gets the new gates, while a legacy settings object that
    has no topic fields falls back to the topic's declared scenes and no extra
    confidence threshold.
    """
    if topic is None or (isinstance(topic, str) and not topic.strip()):
        return "qzone_topic_required", "发布前必须先确定主题"
    if scene is None or (isinstance(scene, str) and not scene.strip()):
        return "qzone_scene_required", "发布前必须先确定发送场景"
    try:
        topic = normalize_topic(topic)
    except ValueError:
        return "qzone_topic_invalid", "主题不在可用目录中"
    try:
        scene = normalize_scene(scene)
    except ValueError:
        return "qzone_scene_invalid", "发送场景不在可用目录中"
    try:
        confidence = validate_confidence(confidence)
    except ValueError:
        return "qzone_topic_confidence_invalid", "主题判断置信度必须是 0 到 1 之间的数字"
    if scene not in topic_spec(topic).scenes:
        return "qzone_scene_invalid", "这个场景不适合所选主题"
    topic_key = topic.value
    if not settings.get(f"{topic_key}_enabled", True):
        return "qzone_topic_disabled", "这个主题目前未开启"
    configured = settings.get(f"{topic_key}_scenes")
    if configured is None:
        configured_scenes = topic_spec(topic).scenes
    else:
        try:
            configured_scenes = allowed_scenes(configured)
        except (TypeError, ValueError):
            return "qzone_topic_not_allowed", "这个主题的场景配置无效"
    if scene not in configured_scenes:
        return "qzone_topic_not_allowed", "这个场景没有在主题设置中放行"
    try:
        minimum = float(settings.get(f"{topic_key}_min_confidence", 0.0))
    except (TypeError, ValueError):
        minimum = 0.0
    if confidence < minimum:
        return "qzone_topic_confidence_low", f"主题判断置信度需要达到 {minimum:.2f}"
    if type(topic_confirmed) is not bool:
        return "qzone_request_invalid", "主题确认状态必须是布尔值"
    if not topic_confirmed and (settings.get("topic_require_confirmation", True)
                                or not settings.get("topic_auto_enabled", False)):
        return "qzone_topic_confirmation_required", "发布前需要确认主题和场景"
    topic_day = state.topic_publish_day.get(topic_key)
    try:
        daily_max = int(settings.get(f"{topic_key}_daily_max", settings.get("publish_max_daily") or 1))
    except (TypeError, ValueError):
        daily_max = 1
    if topic_day == day and state.topic_publish_count.get(topic_key, 0) >= daily_max:
        return "qzone_topic_daily_limit", "这个主题今天的发布额度已经用完"
    last = _moment(state.topic_last_action_at.get(topic_key))
    try:
        cooldown = int(settings.get(f"{topic_key}_cooldown_minutes",
                                    settings.get("action_min_interval_minutes") or 30))
    except (TypeError, ValueError):
        cooldown = 30
    if last is not None and at - last < timedelta(minutes=cooldown):
        return "qzone_topic_cooldown", f"这个主题还在冷却中，至少间隔 {cooldown} 分钟"
    return "", ""


def publish_reason(state, settings, *, text, at, day, topic=None, scene=None,
                   confidence=None, topic_confirmed=True, media_key=""):
    """Everything that must be true before a post: enablement, cap, spacing, novelty."""
    reason = action_reason(state, settings, kind="publish", at=at, day=day)
    if reason:
        return reason
    if topic is not None:
        _code, reason = topic_reason(state, settings, topic=topic, scene=scene,
                                      confidence=confidence, topic_confirmed=topic_confirmed,
                                      at=at, day=day)
        if reason:
            return reason
    if media_key:
        return "这条说说和刚发过的图文重复" if media_key in state.recent_publish_media else ""
    compact = "".join(str(text or "").split())
    if not compact:
        return "说说正文是空的"
    for previous in state.recent_publish_texts:
        if "".join(previous.split()) == compact:
            return "这条说说和刚发过的重复"
        if ngram_shared(previous, compact) >= SIMILARITY_THRESHOLD:
            return "这条说说和刚发过的太像"
    return ""


def note_publish(state, *, text, at, day, topic=None, media_key=""):
    recent = state.recent_publish_texts if media_key else tuple([*state.recent_publish_texts, str(text)[:400]])[-RECENT_PUBLISH_TEXTS:]
    count = state.publish_count + 1 if state.publish_day == day else 1
    changes = dict(recent_publish_texts=recent, publish_day=day, publish_count=count,
                   last_action_at={**state.last_action_at, "publish": stamp(at)})
    if media_key:
        changes["recent_publish_media"] = (*state.recent_publish_media, media_key)[-RECENT_PUBLISH_TEXTS:]
    if topic is not None:
        topic_key = normalize_topic(topic).value
        topic_day = dict(state.topic_publish_day)
        topic_count = dict(state.topic_publish_count)
        previous_day = topic_day.get(topic_key)
        topic_day[topic_key] = day
        topic_count[topic_key] = topic_count.get(topic_key, 0) + 1 if previous_day == day else 1
        changes.update(topic_last_action_at={**state.topic_last_action_at, topic_key: stamp(at)},
                       topic_publish_day=topic_day, topic_publish_count=topic_count)
    return replace(state, **changes)


class QzoneRuntimeStore:
    """One row per role owner; the document is small, bounded and versioned."""

    UPSERT = """INSERT INTO memory_views VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
        ON CONFLICT(view_id) DO UPDATE SET revision = excluded.revision,
            document_json = excluded.document_json, updated_at = excluded.updated_at"""

    def __init__(self, runtime: StorageRuntime):
        self.runtime = runtime

    @staticmethod
    def view_id(owner_key):
        return "qzone-runtime:" + digest(owner_key)[:24]

    def read(self, owner_key):
        row = self.runtime.read_one("SELECT document_json FROM memory_views WHERE view_id = ? AND kind = ?",
                                    (self.view_id(owner_key), KIND))
        if row is None:
            return QzoneRuntimeState()
        try:
            return QzoneRuntimeState.restore(json.loads(row["document_json"]))
        except (ValueError, TypeError):
            return QzoneRuntimeState()

    def write(self, owner_key, state, *, at):
        view_id = self.view_id(owner_key)
        with self.runtime.transaction() as connection:
            row = connection.execute("SELECT revision FROM memory_views WHERE view_id = ?", (view_id,)).fetchone()
            revision = int(row["revision"]) + 1 if row is not None else 1
            connection.execute(self.UPSERT, (view_id, owner_key, KIND, revision,
                json.dumps(state.document(), ensure_ascii=False, sort_keys=True),
                encode({"owner_key": owner_key}), stamp(at)))
        return state


__all__ = ["COOLDOWN_STEPS", "KIND", "QzoneRuntimeState", "QzoneRuntimeStore", "SIMILARITY_THRESHOLD",
           "action_reason", "block_reason", "clear_failure", "ngram_shared", "note_action", "note_failure",
           "note_publish", "publish_reason", "topic_reason"]
