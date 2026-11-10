"""QQ Zone wire protocol: credentials, lenient JSON and the request shapes.

Pure functions only. Nothing here performs I/O, reads configuration or holds a
session: the host owns the HTTP transport, and this module only decides what a
request looks like and what a response means. The rules follow the two working
implementations this extension was written against -- one cookie header and its
matching ``g_tk`` per operation, nothing invented from a uin and a tid alone.
"""

from __future__ import annotations

import json
import base64
import logging
import re
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlsplit, unquote

from companion_v2.execution.models import TaskError
logger = logging.getLogger(__name__)


USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")
REFERER_ROOT = "https://user.qzone.qq.com"
H5_INDEX_URL = "https://h5.qzone.qq.com/mqzone/index"
FEED_LIST_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
FEED_LEGACY_URL = "https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more"
LIKE_URL = "https://user.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
LIKE_LIST_URL = "https://user.qzone.qq.com/proxy/domain/users.qzone.qq.com/cgi-bin/likes/get_like_list_app"
COMMENT_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
PUBLISH_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
IMAGE_UPLOAD_URL = "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image"

COOKIE_DOMAINS = ("user.qzone.qq.com", "qzone.qq.com", "h5.qzone.qq.com", "mobile.qzone.qq.com",
                  "taotao.qzone.qq.com", "qun.qzone.qq.com", "ti.qq.com", "qq.com")
COOKIE_ACTIONS = ("get_cookies", "get_credentials")
SECRET_KEYS = ("p_skey", "skey", "pskey", "skey2")
UIN_KEYS = ("uin", "p_uin", "ptui_loginuin", "luin")
FIELD_ALIASES = {"pskey": "p_skey", "p-skey": "p_skey", "bkn": "g_tk", "gtk": "g_tk",
                 "csrf-token": "csrf_token"}
COOKIE_KEY_HINTS = ("uin", "p_uin", "ptui_loginuin", "luin", "skey", "p_skey", "pskey", "skey2",
                    "g_tk", "gtk", "bkn", "qzonetoken", "pt4_token", "pt_key", "clientkey", "superkey")
AUTH_MARKERS = ("code=-3000", "请先登录", "未登录", "login", "cookie", "p_skey", "pskey", "skey",
                "g_tk", "gtk")
MAX_FEED_TEXT = 400
MAX_COMMENT_CHARS = 120
MAX_PUBLISH_CHARS = 300


def gtk(secret):
    """The QQ ``bkn``: a 31-bit DJB-style hash of the session secret."""
    if type(secret) is not str or not secret:
        raise TaskError("qzone_credential_missing", "QQ Zone needs a session secret to sign requests")
    value = 5381
    for character in secret:
        value += (value << 5) + ord(character)
    return value & 0x7FFFFFFF


def parse_cookie_text(raw):
    """Read a cookie header, JSON blob or key/value list without trusting its shape."""
    fields = {}
    for part in _cookie_fragments(raw):
        for key, value in part.items():
            normalized = _normalize_name(key)
            if normalized and value:
                fields[normalized] = str(value).strip().strip('"')
    return normalize_cookie_fields(fields)


def _cookie_fragments(raw, depth=0, seen=None):
    if depth > 8:
        return
    seen = set() if seen is None else seen
    if raw is None or id(raw) in seen:
        return
    seen.add(id(raw))
    if isinstance(raw, bytes):
        yield from _cookie_fragments(raw.decode("utf-8", "replace"), depth + 1, seen)
        return
    if isinstance(raw, dict):
        pair = {str(raw.get("name") or "").lower(): raw.get("value")}
        if pair and list(pair)[0] in COOKIE_KEY_HINTS:
            yield pair
            return
        for key, value in raw.items():
            lowered = str(key).lower()
            if lowered in COOKIE_KEY_HINTS and isinstance(value, (str, int)):
                yield {lowered: value}
            elif isinstance(value, (dict, list, tuple)):
                yield from _cookie_fragments(value, depth + 1, seen)
            elif isinstance(value, str) and "=" in value:
                # OneBot answers wrap the header in a plain field, e.g.
                # {"cookies": "uin=...; p_skey=..."}; anything that is not a
                # cookie header parses to nothing, so descending is safe.
                yield from _cookie_fragments(value, depth + 1, seen)
        return
    if isinstance(raw, (list, tuple)):
        for item in raw:
            yield from _cookie_fragments(item, depth + 1, seen)
        return
    text = str(raw).strip()
    if not text:
        return
    if text[:1] in "[{":
        try:
            yield from _cookie_fragments(json.loads(text), depth + 1, seen)
            return
        except ValueError:
            pass
    if "=" not in text:
        return
    if not re.search(r"\b(uin|p_uin|skey|p_skey|pskey|g_tk|gtk|bkn)\s*=", text):
        return
    yield from _split_cookie_pairs(text)


