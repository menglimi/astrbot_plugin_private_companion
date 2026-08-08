"""Pure contract for the Companion interaction-expression pipeline.

The contract deliberately owns no persistence, clock, network, or platform
access.  Runtime adapters may pass their already-sanitised snapshots here and
consume the single :class:`ExpressionDecision` result in passive replies,
proactive candidates, TTS, and presentation projections.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import re
from typing import Any, Mapping

try:
    from .interaction_dynamics import project_interaction_dynamics
except ImportError:  # pragma: no cover - direct module import in lightweight tests
    from interaction_dynamics import project_interaction_dynamics
try:
    from .affect_modulation_contract import normalize_affect_modulation
except ImportError:  # pragma: no cover
    from affect_modulation_contract import normalize_affect_modulation


EXPRESSION_CONTRACT_VERSION = "companion_interaction_expression.v2"
CONTENT_TIERS = ("normal", "flirt", "adult")
CONTENT_TIER_LABELS = {"normal": "日常", "flirt": "含蓄暧昧", "adult": "成人私密"}
_CONTENT_STAGE_INDEX = {
    "deeply_distant": 0,
    "strongly_distant": 1,
    "distant": 2,
    "acquaintance": 3,
    "familiar": 4,
    "close": 5,
    "intimate": 6,
    "deeply_bonded": 7,
    "owner_exclusive": 8,
}


class ExpressionBand(str, Enum):
    """The seven short-term expression bands, ordered from reserved to warm."""

    AVOIDANT = "avoidant"
    HURT = "hurt"
    RELAXED = "relaxed"
    LIVELY = "lively"
    WARM = "warm"
    CLOSE = "close"
    AFFECTIONATE = "affectionate"


_OWNER_ONLY_BANDS = frozenset({ExpressionBand.CLOSE, ExpressionBand.AFFECTIONATE})
_ALL_BANDS = frozenset(ExpressionBand)
_BAND_INDEX = {band: index for index, band in enumerate(ExpressionBand)}
_P4_CAP_BANDS = {
    "guarded": ExpressionBand.HURT,
    "neutral": ExpressionBand.RELAXED,
    "warm": ExpressionBand.WARM,
    "close": ExpressionBand.AFFECTIONATE,
}
_DOWN_MOOD_WORDS = frozenset({
    "sad", "bad", "negative", "angry", "anxious", "tense", "tired", "sleepy",
    "难过", "低落", "生气", "焦虑", "紧张", "疲惫", "疲劳", "困", "烦", "受伤", "安静", "收声", "困倦",
})
_UP_MOOD_WORDS = frozenset({
    "happy", "good", "positive", "joy", "calm",
    "开心", "高兴", "愉快", "轻松", "积极", "满足", "安心", "兴奋", "顺利", "温柔", "轻快", "明亮", "活跃", "松弛",
})
EXPRESSION_BAND_LABELS: dict[str, str] = {
    ExpressionBand.AVOIDANT.value: "回避",
    ExpressionBand.HURT.value: "受伤",
    ExpressionBand.RELAXED.value: "放松",
    ExpressionBand.LIVELY.value: "活泼",
    ExpressionBand.WARM.value: "温暖",
    ExpressionBand.CLOSE.value: "亲近",
    ExpressionBand.AFFECTIONATE.value: "爱意",
}
COMMON_EXPRESSION_BANDS = tuple(band.value for band in ExpressionBand if band not in _OWNER_ONLY_BANDS)
OWNER_EXPRESSION_BANDS = tuple(band.value for band in ExpressionBand)
NORMAL_INTERACTION_BAND_CAPS = ("relaxed", "lively", "warm")
DEFAULT_NORMAL_INTERACTION_BAND_CAP = "warm"


@dataclass(frozen=True)
class ExpressionDecision:
    """A complete, immutable decision consumed by all expression channels."""

    contract: str
    expression_band: str
    content_tier: str
    content_provider_policy: str
    tone: str
    warmth: int
    distance: int
    address_style: str
    response_length: str
    followup: bool
    initiative: str
    proactive_budget: int
    proactive_target: int
    proactive_cooldown_until: float
    tts_style: str
    affect_modulation: Mapping[str, Any]
    pacing: str
    directness: str
    validation_style: str
    self_disclosure: str
    humor_mode: str
    topic_initiative: str
    allowed_behaviors: tuple[str, ...]
    safety_mode: str
    blocker: str | None
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-friendly projection without mutating the DTO."""

        return asdict(self)


