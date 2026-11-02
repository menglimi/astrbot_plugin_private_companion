"""Host-only QWeather credentials shared by the current-weather and alert adapters.

Both QWeather products authenticate the same way, and a single QWeather project issues
one credential that covers both. So the credential is read once here, and the two
adapters differ only in which endpoint they call.

Environment names come in two generations. ``QWEATHER_*`` is the current, neutral set;
``QWEATHER_ALERT_*`` is what earlier releases used, from when alerts were the only
QWeather feature. The neutral set wins when both are present, and an installation that
only ever set the older one keeps working untouched.

Credentials come from host settings or the environment, never role configuration.
"""

import base64
import json
import os
from pathlib import Path
import re
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key


MAX_RESPONSE_BYTES = 65536
# QWeather localises text to the location's official language unless a language is
# named, so a role in Vienna would get German. The character's voice is Chinese, so
# every request pins the same language. Values are lowercase; the API rejects unknown
# or mixed-case values with HTTP 400 instead of falling back.
RESPONSE_LANGUAGE = "zh"
HOST_PATTERN = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.qweatherapi\.com\Z")
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
API_KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{32}\Z")

FIELDS = ("API_HOST", "API_KEY", "JWT_TOKEN", "CREDENTIAL_ID", "DEVELOPER_ID", "PROJECT_ID",
          "PRIVATE_KEY_FILE", "PRIVATE_KEY_PEM")
JWT_FIELDS = ("CREDENTIAL_ID", "DEVELOPER_ID", "PROJECT_ID", "PRIVATE_KEY_FILE", "PRIVATE_KEY_PEM")
SIGNED_FIELDS = ("CREDENTIAL_ID", "PROJECT_ID")
KEY_FIELDS = ("PRIVATE_KEY_FILE", "PRIVATE_KEY_PEM")

# Checked in order; the first group with any value set wins as a group, so a
# half-filled neutral set never silently borrows half of the older one.
PREFIXES = ("QWEATHER_", "QWEATHER_ALERT_")


def _encoded(value):
    raw = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def read_credentials(config=None):
    """Return the configured field values, or ``None`` when QWeather is unused."""
    settings = config.get("weather", {}) if isinstance(config, dict) else {}
    if type(settings) is not dict:
        raise ValueError("invalid host weather settings")
    host, credential = settings.get("api_host", ""), settings.get("credential", "")
    if type(host) is not str or type(credential) is not str:
        raise ValueError("invalid host weather credentials")
    signed = {name: settings.get(name, "") for name in
              ("jwt_credential_id", "jwt_developer_id", "jwt_project_id", "jwt_private_key")}
    if type(signed["jwt_private_key"]) is not str:
        raise ValueError("invalid host weather private key")
    if host or credential or any(signed.values()):
        values = dict.fromkeys(FIELDS, "")
        values["API_HOST"] = host
        if any(signed.values()):
            values["CREDENTIAL_ID"] = str(signed["jwt_credential_id"] or "")
            values["PROJECT_ID"] = str(signed["jwt_project_id"] or "")
            values["DEVELOPER_ID"] = str(signed["jwt_developer_id"] or "")
            values["PRIVATE_KEY_PEM"] = signed["jwt_private_key"]
        else:
            values["JWT_TOKEN" if "." in credential else "API_KEY"] = credential
        return values
    for prefix in PREFIXES:
        values = {name: os.environ.get(prefix + name, "") for name in FIELDS}
        if any(values.values()):
            return values
    return None


def credentials_incomplete(values) -> bool:
    signed_started = any(values.get(name) for name in (*SIGNED_FIELDS, "DEVELOPER_ID", *KEY_FIELDS))
    authentication = (bool(values["API_KEY"]), bool(values.get("JWT_TOKEN")), signed_started)
    if sum(authentication) > 1:
        return True
    signed = (all(values.get(name) for name in SIGNED_FIELDS)
              and any(values.get(name) for name in KEY_FIELDS))
    if not values["API_HOST"] or not (values["API_KEY"] or values.get("JWT_TOKEN") or signed):
        return True
    return False


