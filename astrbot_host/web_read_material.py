"""把消息里的链接读成正文，供回复使用。

只做一件事：用户给了网址，就去读那一页的文字。不执行脚本、不登录、不跟随页面里的
链接；内网/回环/元数据地址一律拒绝，重定向每一跳都重新校验目标地址。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import html as html_module
import ipaddress
import re
import socket
from urllib.parse import urlsplit, urljoin

MAX_LINKS = 2
MAX_BYTES = 2 * 1024 * 1024
MAX_CHARS = 4000
MAX_TITLE_CHARS = 200
TIMEOUT_SECONDS = 8
MAX_REDIRECTS = 3

URL_PATTERN = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+", re.IGNORECASE)
TITLE_PATTERN = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
HTML_TYPES = ("text/html", "application/xhtml+xml")
TEXT_TYPES = ("text/plain",)

INSTRUCTION = (
    "这是用户本轮发的链接的正文摘录，由插件当场抓取，可能不完整，也不代表已经核实过。"
    "basis=deepseek_fetch 表示本地页面读不到后由 DeepSeek Fetch 返回的 Markdown 正文；"
    "status=full 时可以据此总结和引用，并说明来自这个链接；status=title_only 表示页面需要脚本或登录，"
    "只拿到标题，这时要说清楚只读到标题，不要补写正文内容；status=blocked/unsupported/error 时说明没读到、原因是什么，"
    "不要假装看过那一页。")


def find_urls(text) -> tuple[str, ...]:
    """从一条消息里取出链接，去重并按出现顺序保留前两条。"""
    if type(text) is not str or not text:
        return ()
    seen, result = set(), []
    for match in URL_PATTERN.finditer(text):
        value = match.group(0).rstrip(".,;:!?，。；：！？、")
        if value and value not in seen:
            seen.add(value)
            result.append(value)
        if len(result) >= MAX_LINKS:
            break
    return tuple(result)


def address_allowed(host: str) -> bool:
    """拒绝内网、回环、链路本地、保留段与云元数据地址。主机名也会先解析再判断。"""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    addresses = {info[4][0] for info in infos if info[4]}
    if not addresses:
        return False
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return False
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast or address.is_unspecified):
            return False
        # 169.254.169.254 已由 link_local 覆盖，这里再挡一次明确的元数据域名解析结果。
        if str(address) == "169.254.169.254":
            return False
    return True


def _plain_text(value: str) -> str:
    """去标签取正文，跳过 script/style。与 news_parsing 同款，但不引入 XML 依赖。"""
    from html.parser import HTMLParser

    class _Text(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts, self.hidden = [], 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"):
                self.hidden += 1
            elif tag in ("br", "p", "div", "li"):
                self.parts.append("\n")

        def handle_endtag(self, tag):
            if tag in ("script", "style") and self.hidden:
                self.hidden -= 1
            elif tag in ("p", "div", "li"):
                self.parts.append("\n")

        def handle_data(self, data):
            if not self.hidden:
                self.parts.append(data)

    parser = _Text()
    parser.feed(value)
    return " ".join("".join(parser.parts).split())


def extract_html(raw: bytes) -> tuple[str, str]:
    """返回 (标题, 正文文本)。标题取 <title>，正文去掉标签与脚本。"""
    text = raw.decode("utf-8", "replace")
    match = TITLE_PATTERN.search(text)
    title = html_module.unescape(re.sub(r"\s+", " ", match.group(1))).strip() if match else ""
    return title[:MAX_TITLE_CHARS], _plain_text(text)


def _bounded(result: dict, *, title: str, body: str, limit: int = MAX_CHARS) -> dict:
    text = re.sub(r"\n{3,}", "\n\n", body).strip()
    if len(text) > limit:
        text = text[:limit]
    if not text:
        return {**result, "status": "title_only" if title else "empty_text", "title": title, "text": ""}
    return {**result, "status": "full", "title": title, "text": text, "chars": len(text)}


async def _read_limited(response) -> bytes:
    chunks, size = [], 0
    async for chunk in response.content.iter_chunked(65536):
        size += len(chunk)
        if size > MAX_BYTES:
            raise ValueError("too_large")
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_page_local(session, url: str, *, max_chars: int = MAX_CHARS,
                           timeout: float = TIMEOUT_SECONDS) -> dict:
    """读一页；返回带状态的结果，任何失败都落到明确的状态而不是抛给回复链路。"""
    current, seen = url, set()
    for _ in range(MAX_REDIRECTS + 1):
        parsed = urlsplit(current)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return {"url": url, "status": "unsupported", "reason": "only http(s) links are read"}
        if current in seen:
            return {"url": url, "status": "error", "reason": "redirect_loop"}
        seen.add(current)
        if not await asyncio.to_thread(address_allowed, parsed.hostname):
            return {"url": current, "status": "blocked", "reason": "private or reserved address"}
        try:
            async with session.get(current, allow_redirects=False,
                                   timeout=timeout) as response:
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        return {"url": current, "status": "error", "reason": "redirect_without_location"}
                    current = urljoin(current, location)
                    continue
                if response.status != 200:
                    return {"url": current, "status": "error", "reason": f"http_{response.status}"}
                content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                raw = await _read_limited(response)
        except asyncio.TimeoutError:
            return {"url": current, "status": "error", "reason": "timeout"}
        except ValueError as error:
            return {"url": current, "status": "error", "reason": str(error) or "invalid_page"}
        except Exception as error:  # noqa: BLE001 - 网络失败要变成可读状态
            return {"url": current, "status": "error", "reason": type(error).__name__}
        if content_type in HTML_TYPES or (not content_type and current.endswith((".html", ".htm", "/"))):
            title, body = extract_html(raw)
            return _bounded({"url": current}, title=title, body=body, limit=max_chars)
        if content_type in TEXT_TYPES or content_type.startswith("text/"):
            return _bounded({"url": current}, title="", body=raw.decode("utf-8", "replace"), limit=max_chars)
        if content_type == "application/pdf":
            from .files.parsing import extract_document
            try:
                document = await asyncio.to_thread(extract_document, raw, 0, "page.pdf", maximum_chars=max_chars)
            except Exception as error:  # noqa: BLE001
                return {"url": current, "status": "unsupported",
                        "reason": getattr(error, "code", type(error).__name__)}
            return _bounded({"url": current}, title="", body=document.text, limit=max_chars)
        return {"url": current, "status": "unsupported", "reason": f"content_type {content_type or 'unknown'}"}
    return {"url": url, "status": "error", "reason": "too_many_redirects"}


def _fetchable(url):
    """Only send public HTTP(S) links to the optional DeepSeek Fetch service."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return (parts.scheme in ("http", "https") and bool(parts.hostname)
            and not parts.username and not parts.password)