def _split_cookie_pairs(text):
    cleaned = re.sub(r"^\s*cookie\s*:\s*", "", text, flags=re.IGNORECASE)
    cleaned = cleaned.replace("\r", ";").replace("\n", ";")
    try:
        jar = SimpleCookie()
        jar.load(cleaned)
        if jar:
            yield {key: morsel.value for key, morsel in jar.items()}
            return
    except Exception as error:
        logger.warning("V2 qzone cookie header fell back to manual split: %s", type(error).__name__)
    fields = {}
    for chunk in cleaned.split(";"):
        if "=" in chunk:
            key, _, value = chunk.partition("=")
            fields[key.strip()] = value.strip()
    if fields:
        yield fields


def _normalize_name(name):
    return FIELD_ALIASES.get(str(name).strip().lower(), str(name).strip().lower())


def normalize_cookie_fields(fields):
    normalized = {_normalize_name(key): str(value).strip().strip('"')
                  for key, value in dict(fields or {}).items() if str(key).strip()}
    if normalized.get("uin") and not normalized.get("p_uin"):
        normalized["p_uin"] = normalized["uin"]
    if normalized.get("p_uin") and not normalized.get("uin"):
        normalized["uin"] = normalized["p_uin"]
    return normalized


def cookie_uin(fields):
    for key in UIN_KEYS:
        value = str((fields or {}).get(key) or "").strip().lstrip("oO")
        if value.isdigit() and int(value) > 0:
            return int(value)
    return 0


def has_identity_and_secret(fields):
    return bool(cookie_uin(fields) and any((fields or {}).get(key) for key in SECRET_KEYS))


def cookie_header(fields):
    return "; ".join(f"{key}={value}" for key, value in (fields or {}).items() if value)


@dataclass(frozen=True)
class QzoneCredential:
    """One immutable credential snapshot; a request never re-reads the session."""

    uin: int
    secret: str
    g_tk: int
    qzonetoken: str
    header: str
    source: str

    def referer(self, suffix=""):
        return f"{REFERER_ROOT}/{self.uin}{suffix}"


def credential_from_cookies(fields, *, qzonetoken="", source="unknown"):
    fields = normalize_cookie_fields(fields)
    uin = cookie_uin(fields)
    if not uin:
        raise TaskError("qzone_credential_invalid", "QQ Zone cookie has no usable uin")
    secret = next((str(fields[key]) for key in SECRET_KEYS if fields.get(key)), "")
    if secret:
        signed = gtk(secret)
    else:
        stored = str(fields.get("g_tk") or "").strip()
        if not stored.isdigit():
            raise TaskError("qzone_credential_invalid", "QQ Zone cookie has no p_skey/skey to sign with")
        signed = int(stored)
    token = str(qzonetoken or fields.get("qzonetoken") or "").strip()
    enriched = dict(fields)
    enriched["uin"] = f"o{uin}"
    enriched["p_uin"] = f"o{uin}"
    if token:
        enriched["qzonetoken"] = token
    return QzoneCredential(uin=uin, secret=secret, g_tk=signed, qzonetoken=token,
                           header=cookie_header(enriched), source=source)


