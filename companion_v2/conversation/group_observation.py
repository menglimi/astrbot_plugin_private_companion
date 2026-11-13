"""Bounded group observation material: topics, mood, moments, joke boundary, denoise.

V1 kept a per-group observation subsystem (``group_observation.py``,
``group_mood.py``, ``group_moments.py``, ``joke_boundary.py``,
``roleplay_strength.py``). V2 keeps the deterministic, cheap half of it and
records it as reference material for the group prompt:

* everything is folded from already-admitted, receipt-validated group material,
  so a message that is not in the authorized window cannot contribute;
* records belong to the group's own memory owner and live on the shared
  ``runtime_metadata`` table, so this slice adds no table and no schema version;
* a record never stores a member identifier, never claims anything about a
  member, and never produces an action against a member. Topic participation
  counts are derived from the current visible window; V1's per-member joke
  sensitivity profile is deliberately not migrated.

The module owns no model call, no clock, no send path and no read privilege of
its own; prompt wording lives in ``participation_prompts``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re

from companion_v2.execution.models import digest
from .group_timing import strip_address_markers, clean_group_text, repeat_signature


PREFIX = "conversation.group_observation.v1"
VERSION = "conversation.group_observation.v1"
MOOD_KIND = "mood"
MOMENTS_KIND = "moments"
JOKE_KIND = "joke_guard"
TOPIC_KIND = "topics"

# Material budgets, in the same spirit as the group context window: bounded
# enough that one turn cannot grow the prompt or the record without limit.
MAX_PROCESSED_KEYS = 128
MAX_MOMENTS = 8
MAX_MOMENT_CHARS = 120
MAX_MOMENT_PROMPT_ITEMS = 3
MAX_DETAIL_MOODS = 2
MAX_TOPIC_THREADS = 40
MAX_TOPIC_PROMPT_ITEMS = 5
MAX_TOPIC_EXAMPLES = 3
MAX_TOPIC_TITLE_CHARS = 40
MAX_TOPIC_EXAMPLE_CHARS = 80
TOPIC_TTL_SECONDS = 90 * 60
MOMENT_TTL_SECONDS = 7 * 24 * 3600
MOMENT_BURST_SECONDS = 30
MOMENT_BURST_SPEAKERS = 3
MOOD_PROMPT_MIN_SCORE = 6.0
# V1 added 28 sensitivity points per serious objection and blocked at 60, so a
# member needed roughly three objections. Without a per-member profile the same
# ratio is applied to the visible window's own objection count.
JOKE_OBJECTION_BLOCK = 3
JOKE_OBJECTION_CAUTION = 1

MOOD_VERSION = "group_mood.v2"
MOOD_LABELS = ("tease", "banter", "serious", "tension", "confession", "dead_silence")
MOOD_LABELS_ZH = {"tease": "调侃", "banter": "互怼", "serious": "严肃",
                  "tension": "火药味", "confession": "告白", "dead_silence": "冷场"}
MOOD_SIGNALS = (
    (r"哈哈+|呵呵+|嘿嘿+|笑死|没绷住|绷不住|好笑|好乐|乐了|2333+|www+|xswl+", "tease", 1.6),
    (r"滑稽|阴阳|玩梗|整活|搞事|皮一下|开玩笑的|我开个玩笑", "tease", 1.4),
    (r"你不行|菜鸡|就这|拉胯|太菜|你这不行", "banter", 1.8),
    (r"？？？+", "banter", 0.6),
    (r"说正事|认真讲|说真的|认真的|严肃点|不是玩笑|不开玩笑|别闹了|谈正事", "serious", 2.4),
    (r"你什么意思|你再说一遍|别太过分|太过分了|有病吧|闭嘴|别吵|吵什么|急了", "tension", 2.6),
    (r"想打架|单挑|开喷|人身攻击|侮辱", "tension", 2.8),
    (r"我喜欢你|喜欢你|表白|在一起吧|好喜欢|心动|暗恋", "confession", 2.6),
    (r"想要你|想你了|舍不得|抱抱你|贴贴", "confession", 1.4),
)
MOOD_HALF_LIVES = {"tease": 1500.0, "banter": 1800.0, "serious": 900.0,
                   "tension": 7200.0, "confession": 2400.0, "dead_silence": 3600.0}
TENSION_WEIGHTS = {"tension": 1.0, "banter": 0.45, "tease": 0.18,
                   "confession": 0.12, "serious": 0.1, "dead_silence": 0.0}
SPARK_PATTERNS = (r"修罗场", r"血流成河", r"没绷住", r"笑死", r"经典", r"名场面", r"这波",
                  r"绷不住了", r"这谁顶得住", r"救命", r"好活", r"逆天", r"公开处刑", r"社死")
OBJECTION_PATTERNS = (r"别拿我开玩笑", r"不要开我玩笑", r"别开这种玩笑", r"这不是玩笑",
                      r"我不是在开玩笑", r"一点都不好笑", r"不好笑", r"过分了",
                      r"玩笑适可而止", r"别再这样了", r"我真的生气了", r"生气了", r"别闹了")
# V1's mood-to-exaggeration base, without the private expression band: a group
# turn has no private relationship band, so V1's neutral band modifier applies.
STRENGTH_BASE = {"tease": 72.0, "banter": 66.0, "confession": 38.0,
                 "serious": 14.0, "tension": 10.0, "dead_silence": 8.0}
STRENGTH_TENSION_DAMPEN = 0.5 * 0.3
STRENGTH_BANDS = ((60.0, "playful_high", "偏玩闹"), (35.0, "playful_moderate", "略玩闹"),
                  (12.0, "reserved", "克制"), (0.0, "minimal", "最低"))


def parse_moment(value):
    """Parse one stored ISO moment as aware UTC time; anything else is absent."""
    try:
        parsed = datetime.fromisoformat(value) if type(value) is str else None
    except ValueError:
        return None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _low(value, default=0.0):
    if type(value) is bool or type(value) not in (int, float):
        return default
    return float(value)


def item_key(item):
    """A stable key so the same admitted message is folded only once."""
    return digest([str(item.get("speaker_id") or ""), str(item.get("received_at") or ""),
                   " ".join(str(item.get("text") or "").split())])


def history_items(recent):
    """Normalize the admitted group history into bounded observation items.

    Only the two receipt-backed shapes are read: ``group_member_message`` keeps
    its member attribution for counting, ``confirmed_group_reply`` is the role's
    own confirmed wording. Nothing else in the material contributes.
    """
    items = []
    for entry in recent:
        if type(entry) is not dict:
            continue
        kind = entry.get("type")
        if kind == "group_member_message":
            raw_text = str(entry.get("text") or "")
            item = {"kind": "member", "speaker_id": str(entry.get("speaker_id") or ""),
                    "received_at": entry.get("received_at"), "text": clean_group_text(raw_text)}
            item["dedupe_key"] = item_key({**item, "text": raw_text})
            items.append(item)
        elif kind == "confirmed_group_reply":
            items.append({"kind": "bot", "speaker_id": "", "received_at": entry.get("delivered_at"),
                          "text": clean_group_text(entry.get("text"))})
    return tuple(items)


class GroupPresenceStore:
    """Bounded, owner-scoped group records on the shared runtime metadata table.

    One key per group owner and record kind. A document is written by the caller
    through ``write`` inside the caller's transaction, exactly like the other
    ``runtime_metadata`` ledgers, so no new table and no migration are needed.
    """

    def __init__(self, runtime):
        self.runtime = runtime

    @staticmethod
    def key(owner, kind):
        return PREFIX + ":" + kind + ":" + digest([VERSION, owner])

    def read(self, owner, kind):
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                    (self.key(owner, kind),))
        if row is None:
            return None
        try:
            value = json.loads(row["value"])
        except ValueError:
            return None
        return value if type(value) is dict and value.get("version") == VERSION else None

    def write(self, connection, owner, kind, document):
        """Persist one bounded document; the caller owns the transaction."""
        connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (self.key(owner, kind), json.dumps({"version": VERSION, **document},
                                               ensure_ascii=False, sort_keys=True, separators=(",", ":"))))

    def clean_media_placeholders(self):
        """Repair persisted group signals without rereading private or group chat logs."""
        prefix = PREFIX + ":"
        rows = self.runtime.read_all("SELECT key, value FROM runtime_metadata WHERE substr(key, 1, ?) = ?",
                                     (len(prefix), prefix))
        updates = []
        for row in rows:
            try:
                document = json.loads(row["value"])
            except ValueError:
                continue
            if type(document) is not dict or document.get("version") != VERSION:
                continue
            kind = row["key"][len(prefix):].split(":", 1)[0]
            changed = False
            if kind == "moments" and type(document.get("moments")) is list:
                moments = []
                seen = set()
                for item in document["moments"]:
                    if type(item) is not dict:
                        changed = True
                        continue
                    text = clean_group_text(item.get("text"))
                    if not text:
                        changed = True
                        continue
                    key = digest(text)
                    cleaned = {**item, "text": text, "hash": key}
                    if key in seen:
                        changed = True
                        continue
                    seen.add(key)
                    moments.append(cleaned)
                    changed |= cleaned != item
                if len(moments) > MAX_MOMENTS:
                    moments = moments[:MAX_MOMENTS]
                    changed = True
                if changed:
                    document["moments"] = moments
            elif kind == "mood" and type(document.get("mood_scores")) is dict:
                scores = document["mood_scores"]
                if _low(scores.get("dead_silence")) > 0:
                    scores["dead_silence"] = 0.0
                    document["top_mood"] = _top_mood(scores)
                    document["social_tension"] = round(sum(
                        min(100.0, max(0.0, _low(scores.get(label)))) * TENSION_WEIGHTS.get(label, 0.0)
                        for label in MOOD_LABELS if label != "dead_silence"), 2)
                    changed = True
                if _low(document.get("message_count")) > 0:
                    document["message_count"] = 0
                    changed = True
            elif kind == "joke_guard" and type(document.get("samples")) is list:
                samples = [text for item in document["samples"] if (text := clean_group_text(item))]
                if samples != document["samples"]:
                    document["samples"] = samples
                    document["objections"] = min(int(_low(document.get("objections"))), len(samples))
                    document["level"] = ("blocked" if document["objections"] >= JOKE_OBJECTION_BLOCK else
                                          "caution" if document["objections"] >= JOKE_OBJECTION_CAUTION else "normal")
                    changed = True
            elif kind == "speech" and type(document.get("repeat")) is dict:
                repeat = document["repeat"]
                signature = repeat_signature(repeat.get("signature"))
                if signature != str(repeat.get("signature") or ""):
                    document["repeat"] = {**repeat, "signature": signature,
                        "acts": 0, "last_act_at": ""}
                    changed = True
                pending = document.get("pending")
                if type(pending) is dict and pending.get("kind") in ("repeat_follow", "repeat_interrupt"):
                    pending_signature = repeat_signature(pending.get("signature"))
                    if not pending_signature:
                        document["pending"] = None
                        changed = True
                    elif pending_signature != pending.get("signature"):
                        document["pending"] = {**pending, "signature": pending_signature}
                        changed = True
            if changed:
                updates.append((row["key"], row["value"],
                    json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
        if updates:
            with self.runtime.transaction() as connection:
                connection.executemany("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                    ((value, key, previous) for key, previous, value in updates))
        return len(updates)


# -- mood -----------------------------------------------------------------

def fold_mood(existing, items, at):
    """Fold the visible window into the stored group mood, V1's decay included."""
    prior = project_mood(existing, at)
    scores = {label: _low((prior.get("mood_scores") or {}).get(label)) for label in MOOD_LABELS}
    processed = [str(key) for key in (prior.get("processed_keys") or ())][-MAX_PROCESSED_KEYS:]
    seen, count = set(processed), 0
    burst = {label: 0.0 for label in MOOD_LABELS}
    for item in items:
        if item.get("kind") != "member":
            continue  # the group's atmosphere is read from members, never from the role's own wording
        text = clean_group_text(item.get("text"))
        if not text:
            continue
        key = str(item.get("dedupe_key") or item_key({**item, "text": text}))
        if key in seen:
            continue
        seen.add(key)
        processed.append(key)
        count += 1
        lowered = text.lower()
        for pattern, mood, weight in MOOD_SIGNALS:
            matches = len(re.findall(pattern, lowered))
            if matches:
                burst[mood] = min(100.0, burst[mood] + weight * min(3, matches))
    for label in MOOD_LABELS:
        if label == "dead_silence":
            continue
        scores[label] = round(min(100.0, scores[label] + burst[label]), 2)
    if count:
        scores["dead_silence"] = round(max(0.0, scores["dead_silence"] - 6.0), 2)
    tension = 0.0
    for label in MOOD_LABELS:
        if label != "dead_silence":
            tension += min(100.0, scores[label]) * TENSION_WEIGHTS.get(label, 0.0)
    return {"version": VERSION, "mood_scores": scores, "top_mood": _top_mood(scores),
            "social_tension": round(min(100.0, tension), 2),
            "updated_at": at.isoformat(), "message_count": int(prior.get("message_count") or 0) + count,
            "processed_keys": processed[-MAX_PROCESSED_KEYS:]}


