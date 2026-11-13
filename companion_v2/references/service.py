"""Reference material and wardrobe for one role.

Authorization and scope come from the task context, never from the caller: every
read and write re-checks the context's authority for the action it is performing.
The service stores registrations only — the bytes stay with the owner that
produced them — and ``verify_asset`` is the single place where an asserted digest
is compared against real content, so material that changed after registration is
refused instead of quietly influencing a generation.

The authority check is injectable so this layer can be tested without assembling a
whole supervisor; production wiring passes the task supervisor's own check.
"""

from __future__ import annotations

from datetime import datetime

from companion_v2.execution.models import TaskContext, TaskError, require
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import RoleOwnerRef, ScopeKind, normalize_utc
from companion_v2.storage.runtime import StorageRuntime

from .models import (ContentDigest, ReferenceAsset, ReferenceKind, ReferenceRole,
                     ReferenceSource, ReferenceState, WardrobeItem)
from .repositories import ReferenceAssetRepository


REGISTER_ACTION = "reference.register"
USE_ACTION = "reference.use"
WARDROBE_ACTION = "wardrobe.manage"
OWNED_REFERENCE_PREFIX = "role-reference:"


def role_owner(context):
    return RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                        context.role_binding.role.subject_id)


def owner_key(context):
    """Material belongs to one role body, not to whichever window is looking."""
    from companion_v2.execution.models import digest
    return digest(["role-references.v1", role_owner(context)])


