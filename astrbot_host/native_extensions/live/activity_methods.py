"""V2 live activity services."""
from __future__ import annotations
from .common import *

class LiveActivityMethods:

    def _safe_parse_port(self, port_val) -> Optional[int]:
        """安全解析端口值，防止非数字字符串导致异常"""
        if port_val is None:
            return None
        try:
            return int(port_val)
        except (ValueError, TypeError):
            logger.warning(f'[VTS] 无效的端口配置值: {port_val}，将使用默认端口')
            return None

    def _on_startup_done(self, task: asyncio.Task) -> None:
        if self._startup_task is task:
            self._startup_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error('[VTS] 后台初始化失败: %s', exc, exc_info=True)

    async def _initialize_runtime(self) -> None:
        try:
            await self._persist_config_compatibility_migrations()
            host, port = ((await self._discover()) if self._auto_connect
                          else (self._manual_host or DEFAULT_HOST, self._manual_port or DEFAULT_PORT))
            vts_url = f'ws://{host}:{port}'
            self.vts.url = vts_url
            self._parameter_vts.url = vts_url
            await asyncio.gather(self.vts.reset_connection(), self._parameter_vts.reset_connection())
            if self._auto_connect:
                await self._try_connect()
                await self._refresh_soullink_vts_input_catalog()
            else:
                logger.info('[VTS] auto_connect 关闭，跳过自动连接')
            if self._auto_connect or self.config.get('mouth_sync_enabled', False) or self.config.get('soullink_enabled', False):
                self._vts_parameter_scheduler.start()
            await self._start_soullink_runtime()
            await self._start_subtitle_server_if_enabled()
            if self._is_bili_live_enabled():
                await self._ensure_bili_area_cache()
            if self._is_bili_live_enabled() and self.config.get('bili_live_auto_start', True):
                bili_type = self._get_bili_live_type()
                room_id = self._get_config_room_id()
                if room_id or bili_type in {'laplace', 'open_live'}:
                    await self._start_bili_live(room_id)
                else:
                    logger.warning('[B站直播] 已开启自动启动，但未配置房间号')
            if self._is_twitch_enabled() and self.config.get('twitch_auto_start', True):
                channel = self._get_twitch_channel()
                if channel:
                    await self._start_twitch_live(channel)
                else:
                    logger.warning('[Twitch] 已开启自动启动，但未配置频道名')
        except Exception as e:
            logger.error(f'[VTS] 初始化失败: {e}')
            raise

    async def _cancel_task_set(self, tasks: set[asyncio.Task]) -> None:
        pending = [task for task in list(tasks) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        tasks.clear()

    async def _cancel_task_attr(self, name: str) -> None:
        task = getattr(self, name, None)
        if task and (not task.done()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f'[任务清理] {name} 结束时出现异常: {e}')
        setattr(self, name, None)

    async def _discover(self) -> tuple:
        """确定要连接的 host:port"""
        if self._manual_host and self._manual_port:
            logger.info(f'[VTS] 使用手动配置：{self._manual_host}:{self._manual_port}')
            return (self._manual_host, self._manual_port)
        if not self._auto_discover:
            return (self._manual_host or DEFAULT_HOST, self._manual_port or DEFAULT_PORT)
        logger.info(f'[VTS] 开启自动发现（平台: {platform.system()}）')
        host, port = await auto_discover(host=self._manual_host or DEFAULT_HOST)
        logger.info(f'[VTS] 自动发现结果：{host}:{port}')
        return (host, port)

    async def _try_connect(self):
        """尝试连接并使用已保存的 Token 认证"""
        try:
            if await self._authenticate_saved_token(self.vts):
                self._connected = True
                logger.info('[VTS] 使用已保存 Token 认证成功')
                return
            if self._last_vts_auth_error:
                logger.warning('[VTS] 已保存授权失效并已清理，请发送 /vts_auth 重新授权: %s', self._last_vts_auth_error)
            else:
                logger.info('[VTS] 未找到有效 Token，请发送 /vts_auth 进行认证')
        except VTSConnectionError as e:
            logger.warning(f'[VTS] 连接失败: {e}')
        except VTSTimeoutError as e:
            logger.warning(f'[VTS] 连接超时: {e}')
        except Exception as e:
            logger.warning(f'[VTS] 自动连接失败（VTube Studio 可能未启动）: {e}')

    async def _check_and_reconnect(self) -> bool:
        """检查连接状态，必要时尝试重连"""
        if self.vts.is_authenticated:
            return True
        try:
            if await self._authenticate_saved_token(self.vts):
                self._connected = True
                return True
        except Exception:
            pass
        self._connected = False
        return False

    async def _check_parameter_connection(self) -> bool:
        """Keep the dedicated realtime parameter socket authenticated."""
        if self._parameter_vts.is_authenticated:
            return True
        try:
            return await self._authenticate_saved_token(self._parameter_vts)
        except Exception:
            pass
        return False

    def _safe_parse_int(self, value, default: int=0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _safe_parse_float(self, value, default: float=0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _is_bili_live_enabled(self) -> bool:
        return bool(self.config.get('bilibili_enabled', False))

    def _get_bili_live_type(self) -> str:
        return str(self.config.get('bilibili_type') or self.config.get('bili_live_type') or 'web').strip()

    def _get_bili_sessdata(self) -> str:
        return str(self.config.get('bilibili_sessdata') or self.config.get('bili_live_sessdata') or '').strip()

    def _get_bili_web_backend(self) -> str:
        configured = str(self.config.get('bilibili_web_backend') or 'builtin').strip().lower()
        if configured == 'blivedm':
            if not self._warned_bili_blivedm_fallback:
                self._warned_bili_blivedm_fallback = True
                logger.warning('[B站直播] blivedm 后端在当前环境中可能无法收到事件，已自动切换到 builtin 后端。')
            return 'builtin'
        return configured

    async def _ensure_bili_area_cache(self, force: bool=False) -> bool:
        if self._bili_area_by_id and (not force):
            return True
        if self._bili_area_load_task and (not self._bili_area_load_task.done()):
            try:
                await self._bili_area_load_task
            except Exception:
                return bool(self._bili_area_by_id)
            return bool(self._bili_area_by_id)
        self._bili_area_load_task = asyncio.create_task(self._load_bili_area_cache())
        try:
            await self._bili_area_load_task
        except Exception:
            return bool(self._bili_area_by_id)
        return bool(self._bili_area_by_id)

    async def _load_bili_area_cache(self) -> None:
        try:
            areas = await fetch_bilibili_live_areas()
        except Exception as e:
            logger.warning(f'[B站直播] 直播分区列表加载失败: {e}')
            return
        by_id: dict[int, BilibiliLiveArea] = {}
        by_key: dict[str, BilibiliLiveArea] = {}
        for area in areas:
            by_id[area.area_id] = area
            for key in self._bili_area_lookup_keys(area):
                by_key.setdefault(key, area)
        self._bili_area_by_id = by_id
        self._bili_area_by_key = by_key
        self._bili_area_loaded_at = time.time()
        logger.info(f'[B站直播] 已加载直播分区列表: {len(by_id)} 个子分区')

    def _bili_area_lookup_keys(self, area: BilibiliLiveArea) -> list[str]:
        keys = [str(area.area_id), area.area_name, area.pinyin, f'{area.part_name}/{area.area_name}']
        return [self._normalize_bili_area_query(key) for key in keys if key]

    def _normalize_bili_area_query(self, query: Any) -> str:
        return re.sub('\\s+', '', str(query or '').strip().lower())

    async def _find_bili_area(self, query: Any) -> Optional[BilibiliLiveArea]:
        text = str(query or '').strip()
        if not text:
            return None
        await self._ensure_bili_area_cache()
        area_id = self._safe_parse_int(text, 0)
        if area_id and area_id in self._bili_area_by_id:
            return self._bili_area_by_id[area_id]
        return self._bili_area_by_key.get(self._normalize_bili_area_query(text))

    async def _execute_live_start_ability(self, ctx: dict[str, Any]) -> dict[str, Any]:
        ability_config = ctx.get('config') if isinstance(ctx.get('config'), dict) else {}
        messages: list[str] = []
        platform = self._resolve_proactive_live_platform(ability_config)
        area = await self._resolve_proactive_live_area(ability_config, platform=platform)
        title = self._draft_proactive_live_title(ctx, ability_config, area)
        if self._config_bool(ability_config.get('start_listener'), True):
            if platform == 'twitch':
                messages.append(await self._start_twitch_live())
            else:
                room_id = self._get_config_room_id()
                if self._get_bili_live_type() == 'web' and (not room_id):
                    messages.append('未配置 B站直播房间号，已跳过弹幕监听')
                else:
                    messages.append(await self._start_bili_live(room_id))
        if self._config_bool(ability_config.get('start_obs_stream'), False):
            messages.extend(await self._start_obs_stream_for_proactive(ability_config))
        elif self._config_bool(ability_config.get('start_apps'), True):
            messages.extend(await self._start_live_apps_for_proactive(ability_config))
        platform_text = 'Twitch' if platform == 'twitch' else 'B站'
        area_text = f"频道 {self._get_twitch_channel() or '未配置'}" if platform == 'twitch' else area.display_text() if area else '未指定分区'
        context = f"直播准备：平台 {platform_text}；{area_text}；拟定标题《{title}》。{'；'.join((item for item in messages if item))}"
        return {'ok': True, 'context': context, 'summary': f'准备{platform_text}直播：{title}', 'memory': f'准备了一场{platform_text}直播，目标是 {area_text}，标题草案是《{title}》。', 'status': context}

    async def _execute_live_stop_ability(self, ctx: dict[str, Any]) -> dict[str, Any]:
        ability_config = ctx.get('config') if isinstance(ctx.get('config'), dict) else {}
        messages: list[str] = []
        platform = self._resolve_proactive_live_platform(ability_config, prefer_running=True)
        if self._config_bool(ability_config.get('stop_obs_stream'), False):
            messages.extend(await self._stop_obs_stream_for_proactive())
        if self._config_bool(ability_config.get('stop_listener'), True):
            if platform == 'twitch':
                messages.append(await self._stop_twitch_live())
            else:
                messages.append(await self._stop_bili_live())
        if not messages:
            messages.append('没有启用具体下播动作，只记录了下播意图')
        platform_text = 'Twitch' if platform == 'twitch' else 'B站'
        context = f'直播收束（{platform_text}）：' + '；'.join((item for item in messages if item))
        return {'ok': True, 'context': context, 'summary': '结束直播', 'memory': f'主动收束了一次{platform_text}直播，并把下播余韵整理进直播记忆。', 'status': context}

    def _resolve_proactive_live_platform(self, ability_config: dict[str, Any], prefer_running: bool=False) -> str:
        platform = str(ability_config.get('platform') or 'auto').strip().lower()
        if platform in {'bili', 'bilibili', 'b站'}:
            return 'bili'
        if platform in {'twitch', 'tw'}:
            return 'twitch'
        if self._is_twitch_live_running():
            return 'twitch'
        if self._is_bili_live_running():
            return 'bili'
        if prefer_running:
            return 'bili'
        if self._is_twitch_enabled() and self._get_twitch_channel():
            return 'twitch'
        return 'bili'

    async def _resolve_proactive_live_area(self, ability_config: dict[str, Any], platform: str='bili') -> Optional[BilibiliLiveArea]:
        if platform == 'twitch':
            return None
        query = str(ability_config.get('area_query') or '').strip()
        if not query:
            query = str(self.config.get('area_id') or '').strip()
        area = await self._find_bili_area(query) if query else None
        if area and self._config_bool(ability_config.get('update_area_config'), True):
            await self._persist_plugin_config_updates({'part_id': area.part_id, 'area_id': area.area_id})
        return area

    def _draft_proactive_live_title(self, ctx: dict[str, Any], ability_config: dict[str, Any], area: Optional[BilibiliLiveArea]) -> str:
        plan = ctx.get('current_plan_item') if isinstance(ctx.get('current_plan_item'), dict) else {}
        plan_text = self._single_line_text(plan.get('title') or plan.get('summary') or plan.get('activity') or '', 32)
        reason = self._single_line_text(ctx.get('reason'), 48)
        values = {'area_name': area.area_name if area else '闲聊', 'part_name': area.part_name if area else '直播', 'bot_name': self._single_line_text(ctx.get('bot_name'), 24) or '我', 'display_name': self._single_line_text(ctx.get('display_name'), 24) or '大家', 'reason': reason, 'plan': plan_text}
        template = str(ability_config.get('title_template') or '').strip()
        if template:
            try:
                title = template.format(**values)
            except Exception:
                title = template
        else:
            topic = plan_text or reason or values['area_name']
            title = f"{values['area_name']}陪伴场：{topic}"
        return self._single_line_text(title, 30).strip(' ：:') or '今天也开一会儿'

    async def _start_live_apps_for_proactive(self, ability_config: dict[str, Any]) -> list[str]:
        helper = self._page_api_helper()
        if helper is None:
            return ['拓展页控制 API 不可用，无法启动 OBS/L2DStudio']
        messages: list[str] = []
        for app in ('obs', 'l2dstudio'):
            try:
                messages.append(helper._start_configured_app(app))
            except Exception as e:
                messages.append(self._single_line_text(e, 90))
        wait_seconds = max(0, min(20, self._safe_parse_int(ability_config.get('wait_seconds'), 5)))
        if wait_seconds:
            await asyncio.sleep(wait_seconds)
        return messages

    async def _start_obs_stream_for_proactive(self, ability_config: dict[str, Any]) -> list[str]:
        if not bool(self.config.get('obs_control_enabled', False)):
            return ['OBS 开播控制未启用，已跳过推流']
        if not bool(self.config.get('obs_allow_stream_start', False)):
            return ['直播插件未允许 OBS StartStream，已跳过推流']
        helper = self._page_api_helper()
        if helper is None:
            return ['拓展页控制 API 不可用，无法启动 OBS 推流']
        messages = []
        if self._config_bool(ability_config.get('start_apps'), True):
            messages.extend(await self._start_live_apps_for_proactive(ability_config))
        scene = self._single_line_text(ability_config.get('scene') or self.config.get('obs_live_scene_name'), 120)
        if scene:
            await helper._obs_request('SetCurrentProgramScene', {'sceneName': scene})
            messages.append(f'OBS 已切换到场景：{scene}')
        status = await helper._obs_control_status(check_obs_ws=True)
        if (status.get('obs') or {}).get('streaming'):
            messages.append('OBS 已在推流中')
            return messages
        await helper._obs_request('StartStream')
        messages.append('OBS 推流已开始')
        return messages

    async def _stop_obs_stream_for_proactive(self) -> list[str]:
        if not bool(self.config.get('obs_control_enabled', False)):
            return ['OBS 开播控制未启用，已跳过停止推流']
        helper = self._page_api_helper()
        if helper is None:
            return ['拓展页控制 API 不可用，无法停止 OBS 推流']
        status = await helper._obs_control_status(check_obs_ws=True)
        if not (status.get('obs') or {}).get('streaming'):
            return ['OBS 当前未推流']
        await helper._obs_request('StopStream')
        return ['OBS 推流已停止']

    async def _start_bili_live(self, room_id: int) -> str:
        if not self._is_bili_live_enabled():
            return 'B站直播功能未启用，请先在插件配置中开启 bilibili_enabled。'
        if self._bili_live_task and (not self._bili_live_task.done()):
            return 'B站直播弹幕监听已在运行。'
        bili_type = self._get_bili_live_type()
        if bili_type == 'laplace':
            laplace_cfg = self._get_laplace_config()
            self._bili_live_client = BilibiliLaplaceClient(bridge_url=laplace_cfg['bridge_url'], room_id=room_id, token=laplace_cfg['token'], on_event=self._on_bili_live_event, debug_log=self._bili_debug_mode)
        elif bili_type == 'web':
            sessdata = self._get_bili_sessdata()
            web_backend = self._get_bili_web_backend()
            if web_backend == 'laplace':
                laplace_cfg = self._get_laplace_config()
                self._bili_live_client = BilibiliLaplaceClient(bridge_url=laplace_cfg['bridge_url'], room_id=room_id, token=laplace_cfg['token'], on_event=self._on_bili_live_event, debug_log=self._bili_debug_mode)
            elif web_backend in {'builtin', 'history'}:
                self._bili_live_client = BilibiliLiveClient(room_id=room_id, sessdata=sessdata, on_event=self._on_bili_live_event, debug_log=self._bili_debug_mode, history_poll_interval=self._safe_parse_float(self.config.get('bili_live_history_poll_interval'), 3.0), websocket_enabled=web_backend != 'history')
            else:
                self._bili_live_client = BilibiliBlivedmClient(room_id=room_id, sessdata=sessdata, on_event=self._on_bili_live_event, debug_log=self._bili_debug_mode)
        elif bili_type == 'open_live':
            open_cfg = self._get_bili_open_live_config()
            missing = [key for key, value in open_cfg.items() if not value]
            if missing:
                return 'B站开放平台配置不完整，请填写：' + ', '.join(missing)
            self._bili_live_client = BilibiliOpenLiveClient(access_key_id=open_cfg['access_key_id'], access_key_secret=open_cfg['access_key_secret'], app_id=open_cfg['app_id'], room_owner_auth_code=open_cfg['room_owner_auth_code'], on_event=self._on_bili_live_event)
        else:
            return f'不支持的 B站直播监听类型: {bili_type}'
        self._bili_session_started_at = time.time()
        self._bili_session_id = uuid.uuid4().hex[:24]
        self._bili_events.clear()
        self._bili_session_events.clear()
        self._bili_acknowledged_support_event_ids.clear()
        self._bili_processing_support_event_ids.clear()
        self._bilibili_ai_written_event_ids.clear()
        self._bili_summary_written_for_session = False
        self._private_companion_writeback_seen.clear()
        self._bili_live_task = asyncio.create_task(self._bili_live_client.run_forever())
        self._bili_live_task.add_done_callback(self._on_bili_live_task_done)
        backend_text = f'/{self._get_bili_web_backend()}' if bili_type == 'web' else ''
        logger.info(f'[B站直播] 已启动 {bili_type}{backend_text} 弹幕监听')
        room_text = f'，房间号：{room_id}' if bili_type == 'web' else ''
        return f'已启动 B站直播弹幕监听（{bili_type}{backend_text}）{room_text}'

    async def _stop_bili_live(self) -> str:
        await self._write_private_companion_live_summary()
        if self._bili_live_client:
            await self._bili_live_client.stop()
            self._bili_live_client = None
        if self._bili_live_task:
            if not self._bili_live_task.done():
                self._bili_live_task.cancel()
                try:
                    await self._bili_live_task
                except asyncio.CancelledError:
                    pass
            else:
                try:
                    self._bili_live_task.exception()
                except BaseException:
                    pass
            self._bili_live_task = None
        self._bili_session_started_at = 0.0
        self._bili_session_id = ''
        self._bili_events.clear()
        self._bili_session_events.clear()
        return '已停止 B站直播弹幕监听。'

    def _on_bili_live_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.warning(f'[B站直播] 弹幕监听任务结束: {exc}')
        if self._bili_session_events:
            asyncio.create_task(self._write_private_companion_live_summary())

    async def _on_bili_live_event(self, event: LiveDanmakuEvent) -> None:
        self._bili_events.append(event)
        self._bili_session_events.append(event)
        await self._write_private_companion_live_event(event)
        memory_task = asyncio.create_task(self._record_bilibili_ai_live_event(event))
        self._bilibili_ai_memory_tasks.add(memory_task)
        memory_task.add_done_callback(self._bilibili_ai_memory_tasks.discard)
        if self._should_collect_for_auto_reply(event):
            self._bili_pending_reply_events.append(event)
            self._schedule_bili_auto_reply()
        if self.config.get('bili_live_log_events', True):
            logger.info(f'[B站直播] 捕获事件 room={self._get_current_bili_room_text()} type={event.event_type} {event.display_text()}')
        elif self._debug_mode or self._bili_debug_mode:
            logger.debug(f'[B站直播] {event.event_type}: {event.display_text()}')

    def _is_bili_live_chat_delivery_platform(self, platform_id: Any) -> bool:
        text = str(platform_id or '').strip().lower()
        if not text:
            return False
        blocked_tokens = ('live2d', 'l2d', 'vtube', 'vts', 'obs', 'subtitle')
        if any((token in text for token in blocked_tokens)):
            return False
        return True

    def _bili_live_chat_delivery_platform_priority(self, platform_id: Any) -> int:
        text = str(platform_id or '').strip().lower()
        preferred_tokens = ('aiocqhttp', 'onebot', 'napcat', 'qq')
        if any((token in text for token in preferred_tokens)):
            return 0
        if text in {'default', 'main'}:
            return 1
        return 5

    def _bili_guaranteed_support_types(self) -> set[str]:
        raw = self.config.get('bili_live_auto_reply_guaranteed_event_types',
                              ['super_chat', 'buy_guard'])
        if not isinstance(raw, list):
            raw = ['super_chat', 'buy_guard']
        return {str(item).strip() for item in raw if str(item).strip()}

    def _ensure_bili_support_acknowledgement(self, reply_text: str, events: list[LiveDanmakuEvent]) -> str:
        reply = str(reply_text or '').strip()
        prefixes: list[str] = []
        for event in events:
            if event.event_type not in self._bili_guaranteed_support_types():
                continue
            username = self._single_line_text(event.username, 30)
            if not username or username in {'系统', '观众'}:
                continue
            if self._bili_reply_has_named_thanks(reply, username):
                continue
            amount = f'{event.amount:g}元' if isinstance(event.amount, (int, float)) else ''
            label = 'SC' if event.event_type == 'super_chat' else '支持'
            prefixes.append(f'谢谢{username}的{amount}{label}')
        if not prefixes:
            return reply
        prefix = '，'.join(prefixes)
        return f'{prefix}，{reply}' if reply else prefix + '！'

    async def _send_unacknowledged_bili_support_fallback(self, events: list[LiveDanmakuEvent], *, reason: str='') -> bool:
        selected = [event for event in events if event.event_type in self._bili_guaranteed_support_types() and event.event_id not in self._bili_acknowledged_support_event_ids]
        if not selected:
            return True
        try:
            session_id = await self._get_bili_reply_session()
            if not session_id:
                logger.error('[B站直播] SC 最终兜底失败：未绑定自动回应会话')
                return False
            reply_text = self._build_bili_support_fallback_reply(selected)
            await self.context.send_message(session_id, MessageChain([Plain(reply_text)]))
            try:
                await self._push_subtitle(reply_text, source='bili_live')
            except Exception as e:
                logger.warning(f'[B站直播] SC 兜底字幕推送失败，文字已发送: {e}')
            self._record_bili_auto_reply_sent(selected, reply_text)
            logger.warning('[B站直播] SC/上舰模型回复未产生，已发送不依赖模型的文字兜底%s -> %s: %s', f' ({self._single_line_text(reason, 100)})' if reason else '', session_id, reply_text)
            return True
        except Exception as e:
            logger.error(f'[B站直播] SC 最终兜底发送失败: {e}')
            return False

    def _bili_live_air_guard_enabled(self) -> bool:
        return bool(self.config.get('bili_live_auto_reply_air_guard_enabled', True))

    def _bili_live_air_guard_threshold(self) -> float:
        return max(0.1, self._safe_parse_float(self.config.get('bili_live_auto_reply_air_guard_threshold'), 2.5))

    def _bili_live_air_guard_simple_greeting(self, text: Any) -> bool:
        compact = re.sub('\\s+', '', str(text or '')).strip().lower()
        if not compact:
            return False
        patterns = ('^(hi|hello|hey|哈喽|嗨|你好|主播好|晚上好|早上好|中午好|下午好)$', '^(来了|来啦|我来了|打卡|签到|冒泡|路过)$', '^(贴贴|摸摸|抱抱|亲亲|啵啵|蹭蹭)$', '^(哈哈+|草+|笑死|乐|绷|6+|666+|？+|\\?+|啊？|啥|好家伙)$', '^(嗯+|哦+|噢+|喔+|好|ok|收到|知道了|了解|拜拜|晚安|辛苦了)$')
        return any((re.fullmatch(pattern, compact, flags=re.IGNORECASE) for pattern in patterns))

    def _bili_live_air_guard_has_new_work_signal(self, text: Any) -> bool:
        compact = re.sub('\\s+', '', str(text or '')).strip()
        if not compact:
            return False
        companion = self._get_v2_live_state_owner()
        checker = getattr(companion, '_group_air_guard_has_new_work_signal', None) if companion else None
        if callable(checker):
            try:
                return bool(checker(compact))
            except Exception:
                pass
        markers = ('吗', '？', '?', '怎么', '咋', '为什么', '如何', '能不能', '可以', '帮我', '看看', '查一下', '解释', '总结', '翻译', '写', '改', '生成', '推荐', '谁', '哪里', '什么时候', '不对', '错了', '不是', '你说', '刚才')
        return any((marker in compact for marker in markers))

    def _bili_live_air_guard_local_decision(self, events: list[LiveDanmakuEvent]) -> dict[str, Any]:
        if not events:
            return {'reply': False, 'reason': 'empty', 'score': 0.0, 'borderline': False}
        if self._is_bili_auto_reply_rate_exempt(events):
            return {'reply': True, 'reason': 'priority_event', 'score': 99.0, 'borderline': False}
        score = 0.0
        simple_count = 0
        new_work_count = 0
        named_viewers: set[str] = set()
        reasons: list[str] = []
        for event in events:
            if event.event_type != 'danmaku':
                score += 1.2
                reasons.append(event.event_type)
                continue
            content = str(event.content or '').strip()
            compact = re.sub('\\s+', '', content)
            if event.username:
                named_viewers.add(str(event.username))
            if self._bili_live_air_guard_has_new_work_signal(content):
                score += 2.0
                new_work_count += 1
                reasons.append('new_work')
            if len(compact) >= 12:
                score += 0.8
                reasons.append('detail')
            if re.search('(喜欢|好看|可爱|厉害|牛|笑死|绷不住|难受|开心|破防|离谱|确实|感觉|觉得)', compact):
                score += 0.7
                reasons.append('emotion')
            if re.search('(主播|bot|机器人|你|老婆|姐姐|老师|米|圈米)', compact, flags=re.IGNORECASE):
                score += 0.5
                reasons.append('address')
            if self._bili_live_air_guard_simple_greeting(content):
                simple_count += 1
        if len(named_viewers) >= 3 and len(events) >= 3:
            score += 0.8
            reasons.append('many_viewers')
        idle_seconds = time.time() - float(self._bili_last_auto_reply_at or 0.0)
        if idle_seconds >= 75 and len(events) >= 3:
            score += 0.8
            reasons.append('idle_batch')
        threshold = self._bili_live_air_guard_threshold()
        if new_work_count > 0:
            return {'reply': True, 'reason': 'new_work_signal', 'score': score, 'borderline': False}
        if simple_count >= len(events) and len(events) < 3 and (idle_seconds < 75):
            return {'reply': False, 'reason': 'simple_greeting_or_reaction', 'score': score, 'borderline': False}
        if score >= threshold:
            return {'reply': True, 'reason': ','.join(reasons[:4]) or 'score', 'score': score, 'borderline': False}
        borderline = score >= max(0.8, threshold - 1.0)
        return {'reply': False, 'reason': ','.join(reasons[:4]) or 'low_signal', 'score': score, 'borderline': borderline}

    def _extract_json_object(self, raw: Any) -> dict[str, Any] | None:
        text = str(raw or '').strip()
        if not text:
            return None
        text = re.sub('^```(?:json)?\\s*', '', text, flags=re.IGNORECASE)
        text = re.sub('\\s*```$', '', text)
        candidates = [text]
        match = re.search('\\{.*\\}', text, flags=re.S)
        if match:
            candidates.append(match.group(0))
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except Exception:
                continue
            if isinstance(payload, dict):
                return payload
        return None

    def _build_message_result_from_chain(self, chain: list[Any]) -> Any:
        try:
            from astrbot.api.event import MessageEventResult
        except ImportError:
            from astrbot.core.message.message_event_result import MessageEventResult
        try:
            result = MessageEventResult(chain=chain)
        except TypeError:
            result = MessageEventResult().chain_result(chain)
        if hasattr(result, 'use_t2i'):
            try:
                result = result.use_t2i(False)
            except Exception:
                pass
        elif hasattr(result, 'use_t2i_'):
            try:
                result.use_t2i_ = False
            except Exception:
                pass
        return result

    def _is_bili_live_running(self) -> bool:
        return bool(self._bili_live_task and (not self._bili_live_task.done()))

    def _get_bili_live_task_error(self) -> str:
        if not self._bili_live_task or not self._bili_live_task.done():
            return ''
        try:
            exc = self._bili_live_task.exception()
        except asyncio.CancelledError:
            return '任务已取消'
        except Exception as e:
            return str(e)
        return str(exc) if exc else ''

    def _recent_bili_events(self, limit: Optional[int]=None, include_events: Optional[list[str]]=None) -> list[LiveDanmakuEvent]:
        if limit is None:
            limit = int(self.config.get('bili_live_inject_max_events', 8) or 8)
        limit = max(1, limit)
        allowed = {item.strip() for item in include_events or [] if str(item).strip()}
        events = list(self._bili_events)
        if allowed:
            events = [event for event in events if event.event_type in allowed]
        return events[-limit:]

    def _format_bili_events(self, events: list[LiveDanmakuEvent]) -> str:
        if not events:
            return ''
        now = time.time()
        lines: list[str] = []
        for event in events:
            age = max(0, int(now - event.ts))
            lines.append(f'- [{event.event_type}，{age}秒前] {event.display_text()}')
        return '\n'.join(lines)

    def _live_relationship_context_enabled(self) -> bool:
        return bool(self._live_setting('live_relationship_context_enabled', 'private_companion_live_context_enabled', True))

    # Compatibility alias for older internal callers.  The implementation and
    # all new call sites use the V2-native Live name above.
    _private_companion_enabled = _live_relationship_context_enabled

    def _live_setting(self, key: str, legacy_key: str | None = None, default: Any = None) -> Any:
        """Read a native Live setting, accepting a legacy value during migration."""
        value = self.config.get(key, None)
        if value is not None:
            return value
        if legacy_key:
            value = self.config.get(legacy_key, None)
            if value is not None:
                return value
        return default

    def _bili_live_streamer_manual_display_name(self) -> str:
        return self._clean_bili_live_streamer_name(self.config.get('bili_live_streamer_display_name'))

    def _clean_bili_live_streamer_name(self, value: Any, *, limit: int=24) -> str:
        text = self._single_line_text(value, limit)
        text = text.strip('「」『』“”"\'`[]()（）<>《》:：,，.。!！?？')
        generic = {'', '你', '妳', '您', '我', '他', '她', '它', '主播', '用户', '主要用户', '主用户', '目标用户'}
        if text in generic or text.isdigit():
            return ''
        return text

    def _private_companion_target_ids(self, plugin: Any) -> list[str]:
        getter = getattr(plugin, '_configured_target_ids', None)
        raw: Any = []
        if callable(getter):
            try:
                raw = getter()
            except Exception:
                raw = []
        if not raw:
            raw = getattr(plugin, 'target_user_ids', [])
        if isinstance(raw, str):
            parts = re.split('[,\\s,、;；]+', raw)
        elif isinstance(raw, (list, tuple, set)):
            parts = list(raw)
        else:
            parts = []
        ids: list[str] = []
        canonical = getattr(plugin, '_canonical_private_user_id', None)
        for item in parts:
            user_id = str(item or '').strip()
            if callable(canonical):
                try:
                    user_id = str(canonical(user_id) or user_id).strip()
                except Exception:
                    pass
            if user_id and user_id not in ids:
                ids.append(user_id)
        return ids

    def _bili_live_streamer_reference(self) -> str:
        name = self._clean_bili_live_streamer_name(self._bili_live_streamer_identity().get('name'))
        return name or '主播'

    def _get_v2_live_state_owner(self) -> Any | None:
        store = getattr(self, '_live_state_store', None)
        if store is not None:
            store.refresh_profiles()
        return getattr(store, 'owner', None) if store is not None else None

    def _live_memory_status(self) -> dict[str, Any]:
        enabled = self._live_memory_enabled()
        return {'available': True, 'ready': enabled, 'recall_enabled': enabled and self._live_memory_context_enabled(),
                'top_k': max(1, min(3, self._safe_parse_int(self.config.get('live_memory_max_items'), 3))),
                'recall_tool_enabled': False, 'memorize_tool_enabled': enabled,
                'message': 'V2 直播记忆库已就绪' if enabled else 'V2 直播记忆库已关闭'}

    def _integration_status_payload(self) -> dict[str, Any]:
        companion = self._get_v2_live_state_owner()
        store = self._v2_live_state_store(companion) if companion else {}
        living_memory = self._live_memory_status()
        return {'bili_live': {'enabled': self._is_bili_live_enabled(), 'running': self._is_bili_live_running(), 'room_id': self._bili_live_client.real_room_id if self._bili_live_client and self._bili_live_client.real_room_id else self._get_config_room_id(), 'session_events': len(self._bili_session_events), 'last_error': (getattr(self._bili_live_client, 'last_error', '') if self._bili_live_client else '') or self._get_bili_live_task_error()}, 'auto_reply': {'enabled': bool(self.config.get('bili_live_auto_reply_enabled', False)), 'mode': str(self.config.get('bili_live_auto_reply_mode') or 'native'), 'identity': self._bili_live_reply_identity_label(), 'streamer': self._bili_live_streamer_identity(), 'force_tts': bool(self.config.get('bili_live_auto_reply_force_full_tts', True)), 'sync_tts_subtitle': self._bili_live_auto_reply_sync_tts_subtitle(), 'local_playback': bool(self.config.get('bili_live_tts_local_playback_enabled', True)), 'web_playback': self._bili_live_tts_web_playback_enabled(), 'pending': len(self._bili_pending_reply_events)}, 'subtitle': {'enabled': self._is_subtitle_enabled(), 'scope': self._subtitle_scope(), 'running': self._subtitle_server is not None}, 'live_native': {'available': companion is not None, 'context_enabled': bool(self.config.get('live_relationship_context_enabled', self.config.get('private_companion_live_context_enabled', True))), 'writeback_enabled': self._private_companion_writeback_enabled(), 'viewer_activity_enabled': bool(self.config.get('live_viewer_activity_enabled', self.config.get('private_companion_viewer_activity_enabled', True))), 'viewer_count': len(store.get('viewer_activity') if isinstance(store.get('viewer_activity'), dict) else {}), 'memory_count': len(store.get('memory_items') if isinstance(store.get('memory_items'), list) else {}), 'summary_count': len(store.get('summaries') if isinstance(store.get('summaries'), list) else [])}, 'living_memory': living_memory, 'bilibili_ai_memory': {'enabled': bool(self.config.get('bilibili_ai_memory_integration_enabled', True)), 'available': self._get_bilibili_ai_bot_api() is not None, 'pending_writes': len(self._bilibili_ai_memory_tasks)}}

    def _format_integration_status(self) -> str:
        payload = self._integration_status_payload()
        live = payload['bili_live']
        auto = payload['auto_reply']
        subtitle = payload['subtitle']
        companion = payload['live_native']
        living = payload['living_memory']
        bilibili_ai = payload['bilibili_ai_memory']
        lines = ['直播联动状态：', f"- B站监听：{('运行中' if live['running'] else '已启用未运行' if live['enabled'] else '未启用')}；房间 {live['room_id'] or '未配置'}；本场事件 {live['session_events']}", f"- 自动回应：{('开启' if auto['enabled'] else '关闭')}；模式 {auto['mode']}；身份 {auto.get('identity') or '主播模式'}；主播 {(auto.get('streamer') or {}).get('name') or '主播'}；全量 TTS {('开' if auto['force_tts'] else '关')}；TTS/打字机同步 {('开' if auto['sync_tts_subtitle'] else '关')}；本机播放 {('开' if auto['local_playback'] else '关')}；网页播放 {('开' if auto['web_playback'] else '关')}", f"- 打字机字幕：{('运行中' if subtitle['running'] else '已启用待启动' if subtitle['enabled'] else '未启用')}；范围 {subtitle['scope']}", f"- V2 关系与直播状态：{('已就绪' if companion['available'] else '未就绪')}；关系线索 {('开' if companion['context_enabled'] else '关')}；状态写回 {('开' if companion['writeback_enabled'] else '关')}；观众画像 {companion['viewer_count']}；直播记忆 {companion['memory_count']}", f"- V2 直播记忆库：{('已就绪' if living['ready'] else '已关闭')}；召回 {('开' if living['recall_enabled'] else '关')}；top_k {living.get('top_k', 0)}；主动写入 {('开' if living['memorize_tool_enabled'] else '关')}", f"- BiliBot 记忆：{('已连接' if bilibili_ai['available'] else '等待插件' if bilibili_ai['enabled'] else '已关闭')}；待写入 {bilibili_ai['pending_writes']}"]
        if live.get('last_error'):
            lines.append(f"- 最近监听错误：{self._single_line_text(live['last_error'], 120)}")
        lines.append('建议：直播自动回应用 native；只让直播字幕进 OBS 时保持 subtitle_scope=bili_live。')
        return '\n'.join(lines)

    @staticmethod
    def _private_companion_name_tokens(profile: dict[str, Any]) -> list[str]:
        tokens: list[str] = []
        for key in ('name', 'nickname', 'display_name'):
            value = str(profile.get(key) or '').strip()
            if value:
                tokens.append(value)
        for key in ('aliases', 'observed_names'):
            values = profile.get(key)
            if isinstance(values, list):
                tokens.extend((str(item).strip() for item in values if str(item).strip()))
        return list(dict.fromkeys(tokens))

    def _private_companion_live_external_ids(self, live_username: str, event: LiveDanmakuEvent | None=None) -> set[str]:
        # Nicknames are display references. Only platform-attested account IDs
        # (or Twitch logins when IRC supplies no ID) can bind a relationship.
        raw = getattr(event, 'raw', None)
        raw = raw if isinstance(raw, dict) else {}
        platform = self._live_event_platform(event)
        if platform == 'twitch':
            tags = raw.get('tags') if isinstance(raw.get('tags'), dict) else {}
            uid = str(raw.get('user_id') or tags.get('user-id') or '').strip()
            if uid.isdigit() and int(uid) > 0:
                return {f'twitch:{uid}'}
            login = str(raw.get('login') or '').strip().lower()
            return {f'twitch:login:{login}'} if re.fullmatch('[a-z0-9_]{1,25}', login) else set()
        uid = str(getattr(event, 'user_id', '') or self._bili_event_uid(raw)).strip()
        return {f'bili:{uid}'} if uid.isdigit() and int(uid) > 0 else set()

    @staticmethod
    def _live_event_platform(event: LiveDanmakuEvent | None) -> str:
        raw = getattr(event, 'raw', None)
        return 'twitch' if isinstance(raw, dict) and raw.get('platform') == 'twitch' else 'bili'

    def _live_viewer_key(self, event: LiveDanmakuEvent | None, username: str = '', *, platform: str = 'bili') -> str:
        if event is not None:
            platform = self._live_event_platform(event)
            external_ids = self._private_companion_live_external_ids(event.username, event)
            if external_ids:
                return sorted(external_ids)[0]
            username = event.username
        return f'{platform}:name:{self._single_line_text(username, 60)}'

    def _private_companion_linked_match(self, plugin: Any, user_id: str, profile: dict[str, Any]) -> dict[str, Any]:
        linked_id = str(profile.get('linked_qq_user_id') or profile.get('merged_into_user_id') or '').strip()
        if linked_id:
            linked_profile = self._private_companion_profile_by_user_id(plugin, linked_id)
            if isinstance(linked_profile, dict) and linked_profile.get('enabled', True):
                return self._augment_private_companion_match(plugin, {'user_id': linked_id, 'name': self._single_line_text(linked_profile.get('name'), 60) or linked_id, 'source': 'worldbook_external_bind'})
        return self._augment_private_companion_match(plugin, {'user_id': user_id, 'name': self._single_line_text(profile.get('name'), 60) or user_id, 'source': 'worldbook_external'})

    @staticmethod
    def _bili_event_uid(raw: Any) -> str:
        if not isinstance(raw, dict):
            return ''
        candidates: list[Any] = [raw.get('uid'), raw.get('user_id'), raw.get('mid')]
        for key in ('user', 'user_info', 'info', 'data'):
            value = raw.get(key)
            if isinstance(value, dict):
                candidates.extend([value.get('uid'), value.get('user_id'), value.get('mid')])
            elif isinstance(value, list):
                for item in value[:4]:
                    if isinstance(item, dict):
                        candidates.extend([item.get('uid'), item.get('user_id'), item.get('mid')])
                    elif isinstance(item, (int, str)):
                        candidates.append(item)
        for value in candidates:
            text = str(value or '').strip()
            if text.isdigit() and int(text) > 0:
                return text
        return ''

    def _private_companion_profile_by_user_id(self, plugin: Any, user_id: str) -> dict[str, Any] | None:
        data = getattr(plugin, 'data', None)
        profiles = data.get('worldbook_member_profiles') if isinstance(data, dict) else None
        if not isinstance(profiles, dict):
            return None
        profile = profiles.get(str(user_id or ''))
        return profile if isinstance(profile, dict) else None

    def _augment_private_companion_match(self, plugin: Any, match: dict[str, Any]) -> dict[str, Any]:
        user_id = str(match.get('user_id') or '').strip()
        profile = self._private_companion_profile_by_user_id(plugin, user_id)
        if not isinstance(profile, dict):
            return match
        match.setdefault('name', self._single_line_text(profile.get('name'), 60) or user_id)
        match.setdefault('aliases', [self._single_line_text(item, 40) for item in profile.get('aliases', []) if self._single_line_text(item, 40)][:8])
        match.setdefault('observed_names', [self._single_line_text(item, 40) for item in profile.get('observed_names', []) if self._single_line_text(item, 40)][:8])
        if not match.get('identity_note'):
            match['identity_note'] = self._single_line_text(profile.get('identity_note') or profile.get('note') or profile.get('content'), 120)
        match['boundary_note'] = self._single_line_text(profile.get('boundary_note'), 140)
        match['profile_content'] = self._single_line_text(profile.get('content'), 140)
        memories = profile.get('important_memories')
        if isinstance(memories, list):
            memory_lines: list[str] = []
            for item in memories:
                if (not isinstance(item, dict) or not item.get('enabled', True)
                        or item.get('source') not in {'live_stream_companion', 'live.native'}):
                    continue
                title = self._single_line_text(item.get('title'), 30)
                content = self._single_line_text(item.get('content'), 90)
                if content:
                    memory_lines.append(f"{(title + ': ' if title else '')}{content}")
                if len(memory_lines) >= 2:
                    break
            match['important_memory_lines'] = memory_lines
        return match

    def _recent_private_companion_group_messages(self, plugin: Any, user_id: str) -> list[dict[str, Any]]:
        data = getattr(plugin, 'data', None)
        groups = data.get('groups') if isinstance(data, dict) else None
        if not isinstance(groups, dict):
            return []
        now = time.time()
        max_age = max(30.0, self._safe_parse_float(self._live_setting('live_relationship_context_max_age_seconds', 'private_companion_live_context_max_age_seconds', 900.0), 900.0))
        limit = max(1, self._safe_parse_int(self._live_setting('live_relationship_context_recent_limit', 'private_companion_live_context_recent_limit', 3), 3))
        rows: list[dict[str, Any]] = []
        for group_id, group in groups.items():
            if not isinstance(group, dict):
                continue
            group_name = self._private_companion_group_name(plugin, str(group_id), group)
            recent = group.get('recent_messages')
            if not isinstance(recent, list):
                continue
            for item in recent:
                if not isinstance(item, dict):
                    continue
                if str(item.get('sender_id') or '') != str(user_id):
                    continue
                ts = self._safe_parse_float(item.get('ts'), 0.0)
                if ts <= 0 or now - ts > max_age:
                    continue
                text = self._single_line_text(item.get('text'), 100)
                if not text:
                    continue
                rows.append({'ts': ts, 'age': max(0, int(now - ts)), 'group_id': str(group_id), 'group_name': group_name, 'name': self._single_line_text(item.get('identity_name') or item.get('name'), 40), 'text': text})
        rows.sort(key=lambda item: item['ts'], reverse=True)
        return rows[:limit]

    def _private_companion_group_name(self, plugin: Any, group_id: str, group: dict[str, Any]) -> str:
        data = getattr(plugin, 'data', None)
        profiles = data.get('worldbook_group_profiles') if isinstance(data, dict) else None
        profile = profiles.get(group_id) if isinstance(profiles, dict) else None
        if isinstance(profile, dict):
            name = self._single_line_text(profile.get('name'), 40)
            if name:
                return name
        return self._single_line_text(group.get('name') or group.get('group_name'), 40) or f'群 {group_id}'

    def _private_companion_relationship_style_hint(self, match: dict[str, Any]) -> str:
        if not self._live_setting('live_relationship_style_context_enabled', 'private_companion_relationship_style_context_enabled', True):
            return ''
        name = self._single_line_text(match.get('name'), 30)
        aliases = [self._single_line_text(item, 24) for item in [*list(match.get('aliases') or []), *list(match.get('observed_names') or [])] if self._single_line_text(item, 24)]
        parts: list[str] = []
        if name:
            parts.append(f'可称呼为{name}')
        if aliases:
            parts.append('也认得这些称呼：' + '、'.join(aliases[:4]))
        boundary = self._single_line_text(match.get('boundary_note'), 100)
        if boundary:
            parts.append(f'边界：{boundary}')
        content = self._single_line_text(match.get('profile_content'), 100)
        if content and self._live_setting('live_relationship_style_include_profile', 'private_companion_relationship_style_include_profile', False):
            parts.append(f'画像：{content}')
        memories = match.get('important_memory_lines')
        if isinstance(memories, list) and memories and self._live_setting('live_relationship_style_include_memories', 'private_companion_relationship_style_include_memories', False):
            parts.append('相关记忆：' + '；'.join((self._single_line_text(item, 80) for item in memories[:2])))
        return '；'.join(parts)

    def _live_memory_enabled(self) -> bool:
        return bool(self.config.get('live_memory_enabled', True))

    def _live_memory_highlight_event_types(self) -> set[str]:
        raw = self.config.get('live_memory_highlight_event_types', ['gift', 'super_chat', 'buy_guard'])
        if not isinstance(raw, list):
            raw = ['gift', 'super_chat', 'buy_guard']
        return {str(item).strip() for item in raw if str(item).strip()}

    def _build_bili_live_memory_recall_query(self, events: list[LiveDanmakuEvent]) -> str:
        parts: list[str] = ['B站直播', '直播弹幕', '直播间观众']
        for event in events[-5:]:
            username = self._single_line_text(event.username, 30)
            content = self._single_line_text(event.content, 80)
            if username and username not in {'系统', '观众'}:
                parts.append(username)
            if content:
                parts.append(content)
            if event.event_type in {'gift', 'super_chat', 'buy_guard'}:
                parts.append(event.event_type)
        deduped: list[str] = []
        for item in parts:
            if item and item not in deduped:
                deduped.append(item)
        return ' '.join(deduped[:18]).strip()

    def _live_memory_session_line(self, *, platform: str = 'bili') -> str:
        events = list(self._twitch_events if platform == 'twitch' else self._bili_session_events)
        if not events:
            return ''
        counts: dict[str, int] = {}
        viewers: dict[str, int] = {}
        for event in events:
            counts[event.event_type] = counts.get(event.event_type, 0) + 1
            if event.username and event.username != '系统':
                viewers[event.username] = viewers.get(event.username, 0) + 1
        top_viewers = sorted(viewers.items(), key=lambda item: item[1], reverse=True)[:3]
        viewer_text = '、'.join((f'{name}({count})' for name, count in top_viewers))
        count_text = '、'.join((f'{key}{value}' for key, value in counts.items()))
        started_at = self._twitch_session_started_at if platform == 'twitch' else self._bili_session_started_at
        duration = max(1, int((time.time() - (started_at or events[0].ts)) / 60))
        parts = [f'已直播约 {duration} 分钟', f'本场互动 {len(events)} 条']
        if count_text:
            parts.append(count_text)
        if viewer_text:
            parts.append('活跃观众：' + viewer_text)
        return '；'.join(parts)

    def _live_memory_recent_items(self, store: dict[str, Any], limit: int) -> list[str]:
        items = store.get('memory_items')
        if not isinstance(items, list):
            return []
        lines: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = self._single_line_text(item.get('text'), 120)
            if not text:
                continue
            username = self._single_line_text(item.get('username'), 30)
            if username:
                text = f'{username}：{text}'
            lines.append(text)
            if len(lines) >= limit:
                break
        return lines

    def _live_memory_recent_highlights(self, store: dict[str, Any], limit: int) -> list[str]:
        items = store.get('highlight_events')
        if not isinstance(items, list):
            return []
        lines: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = self._single_line_text(item.get('text'), 120)
            if not text:
                continue
            lines.append(text)
            if len(lines) >= limit:
                break
        return lines

    def _live_memory_topic_lines(self, store: dict[str, Any], limit: int) -> list[str]:
        topics = store.get('topic_memory')
        if not isinstance(topics, dict):
            return []
        rows = []
        for topic, item in topics.items():
            if not isinstance(item, dict):
                continue
            rows.append((self._safe_parse_int(item.get('count'), 0), self._safe_parse_float(item.get('last_seen'), 0.0), str(topic), item))
        rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
        lines: list[str] = []
        for count, _ts, topic, item in rows[:limit]:
            samples = item.get('samples') if isinstance(item.get('samples'), list) else []
            sample_text = ''
            for sample in samples[:1]:
                if isinstance(sample, dict):
                    sample_text = self._single_line_text(sample.get('text'), 42)
                else:
                    sample_text = self._single_line_text(sample, 42)
                if sample_text:
                    break
            detail = f'{topic}({count}次)'
            if sample_text:
                detail += f' 最近：{sample_text}'
            lines.append(detail)
        return lines

    def _live_memory_open_thread_lines(self, store: dict[str, Any], limit: int) -> list[str]:
        items = store.get('open_threads')
        if not isinstance(items, list):
            return []
        lines: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = self._single_line_text(item.get('text'), 90)
            username = self._single_line_text(item.get('username'), 30)
            if not text:
                continue
            lines.append(f"{(username + '：' if username else '')}{text}")
            if len(lines) >= limit:
                break
        return lines

    def _live_memory_viewer_lines(self, plugin: Any, events: list[LiveDanmakuEvent], limit: int) -> list[str]:
        lines: list[str] = []
        seen: set[str] = set()
        for live_event in reversed(events):
            username = self._single_line_text(live_event.username, 40)
            viewer_key = self._live_viewer_key(live_event)
            if not username or viewer_key in seen or username in {'系统'}:
                continue
            seen.add(viewer_key)
            activity = self._private_companion_viewer_activity_for_context(plugin, username, event=live_event)
            if activity:
                lines.append(f'{username}：{activity}')
            if len(lines) >= limit:
                break
        return lines

    def _format_live_memory_overview(self, plugin: Any, limit: int=8) -> str:
        store = self._v2_live_state_store(plugin)
        if not store:
            return ''
        limit = max(1, min(30, limit))
        sections: list[str] = []
        session_line = self._live_memory_session_line()
        if session_line:
            sections.append('本场直播：' + session_line)
        items = self._live_memory_recent_items(store, limit=limit)
        if items:
            sections.append('可承接记忆：\n' + '\n'.join((f'- {item}' for item in items)))
        highlights = self._live_memory_recent_highlights(store, limit=min(5, limit))
        if highlights:
            sections.append('最近高光：\n' + '\n'.join((f'- {item}' for item in highlights)))
        topics = self._live_memory_topic_lines(store, limit=min(8, limit))
        if topics:
            sections.append('常见话题：\n' + '\n'.join((f'- {item}' for item in topics)))
        threads = self._live_memory_open_thread_lines(store, limit=min(5, limit))
        if threads:
            sections.append('未完话题：\n' + '\n'.join((f'- {item}' for item in threads)))
        summaries = store.get('summaries') if isinstance(store.get('summaries'), list) else []
        summary_lines = []
        for item in reversed(summaries[-min(3, limit):]):
            if isinstance(item, dict):
                text = self._single_line_text(item.get('summary') or item.get('body'), 140)
                if text:
                    summary_lines.append(text)
        if summary_lines:
            sections.append('最近直播小结：\n' + '\n'.join((f'- {item}' for item in summary_lines)))
        return '\n\n'.join(sections)

    def _private_companion_writeback_enabled(self) -> bool:
        return bool(self._live_setting('live_state_writeback_enabled', 'private_companion_writeback_enabled', True))

    def _private_companion_writeback_event_types(self) -> set[str]:
        raw = self._live_setting('live_writeback_event_types', 'private_companion_writeback_memory_event_types', ['gift', 'super_chat', 'buy_guard'])
        if not isinstance(raw, list):
            raw = ['gift', 'super_chat', 'buy_guard']
        return {str(item).strip() for item in raw if str(item).strip()}

    def _private_companion_event_key(self, event: LiveDanmakuEvent) -> str:
        bucket = int((event.ts or time.time()) // 10)
        identity = event.event_id or f'{event.event_type}|{event.username}|{event.content}|{bucket}'
        return f'{self._live_event_platform(event)}|{identity}'

    def _v2_live_state_store(self, plugin: Any = None, *, platform: str = 'bili') -> dict[str, Any]:
        owner = getattr(getattr(self, '_live_state_store', None), 'owner', None)
        data = getattr(owner, 'data', None)
        if not isinstance(data, dict):
            return {}
        store = data.setdefault('live_stream_companion', {})
        if not isinstance(store, dict):
            store = {}
            data['live_stream_companion'] = store
        if platform == 'twitch':
            platforms = store.setdefault('platforms', {})
            if not isinstance(platforms, dict):
                platforms = {}
                store['platforms'] = platforms
            store = platforms.setdefault('twitch', {})
            if not isinstance(store, dict):
                store = {}
                platforms['twitch'] = store
        store.setdefault('viewer_observations', {})
        store.setdefault('viewer_activity', {})
        store.setdefault('summaries', [])
        store.setdefault('memory_items', [])
        store.setdefault('topic_memory', {})
        store.setdefault('highlight_events', [])
        store.setdefault('open_threads', [])
        store.setdefault('stream_profile', {})
        return store

    async def _write_private_companion_live_event(self, event: LiveDanmakuEvent) -> None:
        if not (self._private_companion_writeback_enabled() or self._live_memory_enabled()):
            return
        plugin = self._get_v2_live_state_owner()
        if plugin is None:
            return
        event_key = self._private_companion_event_key(event)
        if event_key in self._private_companion_writeback_seen:
            return
        self._private_companion_writeback_seen.add(event_key)
        try:
            lock = getattr(plugin, '_data_lock', None)
            if lock is not None:
                async with lock:
                    changed = self._write_private_companion_live_event_locked(plugin, event, event_key)
                    if changed:
                        self._save_private_companion(plugin)
            else:
                changed = self._write_private_companion_live_event_locked(plugin, event, event_key)
                if changed:
                    self._save_private_companion(plugin)
        except Exception as e:
            self._private_companion_writeback_seen.discard(event_key)
            logger.debug(f'[Live] 写入 V2 直播状态事件失败: {e}')

    def _write_private_companion_live_event_locked(self, plugin: Any, event: LiveDanmakuEvent, event_key: str) -> bool:
        changed = False
        match = self._match_private_companion_member(plugin, event.username, event)
        writeback_enabled = self._private_companion_writeback_enabled()
        if self._live_setting('live_viewer_activity_enabled', 'private_companion_viewer_activity_enabled', True):
            changed = self._record_private_companion_viewer_activity(plugin, event, match) or changed
        if self._live_memory_enabled():
            changed = self._record_private_companion_live_memory(plugin, event, match) or changed
        if not writeback_enabled:
            return changed
        if match:
            changed = self._write_private_companion_viewer_memory(plugin, match, event, event_key) or changed
        elif self._live_setting('live_auto_register_viewers', 'private_companion_auto_register_viewers', True):
            changed = self._maybe_register_private_companion_live_viewer(plugin, event) or changed
        if self._live_setting('live_afterglow_enabled', 'private_companion_live_state_enabled', True):
            changed = self._maybe_apply_private_companion_live_state(plugin, event) or changed
        return changed

    def _record_private_companion_viewer_activity(self, plugin: Any, event: LiveDanmakuEvent, match: dict[str, Any] | None=None) -> bool:
        if event.event_type not in {'danmaku', 'gift', 'super_chat', 'buy_guard', 'enter_room', 'follow', 'like'}:
            return False
        username = self._single_line_text(event.username, 40)
        if not username or username in {'系统'}:
            return False
        platform = self._live_event_platform(event)
        store = self._v2_live_state_store(plugin, platform=platform)
        activity_map = store.setdefault('viewer_activity', {})
        if not isinstance(activity_map, dict):
            activity_map = {}
            store['viewer_activity'] = activity_map
        user_id = str((match or {}).get('user_id') or '').strip()
        display_name = self._single_line_text((match or {}).get('name'), 40) or username
        primary_key = self._live_viewer_key(event)
        item = activity_map.setdefault(primary_key, {'viewer_key': primary_key, 'live_username': username, 'user_id': user_id, 'display_name': display_name, 'first_seen': time.time(), 'last_seen': 0, 'total_events': 0, 'event_counts': {}, 'recent_events': [], 'recent_danmaku': []})
        if not isinstance(item, dict):
            item = {'viewer_key': primary_key, 'recent_events': [], 'recent_danmaku': []}
            activity_map[primary_key] = item
        item['viewer_key'] = primary_key
        item['platform'] = platform
        item['live_username'] = username
        item['user_id'] = user_id
        item['display_name'] = display_name
        item.setdefault('first_seen', time.time())
        item['last_seen'] = time.time()
        item['total_events'] = self._safe_parse_int(item.get('total_events'), 0) + 1
        event_counts = item.setdefault('event_counts', {})
        if not isinstance(event_counts, dict):
            event_counts = {}
            item['event_counts'] = event_counts
        event_counts[event.event_type] = self._safe_parse_int(event_counts.get(event.event_type), 0) + 1
        recent_events = item.setdefault('recent_events', [])
        if not isinstance(recent_events, list):
            recent_events = []
            item['recent_events'] = recent_events
        recent_events.insert(0, {'type': event.event_type, 'content': self._single_line_text(event.content, 120), 'ts': event.ts})
        del recent_events[12:]
        if event.event_type == 'danmaku' and event.content:
            recent_danmaku = item.setdefault('recent_danmaku', [])
            if not isinstance(recent_danmaku, list):
                recent_danmaku = []
                item['recent_danmaku'] = recent_danmaku
            text = self._single_line_text(event.content, 80)
            if text:
                recent_danmaku.insert(0, {'content': text, 'ts': event.ts})
                seen: set[str] = set()
                deduped: list[dict[str, Any]] = []
                for row in recent_danmaku:
                    if not isinstance(row, dict):
                        continue
                    content = self._single_line_text(row.get('content'), 80)
                    if not content or content in seen:
                        continue
                    seen.add(content)
                    deduped.append({'content': content, 'ts': row.get('ts') or time.time()})
                    if len(deduped) >= 8:
                        break
                item['recent_danmaku'] = deduped
        if user_id:
            aliases = item.setdefault('live_usernames', [])
            if isinstance(aliases, list) and username not in aliases:
                aliases.insert(0, username)
                del aliases[6:]
        logger.debug('[B站直播] 已更新观众活跃画像: %s type=%s total=%s', primary_key, event.event_type, item.get('total_events'))
        return True

    def _merge_private_companion_viewer_activity(self, target: dict[str, Any], source: dict[str, Any]) -> None:
        target['total_events'] = self._safe_parse_int(target.get('total_events'), 0) + self._safe_parse_int(source.get('total_events'), 0)
        target['first_seen'] = min(self._safe_parse_float(target.get('first_seen'), time.time()), self._safe_parse_float(source.get('first_seen'), time.time()))
        target['last_seen'] = max(self._safe_parse_float(target.get('last_seen'), 0), self._safe_parse_float(source.get('last_seen'), 0))
        target_counts = target.setdefault('event_counts', {})
        source_counts = source.get('event_counts') if isinstance(source.get('event_counts'), dict) else {}
        if isinstance(target_counts, dict):
            for key, value in source_counts.items():
                target_counts[key] = self._safe_parse_int(target_counts.get(key), 0) + self._safe_parse_int(value, 0)
        for field, limit in (('recent_events', 12), ('recent_danmaku', 8)):
            merged = []
            for row in [*(target.get(field) if isinstance(target.get(field), list) else []), *(source.get(field) if isinstance(source.get(field), list) else [])]:
                if isinstance(row, dict):
                    merged.append(row)
            merged.sort(key=lambda row: self._safe_parse_float(row.get('ts'), 0), reverse=True)
            target[field] = merged[:limit]

    def _record_private_companion_live_memory(self, plugin: Any, event: LiveDanmakuEvent, match: dict[str, Any] | None=None) -> bool:
        if event.event_type not in {'danmaku', 'gift', 'super_chat', 'buy_guard', 'enter_room', 'follow', 'like'}:
            return False
        username = self._single_line_text(event.username, 40)
        if not username or username in {'系统'}:
            return False
        platform = self._live_event_platform(event)
        store = self._v2_live_state_store(plugin, platform=platform)
        if not store:
            return False
        now = time.time()
        changed = False
        profile = store.setdefault('stream_profile', {})
        if not isinstance(profile, dict):
            profile = {}
            store['stream_profile'] = profile
        profile['last_event_at'] = now
        profile['total_events'] = self._safe_parse_int(profile.get('total_events'), 0) + 1
        started_at = self._twitch_session_started_at if platform == 'twitch' else self._bili_session_started_at
        if started_at:
            profile['current_session_started_at'] = started_at
        counts = profile.setdefault('event_counts', {})
        if not isinstance(counts, dict):
            counts = {}
            profile['event_counts'] = counts
        counts[event.event_type] = self._safe_parse_int(counts.get(event.event_type), 0) + 1
        changed = True
        if event.event_type == 'danmaku' and event.content:
            changed = self._update_live_memory_topics(store, event, username) or changed
            changed = self._maybe_add_live_memory_item(store, event, match) or changed
            changed = self._maybe_add_live_memory_open_thread(store, event, username) or changed
        if event.event_type in self._live_memory_highlight_event_types():
            changed = self._add_live_memory_highlight(store, event, match) or changed
            changed = self._maybe_add_live_memory_item(store, event, match, force=True) or changed
        return changed

    def _update_live_memory_topics(self, store: dict[str, Any], event: LiveDanmakuEvent, username: str) -> bool:
        if not self.config.get('live_memory_topic_enabled', True):
            return False
        content = self._single_line_text(event.content, 120)
        if not content:
            return False
        topics = store.setdefault('topic_memory', {})
        if not isinstance(topics, dict):
            topics = {}
            store['topic_memory'] = topics
        candidates = self._extract_live_memory_topics(content)
        if not candidates:
            return False
        now = event.ts or time.time()
        for topic in candidates[:5]:
            item = topics.setdefault(topic, {'topic': topic, 'count': 0, 'last_seen': 0, 'samples': [], 'viewers': []})
            if not isinstance(item, dict):
                item = {'topic': topic, 'count': 0, 'samples': [], 'viewers': []}
                topics[topic] = item
            item['count'] = self._safe_parse_int(item.get('count'), 0) + 1
            item['last_seen'] = now
            viewers = item.setdefault('viewers', [])
            if isinstance(viewers, list) and username not in viewers:
                viewers.insert(0, username)
                del viewers[6:]
            samples = item.setdefault('samples', [])
            if not isinstance(samples, list):
                samples = []
                item['samples'] = samples
            if not any((isinstance(row, dict) and row.get('text') == content for row in samples)):
                samples.insert(0, {'username': username, 'text': content, 'ts': now})
                del samples[5:]
        self._trim_live_memory_topics(topics)
        return True

    def _extract_live_memory_topics(self, content: str) -> list[str]:
        text = re.sub('https?://\\S+', '', content)
        raw = re.findall('#[A-Za-z0-9_\\u4e00-\\u9fff]{2,24}|[A-Za-z][A-Za-z0-9_]{2,20}|[\\u4e00-\\u9fff]{2,8}', text)
        stopwords = {'哈哈', '哈哈哈', '什么', '这个', '那个', '一下', '可以', '不是', '就是', '感觉', '真的', '今天', '直播', '主播'}
        topics: list[str] = []
        for item in raw:
            topic = item.strip('#').strip()
            if not topic or topic in stopwords:
                continue
            if len(topic) < 2:
                continue
            if topic not in topics:
                topics.append(topic)
        return topics[:8]

    def _trim_live_memory_topics(self, topics: dict[str, Any]) -> None:
        max_topics = max(20, self._safe_parse_int(self.config.get('live_memory_max_topics'), 80))
        if len(topics) <= max_topics:
            return
        rows = []
        for topic, item in topics.items():
            last_seen = 0.0
            count = 0
            if isinstance(item, dict):
                last_seen = self._safe_parse_float(item.get('last_seen'), 0.0)
                count = self._safe_parse_int(item.get('count'), 0)
            rows.append((count, last_seen, topic))
        rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
        keep = {topic for _count, _last_seen, topic in rows[:max_topics]}
        for topic in list(topics.keys()):
            if topic not in keep:
                topics.pop(topic, None)

    def _maybe_add_live_memory_item(self, store: dict[str, Any], event: LiveDanmakuEvent, match: dict[str, Any] | None=None, *, force: bool=False) -> bool:
        content = self._single_line_text(event.content, 140)
        if not content:
            return False
        username = self._single_line_text(event.username, 40)
        if event.event_type == 'danmaku' and (not force):
            pattern = '喜欢|不喜欢|想看|想听|希望|下次|以后|记得|别忘|能不能|可不可以|什么时候|刚才|刚刚|上次|以后还'
            if not re.search(pattern, content):
                return False
        display_name = self._single_line_text((match or {}).get('name'), 40) or username
        text = content
        if event.event_type != 'danmaku':
            text = event.display_text()
        item = {'id': f"live-memory-{uuid.uuid5(uuid.NAMESPACE_URL, self._live_viewer_key(event) + '|' + text).hex[:16]}", 'type': event.event_type, 'platform': self._live_event_platform(event), 'viewer_key': self._live_viewer_key(event), 'username': username, 'display_name': display_name, 'user_id': str((match or {}).get('user_id') or ''), 'text': self._single_line_text(text, 160), 'ts': event.ts or time.time(), 'source': 'live_stream_companion'}
        items = store.setdefault('memory_items', [])
        if not isinstance(items, list):
            items = []
            store['memory_items'] = items
        if any((isinstance(row, dict) and row.get('id') == item['id'] for row in items)):
            return False
        items.insert(0, item)
        max_items = max(20, self._safe_parse_int(self.config.get('live_memory_max_items'), 80))
        del items[max_items:]
        return True

    def _maybe_add_live_memory_open_thread(self, store: dict[str, Any], event: LiveDanmakuEvent, username: str) -> bool:
        content = self._single_line_text(event.content, 120)
        if not content:
            return False
        pattern = '[?？]|下次|以后|待会|等会|一会|继续|记得|别忘|能不能|可不可以|什么时候|怎么'
        if not re.search(pattern, content):
            return False
        items = store.setdefault('open_threads', [])
        if not isinstance(items, list):
            items = []
            store['open_threads'] = items
        thread_id = uuid.uuid5(uuid.NAMESPACE_URL, self._live_viewer_key(event) + '|' + content).hex[:16]
        if any((isinstance(row, dict) and row.get('id') == thread_id for row in items)):
            return False
        items.insert(0, {'id': thread_id, 'username': username, 'platform': self._live_event_platform(event), 'viewer_key': self._live_viewer_key(event), 'text': content, 'ts': event.ts or time.time(), 'source': 'live_stream_companion'})
        max_threads = max(6, self._safe_parse_int(self.config.get('live_memory_max_open_threads'), 20))
        del items[max_threads:]
        return True

    def _add_live_memory_highlight(self, store: dict[str, Any], event: LiveDanmakuEvent, match: dict[str, Any] | None=None) -> bool:
        text = self._single_line_text(event.display_text(), 180)
        if not text:
            return False
        items = store.setdefault('highlight_events', [])
        if not isinstance(items, list):
            items = []
            store['highlight_events'] = items
        highlight_id = uuid.uuid5(uuid.NAMESPACE_URL, event.event_type + '|' + self._live_viewer_key(event) + '|' + text).hex[:16]
        if any((isinstance(row, dict) and row.get('id') == highlight_id for row in items)):
            return False
        items.insert(0, {'id': highlight_id, 'type': event.event_type, 'platform': self._live_event_platform(event), 'viewer_key': self._live_viewer_key(event), 'username': self._single_line_text(event.username, 40), 'display_name': self._single_line_text((match or {}).get('name'), 40), 'text': text, 'ts': event.ts or time.time(), 'source': 'live_stream_companion'})
        max_highlights = max(10, self._safe_parse_int(self.config.get('live_memory_max_highlights'), 40))
        del items[max_highlights:]
        return True

    def _write_private_companion_viewer_memory(self, plugin: Any, match: dict[str, Any], event: LiveDanmakuEvent, event_key: str) -> bool:
        if event.event_type not in self._private_companion_writeback_event_types():
            return False
        data = getattr(plugin, 'data', None)
        profiles = data.get('worldbook_member_profiles') if isinstance(data, dict) else None
        if not isinstance(profiles, dict):
            return False
        user_id = str(match.get('user_id') or '').strip()
        profile = profiles.get(user_id)
        if not isinstance(profile, dict):
            return False
        memories = profile.setdefault('important_memories', [])
        if not isinstance(memories, list):
            memories = []
            profile['important_memories'] = memories
        source_id = f'live_stream_companion:{event_key}'
        if any((isinstance(item, dict) and item.get('source_id') == source_id for item in memories)):
            return False
        title = {'gift': '直播间送礼', 'super_chat': '直播间醒目留言', 'buy_guard': '直播间上舰'}.get(event.event_type, '直播间互动')
        platform_label = 'Twitch' if self._live_event_platform(event) == 'twitch' else 'B站'
        content = f"直播用户名 {event.username} 在 {platform_label}直播间{(event.content if event.event_type != 'danmaku' else '发弹幕：' + event.content)}"
        memories.insert(0, {'title': title, 'content': self._single_line_text(content, 220), 'weight': 75 if event.event_type in {'super_chat', 'buy_guard'} else 62, 'privacy': 'internal', 'source': 'live_stream_companion', 'source_id': source_id, 'enabled': True, 'updated_at': time.time()})
        profile['important_memories'] = [item for item in memories if isinstance(item, dict)][:8]
        profile['manual_edit_ts'] = time.time()
        logger.info('[B站直播] 已写入陪伴关系记忆: user=%s event=%s', user_id, event.event_type)
        return True

    def _maybe_register_private_companion_live_viewer(self, plugin: Any, event: LiveDanmakuEvent) -> bool:
        if event.event_type not in {'danmaku', 'gift', 'super_chat', 'buy_guard'}:
            return False
        username = self._single_line_text(event.username, 40)
        if not username or username in {'观众', '系统'}:
            return False
        platform = self._live_event_platform(event)
        platform_label = 'Twitch' if platform == 'twitch' else 'B站'
        store = self._v2_live_state_store(plugin, platform=platform)
        observations = store.setdefault('viewer_observations', {})
        if not isinstance(observations, dict):
            observations = {}
            store['viewer_observations'] = observations
        viewer_key = self._live_viewer_key(event)
        item = observations.setdefault(viewer_key, {'username': username, 'count': 0, 'first_seen': time.time(), 'recent_events': [], 'profile_id': ''})
        if not isinstance(item, dict):
            item = {'username': username, 'count': 0, 'recent_events': []}
            observations[viewer_key] = item
        item['count'] = self._safe_parse_int(item.get('count'), 0) + 1
        item['last_seen'] = time.time()
        recent = item.setdefault('recent_events', [])
        if not isinstance(recent, list):
            recent = []
            item['recent_events'] = recent
        recent.insert(0, {'type': event.event_type, 'content': self._single_line_text(event.content, 120), 'ts': event.ts})
        del recent[8:]
        min_events = max(1, self._safe_parse_int(self._live_setting('live_auto_register_min_events', 'private_companion_auto_register_min_events', 2), 2))
        if item.get('profile_id') or item['count'] < min_events:
            return True
        external_ids = self._private_companion_live_external_ids(username, event)
        profile_id = viewer_key
        data = getattr(plugin, 'data', None)
        profiles = data.setdefault('worldbook_member_profiles', {}) if isinstance(data, dict) else {}
        if not isinstance(profiles, dict):
            return True
        if profile_id not in profiles:
            profiles[profile_id] = {'user_id': profile_id, 'identity_type': 'external', 'platform': platform, 'name': username, 'aliases': [], 'observed_names': [username], 'external_ids': sorted(external_ids), 'content': f'{platform_label}直播间观众，直播用户名 {username}。身份尚未与 QQ 号确认。', 'identity_note': f'{platform_label}直播间观众，直播用户名 {username}；可能需要后续人工合并到真实关系节点。', 'boundary_note': '直播身份为候选登记，不要在公开场景提及内部匹配或关系网。', 'important_memories': [], 'pending_observations': [{'id': f'live-{int(time.time())}', 'title': '直播观众自动登记', 'content': self._single_line_text(f"{username} 在直播间出现 {item['count']} 次，最近互动：{event.content}", 240), 'evidence': self._single_line_text(event.display_text(), 240), 'weight': 35, 'source': 'live_stream_companion', 'created_at': time.time()}], 'enabled': True, 'priority': 80, 'auto_registration_pending': True, 'source': 'live_stream_companion', 'manual_edit_ts': time.time()}
            logger.info('[B站直播] 已自动登记直播观众候选关系: %s', username)
        else:
            profile = profiles.get(profile_id)
            if isinstance(profile, dict):
                profile['identity_type'] = 'external'
                ids = profile.setdefault('external_ids', [])
                if not isinstance(ids, list):
                    ids = []
                    profile['external_ids'] = ids
                for ext in sorted(external_ids):
                    if ext not in ids:
                        ids.append(ext)
                if username not in (profile.get('observed_names') if isinstance(profile.get('observed_names'), list) else []):
                    observed = profile.setdefault('observed_names', [])
                    if isinstance(observed, list):
                        observed.insert(0, username)
                        del observed[8:]
        item['profile_id'] = profile_id
        return True

    def _maybe_apply_private_companion_live_state(self, plugin: Any, event: LiveDanmakuEvent) -> bool:
        cooldown = max(30.0, self._safe_parse_float(self._live_setting('live_afterglow_cooldown_seconds', 'private_companion_live_state_cooldown_seconds', 300.0), 300.0))
        now = time.time()
        if now - self._private_companion_last_state_at < cooldown:
            return False
        platform = self._live_event_platform(event)
        session_events = list(self._twitch_events if platform == 'twitch' else self._bili_session_events)
        recent_events = [item for item in session_events if now - item.ts <= 300]
        significant = [item for item in recent_events if item.event_type in {'gift', 'super_chat', 'buy_guard'}]
        if event.event_type not in {'gift', 'super_chat', 'buy_guard'} and len(recent_events) < 5:
            return False
        label = '直播间互动很热闹，状态被观众带得更活泼'
        mood = '活泼'
        energy_delta = 6
        intensity = 66
        if significant:
            label = '直播间收到礼物或醒目留言，状态被明显点亮，进入更活泼的营业节奏'
            mood = '兴奋'
            energy_delta = 8
            intensity = 76
        make_condition = getattr(plugin, '_make_condition', None)
        compose = getattr(plugin, '_compose_state_from_conditions', None)
        if not callable(make_condition) or not callable(compose):
            return False
        data = getattr(plugin, 'data', None)
        if not isinstance(data, dict):
            return False
        conditions = data.setdefault('state_conditions', [])
        if not isinstance(conditions, list):
            conditions = []
            data['state_conditions'] = conditions
        conditions.append(make_condition(kind='live_stream', title='直播间互动', label=label, mood=mood, energy_delta=energy_delta, duration_hours=max(1, self._safe_parse_int(self._live_setting('live_afterglow_duration_hours', 'private_companion_live_state_duration_hours', 2), 2)), intensity=intensity, cause=f'{platform}直播间最近 {len(recent_events)} 条互动', phase='live_afterglow', episode_key=self._private_companion_today_key('live-stream')))
        weather = data.get('daily_weather') if isinstance(data.get('daily_weather'), dict) else {}
        data['daily_state'] = compose(weather)
        self._private_companion_last_state_at = now
        return True

    async def _write_private_companion_live_summary(self, *, platform: str = 'bili') -> None:
        if not (self._private_companion_writeback_enabled() or self._live_memory_enabled()):
            return
        if not self._live_setting('live_summary_enabled', 'private_companion_live_summary_enabled', True):
            return
        session_events = self._twitch_events if platform == 'twitch' else self._bili_session_events
        events = list(session_events)
        if not events:
            return
        flag_name = '_twitch_summary_written_for_session' if platform == 'twitch' else '_bili_summary_written_for_session'
        if getattr(self, flag_name, False):
            return
        setattr(self, flag_name, True)
        plugin = self._get_v2_live_state_owner()
        if plugin is None:
            setattr(self, flag_name, False)
            return
        changed = False
        try:
            lock = getattr(plugin, '_data_lock', None)
            if lock is not None:
                async with lock:
                    changed = self._write_private_companion_live_summary_locked(plugin, events, platform=platform)
                    if changed:
                        self._save_private_companion(plugin)
            else:
                changed = self._write_private_companion_live_summary_locked(plugin, events, platform=platform)
                if changed:
                    self._save_private_companion(plugin)
        except Exception as e:
            logger.debug(f'[B站直播] 写入 V2 直播小结失败: {e}')
            setattr(self, flag_name, False)
            return
        if changed:
            session_events.clear()
            setattr(self, '_twitch_session_started_at' if platform == 'twitch' else '_bili_session_started_at', 0.0)

    def _write_private_companion_live_summary_locked(self, plugin: Any, events: list[LiveDanmakuEvent], *, platform: str = 'bili') -> bool:
        data = getattr(plugin, 'data', None)
        if not isinstance(data, dict):
            return False
        summary = self._build_live_summary_payload(events, platform=platform)
        if not summary:
            return False
        store = self._v2_live_state_store(plugin, platform=platform)
        summaries = store.setdefault('summaries', [])
        if not isinstance(summaries, list):
            summaries = []
            store['summaries'] = summaries
        summaries.append(summary)
        del summaries[:-20]
        if not self._private_companion_writeback_enabled():
            logger.info('[B站直播] 已写入直播专用记忆小结: %s', summary['summary'])
            return True
        diaries = data.setdefault('bot_diaries', [])
        if not isinstance(diaries, list):
            diaries = []
            data['bot_diaries'] = diaries
        diary = {'date': summary['date'], 'kind': 'live_stream_summary', 'summary': summary['summary'], 'body': summary['body'], 'share_seed': summary['share_seed'], 'tags': ['直播', '互动', *summary['tags']], 'today_events': summary['today_events'], 'proactive_events': [], 'dream_fragments': [{'text': self._single_line_text(summary['share_seed'], 120), 'weight': 0.5, 'source': 'live_stream_companion'}], 'long_term_events': [], 'generated_at': time.time(), 'source': 'live_stream_companion'}
        diaries.append(diary)
        max_diaries = self._safe_parse_int(getattr(plugin, 'max_diary_entries', 30), 30)
        del diaries[:-max(1, max_diaries)]
        logger.info('[B站直播] 已写入 V2 直播小结: %s', summary['summary'])
        return True

    def _build_live_summary_payload(self, events: list[LiveDanmakuEvent], *, platform: str = 'bili') -> dict[str, Any]:
        if not events:
            return {}
        counts: dict[str, int] = {}
        viewers: dict[str, int] = {}
        highlights: list[str] = []
        for event in events:
            counts[event.event_type] = counts.get(event.event_type, 0) + 1
            if event.username and event.username != '系统':
                viewers[event.username] = viewers.get(event.username, 0) + 1
            if event.event_type in {'gift', 'super_chat', 'buy_guard'}:
                highlights.append(event.display_text())
        top_viewers = sorted(viewers.items(), key=lambda item: item[1], reverse=True)[:5]
        count_text = '、'.join((f'{key} {value} 条' for key, value in counts.items()))
        viewer_text = '、'.join((f'{name}({count})' for name, count in top_viewers)) or '零散观众'
        highlight_text = '；'.join((self._single_line_text(item, 80) for item in highlights[:5]))
        started_at = self._twitch_session_started_at if platform == 'twitch' else self._bili_session_started_at
        started = started_at or events[0].ts
        ended = max((item.ts for item in events))
        duration_minutes = max(1, int((ended - started) / 60))
        platform_label = 'Twitch' if platform == 'twitch' else 'B站'
        summary = f"本次{platform_label}直播约 {duration_minutes} 分钟，收到 {len(events)} 条互动（{count_text or '暂无分类'}）。"
        body = f'今晚直播间留下一段挺具体的互动：{summary}常出现的观众有 {viewer_text}。'
        if highlight_text:
            body += f' 其中比较亮的片段是：{highlight_text}。'
        else:
            body += ' 大多是普通弹幕和轻轻接话，气氛更像有人在旁边陪着说几句。'
        share_seed = f"直播间刚刚有 {len(events)} 条互动，{('还有礼物或醒目留言' if highlights else '主要是弹幕聊天')}。"
        return {'id': f'live-summary-{platform}-{int(time.time())}', 'platform': platform, 'date': time.strftime('%Y-%m-%d', time.localtime(ended)), 'started_at': started, 'ended_at': ended, 'duration_minutes': duration_minutes, 'counts': counts, 'top_viewers': [{'name': name, 'count': count} for name, count in top_viewers], 'highlights': highlights[:8], 'summary': summary, 'body': body, 'share_seed': share_seed, 'tags': ['礼物'] if highlights else ['弹幕'], 'today_events': [{'window': time.strftime('%H:%M', time.localtime(started)) + '-' + time.strftime('%H:%M', time.localtime(ended)), 'event': summary, 'mood': '轻快' if highlights else '平稳'}]}

    def _private_companion_today_key(self, suffix: str='') -> str:
        today = time.strftime('%Y-%m-%d', time.localtime())
        return f'{suffix}-{today}' if suffix else today

    def _save_private_companion(self, plugin: Any) -> None:
        saver = getattr(getattr(self, '_live_state_store', None), 'save', None)
        if not callable(saver):
            saver = getattr(plugin, '_save_data_sync', None)
        if callable(saver):
            saver()

    def _inject_vts_command_fallback_instruction(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """避免 VTS 命令未路由时，模型编造跨平台操作指引。"""
        from ..request_context import set_turn_context

        set_turn_context(req, 'vts_auth')
        message = re.sub('\\s+', ' ', str(getattr(event, 'message_str', '') or '')).strip()
        if not re.fullmatch('/?vts_auth', message, flags=re.IGNORECASE):
            return
        set_turn_context(req, 'vts_auth', '## VTS 授权入口\nVTube Studio 授权在陪伴小屋的直播与表演设置中进行。用户询问连接或授权时，告诉用户打开该设置并点击连接或申请授权；只有实际操作回执才能证明成功。')

    async def _load_token(self) -> Optional[str]:
        """从框架 KV 存储加载 Token"""
        return await self.get_kv_data(KV_KEY_TOKEN, None)

    async def _save_token(self, token: str):
        """保存 Token 到框架 KV 存储"""
        await self.put_kv_data(KV_KEY_TOKEN, token)

    async def _clear_saved_vts_token(self, reason: str='') -> None:
        """清除已撤销授权，避免每次请求都重复提交同一个失效 Token。"""
        self._last_vts_auth_error = str(reason or 'VTube Studio 授权已失效')
        for client in (getattr(self, 'vts', None), getattr(self, '_parameter_vts', None)):
            forget = getattr(client, 'forget_authentication', None)
            if callable(forget):
                forget()
        self._connected = False
        delete = getattr(self, 'delete_kv_data', None)
        try:
            if callable(delete):
                await delete(KV_KEY_TOKEN)
            else:
                await self.put_kv_data(KV_KEY_TOKEN, None)
        except Exception as exc:
            logger.warning(f'[VTS] 清理失效认证 Token 失败: {exc}')

    async def _authenticate_saved_token(self, client: Any) -> bool:
        """用持久 Token 认证；确认失效时立即清理，不吞掉网络错误。"""
        if bool(getattr(client, 'is_authenticated', False)):
            return True
        lock = getattr(self, '_vts_auth_lock', None)
        if lock is None:
            lock = asyncio.Lock()
            self._vts_auth_lock = lock
        async with lock:
            if bool(getattr(client, 'is_authenticated', False)):
                return True
            saved_token = await self._load_token()
            if not saved_token:
                return False
            try:
                authenticated = bool(await client.authenticate(saved_token))
            except VTSResponseError:
                reason = str(getattr(client, 'last_auth_error', '') or '')
                if not reason:
                    raise
                await self._clear_saved_vts_token(reason)
                return False
            if not authenticated:
                reason = str(getattr(client, 'last_auth_error', '') or 'VTube Studio 拒绝了已保存的认证 Token')
                await self._clear_saved_vts_token(reason)
                return False
            self._last_vts_auth_error = ''
            return True

    async def _ensure_connection(self) -> str:
        """确保连接可用，返回错误消息或空字符串"""
        if not await self._check_and_reconnect():
            if self._last_vts_auth_error:
                return '❌ VTube Studio 授权已失效，旧 Token 已清理，请发送 /vts_auth 重新授权。'
            return '❌ 未连接到 VTube Studio，请先发送 /vts_auth 进行认证。'
        return ''
