"""Authenticated Dashboard API for the native image album, references and wardrobe."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json

from astrbot.api.web import request

from companion_v2.configuration.models import ConfigurationError
from companion_v2.execution.models import TaskError, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.references.management import (
    ALBUM_LIMIT,
    ALBUM_MAX_LIMIT,
    ReferenceMaterialManagement,
)
from companion_v2.storage.runtime import StorageOpenError

from .management_identity import dashboard_operator


PREFIX = "/astrbot_plugin_private_companion/image-materials"
MAX_BODY = 8192
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
MAX_UPLOAD_BODY = ((MAX_UPLOAD_BYTES + 2) // 3) * 4 + 16 * 1024


class ImageMaterialsWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self):
        return [
            (PREFIX, self.inventory, ["GET"], "Read one role's native image materials"),
            (PREFIX + "/album", self.album, ["GET"], "Read the role-scoped generated image album"),
            (PREFIX + "/album/image", self.album_image, ["GET"], "Read one full-resolution generated album image"),
            (PREFIX + "/album/export", self.export_album, ["POST"], "Export selected generated images as a ZIP archive"),
            (PREFIX + "/album/hide", self.hide_album, ["POST"], "Remove selected generated images from the album"),
            (PREFIX + "/album/open-folder", self.open_album_folder, ["POST"], "Open the role-scoped generated album folder"),
            (PREFIX + "/references/upload", self.upload_reference, ["POST"], "Upload one role-owned reference image"),
            (PREFIX + "/references", self.register_reference, ["POST"], "Register one explicit album image reference"),
            (PREFIX + "/references/update", self.update_reference, ["POST"], "Update one role image reference"),
            (PREFIX + "/references/order", self.reorder_references, ["POST"], "Save one role's reference gallery order"),
            (PREFIX + "/references/withdraw", self.withdraw_reference, ["POST"], "Withdraw one role image reference"),
            (PREFIX + "/wardrobe", self.register_wardrobe, ["POST"], "Add one role wardrobe item"),
            (PREFIX + "/wardrobe/withdraw", self.withdraw_wardrobe, ["POST"], "Withdraw one role wardrobe item"),
            (PREFIX + "/daily-outfit", self.select_daily_outfit, ["POST"], "Choose today's role outfit"),
            (PREFIX + "/daily-outfit/photo", self.daily_outfit_photo, ["GET"], "Read today's generated outfit photo"),
            (PREFIX + "/daily-outfit/generate", self.generate_daily_outfit_photo, ["POST"], "Generate today's role outfit photo"),
        ]

    def _snapshot(self, *, allowed_query=()):
        controller = self.controller
        username = request.username
        if type(username) is not str or not username.strip() or username.startswith("api_key:"):
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
        query = request.query
        query_keys = tuple(query.keys())
        if set(query_keys).difference(allowed_query) or any(
                len(query.getlist(key)) != 1 for key in query_keys):
            raise TaskError("image_material_request_invalid", "unexpected query fields")
        if controller._terminated:
            raise TaskError("staging_host_terminated", "the plugin has stopped")
        session = controller.session or controller.last_session
        runtime = controller.runtime
        if session is None or runtime is None:
            raise TaskError("staging_not_initialized", "load the V2 profile before managing image materials")
        context = session.profile.target
        if getattr(context, "scope", None) is None or context.scope.kind is not ScopeKind.PRIVATE:
            raise TaskError("reference_scope_invalid", "select a configured private target and role")
        if session.application.runtime is not runtime:
            raise TaskError("staging_profile_changed", "the selected profile no longer owns this V2 data root")
        return username, session, runtime, context, controller.now()

    @staticmethod
    def _authorize_material_access(username, session, runtime, context, at):
        operator = dashboard_operator(runtime, username, at=at)
        require(ObjectAccessPolicy(runtime).feature_access(
            operator,
            scope=ScopeRef(runtime.installation_id, "companion", ScopeKind.SYSTEM, "page.management"),
            action="memory.manage",
            observed_at=at,
        ))
        return operator, context, at, ReferenceMaterialManagement(session.application)

    @staticmethod
    def _document(raw, expected, optional=()):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate field")
                result[key] = value
            return result

        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite value")))
        if (type(value) is not dict or not set(expected).issubset(value)
                or set(value).difference(set(expected).union(optional))):
            raise ValueError("unexpected or missing fields")
        return value

    async def _run(self, action, *, query=None, body=None):
        from .web_api import outcome_response

        controller = self.controller
        try:
            async with controller._control_lock:
                username, session, runtime, context, at = self._snapshot(allowed_query=query or ())

            def execute():
                operator, ctx, observed_at, manager = self._authorize_material_access(
                    username, session, runtime, context, at)
                if action == "inventory":
                    return manager.inventory(operator, ctx, at=observed_at)
                if action == "album":
                    return manager.album(operator, ctx, at=observed_at, **query)
                if action == "album_image":
                    return manager.album_image(operator, ctx, at=observed_at, **query)
                if action == "export_album":
                    return manager.export_album(operator, ctx, at=observed_at, image_ids=body["image_ids"])
                if action == "hide_album":
                    return manager.hide_album_images(operator, ctx, at=observed_at, image_ids=body["image_ids"])
                if action == "open_album_folder":
                    return manager.open_album_folder(operator, ctx, at=observed_at, role_id=body.get("role_id"))
                if action == "register_reference":
                    return manager.register_from_album(operator, ctx, at=observed_at, **body)
                if action == "upload_reference":
                    payload, mime_type = self._decode_upload(body["data_url"])
                    return manager.register_uploaded_reference(
                        operator, ctx, at=observed_at, payload=payload, mime_type=mime_type,
                        label=body["label"], roles=body["roles"], operation_key=body["operation_key"])
                if action == "update_reference":
                    return manager.update_reference(operator, ctx, body["asset_id"], at=observed_at,
                                                    label=body["label"], roles=body["roles"],
                                                    expected_revision=body["expected_revision"],
                                                    usage=body.get("usage"))
                if action == "reorder_references":
                    return manager.reorder_references(
                        operator, ctx, expected_asset_ids=body["expected_asset_ids"],
                        asset_ids=body["asset_ids"], at=observed_at)
                if action == "withdraw_reference":
                    return manager.withdraw_reference(operator, ctx, body["asset_id"], at=observed_at,
                                                      expected_revision=body["expected_revision"])
                if action == "register_wardrobe":
                    return manager.register_wardrobe_item(operator, ctx, at=observed_at, **body)
                if action == "withdraw_wardrobe":
                    return manager.withdraw_wardrobe_item(operator, ctx, body["item_id"], at=observed_at)
                if action == "daily_outfit_photo":
                    return manager.daily_outfit_photo(operator, ctx, at=observed_at)
                if action == "generate_daily_outfit_photo":
                    return manager.generate_daily_outfit_photo(operator, ctx, at=observed_at, **body)
                return manager.select_daily_outfit(operator, ctx, body["item_id"], at=observed_at)

            value = await asyncio.to_thread(execute)
            return outcome_response(Outcome.success(value))
        except (TaskError, ConfigurationError) as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except StorageOpenError:
            return outcome_response(Outcome.failed(
                "image_material_storage_unavailable",
                "image material storage is temporarily unavailable",
            ))
        except (ValueError, TypeError, KeyError):
            return outcome_response(Outcome.failed(
                "image_material_request_invalid",
                "image material values are invalid",
            ))

    async def inventory(self):
        if request.query:
            from .web_api import outcome_response
            return outcome_response(Outcome.failed("image_material_request_invalid", "unexpected query fields"))
        return await self._run("inventory")

    async def album(self):
        from .web_api import outcome_response

        query = request.query
        query_keys = tuple(query.keys())
        if set(query_keys) - {"cursor", "limit"} or any(len(query.getlist(key)) != 1 for key in query_keys):
            return outcome_response(Outcome.failed("image_material_request_invalid", "unknown or repeated album query fields"))
        cursor = query.get("cursor")
        if cursor is not None and (type(cursor) is not str or not 8 <= len(cursor) <= 1024):
            return outcome_response(Outcome.failed("generated_album_cursor_invalid", "the album cursor is invalid"))
        raw_limit = query.get("limit")
        if raw_limit is None:
            limit = ALBUM_LIMIT
        elif type(raw_limit) is str and raw_limit.isascii() and raw_limit.isdigit():
            digits = raw_limit.lstrip("0")
            limit = int(digits) if 0 < len(digits) <= len(str(ALBUM_MAX_LIMIT)) else 0
        else:
            limit = 0
        if not 1 <= limit <= ALBUM_MAX_LIMIT:
            return outcome_response(Outcome.failed(
                "generated_album_limit_invalid", "the album page size is outside its bounds"))
        return await self._run("album", query={"cursor": cursor, "limit": limit})

    async def album_image(self):
        from .web_api import outcome_response

        query = request.query
        query_keys = tuple(query.keys())
        if set(query_keys) != {"image_id"} or any(len(query.getlist(key)) != 1 for key in query_keys):
            return outcome_response(Outcome.failed(
                "image_material_request_invalid", "image_id query parameter is required"))
        image_id = query.get("image_id")
        if type(image_id) is not str or not 1 <= len(image_id) <= 180:
            return outcome_response(Outcome.failed(
                "generated_image_missing", "the selected album image is unavailable"))
        return await self._run("album_image", query={"image_id": image_id})

    @staticmethod
    def _decode_upload(data_url):
        if type(data_url) is not str or not data_url.startswith("data:"):
            raise TaskError("reference_image_invalid", "select a PNG, JPEG or WebP image")
        header, separator, encoded = data_url.partition(",")
        mime_type = header[5:].removesuffix(";base64").lower()
        if (not separator or not header.endswith(";base64")
                or mime_type not in {"image/png", "image/jpeg", "image/webp"}):
            raise TaskError("reference_image_type_invalid", "reference images support PNG, JPEG and WebP")
        if not encoded or len(encoded) > ((MAX_UPLOAD_BYTES + 2) // 3) * 4:
            raise TaskError("reference_upload_too_large", "reference images must be 12 MiB or smaller")
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise TaskError("reference_image_invalid", "the uploaded image data is incomplete") from error
        if not payload or len(payload) > MAX_UPLOAD_BYTES:
            raise TaskError("reference_upload_too_large", "reference images must be 12 MiB or smaller")
        return payload, mime_type

    async def _post(self, action, expected, *, max_body=MAX_BODY, optional=()):
        from .web_api import outcome_response

        try:
            raw = await request.body()
            if len(raw) > max_body or request.query:
                raise ValueError("request is too large or has query fields")
            body = self._document(raw, expected, optional)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("image_material_request_invalid", "invalid UTF-8 image material request"))
        return await self._run(action, body=body)

    async def export_album(self):
        return await self._post("export_album", {"image_ids"})

    async def hide_album(self):
        return await self._post("hide_album", {"image_ids"})

    async def open_album_folder(self):
        return await self._post("open_album_folder", set(), optional={"role_id"})

    async def register_reference(self):
        return await self._post("register_reference", {"image_id", "label", "roles", "operation_key"})

    async def upload_reference(self):
        return await self._post("upload_reference", {"data_url", "label", "roles", "operation_key"},
                                max_body=MAX_UPLOAD_BODY)

    async def update_reference(self):
        return await self._post("update_reference", {"asset_id", "label", "roles", "expected_revision"}, optional={"usage"})

    async def reorder_references(self):
        return await self._post("reorder_references", {"expected_asset_ids", "asset_ids"})

    async def withdraw_reference(self):
        return await self._post("withdraw_reference", {"asset_id", "expected_revision"})

    async def register_wardrobe(self):
        return await self._post("register_wardrobe", {"operation_key", "name", "description", "tags", "asset_id"})

    async def withdraw_wardrobe(self):
        return await self._post("withdraw_wardrobe", {"item_id"})

    async def select_daily_outfit(self):
        return await self._post("select_daily_outfit", {"item_id"})

    async def daily_outfit_photo(self):
        return await self._run("daily_outfit_photo")

    async def generate_daily_outfit_photo(self):
        return await self._post("generate_daily_outfit_photo", {"operation_key"})
