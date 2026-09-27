# -*- coding: utf-8 -*-
"""UnifiedPersonRegistryCoreNamespaceMixin。

由 tools/split_mixin_domain.py 从 unified_person_registry.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 313 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UnifiedPersonRegistry）。
"""
from __future__ import annotations

try:  # package import
    from .unified_person_registry_shared import _LOCK, _identity, _root, _text
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import _LOCK, _identity, _root, _text
try:  # package import
    from .unified_person_registry_shared import Any
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import Any
try:  # package import
    from .unified_person_registry_shared import AssurancePolicy
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import AssurancePolicy
try:  # package import
    from .unified_person_registry_shared import NamespaceContext
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import NamespaceContext
try:  # package import
    from .unified_person_registry_shared import build_identity_key
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import build_identity_key
try:  # package import
    from .unified_person_registry_shared import build_person_projection
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import build_person_projection
try:  # package import
    from .unified_person_registry_shared import deepcopy
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import deepcopy
try:  # package import
    from .unified_person_registry_shared import person_id_for_identity
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import person_id_for_identity
try:  # package import
    from .unified_person_registry_shared import resolve_identity
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import resolve_identity
try:  # package import
    from .unified_person_registry_shared import validate_projection
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import validate_projection



class UnifiedPersonRegistryCoreNamespaceMixin:
    """UnifiedPersonRegistryCoreNamespaceMixin（从 UnifiedPersonRegistry 拆出）。"""


    def is_bound_to(self, store: Any) -> bool:
        """Report whether this lightweight facade targets the active persona store."""
        return self._store is store

    def status(self) -> dict[str, Any]:
        with _LOCK:
            try:
                root = _root(self._store)
            except (TypeError, ValueError):
                return {"state": "invalid", "profiles": 0, "identity_links": 0, "group_overlays": 0}
            profiles = root["profiles"]
            links = root["identity_links"]
            overlays = root["group_overlays"]
            state = "resolved" if profiles and links else "pending"
            if any(not isinstance(item, dict) for item in profiles.values()):
                state = "invalid"
            return {
                "state": state,
                "version": int(root.get("version") or 1),
                "profiles": len(profiles),
                "identity_links": len(links),
                "group_overlays": len(overlays),
                "audit_events": len(root["audit_events"]),
                "operations": len(root["operations"]),
            }

    def resolve(self, identity: dict[str, Any]) -> dict[str, Any]:
        with _LOCK:
            try:
                result = resolve_identity(self._store, identity)
            except (TypeError, ValueError):
                return {"state": "invalid", "identity_key": "", "person_id": "", "errors": ["identity_invalid"]}
            if result.get("state") not in {"pending", "invalid", "resolved", "degraded"}:
                result["state"] = "invalid"
            return deepcopy(result)

    def namespace_context(
        self,
        identity: dict[str, Any],
        *,
        kind: str,
        group_id: str = "",
        policy_version: str,
        migration_epoch: str,
        purpose: str = "memory_read",
    ) -> dict[str, Any]:
        """Resolve a strict Shadow namespace without changing legacy state.

        A complete five-field identity that exactly matches an active link is
        treated as ``verified`` for the new read matrix.  The legacy v1 profile
        keeps its historical ``observed`` value, avoiding a silent contract
        change for old consumers.  Missing links are routed to ``pending`` and
        therefore fail closed for every formal purpose.
        """
        try:
            normalized = _identity(identity)
            identity_key = build_identity_key(normalized)
        except (TypeError, ValueError):
            return {"ok": False, "code": "identity_invalid", "context": None, "decision": "namespace_context_missing"}
        with _LOCK:
            root = _root(self._store)
            link = root["identity_links"].get(identity_key)
            linked = isinstance(link, dict) and link.get("status") == "active" and bool(link.get("person_id"))
            person_id = str(link.get("person_id") or "") if linked else person_id_for_identity(normalized)
            profile = root["profiles"].get(person_id) if linked else None
            status = str(profile.get("profile_status") or "active") if isinstance(profile, dict) else "active"
            stored_assurance = str(link.get("identity_assurance") or "observed") if linked else "unverified"
        assurance = "explicit_linked" if stored_assurance == "explicit_linked" else "verified" if linked else "unverified"
        effective_kind = kind if linked else "pending"
        context = NamespaceContext(
            kind=effective_kind,
            identity_id=person_id,
            group_id=group_id if effective_kind in {"group_member", "group_shared"} else "",
            assurance=assurance,
            profile_status=status,
            policy_version=policy_version,
            migration_epoch=migration_epoch,
        )
        decision = AssurancePolicy.authorize(context, purpose)
        return {
            "ok": decision.allowed,
            "code": "namespace_resolved" if decision.allowed else decision.code,
            "identity_key": identity_key,
            "person_id": person_id,
            "context": context.to_dict(),
            "decision": decision.code,
        }

    def formal_namespace_for_person(
        self,
        person_id: str,
        *,
        kind: str = "private",
        group_id: str = "",
        policy_version: str,
        migration_epoch: str,
        purpose: str = "relationship_write",
    ) -> dict[str, Any]:
        """Resolve one person's primary exact link without guessing a subject."""
        try:
            clean_person = _text(person_id, "person_id")
        except ValueError:
            return {"ok": False, "code": "identity_invalid", "context": None, "decision": "identity_invalid"}
        with _LOCK:
            root = _root(self._store)
            profile = root["profiles"].get(clean_person)
            if not isinstance(profile, dict) or profile.get("profile_status", "active") != "active":
                return {"ok": False, "code": "profile_status_denied", "context": None, "decision": "profile_status_denied"}
            identity_key = str(profile.get("resolved_identity_key") or "")
            identity_keys = profile.get("identity_keys")
            if (
                not isinstance(identity_keys, list)
                or any(not isinstance(item, str) for item in identity_keys)
                or len(set(identity_keys)) != len(identity_keys)
                or identity_key not in identity_keys
            ):
                return {"ok": False, "code": "identity_exact_link_invalid", "context": None, "decision": "identity_exact_link_invalid"}
            active_keys = {
                str(key)
                for key, candidate in root["identity_links"].items()
                if isinstance(candidate, dict)
                and candidate.get("status") == "active"
                and candidate.get("person_id") == clean_person
            }
            if active_keys != set(identity_keys):
                return {"ok": False, "code": "identity_exact_link_invalid", "context": None, "decision": "identity_exact_link_invalid"}
            for candidate_key in identity_keys:
                candidate = root["identity_links"].get(candidate_key)
                try:
                    if not isinstance(candidate, dict) or candidate.get("identity_key") != candidate_key:
                        raise ValueError("identity_link_invalid")
                    normalized = _identity(candidate.get("identity"))
                    if build_identity_key(normalized) != candidate_key:
                        raise ValueError("identity_key_mismatch")
                except (TypeError, ValueError):
                    return {"ok": False, "code": "identity_exact_link_invalid", "context": None, "decision": "identity_exact_link_invalid"}
            link = root["identity_links"][identity_key]
            assurance = "explicit_linked" if link.get("identity_assurance") == "explicit_linked" else "verified"
        context = NamespaceContext(
            kind=kind,
            identity_id=clean_person,
            group_id=group_id if kind in {"group_member", "group_shared"} else "",
            assurance=assurance,
            profile_status="active",
            policy_version=policy_version,
            migration_epoch=migration_epoch,
        )
        decision = AssurancePolicy.authorize(context, purpose)
        return {
            "ok": decision.allowed,
            "code": "namespace_resolved" if decision.allowed else decision.code,
            "identity_key": identity_key,
            "person_id": clean_person,
            "context": context.to_dict(),
            "decision": decision.code,
        }

    def matches_person_subject(self, person_id: str, subject_id: str) -> bool:
        """Check an already-bound legacy row against exact active link subjects."""
        try:
            clean_person = _text(person_id, "person_id")
            subject = _text(subject_id, "subject_id", 160)
        except ValueError:
            return False
        with _LOCK:
            root = _root(self._store)
            for candidate in root["identity_links"].values():
                if (
                    not isinstance(candidate, dict)
                    or candidate.get("status") != "active"
                    or candidate.get("person_id") != clean_person
                ):
                    continue
                try:
                    identity = _identity(candidate.get("identity"))
                    if build_identity_key(identity) != candidate.get("identity_key"):
                        continue
                except (TypeError, ValueError):
                    continue
                platform_subject = identity["platform_subject_id"]
                if subject == platform_subject:
                    return True
                parts = subject.rsplit(":", 2)
                if (
                    len(parts) == 3
                    and len(parts[2]) == 16
                    and all(char in "0123456789abcdef" for char in parts[2].lower())
                    and parts[0].lower() == identity["subject_namespace"].split(":", 1)[0]
                    and parts[1] == platform_subject
                ):
                    return True
        return False

    def identity_for_person_subject(
        self, person_id: str, subject_id: str
    ) -> dict[str, str] | None:
        """Resolve one exact active identity for a trusted internal operation.

        The returned identity contains storage-level routing fields and must not
        be serialized to the page.  Page handlers use it only after selecting a
        concrete legacy user row, so unlink operations never accept a partial
        identity assembled by the browser.
        """
        try:
            clean_person = _text(person_id, "person_id")
            subject = _text(subject_id, "subject_id", 160)
        except ValueError:
            return None
        matches: list[dict[str, str]] = []
        with _LOCK:
            root = _root(self._store)
            for candidate in root["identity_links"].values():
                if (
                    not isinstance(candidate, dict)
                    or candidate.get("status") != "active"
                    or candidate.get("person_id") != clean_person
                ):
                    continue
                try:
                    identity = _identity(candidate.get("identity"))
                    if build_identity_key(identity) != candidate.get("identity_key"):
                        continue
                except (TypeError, ValueError):
                    continue
                platform_subject = identity["platform_subject_id"]
                parts = subject.rsplit(":", 2)
                opaque_subject_match = (
                    len(parts) == 3
                    and len(parts[2]) == 16
                    and all(char in "0123456789abcdef" for char in parts[2].lower())
                    and parts[0].lower() == identity["subject_namespace"].split(":", 1)[0]
                    and parts[1] == platform_subject
                )
                if subject == platform_subject or opaque_subject_match:
                    matches.append(identity)
        return deepcopy(matches[0]) if len(matches) == 1 else None

    def detached_identity_for_person_subject(
        self, person_id: str, subject_id: str
    ) -> dict[str, str] | None:
        """Resolve one exact detached identity for a trusted relink operation."""
        try:
            clean_person = _text(person_id, "person_id")
            subject = _text(subject_id, "subject_id", 160)
        except ValueError:
            return None
        matches: list[dict[str, str]] = []
        with _LOCK:
            root = _root(self._store)
            for candidate in root["detached_identity_links"].values():
                if (
                    not isinstance(candidate, dict)
                    or candidate.get("status") != "detached"
                    or candidate.get("person_id") != clean_person
                ):
                    continue
                try:
                    identity = _identity(candidate.get("identity"))
                    if build_identity_key(identity) != candidate.get("identity_key"):
                        continue
                except (TypeError, ValueError):
                    continue
                platform_subject = identity["platform_subject_id"]
                parts = subject.rsplit(":", 2)
                opaque_subject_match = (
                    len(parts) == 3
                    and len(parts[2]) == 16
                    and all(char in "0123456789abcdef" for char in parts[2].lower())
                    and parts[0].lower() == identity["subject_namespace"].split(":", 1)[0]
                    and parts[1] == platform_subject
                )
                if subject == platform_subject or opaque_subject_match:
                    matches.append(identity)
        return deepcopy(matches[0]) if len(matches) == 1 else None

    def safe_admin_person_summary(
        self, person_id: str, subject_id: str = ""
    ) -> dict[str, Any]:
        """Return bounded identity state without raw subjects or identity keys."""
        try:
            clean_person = _text(person_id, "person_id")
            subject = _text(subject_id, "subject_id", 160) if subject_id else ""
        except ValueError:
            return {"linked": False, "code": "identity_reference_invalid"}
        with _LOCK:
            root = _root(self._store)
            projection = build_person_projection(self._store, clean_person)
            profile = root["profiles"].get(clean_person)
            if (
                not isinstance(profile, dict)
                or projection is None
                or validate_projection(projection)
            ):
                return {"linked": False, "code": "identity_projection_invalid"}
            active = [
                link for link in root["identity_links"].values()
                if isinstance(link, dict)
                and link.get("person_id") == clean_person
                and link.get("status") == "active"
            ]
            detached_count = sum(
                1 for link in root["detached_identity_links"].values()
                if isinstance(link, dict) and link.get("person_id") == clean_person
            )
            current_linked = False
            current_detached = False
            if subject:
                current_linked = self.identity_for_person_subject(clean_person, subject) is not None
                current_detached = self.detached_identity_for_person_subject(clean_person, subject) is not None
            return {
                "linked": True,
                "code": "identity_admin_summary",
                "current_identity_linked": current_linked,
                "current_identity_detached": current_detached,
                "identity_assurance": str(projection.get("identity_assurance") or "unverified"),
                "profile_status": str(projection.get("profile_status") or "active"),
                "projection_revision": int(projection.get("projection_revision") or 0),
                "active_identity_count": len(active),
                "detached_identity_count": detached_count,
                "updated_at": str(projection.get("updated_at") or ""),
            }
