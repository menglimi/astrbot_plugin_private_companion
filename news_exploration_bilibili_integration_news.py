# -*- coding: utf-8 -*-
"""NewsExplorationBilibiliIntegrationNewsMixin。

由 tools/split_mixin_domain.py 从 news_exploration.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 410 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 NewsExplorationMixin）。
"""
from __future__ import annotations

from .news_exploration_shared import logger
from .news_exploration_shared import Any
from .news_exploration_shared import ET
from .news_exploration_shared import PLUGIN_NAME
from .news_exploration_shared import _now_ts
from .news_exploration_shared import _safe_float
from .news_exploration_shared import _safe_int
from .news_exploration_shared import _single_line
from .news_exploration_shared import asyncio
from .news_exploration_shared import datetime
from .news_exploration_shared import re
from .news_exploration_shared import runtime_persona_setting



class NewsExplorationBilibiliIntegrationNewsMixin:
    """NewsExplorationBilibiliIntegrationNewsMixin（从 NewsExplorationMixin 拆出）。"""


    async def _fetch_bilibili_video_info_via_integration(self, bvid: str) -> dict[str, Any]:
        safe_bvid = _single_line(bvid, 40)
        if not safe_bvid or not runtime_persona_setting(self, "enable_bilibili_integration", True):
            return {}
        for obj in self._find_bilibili_runtime_objects():
            try:
                client = getattr(obj, "bili_client", None)
                getter = getattr(client, "get_video_info", None) if client is not None else None
                if not callable(getter):
                    getter = getattr(obj, "get_video_info", None)
                if callable(getter):
                    payload = getter(safe_bvid)
                    if hasattr(payload, "__await__"):
                        payload = await payload
                    normalized = self._normalize_bilibili_video_payload(payload, safe_bvid)
                    if normalized.get("title"):
                        logger.info("已通过 B站插件客户端读取视频信息: %s", safe_bvid)
                        return normalized
                http_get = getattr(obj, "_http_get", None)
                if callable(http_get):
                    payload = http_get(
                        "https://api.bilibili.com/x/web-interface/view",
                        params={"bvid": safe_bvid},
                    )
                    if hasattr(payload, "__await__"):
                        payload = await payload
                    data = payload[0] if isinstance(payload, (list, tuple)) and payload else payload
                    if isinstance(data, dict) and int(data.get("code") or 0) == 0:
                        normalized = self._normalize_bilibili_video_payload(data, safe_bvid)
                        if normalized.get("title"):
                            logger.info("已通过 BiliBot HTTP 客户端读取视频信息: %s", safe_bvid)
                            return normalized
            except Exception as exc:
                logger.debug("B站插件视频信息联动失败 bvid=%s: %s", safe_bvid, exc)
        return {}

    async def _fetch_bilibili_space_payloads_via_integration(self, mid: str) -> list[dict[str, Any]]:
        safe_mid = re.sub(r"\D+", "", str(mid or ""))
        if not safe_mid or not runtime_persona_setting(self, "enable_bilibili_integration", True):
            return []
        payloads: list[dict[str, Any]] = []
        for obj in self._find_bilibili_runtime_objects():
            try:
                client = getattr(obj, "bili_client", None)
                getter = getattr(client, "get_latest_dynamics", None) if client is not None else None
                if not callable(getter):
                    getter = getattr(obj, "get_latest_dynamics", None)
                if callable(getter):
                    payload = getter(int(safe_mid))
                    if hasattr(payload, "__await__"):
                        payload = await payload
                    if isinstance(payload, dict):
                        payloads.append(payload)
                        logger.info("已通过 B站插件客户端读取 UP 最新动态: mid=%s", safe_mid)
                        continue
                http_get = getattr(obj, "_http_get", None)
                if callable(http_get):
                    payload = http_get(
                        "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
                        params={
                            "host_mid": safe_mid,
                            "offset": "",
                            "timezone_offset": -480,
                            "features": "itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote",
                        },
                    )
                    if hasattr(payload, "__await__"):
                        payload = await payload
                    data = payload[0] if isinstance(payload, (list, tuple)) and payload else payload
                    if isinstance(data, dict) and int(data.get("code") or 0) == 0:
                        payloads.append(data)
                        logger.info("已通过 BiliBot HTTP 客户端读取 UP 最新动态: mid=%s", safe_mid)
                        continue
            except Exception as exc:
                logger.debug("B站插件 UP 动态联动失败 mid=%s: %s", safe_mid, exc)
        return payloads

    async def _fetch_bilibili_video_news_source(self, source: dict[str, str]) -> list[dict[str, Any]]:
        bvid = _single_line(source.get("bvid"), 40)
        if not bvid:
            return []
        source_name = _single_line(source.get("name"), 40) or f"B站视频 {bvid}"
        integrated_info = await self._fetch_bilibili_video_info_via_integration(bvid)
        if integrated_info:
            item = await self._bilibili_news_item_from_video(
                source_name=source_name,
                title=integrated_info.get("title"),
                desc=integrated_info.get("desc"),
                bvid=integrated_info.get("bvid") or bvid,
                created=integrated_info.get("created"),
                cid=integrated_info.get("cid"),
            )
            if item:
                item["bilibili_integration_source"] = "plugin_client"
                return [item]
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=12)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
                "Referer": f"https://www.bilibili.com/video/{bvid}",
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate",
            }
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}") as resp:
                    if resp.status >= 400:
                        return await self._fetch_bilibili_news_search_fallback(source)
                    payload = await resp.json(content_type=None)
        except Exception as exc:
            logger.debug("B站单视频新闻源抓取失败 bvid=%s: %s", bvid, exc)
            return await self._fetch_bilibili_news_search_fallback(source)
        data = payload.get("data") if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            return await self._fetch_bilibili_news_search_fallback(source)
        item = await self._bilibili_news_item_from_video(
            source_name=source_name,
            title=data.get("title"),
            desc=data.get("desc") or data.get("dynamic"),
            bvid=data.get("bvid") or bvid,
            created=data.get("pubdate") or data.get("ctime"),
            cid=data.get("cid"),
        )
        return [item] if item else await self._fetch_bilibili_news_search_fallback(source)

    async def _probe_bilibili_arc_search(self, mid: str, *, limit: int = 10) -> dict[str, Any]:
        safe_mid = re.sub(r"\D+", "", str(mid or ""))
        if not safe_mid:
            return {"ok": False, "error": "empty_mid"}
        url = (
            "https://api.bilibili.com/x/space/arc/search"
            f"?mid={safe_mid}&pn=1&ps={max(1, min(30, limit))}&order=pubdate&jsonp=jsonp"
        )
        probe: dict[str, Any] = {"ok": False, "url": url, "mid": safe_mid}
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=12)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
                "Referer": f"https://space.bilibili.com/{safe_mid}",
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate",
            }
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url) as resp:
                    probe["http_status"] = resp.status
                    payload = await resp.json(content_type=None)
        except Exception as exc:
            probe["error"] = _single_line(str(exc), 160)
            return probe
        if not isinstance(payload, dict):
            probe["error"] = "non_json_payload"
            return probe
        probe["code"] = payload.get("code")
        probe["message"] = _single_line(payload.get("message"), 80)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        vlist = (((data.get("list") or {}) if isinstance(data.get("list"), dict) else {}).get("vlist") or [])
        if not isinstance(vlist, list):
            vlist = []
        probe["vlist_count"] = len(vlist)
        if vlist and isinstance(vlist[0], dict):
            first = vlist[0]
            created = _safe_float(first.get("created"), 0)
            probe["first_title"] = _single_line(first.get("title"), 140)
            probe["first_bvid"] = _single_line(first.get("bvid"), 40)
            probe["first_created_ts"] = created
            probe["first_created"] = datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M:%S") if created > 0 else ""
        probe["ok"] = int(payload.get("code") or 0) == 0 and bool(vlist)
        return probe

    async def _fetch_bilibili_news_source(self, source: dict[str, str], *, limit: int | None = None) -> list[dict[str, Any]]:
        mid = re.sub(r"\D+", "", str(source.get("mid") or ""))
        if not mid:
            return []
        configured_limit = runtime_persona_setting(self, "news_max_items_per_source", 5)
        item_limit = max(
            1,
            _safe_int(limit if limit is not None else configured_limit, configured_limit, 1),
        )
        payloads: list[dict[str, Any]] = await self._fetch_bilibili_space_payloads_via_integration(mid)
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=12)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
                "Referer": f"https://space.bilibili.com/{mid}",
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate",
            }
            api_urls = [
                f"https://api.bilibili.com/x/space/arc/search?mid={mid}&pn=1&ps={max(1, min(30, item_limit))}&order=pubdate&jsonp=jsonp",
                f"https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?host_mid={mid}&timezone_offset=-480&features=itemOpusStyle",
            ]
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                for url in api_urls:
                    try:
                        async with session.get(url) as resp:
                            if resp.status >= 400:
                                continue
                            data = await resp.json(content_type=None)
                            if isinstance(data, dict) and int(data.get("code") or 0) == 0:
                                payloads.append(data)
                    except Exception:
                        continue
        except Exception as exc:
            logger.debug("B站新闻源抓取失败 mid=%s: %s", mid, exc)
            if not payloads:
                return []

        source_name = _single_line(source.get("name"), 40) or f"B站 UP {mid}"
        results: list[dict[str, Any]] = []

        async def add_video(title: Any, desc: Any, bvid: Any, created: Any) -> None:
            item = await self._bilibili_news_item_from_video(
                source_name=source_name,
                title=title,
                desc=desc,
                bvid=bvid,
                created=created,
            )
            if item:
                results.append(item)

        for payload in payloads:
            data = payload.get("data") if isinstance(payload, dict) else {}
            if not isinstance(data, dict):
                continue
            vlist = (((data.get("list") or {}) if isinstance(data.get("list"), dict) else {}).get("vlist") or [])
            if isinstance(vlist, list):
                for video in vlist:
                    if not isinstance(video, dict):
                        continue
                    await add_video(video.get("title"), video.get("description") or video.get("desc"), video.get("bvid"), video.get("created"))
            dynamic_items = data.get("items") if isinstance(data.get("items"), list) else []
            for dynamic in dynamic_items:
                if not isinstance(dynamic, dict):
                    continue
                modules = dynamic.get("modules") if isinstance(dynamic.get("modules"), dict) else {}
                major = ((modules.get("module_dynamic") or {}) if isinstance(modules.get("module_dynamic"), dict) else {}).get("major")
                archive = (major or {}).get("archive") if isinstance(major, dict) else {}
                if not isinstance(archive, dict):
                    continue
                await add_video(
                    archive.get("title"),
                    archive.get("desc") or archive.get("cover"),
                    archive.get("bvid"),
                    archive.get("pub_ts") or dynamic.get("pub_ts"),
                )
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for item in results:
            key = str(item.get("key") or "")
            if key and key not in seen:
                seen.add(key)
                unique.append(item)
        unique.sort(key=lambda item: (_safe_float(item.get("published_ts"), 0), _safe_float(item.get("fetched_ts"), 0)), reverse=True)
        if unique:
            return unique[:item_limit]
        return await self._fetch_bilibili_news_search_fallback(source)

    async def _fetch_news_source(self, source: dict[str, str]) -> list[dict[str, Any]]:
        source_type = str(source.get("type") or "").lower()
        if source_type == "bilibili_video":
            return await self._fetch_bilibili_video_news_source(source)
        if source_type == "bilibili":
            return await self._fetch_bilibili_news_source(source)
        url = str(source.get("url") or "")
        if not url:
            return []
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=12)
            headers = {"User-Agent": f"{PLUGIN_NAME}/news-reader", "Accept-Encoding": "gzip, deflate"}
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url) as resp:
                    if resp.status >= 400:
                        return []
                    raw = await resp.text(errors="ignore")
        except Exception as exc:
            logger.debug("新闻源抓取失败 %s: %s", _single_line(url, 120), exc)
            return []
        try:
            root = ET.fromstring(raw.encode("utf-8", errors="ignore"))
        except Exception as exc:
            logger.debug("新闻源 XML 解析失败 %s: %s", _single_line(url, 120), exc)
            return []

        source_name = _single_line(source.get("name"), 40) or "新闻源"
        nodes = list(root.findall(".//item"))
        if not nodes:
            nodes = list(root.findall(".//{http://www.w3.org/2005/Atom}entry"))
        results: list[dict[str, Any]] = []
        for node in nodes[: max(1, runtime_persona_setting(self, "news_max_items_per_source", 5))]:
            title = self._news_xml_text(node, "title", "{http://www.w3.org/2005/Atom}title")
            link = self._news_xml_text(node, "link")
            if not link:
                atom_link = node.find("{http://www.w3.org/2005/Atom}link")
                if atom_link is not None:
                    link = str(atom_link.attrib.get("href") or "").strip()
            summary = self._news_xml_text(
                node,
                "description",
                "summary",
                "{http://www.w3.org/2005/Atom}summary",
                "{http://www.w3.org/2005/Atom}content",
            )
            published = self._news_xml_text(
                node,
                "pubDate",
                "published",
                "updated",
                "{http://www.w3.org/2005/Atom}published",
                "{http://www.w3.org/2005/Atom}updated",
            )
            if not title:
                continue
            item = {
                "source": source_name,
                "title": _single_line(title, 160),
                "link": _single_line(link, 400),
                "summary": _single_line(summary, 320),
                "published": _single_line(published, 80),
                "published_ts": self._news_parse_time(published),
                "fetched_ts": _now_ts(),
            }
            item["key"] = self._news_item_key(item)
            if item["key"]:
                results.append(item)
        return results

    async def _fetch_news_candidates(self) -> list[dict[str, Any]]:
        sources = self._news_source_items()
        if not sources:
            return []
        batches = await asyncio.gather(*(self._fetch_news_source(source) for source in sources), return_exceptions=True)
        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        for batch in batches:
            if not isinstance(batch, list):
                continue
            for item in batch:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or "")
                if not key or key in seen:
                    continue
                seen.add(key)
                items.append(item)
        items.sort(key=lambda item: (_safe_float(item.get("published_ts"), 0), _safe_float(item.get("fetched_ts"), 0)), reverse=True)
        return items[:24]

    async def _fetch_news_reading_candidates(self) -> list[dict[str, Any]]:
        batches = await asyncio.gather(
            self._fetch_news_candidates(),
            self._fetch_hot_trend_candidates(),
            return_exceptions=True,
        )
        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        for batch in batches:
            if not isinstance(batch, list):
                continue
            for item in batch:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or self._news_item_key(item) or self._hot_trend_key(item))
                if not key or key in seen:
                    continue
                seen.add(key)
                normalized = dict(item)
                normalized["key"] = key
                if not normalized.get("summary") and normalized.get("snippet"):
                    normalized["summary"] = normalized.get("snippet")
                if not normalized.get("link") and normalized.get("url"):
                    normalized["link"] = normalized.get("url")
                normalized.setdefault("fetched_ts", _now_ts())
                items.append(normalized)
        items.sort(
            key=lambda item: (
                _safe_float(item.get("published_ts"), 0) or _safe_float(item.get("fetched_ts"), 0),
                _safe_float(item.get("score"), 0),
            ),
            reverse=True,
        )
        return items[:32]

    def _news_fallback_digest(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        item = items[0] if items else {}
        title = _single_line(item.get("title"), 100)
        summary = _single_line(item.get("summary"), 180)
        source = _single_line(item.get("source"), 40)
        impression = summary
        if item.get("video_context_text") and (not impression or len(impression) < 16 or impression in {"早早早", "早", "无"}):
            impression = _single_line(item.get("video_context_text"), 180)
        if not impression and item.get("video_link"):
            subtitle_status = _single_line(item.get("video_subtitle_status"), 40)
            if subtitle_status == "missing":
                impression = "找到当天 B 站 AI 早报视频，也尝试读取字幕；字幕暂无，只能按视频公开信息和标题记录。"
            elif subtitle_status == "unavailable":
                impression = "找到当天 B 站 AI 早报视频，也尝试读取字幕，但这次没有拿到可用正文，只能按视频公开信息和标题记录。"
            else:
                impression = "找到当天 B 站 AI 早报视频，但还没有可展开的文字正文，只能按视频公开信息和标题记录。"
        if not impression:
            impression = f"从 {source or '新闻源'} 看到一条新消息,还没来得及细看。"
        return {
            "topic": title or "新闻",
            "headline": title,
            "impression": impression,
            "selected_key": _single_line(item.get("key"), 32),
            "selected_link": _single_line(item.get("link"), 400),
            "selected_source": source,
            "items": items[:8],
            "created_ts": _now_ts(),
        }