def response_code(payload):
    """Return an explicit failure code even when a nested payload hides it."""
    if not isinstance(payload, dict):
        return 0
    for key in ("code", "ret", "_raw_code"):
        value = payload.get(key)
        if value is not None and value not in ("", 0, "0"):
            return value
    return 0


def looks_like_auth_failure(*messages):
    text = " ".join(str(item or "") for item in messages).lower()
    return bool(text) and any(marker in text for marker in AUTH_MARKERS)


def missing_credential_reason(reason):
    """A missing connection is recoverable; a broken login has to cool down."""
    return any(marker in str(reason or "") for marker in
               ("没有可用的 OneBot 连接", "未配置", "qzonetoken"))


_QZONETOKEN = re.compile(r"""window\.shine0callback.*?return\s+["']([0-9a-zA-Z_-]+?)["']\s*;""", re.S)


def qzonetoken_from_index(html):
    match = _QZONETOKEN.search(str(html or ""))
    return match.group(1) if match else ""


def unbalanced_tail_error(text):
    return "接口响应缺少 JSON" if not str(text or "").strip() else ""


def _skip_ignored(text, index):
    while index < len(text):
        if text[index].isspace():
            index += 1
        elif text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end < 0 else end + 1
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
        else:
            break
    return index


def _quoted(text, index):
    quote = text[index]
    index += 1
    out = []
    while index < len(text):
        character = text[index]
        if character == "\\":
            index += 1
            if index >= len(text):
                break
            escape = text[index]
            if escape == "x" and index + 2 < len(text):
                out.append(chr(int(text[index + 1:index + 3], 16)))
                index += 3
                continue
            if escape == "u" and index + 4 < len(text):
                code = int(text[index + 1:index + 5], 16)
                index += 5
                if 0xD800 <= code <= 0xDBFF and text.startswith("\\u", index) and index + 6 <= len(text):
                    low = int(text[index + 2:index + 6], 16)
                    if 0xDC00 <= low <= 0xDFFF:
                        out.append(chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)))
                        index += 6
                        continue
                out.append(chr(code))
                continue
            mapping = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "0": "\0"}
            out.append(mapping.get(escape, escape))
            index += 1
            continue
        if character == quote:
            return "".join(out), index + 1
        if character == "\n":
            index += 1
            continue
        out.append(character)
        index += 1
    raise ValueError("unterminated string")


def _js_object_to_json(text):
    """Normalize the JavaScript-ish bodies QQ Zone returns into strict JSON."""
    out = []
    index = 0
    length = len(text)
    while index < length:
        index = _skip_ignored(text, index)
        if index >= length:
            break
        character = text[index]
        if character in "\"'":
            value, index = _quoted(text, index)
            out.append(json.dumps(value, ensure_ascii=True))
            continue
        if character == ",":
            probe = _skip_ignored(text, index + 1)
            if probe < length and text[probe] in "}]":
                index += 1
                continue
        if character == ":":
            probe = _skip_ignored(text, index + 1)
            if probe < length and text[probe] in ",}]":
                out.append("null")
                index += 1
                continue
        word = re.match(r"[A-Za-z_$][A-Za-z0-9_$]*", text[index:])
        if word:
            token = word.group(0)
            probe = _skip_ignored(text, index + len(token))
            if probe < length and text[probe] == ":":
                out.append(json.dumps(token, ensure_ascii=True))
            elif token in ("undefined", "NaN", "Infinity"):
                out.append("null")
            else:
                out.append("null")
            index += len(token)
            continue
        out.append(character)
        index += 1
    return "".join(out)


def load_lenient_json(text):
    source = str(text or "")
    try:
        return json.loads(source, parse_constant=lambda _value: None)
    except ValueError:
        pass
    try:
        return json.loads(_js_object_to_json(source), parse_constant=lambda _value: None)
    except ValueError as error:
        raise TaskError("qzone_response_invalid", "QQ Zone response is not readable") from error


