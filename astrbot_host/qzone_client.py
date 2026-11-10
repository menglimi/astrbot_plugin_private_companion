"""QQ Zone HTTP transport and credential acquisition on the host side.

This module owns exactly two host concerns: turning the operator's manual cookie
or the connected OneBot session into one ``QzoneCredential``, and performing one
bounded HTTP call with that credential. It decides nothing about business
meaning -- whether an answer counts as a delivered like, a rejected publish or an
expired login stays with the Zone core, which also owns retries, backoff and
cooldowns. Nothing here retries, and no failure is ever reported as a partial
success.

Two properties are load-bearing and easy to break by accident:

* **One call, one session.** ``send`` reads the credential once and builds the
  cookie header plus the ``Referer`` from that same immutable snapshot, so a
  refresh happening in between cannot mix two sessions inside one request. The
  ``g_tk`` already sits in the request the core prepared, from that same snapshot.
* **A failed effectful request is unknown, not failed.** A timed-out POST may
  already have reached QQ: it is reported as ``delivery_unknown`` and must never
  be replayed, while a failed GET is simply retryable.

Logs carry an action name, a status and boolean flags only: no cookie value, no
request body, no feed text and no tid ever reaches them, and the page-facing
state masks the account id.
"""

import asyncio
import inspect
import logging
import re
import time

import aiohttp

from companion_v2.execution.models import TaskError
from companion_v2.qzone.protocol import (COOKIE_ACTIONS, COOKIE_DOMAINS, REFERER_ROOT, USER_AGENT,
                                         QzoneCredential, QzoneRequest, cookie_uin,
                                         credential_from_cookies, h5_index_request,
                                         has_identity_and_secret, parse_cookie_text,
                                         qzonetoken_from_index)
from companion_v2.qzone.transport import TransportOutcome


COOKIE_ACTION_TIMEOUT_SECONDS = 8.0
LOGIN_INFO_TIMEOUT_SECONDS = 5.0
# 页面拉状态是 15 秒超时（product-page.js 的 bridge_timeout）。探测按「2 个 action × 8 个
# domain × 2 种参数形状」逐个试，每次最多 8 秒——客户端一旦在 get_cookies 上挂住，整轮能拖到
# 几分钟，页面早在 15 秒就报了「读取超时」，凭据状态于是永远停在「尚未检查」。这里给整轮探测
# 一个总预算：到点就带着已经拿到的字段返回，让页面拿到「没有可用凭据」这个明确结论。
COOKIE_PROBE_BUDGET_SECONDS = 10.0
QZONETOKEN_TIMEOUT_SECONDS = 12.0
QZONETOKEN_TTL_SECONDS = 1800.0
MAX_TOKEN_ENTRIES = 8

SOURCE_NONE = "none"
SOURCE_MANUAL = "manual"
SOURCE_RUNTIME = "runtime"
STATE_UNKNOWN = "unknown"
STATE_READY = "ready"
STATE_FAILED = "failed"
STATE_MANUAL_FAILED = "manual_failed"

_MANUAL_INVALID = "手动 QZONE_COOKIE 不可用：需包含 uin/p_uin 与 p_skey 或 skey"
_NO_CONNECTION = "没有可用的 OneBot 连接，且未配置手动 Cookie"
_RUNTIME_MISSING = "OneBot 未能提供可用的 QQ 空间凭据：uin={uin}，skey={skey}，p_skey={p_skey}"

_LOGIN_KEYS = ("user_id", "uin", "qq", "self_id")
_RESULT_WRAPPERS = ("data", "result", "retdata", "payload", "response")

# Request headers the caller may not override: the session snapshot decides the
# cookie, and the extension always presents the one browser identity it was
# verified against.
_SESSION_HEADERS = ("cookie", "user-agent")
_FIXED_HEADERS = ("referer", "origin")

_ACTION_LABEL = re.compile(r"[^A-Za-z0-9_.:-]")
# Belt and braces for the one place free text enters this module: a caller-supplied
# failure reason must never be able to smuggle a cookie pair into a log or a page.
_COOKIE_PAIR = re.compile(r"(?i)\b(p_skey|pskey|skey2|skey|p_uin|uin|qzonetoken|pt4_token|pt_key"
                          r"|clientkey|superkey)\s*=\s*[^\s;,]+")


