"""Dashboard-owned account bindings and native session controls."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
import logging

from astrbot.api.web import request
from astrbot.core import sp

from companion_v2.adapters.astrbot.native import route_id
from companion_v2.execution.models import TaskError, digest
from .multi_settings import MultiHostSettings
from .native import remember_session_settings
from .settings import HostControlError
from .bootstrap_provider import (resolve_bootstrap_provider, supported_text_provider,
                                 text_provider_catalog, text_provider_predicate)


PREFIX = "/astrbot_plugin_private_companion/accounts"
logger = logging.getLogger(__name__)


class AccountSessionWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self):
        return [(PREFIX, self.get, ["GET"], "Read account bindings and native session topology"),
                (PREFIX, self.save, ["POST"], "Apply reviewed account and group bindings"),
                (PREFIX + "/model-provider", self.save_model_provider, ["POST"],
                 "Choose the shared model provider used to initialize the V2 host"),
                (PREFIX + "/role", self.add_role, ["POST"],
                 "Create a companion role for one connected Bot and selected native personas"),
                (PREFIX + "/window-link", self.link_window, ["POST"],
                 "Merge private windows into a primary window or share their role"),
                (PREFIX + "/window-unlink", self.unlink_window, ["POST"],
                 "Remove one private-window merge"),
                (PREFIX + "/group-target", self.save_group_target, ["POST"],
                 "Enable or disable one discovered group target"),
                (PREFIX + "/session", self.save_session, ["POST"], "Manage a discovered native session")]

    @staticmethod
    def _admin():
        username = request.username
        if type(username) is not str or not username.strip() or username.startswith("api_key:"):
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard as an administrator")
        if request.query:
            raise TaskError("account_config_invalid", "query fields are not allowed")

    def _model_providers(self):
        """Return loaded AstrBot providers that can start the V2 host."""
        context = getattr(self.controller, "context", None)
        expected_type = text_provider_predicate(getattr(self.controller, "native_types", None))
        if context is None:
            return []
        return text_provider_catalog(context, expected_type)

    def _configured_provider_id(self):
        config = getattr(self.controller, "config", {})
        section = config.get("model", {}) if isinstance(config, dict) else {}
        return str(section.get("provider_id", "")) if isinstance(section, dict) else ""

    def _model_selection(self):
        context = getattr(self.controller, "context", None)
        expected_type = text_provider_predicate(getattr(self.controller, "native_types", None))
        if context is None:
            return None
        return resolve_bootstrap_provider(context, expected_type, self._configured_provider_id())

    def _model_view(self):
        configured = self._configured_provider_id()
        context = getattr(self.controller, "context", None)
        expected_type = text_provider_predicate(getattr(self.controller, "native_types", None))
        selection = (resolve_bootstrap_provider(context, expected_type, configured)
                     if context is not None else None)
        providers = self._model_providers()
        if selection is not None and selection.provider_id and not any(
                item["id"] == selection.provider_id for item in providers):
            providers.append({"id": selection.provider_id, "name": selection.name or selection.provider_id})
        effective_id = selection.provider_id if selection is not None else ""
        source = selection.source if selection is not None else "unavailable"
        if configured and source == "fallback":
            # A saved provider is a reviewed choice; never switch it silently.
            effective_id, source = configured, "unavailable"
        return {"provider_id": effective_id, "provider_source": source,
                "configured_provider_id": configured, "providers": providers}

    def _view(self):
        controller = self.controller
        section = controller.config.get("multi_host", {})
        targets = deepcopy(section.get("targets", []))
        groups = deepcopy(section.get("group_targets", []))
        personas = deepcopy(section.get("personas", []))
        try:
            # Reading through the same parser as the runtime keeps a persona that
            # is shared by several Bot accounts on one role id in the editor too.
            personas = [asdict(mapping) for mapping in MultiHostSettings.parse(controller.config).personas]
        except (HostControlError, ValueError):
            pass
        enabled = bool(section.get("enabled", False))
        accounts = {(row["platform_id"], row["account_id"], row["bot_id"]):
                    {name: row[name] for name in ("platform_id", "account_id", "bot_id")}
                    for row in [*section.get("accounts", []), *targets, *groups]}
        return {"mode": "multi", "enabled": enabled,
                "accounts": list(accounts.values()), "personas": personas, "sessions": targets, "groups": groups,
                "window_links": deepcopy(section.get("window_links", [])),
                "model": self._model_view()}

    async def _discovered(self):
        from .setup_api import SetupWebAPI
        response = await SetupWebAPI(self.controller).api_targets()
        if response.get("status") != "ok":
            data = response.get("data") if isinstance(response.get("data"), dict) else {}
            code = data.get("code") or response.get("message") or "setup_targets_unavailable"
            reason = response.get("reason") or "the native session directory could not be read"
            raise TaskError(code, reason)
        return response["data"]

    async def get(self):
        try:
            self._admin()
            return {"status": "ok", "data": {**self._view(), "discovered": await self._discovered()}}
        except (TaskError, HostControlError) as error:
            return self._error(error.code, str(error))

    async def add_role(self):
        """Add one role without replacing concurrent account, window or group edits."""
        try:
            self._admin()
            body = await self._body()
            if set(body) != {"platform_id", "account_id", "bot_id", "persona_ids", "role_id"}:
                raise TaskError("account_config_invalid", "select a Bot, at least one native persona and a role name")
            platform_id, account_id, bot_id, persona_ids, role_id = (body[key] for key in
                ("platform_id", "account_id", "bot_id", "persona_ids", "role_id"))
            if any(type(value) is not str or not value.strip() or len(value) > 128
                   for value in (platform_id, account_id, bot_id, role_id)) or (
                   type(persona_ids) is not list or not persona_ids or len(persona_ids) > 128 or
                   any(type(value) is not str or not value.strip() or len(value) > 128
                       for value in persona_ids) or len(set(persona_ids)) != len(persona_ids)):
                raise TaskError("account_config_invalid", "select a Bot, at least one native persona and a role name")
            role_id = role_id.strip()
            account = (platform_id, account_id, bot_id)
            discovered = await self._discovered()
            if not any((item["platform_id"], item["account_id"], item["bot_id"]) == account
                       for item in discovered["accounts"]):
                raise TaskError("account_config_invalid", "the selected Bot is no longer connected")
            available_personas = {item["persona_id"] for item in discovered["personas"]
                                  if item["bot_id"] == bot_id}
            if not set(persona_ids) <= available_personas:
                raise TaskError("host_persona_unavailable", "select a loaded AstrBot persona")
            expected = deepcopy(dict(self.controller.config))
            candidate = deepcopy(expected)
            section = candidate.setdefault("multi_host", {})
            if type(section) is not dict:
                raise TaskError("account_config_invalid", "the shared host configuration is invalid")
            saved_personas = section.setdefault("personas", [])
            if any(item.get("bot_id") == bot_id and item.get("persona_id") in persona_ids
                   for item in saved_personas):
                raise TaskError("account_role_persona_in_use", "this persona already belongs to a role")
            if any(item.get("bot_id") == bot_id and item.get("role_id") == role_id
                   for item in saved_personas):
                raise TaskError("account_role_name_in_use", "choose another role name for this Bot")
            accounts = section.setdefault("accounts", [])
            if not any((item.get("platform_id"), item.get("account_id"), item.get("bot_id")) == account
                       for item in accounts):
                accounts.append(dict(zip(("platform_id", "account_id", "bot_id"), account)))
            targets = section.setdefault("targets", [])
            if not any((item.get("platform_id"), item.get("account_id"), item.get("bot_id")) == account
                       for item in targets):
                targets.append({"target_id": "private-" + digest((platform_id, account_id, "*"))[:20],
                                "platform_id": platform_id, "account_id": account_id,
                                "bot_id": bot_id, "participant_id": "*"})
            saved_personas.extend({"bot_id": bot_id, "persona_id": persona_id, "role_id": role_id}
                                  for persona_id in persona_ids)
            section["enabled"] = True
            MultiHostSettings.parse(candidate)
            result = await self.controller.apply_bindings(candidate, expected_config=expected)
            return {"status": "ok", "data": {**result, "bot_id": bot_id,
                                             "persona_ids": persona_ids, "role_id": role_id}}
        except (TaskError, HostControlError) as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError):
            return self._error("account_config_invalid", "the role could not be validated")
        except OSError:
            return self._error("account_config_save_failed", "the role could not be saved")

    async def link_window(self):
        """Merge selected private windows under one primary window."""
        try:
            self._admin()
            body = await self._body()
            if set(body) != {"primary_target_id", "alias_target_ids", "mode"}:
                raise TaskError("account_config_invalid", "choose a primary window, at least one alias and a merge mode")
            primary, aliases, mode = body["primary_target_id"], body["alias_target_ids"], body["mode"]
            if (type(primary) is not str or not primary.strip() or type(aliases) is not list
                    or not aliases or len(aliases) > 128 or mode not in ("role", "primary")):
                raise TaskError("account_config_invalid", "choose a primary window, at least one alias and a merge mode")
            aliases = sorted({str(item).strip() for item in aliases if type(item) is str and item.strip()})
            if not aliases:
                raise TaskError("account_config_invalid", "choose at least one alias window")
            candidate = deepcopy(dict(self.controller.config))
            section = candidate.setdefault("multi_host", {})
            if mode == "role":
                await self._share_window_roles(section, primary, aliases)
            links = [row for row in section.get("window_links", []) if isinstance(row, dict)
                     and row.get("primary_target_id") != primary
                     and not (set(row.get("alias_target_ids") or []) & set(aliases))]
            links.append({"primary_target_id": primary, "alias_target_ids": aliases, "mode": mode})
            section["window_links"] = links
            MultiHostSettings.parse(candidate)
            result = await self.controller.apply_bindings(candidate)
            return {"status": "ok", "data": {**result, "window_links": links}}
        except (TaskError, HostControlError) as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError):
            return self._error("account_config_invalid", "the window merge could not be validated")

    async def _share_window_roles(self, section, primary, aliases):
        rows = {row.get("target_id"): row for row in await self.controller.session_topology.inventory()
                if row.get("target_id")}
        primary_row = rows.get(primary)
        if primary_row is None or not primary_row.get("persona_id"):
            raise TaskError("account_config_invalid", "the primary window has no resolved persona")
        personas = [row for row in section.get("personas", []) if isinstance(row, dict)]
        primary_bot = next((row.get("bot_id") for row in section.get("targets", [])
                            if row.get("target_id") == primary), None)
        primary_role = next((row.get("role_id") for row in personas
                             if row.get("bot_id") == primary_bot
                             and row.get("persona_id") == primary_row.get("persona_id")), None)
        if not primary_role:
            raise TaskError("account_config_invalid", "the primary window has no role mapping")
        for alias in aliases:
            alias_row = rows.get(alias)
            alias_bot = next((row.get("bot_id") for row in section.get("targets", [])
                              if row.get("target_id") == alias), None)
            if alias_row is None or not alias_bot or not alias_row.get("persona_id"):
                raise TaskError("account_config_invalid", "an alias window has no resolved persona")
            mapping = next((row for row in personas
                            if row.get("bot_id") == alias_bot
                            and row.get("persona_id") == alias_row.get("persona_id")), None)
            if mapping is None:
                raise TaskError("account_config_invalid", "an alias window persona has no role mapping")
            mapping["role_id"] = primary_role
        section["personas"] = personas

    async def unlink_window(self):
        try:
            self._admin()
            body = await self._body()
            if set(body) != {"primary_target_id"}:
                raise TaskError("account_config_invalid", "choose the primary window to unmerge")
            primary = body["primary_target_id"]
            if type(primary) is not str or not primary.strip():
                raise TaskError("account_config_invalid", "choose the primary window to unmerge")
            candidate = deepcopy(dict(self.controller.config))
            section = candidate.setdefault("multi_host", {})
            links = [row for row in section.get("window_links", []) if isinstance(row, dict)
                     and row.get("primary_target_id") != primary]
            section["window_links"] = links
            MultiHostSettings.parse(candidate)
            result = await self.controller.apply_bindings(candidate)
            return {"status": "ok", "data": {**result, "window_links": links}}
        except (TaskError, HostControlError) as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError):
            return self._error("account_config_invalid", "the window merge could not be validated")

    @staticmethod
    def _error(code, reason):
        from .web_api import outcome_response
        from companion_v2.foundation.outcome import Outcome
        return outcome_response(Outcome.failed(code, reason))

    @staticmethod
    async def _body():
        raw = await request.body()
        if len(raw) > 65536:
            raise TaskError("account_config_invalid", "configuration is too large")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TaskError("account_config_invalid", "configuration must be UTF-8 JSON") from error
        if type(data) is not dict:
            raise TaskError("account_config_invalid", "configuration must be an object")
        return data

    async def save_session(self):
        try:
            self._admin()
            body = await self._body()
            if (set(body) - {"session_key", "llm_enabled", "session_enabled", "persona_id"} or len(body) < 2
                    or type(body.get("session_key")) is not str):
                raise TaskError("account_config_invalid", "select a native session and its settings")
            if any(field in body and type(body[field]) is not bool for field in ("llm_enabled", "session_enabled")):
                raise TaskError("account_config_invalid", "session switches must be booleans")
            async with self.controller._control_lock:
                if self.controller._terminated:
                    raise TaskError("staging_host_terminated", "the plugin has stopped")
                row = await self.controller.session_topology.select(body["session_key"])
                if row is None or not row["native"]:
                    raise TaskError("account_session_unavailable", "the selected native session is no longer available")
                if "persona_id" in body:
                    from .persona_catalog import available_personas
                    persona = body["persona_id"]
                    if type(persona) is not str or persona not in ["", "[%None]", *await available_personas(self.controller.context)]:
                        raise TaskError("host_persona_unavailable", "select a currently loaded AstrBot persona")
                    if row["managed"] and persona and not any(
                            item["bot_id"] == row["bot_id"] and item["persona_id"] == persona
                            for item in self._view()["personas"]):
                        raise TaskError("account_session_persona_unmapped", "add this persona to the Bot bindings first")
                umo = row["unified_msg_origin"]
                services = dict(await sp.get_async(scope="umo", scope_id=umo,
                                                   key="session_service_config", default={}) or {})
                for field in ("llm_enabled", "session_enabled"):
                    if field in body:
                        services[field] = body[field]
                if "persona_id" in body:
                    if body["persona_id"]:
                        services["persona_id"] = body["persona_id"]
                    else:
                        services.pop("persona_id", None)
                await sp.put_async(scope="umo", scope_id=umo, key="session_service_config", value=services)
                remember_session_settings(umo, services)
            return {"status": "ok", "data": {"saved": True, "requires_reload": False}}
        except (TaskError, HostControlError) as error:
            return self._error(error.code, str(error))
        except OSError:
            return self._error("account_config_save_failed", "the account settings could not be saved")

    async def save_group_target(self):
        try:
            self._admin()
            controller = self.controller
            body = await self._body()
            fields = {"platform_id", "account_id", "bot_id", "group_id", "enabled"}
            if set(body) != fields or type(body.get("enabled")) is not bool:
                raise TaskError("account_config_invalid", "select one group target and its desired state")
            enabled = body["enabled"]
            platform_id, account_id, bot_id, group_id = (body[name] for name in
                ("platform_id", "account_id", "bot_id", "group_id"))
            if any(type(value) is not str or len(value) > 128
                   for value in (platform_id, account_id, bot_id, group_id)):
                raise TaskError("account_config_invalid", "group target identity fields must be nonempty text")
            if not platform_id.strip() or not group_id.strip():
                raise TaskError("account_config_invalid", "group target identity fields must be nonempty text")
            if enabled and (not account_id.strip() or not bot_id.strip()):
                raise TaskError("account_group_target_route_unresolved",
                                "the native group session is not associated with one connected Bot account yet")
            if not account_id.strip() or not bot_id.strip():
                raise TaskError("account_config_invalid", "group target identity fields must be nonempty text")
            account_id = route_id(account_id)
            group_id = route_id(group_id)
            current = self._view()
            route = (platform_id, account_id, bot_id, group_id)
            group_rows = deepcopy(current["groups"])
            existing = next((row for row in group_rows if
                (row["platform_id"], row["account_id"], row["bot_id"], row["group_id"]) == route), None)
            if enabled and existing is None:
                account_key = (platform_id, account_id, bot_id)
                if account_key not in {(row["platform_id"], row["account_id"], row["bot_id"])
                                       for row in current["accounts"]}:
                    raise TaskError("account_group_target_account_required",
                                    "bind this Bot account and persona in account management first")
                if group_id != "*":
                    discovered = await self._discovered()
                    if not any(row.get("scope_kind") == "group"
                               and row.get("platform_id") == platform_id
                               and row.get("account_id") in ("", account_id)
                               and row.get("group_id") == group_id
                               for row in discovered.get("targets", [])):
                        issue = next((item for item in discovered.get("directory", {}).get("issues", [])
                                      if item.get("platform_id") in ("", platform_id)), None)
                        if issue:
                            raise TaskError(issue["code"], issue["reason"])
                        raise TaskError("account_group_target_unavailable",
                                        "select a group currently present in the native session directory")
                target_id = "group-" + digest((platform_id, account_id, group_id))[:20]
                group_rows.append({"target_id": target_id, "platform_id": platform_id,
                                   "account_id": account_id, "bot_id": bot_id, "group_id": group_id})
            elif enabled:
                target_id = existing["target_id"]
            elif existing is None:
                return {"status": "ok", "data": {"saved": True, "configured": False, "target_id": None}}
            else:
                target_id = existing["target_id"]
                group_rows.remove(existing)

            candidate = deepcopy(dict(controller.config))
            section = candidate.setdefault("multi_host", {})
            if type(section) is not dict:
                raise TaskError("account_config_invalid", "group targets require a valid shared host configuration")
            section["group_targets"] = group_rows
            MultiHostSettings.parse(candidate)
            result = await controller.apply_bindings(candidate)
            return {"status": "ok", "data": {"saved": True, "configured": enabled,
                                                   "target_id": target_id if enabled else None,
                                                   "requires_reload": result["requires_reload"],
                                                   "requires_initialize": result["requires_initialize"]}}
        except (TaskError, HostControlError) as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError):
            return self._error("account_config_invalid", "the selected group target could not be validated")
        except OSError:
            return self._error("account_config_save_failed", "the group access setting could not be saved")
        except Exception as error:
            logger.exception("V2 group target update failed")
            detail = " ".join(str(error).split())
            reason = f"{type(error).__name__}: {detail[:512]}" if detail else type(error).__name__
            return self._error("account_group_target_apply_failed", reason)

    async def save_model_provider(self):
        """Persist the bootstrap text provider without requiring a live role runtime."""
        try:
            self._admin()
            body = await self._body()
            if set(body) != {"provider_id"} or type(body.get("provider_id")) is not str:
                raise TaskError("account_model_provider_invalid", "choose exactly one shared model provider")
            provider_id = body["provider_id"].strip()
            if not provider_id or len(provider_id) > 512:
                raise TaskError("account_model_provider_invalid", "the shared model provider id is invalid")
            available = {item["id"] for item in self._model_view()["providers"]}
            expected_type = text_provider_predicate(getattr(self.controller, "native_types", None))
            provider = (self.controller.context.get_provider_by_id(provider_id)
                        if getattr(self.controller, "context", None) is not None else None)
            # ``expected_type`` may be NativeAdapterTypes.is_text_provider, a bound
            # method; never use it as the second argument of isinstance().
            if (provider is None or provider_id not in available
                    or not supported_text_provider(provider, expected_type)):
                raise TaskError("host_provider_unavailable",
                                "choose a loaded AstrBot text provider supported by V2")
            config = self.controller.config
            if not isinstance(config, dict):
                raise TaskError("account_config_persistence_unavailable",
                                "the plugin configuration cannot be edited here")
            save = getattr(self.controller, "save_plugin_config", None)
            if not callable(save):
                raise TaskError("account_config_persistence_unavailable",
                                "restart the plugin with Dashboard configuration support")
            candidate = deepcopy(dict(config))
            model_settings = candidate.setdefault("model", {})
            if type(model_settings) is not dict:
                raise TaskError("account_model_provider_invalid",
                                "the plugin model settings have an invalid shape")
            model_settings["provider_id"] = provider_id
            profiles = model_settings.get("additional_providers")
            if type(profiles) is list:
                model_settings["additional_providers"] = [
                    profile for profile in profiles
                    if not isinstance(profile, dict) or profile.get("provider_id") != provider_id
                ]
            if model_settings.get("fallback_provider_id") == provider_id:
                model_settings["fallback_provider_id"] = ""
            save(candidate)
            refresh = getattr(self.controller, "refresh_settings", None)
            if callable(refresh):
                refresh()
            prepared = False
            initialization_error = None
            restart_required = False
            if getattr(self.controller, "settings_error", None) is None:
                try:
                    initialized = await self.controller.initialize_profile()
                    failure = getattr(initialized, "failure", None)
                    if failure is None:
                        prepared = True
                    elif getattr(failure, "code", "") == "host_must_stop":
                        # Editing the bootstrap provider while the host is
                        # already serving is valid; apply it on the next reload.
                        prepared = True
                        restart_required = True
                    else:
                        initialization_error = getattr(failure, "code", "host_initialization_failed")
                except Exception as error:
                    initialization_error = getattr(error, "code", "host_initialization_failed")
            return {"status": "ok", "data": {**self._view(), "saved": True,
                "provider_id": provider_id, "prepared": prepared,
                "restart_required": restart_required,
                "initialization_error": initialization_error,
                "status": self.controller.status()}}
        except TaskError as error:
            # ``_admin``/``_body`` are shared helpers and still raise the generic
            # account code; this endpoint owns a separate code so the page never
            # blames role mappings or session sources for a model problem.
            code = ("account_model_provider_invalid" if error.code == "account_config_invalid"
                    else error.code)
            return self._error(code, str(error))
        except HostControlError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, UnicodeError):
            logger.exception("V2 shared model provider update could not be validated")
            return self._error("account_model_provider_invalid",
                               "the shared model provider could not be validated")
        except OSError:
            return self._error("account_config_save_failed", "the shared model provider could not be saved")
        except Exception as error:
            logger.exception("V2 shared model provider update failed")
            detail = " ".join(str(error).split())
            reason = f"{type(error).__name__}: {detail[:512]}" if detail else type(error).__name__
            return self._error("account_model_provider_failed", reason)

    async def save(self):
        try:
            self._admin()
            controller = self.controller
            body = await self._body()
            if set(body) - {"enabled", "accounts", "sessions", "personas", "groups"}:
                raise TaskError("account_config_invalid", "configuration has unknown fields")
            enabled = body.get("enabled", True)
            if type(enabled) is not bool:
                raise TaskError("account_config_invalid", "enabled must be a boolean")
            current = self._view()
            sync_private_targets = "sessions" not in body
            accounts = body.get("accounts")
            sessions = body.get("sessions", current["sessions"])
            personas = body.get("personas")
            groups = body.get("groups", current["groups"])
            if accounts is None:
                accounts = []
            if personas is None:
                personas = current["personas"]
            if any(type(rows) is not list or any(type(row) is not dict for row in rows)
                   for rows in (accounts, sessions, personas, groups)):
                raise TaskError("account_config_invalid", "account bindings must be lists of objects")
            discovered = await self._discovered()
            known = {(row["platform_id"], row["account_id"], row["bot_id"]): row for row in discovered["accounts"]}

            def account_key(row):
                raw = str(row.get("account_key", ""))
                return tuple(raw.split("|")) if raw else tuple(str(row.get(key, "")).strip()
                    for key in ("platform_id", "account_id", "bot_id"))

            selected = {account_key(row) for row in accounts}
            if not selected and not body.get("accounts"):
                selected = set(known)
            if not selected or any(key not in known for key in selected):
                raise TaskError("account_config_invalid", "select currently connected Bot accounts")
            private, group_rows = [], []
            for rows, old, peer_field, kind, output in ((sessions, current["sessions"], "participant_id", "private", private),
                                                      (groups, current["groups"], "group_id", "group", group_rows)):
                existing = {(row["platform_id"], row["account_id"], row[peer_field]): row for row in old}
                for row in rows:
                    key = account_key(row)
                    peer = row.get(peer_field)
                    peer = "*" if peer == "*" else route_id(peer)
                    if kind == "group" and peer == "*":
                        if key not in selected:
                            previous = existing.get((key[0], key[1], peer))
                            if previous is not None and account_key(previous) == key:
                                continue
                            raise TaskError("account_config_invalid",
                                            "each window must select a configured Bot account")
                        previous = existing.get((key[0], key[1], peer))
                        output.append({
                            "target_id": (previous["target_id"] if previous else
                                          "group-" + digest((key[0], key[1], peer))[:20]),
                            "platform_id": key[0], "account_id": key[1],
                            "bot_id": key[2], peer_field: peer,
                        })
                        continue
                    route = (key[0], key[1], peer)
                    previous = existing.get(route)
                    if key not in selected:
                        # A removed account must not be retained by its old windows.
                        # Reject newly supplied references to unselected accounts.
                        if previous is not None and account_key(previous) == key:
                            continue
                        raise TaskError("account_config_invalid", "each window must select a configured Bot account")
                    if kind == "group" and previous is None and not any(
                            item.get("scope_kind") == "group" and item["platform_id"] == key[0]
                            and item.get("account_id") in ("", key[1]) and item.get("group_id") == peer
                            for item in discovered["targets"]):
                        issue = next((item for item in discovered.get("directory", {}).get("issues", [])
                                      if item.get("platform_id") in ("", key[0])), None)
                        if issue:
                            raise TaskError(issue["code"], issue["reason"])
                        raise TaskError("account_config_invalid", "select a group from the native session directory")
                    output.append({"target_id": previous["target_id"] if previous else kind + "-" + digest(route)[:20],
                                   "platform_id": key[0], "account_id": key[1], "bot_id": key[2], peer_field: peer})
            if sync_private_targets:
                # Keep existing private routes and stable IDs. Only accounts with
                # no private target receive a discovery template; groups stay explicit.
                represented = {account_key(row) for row in private}
                for key in sorted(selected - represented):
                    private.append({"target_id": "private-" + digest((key[0], key[1], "*"))[:20],
                                   "platform_id": key[0], "account_id": key[1],
                                   "bot_id": key[2], "participant_id": "*"})
            mapped = []
            available = {(row["bot_id"], row["persona_id"]) for row in discovered["personas"]}
            old_roles = {(row["bot_id"], row["persona_id"]): row["role_id"] for row in current["personas"]}
            selected_bots = {key[2] for key in selected}
            for row in personas:
                key = (str(row.get("bot_id", "")), str(row.get("persona_id", "")))
                if key[0] not in selected_bots:
                    if key in old_roles:
                        continue
                    raise TaskError("account_config_invalid", "each persona must select a configured Bot account")
                if key not in available:
                    raise TaskError("host_persona_unavailable", "select a currently loaded AstrBot persona")
                role_id = str(row.get("role_id", old_roles.get(key, ""))).strip()
                if not role_id or len(role_id) > 128:
                    raise TaskError("account_config_invalid", "each registered persona needs a role name")
                mapped.append({"bot_id": key[0], "persona_id": key[1],
                               "role_id": role_id})
            candidate = deepcopy(dict(controller.config))
            section = candidate.setdefault("multi_host", {})
            section.update(enabled=enabled, accounts=[{"platform_id": key[0], "account_id": key[1], "bot_id": key[2]}
                                                      for key in sorted(selected)],
                           targets=private, personas=mapped, group_targets=group_rows)
            MultiHostSettings.parse(candidate)
            result = await controller.apply_bindings(candidate)
            try:
                discovered = await self._discovered()
            except (TaskError, HostControlError):
                # The configuration is already persisted. A subsequent native
                # directory read must not turn that save into a failure receipt.
                result = {**result, "directory_refresh_pending": True}
            return {"status": "ok", "data": {**result, "configuration": {**self._view(), "discovered": discovered}}}
        except (TaskError, HostControlError) as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError):
            return self._error("account_config_invalid", "the selected account bindings could not be validated")
        except OSError:
            return self._error("account_config_save_failed", "the account bindings could not be saved")