@dataclass(frozen=True)
class ExpressionInput:
    """Optional typed input form; mappings are also accepted by the builder."""

    relationship_score: int = 0
    relationship_role: str = "friend"
    relationship_mode: str = ""
    relationship_baseline: Mapping[str, Any] | None = None
    relationship_stage: str = ""
    normal_interaction_band_cap: str = DEFAULT_NORMAL_INTERACTION_BAND_CAP
    current_interaction: str = ""
    bot_state: Mapping[str, Any] | None = None
    schedule: Mapping[str, Any] | None = None
    message_intent: Mapping[str, Any] | None = None
    proactive_candidate: Mapping[str, Any] | None = None
    safety_constraints: Mapping[str, Any] | None = None
    content_policy: Mapping[str, Any] | None = None
    administrator_override: str | Mapping[str, Any] | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "relationship_score": self.relationship_score,
            "relationship_role": self.relationship_role,
            "relationship_mode": self.relationship_mode,
            "relationship_baseline": self.relationship_baseline,
            "relationship_stage": self.relationship_stage,
            "normal_interaction_band_cap": self.normal_interaction_band_cap,
            "current_interaction": self.current_interaction,
            "bot_state": self.bot_state,
            "schedule": self.schedule,
            "message_intent": self.message_intent,
            "proactive_candidate": self.proactive_candidate,
            "safety_constraints": self.safety_constraints,
            "content_policy": self.content_policy,
            "administrator_override": self.administrator_override,
        }


_BAND_DETAILS: dict[ExpressionBand, tuple[str, int, int, str, str, str, tuple[str, ...]]] = {
    ExpressionBand.AVOIDANT: ("reserved", 15, 90, "formal", "brief", "muted", ("acknowledge", "brief_reply", "give_space")),
    ExpressionBand.HURT: ("careful", 28, 78, "reserved", "brief", "soft", ("acknowledge", "brief_reply", "give_space")),
    ExpressionBand.RELAXED: ("steady", 48, 55, "neutral", "balanced", "natural", ("reply", "clarify")),
    ExpressionBand.LIVELY: ("bright", 63, 40, "casual", "balanced", "bright", ("reply", "light_humor", "followup")),
    ExpressionBand.WARM: ("gentle", 76, 28, "warm", "balanced", "warm", ("reply", "support", "followup")),
    ExpressionBand.CLOSE: ("intimate", 87, 18, "intimate", "expanded", "intimate", ("reply", "support", "followup", "shared_ritual")),
    ExpressionBand.AFFECTIONATE: ("affectionate", 95, 10, "exclusive", "expanded", "affectionate", ("reply", "support", "followup", "shared_ritual", "affectionate_expression")),
}

_INTERACTION_ALIASES: dict[str, ExpressionBand] = {
    "avoidant": ExpressionBand.AVOIDANT,
    "backoff": ExpressionBand.AVOIDANT,
    "refusing": ExpressionBand.AVOIDANT,
    "avoid": ExpressionBand.AVOIDANT,
    "hurt": ExpressionBand.HURT,
    "injured": ExpressionBand.HURT,
    "relaxed": ExpressionBand.RELAXED,
    "normal": ExpressionBand.RELAXED,
    "steady": ExpressionBand.RELAXED,
    "careful": ExpressionBand.RELAXED,
    "lively": ExpressionBand.LIVELY,
    "active": ExpressionBand.LIVELY,
    "warm": ExpressionBand.WARM,
    "warming": ExpressionBand.WARM,
    "close": ExpressionBand.CLOSE,
    "attached": ExpressionBand.CLOSE,
    "affectionate": ExpressionBand.AFFECTIONATE,
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _flag(value: Any) -> bool:
    return value is True or (isinstance(value, int) and not isinstance(value, bool) and value == 1)


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return max(minimum, min(maximum, value))
    if isinstance(value, float) and value.is_integer():
        return max(minimum, min(maximum, int(value)))
    return default


def _band(value: Any) -> ExpressionBand | None:
    return _INTERACTION_ALIASES.get(_text(value))


def _band_from_interaction(value: Any) -> ExpressionBand | None:
    if isinstance(value, Mapping):
        for key in ("band", "expression_band", "state", "mode"):
            resolved = _band(value.get(key))
            if resolved is not None:
                return resolved
        return None
    return _band(value)


def normalize_normal_interaction_band_cap(value: Any) -> str:
    cap = _text(value)
    return cap if cap in NORMAL_INTERACTION_BAND_CAPS else DEFAULT_NORMAL_INTERACTION_BAND_CAP


def _normal_interaction_cap_applies(*, role: str, score: int) -> bool:
    del score
    return role != "owner"


def _baseline_band(score: int, *, owner_exclusive: bool = False) -> ExpressionBand:
    if owner_exclusive:
        return ExpressionBand.CLOSE
    if score < -400:
        return ExpressionBand.AVOIDANT
    if score < 0:
        return ExpressionBand.HURT
    if score >= 600:
        return ExpressionBand.WARM
    if score >= 200:
        return ExpressionBand.LIVELY
    return ExpressionBand.RELAXED


def _bounded_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())[:limit]


