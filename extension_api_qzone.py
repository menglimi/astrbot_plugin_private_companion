from __future__ import annotations

import asyncio
import re
import secrets
import time
import unicodedata
from types import SimpleNamespace
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from astrbot.api import logger

from .qzone_contract import (
    QZONE_TARGET_PLUGIN_ID,
    QzoneContractError,
    bounded_number,
    build_qzone_config_snapshot,
    build_qzone_descriptor,
    build_qzone_result,
    validate_post_ref,
    validate_qzone_operation_payload,
)


_REFERENCE_TTL_SECONDS = 10 * 60
_REFERENCE_CAPACITY = 200
_SENSITIVE_URL_KEY = re.compile(
    r"(?:auth|cookie|credential|key|secret|sig|skey|ticket|token)",
    re.IGNORECASE,
)


def _output_text(value: Any, maximum: int, *, multiline: bool = False) -> str:
    text = str(value or "")
    cleaned: list[str] = []
    for character in text:
        category = unicodedata.category(character)
        if category in {"Cs"}:
            continue
        if category == "Cc":
            if multiline and character in {"\n", "\r", "\t"}:
                cleaned.append("\n" if character in {"\n", "\r"} else " ")
            continue
        cleaned.append(character)
        if len(cleaned) >= maximum:
            break
    return "".join(cleaned).strip()


def _mask_uin(value: Any) -> str:
    digits_buffer: list[str] = []
    for character in str(value or ""):
        if character.isdigit():
            digits_buffer.append(character)
            if len(digits_buffer) >= 20:
                break
    digits = "".join(digits_buffer)
    if not digits:
        return ""
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"{digits[:2]}{'*' * max(3, len(digits) - 4)}{digits[-2:]}"


def _safe_image_url(value: Any) -> str:
    source = _output_text(value, 2048)
    if source.startswith("//"):
        source = f"https:{source}"
    try:
        parsed = urlsplit(source)
    except ValueError:
        return ""
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        return ""
    if any(_SENSITIVE_URL_KEY.search(str(key or "")) for key, _value in query):
        return ""
    cleaned = urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc,
            parsed.path,
            urlencode(query, doseq=True),
            "",
        )
    )
    return cleaned if len(cleaned) <= 2048 else ""


def _post_value(post: Any, *keys: str) -> Any:
    raw = getattr(post, "raw", None)
    for key in keys:
        value = getattr(post, key, None)
        if value not in (None, ""):
            return value
        if isinstance(raw, Mapping):
            value = raw.get(key)
            if value not in (None, ""):
                return value
    return None


def _post_like_count(post: Any) -> int:
    for key in (
        "like_count",
        "likecount",
        "likes",
        "likenum",
        "praise_num",
        "praisenum",
    ):
        value = _post_value(post, key)
        if value not in (None, ""):
            return bounded_number(value, 0, 10_000_000)
    raw = getattr(post, "raw", None)
    if isinstance(raw, Mapping):
        for container_key in ("like", "likeinfo", "like_info", "praise", "praiseinfo"):
            container = raw.get(container_key)
            if not isinstance(container, Mapping):
                continue
            for key in ("count", "num", "total", "like_count", "likecount"):
                if container.get(key) not in (None, ""):
                    return bounded_number(container.get(key), 0, 10_000_000)
    return 0


def _post_liked(post: Any) -> bool:
    for key in (
        "liked",
        "has_liked",
        "isliked",
        "is_liked",
        "selfliked",
        "haslike",
        "has_like",
    ):
        value = _post_value(post, key)
        if isinstance(value, bool):
            return value
        normalized = str(value or "").strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n"}:
            return False
    return bool(getattr(post, "liked", False))