class QWeatherCredentials:
    """One validated QWeather credential, usable against either product endpoint."""

    def __init__(self, *, api_host, api_key=None, bearer_token=None, credential_id=None, developer_id=None,
                 project_id=None, private_key_pem=None, now=None):
        if type(api_host) is not str or not HOST_PATTERN.fullmatch(api_host):
            raise ValueError("use the assigned QWeather API Host")
        jwt_values = (credential_id, developer_id, project_id, private_key_pem)
        if bearer_token is not None:
            if api_key is not None or any(value is not None for value in jwt_values):
                raise ValueError("QWeather authentication settings are mutually exclusive")
            if (type(bearer_token) is not str or len(bearer_token) > 4096
                    or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", bearer_token)):
                raise ValueError("invalid QWeather JWT token")
            key = None
        elif api_key is not None:
            if any(value is not None for value in jwt_values):
                raise ValueError("QWeather API Key and JWT settings are mutually exclusive")
            if type(api_key) is not str or not API_KEY_PATTERN.fullmatch(api_key):
                raise ValueError("invalid QWeather API Key")
            key = None
        else:
            if any(type(value) is not str or not IDENTIFIER_PATTERN.fullmatch(value)
                   for value in jwt_values[:3] if value):
                raise ValueError("invalid QWeather JWT identifiers")
            if not credential_id or not project_id:
                raise ValueError("QWeather JWT requires credential_id and project_id")
            if developer_id and not IDENTIFIER_PATTERN.fullmatch(developer_id):
                raise ValueError("invalid QWeather JWT identifiers")
            if type(private_key_pem) is not bytes:
                raise ValueError("QWeather requires an Ed25519 private key")
            key = load_pem_private_key(private_key_pem, password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError("QWeather requires an Ed25519 private key")
        self._host = api_host
        self._api_key = api_key
        self._bearer_token = bearer_token
        self._credential_id = credential_id
        self._developer_id = developer_id
        self._project_id = project_id
        self._key = key
        self._now = now or (lambda: datetime.now(timezone.utc))

    @property
    def host(self):
        return self._host

    def headers(self, at):
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("QWeather query clock must be timezone aware")
        if self._api_key is not None:
            return {"X-QW-Api-Key": self._api_key}
        if self._bearer_token is not None:
            return {"Authorization": "Bearer " + self._bearer_token}
        now = int(at.timestamp())
        header = _encoded({"alg": "EdDSA", "kid": self._credential_id})
        claims = {"sub": self._project_id, "iat": now - 30, "exp": now + 870}
        if self._developer_id:
            claims["iss"] = self._developer_id
        payload = _encoded(claims)
        signing_input = header + b"." + payload
        signature = base64.urlsafe_b64encode(self._key.sign(signing_input)).rstrip(b"=")
        return {"Authorization": "Bearer " + (signing_input + b"." + signature).decode("ascii")}


def credentials_from_environment(*, label: str, config=None):
    """Build credentials from the environment, or ``None`` when unconfigured.

    ``label`` only shapes the error text, so an operator can tell which adapter
    rejected a malformed set.
    """
    values = read_credentials(config)
    if values is None:
        return None
    if values["API_KEY"] and any(values[name] for name in JWT_FIELDS):
        raise ValueError("QWeather API Key and JWT settings are mutually exclusive")
    if credentials_incomplete(values):
        raise ValueError(f"QWeather {label} host credentials are incomplete")
    if values["API_KEY"]:
        return QWeatherCredentials(api_host=values["API_HOST"], api_key=values["API_KEY"])
    if values.get("JWT_TOKEN"):
        return QWeatherCredentials(api_host=values["API_HOST"], bearer_token=values["JWT_TOKEN"])
    if values.get("PRIVATE_KEY_PEM"):
        private_key_pem = values["PRIVATE_KEY_PEM"].encode("utf-8")
    else:
        key_file = Path(values["PRIVATE_KEY_FILE"])
        if not key_file.is_absolute() or not key_file.is_file():
            raise ValueError("QWeather private key file must be an existing absolute path")
        private_key_pem = key_file.read_bytes()
    return QWeatherCredentials(api_host=values["API_HOST"],
        credential_id=values["CREDENTIAL_ID"] or None,
        developer_id=values["DEVELOPER_ID"] or None,
        project_id=values["PROJECT_ID"] or None, private_key_pem=private_key_pem)
