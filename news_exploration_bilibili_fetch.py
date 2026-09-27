# -*- coding: utf-8 -*-
"""NewsExplorationBilibiliFetchMixin。

由 tools/split_mixin_domain.py 从 news_exploration.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 619 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 NewsExplorationMixin）。
"""
from __future__ import annotations

from .news_exploration_shared import _decode_news_response_text, _news_response_looks_binary, logger
from .news_exploration_shared import Any
from .news_exploration_shared import _now_ts
from .news_exploration_shared import _safe_float
from .news_exploration_shared import _safe_int
from .news_exploration_shared import _single_line
from .news_exploration_shared import _text_looks_garbled
from .news_exploration_shared import datetime
from .news_exploration_shared import html
from .news_exploration_shared import quote
from .news_exploration_shared import re
from .news_exploration_shared import runtime_persona_setting



class NewsExplorationBilibiliFetchMixin:
    """NewsExplorationBilibiliFetchMixin（从 NewsExplorationMixin 拆出）。"""


    async def _fetch_bilibili_video_search_api_fallback(self, source: dict[str, str]) -> list[dict[str, Any]]:
        source_name = _single_line(source.get("name"), 40) or "B站 AI早报"
        mid = re.sub(r"\D+", "", str(source.get("mid") or ""))
        now_dt = datetime.now()
        today = now_dt.strftime("%Y-%m-%d")
        date_tokens = self._ai_daily_date_tokens(now_dt)
        author = _single_line(source.get("author_name"), 60) or source_name
        keywords = [str(token) for token in source.get("keywords") or [] if str(token).strip()] or ["早报", "日报"]
        queries = []
        for keyword in keywords[:4]:
            queries.extend(
                [
                    f"{author} AI {keyword} {today}",
                    f"{author} AI{keyword} {date_tokens[2]}",
                    f"{author} AI{keyword}{date_tokens[-2]}",
                ]
            )
        queries = list(dict.fromkeys(queries))
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=15)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
                "Referer": "https://search.bilibili.com/",
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate",
            }
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                for query in queries:
                    if len(items) >= max(1, runtime_persona_setting(self, "news_max_items_per_source", 5)):
                        break
                    url = f"https://api.bilibili.com/x/web-interface/search/all/v2?keyword={quote(query)}"
                    try:
                        async with session.get(url) as resp:
                            if resp.status >= 400:
                                continue
                            payload = await resp.json(content_type=None)
                    except Exception:
                        continue
                    if not isinstance(payload, dict) or int(payload.get("code") or 0) != 0:
                        continue
                    groups = (payload.get("data") or {}).get("result") if isinstance(payload.get("data"), dict) else []
                    for group in groups if isinstance(groups, list) else []:
                        if not isinstance(group, dict) or group.get("result_type") != "video":
                            continue
                        for raw in group.get("data") if isinstance(group.get("data"), list) else []:
                            if not isinstance(raw, dict):
                                continue
                            if mid and str(raw.get("mid") or "") != mid:
                                continue
                            raw_title = re.sub(r"<[^>]+>", "", html.unescape(str(raw.get("title") or ""))).strip()
                            desc = re.sub(r"<[^>]+>", "", html.unescape(str(raw.get("description") or ""))).strip()
                            haystack = f"{raw_title} {desc}"
                            if "AI" not in haystack.upper() and author not in haystack:
                                continue
                            if not mid and keywords and not any(token in haystack for token in keywords):
                                continue
                            pubdate = _safe_float(raw.get("pubdate"), 0)
                            pubdate_is_today = False
                            if pubdate > 0:
                                try:
                                    pubdate_is_today = datetime.fromtimestamp(pubdate).strftime("%Y-%m-%d") == today
                                except Exception:
                                    pubdate_is_today = False
                            if not pubdate_is_today and not any(token in haystack for token in date_tokens):
                                continue
                            bvid = _single_line(raw.get("bvid") or self._bilibili_bvid_from_url(raw.get("arcurl")), 40)
                            if not bvid or bvid in seen:
                                continue
                            seen.add(bvid)
                            item = await self._bilibili_news_item_from_video(
                                source_name=source_name,
                                title=raw_title,
                                desc=desc,
                                bvid=bvid,
                                created=raw.get("pubdate"),
                            )
                            if item:
                                item["bilibili_integration_source"] = "search_api"
                                items.append(item)
                            if len(items) >= max(1, runtime_persona_setting(self, "news_max_items_per_source", 5)):
                                break
                        if len(items) >= max(1, runtime_persona_setting(self, "news_max_items_per_source", 5)):
                            break
        except Exception as exc:
            logger.debug("B站搜索 API 兜底失败: %s", exc)
        items.sort(key=lambda item: (_safe_float(item.get("published_ts"), 0), _safe_float(item.get("fetched_ts"), 0)), reverse=True)
        return items[: max(1, runtime_persona_setting(self, "news_max_items_per_source", 5))]

    async def _fetch_bilibili_news_search_fallback(self, source: dict[str, str]) -> list[dict[str, Any]]:
        source_name = _single_line(source.get("name"), 40) or "B站 AI早报"
        mid = re.sub(r"\D+", "", str(source.get("mid") or ""))
        now_dt = datetime.now()
        today = now_dt.strftime("%Y-%m-%d")
        date_tokens = self._ai_daily_date_tokens(now_dt)
        author = _single_line(source.get("author_name"), 60) or source_name
        keywords = [str(token) for token in source.get("keywords") or [] if str(token).strip()] or ["早报", "日报"]
        queries: list[str] = []
        for keyword in keywords[:4]:
            compact_keyword = f"AI{keyword}"
            queries.extend(
                [
                    f"AI {keyword} {today}",
                    f"{compact_keyword} {today}",
                    f"AI {keyword} {date_tokens[2]}",
                    f"{compact_keyword} {date_tokens[2]}",
                    f"site:mp.weixin.qq.com/s AI {keyword} {today}",
                    f"site:mp.weixin.qq.com/s {compact_keyword} {today}",
                    f"site:bilibili.com/video AI {keyword} {today}",
                    f"site:bilibili.com/video {compact_keyword} {today}",
                ]
            )
        queries = list(dict.fromkeys(queries))
        api_items = await self._fetch_bilibili_video_search_api_fallback(source)
        if api_items:
            return api_items
        umo = self._pick_available_web_search_umo()
        if not umo and not self._astrbot_web_search_available():
            return []
        items: list[dict[str, Any]] = []
        seen_links: set[str] = set()
        for query in queries:
            if len(items) >= max(1, runtime_persona_setting(self, "news_max_items_per_source", 5)):
                break
            results = await self._run_astrbot_web_search(query, umo=umo, topic="news")
            for result in results[: max(2, runtime_persona_setting(self, "news_max_items_per_source", 5))]:
                title = _single_line(result.get("title"), 160)
                link = _single_line(result.get("url"), 400)
                snippet = _single_line(result.get("snippet"), 360)
                haystack = f"{title} {snippet}"
                if not title or ("AI" not in haystack.upper() and author not in haystack):
                    continue
                if not mid and keywords and not any(token in haystack for token in keywords):
                    continue
                article_match = re.search(r"https?://mp\.weixin\.qq\.com/s/[^\s<>\]）)\"']+", f"{link} {snippet}")
                article_link = _single_line(article_match.group(0), 400) if article_match else ""
                if "mp.weixin.qq.com/s/" in link:
                    article_link = link
                if not article_link and "space.bilibili.com" in link:
                    continue
                video_bvid = self._bilibili_bvid_from_url(link)
                if not article_link and video_bvid:
                    item = await self._bilibili_news_item_from_video(
                        source_name=source_name,
                        title=title,
                        desc=snippet,
                        bvid=video_bvid,
                    )
                    if item:
                        item["bilibili_integration_source"] = "web_search_video"
                        if str(item.get("link") or "") not in seen_links:
                            seen_links.add(str(item.get("link") or ""))
                            items.append(item)
                    if len(items) >= max(1, runtime_persona_setting(self, "news_max_items_per_source", 5)):
                        break
                    continue
                final_link = article_link or link
                if final_link in seen_links:
                    continue
                seen_links.add(final_link)
                article_payload = await self._fetch_news_article_excerpt(article_link) if article_link else {}
                article_text = str(article_payload.get("text") or "").strip()
                article_excerpt = str(article_payload.get("excerpt") or "").strip()
                article_title = _single_line(article_payload.get("title"), 120)
                summary = article_excerpt if article_excerpt else snippet
                media_type = "bilibili_search_article" if article_text else "bilibili_search_result"
                published_hint = today if any(token in haystack for token in date_tokens) else ""
                item = {
                    "source": source_name,
                    "title": article_title or title,
                    "link": final_link,
                    "summary": summary,
                    "published": published_hint,
                    "published_ts": 0,
                    "fetched_ts": _now_ts(),
                    "media_type": media_type,
                    "search_query": query,
                    "article_link": article_link,
                    "article_title": article_title,
                    "article_text": article_text,
                    "article_excerpt": article_excerpt,
                    "article_readable": bool(article_text),
                }
                item["key"] = self._news_item_key(item)
                if item["key"]:
                    items.append(item)
                if len(items) >= max(1, runtime_persona_setting(self, "news_max_items_per_source", 5)):
                    break
        if items:
            items.sort(
                key=lambda item: (
                    1 if str(item.get("published") or "") == today else 0,
                    1 if item.get("article_readable") else 0,
                    _safe_float(item.get("fetched_ts"), 0),
                ),
                reverse=True,
            )
            return items[: max(1, runtime_persona_setting(self, "news_max_items_per_source", 5))]
        query = queries[0]
        results = await self._run_astrbot_web_search(query, umo=umo, topic="news")
        for result in results[: max(1, runtime_persona_setting(self, "news_max_items_per_source", 5))]:
            title = _single_line(result.get("title"), 160)
            link = _single_line(result.get("url"), 400)
            snippet = _single_line(result.get("snippet"), 320)
            haystack = f"{title} {snippet}"
            if not title or ("AI" not in haystack.upper() and author not in haystack):
                continue
            if keywords and not any(token in haystack for token in keywords):
                continue
            item = {
                "source": source_name,
                "title": title,
                "link": link,
                "summary": snippet,
                "published": "",
                "published_ts": 0,
                "fetched_ts": _now_ts(),
                "media_type": "bilibili_search_result",
                "search_query": query,
            }
            item["key"] = self._news_item_key(item)
            if item["key"]:
                items.append(item)
        return items

    async def _fetch_news_article_excerpt(self, url: str) -> dict[str, str]:
        safe_url = _single_line(url, 500)
        if not safe_url.startswith(("http://", "https://")):
            return {}
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=15)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://mp.weixin.qq.com/",
            }
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(safe_url, allow_redirects=True) as resp:
                    if resp.status >= 400:
                        return {}
                    raw_bytes = await resp.read()
                    content_type = resp.headers.get("Content-Type", "")
                    if _news_response_looks_binary(raw_bytes, content_type=content_type):
                        logger.debug(
                            "新闻文字版跳过非文本响应 %s content-type=%s",
                            _single_line(safe_url, 120),
                            _single_line(content_type, 80),
                        )
                        return {}
                    raw = _decode_news_response_text(
                        raw_bytes,
                        content_type=content_type,
                        declared_charset=resp.charset or "",
                    )
                    if not raw.strip():
                        return {}
        except Exception as exc:
            logger.debug("新闻文字版抓取失败 %s: %s", _single_line(safe_url, 120), exc)
            return {}

        text = html.unescape(raw or "")
        title = ""
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
        if title_match:
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", title_match.group(1))).strip()
        for pattern in (
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)["\']',
        ):
            match = re.search(pattern, text, flags=re.I | re.S)
            if match:
                title = match.group(1).strip()
                break
        if _text_looks_garbled(title):
            title = ""

        body = text
        article_match = re.search(r'<div[^>]+id=["\']js_content["\'][^>]*>(.*?)</div>\s*</div>\s*</div>', text, flags=re.I | re.S)
        if not article_match:
            article_match = re.search(r'<article[^>]*>(.*?)</article>', text, flags=re.I | re.S)
        if article_match:
            body = article_match.group(1)
        body = re.sub(r"<script\b[^>]*>.*?</script>", " ", body, flags=re.I | re.S)
        body = re.sub(r"<style\b[^>]*>.*?</style>", " ", body, flags=re.I | re.S)
        body = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", body, flags=re.I)
        body = re.sub(r"<[^>]+>", " ", body)
        body = html.unescape(body)
        body = re.sub(r"\u00a0", " ", body)
        lines = [re.sub(r"\s+", " ", line).strip() for line in body.splitlines()]
        lines = [
            line for line in lines
            if len(line) >= 8
            and not re.search(r"^(赞|在看|分享|微信扫一扫|继续滑动看下一个|向上滑动看下一个|广告)$", line)
        ]
        article_text = "\n".join(lines)
        article_text = re.sub(r"\n{3,}", "\n\n", article_text).strip()
        if len(article_text) < 80 or _text_looks_garbled(article_text[:600]):
            return {}
        return {
            "title": _single_line(title, 120),
            "text": article_text,
            "excerpt": article_text[:2400],
        }

    async def _fetch_bilibili_video_subtitle_text(self, bvid: str, cid: Any = 0) -> dict[str, Any]:
        safe_bvid = _single_line(bvid, 40)
        if not safe_bvid:
            return {}
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=15)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
                "Referer": f"https://www.bilibili.com/video/{safe_bvid}",
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate",
            }
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                safe_cid = _safe_int(cid, 0, 0)
                if safe_cid <= 0:
                    async with session.get(f"https://api.bilibili.com/x/web-interface/view?bvid={safe_bvid}") as resp:
                        if resp.status < 400:
                            payload = await resp.json(content_type=None)
                            data = payload.get("data") if isinstance(payload, dict) else {}
                            if isinstance(data, dict):
                                safe_cid = _safe_int(data.get("cid"), 0, 0)
                if safe_cid <= 0:
                    return {}
                async with session.get(f"https://api.bilibili.com/x/player/v2?bvid={safe_bvid}&cid={safe_cid}") as resp:
                    if resp.status >= 400:
                        return {}
                    payload = await resp.json(content_type=None)
                data = payload.get("data") if isinstance(payload, dict) else {}
                subtitle = data.get("subtitle") if isinstance(data, dict) and isinstance(data.get("subtitle"), dict) else {}
                subtitles = subtitle.get("subtitles") if isinstance(subtitle.get("subtitles"), list) else []
                if not subtitles:
                    return {"cid": safe_cid, "available": False, "count": 0}
                subtitle_item = next((item for item in subtitles if isinstance(item, dict) and str(item.get("lan") or "").lower().startswith("zh")), None)
                if not subtitle_item:
                    subtitle_item = next((item for item in subtitles if isinstance(item, dict)), None)
                if not isinstance(subtitle_item, dict):
                    return {"cid": safe_cid, "available": False, "count": len(subtitles)}
                subtitle_url = _single_line(subtitle_item.get("subtitle_url") or subtitle_item.get("url"), 500)
                if subtitle_url.startswith("//"):
                    subtitle_url = f"https:{subtitle_url}"
                if not subtitle_url.startswith(("http://", "https://")):
                    return {"cid": safe_cid, "available": False, "count": len(subtitles)}
                async with session.get(subtitle_url) as resp:
                    if resp.status >= 400:
                        return {"cid": safe_cid, "available": False, "count": len(subtitles), "subtitle_url": subtitle_url}
                    subtitle_payload = await resp.json(content_type=None)
        except Exception as exc:
            logger.debug("B站字幕读取失败 bvid=%s: %s", safe_bvid, exc)
            return {}

        body = subtitle_payload.get("body") if isinstance(subtitle_payload, dict) and isinstance(subtitle_payload.get("body"), list) else []
        lines = []
        for segment in body:
            if not isinstance(segment, dict):
                continue
            text = re.sub(r"\s+", " ", str(segment.get("content") or "")).strip()
            if text:
                lines.append(text)
        subtitle_text = "\n".join(lines).strip()
        subtitle_text = re.sub(r"\n{3,}", "\n\n", subtitle_text)
        if len(subtitle_text) < 80:
            return {"cid": safe_cid, "available": bool(subtitles), "count": len(subtitles), "subtitle_url": subtitle_url, "chars": len(subtitle_text)}
        return {
            "cid": safe_cid,
            "available": True,
            "count": len(subtitles),
            "subtitle_url": subtitle_url,
            "language": _single_line(subtitle_item.get("lan_doc") or subtitle_item.get("lan"), 40),
            "text": subtitle_text,
            "chars": len(subtitle_text),
        }

    @staticmethod
    def _format_bilibili_duration(seconds: Any) -> str:
        total = _safe_int(seconds, 0, 0)
        if total <= 0:
            return ""
        hours = total // 3600
        minutes = (total % 3600) // 60
        secs = total % 60
        if hours:
            return f"{hours}小时{minutes}分{secs}秒"
        return f"{minutes}分{secs}秒"

    async def _fetch_bilibili_video_public_context(self, bvid: str, cid: Any = 0) -> dict[str, Any]:
        """Lightweight video context inspired by BiliBot: metadata, tags, hot comments."""
        safe_bvid = _single_line(bvid, 40)
        if not safe_bvid:
            return {}

        context: dict[str, Any] = {"bvid": safe_bvid}
        for obj in (
            self._find_bilibili_runtime_objects()
            if runtime_persona_setting(self, "enable_bilibili_integration", False)
            else []
        ):
            try:
                oid = 0
                get_oid = getattr(obj, "_get_video_oid", None)
                if callable(get_oid):
                    payload = get_oid(safe_bvid)
                    if hasattr(payload, "__await__"):
                        payload = await payload
                    oid = _safe_int(payload, 0, 0)
                get_info = getattr(obj, "_get_video_info", None)
                if callable(get_info) and oid > 0:
                    payload = get_info(oid)
                    if hasattr(payload, "__await__"):
                        payload = await payload
                    if isinstance(payload, dict):
                        context.update(self._normalize_bilibili_video_payload({"data": payload}, safe_bvid))
                        context["aid"] = oid
                get_tags = getattr(obj, "_get_video_tags", None)
                if callable(get_tags):
                    payload = get_tags(safe_bvid)
                    if hasattr(payload, "__await__"):
                        payload = await payload
                    if isinstance(payload, list):
                        context["tags"] = [_single_line(tag, 40) for tag in payload if _single_line(tag, 40)][:10]
                get_comments = getattr(obj, "_get_hot_comments", None)
                if callable(get_comments) and oid > 0:
                    payload = get_comments(oid, limit=6)
                    if hasattr(payload, "__await__"):
                        payload = await payload
                    if isinstance(payload, list):
                        context["hot_comments"] = [_single_line(comment, 120) for comment in payload if _single_line(comment, 120)][:6]
                if context.get("title") or context.get("tags") or context.get("hot_comments"):
                    break
            except Exception as exc:
                logger.debug("BiliBot 视频上下文联动失败 bvid=%s: %s", safe_bvid, exc)

        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=15)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
                "Referer": f"https://www.bilibili.com/video/{safe_bvid}",
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate",
            }
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                if not context.get("title") or not context.get("aid") or not context.get("cid"):
                    async with session.get("https://api.bilibili.com/x/web-interface/view", params={"bvid": safe_bvid}) as resp:
                        if resp.status < 400:
                            payload = await resp.json(content_type=None)
                            if isinstance(payload, dict) and int(payload.get("code") or 0) == 0:
                                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                                context.update(self._normalize_bilibili_video_payload(payload, safe_bvid))
                                context["aid"] = _safe_int(data.get("aid"), _safe_int(context.get("aid"), 0, 0), 0)
                                context["duration"] = _safe_int(data.get("duration"), _safe_int(context.get("duration"), 0, 0), 0)
                                stat = data.get("stat") if isinstance(data.get("stat"), dict) else {}
                                context["stat"] = {
                                    "view": _safe_int(stat.get("view"), 0, 0),
                                    "danmaku": _safe_int(stat.get("danmaku"), 0, 0),
                                    "reply": _safe_int(stat.get("reply"), 0, 0),
                                    "like": _safe_int(stat.get("like"), 0, 0),
                                }
                if not context.get("tags"):
                    async with session.get("https://api.bilibili.com/x/tag/archive/tags", params={"bvid": safe_bvid}) as resp:
                        if resp.status < 400:
                            payload = await resp.json(content_type=None)
                            if isinstance(payload, dict) and int(payload.get("code") or 0) == 0:
                                tags = payload.get("data") if isinstance(payload.get("data"), list) else []
                                context["tags"] = [
                                    _single_line(tag.get("tag_name"), 40)
                                    for tag in tags
                                    if isinstance(tag, dict) and _single_line(tag.get("tag_name"), 40)
                                ][:10]
                aid = _safe_int(context.get("aid"), 0, 0)
                if aid > 0 and not context.get("hot_comments"):
                    async with session.get(
                        "https://api.bilibili.com/x/v2/reply/main",
                        params={"oid": aid, "type": 1, "mode": 3, "ps": 6},
                    ) as resp:
                        if resp.status < 400:
                            payload = await resp.json(content_type=None)
                            if isinstance(payload, dict) and int(payload.get("code") or 0) == 0:
                                replies = ((payload.get("data") or {}) if isinstance(payload.get("data"), dict) else {}).get("replies")
                                if isinstance(replies, list):
                                    comments: list[str] = []
                                    for reply in replies:
                                        if not isinstance(reply, dict):
                                            continue
                                        content = reply.get("content") if isinstance(reply.get("content"), dict) else {}
                                        message = _single_line(content.get("message"), 120)
                                        if message:
                                            comments.append(message)
                                    context["hot_comments"] = comments[:6]
        except Exception as exc:
            logger.debug("B站视频公开上下文抓取失败 bvid=%s: %s", safe_bvid, exc)

        parts: list[str] = []
        owner = _single_line(context.get("owner_name"), 60)
        tname = _single_line(context.get("tname"), 40)
        duration = self._format_bilibili_duration(context.get("duration"))
        if owner:
            parts.append(f"UP主：{owner}")
        if tname:
            parts.append(f"分区：{tname}")
        if duration:
            parts.append(f"时长：{duration}")
        desc = _single_line(context.get("desc"), 360)
        if desc:
            parts.append(f"简介：{desc}")
        tags = context.get("tags") if isinstance(context.get("tags"), list) else []
        tags = [_single_line(tag, 40) for tag in tags if _single_line(tag, 40)]
        if tags:
            parts.append("标签：" + "、".join(tags[:10]))
        comments = context.get("hot_comments") if isinstance(context.get("hot_comments"), list) else []
        comments = [_single_line(comment, 120) for comment in comments if _single_line(comment, 120)]
        if comments:
            parts.append("热门评论：" + " / ".join(comments[:5]))
        context["context_text"] = "\n".join(parts).strip()
        context["context_chars"] = len(context["context_text"])
        return context

    async def _bilibili_news_item_from_video(
        self,
        *,
        source_name: str,
        title: Any,
        desc: Any,
        bvid: Any,
        created: Any = 0,
        cid: Any = 0,
    ) -> dict[str, Any]:
        safe_title = _single_line(title, 160)
        safe_bvid = _single_line(bvid, 40)
        if not safe_title or not safe_bvid:
            return {}
        desc_text = str(desc or "")
        article_match = re.search(r"https?://[^\s<>\]）)\"']+", desc_text)
        article_link = _single_line(article_match.group(0), 400) if article_match else ""
        video_link = f"https://www.bilibili.com/video/{safe_bvid}"
        article_payload = await self._fetch_news_article_excerpt(article_link) if article_link else {}
        article_text = str(article_payload.get("text") or "").strip()
        article_excerpt = str(article_payload.get("excerpt") or "").strip()
        article_title = _single_line(article_payload.get("title"), 120)
        subtitle_payload = {} if article_text else await self._fetch_bilibili_video_subtitle_text(safe_bvid, cid)
        subtitle_text = str(subtitle_payload.get("text") or "").strip()
        subtitle_excerpt = subtitle_text[:2400]
        video_context = await self._fetch_bilibili_video_public_context(
            safe_bvid,
            cid or subtitle_payload.get("cid"),
        )
        context_text = str(video_context.get("context_text") or "").strip()
        context_excerpt = context_text[:1600]
        published_ts = _safe_float(created, 0)
        if published_ts <= 0:
            published_ts = _safe_float(video_context.get("created"), 0)
        normalized_desc = desc_text or str(video_context.get("desc") or "")
        summary = article_excerpt or subtitle_excerpt or _single_line(normalized_desc, 320) or _single_line(context_text, 320)
        item = {
            "source": source_name,
            "title": article_title or _single_line(video_context.get("title"), 160) or safe_title,
            "link": article_link or video_link,
            "summary": summary,
            "published": datetime.fromtimestamp(published_ts).isoformat() if published_ts else "",
            "published_ts": published_ts,
            "fetched_ts": _now_ts(),
            "media_type": "bilibili_video",
            "video_link": video_link,
            "video_cid": _safe_int(cid, 0, 0) or _safe_int(subtitle_payload.get("cid"), 0, 0),
            "video_aid": _safe_int(video_context.get("aid"), 0, 0),
            "video_owner_name": _single_line(video_context.get("owner_name"), 80),
            "video_owner_mid": _single_line(video_context.get("owner_mid"), 40),
            "video_tname": _single_line(video_context.get("tname"), 60),
            "video_duration": _safe_int(video_context.get("duration"), 0, 0),
            "video_pic": _single_line(video_context.get("pic"), 300),
            "video_tags": video_context.get("tags") if isinstance(video_context.get("tags"), list) else [],
            "video_hot_comments": video_context.get("hot_comments") if isinstance(video_context.get("hot_comments"), list) else [],
            "video_context_text": context_text,
            "video_context_excerpt": context_excerpt,
            "video_context_chars": len(context_text),
            "video_subtitle_text": subtitle_text,
            "video_subtitle_excerpt": subtitle_excerpt,
            "video_subtitle_readable": bool(subtitle_text),
            "video_subtitle_chars": len(subtitle_text),
            "video_subtitle_status": "read" if subtitle_text else ("missing" if subtitle_payload else "unavailable"),
            "article_link": article_link,
            "article_title": article_title,
            "article_text": article_text,
            "article_excerpt": article_excerpt,
            "article_readable": bool(article_text),
        }
        item["key"] = self._news_item_key(item)
        return item if item["key"] else {}

    @staticmethod
    def _normalize_bilibili_video_payload(payload: Any, bvid: str = "") -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        data: Any = payload
        if isinstance(payload.get("info"), dict):
            data = payload.get("info")
        elif isinstance(payload.get("data"), dict):
            data = payload.get("data")
        if not isinstance(data, dict):
            return {}
        owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
        return {
            "bvid": _single_line(data.get("bvid") or bvid, 40),
            "aid": _safe_int(data.get("aid"), 0, 0),
            "title": _single_line(data.get("title"), 180),
            "desc": str(data.get("desc") or data.get("dynamic") or ""),
            "created": data.get("pubdate") or data.get("ctime") or data.get("created") or 0,
            "owner_name": _single_line(owner.get("name") or data.get("owner_name") or data.get("up_name"), 80),
            "owner_mid": _single_line(owner.get("mid") or data.get("owner_mid") or data.get("mid"), 40),
            "tname": _single_line(data.get("tname"), 60),
            "duration": _safe_int(data.get("duration"), 0, 0),
            "pic": _single_line(data.get("pic"), 300),
            "cid": data.get("cid") or 0,
        }
