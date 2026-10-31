"""Preserve the legacy host settings before AstrBot normalizes the new schema."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from uuid import uuid4


CONFIG_NAME = "astrbot_plugin_private_companion_config.json"
ARCHIVE_DIRECTORY = ".private-companion-upgrade"

logger = logging.getLogger(__name__)


def is_legacy_configuration(value):
    return isinstance(value, dict) and any(key in value for key in (
        "basic_config", "legacy_compat_config", "target_user_ids", "LLM_PROVIDER_ID",
    ))


def _has_value(value):
    """Whether a stored legacy value carries data worth preserving."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return True


def _value_at_path(value, path):
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _preserved_values_would_be_lost(previous, candidate):
    """Keep an earlier source when a later legacy-shaped file is pruned.

    AstrBot can rewrite a plugin config while a replacement schema is being
    loaded. The rewritten document may still contain V1 marker keys, so marker
    detection alone cannot distinguish it from the original V1 configuration.
    Compare populated paths instead of ranking documents by size: schema
    defaults can make a pruned document larger than the original.
    """
    pending = [((), previous)]
    while pending:
        path, value = pending.pop()
        if isinstance(value, dict):
            pending.extend((path + (key,), child) for key, child in value.items())
            continue
        if not _has_value(value):
            continue
        later = _value_at_path(candidate, path)
        if not _has_value(later):
            return True
        if isinstance(value, list) and isinstance(later, list):
            # A list can carry several independent targets/assets. Retain the
            # source if even one earlier item disappears from the later copy.
            if any(item not in later for item in value):
                return True
    return False


def preserved_configuration_path(directory):
    archive = Path(directory) / ARCHIVE_DIRECTORY
    pointer = archive / "source.json"
    if not pointer.is_file():
        return None
    document = json.loads(pointer.read_text(encoding="utf-8"))
    fingerprint = document.get("sha256", "")
    if (not isinstance(fingerprint, str) or len(fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in fingerprint)):
        raise ValueError("the saved upgrade source fingerprint is invalid")
    source = archive / (fingerprint + ".json")
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != fingerprint:
        raise ValueError("the saved upgrade configuration changed")
    if not is_legacy_configuration(json.loads(raw.decode("utf-8-sig"))):
        raise ValueError("the saved upgrade source is not a legacy configuration")
    return source


def usable_preserved_configuration_path(directory):
    """The preserved source when it can still be vouched for, otherwise ``None``.

    ``preserved_configuration_path`` raises on purpose: a migration must never run
    from a source whose bytes no longer match the recorded fingerprint.  That
    guard answers "may I migrate from this?", not "may the plugin run?".  The
    caller that only needs the second answer (the import hook, and the settings
    reader that feeds "not found / present but unreadable") must not turn one
    damaged file inside ``.private-companion-upgrade`` into a plugin that fails to
    load on every restart, with nothing naming the directory to remove.

    Nothing is swallowed: the failure is logged with its exception type and the
    archive path, and an unusable archive simply stops being a migration source.
    A live configuration that is still legacy-shaped lets the next
    ``preserve_before_schema`` write a fresh archive and rewrite the pointer, so
    the usual damage repairs itself on the next start.
    """

    try:
        return preserved_configuration_path(directory)
    except (OSError, ValueError) as error:
        logger.warning(
            "[CompanionV2] 旧配置升级归档不可用，本次不把它作为迁移来源 error_type=%s archive=%s",
            type(error).__name__, Path(directory) / ARCHIVE_DIRECTORY,
        )
        return None


def _write_atomically(destination, raw):
    temporary = destination.parent / ("." + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def preserve_before_schema(directory):
    """Archive exact bytes; fail before schema pruning if preservation is impossible."""
    directory = Path(directory)
    source = directory / CONFIG_NAME
    if not source.is_file():
        return None
    raw = source.read_bytes()
    value = json.loads(raw.decode("utf-8-sig"))
    if not is_legacy_configuration(value):
        # The live file has already been pruned to the current schema, so the
        # archive is the only copy left; return it when it is still readable and
        # let the caller continue without one when it is not.
        return usable_preserved_configuration_path(directory)
    existing = usable_preserved_configuration_path(directory)
    if existing is not None:
        previous = json.loads(existing.read_text(encoding="utf-8-sig"))
        if _preserved_values_would_be_lost(previous, value):
            # Keep an audit copy of the later document, but do not let a
            # defaulted/pruned configuration replace the migration source.
            fingerprint = hashlib.sha256(raw).hexdigest()
            archive = directory / ARCHIVE_DIRECTORY
            archive.mkdir(parents=True, exist_ok=True)
            destination = archive / (fingerprint + ".json")
            if destination.exists():
                if destination.read_bytes() != raw:
                    # The audit copy is diagnostic; the verified source above is
                    # what a migration reads. An inconsistent audit copy is worth
                    # a warning, not a plugin that cannot load.
                    logger.warning(
                        "[CompanionV2] 旧配置升级审计副本与当前配置不一致，保留现有文件 archive=%s",
                        destination,
                    )
            else:
                _write_atomically(destination, raw)
            return existing
    fingerprint = hashlib.sha256(raw).hexdigest()
    archive = directory / ARCHIVE_DIRECTORY
    archive.mkdir(parents=True, exist_ok=True)
    destination = archive / (fingerprint + ".json")
    if destination.exists() and destination.read_bytes() != raw:
        if existing is not None:
            raise ValueError("the upgrade archive is inconsistent")
        # Reaching here means the archive at this fingerprint is the very damage
        # ``usable_preserved_configuration_path`` just refused, while the live
        # configuration is still legacy-shaped and therefore the better source.
        # Rebuild it instead of failing the import hook a second time.
        destination.unlink()
    if not destination.exists():
        _write_atomically(destination, raw)
    pointer = archive / "source.json"
    document = {"format": "private-companion.upgrade-source.v1", "sha256": fingerprint}
    temporary = archive / ("." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, pointer)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def preserve_host_configuration():
    from astrbot.core.utils.astrbot_path import get_astrbot_config_path
    return preserve_before_schema(get_astrbot_config_path())
