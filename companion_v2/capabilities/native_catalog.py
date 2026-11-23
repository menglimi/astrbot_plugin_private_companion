"""Upgrade V2 capability ownership while retaining immutable old descriptors."""

from __future__ import annotations

import json

from companion_v2.foundation.outcome import Outcome
from companion_v2.storage.codec import encode_timestamp
from .repositories import _descriptor_from_row, _descriptor_json


def register_native_replacement(runtime, descriptor, *, previous_package_id, observed_at):
    """The trusted host supplies the exact predecessor, never a user payload.

    This moves only V2's installed capability identity. Settings and user stores
    in independent V1 plugins are neither opened nor copied.
    """
    with runtime.transaction() as connection:
        rows = connection.execute("SELECT * FROM capability_descriptors WHERE extension_id = ?",
                                  (descriptor.extension_id,)).fetchall()
        predecessors = [row for row in rows if row["package_id"] == previous_package_id]
        if not predecessors:
            return Outcome.success(False)
        allowed_packages = {previous_package_id, descriptor.package_id}
        history = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?",
            ("capability.native_owner:" + descriptor.extension_id,)).fetchone()
        historical_predecessor = previous_package_id
        if previous_package_id == descriptor.package_id and history is not None:
            try:
                owner = json.loads(history["value"])
            except (TypeError, ValueError):
                return Outcome.failed("capability_identity_conflict", "the native ownership history is invalid")
            if isinstance(owner, dict) and owner.get("package_id") == descriptor.package_id:
                historical_predecessor = owner.get("previous_package_id")
                if not isinstance(historical_predecessor, str):
                    return Outcome.failed("capability_identity_conflict", "the native predecessor is invalid")
                allowed_packages.add(historical_predecessor)
        for row in rows:
            previous = _descriptor_from_row(row)
            if (previous.package_id not in allowed_packages
                    or previous.publisher_id != descriptor.publisher_id
                    or set(previous.dataset_owners) != set(descriptor.dataset_owners)
                    or not set(previous.capabilities).issubset(descriptor.capabilities)
                    or (previous.furniture and {item.furniture_id for item in previous.furniture}
                        != {item.furniture_id for item in descriptor.furniture})):
                return Outcome.failed("capability_identity_conflict", "the installed capability has an unknown predecessor")
        existing = connection.execute("SELECT * FROM capability_descriptors WHERE extension_id = ? "
            "AND package_id = ? AND package_version = ?",
            (descriptor.extension_id, descriptor.package_id, descriptor.package_version)).fetchone()
        if existing is not None and _descriptor_from_row(existing) != descriptor:
            recorded = _descriptor_from_row(existing)
            if recorded.artifact_hash != descriptor.artifact_hash:
                return Outcome.failed(
                    "capability_descriptor_conflict",
                    f"模块 {descriptor.extension_id}（版本 {descriptor.package_version}）代码校验哈希与已登记记录不一致；"
                    "若刚本地热修或替换了模块代码，请递增插件版本号后重载，或通过完整更新包升级",
                )
            return Outcome.failed(
                "capability_descriptor_conflict",
                f"模块 {descriptor.extension_id}（版本 {descriptor.package_version}）能力声明与已登记记录不一致，请递增版本号后重试",
            )
        if existing is None:
            connection.execute("INSERT INTO capability_descriptors(extension_id, package_id, package_version, "
                "publisher_id, artifact_hash, descriptor_json, registered_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (descriptor.extension_id, descriptor.package_id, descriptor.package_version, descriptor.publisher_id,
                 descriptor.artifact_hash, _descriptor_json(descriptor), encode_timestamp(observed_at)))
        marker = json.dumps({"previous_package_id": historical_predecessor, "package_id": descriptor.package_id},
                            sort_keys=True, separators=(",", ":"))
        connection.execute("INSERT INTO runtime_metadata(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("capability.native_owner:" + descriptor.extension_id, marker))
        # State, role selections, authorizations and retained datasets are kept.
        # No newly introduced capability receives an authorization here.
        connection.execute("UPDATE capability_installations SET package_id = ?, package_version = ?, "
            "artifact_hash = ?, revision = revision + 1, updated_at = ? WHERE extension_id = ? AND package_id = ? "
            "AND (package_id != ? OR package_version != ? OR artifact_hash != ?)",
            (descriptor.package_id, descriptor.package_version, descriptor.artifact_hash,
             encode_timestamp(observed_at), descriptor.extension_id, previous_package_id,
             descriptor.package_id, descriptor.package_version, descriptor.artifact_hash))
    return Outcome.success(True)
