"""Pure keyword/time-window model route matching; does not call a provider."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import re
from typing import Pattern
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MATCH_MODES = frozenset({"contains", "exact", "regex"})
KEYWORD_LOGICS = frozenset({"any", "all"})


@dataclass(frozen=True)
class RouteRule:
    rule_id: str
    provider_id: str
    model: str
    priority: int
    order: int
    keywords: tuple[str, ...]
    match_mode: str
    keyword_logic: str
    case_sensitive: bool
    patterns: tuple[Pattern[str], ...]
    timezone: str
    start_minute: int | None
    end_minute: int | None
    weekdays: tuple[int, ...]


@dataclass(frozen=True)
class RuleBuildResult:
    rules: tuple[RouteRule, ...]
    issues: tuple[str, ...]


@dataclass(frozen=True)
class RouteMatch:
    rule_id: str
    provider_id: str
    model: str
    reason: str
    matched_keyword: str | None


def _keywords(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        value = re.split(r"[,，;；\n]+", value)
    if not isinstance(value, (list, tuple)):
        raise ValueError("keywords must be text or a list of text")
    if any(type(item) is not str for item in value):
        raise ValueError("keywords must contain text")
    return tuple(dict.fromkeys(item.strip() for item in value if item.strip()))


def _minute(value: object) -> int:
    if type(value) is not str or not re.fullmatch(r"\d{2}:\d{2}", value):
        raise ValueError("time must use HH:MM")
    hour, minute = (int(part) for part in value.split(":"))
    if hour > 23 or minute > 59:
        raise ValueError("time is out of range")
    return hour * 60 + minute


def build_rules(raw: object) -> RuleBuildResult:
    if type(raw) is str:
        try:
            raw = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return RuleBuildResult((), ("invalid_json",))
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return RuleBuildResult((), ("rules_must_be_a_list",))
    rules, issues, seen_ids = [], [], set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            issues.append(f"{index}:invalid_rule")
            continue
        if type(item.get("enabled", True)) is not bool:
            issues.append(f"{index}:enabled_must_be_boolean")
            continue
        if item.get("enabled", True) is False:
            continue
        try:
            rule_id = item.get("id", item.get("name", f"rule-{index + 1}"))
            provider_id, model = item.get("provider_id"), item.get("model", "")
            priority = item.get("priority", 0)
            keywords = _keywords(item.get("keywords", ()))
            mode = item.get("match_mode", "contains")
            logic = item.get("keyword_logic", "any")
            case_sensitive = item.get("case_sensitive", False)
            if (type(rule_id) is not str or not rule_id.strip() or type(provider_id) is not str or
                    not provider_id.strip() or type(model) is not str or type(priority) is not int or
                    type(case_sensitive) is not bool):
                raise ValueError("identity_or_priority")
            rule_id, provider_id, model = rule_id.strip(), provider_id.strip(), model.strip()
            if rule_id in seen_ids:
                raise ValueError("duplicate_id")
            if mode not in MATCH_MODES or logic not in KEYWORD_LOGICS:
                raise ValueError("keyword_mode")
            if mode == "exact" and logic == "all" and len(keywords) > 1:
                raise ValueError("exact_all_is_ambiguous")

            window = item.get("time_window")
            timezone_name, start, end, weekdays = "", None, None, ()
            if window is not None:
                if not isinstance(window, dict):
                    raise ValueError("time_window")
                timezone_name = window.get("timezone", "")
                try:
                    ZoneInfo(timezone_name)
                except (TypeError, ZoneInfoNotFoundError, ValueError) as error:
                    raise ValueError("timezone") from error
                start, end = _minute(window.get("start")), _minute(window.get("end"))
                if start == end:
                    raise ValueError("empty_time_window")
                raw_days = window.get("weekdays", list(range(7)))
                if (not isinstance(raw_days, (list, tuple)) or not raw_days or
                        any(type(day) is not int or day not in range(7) for day in raw_days)):
                    raise ValueError("weekdays")
                weekdays = tuple(sorted(set(raw_days)))
            if not keywords and start is None:
                raise ValueError("selector_required")
            patterns = tuple(re.compile(word, 0 if case_sensitive else re.IGNORECASE)
                             for word in keywords) if mode == "regex" else ()
            rules.append(RouteRule(rule_id, provider_id, model, priority, index,
                                   keywords, mode, logic, case_sensitive, patterns, timezone_name, start,
                                   end, weekdays))
            seen_ids.add(rule_id)
        except (re.error, TypeError, ValueError) as error:
            issues.append(f"{index}:{error}")
    rules.sort(key=lambda rule: (-rule.priority, rule.order))
    return RuleBuildResult(tuple(rules), tuple(issues))


def checked_rules(raw: str) -> tuple[RouteRule, ...]:
    if type(raw) is not str or len(raw.encode("utf-8")) > 24000:
        raise ValueError("route rules require bounded UTF-8 JSON")
    result = build_rules(raw)
    if result.issues:
        raise ValueError("invalid route rules; check selectors, IDs and time windows")
    return result.rules


def validate_targets(rules, providers, *, capabilities, max_output_tokens=None):
    """Check every declared route target against the loaded providers.

    ``max_output_tokens=None`` compares no ceiling: a registry default is
    evaluated after the provider chain is known and is clamped to the selected
    provider there, while an explicitly saved value is still compared here.
    """
    for rule in rules:
        provider = providers.get(rule.provider_id)
        if provider is None:
            raise ValueError("route target has no registered V2 provider")
        if not set(capabilities).issubset(provider.capabilities):
            raise ValueError("route target lacks required task capabilities")
        if max_output_tokens is not None and max_output_tokens > provider.maximum_output_tokens:
            raise ValueError("route target output limit is too small")
        if rule.model and not provider.supports_model_override:
            raise ValueError("route target does not support a model name override")


def output_capabilities(values, providers):
    """Every output capability this task's route can actually send to.

    One request can land on the primary provider, on the fallback, or on any
    replacement-rule target, so the budget a task declares has to fit all of
    them -- a value that only fits the primary still fails after a keyword
    match. Dispatch and the settings projection read the same answer here,
    which is what keeps the ceiling a page shows equal to the one that is sent.
    Malformed rules are skipped: they are refused where rules are saved or
    dispatched, and a settings page must still render.
    """
    provider_ids = [values["provider"], values["fallback"]]
    try:
        provider_ids += [rule.provider_id for rule in checked_rules(values["replacement_rules"])]
    except ValueError:
        pass
    return tuple(providers[provider_id].maximum_output_tokens
                 for provider_id in provider_ids if provider_id in providers)


def _keyword_match(rule: RouteRule, message: str) -> str | None:
    if not rule.keywords:
        return None
    target = message if rule.case_sensitive else message.casefold()
    matched = []
    for index, keyword in enumerate(rule.keywords):
        candidate = keyword if rule.case_sensitive else keyword.casefold()
        if rule.match_mode == "exact":
            hit = target.strip() == candidate.strip()
        elif rule.match_mode == "regex":
            hit = rule.patterns[index].search(message) is not None
        else:
            hit = candidate in target
        matched.append(hit)
    if rule.keyword_logic == "all":
        return ", ".join(rule.keywords) if all(matched) else None
    return next((word for word, hit in zip(rule.keywords, matched) if hit), None)


def _time_match(rule: RouteRule, at: datetime) -> bool:
    if rule.start_minute is None or rule.end_minute is None:
        return True
    local = at.astimezone(ZoneInfo(rule.timezone))
    minute = local.hour * 60 + local.minute
    if rule.start_minute < rule.end_minute:
        return local.weekday() in rule.weekdays and rule.start_minute <= minute < rule.end_minute
    if minute >= rule.start_minute:
        return local.weekday() in rule.weekdays
    previous_weekday = (local.weekday() - 1) % 7
    return minute < rule.end_minute and previous_weekday in rule.weekdays


def match_route(rules: tuple[RouteRule, ...] | list[RouteRule], message: str, at: datetime) -> RouteMatch | None:
    if type(message) is not str:
        raise ValueError("message must be text")
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("route matching requires an aware datetime")
    for rule in rules:
        keyword = _keyword_match(rule, message)
        if rule.keywords and keyword is None:
            continue
        if not _time_match(rule, at):
            continue
        reason = "keyword_and_time" if rule.keywords and rule.start_minute is not None else (
            "keyword" if rule.keywords else "time")
        return RouteMatch(rule.rule_id, rule.provider_id, rule.model, reason, keyword)
    return None
