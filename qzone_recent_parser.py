# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from types import SimpleNamespace
from typing import Any


class _QzoneFeedHtmlParser(HTMLParser):
    _VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.repost_parts: list[str] = []
        self.image_urls: list[str] = []
        self._class_stack: list[set[str]] = []
        self._tag_stack: list[str] = []

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        for key, value in attrs:
            if str(key or "").lower() == "class":
                return {item.strip() for item in str(value or "").split() if item.strip()}
        return set()

    @staticmethod
    def _attr(attrs: list[tuple[str, str | None]], name: str) -> str:
        lowered = str(name or "").lower()
        for key, value in attrs:
            if str(key or "").lower() == lowered:
                return str(value or "")
        return ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = str(tag or "").lower()
        classes = self._classes(attrs)
        if normalized_tag != "img":
            if normalized_tag not in self._VOID_TAGS:
                self._tag_stack.append(normalized_tag)
                self._class_stack.append(classes)
            elif classes:
                self._class_stack.append(classes)
                self._class_stack.pop()
            return
        src = self._attr(attrs, "src")
        if not src or src.startswith("http://qzonestyle.gtimg.cn"):
            return
        active_classes = self._class_stack + [classes]
        if any({"img-box", "video-img"} & item for item in active_classes):
            self.image_urls.append(src)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = str(tag or "").lower()
        if normalized_tag == "img":
            self.handle_starttag(tag, attrs)
            return
        classes = self._classes(attrs)
        if classes:
            self._class_stack.append(classes)
            self._class_stack.pop()

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = str(tag or "").lower()
        for index in range(len(self._tag_stack) - 1, -1, -1):
            if self._tag_stack[index] == normalized_tag:
                del self._tag_stack[index:]
                del self._class_stack[index:]
                return
        if self._tag_stack and self._class_stack:
            self._tag_stack.pop()
            self._class_stack.pop()

    def handle_data(self, data: str) -> None:
        text = unescape(str(data or "")).strip()
        if not text:
            return
        if any("f-info" in item for item in self._class_stack):
            self.text_parts.append(text)
        if any("txt-box" in item for item in self._class_stack):
            self.repost_parts.append(text)


def _parse_feed_html(html_content: str) -> tuple[str, str, list[str]]:
    parser = _QzoneFeedHtmlParser()
    parser.feed(str(html_content or ""))
    text = "".join(parser.text_parts).strip()
    repost = "".join(parser.repost_parts).strip()
    if "：" in repost:
        repost = repost.split("：", 1)[1].strip()
    return text, repost, parser.image_urls


def _html_attr_value(html_content: str, *names: str) -> str:
    source = str(html_content or "")
    for name in names:
        escaped = re.escape(str(name or ""))
        match = re.search(rf"""{escaped}\s*=\s*["']([^"']+)["']""", source, flags=re.IGNORECASE)
        if match:
            return unescape(match.group(1)).strip()
    return ""


def parse_recent_feeds(data: dict[str, Any]) -> list[Any]:
    feeds: Any = []
    if isinstance(data, dict):
        first = data.get("data")
        if isinstance(first, dict):
            second = first.get("data")
            feeds = second if isinstance(second, list) else []
        elif isinstance(first, list):
            feeds = first
        elif isinstance(data.get("msglist"), list):
            feeds = data.get("msglist")
    if not isinstance(feeds, list) or not feeds:
        return []
    posts: list[Any] = []
    for feed in feeds:
        if not feed:
            continue
        appid = str(feed.get("appid", ""))
        if appid != "311":
            continue
        uin = int(feed.get("uin") or 0)
        tid = str(feed.get("key") or "")
        if not uin or not tid:
            continue
        html_content = str(feed.get("html") or "")
        if not html_content:
            continue
        text, rt_con, image_urls = _parse_feed_html(html_content)
        posts.append(
            SimpleNamespace(
                tid=tid,
                uin=uin,
                name=str(feed.get("nickname") or feed.get("name") or uin),
                text=text,
                rt_con=rt_con,
                images=image_urls,
                comments=[],
                create_time=int(feed.get("abstime") or 0),
                appid=str(feed.get("appid") or "311"),
                typeid=str(feed.get("typeid") or feed.get("type") or "0"),
                abstime=int(feed.get("abstime") or 0),
                fid=str(feed.get("key") or tid),
                unikey=str(feed.get("unikey") or feed.get("likeKey") or feed.get("like_key") or _html_attr_value(html_content, "data-unikey", "unikey")),
                curkey=str(feed.get("curkey") or feed.get("curlikekey") or feed.get("likeKey") or feed.get("like_key") or _html_attr_value(html_content, "data-curkey", "curkey")),
                raw=feed,
                status="approved",
            )
        )
    return posts
