# -*- coding: utf-8 -*-
"""NewsExplorationExternalEventMixin。

由 tools/split_mixin_domain.py 从 news_exploration.py 机械抽取（24 个方法 + 0 个模块级名字 + 0 个类级赋值 / 497 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 NewsExplorationMixin）。
"""
from __future__ import annotations

from .news_exploration_shared import logger
from .news_exploration_shared import Any
from .news_exploration_shared import _now_ts
from .news_exploration_shared import _safe_float
from .news_exploration_shared import _safe_int
from .news_exploration_shared import _single_line
from .news_exploration_shared import hashlib
from .news_exploration_shared import re
from .news_exploration_shared import runtime_persona_setting



class NewsExplorationExternalEventMixin:
    """NewsExplorationExternalEventMixin（从 NewsExplorationMixin 拆出）。"""


    def _news_current_agenda_item(self) -> dict[str, Any] | None:
        getter = getattr(self, "_agenda_current_context_item", None)
        if callable(getter):
            try:
                item = getter()
            except Exception:
                return None
            return item if isinstance(item, dict) else None
        legacy_getter = getattr(self, "_get_current_plan_item", None)
        try:
            item = legacy_getter(self.data.get("daily_plan", {})) if callable(legacy_getter) else None
        except Exception:
            item = None
        return item if isinstance(item, dict) else None

    def _clean_bilibili_share_field(self, value: Any, limit: int = 160) -> str:
        cleaner = getattr(self, "_clean_external_share_source_field", None)
        if callable(cleaner):
            try:
                return cleaner(value, limit)
            except Exception:
                pass
        text = _single_line(value, limit)
        checker = getattr(self, "_looks_like_internal_provider_error_text", None)
        if callable(checker):
            try:
                if checker(text):
                    return ""
            except Exception:
                pass
        return text

    def _external_event_pool(self) -> list[dict[str, Any]]:
        raw = self.data.setdefault("external_event_pool", [])
        if not isinstance(raw, list):
            raw = []
            self.data["external_event_pool"] = raw
        return raw

    def _cleanup_external_event_pool(self, *, now: float | None = None) -> list[dict[str, Any]]:
        now = _now_ts() if now is None else now
        kept: list[dict[str, Any]] = []
        for item in self._external_event_pool():
            if not isinstance(item, dict):
                continue
            created = _safe_float(item.get("created_ts"), 0)
            if created > 0 and now - created <= 48 * 3600:
                kept.append(item)
        self.data["external_event_pool"] = kept[-200:]
        return self.data["external_event_pool"]

    def _external_event_signature(self, payload: dict[str, Any], *, source_type: str = "") -> str:
        title = _single_line(payload.get("headline") or payload.get("topic") or payload.get("title") or payload.get("source_title"), 120).lower()
        source = _single_line(payload.get("selected_source") or payload.get("source_title") or payload.get("source"), 60).lower()
        link = _single_line(payload.get("selected_link") or payload.get("source_url") or payload.get("link") or payload.get("video_link"), 220).lower()
        if link:
            link = re.sub(r"https?://", "", link)
            link = link.split("?", 1)[0]
        compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", title)
        return "|".join(part for part in (_single_line(source_type, 24).lower(), source[:24], compact[:64], link[:96]) if part)

    def _external_event_title_fingerprint(self, payload: dict[str, Any], *, source_type: str = "") -> str:
        title = _single_line(payload.get("headline") or payload.get("topic") or payload.get("title") or payload.get("source_title"), 140).lower()
        if not title:
            return ""
        title = re.sub(r"https?://\S+", "", title)
        title = re.sub(r"\b\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\b", "", title)
        title = re.sub(r"\b\d{1,2}:\d{2}\b", "", title)
        title = re.sub(r"(?:第?\d+[期条]|今日|今天|昨夜|昨天|早报|日报|周报|速览|合集|汇总)", "", title)
        compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", title)
        if len(compact) < 8:
            return ""
        digest = hashlib.sha1(compact.encode("utf-8", "ignore")).hexdigest()[:20]
        return f"{_single_line(source_type, 24).lower()}:title:{digest}"

    def _external_event_self_link_cache(self) -> dict[str, Any]:
        raw = self.data.setdefault("external_event_self_link_cache", {})
        if not isinstance(raw, dict):
            raw = {}
            self.data["external_event_self_link_cache"] = raw
        return raw

    def _cleanup_external_event_self_link_cache(self, *, now: float | None = None) -> dict[str, Any]:
        now = _now_ts() if now is None else now
        cache = self._external_event_self_link_cache()
        kept: dict[str, Any] = {}
        ranked: list[tuple[float, str, dict[str, Any]]] = []
        for key, item in cache.items():
            if not isinstance(item, dict):
                continue
            created = _safe_float(item.get("created_ts"), 0)
            if created <= 0 or now - created > 72 * 3600:
                continue
            ranked.append((_safe_float(item.get("last_hit_ts"), created), str(key), item))
        ranked.sort(key=lambda row: row[0])
        for _, key, item in ranked[-240:]:
            kept[key] = item
        self.data["external_event_self_link_cache"] = kept
        return kept

    def _external_event_self_link_cache_keys(self, payload: dict[str, Any], *, source_type: str = "") -> list[str]:
        keys: list[str] = []
        for key in (
            self._external_event_signature(payload, source_type=source_type),
            self._external_event_title_fingerprint(payload, source_type=source_type),
        ):
            key = _single_line(key, 220)
            if key and key not in keys:
                keys.append(key)
        return keys

    def _cached_external_event_wish(self, payload: dict[str, Any], *, source_type: str = "") -> dict[str, Any]:
        keys = self._external_event_self_link_cache_keys(payload, source_type=source_type)
        if not keys:
            return {}
        now = _now_ts()
        cache = self._cleanup_external_event_self_link_cache(now=now)
        for key in keys:
            item = cache.get(key)
            if not isinstance(item, dict):
                continue
            wish = item.get("wish")
            if not isinstance(wish, dict):
                continue
            item["hit_count"] = _safe_int(item.get("hit_count"), 0, 0) + 1
            item["last_hit_ts"] = now
            result = dict(wish)
            result["cache_hit"] = True
            result["cache_key"] = key
            logger.info(
                "外界信息自我关联命中缓存: source=%s key=%s hit=%s",
                source_type,
                key,
                item["hit_count"],
            )
            return result
        return {}

    def _remember_external_event_wish_cache(self, payload: dict[str, Any], wish: dict[str, Any], *, source_type: str = "") -> None:
        if not isinstance(wish, dict) or not wish:
            return
        keys = self._external_event_self_link_cache_keys(payload, source_type=source_type)
        if not keys:
            return
        now = _now_ts()
        cache = self._cleanup_external_event_self_link_cache(now=now)
        stored_wish = {
            key: value
            for key, value in dict(wish).items()
            if key
            in {
                "relevance",
                "desire",
                "should_share",
                "share_probability",
                "self_link",
                "motive",
                "tone",
                "boundary",
                "source_type",
                "boost_reason",
            }
        }
        stored_wish["created_ts"] = now
        stored_wish["source_type"] = _single_line(source_type, 24)
        title = _single_line(payload.get("headline") or payload.get("topic") or payload.get("title") or payload.get("source_title"), 120)
        for key in keys:
            cache[key] = {
                "wish": stored_wish,
                "title": title,
                "source_type": _single_line(source_type, 24),
                "created_ts": now,
                "last_hit_ts": now,
                "hit_count": _safe_int((cache.get(key) or {}).get("hit_count") if isinstance(cache.get(key), dict) else 0, 0, 0),
            }
        self._cleanup_external_event_self_link_cache(now=now)

    def _external_event_recently_seen(self, payload: dict[str, Any], *, source_type: str = "", now: float | None = None) -> bool:
        now = _now_ts() if now is None else now
        signature = self._external_event_signature(payload, source_type=source_type)
        if not signature:
            return False
        for item in self._cleanup_external_event_pool(now=now):
            if str(item.get("signature") or "") != signature:
                continue
            if now - _safe_float(item.get("created_ts"), 0) <= 24 * 3600:
                return True
        return False

    def _remember_external_event(self, payload: dict[str, Any], *, source_type: str = "", reason: str = "") -> None:
        now = _now_ts()
        signature = self._external_event_signature(payload, source_type=source_type)
        if not signature:
            return
        pool = self._cleanup_external_event_pool(now=now)
        pool.append(
            {
                "signature": signature,
                "source_type": _single_line(source_type, 24),
                "reason": _single_line(reason, 40),
                "title": _single_line(payload.get("headline") or payload.get("topic") or payload.get("title"), 120),
                "created_ts": now,
            }
        )
        del pool[:-200]

    @staticmethod
    def _external_event_payload_text(payload: dict[str, Any], *, limit: int = 900) -> str:
        if not isinstance(payload, dict):
            return ""
        parts: list[str] = []
        for key in (
            "headline",
            "topic",
            "title",
            "impression",
            "summary",
            "note",
            "comment",
            "review",
            "up_name",
            "selected_source",
            "source",
            "source_title",
            "video_context_text",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                value = " ".join(str(item) for item in value[:6])
            text = _single_line(value, 260)
            if text:
                parts.append(text)
        for key in ("tags", "keywords", "actions", "memory_context"):
            value = payload.get(key)
            if isinstance(value, list):
                text = " ".join(_single_line(item, 80) for item in value[:8] if _single_line(item, 80))
                if text:
                    parts.append(text)
        return _single_line(" ".join(parts), limit)

    def _external_event_user_preference_profile(self, user: dict[str, Any]) -> dict[str, Any]:
        profile: dict[str, Any] = {
            "positive": [],
            "negative": [],
            "context": "",
        }
        memory = user.get("companion_memory") if isinstance(user, dict) else {}
        llm_profile = memory.get("profile") if isinstance(memory, dict) else {}

        def add_values(key: str, target: str, limit: int = 8) -> None:
            value = llm_profile.get(key) if isinstance(llm_profile, dict) else None
            values: list[str] = []
            if isinstance(value, list):
                values = [_single_line(item, 80) for item in value[:limit] if _single_line(item, 80)]
            else:
                text = _single_line(value, 160)
                if text:
                    values = [text]
            profile[target].extend(values)

        add_values("interests", "positive", 10)
        add_values("weak_preferences", "positive", 10)
        add_values("strong_memories", "positive", 5)
        add_values("boundaries", "negative", 10)
        action_prefs = user.get("action_preferences") if isinstance(user, dict) else {}
        if isinstance(action_prefs, dict):
            for key, value in action_prefs.items():
                text = _single_line(value if isinstance(value, str) else key, 80)
                if text:
                    profile["positive"].append(text)
        context_parts = []
        formatter = getattr(self, "_format_companion_memory_for_prompt", None)
        if callable(formatter):
            try:
                context_parts.append(_single_line(formatter(user), 900))
            except Exception:
                pass
        for key in ("last_user_message", "last_companion_message", "proactive_boundary_note"):
            text = _single_line(user.get(key), 180) if isinstance(user, dict) else ""
            if text:
                context_parts.append(text)
        profile["positive"] = list(dict.fromkeys(item for item in profile["positive"] if item))[:24]
        profile["negative"] = list(dict.fromkeys(item for item in profile["negative"] if item))[:16]
        profile["context"] = _single_line("；".join(part for part in context_parts if part), 1200)
        return profile

    @staticmethod
    def _external_event_text_match_score(text: str, clues: list[str], *, negative: bool = False) -> tuple[int, list[str]]:
        haystack = str(text or "").lower()
        score = 0
        hits: list[str] = []
        for clue in clues:
            clue_text = _single_line(clue, 80)
            if not clue_text:
                continue
            tokens = re.findall(r"[\u4e00-\u9fff]{2,8}|[a-z0-9_]{3,24}", clue_text.lower())
            matched = 0
            for token in tokens[:6]:
                if token and token in haystack:
                    matched += 1
            if matched:
                hits.append(clue_text)
                score += min(5 if negative else 4, matched * (3 if negative else 2))
        return min(20 if negative else 18, score), hits[:5]

    def _external_event_user_preference_decision(self, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        profile = self._external_event_user_preference_profile(user)
        payload_text = self._external_event_payload_text(payload).lower()
        positive_score, positive_hits = self._external_event_text_match_score(payload_text, profile.get("positive", []))
        negative_score, negative_hits = self._external_event_text_match_score(payload_text, profile.get("negative", []), negative=True)
        context_score, context_hits = self._external_event_text_match_score(payload_text, [profile.get("context", "")])
        score = max(0, min(30, positive_score + min(8, context_score) - negative_score))
        return {
            "score": score,
            "positive_hits": positive_hits,
            "negative_hits": negative_hits,
            "context_hits": context_hits,
            "blocked": bool(negative_score >= 8 and positive_score <= 2),
        }

    def _external_event_payload_from_news_item(self, item: dict[str, Any], *, base_digest: dict[str, Any] | None = None) -> dict[str, Any]:
        title = _single_line(item.get("title"), 100)
        summary = _single_line(item.get("summary") or item.get("snippet") or item.get("video_context_text"), 240)
        source = _single_line(item.get("source"), 40)
        payload = {
            "topic": title or _single_line((base_digest or {}).get("topic"), 40) or "新闻",
            "headline": title or _single_line((base_digest or {}).get("headline"), 100),
            "impression": summary or _single_line((base_digest or {}).get("impression"), 240),
            "selected_key": _single_line(item.get("key"), 32),
            "selected_link": _single_line(item.get("link") or item.get("video_link"), 400),
            "selected_source": source,
            "items": (base_digest or {}).get("items") if isinstance((base_digest or {}).get("items"), list) else [],
            "created_ts": _safe_float((base_digest or {}).get("created_ts"), 0) or _now_ts(),
        }
        for key in ("published_ts", "score", "video_context_text", "video_link", "article_readable", "video_subtitle_readable"):
            if key in item:
                payload[key] = item.get(key)
        return payload

    def _select_external_event_for_user(
        self,
        user: dict[str, Any],
        candidates: list[dict[str, Any]],
        *,
        source_type: str,
        base_payload: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = _now_ts() if now is None else now
        best: dict[str, Any] = {}
        best_score = -10**9
        for raw in candidates[:12]:
            if not isinstance(raw, dict):
                continue
            payload = self._external_event_payload_from_news_item(raw, base_digest=base_payload) if source_type == "news" else dict(raw)
            if base_payload and source_type != "news":
                payload.setdefault("created_ts", base_payload.get("created_ts"))
            pref = self._external_event_user_preference_decision(user, payload)
            if pref.get("blocked"):
                continue
            quality = self._external_event_quality_score(payload)
            user_match = self._external_event_user_interest_score(user, payload)
            duplicate_penalty = 12 if self._external_event_recently_seen(payload, source_type=source_type, now=now) else 0
            total = quality * 2 + user_match * 3 + _safe_int(pref.get("score"), 0) - duplicate_penalty
            if total > best_score:
                best_score = total
                best = {
                    "payload": payload,
                    "preference": pref,
                    "selection_score": total,
                }
        return best

    def _external_event_user_interest_score(self, user: dict[str, Any], payload: dict[str, Any]) -> int:
        memory_text = ""
        formatter = getattr(self, "_format_companion_memory_for_prompt", None)
        if callable(formatter):
            try:
                memory_text = _single_line(formatter(user), 700).lower()
            except Exception:
                memory_text = ""
        haystack = self._external_event_payload_text(payload).lower()
        if not memory_text or not haystack:
            return 0
        score = 0
        for token in re.findall(r"[\u4e00-\u9fff]{2,8}|[a-z0-9_]{3,24}", haystack):
            if token and token in memory_text:
                score += 1
        return min(10, score)

    def _external_event_quality_score(self, payload: dict[str, Any]) -> int:
        score = 0
        if _single_line(payload.get("headline") or payload.get("topic") or payload.get("title"), 120):
            score += 2
        impression = _single_line(payload.get("impression") or payload.get("summary") or payload.get("note"), 320)
        if len(impression) >= 36:
            score += 2
        if _single_line(payload.get("selected_link") or payload.get("source_url") or payload.get("link"), 220):
            score += 1
        if _safe_float(payload.get("published_ts"), 0) > 0:
            score += 1
        if _safe_int(payload.get("score"), 0, 0, 10) >= max(
            1,
            _safe_int(runtime_persona_setting(self, "bilibili_share_min_score", 7), 7, 0, 10),
        ):
            score += 2
        return min(10, score)

    def _external_event_share_decision(
        self,
        user: dict[str, Any],
        payload: dict[str, Any],
        *,
        source_type: str,
        wish: dict[str, Any] | None = None,
        base_probability: float = 0.2,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = _now_ts() if now is None else now
        relevance = _safe_int((wish or {}).get("relevance"), 0, 0, 10)
        desire = _safe_int((wish or {}).get("desire"), 0, 0, 10)
        user_match = self._external_event_user_interest_score(user, payload)
        quality = self._external_event_quality_score(payload)
        freshness = 6
        published_ts = _safe_float(payload.get("published_ts"), 0)
        if published_ts > 0:
            age_hours = max(0.0, (now - published_ts) / 3600.0)
            if age_hours <= 6:
                freshness = 10
            elif age_hours <= 24:
                freshness = 8
            elif age_hours <= 72:
                freshness = 5
            else:
                freshness = 2
        noisy = self._external_event_recently_seen(payload, source_type=source_type, now=now)
        duplicate_penalty = 4 if noisy else 0
        interrupt_penalty = 0
        external_idle_min = _safe_int(
            runtime_persona_setting(self, "external_event_idle_minutes", 90),
            90,
            5,
            1440,
        )
        if now - _safe_float(user.get("last_seen"), 0) < max(
            runtime_persona_setting(self, "idle_minutes", 60), external_idle_min
        ) * 60:
            interrupt_penalty += 3
        if now - _safe_float(user.get("last_sent"), 0) < max(
            runtime_persona_setting(self, "min_interval_minutes", 120), 120
        ) * 60:
            interrupt_penalty += 2
        total = relevance * 2 + desire * 2 + user_match * 2 + quality + freshness - duplicate_penalty - interrupt_penalty
        normalized = max(0.0, min(1.0, base_probability + total / 100.0))
        return {
            "score": max(0, min(100, total * 2)),
            "probability": normalized,
            "user_match": user_match,
            "quality": quality,
            "freshness": freshness,
            "duplicate_penalty": duplicate_penalty,
            "interrupt_penalty": interrupt_penalty,
            "should_share": bool((wish or {}).get("should_share"))
            and total
            >= _safe_int(
                runtime_persona_setting(self, "external_event_share_min_total", 18),
                18,
                0,
                100,
            )
            and not noisy,
            "duplicate": noisy,
        }

    def _external_link_share_cooldown_remaining(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> float:
        """Return the shared cooldown remaining for plugin-initiated link shares.

        Older versions recorded only per-source candidate timestamps.  Include
        those fields so upgrading immediately stops Bilibili/news/web shares
        from alternating around their separate short cooldowns.
        """
        if not isinstance(user, dict):
            return 0.0
        cooldown_hours = max(
            0.0,
            _safe_float(
                runtime_persona_setting(self, "external_link_share_cooldown_hours", 72),
                72.0,
            ),
        )
        if cooldown_hours <= 0:
            return 0.0
        now = _now_ts() if now is None else now
        last_share_at = max(
            (
                _safe_float(user.get(field), 0.0)
                for field in (
                    "last_external_link_share_at",
                    "last_external_link_candidate_at",
                    "last_bilibili_share_at",
                    "last_news_share_at",
                    "last_web_exploration_share_at",
                    "last_external_event_self_link_at",
                )
            ),
            default=0.0,
        )
        if last_share_at <= 0:
            return 0.0
        return max(0.0, last_share_at + cooldown_hours * 3600.0 - now)

    @staticmethod
    def _note_external_link_candidate(user: dict[str, Any], *, now: float) -> None:
        if isinstance(user, dict):
            user["last_external_link_candidate_at"] = now