def _override_band(value: Any) -> ExpressionBand | None:
    if isinstance(value, Mapping):
        if value.get("enabled") is False:
            return None
        return _band_from_interaction(value)
    return _band(value)


def _has_p4_block(safety: Mapping[str, Any]) -> bool:
    if _flag(safety.get("p4_blocked")):
        return True
    if _text(safety.get("safety_mode")) in {"blocked", "deny", "p4_blocked", "confinement"}:
        return True
    for key in ("p4", "p4_state"):
        state = _mapping(safety.get(key))
        if _flag(state.get("blocked")):
            return True
        if _text(state.get("confinement_state")) in {"blocked", "active", "confinement"}:
            return True
    return False


def _has_contact_boundary(safety: Mapping[str, Any]) -> bool:
    if _flag(safety.get("contact_boundary")) or _flag(safety.get("no_contact")):
        return True
    boundary = safety.get("contact_boundary")
    if _text(boundary) in {"avoid", "no_contact", "stop", "blocked", "quiet"}:
        return True
    details = _mapping(boundary)
    if _flag(details.get("active")) or _flag(details.get("blocked")):
        return True
    return _text(details.get("mode")) in {"avoid", "no_contact", "stop", "blocked", "quiet"}


def _intent_suppresses_followup(intent: Mapping[str, Any]) -> bool:
    if intent.get("followup_allowed") is False:
        return True
    return _text(intent.get("kind")) in {"stop", "end", "no_followup", "quiet"}


def _candidate_budget(candidate: Mapping[str, Any]) -> int:
    if not _flag(candidate.get("eligible")):
        return 0
    for key in ("dynamic_allowance", "daily_allowance", "budget", "max_budget"):
        if key in candidate:
            return _bounded_int(candidate.get(key), 0, 0, 99)
    return 0


def _schedule_is_quiet(schedule: Mapping[str, Any]) -> bool:
    if _flag(schedule.get("quiet_hours")) or _flag(schedule.get("do_not_disturb")):
        return True
    mode = _text(schedule.get("mode"))
    if mode in {"quiet", "busy", "rest"}:
        return True
    text = _mapping_text(schedule, "label", "title", "activity", "schedule")
    return any(word in text for word in ("sleep", "busy", "work", "meeting", "class", "commute", "睡", "休息", "忙", "工作", "会议", "上课", "通勤"))


def _mapping_text(value: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str):
            return candidate.strip().lower()[:240]
    return ""


def _contains_any(text: str, words: frozenset[str]) -> bool:
    return bool(text) and any(word in text for word in words)


def _input_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, ExpressionInput):
        return value.to_mapping()
    return _mapping(value)


def content_intent_from_text(value: Any) -> dict[str, Any]:
    """Return a bounded, non-persistent content request classification."""

    text = _bounded_text(value, 500).lower()
    compact = "".join(text.split())
    adult_requested = bool(
        re.search(r"(?:进入|开启|启用|切换到|继续)(?:一下|这个|当前)?(?:成人模式|成人内容|nsfw)", compact)
        or re.search(r"(?:请|帮我|给我|继续)(?:写|描写|创作|展开)[^，。！？!?]{0,16}(?:露骨|性爱|色情|性描写)", compact)
    )
    flirt_requested = adult_requested or bool(
        re.search(r"(?:请|可以|想|要|回复|语气|对我)[^，。！？!?]{0,12}(?:更暧昧|暧昧一点|调情|撩我|亲密一点|亲密点|更亲密)", compact)
        or re.search(r"(?:回复|语气|对我)[^，。！？!?]{0,8}甜一点", compact)
    )
    consent = adult_requested and bool(
        re.search(r"(?:我同意|我允许)[^\n]{0,20}(?:成人|nsfw|继续)", compact)
        or re.search(r"(?:成人|nsfw)[^，。！？!?]{0,20}(?:我同意|我允许|可以继续)", compact)
    )
    return {
        "requested_content_tier": "adult" if adult_requested else ("flirt" if flirt_requested else "normal"),
        "turn_consent": consent,
    }


