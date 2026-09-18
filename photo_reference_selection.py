"""Deterministic reference selection and side-effect-free selection trials."""
from __future__ import annotations

import inspect
import json
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Mapping


@dataclass(frozen=True)
class CandidateMatch:
    candidate_id: str
    score: float
    rank: int
    matched: tuple[str, ...]
    excluded: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class SelectionResult:
    selected: Mapping[str, Any] | None
    candidates: tuple[CandidateMatch, ...]
    selection_source: str
    selection_reason: str
    fallback_id: str = ""
    model_attempted: bool = False
    model_selected_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": dict(self.selected) if self.selected else None,
            "candidates": [asdict(item) for item in self.candidates],
            "selection_source": self.selection_source,
            "selection_reason": self.selection_reason,
            "fallback_id": self.fallback_id,
            "model_attempted": self.model_attempted,
            "model_selected_id": self.model_selected_id,
        }


@dataclass(frozen=True)
class TrialReport:
    request_text: str
    tool_called: bool
    tool_name: str
    tool_arguments: Mapping[str, Any]
    tool_status: str
    selection: SelectionResult | None
    error_stage: str = ""
    stability: Mapping[str, Any] | None = None
    error: str = ""
    normalized_request: Mapping[str, Any] = field(default_factory=dict)
    model_selection: Mapping[str, Any] = field(default_factory=dict)
    rule_fallback: Mapping[str, Any] = field(default_factory=dict)
    expected_reference_id: str = ""
    expected_match: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["selection"] = self.selection.to_dict() if self.selection else None
        return payload


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _finite_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


_SCENE_CATEGORY_TOKENS = {
    "home": ("在家", "家里", "居家", "宿舍", "卧室", "home"),
    "bedroom": ("卧室", "床边", "睡前", "刚起床", "bedroom"),
    "school": ("上学", "学校", "校园", "教室", "校门", "school"),
    "outdoor": ("外出", "出门", "室外", "户外", "通勤", "逛街", "街头", "旅行", "outdoor"),
    "office": ("办公室", "办公", "公司", "工作场所", "office"),
    "formal_event": ("正式场合", "宴会", "婚礼", "舞会", "典礼"),
    "sport": ("运动", "健身", "跑步", "瑜伽", "球场", "体育馆"),
    "beach": ("海边", "海滩", "沙滩", "泳池"),
}
_TIME_CATEGORY_TOKENS = {
    "morning": ("清晨", "早晨", "早上", "晨间"),
    "daytime": ("白天", "日间", "daytime"),
    "afternoon": ("下午", "午后"),
    "evening": ("傍晚", "黄昏", "日落"),
    "night": ("夜晚", "晚上", "深夜", "夜景", "night"),
    "bedtime": ("睡前", "临睡", "bedtime"),
}
_CATEGORY_CONTEXT_RESET = re.compile(r"[，,。.!！；;、]|而是|改(?:成|为|到)|换(?:成|到)", re.I)
_CATEGORY_NEGATION = re.compile(
    r"(?:不要|别(?:再)?|不(?:想|要|愿)?(?:在|去|到)?|不是|避免|禁止|排除|取消|离开|"
    r"without|not(?:\s+at)?)\s*[^，,。.!！；;、]{0,8}$",
    re.I,
)


def _category_mentions(text: str, vocabulary: Mapping[str, tuple[str, ...]]) -> tuple[set[str], set[str]]:
    normalized = _text(text)
    latest: dict[str, tuple[int, bool]] = {}
    for category, tokens in vocabulary.items():
        for token in tokens:
            start = normalized.find(token)
            while start >= 0:
                prefix = normalized[max(0, start - 24) : start]
                clause_prefix = _CATEGORY_CONTEXT_RESET.split(prefix)[-1]
                state = (start, bool(_CATEGORY_NEGATION.search(clause_prefix)))
                if start >= latest.get(category, (-1, False))[0]:
                    latest[category] = state
                start = normalized.find(token, start + len(token))
    included = {category for category, (_index, negated) in latest.items() if not negated}
    excluded = {category for category, (_index, negated) in latest.items() if negated}
    return included, excluded


