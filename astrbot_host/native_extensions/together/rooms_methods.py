"""V2 together rooms services."""
from __future__ import annotations
from .common import *

class TogetherRoomsMethods:

    async def _start_room_server(self) -> None:
        try:
            await self.room_server.start()
            logger.info('[TogetherCompanion] 房间服务后台启动完成')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error('[TogetherCompanion] 房间服务启动失败: %s', exc, exc_info=True)
            raise

    async def _ensure_mobile_room_access(self, *, via_mobile_gateway: bool=False) -> dict[str, Any]:
        """Prepare room access, using the local gateway proxy when requested."""
        if not self.server_enabled:
            raise RuntimeError('共同房间服务未启用')
        if not self.room_server.running:
            try:
                await self.room_server.start()
            except Exception as exc:
                raise RuntimeError(f'共同房间服务启动失败：{_single_line(exc)}') from exc
        if via_mobile_gateway:
            return {'url': self.room_server.local_base_url, 'tunnel_started': False, 'tunnel_ready': True, 'fixed_public_url': False, 'mobile_gateway': True}
        if self.public_base_url:
            return {'url': self.public_base_url, 'tunnel_started': False, 'tunnel_ready': True, 'fixed_public_url': True}
        tunnel_started = not self.quick_tunnel.running
        self.quick_tunnel.local_url = self.room_server.local_base_url
        try:
            url = await self.quick_tunnel.start(timeout=40)
        except Exception as exc:
            raise RuntimeError(f'自动启动手机公网访问失败：{_single_line(exc, 300)}') from exc
        tunnel_ready = False
        for _ in range(16):
            status_getter = getattr(self.quick_tunnel, 'status', None)
            status = status_getter() if callable(status_getter) else {}
            if bool(status.get('ready')):
                tunnel_ready = True
                break
            if not self.quick_tunnel.running:
                raise RuntimeError('临时公网访问进程已意外退出')
            await asyncio.sleep(0.5)
        return {'url': url, 'tunnel_started': tunnel_started, 'tunnel_ready': tunnel_ready, 'fixed_public_url': False}

    async def _send_private_room_link(self, event: AstrMessageEvent, message: str) -> bool:
        sender_id = _single_line(event.get_sender_id(), 80)
        if not sender_id.isdigit():
            return False
        bot = getattr(event, 'bot', None)
        if bot is None:
            return False
        user_id = int(sender_id)
        errors: list[str] = []
        direct = getattr(bot, 'send_private_msg', None)
        if callable(direct):
            try:
                result = direct(user_id=user_id, message=message)
                if inspect.isawaitable(result):
                    await result
                return True
            except Exception as exc:
                errors.append(_single_line(exc, 160))
        api = getattr(bot, 'api', None)
        action = getattr(api, 'call_action', None)
        if callable(action):
            try:
                result = action('send_private_msg', user_id=user_id, message=message)
                if inspect.isawaitable(result):
                    await result
                return True
            except Exception as exc:
                errors.append(_single_line(exc, 160))
        logger.warning('[TogetherCompanion] 群聊邀请链接私发失败: user=%s errors=%s', sender_id, ' | '.join(errors) or '当前适配器不支持私聊发送')
        return False

    def _revoke_unused_ticket(self, ticket: RoomTicket) -> None:
        revoke = getattr(getattr(self, 'ticket_store', None), 'revoke', None)
        if callable(revoke):
            revoke(ticket.token)

    async def _open_room_from_llm_tool(self, mode: str, event: AstrMessageEvent) -> str:
        """Create a short-lived room for an explicit LLM tool request."""
        normalized_mode = normalize_room_mode(mode)
        if normalized_mode == 'work' and (not self.work_collaboration_available()):
            return json.dumps({'status': 'unavailable', 'message': '工作协同当前不可用；未检测到兼容的屏幕伙伴插件。不要声称已经进入协同房间，也不要反复重试。'}, ensure_ascii=False)
        if self._get_chat_provider() is None:
            return json.dumps({'status': 'error', 'message': '尚未配置有效的实时共处对话模型，请先在插件拓展页选择对话模型。'}, ensure_ascii=False)
        try:
            access = await self._ensure_mobile_room_access()
        except Exception as exc:
            return json.dumps({'status': 'error', 'message': _single_line(exc, 300)}, ensure_ascii=False)
        sender_id = _single_line(event.get_sender_id(), 80)
        label = {'watch': '共同观影', 'work': '工作协同', 'novel': '一起阅读'}.get(normalized_mode, '通话')
        access_note = '已自动启动临时手机公网访问。' if access['tunnel_started'] else '手机公网访问已经可用。'
        if not access['tunnel_ready']:
            access_note += '临时域名可能还需数秒生效，如首次打不开请稍后重试。'
        selection_note = '页面打开后，请由用户选择或粘贴要看的视频；当前工具不会自动选择或播放视频。' if normalized_mode == 'watch' else '页面打开后可从书库继续阅读，或导入 TXT、Markdown、EPUB 小说。' if normalized_mode == 'novel' else '页面打开后会结合屏幕伙伴提供的脱敏工作上下文开始协同。' if normalized_mode == 'work' else '页面打开后即可开始文字或语音通话。'
        user_ticket = self.issue_room_ticket(mode=normalized_mode, user_id=sender_id)
        room_url = self._ticket_url(user_ticket)
        if self._event_is_group(event):
            private_message = f'{label}的手机房间已经准备好了。{access_note}\n{room_url}\n请直接用手机浏览器打开；这是短期一次性链接，请勿转发。'
            private_sent = await self._send_private_room_link(event, private_message)
            if not private_sent:
                self._revoke_unused_ticket(user_ticket)
                return json.dumps({'status': 'error', 'message': '当前 QQ 会话不允许 Bot 向群成员临时私发链接，请先私聊 Bot 再说同样的话或发送 /一起。', 'group_delivery_blocked': True}, ensure_ascii=False)
            return json.dumps({'status': 'ok', 'room_type': normalized_mode, 'message': f'{label}邀请链接已私发给发起人，群内不要重复公开链接。', 'room_url': '', 'credential_included': False, 'delivered_privately': True, 'expires_at': user_ticket.expires_at, 'final_response_instruction': '只需在群里简短告知邀请链接已私发，不得输出或复述任何房间 URL。'}, ensure_ascii=False)
        return json.dumps({'status': 'ok', 'room_type': normalized_mode, 'message': f'{label}房间已准备好。{access_note}{selection_note}', 'room_url': room_url, 'credential_included': True, 'mobile_public_access': True, 'tunnel_started': access['tunnel_started'], 'tunnel_ready': access['tunnel_ready'], 'expires_at': user_ticket.expires_at, 'final_response_instruction': '必须在最终回复中逐字完整输出 room_url；room_url 中的 /join/凭证路径和 ?mode= 参数都不得删除、截断、改写，也不得只回复域名。'}, ensure_ascii=False)

    def _room_base_url(self) -> str:
        tunnel_url = self.quick_tunnel.url if self.quick_tunnel.running else ''
        return self.public_base_url or tunnel_url or self.room_server.local_base_url

    def _ticket_url(self, ticket: RoomTicket) -> str:
        return f"{self._room_base_url()}/join/{quote(ticket.token, safe='')}?mode={quote(ticket.mode, safe='')}"

    def issue_room_ticket(self, *, mode: str, user_id: str='', native_v2: dict[str, Any] | None=None) -> RoomTicket:
        resolved_user = _single_line(user_id, 80) or self._resolve_primary_user_id()
        binding = NativeRoomBinding(**native_v2) if native_v2 is not None else None
        return self.ticket_store.issue(mode=self.resolve_room_mode(mode), user_id=resolved_user, native_v2=binding)

    def resolve_room_mode(self, mode: str) -> str:
        """Return the mode this plugin can actually run for a new room."""
        resolved = normalize_room_mode(mode)
        if resolved == 'work' and (not self.work_collaboration_available()):
            return 'call'
        return resolved

    @staticmethod
    def _room_activity_kind(room: RoomSession) -> str:
        return {'watch': 'shared_watch', 'work': 'shared_work', 'novel': 'shared_reading'}.get(room.mode, 'shared_call')

    def _room_activity_label(self, room: RoomSession) -> str:
        if room.mode == 'watch':
            title = _single_line(room.media_state.get('title'), 100)
            return f'正在和主要用户一起看《{title}》' if title else '正在和主要用户一起看视频'
        if room.mode == 'work':
            current = room.work_context.get('current') if isinstance(room.work_context, dict) else {}
            resource = _single_line(current.get('resource_label'), 100) if isinstance(current, dict) else ''
            return f'正在和主要用户协同处理“{resource}”' if resource else '正在和主要用户进行工作协同'
        if room.mode == 'novel':
            title = _single_line(room.novel_state.get('book_title'), 100)
            return f'正在和主要用户一起读《{title}》' if title else '正在和主要用户一起阅读'
        return '正在和主要用户通话'

    async def open_room(self, ticket: RoomTicket, websocket: Any) -> RoomSession:
        if not _single_line(ticket.user_id, 80):
            raise RoomIdentityError('together_ticket_identity_required')
        room_mode = normalize_room_mode(ticket.mode)
        if ticket.room_id:
            room = next((item for item in list(self.rooms.values()) + list(getattr(self, 'detached_rooms', {}).values()) if item.room_id == ticket.room_id and (not item.integration_closed)), None)
            if room is None or room.mode != room_mode or (not ticket.invitation_id):
                raise RoomIdentityError('together_invited_room_unavailable')
            if ticket.user_id == room.user_id:
                raise RoomIdentityError('together_room_owner_already_connected')
            member = self.admit_room_member(room, ticket.user_id)
            if member.websocket is not None and (not bool(getattr(member.websocket, 'closed', False))):
                raise RoomIdentityError('together_member_already_connected')
            member.websocket = websocket
            self.detached_rooms.pop(member.resume_token, None)
            close_task = room.detach_close_task
            room.detach_close_task = None
            if isinstance(close_task, asyncio.Task) and (not close_task.done()):
                close_task.cancel()
            return room
        room = RoomSession(room_id=uuid.uuid4().hex, ticket_token=ticket.token, mode=room_mode, user_id=ticket.user_id, websocket=websocket, native_v2=ticket.native_v2)
        room.resume_token = uuid.uuid4().hex
        room.members[ticket.user_id] = RoomMember(user_id=ticket.user_id, role='owner', websocket=websocket, resume_token=room.resume_token)
        self.rooms[room.room_id] = room
        logger.info('[TogetherCompanion] 房间已连接: room=%s mode=%s user=%s', room.room_id[:10], room.mode, room.user_id or '未指定')
        await self._prime_astrbot_room_history(room)
        await self._notify_shared_activity_started(room)
        return room

    def _ensure_room_members(self, room: RoomSession) -> dict[str, RoomMember]:
        members = getattr(room, 'members', None)
        if not isinstance(members, dict):
            members = {}
            room.members = members
        owner_id = _single_line(getattr(room, 'user_id', ''), 80)
        if owner_id and owner_id not in members:
            resume_token = _single_line(getattr(room, 'resume_token', ''), 160) or uuid.uuid4().hex
            if not getattr(room, 'resume_token', ''):
                room.resume_token = resume_token
            members[owner_id] = RoomMember(owner_id, 'owner', getattr(room, 'websocket', None), resume_token)
        return members

    def admit_room_member(self, room: RoomSession, user_id: str, *, scope_key: str='') -> RoomMember:
        """Record a member already accepted by the native invitation owner."""
        subject = _single_line(user_id, 80)
        if not subject or subject == room.user_id or room.integration_closed:
            raise RoomIdentityError('together_room_member_invalid')
        members = self._ensure_room_members(room)
        member = members.get(subject)
        if member is None:
            guest_count = sum((item.role == 'guest' for item in members.values()))
            member = RoomMember(subject, 'guest', resume_token=uuid.uuid4().hex, display_name=f'成员 {guest_count + 1}', scope_key=scope_key)
            members[subject] = member
        elif member.websocket is not None and (not bool(getattr(member.websocket, 'closed', False))):
            raise RoomIdentityError('together_member_already_connected')
        if scope_key:
            member.scope_key = scope_key
        return member

    def issue_member_room_ticket(self, room: RoomSession, *, user_id: str, invitation_id: str) -> RoomTicket:
        subject = _single_line(user_id, 80)
        invite = _single_line(invitation_id, 160)
        active = any((item is room for item in list(self.rooms.values()) + list(getattr(self, 'detached_rooms', {}).values())))
        if not subject or subject == room.user_id or (not invite) or (not active) or room.integration_closed or (room.mode not in {'call', 'watch'}):
            raise RoomIdentityError('together_room_member_ticket_invalid')
        return self.ticket_store.issue(mode=room.mode, user_id=subject, room_id=room.room_id, invitation_id=invite)

    def room_member_for_websocket(self, room: RoomSession, websocket: Any) -> RoomMember | None:
        for member in self._ensure_room_members(room).values():
            if member.websocket is websocket:
                return member
        return None

    @staticmethod
    def member_payload_allowed(member: RoomMember, payload: dict[str, Any]) -> bool:
        if member.role == 'owner':
            return True
        return _single_line(payload.get('type'), 40).lower() in {'call_state', 'call_frame', 'call_activity', 'user_text', 'audio_utterance', 'interrupt', 'exclude_utterance', 'ping'}

    def public_room_members(self, room: RoomSession) -> list[dict[str, Any]]:
        result = []
        for member in self._ensure_room_members(room).values():
            result.append({'name': member.display_name or ('房主' if member.role == 'owner' else '共同成员'), 'role': member.role, 'connected': member.websocket is not None and (not bool(getattr(member.websocket, 'closed', False)))})
        return result

    def can_resume_room(self, token: str) -> bool:
        room = self.detached_rooms.get(str(token or ''))
        return bool(room is not None and (not room.integration_closed))

    async def detach_room(self, room: RoomSession, websocket: Any=None) -> None:
        """连接断开时保留房间一段时间，允许客户端凭 resume_token 恢复。"""
        if room.integration_closed:
            return
        members = self._ensure_room_members(room)
        if websocket is None:
            member = members.get(room.user_id)
        else:
            member = next((item for item in members.values() if item.websocket is websocket), None)
        if member is None:
            return
        member.websocket = None
        was_call_active = room.call_active
        member.call_active = False
        member.call_camera_frame = ''
        member.call_camera_updated_at = 0.0
        room.call_active = any((item.call_active for item in members.values()))
        if member.role == 'owner':
            room.websocket = None
            if not room.call_active:
                room.update_call_camera('')
        if member.resume_token:
            self.detached_rooms[member.resume_token] = room
        connected = any((item.websocket is not None and (not bool(getattr(item.websocket, 'closed', False))) for item in members.values()))
        if connected and was_call_active and (not room.call_active):
            try:
                await self._stop_live_mouth_sync(room)
            except Exception:
                pass
        if connected:
            return
        if not room.resume_token:
            await self.close_room(room)
            return
        self.detached_rooms[room.resume_token] = room
        logger.info('[TogetherCompanion] 房间已挂起，等待恢复: room=%s grace=%ss', room.room_id[:10], self.room_resume_grace_seconds)
        try:
            await self._stop_live_mouth_sync(room)
        except Exception:
            pass

        async def close_after_grace() -> None:
            try:
                await asyncio.sleep(self.room_resume_grace_seconds)
            except asyncio.CancelledError:
                return
            if self.detached_rooms.get(room.resume_token) is room and (not any((item.websocket is not None and (not bool(getattr(item.websocket, 'closed', False))) for item in self._ensure_room_members(room).values()))):
                await self.close_room(room)
        room.detach_close_task = asyncio.create_task(close_after_grace())

    async def resume_room(self, token: str, websocket: Any) -> RoomSession | None:
        room = self.detached_rooms.pop(str(token or ''), None)
        if room is None or room.integration_closed:
            return None
        members = self._ensure_room_members(room)
        member = next((item for item in members.values() if item.resume_token == str(token or '')), None)
        if member is None or (member.websocket is not None and (not bool(getattr(member.websocket, 'closed', False)))):
            return None
        member.websocket = websocket
        if member.role == 'owner':
            room.websocket = websocket
        task = room.detach_close_task
        room.detach_close_task = None
        if isinstance(task, asyncio.Task) and (not task.done()):
            task.cancel()
        logger.info('[TogetherCompanion] 房间已恢复: room=%s', room.room_id[:10])
        return room

    async def replay_room_state(self, room: RoomSession, *, websocket: Any=None, member_role: str='owner') -> None:
        """恢复连接后向客户端回放关键房间状态。"""
        await self.send_room_payload(room, {'type': 'mode', 'mode': room.mode}, websocket=websocket)
        if member_role == 'owner' and room.mode == 'work':
            await self._send_work_context(room, force=True)
        if member_role == 'owner' and room.mode == 'novel':
            await self._send_novel_library(room)
            if room.novel_state.get('book_id'):
                await self._send_novel_chapter(room, str(room.novel_state.get('book_id') or ''), int(room.novel_state.get('chapter_index') or 0))
        source = self.media_sources.get(room.media_token) if room.media_token else None
        if room.mode == 'watch' and source is not None and (not source.expired):
            await self.send_room_payload(room, {'type': 'media_ready', 'url': f'/media/{source.token}', 'title': source.title, 'duration': source.duration, 'quality': source.quality, 'quality_label': source.quality_label, 'source': 'bilibili', 'subtitle_available': bool(source.subtitle_cues), 'subtitle_language': source.subtitle_language, 'resume_time': float(room.media_state.get('current_time') or 0.0)}, websocket=websocket)
        for item in room.history[-6:]:
            role = str(item.get('role') or '')
            content = str(item.get('content') or '').strip()
            if not content:
                continue
            payload_type = 'user_text' if role == 'user' else 'bot_text'
            await self.send_room_payload(room, {'type': payload_type, 'text': content}, websocket=websocket)

    async def close_room(self, room: RoomSession) -> None:
        if room.integration_closed:
            return
        room.integration_closed = True
        self.detached_rooms.pop(room.resume_token, None)
        detach_task = room.detach_close_task
        room.detach_close_task = None
        if isinstance(detach_task, asyncio.Task) and detach_task is not asyncio.current_task() and (not detach_task.done()):
            detach_task.cancel()
        active_tasks = [task for task in (room.generation_task, room.media_resolution_task, room.watch_knowledge_task) if isinstance(task, asyncio.Task) and task is not asyncio.current_task() and (not task.done())]
        room.cancel_generation()
        room.cancel_media_resolution()
        room.cancel_watch_knowledge()
        if active_tasks:
            _done, pending = await asyncio.wait(active_tasks, timeout=3.0)
            if pending:
                for task in pending:
                    task.cancel()
                logger.debug('[TogetherCompanion] 房间关闭时仍有 %s 个任务等待取消: room=%s', len(pending), room.room_id[:10])
        await self._stop_live_mouth_sync(room)
        if self.record_shared_experiences and (not room.shared_experience_finalized):
            try:
                task = room.shared_experience_task
                if isinstance(task, asyncio.Task) and (not task.done()):
                    await asyncio.wait_for(asyncio.shield(task), timeout=12)
                elif not room.shared_experience_finalized:
                    await asyncio.wait_for(self._record_shared_experience(room), timeout=12)
            except asyncio.TimeoutError:
                logger.debug('[TogetherCompanion] 房间结束时共享经历整理未及时完成')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug('[TogetherCompanion] 房间结束时共享经历写入失败: %s', exc)
        memory_task = room.watch_memory_task
        room.cancel_watch_memory()
        if isinstance(memory_task, asyncio.Task) and memory_task is not asyncio.current_task() and (not memory_task.done()):
            await asyncio.wait({memory_task}, timeout=3.0)
        for token, attached in list(self.detached_rooms.items()):
            if attached is room:
                self.detached_rooms.pop(token, None)
        for member in self._ensure_room_members(room).values():
            websocket = member.websocket
            member.websocket = None
            if websocket is not None and (not bool(getattr(websocket, 'closed', False))):
                try:
                    await websocket.close(code=1000, message='房间已结束'.encode('utf-8'))
                except Exception as exc:
                    logger.debug('[TogetherCompanion] 关闭成员连接失败: %s', exc)
        room.websocket = None
        self.rooms.pop(room.room_id, None)
        self._drop_room_media(room.room_id)
        self.ticket_store.revoke(room.ticket_token)
        revoke_room_tickets = getattr(self.ticket_store, 'revoke_room', None)
        if callable(revoke_room_tickets):
            revoke_room_tickets(room.room_id)
        await self._notify_shared_activity_ended(room)
        logger.info('[TogetherCompanion] 房间已关闭: room=%s', room.room_id[:10])

    async def room_bootstrap(self, room: RoomSession, *, member_role: str='owner') -> dict[str, Any]:
        is_owner = member_role == 'owner'
        capabilities = await self._capabilities(room)
        work_capability = capabilities.get('work')
        if not isinstance(work_capability, dict):
            work_capability = {'available': False, 'label': ''}
        companion_tts = {'voice_language': room.native_v2.tts_language} if room.native_v2 is not None else self._companion_realtime_voice_config()
        scene = {} if room.native_v2 is not None else self._companion_scene(room.user_id)
        relationship = scene.get('relationship') if isinstance(scene.get('relationship'), dict) else {}
        work_context = await self._work_collaboration_context(room, force=True) if is_owner and room.mode == 'work' and (work_capability.get('available') is True) else {}
        return {'id': room.room_id, 'mode': room.mode, 'invite_available': room.native_v2 is None, 'bot_name': self._room_bot_name(room), 'user_name': _single_line(relationship.get('name'), 60) if is_owner else '', 'avatar_url': f'/avatar?bot_id={room.native_v2.account_id}' if room.native_v2 is not None else '/avatar', 'call': {'proactive_enabled': bool(getattr(self, 'call_proactive_enabled', True)), 'idle_seconds': int(getattr(self, 'call_idle_seconds', 120)), 'camera_available': True, 'camera_vision_available': capabilities['vision']['available'], 'camera_label': capabilities['vision']['label'], 'tts_enabled': bool(room.call_tts_enabled), 'realtime_duplex_enabled': bool(getattr(self, 'realtime_duplex_enabled', False)), 'model_hangup_enabled': bool(getattr(self, 'model_hangup_enabled', True))}, 'stt': {'mode': self.stt_mode, 'server_available': capabilities['stt']['available'], 'server_label': capabilities['stt']['label'], 'browser_language': self.browser_language}, 'tts': {'server_available': capabilities['tts']['available'], 'server_label': capabilities['tts']['label'], 'browser_fallback': self.browser_tts_fallback, 'browser_language': self._companion_tts_browser_language(companion_tts), 'timeout_seconds': room.native_v2.tts_timeout_seconds if room.native_v2 is not None else int(getattr(self, 'tts_timeout_seconds', 60)), 'volume_ratio': _clamp_float(getattr(self, 'tts_volume_ratio', 1.0), 1.0, 0.0, 1.0)}, 'chat': capabilities['chat'], 'vision': capabilities['vision'], 'watch': {'auto_comment': self.watch_auto_comment, 'tts_enabled': bool(room.watch_tts_enabled), 'comment_interval_seconds': self.watch_comment_interval_seconds, 'scene_min_interval_seconds': self.watch_scene_min_interval_seconds, 'duck_video_volume': self.watch_duck_video_volume, 'duck_volume_ratio': self.watch_duck_volume_ratio}, 'novel': {'enabled': bool(self.novel_library_enabled) if is_owner else False, 'books': await asyncio.to_thread(self._novel_library_for(room).list_books, user_id=room.user_id) if is_owner and self.novel_library_enabled else [], 'state': dict(room.novel_state) if is_owner else {}, 'max_upload_mb': int(self.novel_max_upload_mb) if is_owner else 0, 'allowed_formats': ['txt', 'md', 'epub'] if is_owner else [], 'answer_with_sources': bool(self.novel_answer_with_sources) if is_owner else False, 'spoiler_policy': self.novel_spoiler_policy if is_owner else ''}, 'work': {**(work_capability if is_owner else {'available': False, 'label': ''}), 'context': work_context, 'state': dict(room.work_state) if is_owner else {}}}

    @staticmethod
    def _room_umo(room: RoomSession) -> str:
        binding = room.native_v2
        kind = 'GroupMessage' if binding.scope_kind == 'group' else 'FriendMessage'
        return f'{binding.platform}:{kind}:{binding.session_id}'

    def _room_chat_provider(self, room: RoomSession | None) -> Any | None:
        route_provider = self._task_route_provider(room.user_id, 'conversation.reply') if room else None
        if route_provider is not None:
            return route_provider
        if room is None or room.native_v2 is None:
            provider = self._get_chat_provider()
        else:
            provider = self._get_provider_by_id(room.native_v2.model_provider_id)
        if provider is not None and callable(getattr(provider, 'text_chat', None)):
            return provider
        getter = getattr(self.context, 'get_using_provider', None)
        if room is not None and callable(getter):
            try:
                provider = getter(room.session_id)
            except Exception:
                provider = None
        return provider if provider is not None and callable(getattr(provider, 'text_chat', None)) else None

    def _room_vision_provider(self, room: RoomSession | None) -> Any | None:
        if room is not None:
            routed = self._task_route_provider(room.user_id, 'screen.interpretation')
            if routed is not None and self._provider_image_capability(routed) is not False:
                return routed
        if room is None or room.native_v2 is None:
            return self._get_vision_provider()
        provider_id = str(getattr(self, 'vision_provider_id', '') or '').strip()
        if provider_id:
            provider = self._get_provider_by_id(provider_id)
            return provider if (callable(getattr(provider, 'text_chat', None))
                and self._provider_image_capability(provider) is not False) else None
        provider = self._room_chat_provider(room)
        return provider if provider is not None and self._provider_image_capability(provider) is not False else None

    async def handle_room_payload(self, room: RoomSession, payload: dict[str, Any], *, member: RoomMember | None=None) -> None:
        member = member or self._ensure_room_members(room).get(room.user_id)
        message_type = _single_line(payload.get('type'), 40).lower()
        if message_type == 'call_state':
            was_active = room.call_active
            if member is not None:
                member.call_active = bool(payload.get('active'))
                room.call_active = any((item.call_active for item in self._ensure_room_members(room).values()))
            else:
                room.call_active = bool(payload.get('active'))
            room.call_last_user_activity = time.monotonic()
            if member is None or member.role == 'owner':
                room.call_idle_check_count = 0
            if member is not None and (not member.call_active):
                member.call_camera_frame = ''
                member.call_camera_updated_at = 0.0
            owner_stopped = member is not None and member.role == 'owner' and (not member.call_active)
            if member is None and (not room.call_active) or owner_stopped:
                room.update_call_camera('')
            if was_active and (not room.call_active):
                room.call_last_proactive_at = 0.0
                await self._stop_live_mouth_sync(room)
            return
        if message_type == 'call_frame':
            if room.mode != 'call' or member is None or (not member.call_active):
                return
            if payload.get('active') is False:
                if member.role == 'guest':
                    member.call_camera_frame = ''
                else:
                    room.update_call_camera('')
                return
            frame = self._normalize_frame_data_url(payload.get('image'))
            if frame:
                if member.role == 'guest':
                    member.call_camera_frame = frame
                    member.call_camera_updated_at = time.monotonic()
                else:
                    room.update_call_camera(frame)
            return
        if message_type == 'call_activity':
            if room.call_active:
                room.call_last_user_activity = time.monotonic()
                room.call_idle_check_count = 0
            return
        if message_type == 'call_idle':
            if member is not None and member.role != 'owner':
                return
            self._update_client_time_context(room, payload)
            proactive_enabled = bool(getattr(self, 'call_proactive_enabled', True))
            hangup_enabled = bool(getattr(self, 'model_hangup_enabled', True))
            if not room.call_active or room.mode != 'call' or (not (proactive_enabled or hangup_enabled)):
                return
            now = time.monotonic()
            idle_for = now - float(room.call_last_user_activity or now)
            if idle_for < self.call_idle_seconds:
                return
            if now - float(room.call_last_proactive_at or 0.0) < self.call_idle_seconds * 0.75:
                return
            if isinstance(room.generation_task, asyncio.Task) and (not room.generation_task.done()):
                return
            room.call_last_proactive_at = now
            room.call_idle_check_count += 1
            self._start_room_task(room, self._generate_call_proactive(room, idle_seconds=int(idle_for)))
            return
        if message_type == 'set_watch_tts':
            room.watch_tts_enabled = bool(payload.get('enabled'))
            await self.send_room_payload(room, {'type': 'watch_tts', 'enabled': room.watch_tts_enabled})
            return
        if message_type == 'set_call_tts':
            room.call_tts_enabled = bool(payload.get('enabled'))
            await self.send_room_payload(room, {'type': 'call_tts', 'enabled': room.call_tts_enabled})
            return
        if message_type == 'create_invite':
            if room.native_v2 is not None:
                await self.send_room_error(room, '请在 V2 陪伴对话中调用一起房间邀请，再把返回的邀请码发给受邀者。', code='v2_invitation_required')
                return
            binding = asdict(room.native_v2) if room.native_v2 is not None else None
            ticket = self.issue_room_ticket(mode=room.mode, user_id=room.user_id, native_v2=binding)
            await self.send_room_payload(room, {'type': 'invite_link', 'url': self._ticket_url(ticket), 'expires_at': ticket.expires_at})
            return
        if message_type == 'ping':
            await self._notify_shared_activity_updated(room)
            await self.send_room_payload(room, {'type': 'pong', 'ts': time.time()})
            if room.mode == 'work':
                work_context = await self._send_work_context(room)
                self._maybe_start_work_progress_check(room, work_context)
            return
        if message_type == 'set_mode':
            next_mode = normalize_room_mode(payload.get('mode'))
            if next_mode == 'work' and (not self.work_collaboration_available()):
                await self.send_room_payload(room, {'type': 'notice', 'message': '工作协同当前不可用'})
                await self.send_room_payload(room, {'type': 'mode', 'mode': room.mode})
                return
            changed = room.mode != next_mode
            if changed:
                room.cancel_generation()
                if next_mode != 'call':
                    room.update_call_camera('')
            room.mode = next_mode
            await self.send_room_payload(room, {'type': 'mode', 'mode': room.mode})
            if changed:
                await self.send_room_payload(room, {'type': 'stop_audio', 'interrupted': True})
                await self._stop_live_mouth_sync(room)
            if room.mode == 'work':
                await self._send_work_context(room, force=True)
            elif room.mode == 'novel':
                await self._send_novel_library(room)
            await self._notify_shared_activity_updated(room, force=changed)
            return
        if message_type == 'refresh_work_context':
            if room.mode == 'work' and self.work_collaboration_available():
                await self._send_work_context(room, force=True)
                await self._notify_shared_activity_updated(room, force=True)
            return
        if message_type == 'novel_library_request':
            await self._send_novel_library(room)
            return
        if message_type in {'novel_open', 'novel_chapter_request'}:
            if not self.novel_library_enabled:
                await self.send_room_error(room, '小说书库已关闭', code='novel_disabled')
                return
            book_id = _single_line(payload.get('book_id'), 80)
            if not book_id:
                await self._send_novel_library(room)
                return
            chapter_index = _clamp_int(payload.get('chapter_index', 0), 0, 0, 100000)
            if message_type == 'novel_open':
                progress = await asyncio.to_thread(self._novel_library_for(room).progress, room.user_id, book_id)
                chapter_index = int(progress.get('chapter_index') or chapter_index)
                room.novel_state = {}
            await self._send_novel_chapter(room, book_id, chapter_index, include_chapters=message_type == 'novel_open', navigation_sequence=_clamp_int(payload.get('navigation_sequence'), 0, 0, 2147483647))
            return
        if message_type == 'novel_progress':
            book_id = _single_line(payload.get('book_id'), 80)
            if not book_id or book_id != str(room.novel_state.get('book_id') or ''):
                return
            chapter_index = _clamp_int(payload.get('chapter_index'), 0, 0, 100000)
            paragraph_index = _clamp_int(payload.get('paragraph_index'), 0, 0, 1000000)
            if chapter_index != int(room.novel_state.get('chapter_index') or 0):
                return
            progress_sequence = _clamp_int(payload.get('progress_sequence'), 0, 0, 2147483647)
            if progress_sequence and progress_sequence < room.novel_progress_sequence:
                return
            if progress_sequence:
                room.novel_progress_sequence = progress_sequence
            room.novel_state['paragraph_index'] = paragraph_index
            room.novel_state['progress_sequence'] = int(room.novel_progress_sequence)
            room.novel_state['updated_at'] = time.time()
            if self.novel_auto_save_progress and room.user_id:
                try:
                    progress = await asyncio.to_thread(self._novel_library_for(room).save_progress, room.user_id, book_id, chapter_index, paragraph_index)
                    room.novel_state['paragraph_index'] = int(progress.get('paragraph_index') or 0)
                    room.novel_last_progress_at = time.monotonic()
                except ValueError as exc:
                    await self.send_room_error(room, str(exc), code='novel_progress_failed')
                    return
            await self.send_room_payload(room, {'type': 'novel_state', 'state': dict(room.novel_state)})
            await self._notify_shared_activity_updated(room)
            return
        if message_type == 'player_state':
            state = self._normalize_media_state(payload.get('state'))
            player_event = _single_line(payload.get('event'), 40).lower()
            if state:
                self._apply_player_state(room, state, player_event)
                await self._notify_shared_activity_updated(room)
                if player_event == 'ended':
                    self._schedule_shared_experience_record(room, delay_seconds=2.0)
            return
        if message_type == 'interrupt':
            interrupted = room.cancel_generation()
            room.active_utterance_id = ''
            await self.send_room_payload(room, {'type': 'stop_audio', 'interrupted': interrupted})
            await self._stop_live_mouth_sync(room)
            return
        if message_type == 'exclude_utterance':
            utterance_id = _single_line(payload.get('id'), 80)
            excluded = bool(utterance_id and utterance_id == room.active_utterance_id)
            if excluded:
                room.active_utterance_id = ''
                room.cancel_generation()
                room.watch_events = [item for item in room.watch_events if str((item.get('metadata') or {}).get('utterance_id') or '') != utterance_id]
                await self._stop_live_mouth_sync(room)
            await self.send_room_payload(room, {'type': 'utterance_excluded', 'id': utterance_id, 'excluded': excluded})
            if excluded:
                await self.send_room_payload(room, {'type': 'stop_audio', 'interrupted': True})
                await self.send_room_payload(room, {'type': 'status', 'state': 'listening', 'text': '正在听'})
            return
        if message_type == 'user_text':
            text = str(payload.get('text') or '').strip()[:4000]
            if text:
                self._update_client_time_context(room, payload)
                if member is not None and member.call_active and (room.mode == 'call'):
                    room.call_last_user_activity = time.monotonic()
                    room.call_idle_check_count = 0
                input_source = _single_line(payload.get('source'), 40).lower()
                speaker_label = member.display_name if member is not None and member.role == 'guest' else ''
                utterance_id = _single_line(payload.get('utterance_id'), 80) if input_source == 'browser_stt' else ''
                if input_source == 'browser_stt' and (not utterance_id):
                    utterance_id = uuid.uuid4().hex
                raw_alternatives = payload.get('alternatives')
                alternatives = [str(item or '').strip()[:4000] for item in (raw_alternatives[:3] if isinstance(raw_alternatives, list) else []) if str(item or '').strip()]
                state = self._normalize_media_state(payload.get('state'))
                if state and room.mode == 'watch':
                    self._apply_player_state(room, state, '')
                frame = self._normalize_frame_data_url(payload.get('frame'))
                if room.mode == 'call':
                    if member is not None and member.role == 'guest':
                        if not frame:
                            frame = str(getattr(member, 'call_camera_frame', '') or '')
                        updated_at = float(getattr(member, 'call_camera_updated_at', 0.0) or 0.0)
                        if not updated_at or time.monotonic() - updated_at > 12.0:
                            frame = ''
                    elif frame and (member is None or member.call_active):
                        room.update_call_camera(frame)
                    elif member is None or member.call_active:
                        frame = room.recent_call_camera_frame()
                    else:
                        frame = ''
                elif room.mode != 'watch':
                    frame = ''
                selected_text = str(payload.get('selected_text') or '').strip()[:2400]
                if input_source == 'browser_stt':
                    self._start_utterance_task(room, utterance_id, self._correct_stt_and_reply(room, text, source=input_source, alternatives=alternatives, image_data_url=frame, utterance_id=utterance_id, speaker_label=speaker_label))
                else:
                    room_text = f'{speaker_label}说：{text}' if speaker_label else text
                    self._append_user_watch_event(room, room_text)
                    if room.mode == 'novel':
                        self._start_room_task(room, self._reply_to_novel_user(room, text, selected_text=selected_text))
                    else:
                        self._start_room_task(room, self._reply_to_user(room, text, image_data_url=frame, speaker_label=speaker_label))
            return
        if message_type == 'audio_utterance':
            self._update_client_time_context(room, payload)
            if member is not None and member.call_active and (room.mode == 'call'):
                room.call_last_user_activity = time.monotonic()
                room.call_idle_check_count = 0
            audio_bytes, mime_type = self._decode_audio_payload(payload)
            if not audio_bytes:
                await self.send_room_error(room, '没有收到可识别的音频', code='empty_audio')
                return
            frame = self._normalize_frame_data_url(payload.get('frame'))
            if room.mode == 'call':
                if member is not None and member.role == 'guest':
                    if not frame:
                        frame = str(getattr(member, 'call_camera_frame', '') or '')
                    updated_at = float(getattr(member, 'call_camera_updated_at', 0.0) or 0.0)
                    if not updated_at or time.monotonic() - updated_at > 12.0:
                        frame = ''
                elif frame and (member is None or member.call_active):
                    room.update_call_camera(frame)
                elif member is None or member.call_active:
                    frame = room.recent_call_camera_frame()
                else:
                    frame = ''
            else:
                frame = ''
            utterance_id = _single_line(payload.get('utterance_id'), 80) or uuid.uuid4().hex
            self._start_utterance_task(room, utterance_id, self._transcribe_and_reply(room, audio_bytes, mime_type, image_data_url=frame, utterance_id=utterance_id, speaker_label=member.display_name if member is not None and member.role == 'guest' else ''))
            return
        if message_type == 'resolve_media':
            page_url = str(payload.get('url') or '').strip()[:2000]
            if not page_url:
                await self.send_room_payload(room, {'type': 'media_error', 'message': '没有收到需要解析的视频链接'})
                return
            self._start_media_resolution(room, self._resolve_media_for_room(room, page_url))
            return
        if message_type == 'watch_frame':
            trigger = _single_line(payload.get('trigger'), 40).lower()
            if not trigger:
                trigger = 'manual' if bool(payload.get('manual')) else 'heartbeat'
            if trigger not in {'manual', 'opening', 'scene_change', 'heartbeat', 'ending'}:
                trigger = 'heartbeat'
            if room.mode != 'watch' or (not self.watch_auto_comment and trigger != 'manual'):
                return
            if isinstance(room.generation_task, asyncio.Task) and (not room.generation_task.done()):
                if trigger == 'manual':
                    room.cancel_generation()
                else:
                    return
            state = self._normalize_media_state(payload.get('state'))
            if state:
                self._apply_player_state(room, state, '')
            frame = self._normalize_frame_data_url(payload.get('image'))
            if frame:
                if trigger == 'ending':
                    if room.watch_ending_epoch == room.watch_epoch:
                        return
                    room.watch_ending_epoch = room.watch_epoch
                captured_at = _clamp_float(payload.get('captured_at', room.media_state.get('current_time', 0.0)), float(room.media_state.get('current_time') or 0.0), 0.0, 864000.0)
                scene_score = _clamp_float(payload.get('scene_score', 0.0), 0.0, 0.0, 1.0)
                self._start_room_task(room, self._generate_watch_comment(room, frame, trigger=trigger, captured_at=captured_at, scene_score=scene_score), replace=trigger == 'manual')
            return
        await self.send_room_error(room, f"不支持的房间消息类型: {message_type or '空'}")

    def _start_room_task(self, room: RoomSession, operation: Any, *, replace: bool=True) -> None:
        previous = room.generation_task if replace else None
        if replace:
            if room.cancel_generation():
                logger.info('[TogetherCompanion] 新房间任务替换了上一条未完成任务: room=%s mode=%s', room.room_id[:10], room.mode)
        if not (isinstance(previous, asyncio.Task) and (not previous.done())):
            self._spawn_task(room, operation, attr='generation_task', label='房间任务失败', warn=True)
            return
        operation_started = False

        async def run() -> None:
            nonlocal operation_started
            await asyncio.wait({previous}, timeout=10)
            operation_started = True
            await operation
        task = self._spawn_task(room, run(), attr='generation_task', label='房间任务失败', warn=True)

        def ensure_operation_closed(finished: asyncio.Task) -> None:
            if not operation_started and inspect.iscoroutine(operation):
                operation.close()
        task.add_done_callback(ensure_operation_closed)

    async def send_room_payload(self, room: RoomSession, payload: dict[str, Any], *, websocket: Any=None, exclude_websocket: Any=None) -> bool:
        members = self._ensure_room_members(room)
        targets = [websocket] if websocket is not None else [member.websocket for member in members.values() if member.websocket is not None and member.websocket is not exclude_websocket]
        targets = [item for item in targets if item is not None and (not bool(getattr(item, 'closed', False)))]
        if not targets:
            logger.debug('[TogetherCompanion] 房间消息未发送：没有可用 WebSocket: room=%s type=%s', room.room_id[:10], _single_line(payload.get('type'), 40))
            return False
        async with room.send_lock:
            delivered = False
            for target in targets:
                try:
                    await target.send_json(payload, dumps=lambda value: json.dumps(value, ensure_ascii=False))
                    delivered = True
                except Exception as exc:
                    logger.warning('[TogetherCompanion] 房间消息发送失败: room=%s type=%s error=%s', room.room_id[:10], _single_line(payload.get('type'), 40), _single_line(exc, 240))
        return delivered

    async def send_room_error(self, room: RoomSession, message: str, *, code: str='room_error', websocket: Any=None) -> None:
        await self.send_room_payload(room, {'type': 'error', 'code': code, 'message': _single_line(message, 500)}, websocket=websocket)

    async def _prime_astrbot_room_history(self, room: RoomSession) -> None:
        """Load recent user/assistant turns before the first room prompt."""
        if room.history or (not room.user_id) or (not getattr(self, 'sync_astrbot_conversation', True)):
            return
        try:
            manager, unified_origin, conversation_id = await asyncio.wait_for(self._resolve_astrbot_conversation(room, create=False), timeout=3.0)
            if manager is None or not unified_origin or (not conversation_id):
                return
            getter = getattr(manager, 'get_conversation', None)
            if not callable(getter):
                return
            conversation = await asyncio.wait_for(getter(unified_origin, conversation_id, create_if_not_exists=False), timeout=3.0)
            history = self._conversation_history_items(conversation)
            max_messages = max(4, self.history_turns * 2)
            normalized: list[tuple[str, str]] = []
            for item in history[-max(20, max_messages * 3):]:
                role, content = self._unpack_conversation_message(item)
                if role in {'user', 'assistant'} and content:
                    normalized.append((role, content[:4000]))
            for role, content in normalized[-max_messages:]:
                room.append_turn(role, content, history_turns=self.history_turns)
            room.imported_history_count = len(room.history)
            if room.history:
                logger.debug('[TogetherCompanion] 已载入 AstrBot 历史上下文: room=%s messages=%s', room.room_id[:10], len(room.history))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug('[TogetherCompanion] 读取 AstrBot 历史上下文失败: %s', _single_line(exc, 160))

    @staticmethod
    def prepare_room_for_invitations(room: RoomSession) -> None:
        imported = max(0, int(getattr(room, 'imported_history_count', 0) or 0))
        if imported:
            del room.history[:imported]
            room.imported_history_count = 0

    def _room_bot_name(self, room: RoomSession) -> str:
        if room.native_v2 is not None:
            return _single_line(room.native_v2.persona_id, 60) or 'Bot'
        return self._bot_name() or 'Bot'
