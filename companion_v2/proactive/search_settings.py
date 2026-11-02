"""Global DeepSeek search credential binding for the host adapter."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path
from uuid import uuid4


DEFAULTS = {"deepseek_provider_id": "", "source_priority": "auto"}
SOURCE_PRIORITIES = ("auto", "astrbot", "deepseek")
PROVIDER_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}\Z")
_SETTINGS_LOCK = threading.RLock()


class SearchSettingsError(ValueError):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


class SearchSettingsStore:
    """Store only the selected AstrBot Provider id; credentials remain host-owned."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.directory = self.root / "search"
        self.path = self.directory / "settings.json"

    def _read(self) -> dict:
        if not self.path.exists():
            return {**DEFAULTS, "revision": "initial"}
        try:
            raw = self.path.read_bytes()
            if len(raw) > 8192:
                raise ValueError("settings file is too large")
            data = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise SearchSettingsError("search_config_unreadable", "搜索服务配置不可读取") from error
        if type(data) is not dict or set(data) - {"deepseek_provider_id", "source_priority", "revision"}:
            raise SearchSettingsError("search_config_invalid", "搜索服务配置格式无效")
        values = {**DEFAULTS, "deepseek_provider_id": data.get("deepseek_provider_id", ""),
                  "source_priority": data.get("source_priority", "auto")}
        provider_id = values["deepseek_provider_id"]
        if (type(provider_id) is not str or len(provider_id) > 80
                or provider_id and PROVIDER_ID_PATTERN.fullmatch(provider_id) is None):
            raise SearchSettingsError("search_config_invalid", "搜索 Provider 标识无效")
        if values["source_priority"] not in SOURCE_PRIORITIES:
            raise SearchSettingsError("search_config_invalid", "搜索来源优先级无效")
        revision = data.get("revision")
        if type(revision) is not str or not revision:
            revision = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        return {**values, "revision": revision}

    def read(self) -> dict:
        with _SETTINGS_LOCK:
            return self._read()

    def selected_provider_id(self) -> str:
        try:
            return self.read()["deepseek_provider_id"]
        except SearchSettingsError:
            # An unreadable explicit selection must not silently switch providers.
            return "invalid-search-provider-selection"

    def source_priority(self) -> str:
        try:
            return self.read()["source_priority"]
        except SearchSettingsError:
            return "auto"

    def update(self, revision: object, changes: object) -> dict:
        with _SETTINGS_LOCK:
            current = self._read()
            if type(revision) is not str or revision != current["revision"]:
                raise SearchSettingsError("search_config_conflict", "搜索配置已变化，请刷新后再保存")
            if (type(changes) is not dict or not changes
                    or set(changes) - {"deepseek_provider_id", "source_priority"}):
                raise SearchSettingsError("search_config_invalid", "搜索配置字段无效")
            provider_id = changes.get("deepseek_provider_id", current["deepseek_provider_id"])
            if (type(provider_id) is not str or len(provider_id) > 80
                    or provider_id and PROVIDER_ID_PATTERN.fullmatch(provider_id) is None):
                raise SearchSettingsError("search_config_invalid", "搜索 Provider 标识无效")
            priority = changes.get("source_priority", current["source_priority"])
            if priority not in SOURCE_PRIORITIES:
                raise SearchSettingsError("search_config_invalid", "搜索来源优先级无效")
            values = {"deepseek_provider_id": provider_id, "source_priority": priority,
                      "revision": uuid4().hex}
            self._write(values)
            return self._read()

    def _write(self, values: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name("." + self.path.name + "." + uuid4().hex + ".tmp")
        payload = json.dumps(values, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
