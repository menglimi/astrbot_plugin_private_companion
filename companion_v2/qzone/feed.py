"""Turn a Zone answer into bounded posts, without trusting its shape.

Two answer families exist: the JSON ``msglist`` of one person's timeline and the
H5/legacy friend timeline, which arrives as HTML with the tickets in ``data-*``
attributes. Both are read here into the same record, and anything without an
author, an identity or any text/image content is dropped instead of being guessed at.
"""

from __future__ import annotations

import html as html_module
import json
import re
from urllib.parse import urlsplit

from companion_v2.execution.models import TaskError
from companion_v2.qzone.protocol import MAX_FEED_TEXT, response_code, image_location_ids
from companion_v2.qzone.references import QzonePost, QzonePhoto


MAX_COMMENTS = 30
MAX_COMMENT_CHARS = 180
_OFFICIAL_AUTHORS = ("官方qzone", "qzone官方")
_ATTRIBUTE = re.compile(r'data-(uin|key|tid|nickname|unikey|curkey|appid|typeid|abstime|fid)="([^"]{0,300})"',
                        re.I)
_ANCHOR_AUTHOR = re.compile(r'data-uin="', re.I)
_ANCHOR_KEY = re.compile(r'data-key="', re.I)
_INFO_BLOCK = re.compile(r'class="[^"]*(?:f-info|txt-box|feed-text)[^"]*"[^>]*>(.{0,4000}?)(?=<(?:div|p|ul|li|span class="f-|/))',
                         re.I | re.S)
_TAG = re.compile(r"<[^>]{0,400}>")
_IMAGE_PLACEHOLDER = "qzonestyle.gtimg.cn"
_UIN_KEYS = ("uin", "user_uin", "qq", "uin_str", "hostuin", "opuin")
_NAME_KEYS = ("name", "nickname", "nick", "user_name", "username")
_TEXT_KEYS = ("content", "con", "summary", "text", "msg")
_TIME_KEYS = ("created_time", "create_time", "abstime", "time", "timestamp", "pubtime")
_LIKED_KEYS = ("liked", "isliked", "hasliked", "liked_by_me", "selflike")


def plain_text(value, *, limit=MAX_FEED_TEXT):
    """HTML or JSON text collapsed to one bounded line."""
    if value is None:
        return ""
    text = str(value)
    if "<" in text and ">" in text:
        text = _TAG.sub(" ", text)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def display_name(name, uin):
    """A missing nickname must not put a whole account number into prompts or pages."""
    text = plain_text(name, limit=60)
    if text:
        return text
    digits = str(uin or "").strip()
    if not digits:
        return "QQ空间用户"
    if len(digits) <= 4:
        return digits
    return digits[:1] + "*" * (len(digits) - 3) + digits[-2:]


def _int_text(value):
    text = str(value or "").strip().lstrip("oO")
    return text if text.isdigit() else ""


def _first(mapping, keys, *, nested=()):
    """Read the first present key, looking one level into nested objects too."""
    if not isinstance(mapping, dict):
        return None
    lowered = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        value = lowered.get(key)
        if value not in (None, "", [], {}):
            return value
    for container in nested:
        child = lowered.get(container)
        if isinstance(child, dict):
            found = _first(child, keys, nested=nested)
            if found not in (None, "", [], {}):
                return found
        elif isinstance(child, (list, tuple)):
            for item in child:
                found = _first(item, keys, nested=nested)
                if found not in (None, "", [], {}):
                    return found
    return None


def image_url(value):
    value = html_module.unescape(str(value or "").strip())
    if value.startswith("//"):
        value = "https:" + value
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username
                or parsed.password or len(value) > 4096 or any(ord(c) < 32 for c in value)):
            return ""
    except ValueError:
        return ""
    return value


def photo_from_mapping(value):
    if isinstance(value, str):
        value = {"url": value}
    if not isinstance(value, dict):
        return None
    candidates = [value.get(key) for key in ("url3", "origin_url", "raw_url", "url2", "url1", "url", "pic_url", "src")]
    candidates += [child.get("url") for child in value.values() if isinstance(child, dict)]
    urls = tuple(url for candidate in candidates if (url := image_url(candidate)))
    if not urls:
        return None
    identities = {token for key in ("lloc", "sloc", "pic_id", "picid")
                  for token in image_location_ids(value.get(key))}
    for url in urls:
        identities.update(image_location_ids(url))
    def dimension(key):
        raw = str(value.get(key) or 0)
        return int(raw) if raw.isdigit() and len(raw) < 8 else 0
    return QzonePhoto(urls[0], dimension("width"), dimension("height"), tuple(sorted(identities)))


