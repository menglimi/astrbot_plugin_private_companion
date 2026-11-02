"""Host transports for explicit HTTPS JSON, RSS/Atom and Bilibili news sources."""

import json
import os
import re
import asyncio
import ipaddress
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlencode, urlsplit, urlunsplit, parse_qsl

import aiohttp

from companion_v2.execution.models import digest
from companion_v2.proactive.external_materials import NewsItem, ParseBatch, canonical_news_url, parse_news_response
from companion_v2.proactive.news_sources import NewsFetchError, NewsSource, news_uid, news_url

from .news_parsing import parse_bilibili_news, parse_rss_news


MAX_RESPONSE_BYTES = 1048576
HEADER_NAME = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}\Z")
DEFAULT_AI_NEWS_FEED_URL = "https://daily.juya.uk/rss.xml"
DEFAULT_AI_NEWS_FEED_LABEL = "橘鸦AI早报"
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"
DEEPSEEK_SEARCH_URL = DEEPSEEK_API_BASE + "/tools/search"
DEEPSEEK_FETCH_URL = DEEPSEEK_API_BASE + "/tools/fetch"
DEEPSEEK_KEY_ENV = ("COMPANION_V2_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY")
MAX_DEEPSEEK_FETCH_URL = 8192
ASTRBOT_SEARCH_PROVIDERS = {"tavily", "bocha", "brave", "firecrawl", "baidu_ai_search", "exa", "anysearch"}


def _bounded_text(value, limit):
    if type(value) is not str:
        return ""
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _published_at(value):
    if isinstance(value, datetime):
        return value.isoformat() if value.tzinfo is not None and value.utcoffset() is not None else ""
    if type(value) is not str:
        return ""
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return ""
    return parsed.isoformat() if parsed.tzinfo is not None and parsed.utcoffset() is not None else ""


def _task_umo(context):
    scope = getattr(context, "scope", None)
    kind = getattr(getattr(scope, "kind", None), "value", getattr(scope, "kind", None))
    platform = getattr(scope, "platform", "")
    scope_id = getattr(scope, "native_scope_id", "")
    if kind != "private" or type(platform) is not str or not platform or type(scope_id) is not str or not scope_id:
        return ""
    return f"{platform}:FriendMessage:{scope_id}"


def _provider_api_base(provider):
    config = getattr(provider, "provider_config", None)
    value = config.get("api_base", "") if isinstance(config, dict) else ""
    if not value:
        value = getattr(getattr(provider, "client", None), "base_url", "")
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        parts = urlsplit(value.strip().rstrip("/"))
        port = parts.port
    except ValueError:
        return ""
    if (parts.scheme != "https" or parts.hostname != "api.deepseek.com" or parts.username or parts.password
            or port not in (None, 443) or parts.query or parts.fragment or parts.path not in ("", "/", "/v1")):
        return ""
    return "https://api.deepseek.com" + ("/v1" if parts.path == "/v1" else "")


def _all_providers(context):
    getter = getattr(context, "get_all_providers", None)
    if not callable(getter):
        return ()
    try:
        values = getter() or ()
    except Exception:
        return ()
    return tuple(values.values()) if isinstance(values, dict) else tuple(values)


def _provider_identifier(provider):
    try:
        metadata = provider.meta() if callable(getattr(provider, "meta", None)) else None
    except Exception:
        metadata = None
    value = getattr(metadata, "id", "") or getattr(provider, "id", "")
    return value.strip()[:80] if isinstance(value, str) else ""


def _provider_name(provider, identifier):
    try:
        metadata = provider.meta() if callable(getattr(provider, "meta", None)) else None
    except Exception:
        metadata = None
    value = getattr(metadata, "name", "") or getattr(metadata, "label", "") or identifier
    return value.strip()[:120] if isinstance(value, str) else identifier


def _provider_api_key(provider):
    key = ""
    key_getter = getattr(provider, "get_current_key", None)
    if callable(key_getter):
        try:
            key = key_getter()
        except Exception:
            key = ""
    if not isinstance(key, str) or not key.strip():
        key = getattr(getattr(provider, "client", None), "api_key", "")
    if (type(key) is str and key.strip() and len(key) <= 4096
            and not any(ord(char) < 32 for char in key)):
        return key.strip()
    return ""