async def _read_with_fetch(fetch, url, max_chars):
    if fetch is None or not _fetchable(url):
        return None
    parsed = urlsplit(url)
    if not await asyncio.to_thread(address_allowed, parsed.hostname):
        return None
    try:
        page = await asyncio.wait_for(fetch(url), timeout=TIMEOUT_SECONDS + 4)
    except Exception:
        return None
    if not isinstance(page, dict):
        return None
    title = page.get("title")
    content = page.get("content")
    if type(title) is not str:
        title = ""
    if type(content) is not str or not content.strip():
        return None
    return _bounded({"url": url, "basis": "deepseek_fetch"}, title=title[:MAX_TITLE_CHARS],
                    body=content, limit=max_chars)


async def read_page(session, url: str, *, max_chars: int = MAX_CHARS,
                    timeout: float = TIMEOUT_SECONDS, fetch=None) -> dict:
    """Read locally first; DeepSeek Fetch is a fallback for JS/login/blocked pages.

    Local HTML remains the default so a user's link is not sent to an external
    service when the page can be read directly. A blocked/private URL never
    falls back.
    """
    result = await _read_page_local(session, url, max_chars=max_chars, timeout=timeout)
    if result.get("status") in ("full", "blocked"):
        return result
    fetched = await _read_with_fetch(fetch, url, max_chars)
    return fetched or result


async def read_links(urls, *, max_links: int = MAX_LINKS, max_chars: int = MAX_CHARS,
                     timeout: float = TIMEOUT_SECONDS, fetch=None) -> list[dict]:
    """并发读多条链接；单条失败不影响其它。"""
    import aiohttp
    urls = tuple(url for url in urls or () if isinstance(url, str))[:max(1, int(max_links))]
    if not urls:
        return []
    client_timeout = aiohttp.ClientTimeout(total=timeout + 2)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        return list(await asyncio.gather(
            *(read_page(session, url, max_chars=max_chars, timeout=timeout, fetch=fetch)
              for url in urls),
            return_exceptions=False))


def _web_settings(application, context, at):
    """读窗口的网页理解设置；读不到就退回默认值，不让设置故障挡住读取。"""
    from companion_v2.conversation.web_understanding import DEFAULTS, WebUnderstandingSettings
    try:
        return WebUnderstandingSettings(application.configuration).read(context, at)
    except Exception:
        return dict(DEFAULTS)


def web_read_keyword_material(application, fetch=None):
    """按链接触发：消息里出现网址且当前是主要陪伴对象的私聊时才读。"""
    from companion_v2.conversation.keyword_materials import KeywordMaterialSpec
    from companion_v2.expression.configuration import source_subject_allowed_for_context
    from companion_v2.foundation.refs import ScopeKind

    async def prepare(context, text, at):
        settings = _web_settings(application, context, at)
        urls = find_urls(text)[:max(1, int(settings["max_links"]))]
        if not urls:
            return {"state": "unavailable", "reason": "no_link"}
        pages = await read_links(urls, max_links=settings["max_links"],
                                 max_chars=settings["max_chars"],
                                 timeout=settings["timeout_seconds"], fetch=fetch)
        return {"state": "ok", "observed_at": (at or datetime.now(timezone.utc)).isoformat(),
                "links": pages}

    def available(context):
        if context.scope.kind is not ScopeKind.PRIVATE:
            return False
        settings = _web_settings(application, context, application.now())
        if settings.get("enabled") is not True:
            return False
        try:
            return bool(source_subject_allowed_for_context(application.runtime, context))
        except Exception:
            return False

    return KeywordMaterialSpec(owner="web.read", title="链接正文", instruction=INSTRUCTION,
        keywords=("http",), prepare=prepare, available=available, ttl_seconds=0)