def project_mood(existing, at):
    """Project a stored mood record to now, half-life decay included."""
    if type(existing) is not dict or existing.get("version") != VERSION:
        return {}
    updated = parse_moment(existing.get("updated_at"))
    elapsed = max(0.0, (at - updated).total_seconds()) if updated is not None else 0.0
    stored = existing.get("mood_scores") if type(existing.get("mood_scores")) is dict else {}
    scores = {label: round(_decay(max(0.0, _low(stored.get(label))), MOOD_HALF_LIVES[label], elapsed), 2)
              for label in MOOD_LABELS}
    tension = round(_decay(min(100.0, max(0.0, _low(existing.get("social_tension")))), 2400.0, elapsed), 2)
    return {"mood_scores": scores, "top_mood": _top_mood(scores), "social_tension": tension,
            "updated_at": updated, "message_count": max(0, int(_low(existing.get("message_count")))),
            "processed_keys": [str(key) for key in (existing.get("processed_keys") or ())][-MAX_PROCESSED_KEYS:]}


def mood_facts(existing, at):
    """Only what the prompt may quote: top mood, runner-ups and tension band."""
    mood = project_mood(existing, at)
    if not mood:
        return {}
    scores = mood["mood_scores"]
    top = mood["top_mood"]
    ranked = [label for label in sorted(MOOD_LABELS, key=lambda name: scores[name], reverse=True)
              if scores[label] >= MOOD_PROMPT_MIN_SCORE and label != top]
    tension = mood["social_tension"]
    return {"top_mood": top, "top_label": MOOD_LABELS_ZH[top], "top_score": round(scores[top], 2),
            "detail": tuple({"label": MOOD_LABELS_ZH[label], "score": round(scores[label], 2)}
                            for label in ranked[:MAX_DETAIL_MOODS]),
            "tension": tension, "tension_level": "high" if tension >= 55 else "low" if tension <= 12 else "normal"}