def _resolve_content_tier(
    source: Mapping[str, Any],
    *,
    band: ExpressionBand,
    owner_account: bool,
    owner_exclusive: bool,
    reasons: list[str],
) -> tuple[str, str]:
    policy = _mapping(source.get("content_policy"))
    intent = _mapping(source.get("message_intent"))
    requested = _text(intent.get("requested_content_tier") or intent.get("content_tier"))
    if requested not in CONTENT_TIERS:
        requested = "normal"
    if not _flag(policy.get("enabled")):
        return "normal", "unmanaged"
    if requested == "normal":
        return "normal", "current_provider"
    private_chat = _flag(policy.get("private_chat"))
    stage = _text(source.get("relationship_stage"))
    stage_rank = _CONTENT_STAGE_INDEX.get(stage, -1)
    flirt_allowed = (
        _flag(policy.get("flirt_enabled"))
        and private_chat
        and stage_rank >= _CONTENT_STAGE_INDEX["intimate"]
        and band not in {ExpressionBand.AVOIDANT, ExpressionBand.HURT}
    )
    if requested == "adult":
        adult_checks = {
            "adult_disabled": _flag(policy.get("adult_enabled")),
            # Each boundary is an ordinary-user gate that can be relaxed
            # individually; when a switch is absent it stays checked so the
            # adult tier always fails closed by default.
            "adult_owner_required": (not _flag(policy.get("require_owner", True))) or owner_account,
            "adult_exclusive_required": (not _flag(policy.get("require_exclusive", True))) or owner_exclusive,
            "adult_affectionate_required": (not _flag(policy.get("require_affectionate", True))) or band == ExpressionBand.AFFECTIONATE,
            "adult_private_required": (not _flag(policy.get("require_private_chat", True))) or private_chat,
            "adult_age_confirmation_required": _flag(policy.get("adult_owner_confirmed")),
            "adult_turn_consent_required": (
                not _flag(policy.get("require_turn_consent")) or _flag(intent.get("turn_consent"))
            ),
            "adult_local_provider_required": (
                _flag(policy.get("local_provider_configured")) and _flag(policy.get("local_provider_match"))
            ),
        }
        failures = [code for code, passed in adult_checks.items() if not passed]
        if not failures:
            reasons.append("adult_content_tier_allowed")
            return "adult", "configured_local_only"
        reasons.extend(failures)
        if flirt_allowed:
            reasons.append("adult_downgraded_to_flirt")
            return "flirt", "current_provider"
        reasons.append("adult_downgraded_to_normal")
        return "normal", "current_provider"
    if flirt_allowed:
        reasons.append("flirt_content_tier_allowed")
        return "flirt", "current_provider"
    if not private_chat:
        reasons.append("flirt_private_required")
    if stage_rank < _CONTENT_STAGE_INDEX["intimate"]:
        reasons.append("flirt_intimate_stage_required")
    if band in {ExpressionBand.AVOIDANT, ExpressionBand.HURT}:
        reasons.append("flirt_interaction_boundary")
    if not _flag(policy.get("flirt_enabled")):
        reasons.append("flirt_disabled")
    return "normal", "current_provider"


