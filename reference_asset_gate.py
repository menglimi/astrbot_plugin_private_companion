"""Conservative local gate for structured image-reference assets (REQ-021 Q5).

This module intentionally does not import the cross-plugin P5 attestation
contract.  A structured reference asset is a local, administrator-registered
file only; it grants a one-shot image-model input for one generation and
nothing else.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONTRACT_NAME = "ops.p5.reference_asset_sink.v1"
ROLE_ORDER = ("identity", "outfit", "pose", "scene", "style")
ALLOWED_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
MAX_ASSET_BYTES = 20 * 1024 * 1024
MAX_INPUT_ASSETS = 4
TICKET_TTL_SECONDS = 90
_ASSET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


@dataclass(frozen=True)
class ManagedReferenceAsset:
    asset_id: str
    role: str
    path: Path
    prompt_label: str
    outfit_lock_default: bool
    continuation_match: bool
    new_topic_enabled: bool
    edit_enabled: bool


@dataclass(frozen=True)
class ReferenceAssetPlan:
    generation_id: str
    mode: str
    assets: tuple[ManagedReferenceAsset, ...]
    prompt_constraint: str

    @property
    def primary_asset(self) -> ManagedReferenceAsset | None:
        return next((item for item in self.assets if item.role == "identity"), None)

    def public_projection(self) -> dict[str, Any]:
        return {
            "contract": CONTRACT_NAME,
            "enabled": True,
            "mode": self.mode,
            "asset_ids": [item.asset_id for item in self.assets],
            "roles": [item.role for item in self.assets],
            "total": len(self.assets),
        }


@dataclass(frozen=True)
class ReferenceAssetTicket:
    """Opaque in-memory capability.  It has no serializer by design."""

    nonce: str


class ReferenceAssetGate:
    """Validate managed assets and mediate their only allowed sink."""

    def __init__(self, data_dir: str | Path, *, now: Any = time.time) -> None:
        self._root = (Path(data_dir) / "photo_reference_assets").resolve()
        self._now = now
        self._tickets: dict[str, dict[str, Any]] = {}

    @property
    def root(self) -> Path:
        return self._root

    @staticmethod
    def _text(value: Any, limit: int = 160) -> str:
        return " ".join(str(value or "").split())[:limit]

    @staticmethod
    def _bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None or str(value).strip() == "":
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}

    def _resolve_registered_path(self, value: Any) -> Path | None:
        raw = self._text(value, 240).replace("\\", "/")
        if not raw or raw.startswith(("/", "//")) or ":" in raw or ".." in raw.split("/"):
            return None
        try:
            candidate = (self._root / raw).resolve()
            candidate.relative_to(self._root)
        except (OSError, ValueError):
            return None
        return candidate

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def validate_entry(self, raw: Any) -> tuple[ManagedReferenceAsset | None, str]:
        if not isinstance(raw, dict):
            return None, "entry_not_object"
        asset_id = self._text(raw.get("id") or raw.get("asset_id"), 80)
        role = self._text(raw.get("role"), 24).lower()
        digest = self._text(raw.get("sha256"), 80).lower()
        path = self._resolve_registered_path(raw.get("file") or raw.get("relative_path"))
        if not _ASSET_ID_RE.fullmatch(asset_id):
            return None, "invalid_id"
        if role not in ROLE_ORDER:
            return None, "invalid_role"
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            return None, "invalid_hash"
        if path is None:
            return None, "outside_managed_directory"
        try:
            if not path.is_file():
                return None, "file_missing"
            if path.suffix.lower() not in ALLOWED_EXTENSIONS:
                return None, "unsupported_extension"
            if path.stat().st_size > MAX_ASSET_BYTES:
                return None, "file_too_large"
            if not secrets.compare_digest(self._sha256(path), digest):
                return None, "hash_mismatch"
        except OSError:
            return None, "file_unreadable"
        return ManagedReferenceAsset(
            asset_id=asset_id,
            role=role,
            path=path,
            prompt_label=self._text(raw.get("label"), 80),
            outfit_lock_default=self._bool(raw.get("outfit_lock_default"), False),
            continuation_match=self._bool(raw.get("continuation_match"), False),
            new_topic_enabled=self._bool(raw.get("new_topic_enabled"), True),
            edit_enabled=self._bool(raw.get("edit_enabled"), False),
        ), "ok"

    def inspect(self, entries: Any) -> dict[str, Any]:
        items = entries if isinstance(entries, list) else []
        result: list[dict[str, Any]] = []
        valid: dict[str, ManagedReferenceAsset] = {}
        seen_roles: set[str] = set()
        for raw in items[:16]:
            asset, status = self.validate_entry(raw)
            raw_id = self._text(raw.get("id") if isinstance(raw, dict) else "", 80)
            raw_role = self._text(raw.get("role") if isinstance(raw, dict) else "", 24).lower()
            asset_id = raw_id if _ASSET_ID_RE.fullmatch(raw_id) else ""
            role = raw_role if raw_role in ROLE_ORDER else ""
            if asset and asset.role in seen_roles:
                asset, status = None, "duplicate_role"
            if asset:
                seen_roles.add(asset.role)
                valid[asset.role] = asset
                asset_id, role = asset.asset_id, asset.role
            result.append({"id": asset_id, "role": role, "status": status, "valid": status == "ok"})
        return {"items": result, "valid_by_role": valid}

    def plan(self, entries: Any, *, generation_id: str, mode: str) -> tuple[ReferenceAssetPlan | None, str]:
        mode = self._text(mode, 24).lower()
        if mode not in {"continuation", "edit", "new_topic"}:
            return None, "invalid_mode"
        generation_id = self._text(generation_id, 120)
        if not generation_id:
            return None, "missing_generation_id"
        inspected = self.inspect(entries)
        by_role: dict[str, ManagedReferenceAsset] = inspected["valid_by_role"]
        identity = by_role.get("identity")
        if identity is None:
            return None, "missing_verified_identity"
        selected: list[ManagedReferenceAsset] = [identity]
        outfit = by_role.get("outfit")
        if outfit and (outfit.outfit_lock_default if mode == "continuation" else mode == "new_topic"):
            selected.append(outfit)
        for role in ("pose", "scene"):
            asset = by_role.get(role)
            if asset and ((mode == "continuation" and asset.continuation_match) or (mode == "new_topic" and asset.new_topic_enabled) or (mode == "edit" and asset.edit_enabled)):
                selected.append(asset)
        style = by_role.get("style")
        if style and ((mode == "continuation" and style.continuation_match) or (mode == "new_topic" and style.new_topic_enabled) or (mode == "edit" and style.edit_enabled)):
            selected.append(style)
        selected.sort(key=lambda item: ROLE_ORDER.index(item.role))
        selected = selected[:MAX_INPUT_ASSETS]
        constraints = [item for item in selected if item.role != "identity"]
        prompt_constraint = ""
        if constraints:
            names = ", ".join(item.role for item in constraints)
            prompt_constraint = f"Managed visual constraints: keep the approved {names} reference consistent."
        return ReferenceAssetPlan(generation_id=generation_id, mode=mode, assets=tuple(selected), prompt_constraint=prompt_constraint), "ok"

    def issue(self, plan: ReferenceAssetPlan, *, backend: str) -> ReferenceAssetTicket | None:
        if not isinstance(plan, ReferenceAssetPlan) or not plan.primary_asset:
            return None
        nonce = secrets.token_urlsafe(24)
        ticket = ReferenceAssetTicket(nonce=nonce)
        self._tickets[nonce] = {
            "ticket": ticket,
            "plan": plan,
            "backend": self._text(backend, 24).lower(),
            "expires_at": float(self._now()) + TICKET_TTL_SECONDS,
            "consumed": False,
        }
        return ticket

    def consume(
        self,
        ticket: ReferenceAssetTicket,
        *,
        generation_id: str,
        backend: str,
        capacity: int,
    ) -> tuple[list[str], str]:
        record = self._tickets.get(getattr(ticket, "nonce", ""))
        if not record or record.get("ticket") is not ticket:
            return [], "unknown_ticket"
        if record["consumed"] or float(self._now()) >= float(record["expires_at"]):
            return [], "expired_or_consumed_ticket"
        plan: ReferenceAssetPlan = record["plan"]
        if generation_id != plan.generation_id or self._text(backend, 24).lower() != record["backend"]:
            return [], "scope_mismatch"
        if capacity <= 0:
            return [], "invalid_capacity"
        record["consumed"] = True
        paths = [str(item.path) for item in plan.assets[:max(1, min(capacity, MAX_INPUT_ASSETS))]]
        return paths, "ok"

    def public_projection(self, entries: Any, *, backend_capacity: int) -> dict[str, Any]:
        inspected = self.inspect(entries)
        return {
            "contract": CONTRACT_NAME,
            "managed_directory": "photo_reference_assets",
            "items": inspected["items"],
            "backend_capacity": max(0, min(int(backend_capacity or 0), MAX_INPUT_ASSETS)),
            "max_assets": MAX_INPUT_ASSETS,
        }