def _decay(score, half_life, elapsed):
    return 0.0 if score <= 0 or half_life <= 0 else max(0.0, score * (0.5 ** (max(0.0, elapsed) / half_life)))


def _top_mood(scores):
    ranked = sorted(MOOD_LABELS, key=lambda label: _low(scores.get(label)), reverse=True)
    return ranked[0] if _low(scores.get(ranked[0])) > 0 else "dead_silence"


def joke_guard(items, at):
    """Window-level joke boundary signals; deliberately not a member profile.

    V1 accumulated per-member sensitivity with a two-week calm decay. V2 keeps
    the same objection vocabulary but only for the currently visible window and
    without any member key, so the result can lower the joke energy of this turn
    and can never become a stored claim about a person.
    """
    objections = []
    for item in items:
        if item.get("kind") != "member":
            continue
        text = clean_group_text(item.get("text"))
        if text and any(re.search(pattern, text) for pattern in OBJECTION_PATTERNS):
            objections.append(text[:MAX_MOMENT_CHARS])
    count = len(objections)
    level = ("blocked" if count >= JOKE_OBJECTION_BLOCK else
             "caution" if count >= JOKE_OBJECTION_CAUTION else "normal")
    return {"version": VERSION, "objections": count, "level": level, "updated_at": at.isoformat(),
            "samples": objections[-JOKE_OBJECTION_BLOCK:]}


