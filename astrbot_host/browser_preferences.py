"""Small page preferences carried by one browser cookie, without server storage."""

import base64
import binascii
import hashlib
import json
import math


BROWSER_PREFERENCE_KEYS = ("selection", "room_card_position")
BROWSER_PREFERENCE_MAX_AGE = 365 * 24 * 60 * 60
MAX_BROWSER_PREFERENCES_BYTES = 8192
MAX_BROWSER_PREFERENCE_COOKIE_BYTES = 3500


def browser_preference_cookie_name(installation_id, username):
    identity = json.dumps([installation_id, username], ensure_ascii=False, separators=(",", ":"))
    namespace = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"companion_browser_preferences_{namespace}"


def valid_browser_preference(key, value):
    if value is None:
        return True
    if type(value) is not dict:
        return False
    if key == "selection":
        if set(value) != {"bot_id", "role_id", "target_id"}:
            return False
        return all(type(value[field]) is str and bool(value[field].strip())
                   and len(value[field]) <= 512 for field in ("bot_id", "role_id")) and (
            value["target_id"] is None or (
                type(value["target_id"]) is str and bool(value["target_id"].strip())
                and len(value["target_id"]) <= 512))
    if key == "room_card_position":
        return set(value) in ({"left", "bottom"}, {"right", "bottom"}, {"left", "top"}) and all(
            type(coordinate) in (int, float) and math.isfinite(coordinate) and coordinate >= 0
            for coordinate in value.values())
    return False


def encode_browser_preferences(preferences):
    raw = json.dumps({"version": 1, "preferences": preferences}, ensure_ascii=False,
                     allow_nan=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii")
    if len(encoded) > MAX_BROWSER_PREFERENCE_COOKIE_BYTES:
        raise ValueError("browser preference is too large for a cookie")
    return encoded


def read_browser_preferences(cookies, name):
    empty = dict.fromkeys(BROWSER_PREFERENCE_KEYS)
    encoded = cookies.get(name)
    if type(encoded) is not str or len(encoded) > MAX_BROWSER_PREFERENCE_COOKIE_BYTES:
        return empty
    try:
        raw = base64.b64decode(encoded, altchars=b"-_", validate=True)
        document = json.loads(raw.decode("utf-8"))
        if type(document) is not dict or document.get("version") != 1:
            return empty
        preferences = document.get("preferences")
        if (type(preferences) is dict and set(preferences) == set(BROWSER_PREFERENCE_KEYS)
                and all(valid_browser_preference(key, value) for key, value in preferences.items())):
            return preferences
    except (ValueError, UnicodeError, TypeError, OverflowError, RecursionError, binascii.Error):
        pass
    return empty
