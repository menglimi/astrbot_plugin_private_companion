# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from quart import request

from .qzone_recent_parser import parse_recent_feeds


class PrivateCompanionPageApiQzoneMixin:
    async def refresh_qzone_cookies(self) -> dict[str, Any]:
        try:
            cookie_header = await self.plugin._qzone_get_cookies(None)
            ctx = self.plugin._qzone_context_from_cookies(cookie_header)
            async with self.plugin._data_lock:
                state = self.plugin.data.get("qzone_integration") if isinstance(self.plugin.data.get("qzone_integration"), dict) else {}
                if isinstance(state, dict):
                    clearer = getattr(self.plugin, "_qzone_clear_auth_failure", None)
                    if callable(clearer):
                        clearer(state)
            return self._ok(
                {
                    "refreshed": True,
                    "uin": int(ctx.get("uin") or 0),
                    "has_skey": bool(ctx.get("skey")),
                    "has_p_skey": bool(ctx.get("p_skey")),
                }
            )
        except Exception as exc:
            return self._error(str(exc))

    async def get_qzone_status(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                data = deepcopy(self.plugin.data if isinstance(self.plugin.data, dict) else {})
            summary = self._qzone_summary(data)
            login = {"bound": False, "uin": 0, "nickname": "", "avatar": ""}
            if summary.get("available"):
                try:
                    cookie_header = await self.plugin._qzone_get_cookies(None)
                    ctx = self.plugin._qzone_context_from_cookies(cookie_header)
                    login["bound"] = True
                    login["uin"] = int(ctx.get("uin") or 0)
                    login["nickname"] = f"QQ {ctx.get('uin')}"
                except Exception:
                    pass
            return self._ok(
                {
                    "login": login,
                    "summary": summary,
                    "loaded_at": int(time.time()),
                }
            )
        except Exception as exc:
            return self._error(str(exc))

    async def get_qzone_feed(self) -> dict[str, Any]:
        try:
            scope = self._single_line(request.args.get("scope"), 24) or "self"
            hostuin = self._single_line(request.args.get("hostuin"), 40)
            page = self._clamp_int(request.args.get("page"), 1, 1, 10)
            target = hostuin if scope == "profile" else ""
            if scope == "profile" and not target:
                return self._ok({"items": [], "scope": scope, "target_uin": target})
            if scope == "friends":
                cookie_header = await self.plugin._qzone_get_cookies(None)
                ctx = self.plugin._qzone_context_from_cookies(cookie_header)
                raw = await self.plugin._qzone_request(
                    None,
                    "GET",
                    "https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more",
                    params={
                        "uin": ctx["uin"],
                        "scope": 0,
                        "view": 1,
                        "filter": "all",
                        "flag": 1,
                        "applist": "all",
                        "pagenum": page,
                        "aisortEndTime": 0,
                        "aisortOffset": 0,
                        "aisortBeginTime": 0,
                        "begintime": 0,
                        "format": "json",
                        "g_tk": ctx["gtk"],
                        "useutf8": 1,
                        "outputhtmlfeed": 1,
                    },
                    cookie_header=cookie_header,
                )
                posts = parse_recent_feeds(raw)
                return self._ok(
                    {
                        "items": [
                            self._qzone_page_post_payload(post, include_comments=False, viewer_uin=int(ctx.get("uin") or 0))
                            for post in posts[:10]
                        ],
                        "scope": scope,
                        "page": page,
                        "target_uin": target,
                    }
                )
            cookie_header = await self.plugin._qzone_get_cookies(None)
            ctx = self.plugin._qzone_context_from_cookies(cookie_header)
            posts = await self.plugin._qzone_query_feeds(
                None,
                target_id=target or None,
                pos=0,
                num=10,
                with_detail=False,
                cookie_header=cookie_header,
            )
            return self._ok(
                {
                    "items": [
                        self._qzone_page_post_payload(post, include_comments=False, viewer_uin=int(ctx.get("uin") or 0))
                        for post in posts
                    ],
                    "scope": scope,
                    "page": page,
                    "target_uin": target,
                }
            )
        except Exception as exc:
            return self._error(str(exc))

    async def get_qzone_detail(self) -> dict[str, Any]:
        try:
            post = await self._qzone_page_resolve_post_reference(
                self._single_line(request.args.get("id") or request.args.get("post_id"), 120),
                request.args,
                with_detail=True,
            )
            cookie_header = await self.plugin._qzone_get_cookies(None)
            ctx = self.plugin._qzone_context_from_cookies(cookie_header)
            refreshed = await self.plugin._qzone_query_feeds(
                None,
                target_id=str(getattr(post, "uin", "") or ""),
                pos=0,
                num=10,
                with_detail=True,
                cookie_header=cookie_header,
            )
            matched = next((item for item in refreshed if str(getattr(item, "tid", "") or "") == str(getattr(post, "tid", "") or "")), post)
            return self._ok({"post": self._qzone_page_post_payload(matched, include_comments=True, viewer_uin=int(ctx.get("uin") or 0))})
        except Exception as exc:
            return self._error(str(exc))

    async def publish_qzone_post(self) -> dict[str, Any]:
        try:
            payload = await request.get_json(silent=True) or {}
            content = self._multi_line(payload.get("content") if payload.get("content") is not None else payload.get("text"), 300)
            if not content:
                return self._error("说说内容不能为空")
            result = await self.plugin._publish_qzone_text(content, None, images=[], auto_generate_image=True)
            if not result.get("success"):
                return self._error(result.get("message") or "发布失败")
            return self._ok(result)
        except Exception as exc:
            return self._error(str(exc))

    async def like_qzone_post(self) -> dict[str, Any]:
        try:
            payload = await request.get_json(silent=True) or {}
            post = await self._qzone_page_resolve_post_reference(
                self._single_line(payload.get("id") or payload.get("post_id"), 120),
                payload,
                with_detail=False,
            )
            result = await self.plugin._qzone_like_post(None, post)
            setattr(post, "liked", bool(result.get("liked", True)))
            return self._ok(
                {
                    "liked": bool(result.get("liked", True)),
                    "verified": bool(result.get("verified")),
                    "verify_message": result.get("verify_message") or "",
                    "id": self._qzone_page_post_id(post),
                }
            )
        except Exception as exc:
            return self._error(str(exc))

    async def comment_qzone_post(self) -> dict[str, Any]:
        try:
            payload = await request.get_json(silent=True) or {}
            post = await self._qzone_page_resolve_post_reference(
                self._single_line(payload.get("id") or payload.get("post_id"), 120),
                payload,
                with_detail=True,
            )
            content = self._multi_line(payload.get("content") if payload.get("content") is not None else payload.get("text"), 120)
            if not content:
                return self._error("评论内容不能为空")
            sent = await self.plugin._qzone_comment_post(None, post, content=content)
            author_uin = 0
            author_name = "我"
            try:
                cookie_header = await self.plugin._qzone_get_cookies(None)
                ctx = self.plugin._qzone_context_from_cookies(cookie_header)
                author_uin = int(ctx.get("uin") or 0)
                author_name = str(ctx.get("uin") or "我")
            except Exception:
                pass
            comments = list(getattr(post, "comments", []) or [])
            comments.append(
                SimpleNamespace(
                    comment_id=f"local-{int(time.time())}",
                    comment_key="",
                    comment_legacy_id="",
                    uin=author_uin,
                    name=author_name,
                    content=sent,
                    create_time=int(time.time()),
                    raw={},
                )
            )
            setattr(post, "comments", comments)
            return self._ok({"post": self._qzone_page_post_payload(post, include_comments=True, viewer_uin=author_uin)})
        except Exception as exc:
            return self._error(str(exc))

    async def delete_qzone_post(self) -> dict[str, Any]:
        try:
            payload = await request.get_json(silent=True) or {}
            post = await self._qzone_page_resolve_post_reference(
                self._single_line(payload.get("id") or payload.get("post_id"), 120),
                payload,
                with_detail=False,
            )
            cookie_header = await self.plugin._qzone_get_cookies(None)
            ctx = self.plugin._qzone_context_from_cookies(cookie_header)
            viewer_uin = int(ctx.get("uin") or 0)
            if not self._qzone_page_can_delete(post, viewer_uin=viewer_uin):
                return self._error("只能删除当前登录 QQ 自己发布的说说")
            await self.plugin._qzone_delete_post(None, post, cookie_header=cookie_header)
            post_id = self._qzone_page_post_id(post)
            self._qzone_page_store().pop(post_id, None)
            return self._ok({"deleted": True, "id": post_id})
        except Exception as exc:
            return self._error(str(exc))

    def _qzone_page_store(self) -> dict[str, Any]:
        store = getattr(self, "_qzone_page_posts", None)
        if not isinstance(store, dict):
            store = {}
            self._qzone_page_posts = store
        return store

    def _qzone_page_post_id(self, post: Any) -> str:
        return f"{self._single_line(getattr(post, 'uin', ''), 40)}:{self._single_line(getattr(post, 'tid', ''), 80)}"

    def _qzone_page_remember_post(self, post: Any) -> str:
        post_id = self._qzone_page_post_id(post)
        self._qzone_page_store()[post_id] = post
        return post_id

    def _qzone_page_resolve_post(self, post_id: str) -> Any:
        post = self._qzone_page_store().get(str(post_id or "").strip())
        if post is None:
            raise RuntimeError("说说引用已失效，请刷新页面后重试")
        return post

    async def _qzone_page_resolve_post_reference(self, post_id: str = "", source: Any = None, *, with_detail: bool = False) -> Any:
        post_key = self._single_line(post_id, 160)
        if post_key:
            post = self._qzone_page_store().get(post_key)
            if post is not None:
                return post

        def pick(*keys: str) -> str:
            getter = getattr(source, "get", None)
            if not callable(getter):
                return ""
            for key in keys:
                value = getter(key)
                if value not in (None, ""):
                    return self._single_line(value, 160)
            return ""

        hostuin = pick("hostuin", "host_uin", "uin", "author_uin", "target_uin")
        fid = pick("fid", "tid", "post_tid")
        topic_id = pick("topicId", "topic_id")
        if topic_id and "_" in topic_id:
            topic_parts = topic_id.split("_")
            if len(topic_parts) >= 2:
                hostuin = hostuin or self._single_line(topic_parts[0], 40)
                fid = fid or self._single_line(topic_parts[1], 120)
        if post_key and ":" in post_key:
            left, right = post_key.split(":", 1)
            hostuin = hostuin or self._single_line(left, 40)
            fid = fid or self._single_line(right, 120)
        if not fid:
            raise RuntimeError("说说引用已失效，请刷新页面后重试")

        cookie_header = await self.plugin._qzone_get_cookies(None)
        if not hostuin:
            ctx = self.plugin._qzone_context_from_cookies(cookie_header)
            hostuin = self._single_line(ctx.get("uin"), 40)
        if not hostuin:
            raise RuntimeError("说说引用已失效，请刷新页面后重试")
        posts = await self.plugin._qzone_query_feeds(
            None,
            target_id=hostuin,
            pos=0,
            num=20,
            with_detail=with_detail,
            cookie_header=cookie_header,
        )
        for post in posts:
            post_tid = self._single_line(getattr(post, "tid", ""), 120)
            post_fid = self._single_line(getattr(post, "fid", ""), 120)
            if fid and fid in {post_tid, post_fid}:
                self._qzone_page_remember_post(post)
                return post
        raise RuntimeError("未找到这条说说，请刷新动态后重试")

    @staticmethod
    def _qzone_page_raw_value(post: Any, *keys: str) -> Any:
        raw = getattr(post, "raw", None)
        for key in keys:
            value = getattr(post, key, None)
            if value not in (None, ""):
                return value
            if isinstance(raw, dict):
                value = raw.get(key)
                if value not in (None, ""):
                    return value
        return None

    def _qzone_page_like_count(self, post: Any) -> int:
        raw = getattr(post, "raw", None)
        for key in ("like_count", "likecount", "likes", "likenum", "praise_num", "praisenum"):
            value = self._qzone_page_raw_value(post, key)
            try:
                if value not in (None, ""):
                    return max(0, int(float(value)))
            except Exception:
                pass
        if isinstance(raw, dict):
            for container_key in ("like", "likeinfo", "like_info", "praise", "praiseinfo"):
                container = raw.get(container_key)
                if not isinstance(container, dict):
                    continue
                for key in ("count", "num", "total", "like_count", "likecount"):
                    try:
                        value = container.get(key)
                        if value not in (None, ""):
                            return max(0, int(float(value)))
                    except Exception:
                        pass
        return 0

    def _qzone_page_liked(self, post: Any) -> bool:
        for key in ("liked", "has_liked", "isliked", "is_liked", "selfliked", "haslike", "has_like"):
            value = self._qzone_page_raw_value(post, key)
            if isinstance(value, bool):
                return value
            text = str(value or "").strip().lower()
            if text in {"1", "true", "yes", "y"}:
                return True
            if text in {"0", "false", "no", "n"}:
                return False
        return bool(getattr(post, "liked", False))

    @staticmethod
    def _qzone_page_can_delete(post: Any, *, viewer_uin: int = 0) -> bool:
        if not viewer_uin:
            return False
        try:
            post_uin = int(getattr(post, "uin", 0) or 0)
        except Exception:
            post_uin = 0
        return bool(post_uin and post_uin == int(viewer_uin))

    def _qzone_page_post_payload(self, post: Any, *, include_comments: bool = False, viewer_uin: int = 0) -> dict[str, Any]:
        payload = {
            "id": self._qzone_page_remember_post(post),
            "tid": self._single_line(getattr(post, "tid", ""), 80),
            "author": {
                "uin": int(getattr(post, "uin", 0) or 0),
                "nickname": self._single_line(getattr(post, "name", ""), 80) or str(getattr(post, "uin", "") or "QQ空间用户"),
            },
            "content": self._multi_line(getattr(post, "text", "") or getattr(post, "rt_con", ""), 1200),
            "created_at": int(getattr(post, "create_time", 0) or 0),
            "created_at_text": self.plugin._format_timestamp_elapsed(getattr(post, "create_time", 0)) if callable(getattr(self.plugin, "_format_timestamp_elapsed", None)) else "",
            "images": list(getattr(post, "images", []) or []),
            "stats": {
                "likes": self._qzone_page_like_count(post),
                "comments": len(getattr(post, "comments", []) or []),
            },
            "liked": self._qzone_page_liked(post),
            "can_delete": self._qzone_page_can_delete(post, viewer_uin=viewer_uin),
        }
        if include_comments:
            payload["comments"] = [
                {
                    "id": self._single_line(getattr(comment, "comment_id", ""), 120),
                    "author": {
                        "uin": int(getattr(comment, "uin", 0) or 0),
                        "nickname": self._single_line(getattr(comment, "name", ""), 80) or str(getattr(comment, "uin", "") or "QQ空间用户"),
                    },
                    "content": self._multi_line(getattr(comment, "content", ""), 300),
                    "created_at": int(getattr(comment, "create_time", 0) or 0),
                }
                for comment in list(getattr(post, "comments", []) or [])
            ]
        return payload