def build_expression_decision(input_data: ExpressionInput | Mapping[str, Any] | None = None, **overrides: Any) -> ExpressionDecision:
    """Build one deterministic expression decision from bounded structured inputs.

    Unknown values are ignored rather than echoed. An administrator override
    may choose a permitted band, but never bypasses P4 or contact boundaries.
    """

    source = dict(_input_mapping(input_data))
    source.update(overrides)
    reasons: list[str] = []
    safety = _mapping(source.get("safety_constraints"))
    role = _text(source.get("relationship_role"))
    mode = _text(source.get("relationship_mode"))
    owner_account = role == "owner"
    owner_exclusive = owner_account and mode == "owner_exclusive"

    if _has_p4_block(safety):
        return _blocked_decision("p4_blocked", "p4_safety", reasons)
    contact_boundary = _has_contact_boundary(safety)
    passive_reengagement = _flag(safety.get("passive_reengagement"))
    if contact_boundary and not passive_reengagement:
        return _blocked_decision("contact_boundary", "contact_boundary", reasons)

    score = _bounded_int(source.get("relationship_score"), 0, -1200, 1200)
    normal_cap = normalize_normal_interaction_band_cap(
        source.get("normal_interaction_band_cap")
        or _mapping(source.get("current_interaction")).get("normal_interaction_band_cap")
    )
    relationship_baseline = _mapping(source.get("relationship_baseline"))
    band = _baseline_band(score, owner_exclusive=owner_exclusive)
    interaction_band = _band_from_interaction(source.get("current_interaction"))
    if interaction_band is not None:
        if interaction_band == ExpressionBand.RELAXED:
            if band != ExpressionBand.RELAXED:
                reasons.append("relationship_baseline_retained")
        else:
            band = interaction_band
            reasons.append("interaction_band_applied")

    override_band = _override_band(source.get("administrator_override"))
    if override_band is not None:
        band = override_band
        reasons.append("administrator_override_applied")

    if band in _OWNER_ONLY_BANDS and not owner_account:
        band = ExpressionBand.WARM
        reasons.append("owner_role_required")
    normal_cap_band = _band(normal_cap) or ExpressionBand.WARM
    if _normal_interaction_cap_applies(role=role, score=score) and _BAND_INDEX[band] > _BAND_INDEX[normal_cap_band]:
        band = normal_cap_band
        reasons.append("normal_interaction_band_cap_applied")
    if contact_boundary:
        band = ExpressionBand.AVOIDANT
        reasons.append("contact_boundary_passive_reengagement")
    p4_cap = _P4_CAP_BANDS.get(_text(safety.get("p4_warmth_cap")))
    if p4_cap is not None and _BAND_INDEX[band] > _BAND_INDEX[p4_cap]:
        band = p4_cap
        reasons.append("p4_warmth_cap_applied")
    tone, warmth, distance, address_style, response_length, tts_style, behaviors = _BAND_DETAILS[band]
    baseline_tone = _bounded_text(relationship_baseline.get("tone"), 120)
    baseline_address = _bounded_text(
        relationship_baseline.get("address_style") or relationship_baseline.get("address_level"),
        100,
    )
    if band not in {ExpressionBand.AVOIDANT, ExpressionBand.HURT}:
        if baseline_tone:
            tone = baseline_tone
            reasons.append("relationship_tone_applied")
        if baseline_address:
            address_style = baseline_address
            reasons.append("relationship_address_applied")
    soft_behaviors = _mapping(relationship_baseline.get("soft_behaviors"))
    if soft_behaviors:
        if soft_behaviors.get("allow_followup") is False:
            behaviors = tuple(item for item in behaviors if item != "followup")
            reasons.append("relationship_followup_cap")
        if soft_behaviors.get("allow_playful_jokes") is False:
            behaviors = tuple(item for item in behaviors if item != "light_humor")
        if soft_behaviors.get("allow_memory_mention") is False:
            behaviors = tuple(item for item in behaviors if item != "shared_ritual")
        if soft_behaviors.get("allow_daily_care") is False:
            behaviors = tuple(item for item in behaviors if item != "support")
    intent = _mapping(source.get("message_intent"))
    followup = "followup" in behaviors and not _intent_suppresses_followup(intent)
    if not followup and "followup" in behaviors:
        reasons.append("intent_followup_suppressed")

    bot_state = _mapping(source.get("bot_state"))
    energy = _bounded_int(bot_state.get("energy"), 70, 0, 100)
    mood = _mapping_text(bot_state, "mood", "mood_bias", "label", "state")
    low_energy = energy < 30
    down_mood = _contains_any(mood, _DOWN_MOOD_WORDS)
    up_mood = _contains_any(mood, _UP_MOOD_WORDS)
    if low_energy:
        response_length = "brief" if response_length != "brief" else response_length
        followup = False
        warmth = max(0, warmth - 10)
        distance = min(100, distance + 8)
        tts_style = "soft"
        reasons.append("low_energy_expression_cap")
    if down_mood:
        followup = False
        warmth = max(0, warmth - 8)
        distance = min(100, distance + 6)
        if tone not in {"reserved", "careful"}:
            tone = "gentle"
        tts_style = "soft"
        reasons.append("down_mood_expression_cap")
    elif up_mood and not low_energy:
        warmth = min(100, warmth + 5)
        reasons.append("up_mood_expression_lift")

    modulation = normalize_affect_modulation(bot_state.get("affect_modulation"))
    if modulation["confidence"] > 0:
        if modulation["valence"] <= -0.35:
            warmth = max(0, warmth - 3)
            if tone not in {"reserved", "careful"}:
                tone = "gentle"
        elif modulation["valence"] >= 0.35 and band not in {ExpressionBand.AVOIDANT, ExpressionBand.HURT}:
            warmth = min(100, warmth + 3)
        if modulation["arousal"] <= 0.2:
            tts_style = "soft"
        elif modulation["arousal"] >= 0.7 and not low_energy and not down_mood and band not in {ExpressionBand.AVOIDANT, ExpressionBand.HURT}:
            tts_style = "bright"
        if modulation["vulnerability"] >= 0.65:
            followup = False
            if tone != "reserved":
                tone = "careful"
        reasons.append("affect_modulation_applied")

    schedule = _mapping(source.get("schedule"))
    candidate = _mapping(source.get("proactive_candidate"))
    budget = _candidate_budget(candidate)
    proactive_cooldown_until = _bounded_timestamp(candidate.get("cooldown_until"))
    candidate_now = _bounded_timestamp(candidate.get("current_ts"))
    readiness_supplied = "readiness_score" in candidate
    readiness_score = _bounded_int(candidate.get("readiness_score"), 100, 0, 100)
    stage_target = 0
    if "proactive_care_limit" in relationship_baseline:
        configured_target = _bounded_int(relationship_baseline.get("proactive_care_limit"), 0, 0, 30)
        stage_key = _text(relationship_baseline.get("stage_key"))
        relationship_is_distant = score < 0 or stage_key in {
            "deeply_distant",
            "strongly_distant",
            "distant",
        }
        if relationship_is_distant:
            budget = 0
            reasons.append("relationship_distant_proactive_blocked")
        else:
            stage_target = max(1, configured_target)
            reasons.append("relationship_proactive_soft_target")
    initiative = "allowed" if budget else "passive_only"
    if band in {ExpressionBand.AVOIDANT, ExpressionBand.HURT}:
        budget = 0
        initiative = "passive_only"
        reasons.append("interaction_proactive_suppressed")
    if readiness_supplied and readiness_score < 25:
        budget = 0
        initiative = "passive_only"
        reasons.append("proactive_readiness_suppressed")
    elif readiness_supplied and readiness_score < 45 and budget > 1:
        budget = 1
        reasons.append("proactive_readiness_capped")
    if proactive_cooldown_until > 0 and (candidate_now <= 0 or proactive_cooldown_until > candidate_now):
        budget = 0
        initiative = "passive_only"
        reasons.append("proactive_cooldown_active")
    if _schedule_is_quiet(schedule):
        budget = 0
        initiative = "passive_only"
        response_length = "brief" if response_length != "brief" else response_length
        followup = False
        warmth = max(0, warmth - 6)
        distance = min(100, distance + 4)
        reasons.append("schedule_proactive_suppressed")

    if not followup and "followup" in behaviors:
        behaviors = tuple(item for item in behaviors if item != "followup")

    content_tier, content_provider_policy = _resolve_content_tier(
        source,
        band=band,
        owner_account=owner_account,
        owner_exclusive=owner_exclusive,
        reasons=reasons,
    )
    pacing = (
        "slow"
        if low_energy or down_mood or modulation["arousal"] <= 0.2
        else "bright"
        if modulation["arousal"] >= 0.7 and band == ExpressionBand.LIVELY
        else "steady"
    )
    directness = (
        "indirect"
        if band in {ExpressionBand.AVOIDANT, ExpressionBand.HURT}
        else "direct"
        if band in {ExpressionBand.LIVELY, ExpressionBand.AFFECTIONATE}
        else "natural"
    )
    validation_style = (
        "acknowledge"
        if band in {ExpressionBand.AVOIDANT, ExpressionBand.HURT, ExpressionBand.RELAXED}
        else "support_first"
    )
    if modulation["vulnerability"] >= 0.65:
        validation_style = "support_first"
    self_disclosure = (
        "allowed"
        if owner_account and band in {ExpressionBand.CLOSE, ExpressionBand.AFFECTIONATE}
        else "light"
        if "shared_ritual" in behaviors
        else "none"
    )
    humor_mode = "off"
    if (
        "light_humor" in behaviors
        and not low_energy
        and not down_mood
        and band not in {ExpressionBand.AVOIDANT, ExpressionBand.HURT}
    ):
        humor_mode = "playful" if band == ExpressionBand.LIVELY else "light"
    topic_initiative = (
        "reply_only"
        if not followup
        else "shared_topic"
        if "shared_ritual" in behaviors
        else "followup"
    )
    return ExpressionDecision(
        contract=EXPRESSION_CONTRACT_VERSION,
        expression_band=band.value,
        content_tier=content_tier,
        content_provider_policy=content_provider_policy,
        tone=tone,
        warmth=warmth,
        distance=distance,
        address_style=address_style,
        response_length=response_length,
        followup=followup,
        initiative=initiative,
        proactive_budget=budget,
        proactive_target=stage_target,
        proactive_cooldown_until=proactive_cooldown_until,
        tts_style=tts_style,
        affect_modulation=modulation,
        pacing=pacing,
        directness=directness,
        validation_style=validation_style,
        self_disclosure=self_disclosure,
        humor_mode=humor_mode,
        topic_initiative=topic_initiative,
        allowed_behaviors=behaviors,
        safety_mode="contact_boundary_passive" if contact_boundary else "normal",
        blocker=None,
        reason_codes=tuple(reasons),
    )