_OUTFIT_CLAUSE_FIELD = (
    r"(?:上装|外搭|下装|配饰|配色|轮廓|鞋履|鞋子|"
    r"top|outer|bottom|accessory|palette|silhouette|footwear)"
)
# 「当天基础穿搭：上装:…；下装:…」说的是**穿什么**，不是**在哪**。作者原本的候选表是
# 纯英文（"crisp white shirt with a navy knit vest"），永远撞不上场景词表；衣柜接管后
# 衣物名是中文，「黑色运动紧身裤」「居家拖鞋」会把场景误判成 sport/home，把「今日穿搭
# 参考图」挤掉换成无关的场景图库图。所以解析场景前先把这段摘掉。
_DAILY_OUTFIT_CLAUSE = re.compile(
    r"(?:今日穿搭|当天基础穿搭|当天穿搭|日常穿搭|today'?s outfit|daily outfit)\s*[：:]\s*"
    + _OUTFIT_CLAUSE_FIELD
    + r"\s*[:：][^；;]*"
    + r"(?:\s*[；;]\s*" + _OUTFIT_CLAUSE_FIELD + r"\s*[:：][^；;]*)*",
    re.I,
)


def strip_daily_outfit_clause(text: Any) -> str:
    """Drop the 「当天基础穿搭：…」段 before mining scene categories."""

    return _DAILY_OUTFIT_CLAUSE.sub(" ", str(text or ""))


def parse_photo_reference_context_categories(text: str) -> tuple[set[str], set[str], set[str], set[str]]:
    """Return requested scenes/times and explicitly negated scenes/times."""
    # 场景类别只认地点/日程/时间：衣物名先摘掉再扫词表。
    scene_text = strip_daily_outfit_clause(text)
    scenes, excluded_scenes = _category_mentions(scene_text, _SCENE_CATEGORY_TOKENS)
    times, excluded_times = _category_mentions(scene_text, _TIME_CATEGORY_TOKENS)
    return scenes, times, excluded_scenes, excluded_times


def _outfit(text: str) -> str:
    normalized = _text(text)
    for name, tokens in {
        "sleepwear": ("睡衣", "睡裙", "pajama", "sleepwear"),
        "school_uniform": ("校服", "school uniform"),
        "formalwear": ("正装", "礼服", "formalwear"),
        "sportswear": ("运动服", "健身服", "sportswear"),
    }.items():
        if any(token in normalized for token in tokens):
            return name
    return ""