# -- moments --------------------------------------------------------------

def moment_candidates(items, at, *, window_seconds=MOMENT_BURST_SECONDS):
    """Deterministic 名场面 candidates: spark wording, or a tight multi-speaker burst.

    V1 also treated a quoted reply as a signal. The bounded V2 history carries no
    member-level reply attribution, so that signal is not invented here.
    """
    texts = [item for item in items if item.get("kind") == "member" and clean_group_text(item.get("text"))
             and (parse_moment(item.get("received_at")) or at) <= at]
    candidates = []
    for index, item in enumerate(texts):
        text = clean_group_text(item["text"])
        lowered = text.lower()
        score, reasons = 0.0, []
        if any(re.search(pattern, lowered) for pattern in SPARK_PATTERNS):
            score, reasons = score + 2.0, reasons + ["spark"]
        start = parse_moment(item.get("received_at"))
        speakers = {str(item.get("speaker_id") or "")}
        for other in texts[index + 1:]:
            when = parse_moment(other.get("received_at"))
            if start is None or when is None or (when - start).total_seconds() > window_seconds:
                break
            speakers.add(str(other.get("speaker_id") or ""))
        if len(speakers - {""}) >= MOMENT_BURST_SPEAKERS:
            score, reasons = score + 1.0, reasons + ["burst"]
        if score >= 1.0:
            candidates.append({"text": " ".join(text.split())[:MAX_MOMENT_CHARS], "score": round(score, 2),
                               "reasons": reasons, "at": item.get("received_at")})
    candidates.sort(key=lambda entry: entry["score"], reverse=True)
    return tuple(candidates[:MAX_MOMENTS])


