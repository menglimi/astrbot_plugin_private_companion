"""Atomic global Laya configuration with read-only legacy settings adoption."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import shlex
import sys
import threading
from pathlib import Path
from uuid import uuid4

from .contract import CONFIGURABLE_DECISION_FIELDS


DEFAULT_DECISION_FIELDS = {name: True for name in CONFIGURABLE_DECISION_FIELDS}


# The model each backend pins when the operator leaves `remote_model` empty.
# Local and SystemOne use Laya's own checkpoints; a Jev gateway serves `jev-latest`.
MODE_MODELS = {"local": "multilingual", "remote": "multilingual", "jev": "jev-latest",
               # Cloudflare's own decision model draws on the daily Workers AI
               # neuron allowance; third-party models such as typesafe/jev are
               # billed separately and need an override here.
               "workers_ai": "@cf/cloudflare/clef"}


DEFAULTS = {
    "enabled": False,
    "mode": "local",
    "local_model_path": "",
    "endpoint": "",
    "api_key": "",
    # Named apart from the historical `model` key, which older settings files
    # carried without any effect; adopting it would silently retarget a server.
    "remote_model": "",
    "timeout_seconds": 20,
    # 0 disables the host-side gate. Values are percentages because the model
    # page exposes a familiar 0-100 control; Laya reports a 0-1 probability.
    "followup_confidence_threshold": 0,
    "decision_fields": DEFAULT_DECISION_FIELDS,
}
ALLOWED_CHANGES = frozenset(DEFAULTS)
MODEL_REPOSITORY = "models--convaiinnovations--laya"
_SETTINGS_LOCK = threading.RLock()


class LayaSettingsError(ValueError):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def role_storage_key(target_id: object, role_id: object) -> str:
    raw = f"{target_id or 'installation'}\0{role_id or 'default'}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _model_cache_dir() -> Path:
    configured = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if configured:
        return Path(configured).expanduser()
    home = Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface")).expanduser()
    return home / "hub"


def _has_multilingual_model(snapshot: Path) -> bool:
    return checkpoint_ready(snapshot / "multilingual")


def checkpoint_ready(model: Path) -> bool:
    required_files = (
        model / "rl_agent_config.json",
        model / "model.safetensors",
        model / "tokenizer" / "tokenizer.json",
        model / "encoder" / "config.json",
    )
    return all(path.is_file() for path in required_files)


def cached_multilingual_model() -> Path | None:
    """Find complete installed weights without contacting the model repository."""
    repository = _model_cache_dir() / MODEL_REPOSITORY
    snapshots = repository / "snapshots"
    try:
        reference = repository / "refs" / "main"
        if reference.is_file():
            revision = reference.read_text(encoding="utf-8").strip()
            if revision and Path(revision).name == revision:
                preferred = snapshots / revision
                if _has_multilingual_model(preferred):
                    return preferred / "multilingual"
        complete = [snapshot for snapshot in snapshots.iterdir()
                    if snapshot.is_dir() and _has_multilingual_model(snapshot)]
        if complete:
            return max(complete, key=lambda path: (path.stat().st_mtime_ns, path.name)) / "multilingual"
    except (OSError, UnicodeError):
        pass
    return None


def _pip_command(arguments) -> str:
    executable = str(Path(sys.executable).resolve())
    if os.name == "nt":
        quote = lambda value: "'" + value.replace("'", "''") + "'"
        parts = [str(item) if str(item) in {"install", "--no-deps"} else quote(str(item))
                 for item in arguments]
        return f"& {quote(executable)} -m pip " + " ".join(parts)
    return shlex.quote(executable) + " -m pip " + " ".join(shlex.quote(str(item)) for item in arguments)


def _install_command() -> str:
    # Installing the package normally lets pip replace an existing CUDA torch
    # wheel with the CPU wheel from PyPI. Keep the package install dependency
    # free and install only its non-torch runtime dependencies afterwards.
    return (_pip_command(["install", "--no-deps", "laya==0.4.0"]) + "; "
            + _pip_command(["install", "transformers>=4.48.0", "safetensors>=0.4.0",
                            "huggingface_hub>=0.20.0", "numpy>=1.20.0"]))


def _remote_commands() -> tuple[str, str]:
    executable = str(Path(sys.executable).resolve())
    install = (_pip_command(["install", "--no-deps", "laya==0.4.0"]) + "; "
               + _pip_command(["install", "transformers>=4.48.0", "safetensors>=0.4.0",
                               "huggingface_hub>=0.20.0", "numpy>=1.20.0",
                               "fastapi>=0.110.0", "uvicorn>=0.27.0",
                               "python-multipart>=0.0.9"]))
    if os.name == "nt":
        quote = lambda value: "'" + value.replace("'", "''") + "'"
        start = f"$env:LAYA_DEFAULT_MODEL='multilingual'; & {quote(executable)} -m laya.serve"
    else:
        start = f"LAYA_DEFAULT_MODEL=multilingual {shlex.quote(executable)} -m laya.serve"
    return install, start


def _package_status() -> tuple[bool, str]:
    try:
        installed = importlib.util.find_spec("laya") is not None
    except (ImportError, ValueError):
        installed = False
    try:
        version = importlib.metadata.version("laya")
    except importlib.metadata.PackageNotFoundError:
        version = ""
    return installed, version


class LayaSettingsStore:
    def __init__(self, root: Path, target_id: object = "installation", role_id: object = "default"):
        self.root = Path(root).resolve()
        # Historical arguments remain accepted; every target and persona uses
        # one installation-wide configuration.
        self.directory = self.root / "decision"
        self.path = self.directory / "settings.json"

    def _source_path(self):
        if self.path.exists():
            return self.path
        legacy = tuple((self.directory / "roles").glob("*.json"))
        # Select the last saved user choice consistently for every caller.
        # Legacy originals are kept intact, including any older overrides.
        return max(legacy, key=lambda path: (path.stat().st_mtime_ns, path.name)) if legacy else None

    def _read(self) -> dict:
        source = self._source_path()
        if source is None:
            return {**DEFAULTS, "decision_fields": dict(DEFAULT_DECISION_FIELDS), "revision": "initial"}
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise LayaSettingsError("laya_config_unreadable", "Laya 配置不可读取") from error
        if type(data) is not dict:
            raise LayaSettingsError("laya_config_invalid", "Laya 配置格式无效")
        raw_fields = data.get("decision_fields", {})
        if type(raw_fields) is not dict or set(raw_fields) - set(DEFAULT_DECISION_FIELDS):
            raise LayaSettingsError("laya_config_invalid", "decision_fields 配置无效")
        values = {**DEFAULTS, **{key: data.get(key, default) for key, default in DEFAULTS.items()
                                 if key != "decision_fields"}}
        values["decision_fields"] = {**DEFAULT_DECISION_FIELDS, **raw_fields}
        # Read the first integration's mode-less settings as a remote endpoint setup.
        if "mode" not in data and values["endpoint"]:
            values["mode"] = "remote"
        revision = data.get("revision")
        values["revision"] = revision if type(revision) is str and revision else hashlib.sha256(
            json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        self._validate(values)
        return values

    @staticmethod
    def _validate(values: dict) -> None:
        if type(values["enabled"]) is not bool:
            raise LayaSettingsError("laya_config_invalid", "enabled 必须是布尔值")
        if values["mode"] not in MODE_MODELS:
            raise LayaSettingsError("laya_config_invalid",
                                   "mode 必须是 local、remote、jev 或 workers_ai")
        for key in ("endpoint", "api_key", "local_model_path", "remote_model"):
            if type(values[key]) is not str or len(values[key]) > 4096:
                raise LayaSettingsError("laya_config_invalid", f"{key} 配置无效")
        if type(values["timeout_seconds"]) is not int or isinstance(values["timeout_seconds"], bool) or not 1 <= values["timeout_seconds"] <= 120:
            raise LayaSettingsError("laya_config_invalid", "timeout_seconds 超出范围")
        threshold = values["followup_confidence_threshold"]
        if type(threshold) is not int or not 0 <= threshold <= 100:
            raise LayaSettingsError("laya_config_invalid", "followup_confidence_threshold 超出范围")
        fields = values["decision_fields"]
        if type(fields) is not dict or set(fields) != set(DEFAULT_DECISION_FIELDS) or any(
                type(value) is not bool for value in fields.values()):
            raise LayaSettingsError("laya_config_invalid", "decision_fields 必须为每项决策提供布尔值")

    def read(self) -> dict:
        values = self._read()
        installed, version = _package_status()
        cache_dir = _model_cache_dir()
        model_cache_ready = checkpoint_ready(Path(values["local_model_path"]).expanduser()) if values["local_model_path"].strip() else cached_multilingual_model() is not None
        remote_install_command, remote_start_command = _remote_commands()
        return {
            "configuration_scope": "global",
            "enabled": values["enabled"],
            "mode": values["mode"],
            "local_model_path": values["local_model_path"],
            "endpoint": values["endpoint"],
            "remote_model": values["remote_model"],
            "default_model": values["remote_model"] or MODE_MODELS[values["mode"]],
            "api_key_set": bool(values["api_key"]),
            "timeout_seconds": values["timeout_seconds"],
            "followup_confidence_threshold": values["followup_confidence_threshold"],
            "decision_fields": dict(values["decision_fields"]),
            "package_installed": installed,
            "package_version": version,
            "model_cache_ready": model_cache_ready,
            "model_cache_path": str(cache_dir),
            "python_executable": str(Path(sys.executable).resolve()),
            "install_command": _install_command(),
            "remote_install_command": remote_install_command,
            "remote_start_command": remote_start_command,
            "revision": values["revision"],
        }

    def raw(self) -> dict:
        return self._read()

    def update(self, revision: object, changes: object) -> dict:
        with _SETTINGS_LOCK:
            return self._update(revision, changes)

    def _update(self, revision: object, changes: object) -> dict:
        current = self._read()
        if type(revision) is not str or revision != current["revision"]:
            raise LayaSettingsError("laya_config_conflict", "Laya 配置已变化，请刷新后再保存")
        if type(changes) is not dict or not changes or set(changes) - ALLOWED_CHANGES:
            raise LayaSettingsError("laya_config_invalid", "Laya 配置字段无效")
        next_values = {**current, **changes}
        if "decision_fields" in changes:
            if type(changes["decision_fields"]) is not dict:
                raise LayaSettingsError("laya_config_invalid", "decision_fields 配置无效")
            next_values["decision_fields"] = {**current["decision_fields"], **changes["decision_fields"]}
        if "api_key" in changes and changes["api_key"] == "":
            next_values["api_key"] = current["api_key"]
        self._validate(next_values)
        next_values["revision"] = uuid4().hex
        self._write(next_values)
        return self.read()

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
