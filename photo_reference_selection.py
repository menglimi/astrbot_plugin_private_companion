"""Deterministic reference selection and side-effect-free selection trials."""
from __future__ import annotations

import inspect
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping

from .photo_reference_intent import analyze_reference_intent


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": dict(self.selected) if self.selected else None,
            "candidates": [asdict(item) for item in self.candidates],
            "selection_source": self.selection_source,
            "selection_reason": self.selection_reason,
            "fallback_id": self.fallback_id,
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

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["selection"] = self.selection.to_dict() if self.selection else None
        return payload


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _categories(text: str) -> set[str]:
    normalized = _text(text)
    result: set[str] = set()
    for name, tokens in {
        "home": ("在家", "家里", "居家", "home"),
        "bedroom": ("卧室", "床边", "bedroom", "bedtime"),
        "school": ("上学", "学校", "校园", "school"),
        "outdoor": ("出门", "户外", "街头", "outdoor"),
        "night": ("晚上", "夜晚", "night"),
        "bedtime": ("睡前", "睡衣", "bedtime"),
        "daytime": ("白天", "daytime"),
    }.items():
        if any(token in normalized for token in tokens):
            result.add(name)
    return result


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
        requested_outfit = str(request.get("outfit_category") or "") or _outfit(request_text)
        requested_scenes = set(request.get("scene_categories") or ()) | _categories(request_text)
        requested_times = set(request.get("time_categories") or ()) | _categories(request_text)
        excluded_scenes = set(request.get("excluded_scene_categories") or ())
        excluded_times = set(request.get("excluded_time_categories") or ())
        excluded_outfit = set(request.get("excluded_outfit_categories") or ())
    else:
        request_text = str(request or "")
        requested_outfit = _outfit(request_text)
        requested_scenes = _categories(request_text)
        requested_times = _categories(request_text)
        excluded_scenes, excluded_times, excluded_outfit = set(), set(), set()
    ranked: list[tuple[Mapping[str, Any], float, set[str], set[str], str]] = []
    for index, candidate in enumerate(candidates):
        cid = str(candidate.get("id") or f"candidate-{index + 1}")
        roles = set(str(v) for v in (candidate.get("reference_roles") or ()))
        scenes = set(str(v) for v in (candidate.get("scene_categories") or ()))
        times = set(str(v) for v in (candidate.get("time_categories") or ()))
        category = _text(candidate.get("outfit_category")) if "outfit" in roles else ""
        matched = set()
        excluded = set()
        score = float(candidate.get("priority") or 0) / 100.0
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
        if not excluded and not matched:
            score += 1 if "identity" in roles else 0
        eligibility = _text(candidate.get("selection_eligibility") or "matching_only")
        if eligibility == "disabled":
            excluded.add("disabled")
            score = -1000
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


async def run_photo_selection_trial(
    request: Mapping[str, Any],
    *,
    candidates: Iterable[Mapping[str, Any]],
    tool_runner: Callable[[str, Mapping[str, Any]], Any] | None = None,
    runs: int = 1,
) -> TrialReport:
    """Capture a model tool decision without executing the production tool."""
    request_text = str(request.get("request_text") or request.get("text") or "").strip()
    if not request_text:
        return TrialReport("", False, "", {}, "invalid_request", None, "tool_decision")
    selection = select_photo_reference(request, candidates)
    if not callable(tool_runner):
        return TrialReport(request_text, False, "", {}, "no_tool_call", selection, "tool_decision")
    captured: Any = await tool_runner(request_text, dict(request))
    if inspect.isawaitable(captured):
        captured = await captured
    if not isinstance(captured, Mapping):
        return TrialReport(request_text, False, "", {}, "no_tool_call", selection, "tool_decision")
    tool_name = str(captured.get("tool_name") or captured.get("name") or "")
    arguments = captured.get("arguments") or captured.get("parameters") or {}
    if tool_name != "pc_generate_photo":
        return TrialReport(request_text, False, tool_name, dict(arguments) if isinstance(arguments, Mapping) else {}, "no_tool_call", selection, "tool_decision")
    stability = None
    if max(1, min(3, int(runs or 1))) > 1:
        stability = {"runs": int(runs), "stable": True, "selected_ids": [selection.fallback_id] * int(runs)}
    return TrialReport(request_text, True, tool_name, dict(arguments) if isinstance(arguments, Mapping) else {}, "captured", selection, stability=stability)


__all__ = ["CandidateMatch", "SelectionResult", "TrialReport", "select_photo_reference", "run_photo_selection_trial"]