def select_photo_reference(
    request: Mapping[str, Any] | str,
    candidates: Iterable[Mapping[str, Any]],
) -> SelectionResult:
    """Rank candidates without model calls or mutations."""
    if isinstance(request, Mapping):
        request_text = str(request.get("request_text") or request.get("text") or "")
        text_scenes, text_times, text_excluded_scenes, text_excluded_times = (
            parse_photo_reference_context_categories(request_text)
        )
        requested_outfit = str(request.get("outfit_category") or "") or _outfit(request_text)
        requested_scenes = set(request.get("scene_categories") or ()) | text_scenes
        requested_times = set(request.get("time_categories") or ()) | text_times
        excluded_scenes = set(request.get("excluded_scene_categories") or ()) | text_excluded_scenes
        excluded_times = set(request.get("excluded_time_categories") or ()) | text_excluded_times
        excluded_outfit = set(request.get("excluded_outfit_categories") or ())
    else:
        request_text = str(request or "")
        requested_outfit = _outfit(request_text)
        requested_scenes, requested_times, excluded_scenes, excluded_times = (
            parse_photo_reference_context_categories(request_text)
        )
        excluded_outfit = set()
    ranked: list[tuple[Mapping[str, Any], float, set[str], set[str], str]] = []
    seen_candidate_ids: set[str] = set()
    for index, candidate in enumerate(candidates):
        candidate = dict(candidate)
        base_id = str(candidate.get("id") or f"candidate-{index + 1}").strip() or f"candidate-{index + 1}"
        cid = base_id
        suffix = 2
        while cid in seen_candidate_ids:
            cid = f"{base_id}#{suffix}"
            suffix += 1
        candidate["id"] = cid
        seen_candidate_ids.add(cid)
        roles = set(str(v) for v in (candidate.get("reference_roles") or ()))
        scenes = set(str(v) for v in (candidate.get("scene_categories") or ()))
        times = set(str(v) for v in (candidate.get("time_categories") or ()))
        candidate_excluded_scenes = set(str(v) for v in (candidate.get("excluded_scene_categories") or ()))
        candidate_excluded_times = set(str(v) for v in (candidate.get("excluded_time_categories") or ()))
        category = _text(candidate.get("outfit_category")) if "outfit" in roles else ""
        matched = set()
        excluded = set()
        score = _finite_number(candidate.get("priority")) / 100.0
        if requested_outfit and category == requested_outfit:
            score += 20
            matched.add("outfit")
        elif requested_outfit and category and category != requested_outfit:
            score -= 8
        if category and category in excluded_outfit:
            score -= 100
            excluded.add("outfit")
        if scenes & requested_scenes:
            score += 10
            matched.add("scene")
        if times & requested_times:
            score += 8
            matched.add("time")
        if scenes & excluded_scenes:
            score -= 100
            excluded.add("scene")
        if times & excluded_times:
            score -= 100
            excluded.add("time")
        if candidate_excluded_scenes & requested_scenes:
            score -= 100
            excluded.add("scene")
        if candidate_excluded_times & requested_times:
            score -= 100
            excluded.add("time")
        eligibility = _text(candidate.get("selection_eligibility") or "matching_only")
        metadata_source = _text(candidate.get("metadata_source"))
        policy_active = bool(
            candidate.get("editor_intent")
            or metadata_source in {"guided_editor", "guided_editor_draft", "manual_override"}
            or eligibility != "matching_only"
            or candidate_excluded_scenes
            or candidate_excluded_times
            or (not metadata_source and "selection_eligibility" in candidate)
        )
        if policy_active and eligibility == "disabled":
            excluded.add("disabled")
            score = -1000
        elif policy_active and eligibility == "matching_only" and (scenes or times or category) and not matched:
            excluded.add("matching_only")
            score = -1000
        elif policy_active and eligibility == "fallback_identity_only" and not matched and (roles - {"identity"}):
            excluded.add("identity_only_fallback")
            score = -1000
        if not excluded and not matched:
            score += 1 if "identity" in roles else 0
        reason = "匹配用户原话" if matched else "身份兜底"
        ranked.append((candidate, score, matched, excluded, reason))
    ranked.sort(key=lambda row: (-row[1], str(row[0].get("id") or "")))
    matches = tuple(
        CandidateMatch(str(item.get("id") or ""), score, index, tuple(sorted(matched)), tuple(sorted(excluded)), reason)
        for index, (item, score, matched, excluded, reason) in enumerate(ranked, start=1)
    )
    usable = next((row for row in ranked if not row[3] and row[1] > -999), None)
    selected = usable[0] if usable else None
    return SelectionResult(
        selected=selected,
        candidates=matches,
        selection_source="rule_fallback" if selected else "none",
        selection_reason="best_match" if selected else "no_usable_reference",
        fallback_id=str(selected.get("id") or "") if selected else "",
    )


