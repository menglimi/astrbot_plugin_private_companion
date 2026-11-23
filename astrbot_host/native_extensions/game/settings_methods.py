"""V2 game settings services."""
from __future__ import annotations
from .common import *

class GameSettingsMethods:

    def _configured_access_base(self) -> str:
        return self.public_base_url or getattr(self, 'external_base_url', '')

    def _register_page_api(self) -> tuple[str, ...]:
        from .management import GAME_MANAGEMENT_ACTIONS
        self._registered_management_actions = tuple(sorted(GAME_MANAGEMENT_ACTIONS))
        return self._registered_management_actions

    def management_snapshot(self) -> dict[str, Any]:
        from .management import snapshot
        return snapshot(self)

    async def management_action(self, action_name: str, payload: dict[str, Any], current) -> dict[str, Any]:
        from .management import action
        return await action(self, action_name, payload, current)

    async def page_rooms(self) -> dict[str, Any]:
        return {'status': 'ok', 'data': {'rooms': [room.admin_snapshot() for room in self.manager.rooms.values()], 'native_games': self._native_provider.list_web_games() if self._native_provider is not None else [], 'server': {'running': self.room_server.running, 'port': self.room_server.port if self.room_server.running else None, 'public_base_url': self.public_base_url, 'external_base_url': self.external_base_url, 'access_host': self.access_host}, 'tunnel': self.quick_tunnel.status(), 'xiangqi_engine': self.xiangqi_engine.status(), 'limits': {'group': self.manager.max_group_rooms, 'private': self.manager.max_private_rooms}, 'enabled_games': dict(self.manager.enabled_games)}}

    async def page_native_games(self) -> dict[str, Any]:
        provider = self._native_provider
        return {'status': 'ok', 'data': {'games': provider.list_web_games() if provider is not None else [], 'available': provider is not None}}

    async def page_native_game_open(self) -> dict[str, Any]:
        payload = await request.json(default={}) or {}
        game_id = str(payload.get('game_id') or '').strip()
        provider = self._native_provider
        if provider is None:
            return {'status': 'error', 'message': 'V2 原生游戏执行端未启动', 'data': {}}
        try:
            ticket = provider.issue_web_ticket(game_id)
            base_url = await self._ensure_mobile_room_access()
        except (ValueError, RuntimeError, OSError) as exc:
            return {'status': 'error', 'message': str(exc), 'data': {}}
        return {'status': 'ok', 'data': {'ticket': ticket, 'url': f"{base_url.rstrip('/')}/native/{quote(ticket, safe='')}", 'expires_in': 12 * 60 * 60}}

    async def page_native_game_state(self) -> dict[str, Any]:
        payload = await request.json(default={}) or {}
        provider = self._native_provider
        if provider is None:
            return {'status': 'error', 'message': 'V2 原生游戏执行端未启动', 'data': {}}
        try:
            return {'status': 'ok', 'data': await provider.web_state(str(payload.get('ticket') or ''))}
        except ValueError as exc:
            return {'status': 'error', 'message': str(exc), 'data': {}}

    async def page_native_game_action(self) -> dict[str, Any]:
        payload = await request.json(default={}) or {}
        provider = self._native_provider
        if provider is None:
            return {'status': 'error', 'message': 'V2 原生游戏执行端未启动', 'data': {}}
        ticket = str(payload.pop('ticket', '') or '')
        try:
            return {'status': 'ok', 'data': await provider.web_action(ticket, payload)}
        except (ValueError, RuntimeError) as exc:
            return {'status': 'error', 'message': str(exc), 'data': {}}

    async def page_room_action(self) -> dict[str, Any]:
        payload = await request.json(default={}) or {}
        room = self.manager.rooms.get(str(payload.get('room_id') or ''))
        if room is None:
            return {'status': 'error', 'message': '房间不存在或已经结束', 'data': {}}
        action = str(payload.get('action') or '').strip().lower()
        try:
            if action == 'assign':
                await self.manager.assign_player(room, int(payload.get('visitor_number') or 0), str(payload.get('player_qq') or ''))
            elif action == 'demote':
                await self.manager.remove_player(room, int(payload.get('visitor_number') or 0))
            elif action == 'kick':
                await self.manager.kick_visitor(room, int(payload.get('visitor_number') or 0))
            elif action == 'pause':
                await self.manager.pause(room)
            elif action == 'resume':
                await self.manager.resume(room)
            elif action == 'switch_game':
                await self.manager.switch_game(room, self._game_type(payload.get('game_type')), force=self._value_bool(payload.get('confirm_abandon')))
            elif action == 'close':
                await self.manager.destroy(room.room_id, '管理员关闭了房间')
            else:
                raise ValueError('不支持的管理操作')
        except (ValueError, RuntimeError, PermissionError) as exc:
            return {'status': 'error', 'message': str(exc), 'data': {}}
        return {'status': 'ok', 'data': {'room_id': room.room_id, 'action': action}}

    async def page_xiangqi_install(self) -> dict[str, Any]:
        try:
            status = await self.xiangqi_engine.install_latest()
        except (ValueError, RuntimeError, PermissionError, OSError) as exc:
            return {'status': 'error', 'message': str(exc), 'data': {}}
        return {'status': 'ok', 'data': {'xiangqi_engine': status}}

    async def page_cloudflared_install(self) -> dict[str, Any]:
        try:
            status = await self.quick_tunnel.install_latest()
        except (ValueError, RuntimeError, PermissionError, OSError) as exc:
            logger.warning('[GameCompanion] cloudflared 安装失败: %s', exc)
            return {'status': 'error', 'message': str(exc), 'data': {}}
        return {'status': 'ok', 'data': {'tunnel': status}}

    async def page_game_settings(self) -> dict[str, Any]:
        return {'status': 'ok', 'data': self._game_settings_snapshot()}

    async def page_game_settings_update(self) -> dict[str, Any]:
        payload = await request.json(default={}) or {}
        try:
            changes = self._validated_game_settings(payload)
            async with self._settings_lock:
                patch = self._game_settings_config_patch(changes)
                await self._persist_game_settings(patch)
                self._apply_game_settings_runtime()
        except (TypeError, ValueError, RuntimeError) as exc:
            return {'status': 'error', 'message': str(exc), 'data': {}}
        return {'status': 'ok', 'message': '游戏配置已保存；新房间和新一局将使用最新设置', 'data': self._game_settings_snapshot()}

    def _game_settings_config_patch(self, changes: dict[str, Any]) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        for dotted_key, value in changes.items():
            if dotted_key in self.config:
                patch[dotted_key] = value
                continue
            section, key = dotted_key.split('.', 1)
            if section not in patch:
                current = self.config.get(section, {})
                patch[section] = dict(current) if isinstance(current, dict) else {}
            patch[section][key] = value
        return patch

    async def page_tunnel_start(self) -> dict[str, Any]:
        if self._configured_access_base():
            return {'status': 'error', 'message': '已配置外部访问地址', 'data': {}}
        try:
            if not self.room_server.running:
                await self.room_server.start()
            self.quick_tunnel.local_url = self.room_server.local_base_url
            url = await self.quick_tunnel.start(timeout=40)
        except Exception as exc:
            return {'status': 'error', 'message': str(exc), 'data': {}}
        return {'status': 'ok', 'data': {'url': url, 'tunnel': self.quick_tunnel.status()}}

    async def page_tunnel_stop(self) -> dict[str, Any]:
        native_games = self._native_provider.list_web_games() if self._native_provider is not None else []
        if self.manager.rooms or native_games:
            return {'status': 'error', 'message': '仍有活动对局，不能停止访问通道', 'data': {}}
        await self.quick_tunnel.stop()
        await self.room_server.stop()
        return {'status': 'ok', 'data': {'tunnel': self.quick_tunnel.status()}}

    def _cfg(self, dotted_key: str, default: Any=None) -> Any:
        if dotted_key in self.config:
            return self.config.get(dotted_key, default)
        current: Any = self.config
        for part in dotted_key.split('.'):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current.get(part)
        return default if current is None else current

    def _cfg_str(self, dotted_key: str, default: str='') -> str:
        return str(self._cfg(dotted_key, default) or '').strip()

    def _cfg_bool(self, dotted_key: str, default: bool) -> bool:
        value = self._cfg(dotted_key, default)
        if isinstance(value, str):
            return value.strip().lower() in {'true', '1', 'yes', 'on', '是', '开启'}
        return bool(value)

    def _cfg_int(self, dotted_key: str, default: int, *, minimum: int, maximum: int) -> int:
        try:
            value = int(self._cfg(dotted_key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    def _cfg_non_negative(self, dotted_key: str, default: int) -> int:
        try:
            return max(0, int(self._cfg(dotted_key, default)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _admin_room_requested(message: str, tool_value: bool=False) -> bool:
        """Keep explicit room mode reliable even if the model omits the tool flag."""
        if tool_value:
            return True
        normalized = re.sub('[\\s，。！!？?、]', '', str(message or '').lower())
        if any((phrase in normalized for phrase in ('普通房间', '普通模式', '不要管理员房间', '不是管理员房间', '非管理员房间'))):
            return False
        return any((phrase in normalized for phrase in ('管理员房间', '管理员模式', '管理房', '审核房间', '需要我审核玩家')))
