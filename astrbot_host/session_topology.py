"""Native session metadata and reviewed topology, without copying chat history."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging

from companion_v2.adapters.astrbot.group import group_event_route
from companion_v2.adapters.astrbot.telegram import event_client as resolve_event_client
from companion_v2.adapters.astrbot.native import (
    account_number,
    bounded_native_lookup,
    configured_default_persona_id,
    event_account_id,
    event_peer_id,
    native_client_connected,
    platform_client_matches,
    route_id,
    supported_platform_name,
)
from companion_v2.execution.models import TaskError, digest
from .identity_derivation import derive_bot_id, derive_role_id, role_display_name
from .native import SESSION_SETTINGS_LOOKUP_BUDGET_SECONDS, load_session_settings
from .multi_settings import find_persona_mapping
from .display_names import display_names, event_person_names, platform_label


logger = logging.getLogger(__name__)


def session_route(umo):
    if not isinstance(umo, str):
        return None
    parts = umo.split(":")
    if len(parts) != 3 or not parts[0]:
        return None
    platform, kind, session = parts
    try:
        if kind == "FriendMessage":
            return platform, "private", route_id(session)
        if kind == "GroupMessage":
            # Legacy OneBot per-member group sessions are ``sender_group``.
            # QQ Official group sessions are opaque group openids and must be
            # kept whole even when they happen to contain an underscore.
            ids = session.split("_")
            if len(ids) in (1, 2):
                try:
                    for value in ids:
                        account_number(value)
                    return platform, "group", ids[-1]
                except ValueError:
                    pass
            return platform, "group", route_id(session)
    except ValueError:
        pass
    return None


class SessionTopology:
    def __init__(self, controller):
        self.controller = controller
        self.path = controller.data_root / "setup" / "session_directory.json"
        self._pending = set()
        self._closed = False
        self._write_lock = asyncio.Lock()
        self._persona_role_snapshot = {}

    def schedule_observe(self, event):
        """Keep optional directory IO outside native reply hooks."""
        if self._closed or self.controller._terminated:
            return None

        async def record():
            try:
                await self.observe(event)
            except Exception as error:
                detail = str(error)
                if "unsupported native event platform" in detail:
                    logger.debug("[CompanionV2] 跳过非常规平台事件: %s", detail[:120])
                else:
                    logger.warning("[CompanionV2] 会话目录后台更新失败 error_type=%s reason=%s",
                                   type(error).__name__, detail[:200])

        task = asyncio.create_task(record(), name="companion-v2-session-observation")
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return task

    async def close(self):
        """Drain started writes; a cancelled to_thread call could outlive shutdown."""
        self._closed = True
        if self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)

    def configured(self, settings=None):
        controller = self.controller
        if settings is not None:
            return [{**vars(target), "scope_kind": kind}
                    for targets, kind in ((settings.targets, "private"), (settings.group_targets, "group"))
                    for target in targets]
        if controller._multi_host:
            section = controller.config.get("multi_host", {})
            return [{**item, "scope_kind": kind}
                    for field, kind in (("targets", "private"), ("group_targets", "group"))
                    for item in section.get(field, []) if isinstance(item, dict)]
        return controller.status().get("targets", [])

    def _native_platforms(self):
        """按多个宿主版本可能提供的入口取平台实例。

        AstrBot 升级后 `platform_manager` 上的取数入口可能改名（4.28 是
        `get_insts()`，之后出现过 `platform_insts` 与 `context.platforms`）。
        只依赖其中一个名字会让整页读出“0 个会话/0 个账号”。
        """
        context = self.controller.context
        manager = getattr(context, "platform_manager", None)
        enumerator = getattr(manager, "get_insts", None)
        if callable(enumerator):
            try:
                values = list(enumerator() or [])
            except Exception:
                values = []
            if values:
                return values
        insts = getattr(manager, "platform_insts", None)
        if isinstance(insts, (list, tuple)) and insts:
            return list(insts)
        platforms = getattr(context, "platforms", None)
        if isinstance(platforms, dict) and platforms:
            return list(platforms.values())
        single = getattr(context, "platform", None)
        return [single] if single is not None else []

    def accounts(self, settings=None):
        platforms = self._native_platforms()
        configured = self.configured(settings)
        aliases = {(row["platform_id"], row["account_id"]): row["bot_id"]
                   for row in configured
                   if row.get("platform_id") and row.get("account_id") and row.get("bot_id")}
        if settings is not None:
            registered = [{"platform_id": account.platform_id, "account_id": account.self_id,
                           "bot_id": account.bot_id} for account in settings.accounts]
        else:
            shared = self.controller.config.get("multi_host", {})
            registered = shared.get("accounts", []) if isinstance(shared, dict) else []
        rows = list(configured)
        for row in registered if isinstance(registered, list) else ():
            if (isinstance(row, dict) and all(isinstance(row.get(field), str) and row[field].strip()
                    for field in ("platform_id", "account_id", "bot_id"))):
                aliases[(row["platform_id"], row["account_id"])] = row["bot_id"]
                rows.append(row)
        result = {}
        platform_names = {}

        def remember(platform_id, account_id, bot_id=None, *, platform_name=""):
            platform_id, account_id = str(platform_id or "").strip(), str(account_id or "").strip()
            if not platform_id or not account_id:
                return
            previous = result.get((platform_id, account_id), {})
            result[(platform_id, account_id)] = {
                **previous,
                "platform_id": platform_id, "account_id": account_id,
                "bot_id": bot_id or aliases.get((platform_id, account_id)) or derive_bot_id(account_id),
                # platform_id is the configured instance key (for example
                # ``test``); platform_name is the adapter family used for UI
                # labels (for example ``qq_official``).
                "platform_name": platform_name or previous.get("platform_name", ""),
            }

        # Resolve adapter family names before configured rows are remembered.
        for platform in platforms:
            try:
                meta = platform.meta()
                if supported_platform_name(meta.name):
                    platform_names[str(meta.id)] = str(meta.name)
            except (AttributeError, ValueError, TypeError):
                continue

        # Configured accounts remain visible before a reverse websocket has
        # registered its live client. A fresh install has no ``accounts`` rows
        # yet, so targets also seed the picker.
        for row in rows:
            platform_id = row.get("platform_id")
            remember(platform_id, row.get("account_id"), row.get("bot_id"),
                     platform_name=platform_names.get(str(platform_id or ""), ""))
        for platform in platforms:
            try:
                meta = platform.meta()
                if not supported_platform_name(meta.name):
                    continue
                if meta.name == "aiocqhttp":
                    clients = getattr(platform.get_client(), "_wsr_api_clients", {})
                    for raw_id in clients:
                        remember(meta.id, account_number(raw_id), platform_name=meta.name)
                else:
                    # QQ Official, Lark and Telegram do not expose a OneBot
                    # self-id. Their stable account key is the platform
                    # instance id, also used by event_identity.
                    remember(meta.id, meta.id, platform_name=meta.name)
                    if meta.name == "telegram":
                        # Telegram's account key is an opaque instance id; a
                        # cached bot username keeps the picker readable without
                        # a second platform call. Observed labels still win.
                        label = display_names(self.controller).cached_label(
                            meta.id, meta.id, "account", meta.id)
                        row = result.get((meta.id, meta.id))
                        if label and row is not None:
                            row["account_name"] = label
            except (AttributeError, ValueError, TypeError):
                continue
        rows = sorted(result.values(), key=lambda row: (row["platform_id"], row["account_id"]))
        if not rows:
            # 升级后排障：把“为什么一个账号都没发现”一次性说清楚。
            logger.info("[CompanionV2] 账号发现为空 native_platforms=%d configured=%d registered=%d "
                        "platform_names=%s", len(platforms), len(configured),
                        len(registered) if isinstance(registered, list) else 0,
                        ",".join(sorted(set(platform_names.values()))) or "-")
        return rows

    def _observed(self):
        if not self.path.is_file():
            return {}
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(rows, dict):
            raise TaskError("session_directory_invalid", "the native session directory is unreadable")
        return rows

    def stored_group_names(self):
        """Read observed labels without discovering sessions or resolving roles."""
        try:
            rows = self._observed()
        except (OSError, ValueError, TaskError) as error:
            logger.warning("[CompanionV2] 已保存群名读取失败 error_type=%s", type(error).__name__)
            return {}
        names = {}
        for row in sorted((row for row in rows.values() if isinstance(row, dict)),
                          key=lambda row: str(row.get("last_seen_at") or ""), reverse=True):
            route = session_route(row.get("unified_msg_origin"))
            account = row.get("account_id")
            if route is None or route[1] != "group" or not isinstance(account, str) or not account:
                continue
            # Older group display_name fields may contain a speaker's nickname.
            name = platform_label(row.get("group_name"), route[2])
            if name:
                names.setdefault((route[0], account, route[2]), name)
        return names

    async def services(self, umo):
        return await load_session_settings(umo)

    def persona_selection_snapshot(self, umo, account_id):
        """Return the last installed window-to-persona/role mapping."""
        snapshot = self._persona_role_snapshot.get((umo, account_id))
        if snapshot is not None:
            return snapshot
        route = session_route(umo)
        if route is None:
            return None
        platform, kind, _peer = route
        native_kind = "FriendMessage" if kind == "private" else "GroupMessage"
        return self._persona_role_snapshot.get((f"{platform}:{native_kind}:*", account_id))

    def update_persona_selection_snapshot(self, umo, account_id, bot_id, persona_id, role_id, conversation_id):
        """Refresh one verified live window after a native lookup or conversation reset."""
        controller = self.controller
        settings = controller.settings if controller._multi_host else None
        route = session_route(umo)
        if settings is None or route is None:
            return
        platform, kind, peer = route
        target = (settings.private_target(platform, account_id, peer) if kind == "private"
                  else settings.group_target(platform, account_id, peer))
        mapping = find_persona_mapping(settings.personas, bot_id, persona_id)
        if mapping is not None and mapping.role_id != role_id:
            mapping = None
        if (target is None or target.bot_id != bot_id or mapping is None
                or not any(item.self_id == account_id and item.bot_id == bot_id for item in settings.accounts)):
            return
        self._persona_role_snapshot[(umo, account_id)] = {
            "bot_id": bot_id, "persona_id": persona_id, "role_id": role_id,
            "conversation_id": conversation_id, "selection_source": "verified",
        }

    def persona_mapping_snapshot(self, umo, account_id, bot_id):
        snapshot = self.persona_selection_snapshot(umo, account_id)
        settings = self.controller.settings
        if snapshot is None or settings is None or snapshot["bot_id"] != bot_id:
            return None
        mapping = find_persona_mapping(settings.personas, bot_id, snapshot["persona_id"])
        return mapping if mapping is not None and mapping.role_id == snapshot["role_id"] else None

    def _publish_persona_role_snapshot(self, targets, *, complete, scope_kind=None):
        discovered = {}
        for row in targets:
            if (row.get("native") is not True or row.get("details_available") is not True
                    or not row.get("unified_msg_origin") or not row.get("account_id")
                    or not row.get("bot_id") or not row.get("persona_id")):
                continue
            role = next((item for item in row.get("roles", ())
                         if row["persona_id"] in item.get("persona_ids", ())), None)
            if not role or not role.get("role_id"):
                continue
            discovered[(row["unified_msg_origin"], row["account_id"])] = {
                "bot_id": row["bot_id"], "persona_id": row["persona_id"],
                "role_id": role["role_id"], "conversation_id": row.get("conversation_id"),
                "selection_source": "conversation",
            }

        if complete:
            retained = {key: value for key, value in self._persona_role_snapshot.items()
                        if scope_kind is not None and (session_route(key[0]) is None
                                                       or session_route(key[0])[1] != scope_kind)}
            retained.update(discovered)
            self._persona_role_snapshot = retained
        else:
            self._persona_role_snapshot.update(discovered)

    async def _seed_configured_persona_snapshot(self, settings, *, scope_kind=None):
        if settings is None:
            return
        mappings = tuple(settings.personas)
        windows = []
        windows.extend((target, f"{target.platform_id}:FriendMessage:{target.participant_id}", "private")
                       for target in settings.targets if target.participant_id != "*")
        windows.extend((target, f"{target.platform_id}:GroupMessage:{target.group_id}", "group")
                       for target in settings.group_targets if target.group_id != "*")
        windows.extend((target, f"{target.platform_id}:FriendMessage:*", "private")
                       for target in settings.targets if target.participant_id == "*")
        windows.extend((target, f"{target.platform_id}:GroupMessage:*", "group")
                       for target in settings.group_targets if target.group_id == "*")
        for target, umo, kind in windows:
            if scope_kind is not None and kind != scope_kind:
                continue
            key = (umo, target.account_id)
            if key in self._persona_role_snapshot:
                continue
            candidates = [item for item in mappings if item.bot_id == target.bot_id]
            override_lookup = getattr(self.controller.native_types, "session_persona_override_lookup", None)
            try:
                selected = (await bounded_native_lookup(override_lookup(umo), operation="session_settings")
                            if callable(override_lookup) and not umo.endswith(":*") else None)
                if selected == "":
                    selected = None
                if selected is None:
                    # A single registered mapping is not a native persona choice.
                    selected = await self._selected_native_persona(umo, None)
            except TaskError:
                continue
            mapping = find_persona_mapping(candidates, target.bot_id, selected)
            if mapping is None:
                continue
            self._persona_role_snapshot[key] = {
                "bot_id": target.bot_id, "persona_id": selected or mapping.persona_id,
                "role_id": mapping.role_id, "conversation_id": None,
                "selection_source": "configured",
            }

    async def _selected_native_persona(self, umo, conversation_persona_id):
        # AstrBot routes a group's persona through the per-UMO agent config. The
        # conversation row still carries the private/default persona, and passing
        # it here would make every group window look like the private persona.
        if type(umo) is str and ":GroupMessage:" in umo:
            configured = configured_default_persona_id(self.controller.context, umo)
            if (configured not in (None, "", "default", "[%None]")
                    and conversation_persona_id not in ("default", "[%None]")):
                conversation_persona_id = None
        lookup = getattr(self.controller.native_types, "persona_selection_lookup", None)
        if callable(lookup):
            resolved = lookup(self.controller.context, umo, conversation_persona_id)
            return (await bounded_native_lookup(resolved, operation="persona", report_slow=False)
                    if inspect.isawaitable(resolved) else resolved)
        return conversation_persona_id or configured_default_persona_id(self.controller.context, umo)

    async def window_persona(self, platform_id, participant_id):
        """The persona AstrBot resolves for one private window, or "".

        A bind command without a persona name is meant to follow the window's own
        voice, but pinning an empty string is not enough: the runtime maps personas
        by exact name, so the empty pin becomes "[%None]" and a window that resolves
        a real persona is rejected with ``host_persona_window_unbound``. Resolve the
        name once, at bind time, so the pinned role matches what will speak.
        """
        if not all(type(value) is str and value for value in (platform_id, participant_id)):
            return ""
        umo = f"{platform_id}:FriendMessage:{participant_id}"
        manager = getattr(self.controller.context, "conversation_manager", None)
        if manager is None:
            return ""
        try:
            conversation_id = await bounded_native_lookup(
                manager.get_curr_conversation_id(umo), operation="conversation_id")
            conversation_persona_id = None
            if conversation_id is not None:
                conversation = await bounded_native_lookup(
                    manager.get_conversation(umo, conversation_id, create_if_not_exists=False),
                    operation="conversation")
                if conversation is not None:
                    conversation_persona_id = getattr(conversation, "persona_id", None)
            selected = await self._selected_native_persona(umo, conversation_persona_id)
        except (TaskError, AttributeError, TypeError, ValueError, KeyError):
            return ""
        if not isinstance(selected, str):
            return ""
        selected = selected.strip()
        if not selected or selected == "default" or selected == "[%None]":
            return ""
        if len(selected) > 120 or any(ord(char) < 32 for char in selected):
            return ""
        return selected

    async def reply_enabled(self, event):
        try:
            services = await asyncio.wait_for(
                self.services(event.unified_msg_origin), timeout=SESSION_SETTINGS_LOOKUP_BUDGET_SECONDS)
        except TimeoutError:
            logger.warning("[CompanionV2] 会话回复开关读取超时，继续 AstrBot 核心回复")
            return True
        return services.get("session_enabled", True)

    async def observe(self, event):
        controller = self.controller
        if controller._terminated or not isinstance(event, controller.native_types.event):
            return
        route = session_route(event.unified_msg_origin)
        if route is None:
            return
        platform_id, kind, peer = route
        platform = controller.context.get_platform_inst(platform_id)
        message, session, meta = event.message_obj, event.session, event.platform_meta
        login = event_account_id(event)
        if platform is None:
            logger.debug("[CompanionV2] 跳过事件：找不到平台实例 %s", platform_id)
            return
        if not supported_platform_name(meta.name):
            logger.debug("[CompanionV2] 跳过事件：平台 %s 不在支持名单", meta.name)
            return
        if not platform_client_matches(platform, resolve_event_client(event)):
            logger.debug(
                "[CompanionV2] 跳过事件：事件携带的 bot(%s) 不是平台当前的 client",
                type(getattr(event, "bot", None)).__name__,
            )
            return
        if not native_client_connected(platform, login):
            logger.debug("[CompanionV2] 跳过事件：账号 %s 没有可用的原生连接", login)
            return
        if kind == "group":
            native = group_event_route(event)
            if (native.platform_id, native.account_id, native.group_id) != (platform_id, login, peer):
                return
        elif (message.group_id or event_peer_id(event) != peer
              or getattr(message.type, "value", None) != "FriendMessage"
              or getattr(session.message_type, "value", None) != "FriendMessage"
              or session.session_id != peer or session.platform_id != platform_id
              or session.platform_name != platform_id or meta.id != platform_id):
            return
        manager = controller.context.conversation_manager
        cid = await bounded_native_lookup(manager.get_curr_conversation_id(event.unified_msg_origin),
                                          operation="conversation_id")
        if controller._terminated:
            return
        key = digest([platform_id, login, event.unified_msg_origin])
        row = {"unified_msg_origin": event.unified_msg_origin, "account_id": login,
               "conversation_id": cid, "last_seen_at": controller.now().isoformat()}
        if kind == "group":
            group = getattr(message, "group", None)
            group_name = platform_label(getattr(group, "group_name", None), peer)
            if group_name:
                row["group_name"] = group_name
        else:
            nickname, _ = event_person_names(event)
            nickname = nickname or platform_label(getattr(getattr(message, "sender", None), "nickname", None), peer)
            if nickname:
                row["display_name"] = nickname
        async with self._write_lock:
            await asyncio.to_thread(self._store_observation, key, row)

    def _store_observation(self, key, row):
        rows = self._observed()
        route = session_route(row.get("unified_msg_origin"))
        if route:
            field = "group_name" if route[1] == "group" else "display_name"
            previous_name = platform_label(rows.get(key, {}).get(field), route[2])
            # A missing field on an older host or a failed name lookup must not
            # erase the last recognized name. New observations replace ID fallbacks.
            if not row.get(field) and previous_name:
                row[field] = previous_name
        rows[key] = row
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(rows, ensure_ascii=False, sort_keys=True) + "\n",
                             encoding="utf-8", newline="\n")
        temporary.replace(self.path)

    async def _native_sessions(self, platforms, *, scope_kind=None):
        manager = getattr(self.controller.context, "conversation_manager", None)
        reader = getattr(getattr(manager, "db", None), "get_session_conversations", None)
        rows = {}
        issues = []
        if callable(reader):
            for platform in sorted(platforms):
                page = 1
                while True:
                    filters = ({"search_query": ":FriendMessage:" if scope_kind == "private" else ":GroupMessage:"}
                               if scope_kind else {})
                    try:
                        batch, total = await bounded_native_lookup(
                            reader(page=page, page_size=100, platform=platform, **filters),
                            operation="session_directory")
                    except TaskError as error:
                        issues.append(self._directory_issue(error, platform_id=platform))
                        break
                    except Exception as error:
                        issues.append(self._directory_issue(error, platform_id=platform,
                                                            default_stage="session_directory"))
                        break
                    for item in batch:
                        umo = item.get("session_id")
                        route = session_route(umo)
                        if route and (scope_kind is None or route[1] == scope_kind):
                            rows[umo] = {**item, "unified_msg_origin": umo}
                    if not batch or page * 100 >= total:
                        break
                    page += 1
        else:
            for umo, cid in getattr(manager, "session_conversations", {}).items():
                route = session_route(umo)
                if route and route[0] in platforms and (scope_kind is None or route[1] == scope_kind):
                    rows[umo] = {"unified_msg_origin": umo, "conversation_id": cid}
        return rows, issues

    async def inventory(self, *, settings=None, scope_kind=None):
        return (await self.inventory_report(settings=settings, scope_kind=scope_kind))["targets"]

    async def inventory_report(self, *, settings=None, scope_kind=None, include_unmanaged=False):
        # Discovery needs unbound windows even when normalized runtime settings
        # supply the account and persona mappings. Startup still limits its inventory.
        managed_only = settings is not None and not include_unmanaged
        accounts, configured = self.accounts(settings), self.configured(settings)
        if scope_kind is not None:
            configured = [row for row in configured if row["scope_kind"] == scope_kind]
        if managed_only:
            routes = {(row["platform_id"], row["account_id"]) for row in configured}
            accounts = [row for row in accounts if (row["platform_id"], row["account_id"]) in routes]
        platforms = {row["platform_id"] for row in accounts}
        native, issues = await self._native_sessions(platforms, scope_kind=scope_kind)
        observed = {}
        for row in self._observed().values():
            umo = row.get("unified_msg_origin")
            route = session_route(umo)
            if route and route[0] in platforms and (scope_kind is None or route[1] == scope_kind):
                observed.setdefault(umo, []).append(row)
                native.setdefault(umo, {"unified_msg_origin": umo,
                                       "conversation_id": row.get("conversation_id")})
        mappings = ([vars(row) for row in settings.personas] if settings is not None else
                    self.controller.config.get("multi_host", {}).get("personas", []) if self.controller._multi_host else [])
        slots = asyncio.Semaphore(4)

        async def read(umo, item):
            async with slots:
                try:
                    rows = await self._session_windows(umo, item, accounts, configured, mappings, observed,
                                                       managed_only=managed_only)
                    return rows, None
                except TaskError as error:
                    return self._partial_windows(umo, accounts, configured, observed), error

        # One stale or slow conversation must not erase the rest of the directory.
        native_items = list(native.items())
        tasks = [asyncio.create_task(read(umo, item)) for umo, item in native_items]
        batches = await asyncio.gather(*tasks, return_exceptions=True)
        result = []
        for (umo, _), value in zip(native_items, batches):
            if isinstance(value, BaseException):
                if isinstance(value, asyncio.CancelledError):
                    raise value
                issues.append(self._directory_issue(value))
                result.extend(self._partial_windows(umo, accounts, configured, observed))
                continue
            rows, error = value
            result.extend(rows)
            if error is not None:
                issues.append(self._directory_issue(error))
        represented = {row["target_id"] for row in result}
        for binding in configured:
            if binding["target_id"] not in represented:
                result.append({**binding, "session_key": "configured-" + binding["target_id"],
                               "managed": True, "native": False,
                                "roles": binding.get("roles") or [{"role_id": row["role_id"],
                                    "role_name": role_display_name(row["role_id"], [row["persona_id"]]),
                                    "persona_ids": [row["persona_id"]]}
                                   for row in mappings if row.get("bot_id") == binding["bot_id"]]})
        targets = sorted(result, key=lambda row: (not row["managed"], row["platform_id"], row["account_id"], row["session_key"]))
        complete = not issues
        self._publish_persona_role_snapshot(targets, complete=complete, scope_kind=scope_kind)
        snapshot_settings = settings if settings is not None else (
            self.controller.settings if self.controller._multi_host else None)
        await self._seed_configured_persona_snapshot(snapshot_settings, scope_kind=scope_kind)
        return {"targets": targets, "complete": complete, "issues": self._unique_issues(issues)}

    @staticmethod
    def _directory_issue(error, *, platform_id="", default_stage="details"):
        code = getattr(error, "code", "session_details_unavailable")
        reason = str(error) if isinstance(error, TaskError) else "AstrBot 会话详情未能读取。"
        stage = code.rsplit("__", 1)[-1] if code.startswith("host_native_lookup_timeout__") else default_stage
        return {"code": code, "stage": stage, "platform_id": platform_id, "reason": reason}

    @staticmethod
    def _unique_issues(issues):
        counts = {}
        for issue in issues:
            key = (issue["code"], issue["stage"], issue["platform_id"], issue["reason"])
            counts[key] = counts.get(key, 0) + 1
        return [{"code": code, "stage": stage, "platform_id": platform, "reason": reason, "count": count}
                for (code, stage, platform, reason), count in sorted(counts.items())]

    @staticmethod
    def _partial_windows(umo, accounts, configured, observed):
        route = session_route(umo)
        if route is None:
            return []
        platform, kind, peer = route
        peer_field = "group_id" if kind == "group" else "participant_id"
        explicit = {row.get("account_id") for row in observed.get(umo, []) if row.get("account_id")}
        bindings = [row for row in configured if row.get("platform_id") == platform
                    and row.get("scope_kind") == kind and row.get(peer_field) == peer]
        if bindings:
            return []
        if explicit:
            selected = [row for row in accounts if row["platform_id"] == platform
                        and row["account_id"] in explicit]
        elif bindings:
            selected = [{"platform_id": row["platform_id"], "account_id": row["account_id"],
                         "bot_id": row["bot_id"]} for row in bindings]
        else:
            selected = [{"platform_id": platform, "account_id": "", "bot_id": ""}]

        result = []
        for account in selected:
            account_id = account.get("account_id", "")
            observed_name = next((name for row in sorted(observed.get(umo, []),
                key=lambda value: value.get("last_seen_at") or "", reverse=True)
                if row.get("account_id") == account_id
                and (name := platform_label(row.get("group_name" if kind == "group" else "display_name"), peer))), "")
            binding = next((row for row in bindings if row.get("account_id") == account_id), None)
            session_key = "session-" + digest([platform, account_id, umo])[:24]
            result.append({**account, "session_key": session_key,
                "target_id": binding["target_id"] if binding else session_key,
                "scope_kind": kind, peer_field: peer, "unified_msg_origin": umo,
                **({"display_name": observed_name} if observed_name else {}),
                **({"group_name": observed_name} if kind == "group" and observed_name else {}),
                "conversation_id": "", "managed": False, "native": True,
                "details_available": False, "persona_id": "", "persona_override": "",
                "persona_source": "unresolved",
                "llm_enabled": True, "session_enabled": True, "persona_pinned": False, "roles": []})
        return result

    async def _session_windows(self, umo, item, accounts, configured, mappings, observed, *, managed_only):
        manager = getattr(self.controller.context, "conversation_manager", None)
        result = []
        platform, kind, peer = session_route(umo)
        candidates = [row for row in accounts if row["platform_id"] == platform]
        if not candidates:
            return result
        peer_field = "group_id" if kind == "group" else "participant_id"
        account_matches = [row for row in configured if row["platform_id"] == platform
                           and row["scope_kind"] == kind and row.get(peer_field) in (peer, "*")]
        exact = {row["account_id"] for row in observed.get(umo, [])}
        if exact:
            candidates = [row for row in candidates if row["account_id"] in exact]
        elif len(candidates) > 1:
            ids = {row["account_id"] for row in account_matches}
            candidates = [row for row in candidates if row["account_id"] in ids] if len(ids) == 1 else []
        if managed_only:
            candidates = [row for row in candidates if any(
                binding["account_id"] == row["account_id"] for binding in account_matches)]
            if not candidates:
                return result
        if not candidates:
            candidates = [{"platform_id": platform, "account_id": "", "bot_id": ""}]
        cid = await bounded_native_lookup(manager.get_curr_conversation_id(umo), operation="conversation_id")
        if cid != item.get("conversation_id") or cid is None and umo not in observed:
            return result
        services = await bounded_native_lookup(self.services(umo), operation="session_settings")
        override = services.get("persona_id")
        if override == "":
            override = None
        if override is not None:
            if (type(override) is not str or not override.strip() or len(override) > 120
                    or any(ord(char) < 32 for char in override)):
                raise TaskError("host_persona_invalid", "the V2 window persona override is invalid")
        for account in candidates:
            observed_name = next((name for row in sorted(observed.get(umo, []),
                key=lambda value: value.get("last_seen_at") or "", reverse=True)
                if row.get("account_id") == account["account_id"]
                and (name := platform_label(row.get("group_name" if kind == "group" else "display_name"), peer))), "")
            native_name = (observed_name or platform_label(item.get("group_name"), peer)
                           or platform_label(item.get("display_name"), peer))
            matches = [row for row in account_matches if row["account_id"] == account["account_id"]]
            binding = next((row for row in matches if row.get(peer_field) == peer), None)
            binding = binding or next((row for row in matches if row.get(peer_field) == "*"), None)
            if override is not None:
                selected = override
                persona_source = "v2_window_override"
            else:
                if self.controller._multi_host:
                    native_persona = await self._selected_native_persona(umo, item.get("persona_id"))
                    selected = native_persona if type(native_persona) is str else ""
                    persona_source = "astrbot_selection" if selected else "unresolved"
                elif not self.controller._multi_host:
                    configured_ids = {persona_id for row in matches for role_row in row.get("roles", [])
                                      for persona_id in role_row.get("persona_ids", []) if persona_id}
                    selected = next(iter(configured_ids)) if len(configured_ids) == 1 else ""
                    persona_source = "role_mapping" if selected and binding else "unresolved"
            mapping = find_persona_mapping(mappings, account["bot_id"], selected)
            role = mapping.get("role_id") if isinstance(mapping, dict) else getattr(mapping, "role_id", None)
            if binding and not self.controller._multi_host:
                role = binding["roles"][0]["role_id"]
            key = "session-" + digest([platform, account["account_id"], umo])[:24]
            roles = ([{"role_id": role, "role_name": role_display_name(role, [selected]),
                       "persona_ids": [selected]}] if role else [])
            result.append({**account, "session_key": key, "target_id": binding["target_id"] if binding else key,
                "scope_kind": kind, "group_id" if kind == "group" else "participant_id": peer,
                **({"display_name": native_name} if native_name else {}),
                **({"group_name": native_name} if kind == "group" and native_name else {}),
                "unified_msg_origin": umo, "conversation_id": cid,
                "managed": binding is not None and (not self.controller._multi_host or role is not None),
                "native": True, "details_available": True,
                "persona_id": selected, "persona_override": services.get("persona_id") or "",
                "persona_source": persona_source,
                "llm_enabled": services.get("llm_enabled", True),
                "session_enabled": services.get("session_enabled", True),
                "persona_pinned": bool(binding and not self.controller._multi_host),
                "roles": roles})
        return result

    async def select(self, session_key):
        return next((row for row in await self.inventory() if row["session_key"] == session_key), None)