class _QzoneCapabilityFamily:
    """Generation-bound QZone owner façade with no raw host/path exposure."""

    __slots__ = ("_owner",)

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def _generation(self) -> str:
        generation = self._owner._story_migration_instance_generation()
        if type(generation) is not str:
            raise QzoneContractError("qzone_generation_invalid")
        return generation

    def _state(self) -> str:
        return str(self._owner._story_migration_lifecycle_state() or "closed")

    def _plugin(self) -> Any:
        state = self._state()
        if state in {"superseded", "closed"}:
            raise QzoneContractError("qzone_generation_stale")
        if state != "ready":
            raise QzoneContractError("qzone_service_not_ready")
        plugin = getattr(self._owner, "_plugin", None)
        if plugin is None or getattr(plugin, "context", None) is None:
            raise QzoneContractError("qzone_generation_stale")
        return plugin

    @staticmethod
    def _service_available(plugin: Any) -> bool:
        return all(
            callable(getattr(plugin, method, None))
            for method in (
                "_qzone_get_cookies",
                "_qzone_context_from_cookies",
                "_qzone_query_feeds",
                "_publish_qzone_text",
                "_qzone_like_post",
                "_qzone_comment_post",
                "_qzone_delete_post",
            )
        )

    @staticmethod
    def _platform_supported(plugin: Any) -> bool:
        checker = getattr(plugin, "_qzone_platform_supported", None)
        try:
            return bool(checker(None)) if callable(checker) else False
        except Exception:
            return False

    @staticmethod
    def _credential(plugin: Any) -> tuple[dict[str, Any], str]:
        state = (
            plugin.data.get("qzone_integration")
            if isinstance(getattr(plugin, "data", None), dict)
            and isinstance(plugin.data.get("qzone_integration"), dict)
            else {}
        )
        try:
            blocked_until = float(state.get("auth_block_until") or 0)
        except (TypeError, ValueError, OverflowError):
            blocked_until = 0.0
        manual = str(getattr(plugin, "qzone_cookie", "") or "").strip()
        if manual:
            credential_state = "blocked" if blocked_until > time.time() else "invalid"
            masked = ""
            parser = getattr(plugin, "_qzone_context_from_cookies", None)
            if callable(parser):
                try:
                    context = parser(manual)
                    masked = _mask_uin(context.get("uin") if isinstance(context, dict) else "")
                    if credential_state != "blocked":
                        credential_state = "ready"
                except Exception:
                    pass
            return {
                "configured": True,
                "source": "manual",
                "state": credential_state,
            }, masked
        bot = getattr(plugin, "_qzone_last_bot", None)
        usable = getattr(plugin, "_qzone_runtime_bot_usable", None)
        runtime_ready = False
        if callable(usable):
            try:
                runtime_ready = bool(usable(bot))
            except Exception:
                runtime_ready = False
        if runtime_ready:
            return {
                "configured": True,
                "source": "runtime",
                "state": "blocked" if blocked_until > time.time() else "ready",
            }, ""
        return {
            "configured": False,
            "source": "none",
            "state": "blocked" if blocked_until > time.time() else "missing",
        }, ""

    def qzone_capabilities(self) -> dict[str, Any]:
        state = self._state()
        degraded: list[str] = []
        plugin = getattr(self._owner, "_plugin", None)
        if state != "ready":
            degraded.append("qzone_service_not_ready")
        elif plugin is None or not self._service_available(plugin):
            degraded.append("qzone_service_unavailable")
        return build_qzone_descriptor(
            instance_generation=self._generation(),
            lifecycle_state=state,
            degraded_reasons=degraded,
        )

    def qzone_status_snapshot(self) -> dict[str, Any]:
        plugin = self._plugin()
        service_available = self._service_available(plugin)
        platform_supported = self._platform_supported(plugin)
        enabled = bool(getattr(plugin, "enable_qzone_integration", False))
        credential, masked = self._credential(plugin)
        reasons: list[str] = []
        if not service_available:
            reasons.append("qzone_service_unavailable")
        if not platform_supported:
            reasons.append("qzone_platform_unsupported")
        if credential["state"] != "ready":
            reasons.append(f"qzone_credentials_{credential['state']}")
        if not enabled:
            reasons.append("qzone_feature_disabled")
        return {
            "enabled": enabled,
            "available": bool(service_available and platform_supported),
            "platform_supported": platform_supported,
            "service_available": service_available,
            "credential_state": credential["state"],
            "credential_source": credential["source"],
            "bound": credential["state"] == "ready",
            "uin_masked": masked,
            "features": {
                "feed": bool(enabled),
                "publish": bool(enabled),
                "life_publish": bool(
                    enabled and getattr(plugin, "enable_qzone_life_publish", False)
                ),
                "comment_inbox": bool(
                    enabled and getattr(plugin, "enable_qzone_comment_inbox", False)
                ),
                "generated_image": bool(
                    enabled
                    and getattr(plugin, "enable_qzone_generated_image_publish", False)
                ),
            },
            "degraded_reasons": reasons,
        }

    def export_qzone_config_snapshot(
        self,
        *,
        target_plugin_id: str,
    ) -> dict[str, Any]:
        plugin = self._plugin()
        credential, _masked = self._credential(plugin)
        return build_qzone_config_snapshot(
            instance_generation=self._generation(),
            target_plugin_id=target_plugin_id,
            settings={
                "enabled": bool(getattr(plugin, "enable_qzone_integration", False)),
                "life_publish_enabled": bool(
                    getattr(plugin, "enable_qzone_life_publish", False)
                ),
                "comment_inbox_enabled": bool(
                    getattr(plugin, "enable_qzone_comment_inbox", False)
                ),
                "generated_image_enabled": bool(
                    getattr(plugin, "enable_qzone_generated_image_publish", False)
                ),
            },
            credential=credential,
        )

    def _expire_references_locked(self) -> None:
        now = time.monotonic()
        references = self._owner._qzone_references
        for reference, (expires_at, _post) in tuple(references.items()):
            if expires_at <= now:
                references.pop(reference, None)

    def _remember_post(self, post: Any) -> str:
        references = self._owner._qzone_references
        with self._owner._qzone_reference_lock:
            self._expire_references_locked()
            while len(references) >= _REFERENCE_CAPACITY:
                references.pop(next(iter(references)))
            reference = f"qzref_{secrets.token_urlsafe(24)}"
            references[reference] = (
                time.monotonic() + _REFERENCE_TTL_SECONDS,
                post,
            )
            return reference

    def _resolve_post(self, reference: Any) -> Any:
        normalized = validate_post_ref(reference)
        with self._owner._qzone_reference_lock:
            self._expire_references_locked()
            record = self._owner._qzone_references.get(normalized)
            if record is None:
                raise QzoneContractError("qzone_post_ref_stale")
            return record[1]

    def _forget_post(self, reference: str) -> None:
        with self._owner._qzone_reference_lock:
            self._owner._qzone_references.pop(reference, None)

    @staticmethod
    def _post_images(post: Any) -> list[str]:
        raw_items = getattr(post, "image_items", None)
        if not isinstance(raw_items, list):
            raw_items = getattr(post, "images", None)
        if not isinstance(raw_items, list):
            return []
        values: list[str] = []
        for item in raw_items:
            candidates: list[Any]
            if isinstance(item, Mapping):
                candidates = [
                    item.get(key)
                    for key in (
                        "full_url",
                        "original_url",
                        "origin_url",
                        "large_url",
                        "url",
                        "src",
                        "image_url",
                    )
                ]
            else:
                candidates = [item]
            url = ""
            for candidate in candidates:
                url = _safe_image_url(candidate)
                if url:
                    break
            if url and url not in values:
                values.append(url)
            if len(values) >= 9:
                break
        return values

    def _public_post(
        self,
        post: Any,
        *,
        viewer_uin: Any = "",
        include_comments: bool = False,
        existing_ref: str = "",
    ) -> dict[str, Any]:
        post_uin = str(getattr(post, "uin", "") or "")
        reference = existing_ref or self._remember_post(post)
        raw_comments = getattr(post, "comments", None)
        comments = raw_comments if isinstance(raw_comments, (list, tuple)) else ()
        result: dict[str, Any] = {
            "post_ref": reference,
            "author": {
                "display_name": _output_text(
                    getattr(post, "name", "") or "QQ空间用户",
                    80,
                ),
                "uin_masked": _mask_uin(post_uin),
            },
            "content": _output_text(
                getattr(post, "text", "") or getattr(post, "rt_con", ""),
                1200,
                multiline=True,
            ),
            "created_at": bounded_number(
                getattr(post, "create_time", 0),
                0,
                10_000_000_000,
            ),
            "images": self._post_images(post),
            "like_count": _post_like_count(post),
            "comment_count": min(len(comments), 10_000_000),
            "liked": _post_liked(post),
            "can_delete": bool(
                post_uin
                and str(viewer_uin or "")
                and post_uin == str(viewer_uin or "")
            ),
        }
        if include_comments:
            result["comments"] = [
                {
                    "author": {
                        "display_name": _output_text(
                            getattr(comment, "name", "") or "QQ空间用户",
                            80,
                        ),
                        "uin_masked": _mask_uin(getattr(comment, "uin", "")),
                    },
                    "content": _output_text(
                        getattr(comment, "content", ""),
                        300,
                        multiline=True,
                    ),
                    "created_at": bounded_number(
                        getattr(comment, "create_time", 0),
                        0,
                        10_000_000_000,
                    ),
                }
                for comment in comments[:50]
            ]
        return result

    def _success(self, operation: str, data: dict[str, Any]) -> dict[str, Any]:
        return build_qzone_result(
            instance_generation=self._generation(),
            operation=operation,
            ok=True,
            code="",
            message="",
            data=data,
        )

    def _failure(self, operation: str, code: str) -> dict[str, Any]:
        messages = {
            "qzone_feature_disabled": "QQ 空间能力未启用",
            "qzone_generation_stale": "QQ 空间服务代际已失效",
            "qzone_operation_unsupported": "不支持的 QQ 空间操作",
            "qzone_payload_invalid": "QQ 空间请求格式无效",
            "qzone_payload_too_large": "QQ 空间请求过大",
            "qzone_platform_unsupported": "当前平台不支持 QQ 空间",
            "qzone_post_ref_invalid": "QQ 空间引用无效",
            "qzone_post_ref_stale": "QQ 空间引用已过期",
            "qzone_service_not_ready": "QQ 空间服务尚未就绪",
            "qzone_service_unavailable": "QQ 空间服务不可用",
        }
        normalized = str(code or "qzone_operation_failed")[:64]
        return build_qzone_result(
            instance_generation=self._generation(),
            operation=operation,
            ok=False,
            code=normalized,
            message=messages.get(normalized, "QQ 空间操作失败"),
            data={},
        )

    def _require_operation_available(self, plugin: Any, operation: str) -> None:
        if not self._service_available(plugin):
            raise QzoneContractError("qzone_service_unavailable")
        if not self._platform_supported(plugin):
            raise QzoneContractError("qzone_platform_unsupported")
        if operation != "refresh" and not bool(
            getattr(plugin, "enable_qzone_integration", False)
        ):
            raise QzoneContractError("qzone_feature_disabled")

    async def _feed(self, plugin: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
        cookie_header = await plugin._qzone_get_cookies(None)
        context = plugin._qzone_context_from_cookies(cookie_header)
        self._plugin()
        scope = payload["scope"]
        viewer_uin = str(context.get("uin") or "")
        target_uin = (
            payload["target_uin"]
            if scope == "profile"
            else viewer_uin if scope == "self" else ""
        )
        posts = await plugin._qzone_query_feeds(
            None,
            target_id=target_uin or None,
            pos=(payload["page"] - 1) * 10,
            num=10,
            with_detail=False,
            cookie_header=cookie_header,
        )
        self._plugin()
        return self._success(
            "feed",
            {
                "items": [
                    self._public_post(post, viewer_uin=viewer_uin)
                    for post in list(posts or [])[:10]
                ],
                "scope": scope,
                "page": payload["page"],
                "target_uin_masked": _mask_uin(target_uin),
                "feed_source": "companion",
            },
        )

    async def _detail(self, plugin: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
        reference = payload["post_ref"]
        post = self._resolve_post(reference)
        cookie_header = await plugin._qzone_get_cookies(None)
        context = plugin._qzone_context_from_cookies(cookie_header)
        self._plugin()
        post_uin = str(getattr(post, "uin", "") or "")
        post_tid = str(getattr(post, "tid", "") or "")
        posts = await plugin._qzone_query_feeds(
            None,
            target_id=post_uin or None,
            pos=0,
            num=20,
            with_detail=True,
            cookie_header=cookie_header,
        )
        matched = next(
            (
                candidate
                for candidate in list(posts or [])
                if str(getattr(candidate, "tid", "") or "") == post_tid
            ),
            None,
        )
        if matched is None:
            raise QzoneContractError("qzone_post_ref_stale")
        self._plugin()
        with self._owner._qzone_reference_lock:
            self._owner._qzone_references[reference] = (
                time.monotonic() + _REFERENCE_TTL_SECONDS,
                matched,
            )
        return self._success(
            "detail",
            {
                "post": self._public_post(
                    matched,
                    viewer_uin=context.get("uin"),
                    include_comments=True,
                    existing_ref=reference,
                )
            },
        )

    async def _refresh(self, plugin: Any) -> dict[str, Any]:
        cookie_header = await plugin._qzone_get_cookies(None)
        context = plugin._qzone_context_from_cookies(cookie_header)
        self._plugin()
        return self._success(
            "refresh",
            {
                "refreshed": True,
                "bound": bool(context.get("uin")),
                "uin_masked": _mask_uin(context.get("uin")),
                "has_skey": bool(context.get("skey")),
                "has_p_skey": bool(context.get("p_skey")),
            },
        )

    async def _publish(self, plugin: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
        result = await plugin._publish_qzone_text(
            payload["content"],
            None,
            images=[],
            auto_generate_image=payload["auto_generate_image"],
            publish_reason="manual_publish",
        )
        if type(result) is not dict or result.get("success") is not True:
            raise QzoneContractError("qzone_operation_failed")
        self._plugin()
        post = SimpleNamespace(
            tid=_output_text(result.get("tid"), 120),
            uin=_output_text(result.get("uin"), 20),
            name="我",
            text=_output_text(result.get("text") or payload["content"], 300),
            create_time=int(time.time()),
            images=list(result.get("images") or [])[:9],
            comments=[],
            liked=False,
            raw={},
        )
        return self._success(
            "publish",
            {
                "post_ref": self._remember_post(post),
                "text": _output_text(result.get("text") or payload["content"], 300),
                "image_count": bounded_number(result.get("image_count"), 0, 9),
                "verified": bool(result.get("verified")),
                "verify_message": (
                    "verified" if result.get("verified") else "verification_pending"
                ),
            },
        )

    async def _like(self, plugin: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
        post = self._resolve_post(payload["post_ref"])
        result = await plugin._qzone_like_post(None, post)
        if type(result) is not dict or result.get("success") is not True:
            raise QzoneContractError("qzone_operation_failed")
        self._plugin()
        liked = bool(result.get("liked", True))
        post.liked = liked
        return self._success(
            "like",
            {
                "post_ref": payload["post_ref"],
                "liked": liked,
                "verified": bool(result.get("verified")),
                "verify_message": (
                    "verified" if result.get("verified") else "verification_pending"
                ),
            },
        )

    async def _comment(self, plugin: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
        post = self._resolve_post(payload["post_ref"])
        sent = await plugin._qzone_comment_post(
            None,
            post,
            content=payload["content"],
        )
        self._plugin()
        comments = list(getattr(post, "comments", []) or [])
        comments.append(
            SimpleNamespace(
                uin=0,
                name="我",
                content=_output_text(sent or payload["content"], 120),
                create_time=int(time.time()),
            )
        )
        post.comments = comments[-50:]
        return self._success(
            "comment",
            {
                "post": self._public_post(
                    post,
                    include_comments=True,
                    existing_ref=payload["post_ref"],
                )
            },
        )

    async def _delete(self, plugin: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
        post = self._resolve_post(payload["post_ref"])
        await plugin._qzone_delete_post(None, post)
        self._plugin()
        self._forget_post(payload["post_ref"])
        return self._success(
            "delete",
            {
                "post_ref": payload["post_ref"],
                "deleted": True,
            },
        )

    async def execute_qzone_operation(
        self,
        operation: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            normalized_operation, normalized_payload = validate_qzone_operation_payload(
                operation,
                payload,
            )
            plugin = self._plugin()
            if normalized_operation == "status":
                return self._success("status", self.qzone_status_snapshot())
            self._require_operation_available(plugin, normalized_operation)
            if normalized_operation == "feed":
                return await self._feed(plugin, normalized_payload)
            if normalized_operation == "detail":
                return await self._detail(plugin, normalized_payload)
            if normalized_operation == "refresh":
                return await self._refresh(plugin)
            # This is the last lifecycle check before a mutation helper can
            # issue its first external side effect; there is no await gap.
            self._plugin()
            if normalized_operation == "publish":
                return await self._publish(plugin, normalized_payload)
            if normalized_operation == "like":
                return await self._like(plugin, normalized_payload)
            if normalized_operation == "comment":
                return await self._comment(plugin, normalized_payload)
            return await self._delete(plugin, normalized_payload)
        except asyncio.CancelledError:
            raise
        except QzoneContractError as exc:
            fallback_operation = (
                operation.strip().lower()
                if type(operation) is str
                and operation.strip().lower()
                in {"status", "feed", "detail", "refresh", "publish", "like", "comment", "delete"}
                else "status"
            )
            return self._failure(fallback_operation, exc.code)
        except Exception as exc:
            fallback_operation = (
                operation.strip().lower()
                if type(operation) is str
                and operation.strip().lower()
                in {"status", "feed", "detail", "refresh", "publish", "like", "comment", "delete"}
                else "status"
            )
            logger.warning(
                "[PrivateCompanion] formal QZone operation failed: operation=%s generation=%s error_type=%s",
                fallback_operation,
                self._generation()[-8:],
                type(exc).__name__,
            )
            return self._failure(fallback_operation, "qzone_operation_failed")


__all__ = ["_QzoneCapabilityFamily", "QZONE_TARGET_PLUGIN_ID"]