def deepseek_provider_catalog(context):
    """Project usable official DeepSeek Providers without exposing their credentials."""
    providers = {}
    for provider in _all_providers(context):
        identifier = _provider_identifier(provider)
        if (not identifier or not _provider_api_base(provider) or not _provider_api_key(provider)
                or identifier in providers):
            continue
        providers[identifier] = {"id": identifier, "name": _provider_name(provider, identifier)}
    return sorted(providers.values(), key=lambda item: (item["name"].casefold(), item["id"]))


def deepseek_api_key(context=None, umo="", *, provider_id=None):
    """Read a key from the selected DeepSeek Provider, or the legacy automatic sources."""
    if provider_id:
        if context is None:
            return ""
        return next((_provider_api_key(provider) for provider in _all_providers(context)
                     if _provider_identifier(provider) == provider_id
                     and _provider_api_base(provider)), "")
    for name in DEEPSEEK_KEY_ENV:
        value = os.environ.get(name, "")
        if type(value) is str and value.strip():
            return value.strip()
    if context is None:
        return ""
    candidates = []
    getter = getattr(context, "get_using_provider", None)
    if callable(getter):
        try:
            selected = getter(umo) if umo else getter()
        except Exception:
            selected = None
        if selected is not None:
            candidates.append(selected)
    candidates.extend(_all_providers(context))
    seen = set()
    for provider in candidates:
        if id(provider) in seen:
            continue
        seen.add(id(provider))
        if _provider_api_base(provider):
            key = _provider_api_key(provider)
            if key:
                return key
    return ""


def _astrbot_search_settings(context, umo=""):
    if context is None:
        return "", {}
    getter = getattr(context, "get_config", None)
    if not callable(getter):
        return "", {}
    try:
        config = getter(umo=umo) if umo else getter()
    except TypeError:
        try:
            config = getter()
        except Exception:
            return "", {}
    except Exception:
        return "", {}
    if not isinstance(config, dict):
        return "", {}
    settings = config.get("provider_settings", {})
    if not isinstance(settings, dict) or settings.get("web_search") is not True:
        return "", {}
    provider = settings.get("websearch_provider", "")
    if type(provider) is not str or provider not in ASTRBOT_SEARCH_PROVIDERS:
        return "", {}
    key_name = "websearch_baidu_app_builder_key" if provider == "baidu_ai_search" else "websearch_" + provider + "_key"
    key = settings.get(key_name)
    if isinstance(key, list):
        configured = any(type(item) is str and item.strip() for item in key)
    else:
        configured = type(key) is str and bool(key.strip())
    return (provider, dict(settings)) if configured or provider == "anysearch" else ("", {})


def astrbot_web_search_available(context, umo=""):
    return bool(_astrbot_search_settings(context, umo)[0])