def balanced_objects(text, *, limit=24):
    """Extract balanced ``{...}`` candidates; responses often append live script."""
    source = str(text or "")
    found = []
    index = 0
    while index < len(source) and len(found) < limit:
        start = source.find("{", index)
        if start < 0:
            break
        depth = 0
        quote = ""
        position = start
        while position < len(source):
            character = source[position]
            if quote:
                if character == "\\":
                    position += 2
                    continue
                if character == quote:
                    quote = ""
            elif character in "\"'":
                quote = character
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    found.append(source[start:position + 1])
                    index = position + 1
                    break
            position += 1
        else:
            break
    return found


RESPONSE_KEYS = ("code", "ret", "message", "msg", "data", "subcode")


def parse_response(text):
    """Return the most plausible response object, with its original code kept."""
    candidates = balanced_objects(text) or ([str(text)] if str(text or "").strip() else [])
    fallback = None
    for candidate in candidates:
        try:
            payload = load_lenient_json(candidate)
        except TaskError:
            continue
        if not isinstance(payload, dict):
            continue
        if any(key in payload for key in RESPONSE_KEYS):
            return _normalize(payload)
        fallback = fallback or payload
    if fallback is not None:
        return _normalize(fallback)
    if not str(text or "").strip():
        return {"code": -1, "message": "接口返回空响应", "_raw_code": -1}
    raise TaskError("qzone_response_invalid", "QQ Zone response contains no readable object")


def _normalize(payload):
    data = payload.get("data")
    if isinstance(data, dict) and not any(key in data for key in RESPONSE_KEYS):
        merged = {**data, "code": payload.get("code"), "message": payload.get("message") or payload.get("msg"),
                  "_raw_code": payload.get("code"), "_raw_message": payload.get("message") or payload.get("msg")}
        return merged
    result = dict(payload)
    result.setdefault("_raw_code", payload.get("code"))
    result.setdefault("_raw_message", payload.get("message") or payload.get("msg"))
    return result


@dataclass(frozen=True)
class QzoneRequest:
    """One prepared call: the host adds the bounded session and the time budget."""

    method: str
    url: str
    params: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    description: str = ""
    credential: QzoneCredential | None = field(default=None, repr=False, compare=False)

    @property
    def effectful(self):
        return self.method.upper() not in ("GET", "HEAD")


def _form_headers(referer, *, ajax=False):
    headers = {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
               "Referer": referer, "Origin": REFERER_ROOT}
    if ajax:
        headers["X-Requested-With"] = "XMLHttpRequest"
    return headers


def feed_request(credential, *, uin=0, pos=0, num=10, detail=False):
    target = int(uin or credential.uin)
    params = {"g_tk": credential.g_tk, "uin": target, "ftype": 0, "sort": 0, "pos": max(0, int(pos)),
              "num": max(1, int(num)), "replynum": 100, "callback": "_preloadCallback",
              "code_version": 1, "format": "json"}
    if detail:
        params["need_comment"] = 1
        params["need_private_comment"] = 1
    return QzoneRequest("GET", FEED_LIST_URL, params=params, headers={"Referer": credential.referer()},
                        description="feed.list", credential=credential)


def friends_feed_request(credential, *, page=1, count=10):
    """The legacy friend timeline; the H5 index covers page one with richer fields."""
    return QzoneRequest("GET", FEED_LEGACY_URL, params={
        "uin": credential.uin, "scope": 0, "view": 1, "filter": "all", "flag": 1, "applist": "all",
        "pagenum": max(1, int(page)), "count": max(1, int(count)), "refresh": 0, "aisortEndTime": 0,
        "aisortOffset": 0, "getAisort": 0, "aisortBeginTime": 0, "begintime": 0, "format": "json",
        "g_tk": credential.g_tk, "useutf8": 1, "outputhtmlfeed": 1,
        **({"qzonetoken": credential.qzonetoken} if credential.qzonetoken else {}),
    }, headers={"Referer": credential.referer()}, description="feed.friends")


