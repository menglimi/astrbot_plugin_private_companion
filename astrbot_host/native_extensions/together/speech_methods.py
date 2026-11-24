"""V2 together speech services."""
from __future__ import annotations
from .common import *

_VOICE_POLICY_UNSET = object()


class TogetherSpeechMethods:

    @staticmethod
    def _normalize_stt_mode(value: Any) -> str:
        mode = str(value or 'auto').strip().lower()
        return mode if mode in {'auto', 'browser', 'astrbot'} else 'auto'

    def _room_stt_provider(self, room: RoomSession | None) -> Any | None:
        provider_id = str(getattr(self, 'stt_provider_id', '') or '').strip()
        if provider_id:
            provider = self._get_provider_by_id(provider_id)
            return provider if callable(getattr(provider, 'get_text', None)) else None
        if room is None or room.native_v2 is None:
            return self._get_stt_provider()
        getter = getattr(self.context, 'get_using_stt_provider', None)
        try:
            provider = getter(self._room_umo(room)) if callable(getter) else None
        except Exception:
            return None
        return provider if callable(getattr(provider, 'get_text', None)) else None

    @staticmethod
    def _provider_tts_id(provider: Any) -> str:
        try:
            meta = provider.meta() if callable(getattr(provider, 'meta', None)) else None
        except Exception:
            meta = None
        config = getattr(provider, 'provider_config', None) or getattr(provider, 'config', None) or {}
        for value in (
            meta.get('id') if isinstance(meta, dict) else getattr(meta, 'id', ''),
            config.get('id') if isinstance(config, dict) else getattr(config, 'id', ''),
            config.get('provider_id') if isinstance(config, dict) else getattr(config, 'provider_id', ''),
            getattr(provider, 'provider_id', ''),
            getattr(provider, 'id', ''),
        ):
            cleaned = _single_line(value, 160)
            if cleaned:
                return cleaned
        return ''

    def _get_tts_provider_by_id(self, provider_id: str) -> Any | None:
        target = str(provider_id or '').strip()
        if not target:
            return None
        lister = getattr(self.context, 'get_all_tts_providers', None)
        if callable(lister):
            try:
                for provider in list(lister() or ()):
                    if not callable(getattr(provider, 'get_audio', None)):
                        continue
                    if self._provider_tts_id(provider) == target:
                        return provider
            except Exception as exc:
                logger.debug('[TogetherCompanion] 读取 TTS Provider 列表失败: id=%s error=%s', target[:80], exc)
        provider = self._get_provider_by_id(target)
        return provider if provider is not None and callable(getattr(provider, 'get_audio', None)) else None

    def _companion_voice_policy(self, room: RoomSession | None) -> Any | None:
        controller = getattr(self, 'controller', None)
        if controller is None:
            return None
        try:
            from astrbot_host.native_extensions import together_voice_policy
            from .native_context import current_room_context

            application = context = None
            if room is not None and room.native_v2 is not None:
                resolved = current_room_context(self, room)
                if resolved is None:
                    return None
                application, context = resolved.application, resolved.context
            else:
                user_id = _single_line(getattr(room, 'user_id', '') if room is not None else '', 80) or self._resolve_primary_user_id()
                if user_id:
                    from astrbot_host.mobile_together import MobileTogetherBridge
                    target = MobileTogetherBridge(controller)._target_context(user_id)
                    if target is not None:
                        _session, application, context = target
                if application is None or context is None:
                    session = getattr(controller, 'session', None) or getattr(controller, 'last_session', None)
                    if session is not None:
                        application = getattr(session, 'application', None)
                        context = getattr(getattr(session, 'profile', None), 'target', None)
            now = controller.now() if callable(getattr(controller, 'now', None)) else None
            return together_voice_policy(application, context, now=now)
        except Exception as exc:
            logger.debug('[TogetherCompanion] 读取陪伴语音配置失败，回退默认 TTS 链路: %s', _single_line(exc, 160))
            return None

    def _room_voice_config(self, room: RoomSession | None, *, spoken_text: str='', policy: Any=_VOICE_POLICY_UNSET) -> dict[str, Any]:
        if spoken_text:
            from companion_v2.conversation.voice_reply import detected_speech_language
            detected = detected_speech_language(spoken_text)
            if detected:
                return {'voice_language': detected}
        resolved_policy = self._companion_voice_policy(room) if policy is _VOICE_POLICY_UNSET else policy
        if resolved_policy is not None:
            return {'voice_language': resolved_policy.language}
        if room is not None and room.native_v2 is not None:
            return {'voice_language': room.native_v2.tts_language}
        return self._companion_realtime_voice_config()

    def _room_tts_provider(self, room: RoomSession | None, *, spoken_text: str='', policy: Any=_VOICE_POLICY_UNSET) -> Any | None:
        explicit_id = str(getattr(self, 'tts_provider_id', '') or '').strip()
        if explicit_id:
            explicit = self._get_tts_provider_by_id(explicit_id)
            if explicit is not None:
                return explicit
        resolved_policy = self._companion_voice_policy(room) if policy is _VOICE_POLICY_UNSET else policy
        if resolved_policy is not None:
            from companion_v2.conversation.voice_reply import detected_speech_language
            detected = detected_speech_language(spoken_text) if spoken_text else ''
            channel = (
                'group'
                if (room is not None and room.native_v2 is not None and room.native_v2.scope_kind == 'group')
                else 'private'
            )
            seen_ids: set[str] = set()
            for language in (detected, resolved_policy.language):
                if not language:
                    continue
                candidate_id = str(resolved_policy.provider_for_language(channel, language) or '').strip()
                if not candidate_id or candidate_id in seen_ids:
                    continue
                seen_ids.add(candidate_id)
                provider = self._get_tts_provider_by_id(candidate_id)
                if provider is not None:
                    return provider
        if room is None or room.native_v2 is None:
            return self._get_tts_provider()
        provider_id = room.native_v2.tts_provider_id
        if provider_id:
            provider = self._get_tts_provider_by_id(provider_id)
        else:
            getter = getattr(self.context, 'get_using_tts_provider', None)
            try:
                provider = getter(self._room_umo(room)) if callable(getter) else None
            except Exception:
                return None
        return provider if callable(getattr(provider, 'get_audio', None)) else None

    def _get_stt_provider(self) -> Any | None:
        provider_id = str(getattr(self, 'stt_provider_id', '') or '').strip()
        if provider_id:
            provider = self._get_provider_by_id(provider_id)
            return provider if callable(getattr(provider, 'get_text', None)) else None
        getter = getattr(self.context, 'get_using_stt_provider', None)
        try:
            provider = getter() if callable(getter) else None
        except Exception:
            provider = None
        return provider if callable(getattr(provider, 'get_text', None)) else None

    def _stt_unavailable_message(self) -> str:
        if str(getattr(self, 'stt_provider_id', '') or '').strip():
            return '选中的语音识别提供商当前不可用，请在陪伴小屋的「一起房间 → 语音链路」检查 STT 选择及提供商是否启用、加载成功。也可以先输入文字。'
        return '当前房间未接通语音识别。请在陪伴小屋的「一起房间 → 语音链路」选择 STT 提供商，或启用 AstrBot 当前会话的默认 STT。也可以先输入文字。'

    def _get_tts_provider(self) -> Any | None:
        provider = self._get_tts_provider_by_id(self.tts_provider_id)
        if provider is not None:
            return provider
        getter = getattr(self.context, 'get_using_tts_provider', None)
        try:
            provider = getter() if callable(getter) else None
            return provider if provider is not None and hasattr(provider, 'get_audio') else None
        except Exception:
            return None

    @staticmethod
    def _companion_tts_browser_language(config: Any) -> str:
        if not isinstance(config, dict) or config.get('available') is False:
            return DEFAULT_TTS_BROWSER_LANGUAGE
        browser_language = str(config.get('browser_language') or '').strip()
        if browser_language:
            return browser_language
        voice_language = str(config.get('voice_language') or '').strip().lower().replace('_', '-')
        if voice_language in {'ja', 'jp', 'japanese'} or voice_language.startswith('ja-'):
            return 'ja-JP'
        if voice_language in {'zh', 'cn', 'chinese'} or voice_language.startswith('zh-'):
            return 'zh-CN'
        if voice_language == 'en-gb':
            return 'en-GB'
        if voice_language in {'en', 'eng', 'english'} or voice_language.startswith('en-'):
            return 'en-US'
        return DEFAULT_TTS_BROWSER_LANGUAGE

    @staticmethod
    def _direct_voice_language(config: Any) -> tuple[str, str]:
        if not isinstance(config, dict) or config.get('available') is False:
            return ('', '')
        voice_language = str(config.get('voice_language') or '').strip().lower()
        browser_language = str(config.get('browser_language') or '').strip().lower()
        aliases = {'ja': ('ja-JP', '日语'), 'jp': ('ja-JP', '日语'), 'japanese': ('ja-JP', '日语'), 'ja-jp': ('ja-JP', '日语'), 'en': ('en-US', '英语'), 'eng': ('en-US', '英语'), 'english': ('en-US', '英语'), 'en-us': ('en-US', '英语'), 'en-gb': ('en-GB', '英语')}
        direct = aliases.get(voice_language)
        if direct:
            return direct
        if browser_language.startswith('ja'):
            return ('ja-JP', '日语')
        if browser_language.startswith('en'):
            return ('en-GB', '英语') if browser_language.startswith('en-gb') else ('en-US', '英语')
        return ('', '')

    def _call_direct_speech_prompt(self, room: RoomSession) -> str:
        if room.mode != 'call' or not room.call_active or not getattr(self, 'direct_multilingual_tts', True):
            return ''
        voice_config = self._room_voice_config(room)
        language_code, language_label = self._direct_voice_language(voice_config)
        if not language_code:
            return ''
        return CALL_DIRECT_SPEECH_PROMPT.format(language_code=language_code, language_label=language_label, fallback='系统会保留正文并按当前语音能力处理' if room.native_v2 is not None else '系统会自动回退到原有语种转换链路')

    async def _correct_stt_and_reply(self, room: RoomSession, text: str, *, source: str, alternatives: list[str] | None=None, image_data_url: str='', utterance_id: str='', speaker_label: str='') -> None:
        await self.send_room_payload(room, {'type': 'status', 'state': 'transcribing', 'text': '正在校对'})
        corrected = await self._correct_stt_transcript(room, text, source=source, alternatives=alternatives)
        self._append_user_watch_event(room, corrected, utterance_id=utterance_id)
        await self._reply_to_user(room, corrected, image_data_url=image_data_url, input_source=source, utterance_id=utterance_id, speaker_label=speaker_label)

    async def _correct_stt_transcript(self, room: RoomSession, text: str, *, source: str, alternatives: list[str] | None=None) -> str:
        original = str(text or '').strip()[:4000]
        if not original or not self.stt_correction_enabled:
            return original
        bot_name = self._room_bot_name(room)
        clean_alternatives = self._clean_stt_alternatives(original, alternatives)
        has_redaction = self._stt_contains_redaction_marker(original, clean_alternatives)
        if not has_redaction and (not self._stt_may_contain_bot_name(original, clean_alternatives, bot_name)):
            return original
        cache_key = (bot_name, original, tuple(clean_alternatives))
        cached = self._get_stt_correction_cache(cache_key)
        if cached is not None:
            return cached
        provider = self._room_chat_provider(room)
        if provider is None or not callable(getattr(provider, 'text_chat', None)):
            return original
        scene = {} if room.native_v2 is not None else self._companion_scene_cached(room.user_id)
        relationship = scene.get('relationship') if isinstance(scene.get('relationship'), dict) else {}
        user_name = _single_line(relationship.get('name'), 80)
        recent_lines = [f"{item.get('role', 'unknown')}: {_single_line(item.get('content'), 300)}" for item in room.history[-6:] if isinstance(item, dict) and _single_line(item.get('content'), 300)]
        request_data = {'bot_name': bot_name, 'user_name': user_name, 'source': source, 'room_mode': room.mode, 'call_active': bool(room.call_active), 'has_redaction_marker': has_redaction, 'transcript': original, 'alternatives': clean_alternatives, 'recent_dialogue': recent_lines}
        try:
            response = await self._tracked_text_chat(provider, task='together_stt_correction', prompt=json.dumps(request_data, ensure_ascii=False), contexts=[], system_prompt=STT_CORRECTION_PROMPT, timeout=18)
            corrected = self._parse_stt_correction(getattr(response, 'completion_text', ''), original)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug('[TogetherCompanion] STT 文本校对失败，保留原始转写: %s', exc)
            return original
        if corrected != original:
            logger.info('[TogetherCompanion] STT 已校对: source=%s raw=%s corrected=%s', source, _single_line(original, 160), _single_line(corrected, 160))
        self._set_stt_correction_cache(cache_key, corrected)
        return corrected

    @staticmethod
    def _clean_stt_alternatives(original: str, alternatives: list[str] | None) -> list[str]:
        clean: list[str] = []
        for candidate in list(alternatives or [])[:3]:
            candidate_text = str(candidate or '').strip()[:4000]
            if candidate_text and candidate_text != original and (candidate_text not in clean):
                clean.append(candidate_text)
        return clean

    @staticmethod
    def _stt_contains_redaction_marker(original: str, alternatives: list[str]) -> bool:
        return any((re.search('(?<![*＊])(?:\\*{2,8}|＊{2,8})(?![*＊])', str(candidate or '')) for candidate in (original, *alternatives)))

    @classmethod
    def _stt_may_contain_bot_name(cls, original: str, alternatives: list[str], bot_name: str) -> bool:
        name = str(bot_name or '').strip()
        if not name or name == 'Bot':
            return False
        candidates = [original, *alternatives]
        if any((name in candidate for candidate in candidates)):
            return True
        name_signature = cls._pinyin_signature(name)
        if not name_signature:
            return False
        name_length = len(re.sub('\\s+', '', name))
        has_pinyin = callable(lazy_pinyin)
        for candidate in candidates:
            compact = re.sub('[\\s，。！？、,.!?；：:（）()“”\\"\'「」【】]', '', candidate)
            if not compact:
                continue
            for size in range(max(1, name_length - 1), name_length + 2):
                if len(compact) < size:
                    continue
                for start in range(0, len(compact) - size + 1):
                    window = compact[start:start + size]
                    score = SequenceMatcher(None, name_signature, cls._pinyin_signature(window)).ratio()
                    if score >= 0.82:
                        return True
                    if not has_pinyin and size == name_length and (name_length >= 3) and (SequenceMatcher(None, name, window).ratio() >= 0.66):
                        return True
        return False

    def _get_stt_correction_cache(self, key: tuple[str, str, tuple[str, ...]]) -> str | None:
        cache = getattr(self, '_stt_correction_cache', None)
        if not isinstance(cache, dict):
            cache = {}
            self._stt_correction_cache = cache
        item = cache.get(key)
        if not isinstance(item, tuple) or len(item) != 2:
            return None
        if time.monotonic() - float(item[0] or 0.0) > 900:
            cache.pop(key, None)
            return None
        cache.pop(key, None)
        cache[key] = item
        logger.debug('[TogetherCompanion] STT 校对缓存命中')
        return str(item[1] or '')

    def _set_stt_correction_cache(self, key: tuple[str, str, tuple[str, ...]], value: str) -> None:
        cache = getattr(self, '_stt_correction_cache', None)
        if not isinstance(cache, dict):
            cache = {}
            self._stt_correction_cache = cache
        cache.pop(key, None)
        cache[key] = (time.monotonic(), str(value or ''))
        while len(cache) > 128:
            cache.pop(next(iter(cache)))

    @classmethod
    def _parse_stt_correction(cls, value: Any, original: str) -> str:
        output = cls._clean_model_text(value)
        candidates = [output]
        if '{' in output and '}' in output:
            candidates.append(output[output.find('{'):output.rfind('}') + 1])
        for candidate in candidates:
            try:
                data = json.loads(candidate)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            corrected = str(data.get('text') or '').strip()
            if not corrected:
                return original
            maximum = min(4000, max(len(original) * 2 + 40, 120))
            return corrected if len(corrected) <= maximum else original
        return original

    def _subtitle_context(self, room: RoomSession, *, lookback_seconds: float=18.0) -> str:
        source = self._resolved_media_for_room(room)
        if source is None or not source.subtitle_cues:
            return ''
        current = float(room.media_state.get('current_time') or 0.0)
        lower = max(0.0, current - max(2.0, lookback_seconds))
        cues = [cue for cue in source.subtitle_cues if float(cue.get('end') or 0.0) >= lower and float(cue.get('start') or 0.0) <= current + 0.25][-8:]
        lines = []
        for cue in cues:
            text = _single_line(cue.get('text'), 240)
            if text and (not lines or not lines[-1].endswith(text)):
                lines.append(f"[{self._format_media_clock(float(cue.get('start') or 0.0))}] {text}")
        return '\n'.join(lines)[-1200:]

    def _record_current_subtitle(self, room: RoomSession) -> str:
        subtitle = self._subtitle_context(room)
        if not subtitle:
            return ''
        compact = _single_line(subtitle, 600)
        last_subtitle = next((item for item in reversed(room.watch_events) if item.get('kind') == 'subtitle'), None)
        if not last_subtitle or _single_line(last_subtitle.get('text'), 600) != compact:
            room.append_watch_event('subtitle', compact, media_time=float(room.media_state.get('current_time') or 0.0))
        return subtitle

    @staticmethod
    def _split_tts_payload(value: Any) -> tuple[str, str]:
        """Split private-companion voice markup into speech and visible text."""
        raw = str(value or '').strip()
        if not raw:
            return ('', '')
        block_pattern = re.compile('<(?:pc[_-]?tts|t{2,}s)\\b[^>]*>(.*?)</(?:pc[_-]?tts|t{2,}s)\\s*>', flags=re.IGNORECASE | re.DOTALL)
        matches = list(block_pattern.finditer(raw))
        if not matches:
            cleaned = re.sub('</?(?:pc[_-]?tts|t{2,}s)\\b[^>]*>', '', raw, flags=re.IGNORECASE).strip()
            return (cleaned, cleaned)
        spoken = ' '.join((match.group(1).strip() for match in matches if match.group(1).strip()))
        visible = block_pattern.sub('', raw)
        visible = re.sub('</?(?:pc[_-]?tts|t{2,}s)\\b[^>]*>', '', visible, flags=re.IGNORECASE)
        visible = re.sub('\\s+', ' ', visible).strip()
        if not visible:
            visible = re.sub('\\[(?:[a-z][a-z0-9 _-]{0,30})\\]', '', spoken, flags=re.IGNORECASE)
            visible = re.sub('\\s+', ' ', visible).strip()
        return (spoken.strip(), visible[:4000])

    async def _push_live_subtitle(self, text: str, *, source: str) -> None:
        try:
            await self._invoke_extension(self._live_native_api(), 'push_subtitle', text, source=source)
        except Exception as exc:
            logger.debug('[TogetherCompanion] 直播字幕联动失败: %s', exc)

    async def _start_live_mouth_sync(self, room: RoomSession, audio_path: Path) -> None:
        try:
            await self._invoke_extension(self._live_native_api(), 'start_mouth_sync', str(audio_path), source=f'together:{room.room_id}')
        except Exception as exc:
            logger.debug('[TogetherCompanion] Live2D 嘴型联动失败: %s', exc)

    async def _stop_live_mouth_sync(self, room: RoomSession) -> None:
        try:
            await self._invoke_extension(self._live_native_api(), 'stop_mouth_sync', source=f'together:{room.room_id}')
        except Exception as exc:
            logger.debug('[TogetherCompanion] 停止 Live2D 嘴型联动失败: %s', exc)

    def _decode_audio_payload(self, payload: dict[str, Any]) -> tuple[bytes, str]:
        encoded = str(payload.get('data') or '')
        if not encoded or len(encoded) > 18 * 1024 * 1024:
            return (b'', '')
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError, TypeError):
            return (b'', '')
        if not decoded or len(decoded) > 12 * 1024 * 1024:
            return (b'', '')
        mime_type = _single_line(payload.get('mime'), 80).lower() or 'audio/webm'
        if not mime_type.startswith('audio/'):
            return (b'', '')
        return (decoded, mime_type)

    def _audio_temp_paths(self, mime_type: str) -> tuple[Path, Path]:
        extension = {'audio/webm': '.webm', 'audio/ogg': '.ogg', 'audio/mp4': '.m4a', 'audio/mpeg': '.mp3', 'audio/wav': '.wav', 'audio/x-wav': '.wav'}.get(mime_type.split(';', 1)[0], '.bin')
        stem = f'utterance_{uuid.uuid4().hex}'
        return (self.temp_dir / f'{stem}{extension}', self.temp_dir / f'{stem}.wav')

    async def _convert_audio_to_wav(self, source_path: Path, wav_path: Path) -> Path:
        if source_path.suffix.lower() == '.wav':
            return source_path
        ffmpeg = self._ffmpeg_path()
        if ffmpeg is None:
            if not getattr(self, '_ffmpeg_missing_warned', False):
                self._ffmpeg_missing_warned = True
                logger.warning('[TogetherCompanion] 未找到 FFmpeg，将把原始音频直接交给 STT Provider，识别可能失败')
            return source_path
        process = await asyncio.create_subprocess_exec(str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source_path), '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(wav_path), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if process.returncode is None:
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
            raise
        if process.returncode != 0 or not wav_path.is_file():
            detail = stderr.decode('utf-8', errors='replace')[-300:]
            raise RuntimeError(f'音频格式转换失败: {_single_line(detail, 300)}')
        return wav_path