def fold_moments(existing, candidates, at):
    """Merge candidates into the bounded moment record, deduplicated and expiring."""
    stored = {}
    if type(existing) is dict and existing.get("version") == VERSION:
        for entry in existing.get("moments") or ():
            if type(entry) is not dict or not str(entry.get("text") or ""):
                continue
            text = clean_group_text(entry.get("text"))
            if not text:
                continue
            expires = parse_moment(entry.get("expires_at"))
            if expires is not None and expires < at:
                continue
            stored[digest(text)] = {**entry, "text": text, "hash": digest(text)}
    for candidate in candidates:
        text = str(candidate.get("text") or "")
        if not text:
            continue
        key = digest(text)
        previous = stored.get(key)
        stored[key] = {"hash": key, "text": text, "at": candidate.get("at") or at.isoformat(),
                       "score": max(_low(candidate.get("score")), _low((previous or {}).get("score"))),
                       "reasons": list(candidate.get("reasons") or ()),
                       "created_at": (previous or {}).get("created_at") or at.isoformat(),
                       "expires_at": (previous or {}).get("expires_at") or (at + timedelta(seconds=MOMENT_TTL_SECONDS)).isoformat()}
    moments = sorted(stored.values(), key=lambda entry: str(entry.get("at") or ""), reverse=True)[:MAX_MOMENTS]
    return {"version": VERSION, "moments": moments, "updated_at": at.isoformat()}