class NewsJsonPort:
    kind = "json"

    def __init__(self, *, url, api_key="", auth_header="Authorization", auth_scheme="Bearer"):
        self.url = news_url(url)
        if (type(api_key) is not str or len(api_key) > 4096 or any(ord(char) < 32 for char in api_key)
                or type(auth_header) is not str or HEADER_NAME.fullmatch(auth_header) is None
                or type(auth_scheme) is not str or re.fullmatch(r"[A-Za-z0-9_-]{0,32}", auth_scheme) is None):
            raise ValueError("news authorization settings are invalid")
        self.source_label = urlsplit(url).hostname.casefold()
        self.fingerprint = digest([self.kind, url, auth_header, auth_scheme, api_key])
        self.headers = {}
        if api_key:
            self.headers[auth_header] = (auth_scheme + " " if auth_scheme else "") + api_key

    async def _read(self, url=None):
        timeout = aiohttp.ClientTimeout(total=8)
        try:
            # Some public RSS hosts reject the aiohttp default client header (or
            # return a bot-check page without one). Keep the request identity
            # explicit and stable so the default AI daily feed is read the same
            # way in background maintenance and in the verified contact path.
            async with aiohttp.ClientSession(
                    timeout=timeout, trust_env=False,
                    headers={"Accept-Encoding": "gzip, deflate",
                             "User-Agent": "AstrBotCompanionV2/2.0"}) as session:
                async with session.get(url or self.url, headers=self.headers, allow_redirects=False) as response:
                    if response.status != 200:
                        raise NewsFetchError("news_http_error", http_status=response.status)
                    if response.content_length is not None and response.content_length > MAX_RESPONSE_BYTES:
                        raise NewsFetchError("news_too_large")
                    chunks = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        if len(chunks) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise NewsFetchError("news_too_large")
                        chunks.extend(chunk)
                    return bytes(chunks)
        except TimeoutError as error:
            raise NewsFetchError("news_timeout") from error
        except aiohttp.ClientError as error:
            raise NewsFetchError("news_unavailable") from error

    async def fetch(self):
        raw = await self._read()
        try:
            return parse_news_response(json.loads(raw))
        except (ValueError, TypeError, UnicodeError) as error:
            raise NewsFetchError("news_payload_invalid") from error


class NewsRssPort(NewsJsonPort):
    kind = "rss"

    def __init__(self, *, url, source_label="", api_key="", auth_header="Authorization", auth_scheme="Bearer"):
        super().__init__(url=url, api_key=api_key, auth_header=auth_header, auth_scheme=auth_scheme)
        if source_label:
            self.source_label = source_label

    async def fetch(self):
        raw = await self._read()
        try:
            return parse_rss_news(raw, source_label=self.source_label)
        except NewsFetchError:
            raise
        except (ValueError, TypeError, UnicodeError, OverflowError) as error:
            raise NewsFetchError("news_payload_invalid") from error


class BilibiliNewsPort(NewsJsonPort):
    kind = "bilibili"

    def __init__(self, *, uid, cookie=""):
        self.uid = news_uid(uid)
        if (type(cookie) is not str or len(cookie) > 8192 or any(ord(char) < 32 for char in cookie)):
            raise ValueError("Bilibili cookie is invalid")
        super().__init__(url="https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?host_mid=" + uid)
        self.source_label = "B站动态 · UID " + uid
        self.fingerprint = digest([self.kind, uid, cookie])
        self.headers.update({"Referer": "https://space.bilibili.com/" + uid + "/dynamic",
                             "User-Agent": "Mozilla/5.0 CompanionV2/2.0"})
        if cookie:
            self.headers["Cookie"] = cookie

    async def fetch(self):
        raw = await self._read()
        try:
            return parse_bilibili_news(json.loads(raw), uid=self.uid)
        except NewsFetchError:
            raise
        except (ValueError, TypeError, UnicodeError, OverflowError) as error:
            raise NewsFetchError("news_payload_invalid") from error


class WebSearchNewsPort(NewsJsonPort):
    kind = "search"

    def __init__(self, *, url, query, result_count=8, api_key="", auth_header="Authorization", auth_scheme="Bearer"):
        super().__init__(url=url, api_key=api_key, auth_header=auth_header, auth_scheme=auth_scheme)
        self.query = query.strip()[:240] if type(query) is str else ""
        self.result_count = max(1, min(30, int(result_count)))
        self.source_label = "网页搜索"
        self.fingerprint = digest([self.kind, self.url, self.query, self.result_count, self.fingerprint])

    async def _read(self):
        parts = urlsplit(self.url)
        query = urlencode([*parse_qsl(parts.query, keep_blank_values=True),
                           ("q", self.query), ("count", str(self.result_count))])
        return await super()._read(urlunsplit((parts.scheme, parts.netloc, parts.path, query, "")))

    async def fetch(self):
        batch = await super().fetch()
        return ParseBatch(batch.items[:self.result_count], batch.issues)