def images_from_mapping(item):
    entries = []
    for container in (item, item.get("raw") if isinstance(item.get("raw"), dict) else {}):
        for key in ("pic", "pics", "images", "photos", "pic_list"):
            raw = container.get(key)
            if isinstance(raw, dict):
                raw = [raw] if any(k in raw for k in ("url", "url1", "url2", "url3")) else list(raw.values())
            if isinstance(raw, list):
                entries.extend(raw)
    found, seen = [], set()
    for entry in entries:
        photo = photo_from_mapping(entry)
        if photo and photo.url not in seen:
            found.append(photo)
            seen.add(photo.url)
            if len(found) == 9:
                break
    return tuple(found)


def _post_from_mapping(item, *, my_uin):
    if not isinstance(item, dict):
        return None
    post_id = str(_first(item, ("tid", "id", "cellid", "topicid", "post_id")) or "").strip()
    author = _int_text(_first(item, _UIN_KEYS, nested=("userinfo", "user", "comm", "raw")))
    if not post_id or not author:
        return None
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else item
    text = plain_text(_first(item, _TEXT_KEYS, nested=("raw", "summary")))
    images = images_from_mapping(item)
    if not text and not images:
        return None
    name = display_name(_first(item, _NAME_KEYS, nested=("userinfo", "user", "raw")), author)
    return QzonePost(post_id=post_id, author_uin=author, author_name=name, excerpt=text,
        unikey=str(_first(raw, ("unikey", "uni_key")) or ""), curkey=str(_first(raw, ("curkey", "cur_key")) or ""),
        appid=str(_first(raw, ("appid", "app_id")) or ""), typeid=str(_first(raw, ("typeid", "type_id")) or ""),
        abstime=str(_first(item, _TIME_KEYS, nested=("raw",)) or ""),
        fid=str(_first(raw, ("fid", "feeds_key")) or ""),
        created_at=str(_first(item, _TIME_KEYS) or "")[:40],
        liked=bool(_first(item, _LIKED_KEYS, nested=("like", "raw")) in (1, "1", True, "true")),
        own=author == str(my_uin), images=images)


def posts_from_msglist(payload, *, my_uin, limit):
    """One person's timeline: the documented ``msglist`` answer."""
    if not isinstance(payload, dict):
        raise TaskError("qzone_response_invalid", "QQ Zone timeline answer is not an object")
    code = response_code(payload)
    if code not in (0, "0"):
        raise TaskError("qzone_operation_failed", str(payload.get("message") or payload.get("msg") or code)[:200])
    entries = payload.get("msglist")
    if not isinstance(entries, list):
        return ()
    posts, seen = [], set()
    for entry in entries:
        post = _post_from_mapping(entry, my_uin=my_uin)
        if post is None or post.post_id in seen:
            continue
        if post.author_name.strip().casefold() in _OFFICIAL_AUTHORS:
            continue
        seen.add(post.post_id)
        posts.append(post)
        if len(posts) >= limit:
            break
    return tuple(posts)


def posts_from_html(text, *, my_uin, limit):
    """The H5/legacy friend timeline: tickets live in ``data-*`` attributes.

    Feed blocks are sliced on their own attribute anchors rather than on a fixed
    DOM shape, because the page markup changes far more often than the tickets it
    carries. A block without author, identity or text is dropped.
    """
    source = str(text or "")
    # One feed element carries exactly one author attribute, so slicing on the
    # author (or, when absent, on the post key) keeps its tickets in one block.
    anchors = [match.start() for match in _ANCHOR_AUTHOR.finditer(source)]
    if not anchors:
        anchors = [match.start() for match in _ANCHOR_KEY.finditer(source)]
    if not anchors:
        return ()
    bounds = [*anchors, len(source)]
    posts, seen = [], set()
    for start, end in zip(bounds, bounds[1:]):
        block = source[start:end]
        attributes = {name.lower(): value for name, value in _ATTRIBUTE.findall(block)}
        post_id = str(attributes.get("key") or attributes.get("tid") or "").strip()
        author = _int_text(attributes.get("uin"))
        if not post_id or not author:
            continue
        info = _INFO_BLOCK.search(block)
        excerpt = plain_text(info.group(1) if info else block)
        images = tuple(photo_from_mapping(url) for url in image_urls_from_html(block))
        if not excerpt and not images:
            continue
        name = display_name(attributes.get("nickname"), author)
        if name.strip().casefold() in _OFFICIAL_AUTHORS:
            continue
        key = (author, post_id)
        if key in seen:
            continue
        seen.add(key)
        posts.append(QzonePost(post_id=post_id, author_uin=author, author_name=name, excerpt=excerpt,
            unikey=str(attributes.get("unikey") or ""), curkey=str(attributes.get("curkey") or ""),
            appid=str(attributes.get("appid") or ""), typeid=str(attributes.get("typeid") or ""),
            abstime=str(attributes.get("abstime") or ""), fid=str(attributes.get("fid") or ""),
            own=author == str(my_uin), images=images))
        if len(posts) >= limit:
            break
    return tuple(posts)