def moment_facts(existing, at, *, limit=MAX_MOMENT_PROMPT_ITEMS):
    """The unexpired moments the prompt may mention, newest first, text only."""
    if type(existing) is not dict or existing.get("version") != VERSION:
        return ()
    active = []
    for entry in existing.get("moments") or ():
        if type(entry) is not dict or not str(entry.get("text") or ""):
            continue
        expires = parse_moment(entry.get("expires_at"))
        if expires is not None and expires < at:
            continue
        active.append({"text": str(entry["text"])[:MAX_MOMENT_CHARS], "at": str(entry.get("at") or "")})
    active.sort(key=lambda entry: entry["at"], reverse=True)
    return tuple(active[:limit])


def moment_portrait(items, current_speaker):
    """Current speaker's own visible cues for this turn only; never stored.

    V1 derived a provisional portrait from stored moments. V2 keeps it inside
    the turn: the cues come from this speaker's own authorized messages in the
    visible window, they are handed to the prompt as unverified reference, and
    nothing about them is written anywhere.
    """
    text = " ".join(clean_group_text(item.get("text")) for item in items
                    if item.get("kind") == "member" and str(item.get("speaker_id") or "") == str(current_speaker or ""))
    if not text:
        return {}
    if any(re.search(pattern, text) for pattern in OBJECTION_PATTERNS):
        return {"cue": "boundary", "text": text[:MAX_MOMENT_CHARS]}
    sparks = [pattern for pattern in SPARK_PATTERNS if re.search(pattern, text)]
    if sparks:
        return {"cue": "playful", "sparks": tuple(sparks[:3]), "text": text[:MAX_MOMENT_CHARS]}
    return {}


def project_strength(mood_facts_value):
    """V1's exaggeration projection: playful moods lean in, tense ones step back."""
    facts = mood_facts_value or {}
    top = str(facts.get("top_mood") or "dead_silence")
    if top not in MOOD_LABELS:
        top = "dead_silence"
    dominant = _low(facts.get("top_score"))
    weight = min(1.0, dominant / (60.0 if top == "dead_silence" else 50.0))
    exaggerate = STRENGTH_BASE.get(top, 8.0) * weight + 8.0 * (1.0 - weight)
    exaggerate = max(0.0, exaggerate - _low(facts.get("tension")) * STRENGTH_TENSION_DAMPEN)
    band = next((name for limit, name, _ in STRENGTH_BANDS if exaggerate >= limit), "minimal")
    return {"exaggerate": round(exaggerate, 2), "band": band, "top_mood": top,
            "label": next(label for _, name, label in STRENGTH_BANDS if name == band)}


def topic_signature(text):
    """Build V1-style Chinese topic anchors from one admitted, bounded line."""
    value = " ".join(strip_address_markers(clean_group_text(text)).split())[:160]
    address_prefix = re.compile(
        r"^(?:[嗯唔哦噢诶欸啊呀哎嘿嗨]+[。！？!?…~～，,\s]*)?"
        r"(?:[\w\u4e00-\u9fffぁ-んァ-ヶー]{1,10}(?:大人|老师|主人|哥哥|姐姐|同学|宝宝|宝贝)"
        r"[，,、：:\s~～…]*|(?!(?:今天|现在|刚才|刚刚|这会儿|早上|中午|晚上|外面|天气|最近|等下|待会)[，,、：:])"
        r"[\w\u4e00-\u9fffぁ-んァ-ヶー]{1,3}[，,、：:]\s*)"
    )
    value = address_prefix.sub("", value, count=1).strip() or value
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", value)
    stopwords = {"刚才", "现在", "今天", "这个", "那个", "一下", "一点", "有点", "还是",
        "没有", "已经", "时候", "用户", "对方", "主动", "消息", "这会儿", "内容", "第一",
        "时间", "看到", "喜欢", "希望", "继续", "话题", "换个", "说过", "讲过", "提过",
        "聊过", "发过", "前面", "之前", "刚刚", "群聊", "群里", "本群",
        "我今", "我今天", "天想", "今天想", "我想", "你想", "我觉", "我觉得", "觉得",
        "我感", "我感觉", "感觉", "我喜", "我喜欢", "我说", "你说", "他说", "然后", "所以",
        "就是", "真的", "其实", "可能", "应该", "可以", "怎么", "什么", "最近"}
    anchors = []

    def add(anchor):
        if (len(anchor) < 2 or anchor in stopwords
                or re.fullmatch(r"[了啦呀呢嘛吗吧啊哦噢诶嗯]+", anchor)
                or re.fullmatch(r"[年月日点分秒上下左右前后早晚中午今晚昨今明]+", anchor)):
            return
        if anchor not in anchors and len(anchors) < 64:
            anchors.append(anchor)

    for token in tokens:
        if re.fullmatch(r"[A-Za-z0-9_]{3,}", token):
            add(token.lower())
            continue
        cleaned = re.sub(r"(的时候|时候|一下|一点|了|啦|呀|呢|嘛|吗|吧|啊|哦|噢|诶|嗯)$", "", token)
        add(cleaned)
        if len(cleaned) >= 3:
            for size in (2, 3):
                for index in range(len(cleaned) - size + 1):
                    add(cleaned[index:index + size])
    return "|".join(anchors)