def allowed_expression_bands(relationship_role: Any, relationship_mode: Any) -> tuple[str, ...]:
    role = _text(relationship_role)
    del relationship_mode
    return OWNER_EXPRESSION_BANDS if role == "owner" else COMMON_EXPRESSION_BANDS


def current_interaction_projection(
    value: Any,
    *,
    relationship_role: Any = "friend",
    relationship_mode: Any = "normal",
    normal_interaction_band_cap: Any = None,
    relationship_score: Any = 0,
    now: Any = None,
) -> dict[str, Any]:
    """Normalize persisted and legacy interaction state for runtime/page use."""
    raw = dict(value) if isinstance(value, Mapping) else {}
    current_ts = _bounded_timestamp(now)
    hard_expires_at = _bounded_timestamp(raw.get("hard_expires_at") or raw.get("expires_at"))
    dynamics = project_interaction_dynamics(raw, now=current_ts) if current_ts else {}
    if dynamics:
        if hard_expires_at:
            dynamics["expires_at"] = min(
                _bounded_timestamp(dynamics.get("expires_at")) or hard_expires_at,
                hard_expires_at,
            )
        raw.update(dynamics)
    band = _band_from_interaction(raw) or ExpressionBand.RELAXED
    role = _text(relationship_role)
    score = _bounded_int(raw.get("relationship_score", relationship_score), 0, -1200, 1200)
    normal_cap = normalize_normal_interaction_band_cap(
        normal_interaction_band_cap if normal_interaction_band_cap is not None else raw.get("normal_interaction_band_cap")
    )
    allowed = allowed_expression_bands(relationship_role, relationship_mode)
    reason_codes: list[str] = []
    if band.value not in allowed:
        band = ExpressionBand.WARM
        reason_codes.append("owner_role_required")
    normal_cap_band = _band(normal_cap) or ExpressionBand.WARM
    if _normal_interaction_cap_applies(role=role, score=score) and _BAND_INDEX[band] > _BAND_INDEX[normal_cap_band]:
        band = normal_cap_band
        reason_codes.append("normal_interaction_band_cap_applied")
    expires_at = _bounded_timestamp(raw.get("expires_at"))
    manual = bool(raw.get("manual_override") or _text(raw.get("source")) == "manual")
    dynamics_hard_expired = bool(dynamics and hard_expires_at and current_ts and current_ts >= hard_expires_at)
    if (not dynamics or dynamics_hard_expired) and expires_at and current_ts and current_ts >= expires_at:
        band = ExpressionBand.RELAXED
        manual = False
        reason_codes.append("interaction_expired")
    source = "manual" if manual else _text(raw.get("source")) or "automatic"
    reason = str(raw.get("reason") or raw.get("reason_code") or "").replace("\r", " ").replace("\n", " ")[:120]
    projection = {
        "expression_band": band.value,
        "label": EXPRESSION_BAND_LABELS[band.value],
        "source": source,
        "operator": _bounded_text(raw.get("operator"), 40),
        "reason": reason,
        "updated_at": _bounded_timestamp(raw.get("updated_at")),
        "expires_at": expires_at,
        "manual_override": manual,
        "normal_interaction_band_cap": normal_cap,
        "allowed_bands": list(allowed),
        "reason_codes": reason_codes,
        "last_event_id": _bounded_text(raw.get("last_event_id"), 96),
        "trace_id": _bounded_text(raw.get("trace_id"), 96),
    }
    if dynamics:
        projection.update({
            "dynamics_version": dynamics["dynamics_version"],
            "load": dynamics["load"],
            "peak_intensity": dynamics["peak_intensity"],
            "decay_started_at": dynamics["decay_started_at"],
            "half_life": dynamics["half_life"],
            "recovery_band": dynamics["recovery_band"],
            "projection_revision": dynamics["projection_revision"],
            "polarity": dynamics["polarity"],
            "base_band": dynamics["base_band"],
        })
    return projection