def h5_index_request(credential):
    return QzoneRequest("GET", H5_INDEX_URL, headers={
        "Referer": credential.referer(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }, description="credential.qzonetoken")


def post_like_key(post):
    """Keep QQ's original ticket, or use its canonical shuoshuo resource key."""
    return post.unikey or post.curkey or f"http://user.qzone.qq.com/{post.author_uin}/mood/{post.post_id}.1"


def like_list_request(credential, post):
    return QzoneRequest("GET", LIKE_LIST_URL, params={
        "uin": credential.uin, "unikey": post_like_key(post), "begin_uin": 0,
        "query_count": 60, "if_first_page": 1, "g_tk": credential.g_tk,
    }, headers={"Referer": f"{REFERER_ROOT}/{post.author_uin}/mood/{post.post_id}"},
        description="post.likes", credential=credential)


def like_list_has_self(payload, uin):
    """Only the caller's explicit flag or roster entry proves an existing like."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    for key in ("is_dolike", "isliked", "is_like", "if_like", "selflike"):
        if data.get(key) in (1, "1", True, "true"):
            return True
    members = data.get("like_uin_info")
    return isinstance(members, list) and any(
        isinstance(member, dict) and str(member.get("uin") or "").lstrip("oO") == str(uin)
        for member in members)


def like_request(credential, post):
    """Like one post using the tickets that came with the scan, never a guess."""
    reference = f"{REFERER_ROOT}/{post.author_uin}/mood/{post.post_id}"
    data = {"qzreferrer": credential.referer(), "opuin": credential.uin,
            "unikey": post_like_key(post), "curkey": post.curkey or post_like_key(post),
            "appid": post.appid or "311", "from": 1, "typeid": post.typeid or "0",
            "abstime": post.abstime or "0", "fid": post.fid or post.post_id, "active": 0,
            "format": "json", "fupdate": 1}
    return QzoneRequest("POST", LIKE_URL, params={"g_tk": credential.g_tk}, data=data,
                        headers=_form_headers(reference), description="post.like", credential=credential)


def comment_request(credential, post, text):
    data = {"topicId": f"{post.author_uin}_{post.post_id}__1", "uin": credential.uin,
            "hostUin": post.author_uin, "feedsType": 100, "inCharset": "utf-8", "outCharset": "utf-8",
            "plat": "qzone", "source": "ic", "platformid": 52, "format": "fs", "ref": "feeds",
            "content": text}
    reference = f"{REFERER_ROOT}/{post.author_uin}/mood/{post.post_id}"
    return QzoneRequest("POST", COMMENT_URL, params={"g_tk": credential.g_tk}, data=data,
                        headers=_form_headers(reference), description="post.comment", credential=credential)


def upload_image_request(credential, image, *, qzonetoken=""):
    """QQ's shuoshuo album upload; bytes are sent once as a base64 form field."""
    fields = parse_cookie_text(credential.header)
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}[image.mime_type]
    data = {"filename": f"{image.content_hash[:16]}.{extension}", "uin": credential.uin,
            "skey": fields.get("skey") or credential.secret,
            "p_skey": fields.get("p_skey") or credential.secret,
            "zzpaneluin": credential.uin, "p_uin": credential.uin,
            "qzonetoken": qzonetoken or credential.qzonetoken, "zzpanelkey": "",
            "uploadtype": "1", "albumtype": "7", "exttype": "0", "refer": "shuoshuo",
            "output_type": "json", "charset": "utf-8", "output_charset": "utf-8",
            "upload_hd": "1", "hd_width": "2048", "hd_height": "10000", "hd_quality": "96",
            "url": f"{IMAGE_UPLOAD_URL}?g_tk={credential.g_tk}", "base64": "1",
            "picfile": base64.b64encode(image.content).decode("ascii"),
            "qzreferrer": credential.referer()}
    return QzoneRequest("POST", IMAGE_UPLOAD_URL, params={"g_tk": credential.g_tk}, data=data,
        headers=_form_headers(credential.referer(), ajax=True), description="image.upload", credential=credential)


@dataclass(frozen=True)
class UploadedImage:
    richval: str
    pic_bo: str
    lloc: str
    sloc: str