class ReferenceAssetService:
    def __init__(self, runtime: StorageRuntime, tasks, *, settings=None, wardrobe=None,
                 authorize=None, require_running=None):
        if runtime is not tasks.runtime:
            raise ValueError("reference material must share its original task runtime")
        self.runtime, self.tasks = runtime, tasks
        self.assets = ReferenceAssetRepository(runtime)
        self.settings = settings
        self.wardrobe = wardrobe
        self._authorize = authorize
        self.require_running = require_running

    # --- shared guards -----------------------------------------------------------

    def _running(self):
        if self.require_running is not None:
            self.require_running()

    def _options(self, context, now):
        """The role's switches, or permissive defaults when no configuration is wired.

        Assembly always supplies settings; the fallback keeps this layer usable in
        isolation without pretending a switch was consulted when it was not.
        """
        if self.settings is None:
            return {"enabled": True, "group_enabled": False, "daily_outfit_enabled": True,
                    "daily_outfit_rotation_days": 14}
        return self.settings.read(context, now)

    def _admit(self, context, action, *, require_enabled=True):
        if not isinstance(context, TaskContext):
            raise ValueError("reference material requires its task context")
        self._running()
        now = normalize_utc(self.tasks.now(), field="reference observed time")
        # Reading role-owned material is part of the explicitly authorized Image
        # tool call. It is not a second user-facing capability or an implicit grant.
        policy_action = "extension.tool.invoke" if action == USE_ACTION else action
        decision = (self._authorize(context, action=action, observed_at=now)
                    if self._authorize is not None
                    else self.tasks.authorize_context(context, action=policy_action, observed_at=now))
        # The supervisor answers with an Outcome; an injected guard may answer with
        # a plain decision, so both shapes are honoured rather than assumed.
        if isinstance(decision, bool):
            if not decision:
                raise TaskError("reference_authority_revoked", "this context may not use reference material")
        else:
            require(decision)
        options = self._options(context, now)
        if require_enabled and not options["enabled"]:
            raise TaskError("photo_reference_disabled",
                            "reference material is off for this role")
        if (action == USE_ACTION and context.scope.kind is ScopeKind.GROUP
                and not options["group_enabled"]):
            raise TaskError("photo_reference_group_disabled",
                            "group use of this role's reference images is off")
        return now, options

    def owner(self, context):
        resolver = getattr(self.settings, 'role_owner', None)
        if resolver is None:
            return owner_key(context)
        from companion_v2.execution.models import digest
        return digest(['role-references.v1', resolver(context)])

    # --- registrations -----------------------------------------------------------

    def list_assets(self, context, *, kind=None, role=None, state=ReferenceState.ACTIVE,
                    allow_disabled=False):
        self._admit(context, USE_ACTION, require_enabled=not allow_disabled)
        return self.assets.list(self.owner(context), kind=kind, role=role, state=state)

    def get_asset(self, context, asset_id):
        self._admit(context, USE_ACTION)
        asset = self.assets.get(self.owner(context), asset_id)
        if asset is None:
            raise TaskError("reference_asset_missing", "the reference material is not registered")
        return asset

    @outcome
    def register_asset(self, context, *, asset_id, kind, role, content_hash, size_bytes, mime_type,
                       media_ref, source=ReferenceSource.OPERATOR, label="", payload=None):
        """Record one material, refusing a digest that does not match the bytes.

        When ``payload`` is supplied the asserted digest and size must match it;
        otherwise the caller is asserting a fact this layer cannot yet check, which
        is allowed only because the check is repeated at use time.
        """
        now, _options = self._admit(context, REGISTER_ACTION)
        digest = ContentDigest(content_hash) if isinstance(content_hash, str) else content_hash
        if payload is not None:
            if not isinstance(payload, bytes):
                raise ValueError("reference payload must be bytes")
            if not digest.matches(payload):
                raise TaskError("reference_digest_mismatch",
                                "the asserted digest does not match the supplied content")
            if len(payload) != size_bytes:
                raise TaskError("reference_size_mismatch",
                                "the asserted size does not match the supplied content")
        if media_ref.startswith(OWNED_REFERENCE_PREFIX) and payload is None:
            raise TaskError("reference_payload_missing", "role-owned reference material requires its original image bytes")
        asset = ReferenceAsset(asset_id=asset_id, kind=ReferenceKind(kind), role=ReferenceRole(role),
                               content_hash=digest, size_bytes=size_bytes, mime_type=mime_type,
                               media_ref=media_ref, source=ReferenceSource(source), label=label,
                               revision=1, created_at=now, updated_at=now)
        return self.assets.put(self.owner(context), asset, created_at=now,
                               payload=payload if media_ref.startswith(OWNED_REFERENCE_PREFIX) else None)

    @outcome
    def add_role(self, context, asset_id, role):
        """Declare one more use for existing material; never implied by the first."""
        now, _options = self._admit(context, REGISTER_ACTION)
        owner = self.owner(context)
        if self.assets.get(owner, asset_id) is None:
            raise TaskError("reference_asset_missing", "the reference material is not registered")
        return self.assets.add_role(owner, asset_id, role, created_at=now)

    @outcome
    def withdraw_asset(self, context, asset_id):
        # Withdrawal stays available while the switch is off: the switch governs
        # whether material may be *used*, not whether the operator can clean up.
        now, _options = self._admit(context, USE_ACTION, require_enabled=False)
        return self.assets.withdraw(self.owner(context), asset_id, updated_at=now)

    @outcome
    def withdraw_source(self, context, source):
        now, _options = self._admit(context, USE_ACTION, require_enabled=False)
        return self.assets.withdraw_source(self.owner(context), source, updated_at=now)

    # --- digest check at use time ------------------------------------------------

    def verify_asset(self, context, asset_id, payload):
        """The C2-1 rule: tampered material is refused when it is used.

        Returns the registration so a caller can read its declared roles; raises
        when the content no longer matches the digest recorded at registration.
        """
        self._admit(context, USE_ACTION)
        asset = self.assets.get(self.owner(context), asset_id)
        if asset is None:
            raise TaskError("reference_asset_missing", "the reference material is not registered")
        if asset.state is not ReferenceState.ACTIVE:
            raise TaskError("reference_asset_withdrawn", "the reference material was withdrawn")
        if not asset.verify(payload):
            raise TaskError("reference_fingerprint_mismatch",
                            "the reference material changed since it was registered")
        return asset

    def read_asset(self, context, asset_id):
        """Read one active role-owned picture and its explicitly declared roles."""
        self._admit(context, USE_ACTION)
        owner = self.owner(context)
        asset = self.assets.get(owner, asset_id)
        if asset is None:
            raise TaskError("reference_asset_missing", "the reference material is not registered")
        if asset.state is not ReferenceState.ACTIVE:
            raise TaskError("reference_asset_withdrawn", "the reference material was withdrawn")
        payload = self.assets.get_payload(owner, asset_id)
        if payload is None or not asset.media_ref.startswith(OWNED_REFERENCE_PREFIX):
            raise TaskError("reference_media_unavailable", "the reference material has no V2-owned image bytes")
        if not asset.verify(payload):
            raise TaskError("reference_fingerprint_mismatch",
                            "the reference material changed since it was registered")
        roles = self.assets.roles_of(owner, asset_id)
        return asset, payload, roles

    # --- wardrobe ----------------------------------------------------------------

    def list_items(self, context, *, state=ReferenceState.ACTIVE, allow_disabled=False):
        self._admit(context, USE_ACTION, require_enabled=not allow_disabled)
        return self.assets.list_items(self.owner(context), state=state)

    def get_item(self, context, item_id):
        self._admit(context, USE_ACTION)
        item = self.assets.get_item(self.owner(context), item_id)
        if item is None:
            raise TaskError("wardrobe_item_missing", "the wardrobe item is unavailable")
        return item
    @outcome
    def register_item(self, context, *, item_id, name, description="", tags=(), asset_id=None,
                      source=ReferenceSource.OPERATOR):
        now, _options = self._admit(context, WARDROBE_ACTION, require_enabled=False)
        item = WardrobeItem(item_id=item_id, name=name, description=description,
                            tags=tuple(sorted(tags)), asset_id=asset_id,
                            source=ReferenceSource(source), revision=1,
                            created_at=now, updated_at=now)
        return self.assets.put_item(self.owner(context), item, created_at=now)

    @outcome
    def withdraw_item(self, context, item_id):
        now, _options = self._admit(context, WARDROBE_ACTION, require_enabled=False)
        return self.assets.withdraw_item(self.owner(context), item_id, updated_at=now)

    # --- daily outfit ------------------------------------------------------------

    def daily_outfit_options(self, context):
        """The rotation window, but only when the daily-outfit path is switched on.

        Agreeing to reference pictures is not agreement to a picture every day, so
        this is a second switch rather than a consequence of the first.
        """
        _now, options = self._admit(context, USE_ACTION, require_enabled=False)
        if not options["daily_outfit_enabled"]:
            raise TaskError("daily_outfit_disabled", "the daily outfit path is off for this role")
        return {"rotation_days": options["daily_outfit_rotation_days"],
                "enabled": bool(options["enabled"])}

    def recent_outfit_items(self, context):
        """Active wardrobe items that may be considered for today's outfit."""
        options = self.daily_outfit_options(context)
        if not options["enabled"]:
            return ()
        return self.list_items(context)

    def prepare_daily_outfit(self, context, *, local_date):
        """Persist the role's daily outfit choice before a read-only prompt snapshot."""
        from datetime import date
        if type(local_date) is not str or date.fromisoformat(local_date).isoformat() != local_date:
            raise TaskError("daily_outfit_date_invalid", "daily outfit selection requires a local ISO date")
        options = self.daily_outfit_options(context)
        if not options["enabled"] or self.wardrobe is None:
            return None
        now = normalize_utc(self.tasks.now(), field="daily outfit observed time")
        wardrobe = self.wardrobe.read(context, now)
        if not wardrobe["enabled"]:
            return None
        candidates = self.assets.list_items(self.owner(context))
        return self.assets.choose_daily_item(self.owner(context), local_date,
            rotation_days=options["rotation_days"], candidates=candidates, created_at=now)

    # --- wardrobe prompt material -------------------------------------------------

    def wardrobe_options(self, context):
        """The role's wardrobe switches, or permissive defaults when unwired."""
        now, _options = self._admit(context, USE_ACTION, require_enabled=False)
        if self.wardrobe is None:
            return {"enabled": True, "prompt_enabled": True, "prompt_max_items": 12,
                    "image_max_count": 3, "tendency": "", "image_prompt": ""}
        return self.wardrobe.read(context, now)

    def wardrobe_material(self, context):
        """What the wardrobe may contribute to one turn, or nothing at all.

        Returns ``{"tendency", "items"}``. The wardrobe must be enabled *and* the
        prompt switch on, and the item list is bounded by ``prompt_max_items`` —
        the ceiling bounds the material, it does not truncate stored items.
        """
        options = self.wardrobe_options(context)
        if not options["enabled"] or not options["prompt_enabled"]:
            return {"tendency": "", "items": ()}
        items = self.assets.list_items(self.owner(context), limit=options["prompt_max_items"])
        return {"tendency": options["tendency"], "items": items}

    def prompt_material(self, context, *, local_date):
        """Return bounded, role-scoped references and wardrobe guidance for Image."""
        from datetime import date
        if type(local_date) is not str or date.fromisoformat(local_date).isoformat() != local_date:
            raise TaskError("daily_outfit_date_invalid", "daily outfit selection requires a local ISO date")
        now, reference_options = self._admit(context, USE_ACTION, require_enabled=False)
        if not reference_options["enabled"]:
            return {"references": (), "wardrobe": None, "daily_outfit": None}
        references = []
        for asset in self.assets.list(self.owner(context)):
            if not asset.media_ref.startswith(OWNED_REFERENCE_PREFIX):
                continue
            roles = self.assets.roles_of(self.owner(context), asset.asset_id)
            references.append({"id": asset.asset_id, "label": asset.label, "roles": roles,
                               "usage": self.assets.usage_of(self.owner(context), asset.asset_id)})
        if context.scope.kind is ScopeKind.GROUP:
            return {"references": tuple(references), "wardrobe": None, "daily_outfit": None}
        wardrobe_options = self.wardrobe_options(context)
        if not wardrobe_options["enabled"] or not wardrobe_options["prompt_enabled"]:
            return {"references": tuple(references), "wardrobe": None, "daily_outfit": None}
        items = self.assets.list_items(self.owner(context), limit=wardrobe_options["prompt_max_items"])
        wardrobe = {"tendency": wardrobe_options["tendency"],
                    "image_prompt": wardrobe_options["image_prompt"],
                    "items": tuple({"id": item.item_id, "name": item.name,
                                    "description": item.description, "tags": item.tags,
                                    "asset_id": item.asset_id} for item in items)}
        daily = None
        if reference_options["daily_outfit_enabled"]:
            selected = self.assets.daily_outfit_item(self.owner(context), local_date)
            if selected is not None:
                outfit_asset_id = None
                if selected.asset_id is not None:
                    linked = self.assets.get(self.owner(context), selected.asset_id)
                    if (linked is not None and linked.state is ReferenceState.ACTIVE
                            and ReferenceRole.OUTFIT.value in self.assets.roles_of(self.owner(context), linked.asset_id)):
                        outfit_asset_id = linked.asset_id
                daily = {"local_date": local_date, "rotation_days": reference_options["daily_outfit_rotation_days"],
                         "item_id": selected.item_id, "name": selected.name,
                         "description": selected.description, "tags": selected.tags,
                         "asset_id": outfit_asset_id}
        return {"references": tuple(references), "wardrobe": wardrobe, "daily_outfit": daily}

    def outfit_image_budget(self, context):
        """How many pictures one outfit request may take in."""
        return self.wardrobe_options(context)["image_max_count"]