def expression_decision_prompt(value: ExpressionDecision | Mapping[str, Any]) -> str:
    decision = value.to_dict() if isinstance(value, ExpressionDecision) else dict(_mapping(value))
    band = _band(decision.get("expression_band")) or ExpressionBand.RELAXED
    if decision.get("blocker"):
        return "当前表达受边界或安全规则限制：保持简短、低压，不主动扩展或追问。"
    followup = "可以自然追问" if bool(decision.get("followup")) else "不要追问"
    initiative = "允许在合适窗口主动联系" if decision.get("initiative") == "allowed" else "本轮只被动回应"
    proactive_target = _bounded_int(decision.get("proactive_target"), 0, 0, 30)
    reason_codes = decision.get("reason_codes") if isinstance(decision.get("reason_codes"), (list, tuple)) else ()
    proactive_rhythm = (
        f"本阶段每天约 {proactive_target} 次主动联系只是柔性节奏目标，不是必须凑满或一到即停的硬配额；"
        "结合真实由头、对方反馈和打扰感自然调整"
        if proactive_target > 0
        else "当前关系处于疏离阶段，不主动联系"
        if "relationship_distant_proactive_blocked" in reason_codes
        else "当前没有阶段主动节奏目标，按本轮实际边界决定"
    )
    content_tier = _text(decision.get("content_tier"))
    provider_policy = _text(decision.get("content_provider_policy"))
    if provider_policy == "unmanaged":
        content_instruction = ""
    elif content_tier == "adult":
        content_instruction = "内容尺度=成人私密；只承接本轮明确同意的成年人私聊，不扩大同意，不转入群聊或主动消息；插件二次复核固定使用后台指定 Provider"
    elif content_tier == "flirt":
        content_instruction = "内容尺度=含蓄暧昧；可以亲密和调情，但保持非露骨，不描写成人性行为"
    else:
        content_instruction = "内容尺度=日常；不要主动升级为暧昧或成人内容"
    dimensions = (
        f"节奏={str(decision.get('pacing') or 'steady')[:12]}，"
        f"直接度={str(decision.get('directness') or 'natural')[:12]}，"
        f"回应={str(decision.get('validation_style') or 'none')[:16]}，"
        f"自述={str(decision.get('self_disclosure') or 'none')[:16]}，"
        f"幽默={str(decision.get('humor_mode') or 'off')[:12]}，"
        f"话题={str(decision.get('topic_initiative') or 'reply_only')[:16]}"
    )
    return (
        f"当前互动表达：{EXPRESSION_BAND_LABELS[band.value]}；"
        f"语气={str(decision.get('tone') or 'steady')[:24]}，"
        f"称呼距离={str(decision.get('address_style') or 'neutral')[:24]}，"
        f"回复长度={str(decision.get('response_length') or 'balanced')[:24]}；"
        f"{followup}；{initiative}；{proactive_rhythm}；{dimensions}{f'；{content_instruction}' if content_instruction else ''}。"
    )