def image_location_ids(value):
    """QQ's /psc?/album/photo/b stores its photo path inside the query string."""
    value = str(value or "").strip()
    if not value:
        return ()
    parsed = urlsplit(value)
    if parsed.scheme or value.startswith("/"):
        route = parsed.query.split("&", 1)[0] if parsed.query.startswith("/") else parsed.path
        parts = [part for part in unquote(route).split("/") if part]
        if len(parts) >= 3 and parts[0].startswith("V"):
            return (parts[1],)
        if parts and parts[-1] in ("b", "m", "s", "l", "0"):
            parts.pop()
        return (parts[-1],) if parts else ()
    return (value,)


def uploaded_image(payload):
    """Require the attachment receipt; HTTP success alone cannot attach a photo."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    def value(*keys):
        return next((str(data[key]).strip() for key in keys if data.get(key) not in (None, "")), "")
    album, lloc, sloc = value("albumid", "album_id"), value("lloc"), value("sloc")
    bo = value("pic_bo", "picbo", "bo")
    if not bo:
        query = parse_qs(urlsplit(value("url", "origin_url", "pre", "raw_url")).query)
        bo = next((query[key][0] for key in ("bo", "pic_bo", "picbo") if query.get(key)), "")
    width, height = value("width", "w"), value("height", "h")
    if (not all((album, lloc, sloc, bo)) or not width.isdigit() or not height.isdigit()
            or int(width) < 1 or int(height) < 1
            or any(char in album + lloc + sloc for char in ",\t\r\n")):
        raise TaskError("qzone_image_upload_failed", "图片上传回执缺少附件标识，未发布说说")
    richval = ",".join(("", album, lloc, sloc, value("type", "phototype", "image_type") or "1",
                        height, width, "", height, width))
    return UploadedImage(richval, bo, lloc, sloc)


def publish_request(credential, text, images=()):
    data = {"syn_tweet_verson": 1, "paramstr": 1, "who": 1, "con": text, "feedversion": 1, "ver": 1,
            "ugc_right": 1, "to_sign": 0, "hostuin": credential.uin, "code_version": 1,
            "issyncweibo": 0, "format": "json", "qzreferrer": credential.referer()}
    if images:
        data.update(richtype="1", subrichtype="1", richval="\t".join(image.richval for image in images),
                    pic_bo=",".join(image.pic_bo for image in images), pic_template="")
        data["con"] = text or " "
    return QzoneRequest("POST", PUBLISH_URL, params={"g_tk": credential.g_tk}, data=data,
                        headers=_form_headers(credential.referer(), ajax=True), description="post.publish",
                        credential=credential)


def clean_text(value, *, limit, field="text"):
    """One line of plain text, no control characters, bounded before it is sent."""
    if type(value) is not str:
        raise TaskError("qzone_text_invalid", f"{field} must be text")
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        raise TaskError("qzone_text_invalid", f"{field} must not be empty")
    if any(ord(character) < 32 or 0xD800 <= ord(character) <= 0xDFFF for character in text):
        raise TaskError("qzone_text_invalid", f"{field} must not contain control characters")
    if len(text) > limit:
        raise TaskError("qzone_text_invalid", f"{field} must stay within {limit} characters")
    return text


__all__ = ["AUTH_MARKERS", "COMMENT_URL", "COOKIE_ACTIONS", "COOKIE_DOMAINS", "FEED_LEGACY_URL",
           "FEED_LIST_URL", "H5_INDEX_URL", "LIKE_URL", "MAX_COMMENT_CHARS", "MAX_FEED_TEXT",
           "MAX_PUBLISH_CHARS", "PUBLISH_URL", "QzoneCredential", "QzoneRequest", "REFERER_ROOT",
           "SECRET_KEYS", "USER_AGENT", "balanced_objects", "clean_text", "comment_request",
           "cookie_header", "cookie_uin", "credential_from_cookies", "feed_request",
           "friends_feed_request", "gtk", "h5_index_request", "has_identity_and_secret",
           "like_request", "load_lenient_json", "looks_like_auth_failure",
           "missing_credential_reason", "normalize_cookie_fields", "parse_cookie_text",
           "parse_response", "publish_request", "qzonetoken_from_index", "response_code"]
