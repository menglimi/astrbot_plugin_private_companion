"""V2 together settings services."""
from __future__ import annotations
from .common import *

class TogetherSettingsMethods:

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
            lowered = value.strip().lower()
            if lowered in {'true', '1', 'yes', 'on', '开启', '是'}:
                return True
            if lowered in {'false', '0', 'no', 'off', '关闭', '否', ''}:
                return False
        return bool(value)

    def _register_page_api(self) -> None:
        pass

    async def page_status(self) -> dict[str, Any]:
        capabilities = await self._capabilities()
        tunnel = self.quick_tunnel.status()
        tunnel['fixed_public_url'] = self.public_base_url
        return {'status': 'ok', 'data': {'enabled': self.server_enabled, 'running': self.room_server.running, 'base_url': self._room_base_url() if self.room_server.running else '', 'port': self.room_server.port, 'capabilities': capabilities, 'tunnel': tunnel}}

    async def page_start_tunnel(self) -> dict[str, Any]:
        if self.public_base_url:
            return {'status': 'error', 'message': '已配置固定外部访问地址，无需启动临时穿透', 'data': {'url': self.public_base_url}}
        if not self.server_enabled:
            return {'status': 'error', 'message': '房间服务未启用', 'data': {}}
        if not self.room_server.running:
            try:
                await self.room_server.start()
            except Exception as exc:
                return {'status': 'error', 'message': f'房间服务启动失败: {_single_line(exc)}', 'data': {}}
        self.quick_tunnel.local_url = self.room_server.local_base_url
        try:
            url = await self.quick_tunnel.start(timeout=40)
        except Exception as exc:
            logger.warning('[TogetherCompanion] 临时公网访问启动失败: %s', _single_line(exc, 300))
            return {'status': 'error', 'message': _single_line(exc, 300), 'data': {}}
        logger.info('[TogetherCompanion] 临时公网访问已启动: %s', url)
        return {'status': 'ok', 'data': {'url': url, 'tunnel': self.quick_tunnel.status()}}

    async def page_stop_tunnel(self) -> dict[str, Any]:
        await self.quick_tunnel.stop()
        logger.info('[TogetherCompanion] 临时公网访问已停止')
        return {'status': 'ok', 'data': {'tunnel': self.quick_tunnel.status()}}

    async def page_create_room(self) -> dict[str, Any]:
        payload = await request.json(default={}) or {}
        mode = normalize_room_mode(payload.get('mode'))
        if mode == 'work' and (not self.work_collaboration_available()):
            return {'status': 'error', 'message': '工作协同需要 V2 屏幕上下文服务已就绪', 'data': {}}
        if self._get_chat_provider() is None:
            return {'status': 'error', 'message': '请先配置有效的实时共处对话模型', 'data': {}}
        if not self.server_enabled:
            return {'status': 'error', 'message': '房间服务未启用', 'data': {}}
        if not self.room_server.running:
            try:
                await self.room_server.start()
            except Exception as exc:
                return {'status': 'error', 'message': f'房间服务启动失败: {_single_line(exc)}', 'data': {}}
        browser_requested = payload.get('open_browser') is True
        client_host = getattr(request, 'client_host', '')
        request_host = request.headers.get('host', '')
        browser_launch_available = _is_local_dashboard_request(client_host, request_host)
        browser_opened = False
        resolved_user = self._resolve_primary_user_id()
        native_v2 = await self._resolve_native_v2_binding(resolved_user) if resolved_user else None
        if browser_requested and browser_launch_available:
            browser_ticket = self.issue_room_ticket(mode=mode, user_id=resolved_user, native_v2=native_v2)
            browser_url = self._ticket_url(browser_ticket)
            try:
                browser_opened = bool(await asyncio.to_thread(webbrowser.open, browser_url, new=2, autoraise=True))
            except Exception as exc:
                logger.warning('[TogetherCompanion] 唤起系统浏览器失败: mode=%s error=%s', browser_ticket.mode, _single_line(exc))
        elif browser_requested:
            logger.info('[TogetherCompanion] 当前拓展页不是本机访问，跳过服务器浏览器唤起: remote=%s', _single_line(client_host) or '未知')
        ticket = self.issue_room_ticket(mode=mode, user_id=resolved_user, native_v2=native_v2)
        room_url = self._ticket_url(ticket)
        logger.info('[TogetherCompanion] 拓展页已创建房间凭据: mode=%s user=%s expires_in=%ss browser_opened=%s', ticket.mode, ticket.user_id or '未指定', max(0, int(ticket.expires_at - time.time())), browser_opened)
        return {'status': 'ok', 'data': {'url': room_url, 'mode': ticket.mode, 'expires_at': ticket.expires_at, 'browser_opened': browser_opened, 'browser_launch_available': browser_launch_available}}

    async def page_config(self) -> dict[str, Any]:
        return {'status': 'ok', 'data': {'values': self._page_setting_values(), 'providers': await self._page_provider_options()}}

    async def page_save_config(self) -> dict[str, Any]:
        payload = await request.json(default={}) or {}
        values = payload.get('values') if isinstance(payload, dict) and isinstance(payload.get('values'), dict) else {}
        chat_provider_id = _single_line(values.get('conversation.chat_provider_id', getattr(self, 'chat_provider_id', '')), 160)
        chat_provider = self._get_provider_by_id(chat_provider_id) if chat_provider_id else None
        if not chat_provider_id or not callable(getattr(chat_provider, 'text_chat', None)):
            return {'status': 'error', 'message': '请选择一个有效的通话与观影对话模型', 'data': {}}
        updates = self._validate_page_settings(values)
        if not updates:
            return {'status': 'error', 'message': '没有可保存的配置变更', 'data': {}}
        for dotted_key, value in updates.items():
            self._set_config_value(dotted_key, value)
        self._sync_page_settings_runtime()
        persisted = await self._persist_config()
        return {'status': 'ok', 'data': {'values': self._page_setting_values(), 'persisted': persisted, 'message': '配置已保存并立即生效' if persisted else '配置已应用到当前运行实例'}}

    def _page_setting_values(self) -> dict[str, Any]:
        defaults = {'conversation.chat_provider_id': '', 'conversation.vision_provider_id': '', 'conversation.history_turns': 12, 'conversation.enable_memory_context': True, 'conversation.call_proactive_enabled': True, 'conversation.model_hangup_enabled': True, 'conversation.call_idle_seconds': 120, 'conversation.sync_astrbot_conversation': True, 'conversation.record_visible_turns': False, 'conversation.record_shared_experiences': True, 'conversation.system_prompt': '', 'speech.stt_mode': 'auto', 'speech.stt_provider_id': '', 'speech.stt_correction_enabled': True, 'speech.browser_language': 'zh-CN', 'speech.tts_provider_id': '', 'speech.browser_tts_fallback': True, 'speech.direct_multilingual_tts': True, 'speech.tts_timeout_seconds': 60, 'speech.tts_volume_percent': 100, 'speech.realtime_duplex_enabled': False, 'watch.prepare_knowledge': True, 'watch.auto_comment': True, 'watch.comment_interval_seconds': 60, 'watch.scene_min_interval_seconds': 18, 'watch.memory_refresh_seconds': 240, 'watch.duck_video_volume': True, 'watch.duck_volume_percent': 28, 'library.enabled': True, 'library.max_upload_mb': 40, 'library.import_admin_only': True, 'novel.retrieval_top_k': 8, 'novel.spoiler_policy': 'strict', 'novel.answer_with_sources': True, 'novel.auto_save_progress': True}
        return {key: self._cfg(key, default) for key, default in defaults.items()}

    def _validate_page_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        string_limits = {'conversation.chat_provider_id': 160, 'conversation.vision_provider_id': 160, 'speech.stt_provider_id': 160, 'speech.tts_provider_id': 160, 'speech.browser_language': 20}
        boolean_keys = {'conversation.enable_memory_context', 'conversation.call_proactive_enabled', 'conversation.model_hangup_enabled', 'conversation.sync_astrbot_conversation', 'conversation.record_visible_turns', 'conversation.record_shared_experiences', 'speech.stt_correction_enabled', 'speech.browser_tts_fallback', 'speech.direct_multilingual_tts', 'speech.realtime_duplex_enabled', 'watch.prepare_knowledge', 'watch.auto_comment', 'watch.duck_video_volume', 'library.enabled', 'library.import_admin_only', 'novel.answer_with_sources', 'novel.auto_save_progress'}
        integer_ranges = {'conversation.history_turns': (2, 60), 'conversation.call_idle_seconds': (60, 900), 'speech.tts_timeout_seconds': (15, 180), 'speech.tts_volume_percent': (0, 100), 'watch.comment_interval_seconds': (20, 600), 'watch.scene_min_interval_seconds': (8, 120), 'watch.memory_refresh_seconds': (90, 900), 'watch.duck_volume_percent': (5, 80), 'library.max_upload_mb': (1, 200), 'novel.retrieval_top_k': (3, 20)}
        for key, limit in string_limits.items():
            if key in values:
                updates[key] = str(values.get(key) or '').strip()[:limit]
        if 'conversation.system_prompt' in values:
            updates['conversation.system_prompt'] = str(values.get('conversation.system_prompt') or '').strip()[:12000]
        for key in boolean_keys:
            if key in values:
                value = values.get(key)
                updates[key] = value.strip().lower() in {'1', 'true', 'yes', 'on', '开启', '是'} if isinstance(value, str) else bool(value)
        for key, (minimum, maximum) in integer_ranges.items():
            if key in values:
                default_value = 100 if key == 'speech.tts_volume_percent' else minimum
                current_value = _clamp_int(self._cfg(key, default_value), default_value, minimum, maximum)
                updates[key] = _clamp_int(values.get(key), current_value, minimum, maximum)
        if 'speech.stt_mode' in values:
            updates['speech.stt_mode'] = self._normalize_stt_mode(values.get('speech.stt_mode'))
        if 'novel.spoiler_policy' in values:
            spoiler_policy = str(values.get('novel.spoiler_policy') or 'strict').strip().lower()
            updates['novel.spoiler_policy'] = spoiler_policy if spoiler_policy in {'strict', 'ask_first', 'allow'} else 'strict'
        return updates

    def _set_config_value(self, dotted_key: str, value: Any) -> None:
        if dotted_key in self.config:
            self.config[dotted_key] = value
            return
        current: Any = self.config
        parts = dotted_key.split('.')
        for part in parts[:-1]:
            nested = current.get(part) if isinstance(current, dict) else None
            if not isinstance(nested, dict):
                nested = {}
                current[part] = nested
            current = nested
        current[parts[-1]] = value

    def _sync_page_settings_runtime(self) -> None:
        self.chat_provider_id = self._cfg_str('conversation.chat_provider_id', '')
        self.vision_provider_id = self._cfg_str('conversation.vision_provider_id', '')
        self.history_turns = _clamp_int(self._cfg('conversation.history_turns', 12), 12, 2, 60)
        self.enable_memory_context = self._cfg_bool('conversation.enable_memory_context', True)
        self.custom_system_prompt = self._cfg_str('conversation.system_prompt', '')
        self.call_proactive_enabled = self._cfg_bool('conversation.call_proactive_enabled', True)
        self.model_hangup_enabled = self._cfg_bool('conversation.model_hangup_enabled', True)
        self.call_idle_seconds = _clamp_int(self._cfg('conversation.call_idle_seconds', 120), 120, 60, 900)
        self.sync_astrbot_conversation = self._cfg_bool('conversation.sync_astrbot_conversation', True)
        self.record_shared_experiences = self._cfg_bool('conversation.record_shared_experiences', True)
        self.record_visible_turns = self._cfg_bool('conversation.record_visible_turns', False)
        self.stt_mode = self._normalize_stt_mode(self._cfg_str('speech.stt_mode', 'auto'))
        self.stt_provider_id = self._cfg_str('speech.stt_provider_id', '')
        self.stt_correction_enabled = self._cfg_bool('speech.stt_correction_enabled', True)
        self.browser_language = self._cfg_str('speech.browser_language', 'zh-CN') or 'zh-CN'
        self.tts_provider_id = self._cfg_str('speech.tts_provider_id', '')
        self.browser_tts_fallback = self._cfg_bool('speech.browser_tts_fallback', True)
        self.direct_multilingual_tts = self._cfg_bool('speech.direct_multilingual_tts', True)
        self.tts_timeout_seconds = _clamp_int(self._cfg('speech.tts_timeout_seconds', 60), 60, 15, 180)
        self.tts_volume_ratio = _clamp_float(self._cfg('speech.tts_volume_percent', 100), 100.0, 0.0, 100.0) / 100.0
        self.realtime_duplex_enabled = self._cfg_bool('speech.realtime_duplex_enabled', False)
        self.watch_prepare_knowledge = self._cfg_bool('watch.prepare_knowledge', True)
        self.watch_auto_comment = self._cfg_bool('watch.auto_comment', True)
        self.watch_comment_interval_seconds = _clamp_int(self._cfg('watch.comment_interval_seconds', 60), 60, 20, 600)
        self.watch_scene_min_interval_seconds = _clamp_int(self._cfg('watch.scene_min_interval_seconds', 18), 18, 8, 120)
        self.watch_memory_refresh_seconds = _clamp_int(self._cfg('watch.memory_refresh_seconds', 240), 240, 90, 900)
        self.watch_duck_video_volume = self._cfg_bool('watch.duck_video_volume', True)
        self.watch_duck_volume_ratio = _clamp_float(self._cfg('watch.duck_volume_percent', 28), 28.0, 5.0, 80.0) / 100.0
        self.novel_library_enabled = self._cfg_bool('library.enabled', True)
        self.novel_import_admin_only = self._cfg_bool('library.import_admin_only', True)
        self.novel_max_upload_mb = _clamp_int(self._cfg('library.max_upload_mb', 40), 40, 1, 200)
        self.novel_retrieval_top_k = _clamp_int(self._cfg('novel.retrieval_top_k', 8), 8, 3, 20)
        self.novel_spoiler_policy = self._cfg_str('novel.spoiler_policy', 'strict') or 'strict'
        self.novel_answer_with_sources = self._cfg_bool('novel.answer_with_sources', True)
        self.novel_auto_save_progress = self._cfg_bool('novel.auto_save_progress', True)
        if getattr(self, 'novel_library', None) is not None:
            self.novel_library.max_upload_bytes = self.novel_max_upload_mb * 1024 * 1024
        for library in getattr(self, '_native_novel_libraries', {}).values():
            library.max_upload_bytes = self.novel_max_upload_mb * 1024 * 1024

    async def _persist_config(self) -> bool:
        for method_name in ('save_config', 'save', 'save_conf', 'flush', 'dump'):
            method = getattr(self.config, method_name, None)
            if not callable(method):
                continue
            try:
                result = method()
                if inspect.isawaitable(result):
                    await result
                return True
            except TypeError:
                continue
            except Exception as exc:
                logger.debug('[TogetherCompanion] 保存拓展页配置失败: method=%s error=%s', method_name, exc)
        return False

    async def _page_provider_options(self) -> dict[str, list[dict[str, str]]]:
        providers: list[Any] = []
        for getter_name in ('get_all_providers', 'get_all_stt_providers', 'get_all_tts_providers'):
            getter = getattr(self.context, getter_name, None)
            if not callable(getter):
                continue
            try:
                result = getter()
                if inspect.isawaitable(result):
                    result = await result
                providers.extend(list(result or []))
            except Exception as exc:
                logger.debug('[TogetherCompanion] 读取 Provider 列表失败: getter=%s error=%s', getter_name, exc)
                continue
        manager = getattr(self.context, 'provider_manager', None)
        inst_map = getattr(manager, 'inst_map', None)
        if isinstance(inst_map, dict):
            providers.extend(inst_map.values())
        result: dict[str, list[dict[str, str]]] = {'chat': [], 'vision': [], 'stt': [], 'tts': []}
        seen: dict[str, set[str]] = {key: set() for key in result}
        for provider in providers:
            provider_id = self._provider_runtime_id(provider)
            if not provider_id:
                continue
            label = self._provider_label(provider) or provider_id
            kinds = []
            if callable(getattr(provider, 'text_chat', None)):
                kinds.append('chat')
                if self._provider_supports_image(provider):
                    kinds.append('vision')
            if callable(getattr(provider, 'get_text', None)):
                kinds.append('stt')
            if callable(getattr(provider, 'get_audio', None)):
                kinds.append('tts')
            for kind in kinds:
                if provider_id in seen[kind]:
                    continue
                seen[kind].add(provider_id)
                result[kind].append({'id': provider_id, 'label': label})
        for items in result.values():
            items.sort(key=lambda item: (item['label'].lower(), item['id'].lower()))
        return result

    def _companion_realtime_voice_config(self) -> dict[str, Any]:
        # Compatibility rooms use Together's own browser fallback. Native V2
        # rooms carry their language in the authenticated room binding, so no
        # host facade or legacy voice bridge is needed here.
        return {'browser_language': self.browser_language}

    def _bot_identity_cached(self) -> dict[str, Any]:
        cache = getattr(self, '_identity_cache', None)
        now = time.monotonic()
        if isinstance(cache, dict) and now - float(cache.get('at') or 0.0) < 300 and isinstance(cache.get('identity'), dict):
            return cache['identity']
        identity = self._bot_identity()
        if isinstance(cache, dict):
            cache.update(at=now, identity=identity)
        return identity

    def _bot_identity(self) -> dict[str, Any]:
        identity: dict[str, Any] = {}
        connected_qq_ids = self._connected_qq_bot_ids()
        if connected_qq_ids:
            self_ids = [_single_line(item, 80) for item in (identity.get('self_ids') if isinstance(identity.get('self_ids'), list) else []) if _single_line(item, 80)]
            for qq_id in connected_qq_ids:
                if qq_id not in self_ids:
                    self_ids.append(qq_id)
            identity['self_ids'] = self_ids
            if len(connected_qq_ids) == 1:
                identity.update({'selected_id': connected_qq_ids[0], 'qq_id': connected_qq_ids[0], 'ambiguous': False})
            else:
                identity.update({'selected_id': '', 'qq_id': '', 'ambiguous': True})
        configured = _single_line(getattr(self, 'bot_qq_id', ''), 32)
        if re.fullmatch('[1-9]\\d{4,14}', configured):
            self_ids = [_single_line(item, 80) for item in (identity.get('self_ids') if isinstance(identity.get('self_ids'), list) else []) if _single_line(item, 80)]
            if configured not in self_ids:
                self_ids.append(configured)
            identity.update({'self_ids': self_ids, 'selected_id': configured, 'qq_id': configured, 'ambiguous': False})
        return identity