def _bounded_timestamp(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    numeric = float(value)
    return numeric if 0 < numeric < 10**12 else 0.0


def _blocked_decision(reason: str, blocker: str, inherited_reasons: list[str]) -> ExpressionDecision:
    return ExpressionDecision(
        contract=EXPRESSION_CONTRACT_VERSION,
        expression_band=ExpressionBand.AVOIDANT.value,
        content_tier="normal",
        content_provider_policy="current_provider",
        tone="blocked",
        warmth=0,
        distance=100,
        address_style="none",
        response_length="none",
        followup=False,
        initiative="blocked",
        proactive_budget=0,
        proactive_target=0,
        proactive_cooldown_until=0.0,
        tts_style="none",
        affect_modulation=normalize_affect_modulation({}),
        pacing="slow",
        directness="indirect",
        validation_style="none",
        self_disclosure="none",
        humor_mode="off",
        topic_initiative="reply_only",
        allowed_behaviors=(),
        safety_mode=reason,
        blocker=blocker,
        reason_codes=tuple(inherited_reasons + [reason]),
    )


# A descriptive alias keeps runtime call sites readable while preserving one contract.
resolve_expression_decision = build_expression_decision


__all__ = [
    "COMMON_EXPRESSION_BANDS",
    "CONTENT_TIERS",
    "CONTENT_TIER_LABELS",
    "EXPRESSION_CONTRACT_VERSION",
    "EXPRESSION_BAND_LABELS",
    "ExpressionBand",
    "ExpressionDecision",
    "ExpressionInput",
    "DEFAULT_NORMAL_INTERACTION_BAND_CAP",
    "NORMAL_INTERACTION_BAND_CAPS",
    "OWNER_EXPRESSION_BANDS",
    "allowed_expression_bands",
    "build_expression_decision",
    "current_interaction_projection",
    "content_intent_from_text",
    "normalize_normal_interaction_band_cap",
    "expression_decision_prompt",
    "resolve_expression_decision",
]