def posts_from_legacy_feed(payload, *, my_uin, limit):
    """The legacy friend timeline answers with HTML wrapped inside JSON."""
    if not isinstance(payload, dict):
        return ()
    code = response_code(payload)
    if code not in (0, "0"):
        raise TaskError("qzone_operation_failed", str(payload.get("message") or payload.get("msg") or code)[:200])
    for key in ("data", "html", "feeds"):
        value = payload.get(key)
        if isinstance(value, str) and "data-" in value:
            return posts_from_html(value, my_uin=my_uin, limit=limit)
    return ()


def comments_from_mapping(item, *, limit=MAX_COMMENTS):
    """Bounded comments of one timeline entry, oldest first, as the model may read them."""
    if not isinstance(item, dict):
        return ()
    collected = []
    for key in ("commentlist", "comments", "comment", "reply", "replies", "private_commentlist"):
        value = item.get(key)
        if isinstance(value, list):
            collected.extend(value)
        elif isinstance(value, dict):
            collected.extend(list(value.values()))
    found, seen = [], set()
    for entry in collected:
        if not isinstance(entry, dict):
            continue
        text = plain_text(_first(entry, _TEXT_KEYS), limit=MAX_COMMENT_CHARS)
        if not text:
            continue
        author = _int_text(_first(entry, _UIN_KEYS)) or "0"
        name = display_name(_first(entry, _NAME_KEYS), author)
        created = str(_first(entry, _TIME_KEYS) or "")[:40]
        fingerprint = (author, text, created)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        found.append({"author_uin": author, "author": name, "text": text, "at": created})
        if len(found) >= limit:
            break
    return tuple(found)


def msglist_entry(payload, post_id):
    """The original entry of one timeline id, so its comments can be read."""
    if not isinstance(payload, dict) or not isinstance(payload.get("msglist"), list):
        return None
    for entry in payload["msglist"]:
        if isinstance(entry, dict) and str(_first(entry, ("tid", "id", "cellid")) or "") == str(post_id):
            return entry
    return None


def decode_payload(text):
    from companion_v2.qzone.protocol import parse_response
    return parse_response(text)


def image_urls_from_html(text, *, limit=9):
    """Original image URLs of one feed block, with QQ's placeholder art removed."""
    found = []
    for match in re.finditer(r'<img[^>]{0,2000}?>', str(text or ""), re.I):
        tag = match.group(0)
        if re.search(r'''(?:class|id)=["'][^"']*(?:avatar|head-pic|emoji|emoticon)''', tag, re.I):
            continue
        source = ""
        for attribute in ("data-original", "data-original-url", "data-bigurl", "data-rawurl", "origsrc",
                          "original", "data-url", "data-src", "src"):
            hit = re.search(r'(?<![\w-])' + attribute + r'''\s*=\s*(["'])(.{0,4096}?)\1''', tag, re.I)
            if hit and image_url(hit.group(2)):
                source = image_url(hit.group(2))
                break
        if not source or _IMAGE_PLACEHOLDER in source or source in found:
            continue
        host = urlsplit(source).hostname or ""
        if host == "qlogo.cn" or host.endswith(".qlogo.cn"):
            continue
        found.append(source)
        if len(found) >= limit:
            break
    return tuple(found)


def json_dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


__all__ = ["MAX_COMMENTS", "comments_from_mapping", "decode_payload", "display_name", "image_urls_from_html",
           "json_dumps", "msglist_entry", "plain_text", "posts_from_html", "posts_from_legacy_feed",
           "posts_from_msglist"]