def _seconds(value, fallback):
    """A positive duration, or the caller's default -- never an unlimited one."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def _short(text, limit=160):
    """One bounded line with any cookie-looking pair redacted."""
    cleaned = _COOKIE_PAIR.sub(r"\1=***", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip()[:limit]


def _presence(value):
    return "有" if value else "无"


def _mask_uin(uin):
    text = str(int(uin or 0))
    if not text.strip("0"):
        return ""
    if len(text) <= 4:
        return "*" * len(text)
    return f"{text[:1]}{'*' * (len(text) - 3)}{text[-2:]}"


def _digits(value):
    """A positive account id from a reply field; anything else counts as absent."""
    text = str(value if value is not None else "").strip().lstrip("oO")
    return int(text) if text.isdigit() and int(text) > 0 else 0


def _login_uin(payload, depth=0):
    """Find the account id in a login-info reply without trusting its wrapper."""
    if depth > 4:
        return 0
    if isinstance(payload, dict):
        for key in _LOGIN_KEYS:
            found = _digits(payload.get(key))
            if found:
                return found
        for key in _RESULT_WRAPPERS:
            found = _login_uin(payload.get(key), depth + 1)
            if found:
                return found
        return 0
    if isinstance(payload, (list, tuple)):
        for item in payload[:8]:
            found = _login_uin(item, depth + 1)
            if found:
                return found
    return 0


def _cookie_candidates(result, depth=0):
    """Every place a OneBot reply may keep its cookie text, outermost first.

    ``get_cookies`` and ``get_credentials`` answer with the OneBot shape
    ``{"cookies": "<header text>", "csrf_token": "..."}``, and some builds wrap
    that again in ``data``. The protocol parser reads flat name/value replies, so
    the enclosing strings are offered to it as well; anything it does not
    recognize as cookie text parses to nothing and changes no field.
    """
    yield result
    if depth > 3:
        return
    if isinstance(result, dict):
        values = result.values()
    elif isinstance(result, (list, tuple)):
        values = result[:16]
    else:
        return
    for value in values:
        if type(value) is str:
            yield value
        elif isinstance(value, (dict, list, tuple)):
            yield from _cookie_candidates(value, depth + 1)


def _bot_invokers(bot, action, params):
    """The documented calling shapes of one OneBot action, in priority order.

    Each shape is bound through a default argument: several host generations
    expose the same concept under a different attribute, and a later lookup must
    not silently rewrite an earlier closure.
    """
    method = getattr(bot, action, None)
    if callable(method):
        yield lambda call=method: call(**params)
    api_call = getattr(getattr(bot, "api", None), "call_action", None)
    if callable(api_call):
        yield lambda call=api_call: call(action, **params)
    bot_call = getattr(bot, "call_action", None)
    if callable(bot_call):
        yield lambda call=bot_call: call(action, **params)


class QzoneHttpClient:
    """The host implementation of ``companion_v2.qzone.transport.QzoneTransport``."""

    def __init__(self, *, bot_provider=None, manual_cookie=None, logger=None,
                 credential_ttl_seconds=300, request_timeout_seconds=20.0):
        self._bot_provider = bot_provider
        self._manual_cookie = manual_cookie
        self._logger = logging.getLogger(__name__) if logger is None else logger
        self._credential_ttl = _seconds(credential_ttl_seconds, 300.0)
        self._request_timeout = _seconds(request_timeout_seconds, 20.0)
        self._credential = None
        self._credential_at = 0.0
        self._tokens = {}
        self._lock = asyncio.Lock()
        self._state = {"source": SOURCE_NONE, "state": STATE_UNKNOWN, "uin_masked": "", "reason": ""}

    # -- credential ------------------------------------------------------------------

    async def credential(self, *, refresh=False) -> QzoneCredential:
        """The current session: cached while fresh, re-acquired otherwise.

        One refresh runs at a time, so a burst of concurrent calls cannot turn into
        a burst of OneBot requests. A failed acquisition drops the previous
        snapshot: a session the host just refused to refresh must not come back
        through the next cache hit.
        """
        async with self._lock:
            if (not refresh and self._credential is not None
                    and self._credential_at + self._credential_ttl > time.monotonic()):
                return self._credential
            try:
                return await self._acquire()
            except BaseException:
                self._credential = None
                self._credential_at = 0.0
                raise

    async def _acquire(self) -> QzoneCredential:
        manual = self._manual_text()
        if manual:
            fields = parse_cookie_text(manual)
            if has_identity_and_secret(fields):
                return self._remember(fields, SOURCE_MANUAL)
            self._record(SOURCE_MANUAL, STATE_MANUAL_FAILED, fields, "手动 Cookie 缺少身份或会话密钥")
            self._note("credential.acquire", "invalid", manual=True)
            raise TaskError("qzone_credential_invalid", _MANUAL_INVALID)

        bot = self._connection()
        if bot is None:
            self._record(SOURCE_NONE, STATE_FAILED, {}, _NO_CONNECTION)
            self._note("credential.acquire", "missing", manual=False)
            raise TaskError("qzone_credential_missing", _NO_CONNECTION)

        fields = await self._runtime_cookies(bot)
        if not has_identity_and_secret(fields):
            uin, skey = bool(cookie_uin(fields)), bool(fields.get("skey") or fields.get("skey2"))
            p_skey = bool(fields.get("p_skey") or fields.get("pskey"))
            self._record(SOURCE_RUNTIME, STATE_FAILED, fields, "OneBot 未返回可用的身份或会话密钥")
            self._note("credential.acquire", "missing", manual=False, uin=uin, skey=skey, p_skey=p_skey)
            raise TaskError("qzone_credential_missing", _RUNTIME_MISSING.format(
                uin=_presence(uin), skey=_presence(skey), p_skey=_presence(p_skey)))
        return self._remember(fields, SOURCE_RUNTIME)

    def _manual_text(self):
        """The configured cookie, or an empty string when there is nothing to use."""
        manual = self._manual_cookie
        if callable(manual):
            try:
                manual = manual()
            except Exception:
                return ""
        return manual.strip() if type(manual) is str else ""

    def _connection(self):
        """The OneBot connection to ask, if the host still has one to offer."""
        provider = self._bot_provider
        if not callable(provider):
            return None
        try:
            return provider()
        except Exception:
            return None

    async def _runtime_cookies(self, bot):
        """Merge whatever the connected OneBot is willing to reveal about its session.

        Every documented action/domain/parameter shape is tried once, and the
        search stops the moment the merge carries both an identity and a secret.
        A shape mismatch or a refusal only moves the search on; none of it is
        re-raised, because one unsupported action says nothing about the next.

        The whole probe shares one budget (``COOKIE_PROBE_BUDGET_SECONDS``): a
        client that never answers must not keep the page waiting past its own
        timeout, so the last attempt only gets the time that is left.
        """
        fields = {}
        deadline = time.monotonic() + COOKIE_PROBE_BUDGET_SECONDS
        for action in COOKIE_ACTIONS:
            for domain in COOKIE_DOMAINS:
                for params in ({"domain": domain}, {}):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0.05:
                        self._note("credential.acquire", "budget", manual=False)
                        return fields
                    result = await self._bot_action(bot, action, params,
                                                    timeout=min(COOKIE_ACTION_TIMEOUT_SECONDS, remaining))
                    if result is not None:
                        for candidate in _cookie_candidates(result):
                            fields.update(parse_cookie_text(candidate))
                    if has_identity_and_secret(fields):
                        return fields
        if not cookie_uin(fields):
            remaining = deadline - time.monotonic()
            if remaining > 0.05:
                result = await self._bot_action(bot, "get_login_info", {},
                                                timeout=min(LOGIN_INFO_TIMEOUT_SECONDS, remaining))
                uin = _login_uin(result)
                if uin:
                    fields["uin"] = fields["p_uin"] = f"o{uin}"
        return fields

    @staticmethod
    async def _bot_action(bot, action, params, *, timeout):
        """Call one OneBot action through the first shape whose signature fits."""
        for invoke in _bot_invokers(bot, action, params):
            try:
                result = invoke()
                if not inspect.isawaitable(result):
                    return result
                return await asyncio.wait_for(result, timeout=timeout)
            except TypeError:
                continue
            except Exception:
                # A timeout or a refusal from OneBot itself: no answer here, and
                # nothing to raise over -- the caller keeps searching.
                return None
        return None

    def _remember(self, fields, source):
        credential = credential_from_cookies(fields, source=source)
        self._credential = credential
        self._credential_at = time.monotonic()
        self._state = {"source": source, "state": STATE_READY,
                       "uin_masked": _mask_uin(credential.uin), "reason": ""}
        self._note("credential.acquire", "ok", manual=(source == SOURCE_MANUAL))
        return credential

    def _record(self, source, state, fields, reason):
        uin = cookie_uin(fields)
        self._state = {"source": source, "state": state,
                       "uin_masked": _mask_uin(uin) if uin else "", "reason": _short(reason)}

    # -- requests --------------------------------------------------------------------

    async def send(self, request: QzoneRequest, *, timeout: float) -> TransportOutcome:
        """Perform exactly one request with one credential snapshot.

        A credential that cannot be acquired raises before anything is sent; a
        transport failure comes back as an outcome whose ``delivery_unknown``
        flag tells the caller whether replaying it is allowed.
        """
        credential = request.credential or await self.credential()
        outcome = await self._exchange(request, credential,
                                       timeout=_seconds(timeout, self._request_timeout))
        self._note(request.description, outcome.status, effectful=request.effectful,
                   retryable=outcome.retryable, delivery_unknown=outcome.delivery_unknown)
        return outcome

    async def _exchange(self, request, credential, timeout):
        """One bounded HTTP exchange; failures become outcomes, never exceptions."""
        headers = self._headers(request, credential)
        params = request.params or None
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout),
                                             trust_env=False) as session:
                if str(request.method).upper() == "GET":
                    call = session.get(request.url, params=params, headers=headers,
                                       allow_redirects=False)
                else:
                    call = session.post(request.url, params=params, data=request.data or None,
                                        headers=headers, allow_redirects=False)
                async with call as response:
                    return TransportOutcome(status=int(response.status), text=await response.text())
        except (asyncio.TimeoutError, aiohttp.ClientError, UnicodeError, OSError):
            return self._transport_failure(request)

    @staticmethod
    def _transport_failure(request):
        """A read can be repeated; a write that produced no answer cannot."""
        if request.effectful:
            return TransportOutcome(status=0, error="投递结果未知", retryable=False,
                                    delivery_unknown=True)
        return TransportOutcome(status=0, error="网络读取失败", retryable=True,
                                delivery_unknown=False)

    @staticmethod
    def _headers(request, credential):
        """The prepared request headers, signed with the snapshot that signs ``g_tk``."""
        extra = {}
        for key, value in (request.headers or {}).items():
            name = str(key).strip()
            if not name or value is None or name.lower() in _SESSION_HEADERS:
                continue
            extra[name] = str(value)
        referer = next((value for name, value in extra.items() if name.lower() == "referer"), "")
        headers = {"Cookie": credential.header, "User-Agent": USER_AGENT, "Origin": REFERER_ROOT,
                   "Referer": referer or credential.referer()}
        for name, value in extra.items():
            if name.lower() not in _FIXED_HEADERS:
                headers[name] = value
        return headers

    # -- H5 token --------------------------------------------------------------------

    async def qzonetoken(self, credential: QzoneCredential) -> str:
        """Best effort H5 token; callers still check the upload response."""
        try:
            uin = int(getattr(credential, "uin", 0) or 0)
        except (TypeError, ValueError):
            uin = 0
        if not uin:
            return ""
        cached = self._tokens.get(uin)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            outcome = await self._exchange(h5_index_request(credential), credential,
                                           QZONETOKEN_TIMEOUT_SECONDS)
            token = qzonetoken_from_index(outcome.text) if outcome.ok else ""
        except Exception:
            token = ""
        if token:
            self._remember_token(uin, token)
        self._note("credential.qzonetoken", "ok" if token else "missing")
        return token

    def _remember_token(self, uin, token):
        now = time.monotonic()
        self._tokens = {key: value for key, value in self._tokens.items() if value[0] > now}
        self._tokens[uin] = (now + QZONETOKEN_TTL_SECONDS, token)
        while len(self._tokens) > MAX_TOKEN_ENTRIES:
            self._tokens.pop(next(iter(self._tokens)))

    # -- state -----------------------------------------------------------------------

    def note_auth_failure(self, reason: str) -> None:
        """Remember why the last call was refused. This is a note, not a lock-out."""
        self._state = {"source": self._state.get("source") or SOURCE_NONE, "state": STATE_FAILED,
                       "uin_masked": self._state.get("uin_masked", ""), "reason": _short(reason)}
        self._note("credential.auth_failure", "noted")

    @property
    def last_credential_state(self) -> dict:
        """The page-facing conclusion: a source, a state, a masked id and a reason."""
        return dict(self._state)

    def _note(self, action, status, **flags):
        """Record one conclusion: an action, a status and boolean flags only."""
        if self._logger is None:
            return
        label = _ACTION_LABEL.sub("", str(action))[:40] or "request"
        details = "".join(f" {name}={value}" for name, value in flags.items())
        self._logger.info("V2 qzone %s: status=%s%s", label, status, details)


__all__ = ["QzoneHttpClient"]