def _deepseek_rows(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for owner in (payload, payload.get("data"), payload.get("result")):
        if isinstance(owner, list):
            return owner
        if isinstance(owner, dict):
            for key in ("results", "items", "data"):
                rows = owner.get(key)
                if isinstance(rows, list):
                    return rows
    return []


def _safe_deepseek_fetch_url(value):
    if (type(value) is not str or not 1 <= len(value) <= MAX_DEEPSEEK_FETCH_URL or "\\" in value
            or any(ord(char) < 33 for char in value)):
        return ""
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").casefold()
        port = parts.port
    except ValueError:
        return ""
    expected_port = 80 if parts.scheme == "http" else 443
    if (parts.scheme not in {"http", "https"} or not host or parts.username or parts.password
            or port not in (None, expected_port)):
        return ""
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return ""
    try:
        return canonical_news_url(value)
    except ValueError:
        return ""


def _deepseek_search_endpoint(value):
    if type(value) is not str:
        return ""
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return ""
    path = parts.path.rstrip("/")
    if (parts.scheme != "https" or parts.hostname != "api.deepseek.com" or parts.username or parts.password
            or port not in (None, 443) or parts.query or parts.fragment
            or path not in {"/v1/tools/search", "/tools/search"}):
        return ""
    return "https://api.deepseek.com/v1/tools/search"


class DeepSeekSearchNewsPort:
    """DeepSeek Search API with on-demand Fetch for the one selected article."""

    kind = "search"
    source_label = "DeepSeek 网页搜索"

    def __init__(self, *, api_key, query, result_count=8, search_url=""):
        if (type(api_key) is not str or not api_key.strip() or len(api_key) > 4096
                or any(ord(char) < 32 for char in api_key)):
            raise ValueError("DeepSeek API key is invalid")
        self.api_key = api_key.strip()
        self.query = _bounded_text(query, 128)
        if not self.query:
            raise ValueError("DeepSeek search query is empty")
        self.result_count = max(1, min(50, int(result_count)))
        self.search_url = _deepseek_search_endpoint(search_url) if search_url else DEEPSEEK_SEARCH_URL
        if not self.search_url:
            raise ValueError("DeepSeek Search endpoint is invalid")
        self.fetch_url = self.search_url.rsplit("/", 1)[0] + "/fetch"
        self.headers = {"Authorization": "Bearer " + self.api_key, "Accept": "application/json",
                        "Content-Type": "application/json"}
        self.fingerprint = digest([self.kind, self.search_url, self.query, self.result_count, self.api_key])

    async def _post(self, url, payload):
        timeout = aiohttp.ClientTimeout(total=12)
        try:
            async with aiohttp.ClientSession(
                    timeout=timeout, trust_env=False,
                    headers={"User-Agent": "AstrBotCompanionV2/2.0"}) as session:
                async with session.post(url, json=payload, headers=self.headers, allow_redirects=False) as response:
                    if response.content_length is not None and response.content_length > MAX_RESPONSE_BYTES:
                        raise NewsFetchError("news_too_large")
                    chunks = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        if len(chunks) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise NewsFetchError("news_too_large")
                        chunks.extend(chunk)
                    try:
                        result = json.loads(bytes(chunks))
                    except (ValueError, TypeError, UnicodeError) as error:
                        if response.status == 200:
                            raise NewsFetchError("news_payload_invalid") from error
                        result = {}
                    if response.status != 200:
                        error_body = result.get("error", {}) if isinstance(result, dict) else {}
                        if not isinstance(error_body, dict):
                            error_body = {}
                        code = (error_body.get("code") or result.get("code")) if isinstance(result, dict) else ""
                        code = code if type(code) is str and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", code) else None
                        raise NewsFetchError("news_http_error", http_status=response.status, provider_code=code)
                    return result
        except TimeoutError as error:
            raise NewsFetchError("news_timeout") from error
        except aiohttp.ClientError as error:
            raise NewsFetchError("news_unavailable") from error

    async def fetch(self):
        payload = await self._post(self.search_url, {
            "query": self.query,
            "count": self.result_count,
            "need_content": False,
        })
        rows = []
        for raw in _deepseek_rows(payload)[:self.result_count]:
            if not isinstance(raw, dict):
                continue
            url = raw.get("url", "")
            rows.append({
                "id": raw.get("id", url),
                "title": raw.get("title", ""),
                "url": url,
                "source": raw.get("site_name") or raw.get("source") or raw.get("publisher") or "DeepSeek 网页搜索",
                "summary": raw.get("summary") or raw.get("snippet") or raw.get("content") or raw.get("description") or "",
                "published_at": _published_at(raw.get("date") or raw.get("published_at")
                                                or raw.get("published_date")) or None,
                "category": raw.get("category", ""),
            })
        try:
            batch = parse_news_response({"source": self.source_label, "items": rows})
        except (ValueError, TypeError, UnicodeError) as error:
            raise NewsFetchError("news_payload_invalid") from error
        return ParseBatch(batch.items[:self.result_count], batch.issues)

    async def fetch_article(self, url):
        target = _safe_deepseek_fetch_url(url)
        if not target:
            raise NewsFetchError("news_url_invalid")
        payload = await self._post(self.fetch_url, {"url": target})
        rows = _deepseek_rows(payload)
        row = next((item for item in rows if isinstance(item, dict)), None)
        if row is None and isinstance(payload, dict):
            row = next((item for item in (payload, payload.get("data"), payload.get("result"))
                        if isinstance(item, dict) and any(key in item for key in
                            ("title", "content", "markdown", "markdown_content", "text", "body"))), payload)
        if not isinstance(row, dict):
            row = {}
        title = _bounded_text(row.get("title") or row.get("page_title", ""), 240)
        content = (row.get("content") or row.get("markdown") or row.get("markdown_content")
                   or row.get("text") or row.get("body") or "")
        if type(content) is not str and isinstance(content, dict):
            content = content.get("markdown") or content.get("text") or content.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(item.get("text", "") if isinstance(item, dict) else str(item)
                                  for item in content)
        return {"title": title, "content": content.strip()[:12000] if type(content) is str else ""}


class AstrBotWebSearchNewsPort:
    """V1-compatible adapter for the search provider configured in AstrBot."""

    kind = "search"
    source_label = "AstrBot 网页搜索"

    def __init__(self, *, context, query, result_count=8, umo="", topic="news"):
        self.context, self.umo = context, umo
        self.query = _bounded_text(query, 240)
        if not self.query:
            raise ValueError("AstrBot search query is empty")
        self.result_count = max(1, min(30, int(result_count)))
        self.topic = "news" if topic == "news" else "general"
        self.provider, self.settings = _astrbot_search_settings(context, umo)
        if not self.provider:
            raise ValueError("AstrBot web search is not configured")
        self.fingerprint = digest([self.kind, self.provider, self.query, self.result_count, self.topic,
                                   self.settings])

    async def fetch(self):
        from astrbot.core.tools import web_search_tools as search_tools

        settings = dict(self.settings)
        for key in ("websearch_tavily_key", "websearch_bocha_key", "websearch_brave_key",
                    "websearch_firecrawl_key", "websearch_exa_key", "websearch_anysearch_key"):
            if isinstance(settings.get(key), str):
                value = settings[key].strip()
                settings[key] = [value] if value else []
        baidu_key = settings.get("websearch_baidu_app_builder_key")
        if isinstance(baidu_key, list):
            settings["websearch_baidu_app_builder_key"] = str(baidu_key[0] if baidu_key else "").strip()
        elif isinstance(baidu_key, str):
            settings["websearch_baidu_app_builder_key"] = baidu_key.strip()
        count = self.result_count
        if self.provider == "tavily":
            request = {"query": self.query, "max_results": count, "include_favicon": True,
                       "search_depth": "basic", "topic": self.topic}
            if self.topic == "news":
                request["days"] = 7
            raw = await search_tools._tavily_search(settings, request)
        elif self.provider == "bocha":
            raw = await search_tools._bocha_search(settings, {"query": self.query, "count": count,
                "summary": True, "freshness": "noLimit"})
        elif self.provider == "brave":
            raw = await search_tools._brave_search(settings, {"q": self.query, "count": min(20, count),
                "country": "CN", "search_lang": "zh-hans"})
        elif self.provider == "firecrawl":
            raw = await search_tools._firecrawl_search(settings, {"query": self.query, "limit": count,
                "sources": ["web"]})
        elif self.provider == "baidu_ai_search":
            raw = await search_tools._baidu_search(settings, {
                "messages": [{"role": "user", "content": self.query[:72]}],
                "search_source": "baidu_search_v2",
                "resource_type_filter": [{"type": "web", "top_k": count}],
            })
        elif self.provider == "exa":
            raw = await search_tools._exa_search(settings, {
                "query": self.query, "numResults": count, "type": "auto",
                "contents": {"text": {"maxCharacters": 500}},
            })
        elif self.provider == "anysearch":
            raw = await search_tools._anysearch_search(settings, {
                "query": self.query, "max_results": min(10, count), "format": "json",
            })
        else:
            raise NewsFetchError("news_source_disabled")
        rows = []
        for item in raw or ():
            def value(*names):
                for name in names:
                    candidate = item.get(name) if isinstance(item, dict) else getattr(item, name, None)
                    if candidate not in (None, ""):
                        return candidate
                return ""
            url = value("url", "link", "href")
            url = url if type(url) is str else ""
            try:
                host = urlsplit(url).hostname
            except ValueError:
                host = None
            rows.append({"title": value("title", "name", "headline"), "url": url,
                "source": value("site_name", "source", "publisher") or host or "网页搜索",
                "summary": value("snippet", "summary", "content", "description", "text"),
                "published_at": _published_at(value("published_at", "published_date", "date_published", "date"))
                    or None})
        try:
            batch = parse_news_response({"source": self.source_label, "items": rows})
        except (ValueError, TypeError, UnicodeError) as error:
            raise NewsFetchError("news_payload_invalid") from error
        return ParseBatch(batch.items[:count], batch.issues)


def configured_search_port(context, *, query, result_count=8, search_url="", auth=None, task_context=None,
                           topic="news", deepseek_provider_id=None, deepseek_provider_resolver=None,
                           source_priority="auto"):
    """Resolve V2 search through an explicit endpoint, the preferred source, then the other one."""
    auth = auth or {}
    umo = _task_umo(task_context)
    priority = source_priority if source_priority in {"auto", "astrbot", "deepseek"} else "auto"
    if deepseek_provider_id is None and callable(deepseek_provider_resolver):
        try:
            deepseek_provider_id = deepseek_provider_resolver()
        except Exception:
            deepseek_provider_id = "invalid-search-provider-selection"
    if deepseek_provider_id is None:
        deepseek_provider_id = ""
    elif type(deepseek_provider_id) is not str:
        deepseek_provider_id = "invalid-search-provider-selection"
    deepseek_provider_id = deepseek_provider_id.strip()
    configured_url = search_url.strip() if type(search_url) is str else ""
    if configured_url:
        if _deepseek_search_endpoint(configured_url):
            api_key = (deepseek_api_key(context, umo, provider_id=deepseek_provider_id)
                       if deepseek_provider_id else auth.get("api_key") or deepseek_api_key(context, umo))
            if not api_key:
                return None
            return DeepSeekSearchNewsPort(api_key=api_key, query=query, result_count=result_count,
                                          search_url=configured_url)
        return WebSearchNewsPort(url=configured_url, query=query, result_count=result_count, **auth)
    if deepseek_provider_id == "invalid-search-provider-selection":
        # 选择项本身读不出来：不猜、不静默换到别的 Provider。
        return None

    def astrbot_port():
        if astrbot_web_search_available(context, umo):
            return AstrBotWebSearchNewsPort(context=context, query=query, result_count=result_count,
                                            umo=umo, topic=topic)
        return None

    def provider_port(provider_id):
        api_key = deepseek_api_key(context, umo, provider_id=provider_id or None)
        if api_key:
            return DeepSeekSearchNewsPort(api_key=api_key, query=query, result_count=result_count)
        return None

    # 显式选了 Provider 时它仍是“首选”；优先级只决定“首选不可用时先试谁”。
    if priority == "astrbot":
        return astrbot_port() or provider_port(deepseek_provider_id)
    if priority == "deepseek":
        return provider_port(deepseek_provider_id) or astrbot_port()
    if deepseek_provider_id:
        return provider_port(deepseek_provider_id) or astrbot_port()
    return astrbot_port() or provider_port("")


class HotTrendNewsPort:
    kind = "hot"
    source_label = "公开热点"

    def __init__(self, sources=("weibo", "hackernews"), limit=12):
        allowed = {"weibo", "hackernews"}
        self.sources = tuple(dict.fromkeys(item for item in sources if item in allowed))
        if not self.sources:
            raise ValueError("at least one supported hot source is required")
        self.limit = max(3, min(30, int(limit)))
        self.fingerprint = digest([self.kind, self.sources, self.limit])

    async def _weibo(self, session):
        async with session.get("https://weibo.com/ajax/side/hotSearch") as response:
            if response.status != 200:
                return []
            payload = await response.json(content_type=None)
        rows = payload.get("data", {}).get("realtime", []) if type(payload) is dict else []
        result = []
        for rank, row in enumerate(rows[:self.limit], 1):
            title = row.get("note") or row.get("word") if type(row) is dict else ""
            if type(title) is str and title.strip():
                result.append(NewsItem("微博热搜", str(rank), title.strip()[:240],
                    "https://s.weibo.com/weibo?q=" + quote("#" + title.strip() + "#"),
                    "微博热搜第 " + str(rank) + " 位", None))
        return result

    async def _hackernews(self, session):
        async with session.get("https://hacker-news.firebaseio.com/v0/topstories.json") as response:
            if response.status != 200:
                return []
            ids = await response.json()
        async def one(identifier):
            async with session.get(f"https://hacker-news.firebaseio.com/v0/item/{int(identifier)}.json") as response:
                if response.status != 200:
                    return None
                row = await response.json()
            if type(row) is not dict or not row.get("title") or not row.get("time"):
                return None
            return NewsItem("Hacker News", str(identifier), str(row["title"])[:240],
                str(row.get("url") or f"https://news.ycombinator.com/item?id={identifier}"),
                "Hacker News 热门讨论", datetime.fromtimestamp(int(row["time"]), timezone.utc))
        return [item for item in await asyncio.gather(*(one(identifier) for identifier in ids[:self.limit])) if item]

    async def fetch(self):
        timeout = aiohttp.ClientTimeout(total=12)
        headers = {"User-Agent": "AstrBotCompanionV2/2.0", "Accept": "application/json"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers, trust_env=False) as session:
            calls = [self._weibo(session) if name == "weibo" else self._hackernews(session)
                     for name in self.sources]
            batches = await asyncio.gather(*calls, return_exceptions=True)
        items = [item for batch in batches if isinstance(batch, list) for item in batch]
        if not items:
            raise NewsFetchError("news_unavailable")
        return ParseBatch(tuple(items[:self.limit * len(self.sources)]), ())


class NewsSourceRouter:
    """Resolve each window's public selection without storing host credentials in V2."""

    def __init__(self, default_source, *, auth, default_source_auth=True, bilibili_cookie="", search_url="",
                 hot_sources=("weibo", "hackernews"), hot_result_count=10, host_context=None,
                 deepseek_provider_resolver=None, search_priority_resolver=None):
        self.default_source, self.auth, self.bilibili_cookie = default_source, auth, bilibili_cookie
        self.default_source_auth = default_source_auth
        self.search_url, self.hot_sources = search_url, tuple(hot_sources)
        self.hot_result_count = max(3, min(30, int(hot_result_count)))
        self.host_context = host_context
        self.deepseek_provider_resolver = deepseek_provider_resolver
        self.search_priority_resolver = search_priority_resolver

    def for_settings(self, values, task_context=None):
        source = NewsSource.read(values)
        if source.kind == "host":
            source = self.default_source
        if source.kind in ("host", "disabled"):
            return None
        if source.kind == "bilibili":
            return BilibiliNewsPort(uid=source.uid, cookie=self.bilibili_cookie)
        if source.kind == "search":
            query = values.get("web_exploration_interests", "").strip() or "有趣的新鲜事 科技 文化 生活"
            return configured_search_port(self.host_context, query=query,
                result_count=values.get("web_exploration_result_count", 8), search_url=self.search_url,
                auth=self.auth, task_context=task_context, topic="news",
                deepseek_provider_resolver=self.deepseek_provider_resolver,
                source_priority=(self.search_priority_resolver() if callable(self.search_priority_resolver)
                                 else "auto"))
        if source.kind == "hot":
            configured = values.get("news_hot_sources", "")
            selected = tuple(item.strip().lower() for item in configured.split(",") if item.strip()) or self.hot_sources
            return HotTrendNewsPort(selected, values.get("news_hot_result_count", self.hot_result_count))
        auth = self.auth if source == self.default_source and self.default_source_auth else {}
        if source.kind == "rss":
            label = (DEFAULT_AI_NEWS_FEED_LABEL if source == self.default_source
                     and source.url == DEFAULT_AI_NEWS_FEED_URL else "")
            return NewsRssPort(url=source.url, source_label=label, **auth)
        return NewsJsonPort(url=source.url, **auth)


def configured_news_port(config=None, *, context=None, deepseek_provider_resolver=None,
                         search_priority_resolver=None):
    names = ("URL", "API_KEY", "AUTH_HEADER", "AUTH_SCHEME", "BILIBILI_COOKIE", "SEARCH_URL")
    values = {name: os.environ.get("COMPANION_V2_NEWS_" + name, "") for name in names}
    auth = {"api_key": values["API_KEY"], "auth_header": values["AUTH_HEADER"] or "Authorization",
            "auth_scheme": values["AUTH_SCHEME"] or "Bearer"}
    if config is None:
        if not any(values[name] for name in names if name != "BILIBILI_COOKIE"):
            return None
        if not values["URL"]:
            raise ValueError("news URL is required when news environment settings are present")
        return NewsJsonPort(url=values["URL"], **auth)
    settings = config.get("news", {}) or {}
    if type(settings) is not dict:
        raise ValueError("news host settings must be an object")
    configured_kind = settings.get("source_kind")
    # 缺键或空串都落到与 _conf_schema.json 相同的默认源（https://daily.juya.uk/rss.xml）。
    # 原先取 ""，于是 kind=rss 但没有 feed_url 的数据根会拿空地址去请求；
    # 只有 kind=host 那条分支有兜底，rss 这条没有。
    feed_url = settings.get("feed_url") or DEFAULT_AI_NEWS_FEED_URL
    if configured_kind is None and settings.get("feed_url"):
        source = NewsSource.read({"news_source_kind": "rss", "news_source_url": settings["feed_url"]})
    else:
        source = NewsSource.read({"news_source_kind": configured_kind or "host",
                                  "news_source_uid": settings.get("bilibili_uid", ""),
                                  "news_source_url": feed_url})
    if source.kind == "host" and values["URL"]:
        source = NewsSource("json", url=news_url(values["URL"]))
    elif source.kind == "host":
        source = NewsSource("rss", url=DEFAULT_AI_NEWS_FEED_URL)
    hot_sources = settings.get("hot_sources", "weibo,hackernews")
    if type(hot_sources) is not str:
        raise ValueError("news hot sources must be comma-separated text")
    default_source_auth = not (source.kind == "rss" and source.url == DEFAULT_AI_NEWS_FEED_URL)
    router = NewsSourceRouter(source, auth=auth, default_source_auth=default_source_auth,
                              bilibili_cookie=values["BILIBILI_COOKIE"],
                              search_url=settings.get("search_url") or values["SEARCH_URL"],
                              hot_sources=tuple(item.strip().lower() for item in hot_sources.split(",")),
                              hot_result_count=settings.get("hot_result_count", 10), host_context=context,
                              deepseek_provider_resolver=deepseek_provider_resolver,
                              search_priority_resolver=search_priority_resolver)
    router.for_settings({})
    return router
