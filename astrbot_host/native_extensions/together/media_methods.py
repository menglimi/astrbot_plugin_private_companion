"""V2 together media services."""
from __future__ import annotations
from .common import *

class TogetherMediaMethods:

    def novel_import_allowed(self, room: RoomSession) -> bool:
        if not self.novel_import_admin_only:
            return True
        primary_user = _single_line(self._resolve_primary_user_id(), 80)
        return not primary_user or primary_user == _single_line(room.user_id, 80)

    def _bilibili_bot_runtime(self) -> Any | None:
        getter = getattr(getattr(self, 'context', None), 'get_registered_star', None)
        if not callable(getter):
            return None
        for name in ('astrbot_plugin_bilibili_ai_bot', 'astrbot_plugin_bilibili_bot'):
            try:
                metadata = getter(name)
            except Exception:
                continue
            instance = getattr(metadata, 'star_cls', None) if metadata is not None else None
            if instance is not None and callable(getattr(instance, 'check_cookie', None)):
                return instance
        return None

    @staticmethod
    def _bilibili_bot_headers(runtime: Any) -> dict[str, str]:
        getter = getattr(runtime, '_headers', None)
        if not callable(getter):
            return {}
        raw_headers = getter()
        if not isinstance(raw_headers, dict):
            return {}
        allowed = {'cookie', 'user-agent', 'referer', 'accept', 'accept-encoding'}
        canonical_keys = {'cookie': 'Cookie', 'user-agent': 'User-Agent', 'referer': 'Referer', 'accept': 'Accept', 'accept-encoding': 'Accept-Encoding'}
        return {canonical_keys.get(str(key).strip().lower(), str(key).strip()): re.sub('[\\r\\n]', '', str(value or '')).strip() for key, value in raw_headers.items() if str(key or '').strip().lower() in allowed and str(value or '').strip()}

    async def _sync_bilibili_bot_cookie(self) -> dict[str, Any]:
        cache = getattr(self, '_bilibili_runtime_state', None)
        now = time.monotonic()
        if isinstance(cache, dict) and now - float(cache.get('at') or 0.0) < 120 and cache.get('linked') and ('headers' in cache):
            return dict(cache)
        runtime = self._bilibili_bot_runtime()
        result: dict[str, Any] = {'at': now, 'linked': runtime is not None, 'valid': None, 'refreshed': False, 'headers': {}}
        if runtime is None:
            self._bilibili_runtime_state = result
            return result
        try:
            result['headers'] = self._bilibili_bot_headers(runtime)
        except Exception as exc:
            logger.debug('[TogetherCompanion] 读取 B站 Bot 请求头失败: %s', _single_line(exc, 160))
        checker = getattr(runtime, 'check_cookie', None)
        refresher = getattr(runtime, 'refresh_cookie', None)
        try:
            checked = await asyncio.wait_for(checker(), timeout=15)
            valid = bool(checked[0]) if isinstance(checked, tuple) and checked else bool(checked)
        except Exception as exc:
            logger.debug('[TogetherCompanion] B站 Bot 登录状态检查失败: %s', _single_line(exc, 160))
            self._bilibili_runtime_state = result
            return result
        result['valid'] = valid
        if valid or not callable(refresher):
            self._bilibili_runtime_state = result
            return result
        try:
            refreshed = await asyncio.wait_for(refresher(), timeout=40)
            refresh_ok = bool(refreshed[0]) if isinstance(refreshed, tuple) and refreshed else bool(refreshed)
            result['refreshed'] = refresh_ok
            result['valid'] = refresh_ok
            if refresh_ok:
                logger.info('[TogetherCompanion] 已通过 B站 Bot 刷新登录 Cookie，将使用账号可用清晰度')
            else:
                logger.info('[TogetherCompanion] B站 Bot Cookie 刷新未成功，将使用未登录可用清晰度')
        except Exception as exc:
            logger.info('[TogetherCompanion] B站 Bot Cookie 刷新失败，将使用未登录可用清晰度: %s', _single_line(exc, 160))
        try:
            result['headers'] = self._bilibili_bot_headers(runtime)
        except Exception as exc:
            logger.debug('[TogetherCompanion] 读取刷新后的 B站 Bot 请求头失败: %s', _single_line(exc, 160))
        self._bilibili_runtime_state = result
        return result

    async def resolve_avatar_path(self, bot_id: str='') -> Path | None:
        requested = _single_line(bot_id, 32)
        identity = {} if requested else self._bot_identity()
        configured = requested or _single_line(self.bot_qq_id, 32)
        qq_id = configured if re.fullmatch('[1-9]\\d{4,14}', configured) else ''
        if not qq_id:
            candidate = _single_line(identity.get('qq_id'), 32)
            qq_id = candidate if re.fullmatch('[1-9]\\d{4,14}', candidate) else ''
        if qq_id:
            cached = self._avatar_cache_dir / f'qq-{qq_id}.jpg'
            try:
                fresh = cached.is_file() and time.time() - cached.stat().st_mtime < 7 * 86400
            except OSError:
                fresh = False
            if fresh or await self._download_qq_avatar(qq_id, cached):
                logger.debug('[TogetherCompanion] 使用 Bot QQ 头像: qq=%s', qq_id)
                return cached
        fallback_key = json.dumps({'self_ids': identity.get('self_ids') or [], 'ambiguous': bool(identity.get('ambiguous'))}, ensure_ascii=False, sort_keys=True)
        if fallback_key != getattr(self, '_avatar_fallback_log_key', ''):
            self._avatar_fallback_log_key = fallback_key
            logger.warning('[TogetherCompanion] Bot 头像暂不可用，使用内置默认头像: candidates=%s ambiguous=%s', identity.get('self_ids') or [], bool(identity.get('ambiguous')))
        fallback = Path(__file__).resolve().parent / 'web' / 'default-avatar.svg'
        return fallback if fallback.is_file() else None

    async def _download_qq_avatar(self, qq_id: str, target: Path) -> bool:

        def download() -> bytes:
            request = UrlRequest(f'https://q1.qlogo.cn/g?b=qq&nk={qq_id}&s=640', headers={'User-Agent': 'AstrBot-TogetherCompanion/1.0'})
            with urlopen(request, timeout=6) as response:
                content_type = str(response.headers.get('Content-Type') or '').lower()
                if 'image/' not in content_type:
                    return b''
                return response.read(5 * 1024 * 1024 + 1)
        try:
            data = await asyncio.to_thread(download)
            if not data or len(data) > 5 * 1024 * 1024:
                return False
            if not (data.startswith(b'\xff\xd8\xff') or data.startswith(b'\x89PNG\r\n\x1a\n') or (data.startswith(b'RIFF') and data[8:12] == b'WEBP')):
                return False
            temp = target.with_suffix('.tmp')
            await asyncio.to_thread(temp.write_bytes, data)
            await asyncio.to_thread(temp.replace, target)
            return True
        except Exception as exc:
            logger.debug('[TogetherCompanion] QQ 头像获取失败，使用本地头像: %s', exc)
            return target.is_file()

    def _novel_library_for(self, room: RoomSession) -> NovelLibrary:
        if room.native_v2 is None:
            return self.novel_library
        scope = room.native_v2.scope_key
        libraries = self._native_novel_libraries
        if scope not in libraries:
            libraries[scope] = NovelLibrary(self.data_dir / 'native_v2' / 'novel_library' / scope, max_upload_mb=self.novel_max_upload_mb)
        return libraries[scope]

    async def _send_novel_library(self, room: RoomSession) -> None:
        library = self._novel_library_for(room)
        books = await asyncio.to_thread(library.list_books, user_id=room.user_id) if self.novel_library_enabled else []
        await self.send_room_payload(room, {'type': 'novel_library', 'books': books})

    async def _send_novel_chapter(self, room: RoomSession, book_id: str, chapter_index: int, *, include_chapters: bool=True, navigation_sequence: int=0) -> None:
        library = self._novel_library_for(room)
        book = await asyncio.to_thread(library.get_book, book_id, user_id=room.user_id)
        if not book:
            await self.send_room_error(room, '书库中没有找到这本小说', code='novel_not_found')
            return
        chapter_count = int(book.get('chapter_count') or 0)
        if chapter_count <= 0:
            await self.send_room_error(room, '这本小说没有可阅读的章节', code='novel_empty')
            return
        resolved_index = max(0, min(int(chapter_index or 0), chapter_count - 1))
        chapter = await asyncio.to_thread(library.chapter, book_id, resolved_index)
        if not chapter:
            await self.send_room_error(room, '章节不存在', code='novel_chapter_not_found')
            return
        previous_book = str(room.novel_state.get('book_id') or '')
        progress = await asyncio.to_thread(library.progress, room.user_id, book_id) if previous_book != book_id else {}
        paragraph_index = int(progress.get('paragraph_index') or 0) if previous_book != book_id and int(progress.get('chapter_index') or 0) == resolved_index else int(room.novel_state.get('paragraph_index') or 0) if previous_book == book_id and int(room.novel_state.get('chapter_index') or 0) == resolved_index else 0
        paragraph_index = max(0, min(paragraph_index, max(0, len(chapter.get('paragraphs') or []) - 1)))
        room.novel_state = {'book_id': book_id, 'book_title': book.get('title') or '', 'chapter_index': resolved_index, 'chapter_title': chapter.get('title') or '', 'paragraph_index': paragraph_index, 'progress_sequence': int(room.novel_progress_sequence), 'updated_at': time.time()}
        payload = {'type': 'novel_chapter', 'book': book, 'chapter': chapter, 'state': dict(room.novel_state), 'navigation_sequence': max(0, int(navigation_sequence or 0))}
        if include_chapters:
            payload['chapters'] = await asyncio.to_thread(library.chapters, book_id)
        await self.send_room_payload(room, payload)
        await self._notify_shared_activity_updated(room, force=True)

    async def _refresh_novel_context(self, room: RoomSession, query: str, selected_text: str='') -> None:
        state = room.novel_state
        book_id = str(state.get('book_id') or '')
        if not book_id:
            room.novel_context = '用户尚未从书库选择小说。'
            return
        combined_query = '\n'.join((item for item in (selected_text[:2400], query) if item)).strip()
        library = self._novel_library_for(room)
        context = await asyncio.to_thread(library.reading_context, user_id=room.user_id, book_id=book_id, chapter_index=int(state.get('chapter_index') or 0), paragraph_index=int(state.get('paragraph_index') or 0), query=combined_query, top_k=self.novel_retrieval_top_k)
        room.novel_context = library.format_context(context)
        if selected_text:
            room.novel_context += f'\n用户本轮在阅读器中选中的原文：\n{selected_text[:2400]}'

    async def _resolve_media_for_room(self, room: RoomSession, page_url: str) -> None:
        await self.send_room_payload(room, {'type': 'media_resolving', 'message': '正在解析 B 站视频'})
        try:
            bilibili_state = await self._sync_bilibili_bot_cookie()
            runtime_headers = bilibili_state.get('headers') if isinstance(bilibili_state, dict) else None
            source = await asyncio.wait_for(self.media_resolver.resolve(page_url, room_id=room.room_id, request_headers=runtime_headers if isinstance(runtime_headers, dict) else None), timeout=40)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.info('[TogetherCompanion] 视频链接解析失败: %s', _single_line(exc, 300))
            await self.send_room_payload(room, {'type': 'media_error', 'message': f'视频解析失败：{_single_line(exc, 260)}'})
            return
        if (getattr(self, 'record_shared_experiences', True)
                and not room.shared_experience_finalized
                and (room.visual_digest_ring or room.watch_memory or room.watch_knowledge)):
            await self._record_shared_experience(room, final=False)
        self._drop_room_media(room.room_id)
        self.media_sources[source.token] = source
        room.reset_watch(media_token=source.token)
        room.media_state = {}
        room.append_watch_event('media', f'开始观看《{source.title}》', media_time=0.0)
        await self.send_room_payload(room, {'type': 'media_ready', 'url': f'/media/{source.token}', 'audio_url': f'/media/{source.token}/audio' if source.audio_source_url else '', 'playback_mode': source.playback_mode, 'title': source.title, 'duration': source.duration, 'quality': source.quality, 'quality_label': source.quality_label, 'source': 'bilibili', 'subtitle_available': bool(source.subtitle_cues), 'subtitle_language': source.subtitle_language})
        self._schedule_watch_knowledge(room, source)

    def resolve_media_source(self, token: str) -> ResolvedMedia | None:
        self._prune_media_sources()
        source = self.media_sources.get(str(token or ''))
        if source is None or source.expired:
            return None
        return source

    def _drop_room_media(self, room_id: str) -> None:
        stale = [token for token, source in self.media_sources.items() if source.room_id == room_id]
        for token in stale:
            self.media_sources.pop(token, None)

    def _prune_media_sources(self) -> int:
        stale = [token for token, source in self.media_sources.items() if source.expired]
        for token in stale:
            self.media_sources.pop(token, None)
        return len(stale)

    @staticmethod
    def _media_token_from_source(source: str) -> str:
        match = re.search('/media/([A-Za-z0-9_-]{24,80})(?:[?#]|$)', str(source or ''))
        return match.group(1) if match else ''

    @staticmethod
    def _format_media_clock(seconds: float) -> str:
        value = max(0, int(float(seconds or 0.0)))
        return f'{value // 60:02d}:{value % 60:02d}'

    def _start_media_resolution(self, room: RoomSession, operation: Any) -> None:
        room.cancel_media_resolution()
        self._spawn_task(room, operation, attr='media_resolution_task', label='视频解析任务失败', warn=True)

    async def _reply_to_novel_user(self, room: RoomSession, text: str, *, selected_text: str='') -> None:
        await self._refresh_novel_context(room, text, selected_text)
        await self._reply_to_user(room, text)

    def _append_user_watch_event(self, room: RoomSession, text: str, *, utterance_id: str='') -> None:
        if room.mode != 'watch':
            return
        room.append_watch_event('user', f'用户说：{_single_line(text, 400)}', media_time=float(room.media_state.get('current_time') or 0.0), metadata={'utterance_id': utterance_id} if utterance_id else None)

    @staticmethod
    def _normalize_media_state(value: Any) -> dict[str, Any]:
        raw = value if isinstance(value, dict) else {}
        try:
            current_time = max(0.0, float(raw.get('current_time') or 0.0))
        except (TypeError, ValueError):
            current_time = 0.0
        try:
            duration = max(0.0, float(raw.get('duration') or 0.0))
        except (TypeError, ValueError):
            duration = 0.0
        try:
            playback_rate = float(raw.get('playback_rate') or 1.0)
        except (TypeError, ValueError):
            playback_rate = 1.0
        return {'title': _single_line(raw.get('title'), 160), 'source': _single_line(raw.get('source'), 400), 'current_time': round(current_time, 2), 'duration': round(duration, 2), 'paused': bool(raw.get('paused', True)), 'playback_rate': max(0.25, min(playback_rate, 4.0))}

    @staticmethod
    def _format_media_state(state: dict[str, Any]) -> str:
        if not state:
            return ''
        current = int(float(state.get('current_time') or 0))
        duration = int(float(state.get('duration') or 0))
        status = '暂停' if state.get('paused') else '播放中'
        title = _single_line(state.get('title'), 120) or '未命名视频'
        return f'《{title}》，{status}，进度 {current // 60:02d}:{current % 60:02d}/{duration // 60:02d}:{duration % 60:02d}'

    def _resolved_media_for_room(self, room: RoomSession) -> ResolvedMedia | None:
        token = room.media_token or self._media_token_from_source(str(room.media_state.get('source') or ''))
        return self.resolve_media_source(token) if token else None

    @staticmethod
    def _watch_knowledge_material(source: ResolvedMedia) -> tuple[str, str]:
        fields: list[tuple[str, str]] = [('标题', _single_line(source.title, 240))]
        if source.uploader:
            fields.append(('UP 主', _single_line(source.uploader, 100)))
        if source.category:
            fields.append(('分区', _single_line(source.category, 80)))
        if source.tags:
            fields.append(('标签', '、'.join((_single_line(tag, 40) for tag in source.tags[:12] if tag))))
        if source.description:
            fields.append(('公开简介', _single_line(source.description, 1600)))
        available = [(label, value) for label, value in fields if value]
        material = '\n'.join((f'{label}：{value}' for label, value in available))
        return (material, '、'.join((label for label, _value in available)))

    def _schedule_watch_knowledge(self, room: RoomSession, source: ResolvedMedia) -> None:
        if not self.watch_prepare_knowledge or room.room_id not in self.rooms or self._room_chat_provider(room) is None:
            return
        material, _source_fields = self._watch_knowledge_material(source)
        if not material:
            return
        room.cancel_watch_knowledge()
        epoch = room.watch_epoch
        self._spawn_task(room, self._prepare_watch_knowledge(room, source, material, epoch=epoch), attr='watch_knowledge_task', label='观前无剧透背景整理失败')

    async def _prepare_watch_knowledge(self, room: RoomSession, source: ResolvedMedia, material: str, *, epoch: int) -> None:
        provider = self._room_chat_provider(room)
        if provider is None:
            return
        response = await self._tracked_text_chat(provider, task='together_watch_knowledge', prompt=f'来源材料（B站视频页公开信息）：\n{material}', system_prompt=WATCH_KNOWLEDGE_PROMPT, timeout=90)
        knowledge = self._clean_model_text(getattr(response, 'completion_text', ''))[:1000]
        if not knowledge or room.watch_epoch != epoch or room.media_token != source.token or (room.room_id not in self.rooms):
            return
        _material, source_fields = self._watch_knowledge_material(source)
        source_url = _single_line(source.page_url, 500)
        source_line = f"来源：B站视频页公开信息（{source_fields or '标题'}）"
        if source_url:
            source_line += f'，页面：{source_url}'
        room.watch_knowledge = f'{source_line}\n{knowledge}'[:1400]
        if source.title:
            room.record_visual_digest(f'正在观看《{_single_line(source.title, 60)}》', mode='watch', source='watch_knowledge')

    @staticmethod
    def _watch_event_source(kind: Any) -> str:
        return {'media': '媒体', 'loaded': '播放状态', 'play': '播放状态', 'pause': '播放状态', 'seeked': '播放状态', 'ended': '播放状态', 'subtitle': '字幕', 'observation': '画面观察', 'user': '用户反应', 'bot': 'Bot 反应'}.get(str(kind or ''), '房间事件')

    def _format_watch_context(self, room: RoomSession, *, include_subtitles: bool=True) -> str:
        parts: list[str] = []
        if room.watch_knowledge:
            parts.append(f'观前无剧透背景（来源已标注，只用于辅助理解，不代表共同看过）：\n{room.watch_knowledge[:1400]}')
        if room.watch_memory:
            parts.append(f'截至此前进度的观中剧情笔记（仅来自已播放内容）：\n{room.watch_memory[:1200]}')
        if include_subtitles:
            subtitle = self._subtitle_context(room)
            if subtitle:
                parts.append(f'当前进度附近已经播放过的字幕：\n{subtitle}')
        recent = [item for item in room.watch_events[-16:] if item.get('kind') not in {'user', 'bot', 'subtitle'}]
        if recent:
            lines = [f"[{self._format_media_clock(float(item.get('media_time') or 0.0))}][{self._watch_event_source(item.get('kind'))}] {_single_line(item.get('text'), 420)}" for item in recent if _single_line(item.get('text'), 420)]
            if lines:
                parts.append('最近的共同观影片段（方括号内为证据来源）：\n' + '\n'.join(lines)[-2000:])
        return '\n\n'.join(parts)

    @staticmethod
    def _watch_trigger_text(trigger: str) -> str:
        return {'opening': '影片刚开始，双方都还不知道后续；可以给即时开场反应，也可以先安静看。', 'scene_change': '浏览器检测到明显镜头变化；只有内容本身值得分享时才开口。', 'heartbeat': '这是持续观看中的一次普通观察，通常应保持安静，避免周期性报幕。', 'manual': '用户主动点击了查看当前画面，请基于画面自然回应，不要只做开口价值判断。', 'ending': '影片刚刚播放到结尾；结合共同看过的内容给一句自然的即时收尾反应。'}.get(trigger, '这是共同观看中的一次普通观察。')

    @staticmethod
    def _parse_watch_decision(value: str, *, trigger: str) -> dict[str, Any]:
        text = str(value or '').strip()
        if not text:
            return {'speak': False, 'utterance': '', 'observation': '', 'expires_in': 12.0}
        candidates = [text]
        if '{' in text and '}' in text:
            candidates.append(text[text.find('{'):text.rfind('}') + 1])
        data = None
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                data = parsed
                break
        if data is not None:
            utterance = _single_line(data.get('utterance'), 500)
            observation = _single_line(data.get('observation'), 600)
            speak_value = data.get('speak', False)
            if isinstance(speak_value, str):
                speak = speak_value.strip().lower() in {'true', '1', 'yes', 'speak', '开口', '是'}
            else:
                speak = bool(speak_value)
            expires_in = _clamp_float(data.get('expires_in', 12.0), 12.0, 4.0, 60.0)
            return {'speak': bool(speak and utterance), 'utterance': utterance, 'observation': observation, 'expires_in': expires_in}
        if text.upper() in {'[SILENT]', 'SILENT', '[保持安静]'}:
            return {'speak': False, 'utterance': '', 'observation': '', 'expires_in': 12.0}
        looks_internal = any((token in text.lower() for token in ('"speak"', '"utterance"', '"observation"')))
        if looks_internal:
            return {'speak': False, 'utterance': '', 'observation': '', 'expires_in': 12.0}
        utterance = _single_line(text, 500)
        return {'speak': bool(utterance and trigger in {'manual', 'opening', 'ending'}), 'utterance': utterance, 'observation': '', 'expires_in': 20.0}

    @staticmethod
    def _watch_comment_is_stale(room: RoomSession, *, epoch: int, captured_at: float, expires_in: float, trigger: str) -> bool:
        if room.watch_epoch != epoch or trigger == 'ending':
            return room.watch_epoch != epoch
        current = float(room.media_state.get('current_time') or 0.0)
        tolerance = max(expires_in, 30.0 if trigger == 'manual' else expires_in)
        return abs(current - captured_at) > tolerance

    async def _generate_watch_comment(self, room: RoomSession, frame: str, *, trigger: str, captured_at: float, scene_score: float=0.0) -> None:
        await self.send_room_payload(room, {'type': 'status', 'state': 'watching', 'text': '一起看着'})
        if trigger == 'opening':
            knowledge_task = room.watch_knowledge_task
            if isinstance(knowledge_task, asyncio.Task) and (not knowledge_task.done()):
                await asyncio.wait({knowledge_task}, timeout=3.0)
        epoch = room.watch_epoch
        subtitle = self._record_current_subtitle(room)
        since_spoken = max(0.0, captured_at - room.last_watch_spoken_media_time) if room.last_watch_spoken_media_time >= 0 else 9999.0
        prompt_parts = [self._watch_trigger_text(trigger), f'画面时间：{self._format_media_clock(captured_at)}。', f'距离你上次主动评论约 {int(since_spoken)} 秒。']
        if trigger == 'scene_change':
            prompt_parts.append(f'本地镜头变化强度约 {scene_score:.2f}，它只代表画面变化，不代表一定值得说话。')
        if subtitle:
            prompt_parts.append(f'已经播放到的近期字幕：\n{subtitle}')
        prompt_parts.append('请按系统约定返回内部 JSON。')
        try:
            raw = await self._generate_model_text(room, '\n\n'.join(prompt_parts), image_data_url=frame, watch_comment=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('[TogetherCompanion] 观影画面理解失败: provider=%s error=%s', self._provider_label(self._room_vision_provider(room)), _single_line(exc, 240))
            if not room.vision_error_notified:
                room.vision_error_notified = True
                error_text = str(exc or '').lower()
                unsupported = '未配置支持图片输入' in str(exc or '') or any((token in error_text for token in ('does not support vision', "doesn't support vision", 'do not support image', 'not support image', '不支持视觉', '不支持图片输入')))
                await self.send_room_payload(room, {'type': 'notice', 'level': 'warning', 'message': '没有可用的观影视觉模型，请在插件配置中选择视觉模型；聊天和播放不受影响。' if unsupported else '观影视觉模型本次调用失败，稍后会继续尝试；聊天和播放不受影响。'})
            return
        room.vision_error_notified = False
        decision = self._parse_watch_decision(raw, trigger=trigger)
        observation = _single_line(decision.get('observation'), 600)
        obs_digest = ''
        if observation and room.watch_epoch == epoch:
            room.append_watch_event('observation', observation, media_time=captured_at, metadata={'trigger': trigger})
            obs_digest = room.record_visual_digest(observation, mode='watch', source='watch_observation')
        force_memory = trigger == 'ending'
        if not force_memory:
            self._schedule_watch_memory_refresh(room)
        if not decision.get('speak') or self._watch_comment_is_stale(room, epoch=epoch, captured_at=captured_at, expires_in=float(decision.get('expires_in') or 12.0), trigger=trigger):
            if force_memory:
                self._schedule_watch_memory_refresh(room, force=True)
                self._schedule_shared_experience_record(room, delay_seconds=2.0)
            status = '已经看完' if trigger == 'ending' else '一起看着'
            await self.send_room_payload(room, {'type': 'status', 'state': 'watching', 'text': status})
            return
        comment = _single_line(decision.get('utterance'), 500)
        if not comment:
            return
        room.last_watch_spoken_media_time = captured_at
        room.append_turn('assistant', comment, history_turns=self.history_turns)
        room.append_watch_event('bot', f'Bot 说：{comment}', media_time=captured_at)
        if force_memory:
            self._schedule_watch_memory_refresh(room, force=True)
            self._schedule_shared_experience_record(room, delay_seconds=2.0)
        if room.native_v2 is None:
            await self._push_live_subtitle(comment, source='together_companion')
        watch_transcript = {
            'event_id': f'together-watch-{room.room_id}-{uuid.uuid4().hex[:10]}',
            'user_text': '',
            'visual_context_used': bool(obs_digest),
            'visual_digest': obs_digest,
        }
        await self._synthesize_and_send(room, comment, display_text=comment, display_source='watch_comment', native_transcript=watch_transcript)
        status = '已经看完' if trigger == 'ending' else '一起看着'
        await self.send_room_payload(room, {'type': 'status', 'state': 'watching', 'text': status})

    def _schedule_watch_memory_refresh(self, room: RoomSession, *, force: bool=False) -> None:
        if room.room_id not in self.rooms:
            return
        if room.watch_memory_refreshing:
            if not force:
                return
            room.cancel_watch_memory()
        pending = [item for item in room.watch_events if int(item.get('seq') or 0) > room.watch_memory_cursor]
        current = float(room.media_state.get('current_time') or 0.0)
        elapsed = abs(current - room.watch_memory_media_time)
        meaningful = [item for item in pending if item.get('kind') in {'observation', 'subtitle', 'user', 'bot', 'media', 'ended'}]
        if not force and (len(meaningful) < 6 or elapsed < self.watch_memory_refresh_seconds):
            return
        room.watch_memory_refreshing = True
        self._spawn_task(room, self._refresh_watch_memory(room, pending), attr='watch_memory_task', label='临时观影记忆整理失败', on_done=lambda: setattr(room, 'watch_memory_refreshing', False))

    async def _refresh_watch_memory(self, room: RoomSession, events: list[dict[str, Any]]) -> None:
        provider = self._room_chat_provider(room)
        if provider is None or not events:
            return
        epoch = room.watch_epoch
        cursor = max((int(item.get('seq') or 0) for item in events))
        lines = [f"[{self._format_media_clock(float(item.get('media_time') or 0.0))}][{self._watch_event_source(item.get('kind'))}] {_single_line(item.get('text'), 500)}" for item in events[-60:] if _single_line(item.get('text'), 500)]
        event_text = '\n'.join(lines)
        prompt = f"此前临时观影记忆：\n{room.watch_memory or '暂无'}\n\n新增观影事件：\n{event_text}\n\n当前播放进度：{self._format_media_clock(float(room.media_state.get('current_time') or 0.0))}"
        response = await self._tracked_text_chat(provider, task='together_watch_memory', prompt=prompt, system_prompt=WATCH_MEMORY_PROMPT, timeout=90)
        memory = self._clean_model_text(getattr(response, 'completion_text', ''))[:1200]
        if not memory or room.watch_epoch != epoch or room.room_id not in self.rooms:
            return
        room.watch_memory = memory
        room.watch_memory_cursor = cursor
        room.watch_memory_media_time = float(room.media_state.get('current_time') or 0.0)