def normalize_photo_selection_request(
    request_text: str,
    tool_arguments: Mapping[str, Any],
    request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize captured pc_generate_photo arguments for the formal selector."""
    raw_kind = _text(tool_arguments.get("kind") or "text2img")
    kind = {
        "portrait": "selfie",
        "自拍": "selfie",
        "人像": "selfie",
        "emoji": "sticker",
        "meme": "sticker",
        "贴纸": "sticker",
        "表情包": "sticker",
        "改图": "edit",
        "修图": "edit",
        "重绘": "edit",
    }.get(raw_kind, raw_kind)
    if kind not in {"text2img", "selfie", "sticker", "edit"}:
        kind = "text2img"
    prompt = str(tool_arguments.get("prompt") or request_text or "").strip()
    scene_preset = str(tool_arguments.get("scene_preset") or "").strip()
    selection_text = "\n".join(item for item in (prompt, scene_preset) if item)
    source = request or {}
    scenes, times, excluded_scenes, excluded_times = parse_photo_reference_context_categories(selection_text)
    source_scenes = set(source.get("scene_categories") or ())
    source_times = set(source.get("time_categories") or ())
    source_excluded_scenes = set(source.get("excluded_scene_categories") or ())
    source_excluded_times = set(source.get("excluded_time_categories") or ())
    ambient_context = str(source.get("ambient_context") or source.get("context_snapshot") or "").strip()
    ambient_scenes, ambient_times, ambient_excluded_scenes, ambient_excluded_times = (
        parse_photo_reference_context_categories(ambient_context)
    )
    if not scenes and not excluded_scenes:
        scenes = source_scenes or ambient_scenes
        excluded_scenes = source_excluded_scenes | ambient_excluded_scenes
    else:
        scenes |= source_scenes
        excluded_scenes |= source_excluded_scenes
    if not times and not excluded_times:
        times = source_times or ambient_times
        excluded_times = source_excluded_times | ambient_excluded_times
    else:
        times |= source_times
        excluded_times |= source_excluded_times
    outfit_category = (
        str(source.get("outfit_category") or "").strip()
        or _outfit(selection_text)
        or _outfit(ambient_context)
    )
    return {
        "original_request_text": str(request_text or "").strip(),
        "request_text": selection_text or str(request_text or "").strip(),
        "kind": kind,
        "prompt": prompt,
        "scene_preset": scene_preset,
        "explicit_reference_image_path": str(tool_arguments.get("reference_image_path") or "").strip()[:1000],
        "user_id": str(source.get("user_id") or "").strip(),
        "ambient_context": str(source.get("_trial_context_snapshot") or source.get("ambient_context") or "").strip(),
        "outfit_category": outfit_category,
        "scene_categories": sorted(str(item) for item in scenes if str(item).strip()),
        "time_categories": sorted(str(item) for item in times if str(item).strip()),
        "excluded_scene_categories": sorted(str(item) for item in excluded_scenes if str(item).strip()),
        "excluded_time_categories": sorted(str(item) for item in excluded_times if str(item).strip()),
    }


async def run_photo_selection_trial(
    request: Mapping[str, Any],
    *,
    candidates: Iterable[Mapping[str, Any]],
    tool_runner: Callable[[str, Mapping[str, Any]], Any] | None = None,
    selection_runner: Callable[[Mapping[str, Any], tuple[Mapping[str, Any], ...], SelectionResult], Any] | None = None,
    runs: int = 1,
) -> TrialReport:
    """Capture a model tool decision without executing the production tool."""
    request_text = str(request.get("request_text") or request.get("text") or "").strip()
    if not request_text:
        return TrialReport(
            request_text="",
            tool_called=False,
            tool_name="",
            tool_arguments={},
            tool_status="invalid_request",
            selection=None,
            error_stage="tool_decision",
        )
    candidate_list = tuple(dict(item) for item in candidates)
    if not callable(tool_runner):
        return TrialReport(
            request_text=request_text,
            tool_called=False,
            tool_name="",
            tool_arguments={},
            tool_status="no_tool_call",
            selection=None,
            error_stage="tool_decision",
        )
    run_count = max(1, min(3, int(runs or 1)))
    captures: list[dict[str, Any]] = []
    selections: list[SelectionResult | None] = []
    rule_selections: list[SelectionResult | None] = []
    normalized_requests: list[dict[str, Any]] = []
    for _index in range(run_count):
        captured: Any = await tool_runner(request_text, dict(request))
        if inspect.isawaitable(captured):
            captured = await captured
        if not isinstance(captured, Mapping):
            captures.append({"tool_name": "", "arguments": {}, "status": "no_tool_call", "error": ""})
            selections.append(None)
            rule_selections.append(None)
            normalized_requests.append({})
            continue
        tool_name = str(captured.get("tool_name") or captured.get("name") or "")
        arguments = captured.get("arguments") or captured.get("parameters") or {}
        if isinstance(arguments, str):
            try:
                decoded_arguments = json.loads(arguments)
            except (TypeError, ValueError, json.JSONDecodeError):
                decoded_arguments = {}
            arguments = decoded_arguments if isinstance(decoded_arguments, Mapping) else {}
        normalized_arguments = dict(arguments) if isinstance(arguments, Mapping) else {}
        captures.append(
            {
                "tool_name": tool_name,
                "arguments": normalized_arguments,
                "status": str(captured.get("status") or "").strip(),
                "error": str(captured.get("error") or "").strip(),
            }
        )
        if tool_name != "pc_generate_photo":
            selections.append(None)
            rule_selections.append(None)
            normalized_requests.append({})
            continue
        selection_request = normalize_photo_selection_request(request_text, normalized_arguments, request)
        normalized_requests.append(selection_request)
        explicit_reference_path = str(selection_request.get("explicit_reference_image_path") or "").strip()
        if explicit_reference_path:
            explicit_candidate = {
                "id": "explicit_reference",
                "kind": "explicit",
                "path": explicit_reference_path,
                "source": explicit_reference_path,
                "reference_roles": ["source"],
            }
            rule_selection = SelectionResult(
                selected=explicit_candidate,
                candidates=(),
                selection_source="explicit_reference",
                selection_reason="explicit_reference_bypasses_catalog",
            )
        else:
            rule_selection = select_photo_reference(selection_request, candidate_list)
        rule_selections.append(rule_selection)
        selection = rule_selection
        if callable(selection_runner) and not explicit_reference_path:
            selected: Any = await selection_runner(selection_request, candidate_list, rule_selection)
            if inspect.isawaitable(selected):
                selected = await selected
            if isinstance(selected, SelectionResult):
                selection = selected
        selections.append(selection)
    signatures = [
        (item["tool_name"], json.dumps(item["arguments"], ensure_ascii=False, sort_keys=True, default=str))
        for item in captures
    ]
    stability = None
    if run_count > 1:
        selected_ids = [
            str(selection.selected.get("id") or "") if selection and selection.selected else ""
            for selection in selections
        ]
        selection_signatures = [
            (
                selected_ids[index],
                selection.selection_source if selection else "",
                selection.model_selected_id if selection else "",
                selection.fallback_id if selection else "",
            )
            for index, selection in enumerate(selections)
        ]
        stability = {
            "runs": run_count,
            "completed_runs": len(captures),
            "stable": (
                len(set(signatures)) == 1
                and len(set(selection_signatures)) == 1
                and all(item["tool_name"] == "pc_generate_photo" for item in captures)
            ),
            "tool_names": [item["tool_name"] for item in captures],
            "tool_arguments": [item["arguments"] for item in captures],
            "normalized_requests": normalized_requests,
            "selected_ids": selected_ids,
            "selection_sources": [selection.selection_source if selection else "" for selection in selections],
            "model_selected_ids": [selection.model_selected_id if selection else "" for selection in selections],
            "rule_fallback_ids": [selection.fallback_id if selection else "" for selection in selections],
        }
    first = captures[0]
    selection = selections[0]
    rule_selection = rule_selections[0]
    normalized_request = normalized_requests[0]
    if first["tool_name"] != "pc_generate_photo":
        tool_status = first.get("status") or "no_tool_call"
        if tool_status not in {"model_error", "model_unavailable", "no_tool_call"}:
            tool_status = "no_tool_call"
        return TrialReport(
            request_text=request_text,
            tool_called=False,
            tool_name=first["tool_name"],
            tool_arguments=first["arguments"],
            tool_status=tool_status,
            selection=selection,
            error_stage="tool_decision",
            stability=stability,
            error=first.get("error") or "",
        )
    selected_id = str(selection.selected.get("id") or "") if selection and selection.selected else ""
    expected_reference_id = str(request.get("expected_reference_id") or "").strip()
    model_selection = {
        "attempted": bool(selection and selection.model_attempted),
        "selected_id": str(selection.model_selected_id or "") if selection else "",
        "used": bool(selection and selection.selection_source == "model"),
    }
    formal_fallback_id = str(selection.fallback_id or "") if selection else (
        str(rule_selection.fallback_id or "") if rule_selection else ""
    )
    rule_fallback = {
        "selected_id": formal_fallback_id,
        "used": bool(
            selection
            and selection.selection_source != "model"
            and selected_id
            and selected_id == formal_fallback_id
        ),
    }
    return TrialReport(
        request_text=request_text,
        tool_called=True,
        tool_name=first["tool_name"],
        tool_arguments=first["arguments"],
        tool_status="captured",
        selection=selection,
        stability=stability,
        normalized_request=normalized_request,
        model_selection=model_selection,
        rule_fallback=rule_fallback,
        expected_reference_id=expected_reference_id,
        expected_match=(selected_id == expected_reference_id) if expected_reference_id else None,
    )


__all__ = [
    "CandidateMatch",
    "SelectionResult",
    "TrialReport",
    "normalize_photo_selection_request",
    "parse_photo_reference_context_categories",
    "select_photo_reference",
    "run_photo_selection_trial",
]