def topic_signatures_similar(left, right):
    """Use bounded anchor overlap so short follow-ups stay on their thread."""
    if not left or not right:
        return False
    if left == right:
        return True
    left_set, right_set = set(left.split("|")), set(right.split("|"))
    shared = left_set & right_set
    for phrase in shared:
        if (len(phrase) == 3 and re.fullmatch(r"[\u4e00-\u9fff]{3}", phrase)
                and phrase[:2] in shared and phrase[1:] in shared):
            return True
    common = len(shared)
    smaller = min(len(left_set), len(right_set))
    if smaller <= 2:
        return common >= 1
    overlap = common / smaller
    return ((common >= 2 and overlap >= 0.5) or (common >= 3 and overlap >= 0.3)
            or (common >= 4 and overlap >= 0.18) or common >= 6)


def fold_topic_threads(existing, items, at, *, max_threads=12):
    """Fold active topic lines without retaining member identities."""
    if type(max_threads) is not int or not 1 <= max_threads <= MAX_TOPIC_THREADS:
        raise ValueError("topic thread limit is invalid")
    prior = existing if type(existing) is dict and existing.get("version") == VERSION else {}
    threads = {}
    prior_threads = prior.get("threads")
    for item in prior_threads if type(prior_threads) in (list, tuple) else ():
        if type(item) is not dict:
            continue
        signature = str(item.get("signature") or "")[:4096]
        title = " ".join(clean_group_text(item.get("title")).split())[:MAX_TOPIC_TITLE_CHARS]
        started, last = parse_moment(item.get("started_at")), parse_moment(item.get("last_at"))
        if not signature or not title or started is None or last is None:
            continue
        age = (at - last).total_seconds()
        if age < 0 or age > TOPIC_TTL_SECONDS:
            continue
        thread_id = str(item.get("thread_id") or digest(signature))[:80]
        raw_examples = item.get("examples")
        examples = ([" ".join(clean_group_text(value).split())[:MAX_TOPIC_EXAMPLE_CHARS]
                     for value in raw_examples if type(value) is str and clean_group_text(value)]
                    if type(raw_examples) in (list, tuple) else [])
        raw_count = item.get("message_count")
        threads[thread_id] = {"thread_id": thread_id, "signature": signature, "title": title,
            "started_at": started.isoformat(), "last_at": last.isoformat(),
            "message_count": max(0, raw_count) if type(raw_count) is int else 0,
            "bot_joined": item.get("bot_joined") is True,
            "examples": list(dict.fromkeys(examples))[-MAX_TOPIC_EXAMPLES:]}
    raw_processed = prior.get("processed_keys")
    processed = ([str(key)[:128] for key in raw_processed][-MAX_PROCESSED_KEYS:]
                 if type(raw_processed) in (list, tuple) else [])
    seen = set(processed)
    participants = {}
    active_member = None
    active_member_at = None
    ordered = []
    for index, item in enumerate(items):
        if type(item) is dict and item.get("kind") in ("member", "bot"):
            when = parse_moment(item.get("received_at"))
            if when is not None:
                ordered.append((when, index, item))
    for when, _, item in sorted(ordered, key=lambda entry: (entry[0], entry[1])):
        if when > at or (at - when).total_seconds() > TOPIC_TTL_SECONDS:
            continue
        if item["kind"] == "bot":
            if active_member is not None and active_member_at is not None and when >= active_member_at:
                active_member["bot_joined"] = True
            continue
        text = clean_group_text(item.get("text"))
        signature = topic_signature(text)
        if not signature:
            continue
        thread = next((value for value in threads.values()
                       if topic_signatures_similar(signature, value["signature"])), None)
        if thread is None:
            thread_id = digest(signature)
            thread = {"thread_id": thread_id, "signature": signature,
                "title": " ".join(text.split())[:MAX_TOPIC_TITLE_CHARS],
                "started_at": when.isoformat(), "last_at": when.isoformat(),
                "message_count": 0, "bot_joined": False, "examples": []}
            threads[thread_id] = thread
        active_member, active_member_at = thread, when
        speaker = str(item.get("speaker_id") or "")
        if speaker:
            participants.setdefault(thread["thread_id"], set()).add(speaker)
        key = str(item.get("dedupe_key") or item_key(item))
        if key in seen:
            continue
        seen.add(key)
        processed.append(key)
        thread["message_count"] += 1
        if when > parse_moment(thread["last_at"]):
            thread["last_at"] = when.isoformat()
        example = " ".join(text.split())[:MAX_TOPIC_EXAMPLE_CHARS]
        if example and (not thread["examples"] or thread["examples"][-1] != example):
            thread["examples"].append(example)
            thread["examples"] = thread["examples"][-MAX_TOPIC_EXAMPLES:]
    active = sorted(threads.values(), key=lambda item: item["last_at"], reverse=True)[:max_threads]
    record = {"version": VERSION, "threads": active, "processed_keys": processed[-MAX_PROCESSED_KEYS:],
              "updated_at": at.isoformat()}
    facts = tuple({"title": item["title"], "message_count": item["message_count"],
                   "participant_count": len(participants.get(item["thread_id"], ())),
                   "bot_joined": item["bot_joined"], "examples": tuple(item["examples"][-2:])}
                  for item in active)
    return record, facts


def observe(store, *, owner, settings, items, at, current_speaker=""):
    """Compute this turn's bounded observation facts and the records to fold.

    Nothing is written here: the caller folds ``records`` at the moment the turn
    actually decides something, so an unfinished turn leaves no observation.
    """
    facts, records = {}, {}
    if settings["enable_group_topic_threads"]:
        record, facts["topic_threads"] = fold_topic_threads(
            store.read(owner, TOPIC_KIND), items, at, max_threads=settings["group_topic_thread_max"])
        records[TOPIC_KIND] = record
    if settings["enable_group_social_context"]:
        if settings["enable_group_mood_detection"]:
            record = fold_mood(store.read(owner, MOOD_KIND), items, at)
            records[MOOD_KIND] = record
            facts["mood"] = mood_facts(record, at)
        if settings["enable_group_roleplay_strength"]:
            facts["strength"] = project_strength(facts.get("mood"))
        if settings["enable_group_moments"]:
            record = fold_moments(store.read(owner, MOMENTS_KIND), moment_candidates(items, at), at)
            records[MOMENTS_KIND] = record
            facts["moments"] = moment_facts(record, at)
        if settings["enable_group_moment_portrait"]:
            facts["portrait"] = moment_portrait(items, current_speaker)
        if settings["enable_group_joke_guard"]:
            record = joke_guard(items, at)
            records[JOKE_KIND] = record
            facts["joke_guard"] = record
    return {"facts": facts, "records": records}
