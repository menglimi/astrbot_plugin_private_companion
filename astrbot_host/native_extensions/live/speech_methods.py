"""V2 live speech services."""
from __future__ import annotations
from .common import *

class LiveSpeechMethods:

    async def _send_bili_live_tts_followup(self, session_id: str, text: str, *, push_subtitle: bool=True) -> None:
        started_at = time.perf_counter()
        try:
            record_chain = await self._build_bili_live_tts_chain(session_id, text, push_subtitle=push_subtitle)
            if not record_chain:
                return
            await self.context.send_message(session_id, MessageChain(record_chain))
            await self._start_bili_live_mouth_sync_for_chain(record_chain)
            logger.info('[B站直播] 已后台补发直播自动回应 TTS: elapsed=%.2fs session=%s', time.perf_counter() - started_at, session_id)
        except Exception as e:
            logger.warning('[B站直播] 后台补发直播自动回应 TTS 失败: %s', e)

    def _bili_live_auto_reply_sync_tts_subtitle(self) -> bool:
        return bool(self.config.get('bili_live_auto_reply_sync_tts_subtitle', True))

    async def _send_bili_live_synced_tts_reply(self, session_id: str, text: str) -> bool:
        payload = await self._build_bili_live_tts_payload(session_id, text, push_subtitle=False, schedule_local_playback=False)
        if not payload:
            return False
        visible_text = self._strip_tts_blocks_from_text(text)
        chain = list(payload.get('chain') or [])
        if visible_text:
            chain.append(Plain(visible_text))
        if not chain:
            return False
        await self._push_subtitle(str(payload.get('subtitle_text') or visible_text), source='bili_live')
        await self._start_bili_live_mouth_sync_for_chain(chain)
        self._schedule_bili_live_tts_local_playback(str(payload.get('audio_path') or ''))
        self._schedule_bili_live_tts_web_playback(str(payload.get('audio_path') or ''))
        await self.context.send_message(session_id, MessageChain(chain))
        return True

    async def _start_bili_live_mouth_sync_for_chain(self, chain: list[Any]) -> None:
        if not self._is_mouth_sync_enabled():
            return
        try:
            result = self._build_message_result_from_chain(chain)
            await self._start_mouth_sync_for_result(result)
        except Exception as e:
            logger.debug(f'[B站直播] 启动直播 TTS 嘴型联动失败: {e}')

    def _wrap_plain_chain_as_tts(self, chain: list[Any]) -> list[Any]:
        wrapped: list[Any] = []
        for component in chain:
            if isinstance(component, Plain):
                text = str(getattr(component, 'text', '') or '').strip()
                if text and '<tts' not in text.lower():
                    wrapped.append(Plain(f'<tts>{text}</tts>'))
                    continue
            wrapped.append(component)
        return wrapped

    async def _build_bili_live_tts_chain(self, session_id: str, text: str, *, push_subtitle: bool=True, schedule_local_playback: bool=True) -> list[Any]:
        payload = await self._build_bili_live_tts_payload(session_id, text, push_subtitle=push_subtitle, schedule_local_playback=schedule_local_playback)
        return list(payload.get('chain') or []) if payload else []

    async def _build_bili_live_tts_payload(self, session_id: str, text: str, *, push_subtitle: bool=True, schedule_local_playback: bool=True) -> dict[str, Any]:
        spoken = self._strip_tts_blocks_from_text(text)
        if not spoken:
            return {}
        try:
            tts_provider = self.context.get_using_tts_provider(session_id)
        except Exception as e:
            logger.warning('[B站直播] 直播自动回应 TTS 生成失败：未找到会话 TTS Provider session=%s err=%s', session_id, e)
            return {}
        if not tts_provider:
            logger.warning('[B站直播] 直播自动回应 TTS 生成失败：当前会话未配置 TTS Provider session=%s', session_id)
            return {}
        convert_started_at = time.perf_counter()
        spoken = await self._convert_bili_live_tts_spoken_text(session_id, spoken, tts_provider)
        spoken = self._sanitize_bili_live_tts_spoken_text(spoken, source_text=text)
        convert_elapsed = time.perf_counter() - convert_started_at
        if not spoken:
            return {}
        try:
            tts_started_at = time.perf_counter()
            audio_path = await tts_provider.get_audio(spoken)
            tts_elapsed = time.perf_counter() - tts_started_at
        except Exception as e:
            logger.warning('[B站直播] 直播自动回应 TTS 生成失败: %s', e)
            return {}
        if not audio_path:
            logger.warning('[B站直播] 直播自动回应 TTS 生成失败：Provider 未返回音频路径')
            return {}
        audio_path = str(audio_path)
        record_audio_path = self._prepare_bili_live_audio_for_record(audio_path)
        record = await self._bili_live_tts_record(session_id, record_audio_path)
        visible_text = self._strip_tts_blocks_from_text(text)
        subtitle_text = self._build_bili_live_tts_subtitle_text(visible_text=visible_text, spoken_text=spoken)
        if schedule_local_playback:
            self._schedule_bili_live_tts_local_playback(record_audio_path)
        self._schedule_bili_live_tts_web_playback(record_audio_path)
        if push_subtitle and (not self._companion_tts_live_subtitle_enabled()):
            await self._push_subtitle(subtitle_text, source='bili_live')
        logger.info('[B站直播] 已生成直播自动回应 TTS: convert=%.2fs provider=%.2fs path=%s text=%s', convert_elapsed, tts_elapsed, record_audio_path, spoken[:80])
        return {'chain': [record], 'spoken_text': spoken, 'subtitle_text': subtitle_text, 'audio_path': record_audio_path, 'source_audio_path': audio_path}

    async def _bili_live_tts_record(self, session_id: str, record_audio_path: str):
        """Build the outbound voice component for the live room's platform.

        Telegram rejects WAV voice notes and re-encodes every plain ``Record``
        to WAV inside AstrBot, so a Telegram live-room TTS uses the converted
        OGG/MP3/M4A path through ``TelegramVoiceRecord``.  Every other platform
        keeps the plain Record it always had.
        """
        platform_name = ""
        try:
            from astrbot.core.platform.message_session import MessageSession
            platform_id = MessageSession.from_str(session_id).platform_name
            platform = self.context.get_platform_inst(platform_id)
            if platform is not None:
                platform_name = str(platform.meta().name or "")
        except Exception:
            platform_name = ""
        if platform_name == "telegram":
            try:
                from companion_v2.adapters.astrbot.platform_sender import TelegramVoiceRecord
                from astrbot_host.telegram_media import telegram_voice_path

                converted = await telegram_voice_path(record_audio_path)
                if converted:
                    return TelegramVoiceRecord(file=converted, path=converted)
            except Exception:
                logger.warning('[B站直播] Telegram 语音转码失败，退回普通语音段', exc_info=True)
        try:
            return Record(file=record_audio_path, url=record_audio_path)
        except TypeError:
            try:
                return Record(file=record_audio_path)
            except TypeError:
                return Record.fromFileSystem(record_audio_path)

    def _sanitize_bili_live_tts_spoken_text(self, text: str, *, source_text: str='') -> str:
        cleaned = self._strip_tts_blocks_from_text(text)
        cleaned = re.sub('(?is)<[^>\\n]{1,80}>', '', cleaned)
        while True:
            stripped = re.sub('^\\s*(?:\\[[^\\]\\n]{1,40}\\]|\\u3010[^\\u3011\\n]{1,40}\\u3011)\\s*', '', cleaned, count=1)
            if stripped == cleaned:
                break
            cleaned = stripped
        cleaned = re.sub('(?<![\\u3400-\\u9fff\\u3005々〆ヶ])([\\u3400-\\u9fff\\u3005々〆ヶ]{2,12})\\uff08([\\u3040-\\u30ffー・\\s]{1,48})\\uff09', '\\2', cleaned)
        cleaned = re.sub('(?<![\\u3400-\\u9fff\\u3005々〆ヶ])([\\u3400-\\u9fff\\u3005々〆ヶ]{2,12})\\(([\\u3040-\\u30ffー・\\s]{1,48})\\)', '\\2', cleaned)
        if source_text and re.search('[\\u3040-\\u30ff]', cleaned):
            kana_matches = list(re.finditer('[\\u3040-\\u30ff]', cleaned))
            tail_start = kana_matches[-1].end() if kana_matches else 0
            tail = cleaned[tail_start:]
            first_tail_cjk = re.search('[\\u4e00-\\u9fff]', tail)
            if first_tail_cjk and (not re.search('[\\u3040-\\u30ff]', tail[first_tail_cjk.start():])):
                lead = tail[:first_tail_cjk.start()]
                if re.fullmatch('[\\s.。…!！?？,，、~～:：;；-]*', lead):
                    cleaned = cleaned[:tail_start + first_tail_cjk.start()]
        cleaned = re.sub('\\s+([。！？、，,.!?])', '\\1', cleaned)
        cleaned = re.sub('[ \\t]{2,}', ' ', cleaned)
        cleaned = re.sub('\\n{3,}', '\n\n', cleaned).strip()
        return cleaned or self._strip_tts_blocks_from_text(source_text)

    def _build_bili_live_tts_subtitle_text(self, *, visible_text: str, spoken_text: str) -> str:
        visible = self._strip_tts_blocks_from_text(visible_text)
        if not bool(self.config.get('subtitle_use_tts_spoken_text', False)):
            return visible
        spoken_display = self._prefer_subtitle_display_text(spoken_text, voice_context=True)
        if spoken_display and (not re.search('[\\u3040-\\u30ff]', spoken_display)):
            return spoken_display
        return visible

    def _bili_live_tts_web_playback_enabled(self) -> bool:
        return bool(self.config.get('bili_live_tts_web_playback_enabled', False))

    def _schedule_bili_live_tts_local_playback(self, audio_path: str) -> None:
        if not bool(self.config.get('bili_live_tts_local_playback_enabled', True)):
            return
        path = str(audio_path or '').strip()
        if not path:
            return
        task = asyncio.create_task(self._play_bili_live_tts_audio(path))
        self._mouth_sync_tasks.add(task)
        task.add_done_callback(self._mouth_sync_tasks.discard)

    def _schedule_bili_live_tts_web_playback(self, audio_path: str) -> None:
        if not self._bili_live_tts_web_playback_enabled():
            return
        path = str(audio_path or '').strip()
        if not path:
            return
        task = asyncio.create_task(self._push_tts_audio_to_overlay(path))
        self._mouth_sync_tasks.add(task)
        task.add_done_callback(self._mouth_sync_tasks.discard)

    async def _play_bili_live_tts_audio(self, audio_path: str) -> None:
        path = self._normalize_local_audio_path(audio_path)
        if not path:
            logger.debug('[B站直播] TTS 本机播放跳过：音频路径不可读 %s', audio_path)
            return
        try:
            await asyncio.to_thread(self._play_bili_live_tts_audio_sync, path)
        except Exception as e:
            logger.warning('[B站直播] TTS 本机播放失败: %s', e)

    def _play_bili_live_tts_audio_sync(self, audio_path: str) -> None:
        path = self._wait_for_bili_live_audio_file(audio_path)
        if not path:
            logger.warning('[B站直播] TTS 本机播放失败：音频文件不可读或为空 %s', audio_path)
            return
        playback_path, temporary_path = self._prepare_bili_live_audio_for_playback(path)
        try:
            if self._play_bili_live_tts_audio_with_ffplay(playback_path):
                return
            if os.name == 'nt' and self._play_bili_live_tts_audio_with_powershell(playback_path):
                return
            if os.name == 'nt' and Path(playback_path).suffix.lower() == '.wav':
                self._play_bili_live_tts_audio_with_winsound(playback_path)
                return
            logger.warning('[B站直播] TTS 本机播放失败：未找到可用播放器 path=%s', playback_path)
        finally:
            if temporary_path:
                try:
                    os.remove(temporary_path)
                except OSError:
                    pass

    def _wait_for_bili_live_audio_file(self, audio_path: str) -> str:
        path = str(audio_path or '').strip()
        if not path:
            return ''
        last_size = -1
        stable_count = 0
        deadline = time.time() + 2.5
        while time.time() < deadline:
            try:
                size = os.path.getsize(path)
            except OSError:
                time.sleep(0.05)
                continue
            if size <= 0:
                time.sleep(0.05)
                continue
            if size == last_size:
                stable_count += 1
                if stable_count >= 2:
                    return path
            else:
                stable_count = 0
                last_size = size
            time.sleep(0.08)
        return path if os.path.exists(path) and os.path.getsize(path) > 0 else ''

    def _prepare_bili_live_audio_for_record(self, audio_path: str) -> str:
        prepared_path, _ = self._prepare_bili_live_wav_audio(audio_path, purpose='发送/播放')
        return prepared_path

    def _prepare_bili_live_audio_for_playback(self, audio_path: str) -> tuple[str, str]:
        return self._prepare_bili_live_wav_audio(audio_path, purpose='本机播放')

    def _prepare_bili_live_wav_audio(self, audio_path: str, *, purpose: str) -> tuple[str, str]:
        path = str(audio_path or '').strip()
        if Path(path).suffix.lower() != '.wav':
            return (path, '')
        try:
            file_size = os.path.getsize(path)
            with wave.open(path, 'rb') as wav:
                channels = max(1, wav.getnchannels())
                sample_width = max(1, wav.getsampwidth())
                rate = max(1, wav.getframerate())
                frames = max(0, wav.getnframes())
                comptype = wav.getcomptype()
                max_possible_frames = max(1, file_size // max(1, channels * sample_width))
                raw = wav.readframes(max_possible_frames)
        except Exception as e:
            logger.debug('[B站直播] 检查 wav 播放头失败，直接使用原文件: %s', e)
            return (path, '')
        if comptype != 'NONE' or not raw:
            return (path, '')
        expected_bytes = frames * channels * sample_width
        actual_bytes = len(raw)
        header_suspicious = expected_bytes <= 0 or actual_bytes <= 0 or expected_bytes > max(file_size * 2, actual_bytes * 2) or (abs(expected_bytes - actual_bytes) > max(4096, actual_bytes // 20))
        if not header_suspicious:
            return (path, '')
        fixed_path = str(Path(path).with_name(f'{Path(path).stem}.playback-{uuid.uuid4().hex[:8]}.wav'))
        try:
            with wave.open(fixed_path, 'wb') as fixed:
                fixed.setnchannels(channels)
                fixed.setsampwidth(sample_width)
                fixed.setframerate(rate)
                fixed.writeframes(raw)
            logger.info('[B站直播] 已修复异常 wav 头用于%s: original=%s fixed=%s frames=%s bytes=%s', purpose, path, fixed_path, frames, actual_bytes)
            return (fixed_path, fixed_path)
        except Exception as e:
            logger.debug('[B站直播] 修复 wav 头失败，直接使用原文件: %s', e)
            try:
                if os.path.exists(fixed_path):
                    os.remove(fixed_path)
            except OSError:
                pass
            return (path, '')

    def _play_bili_live_tts_audio_with_ffplay(self, audio_path: str) -> bool:
        ffplay = shutil.which('ffplay')
        if not ffplay:
            return False
        try:
            result = subprocess.run([ffplay, '-nodisp', '-autoexit', '-loglevel', 'error', audio_path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore', check=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
        except Exception as e:
            logger.debug('[B站直播] ffplay 播放 TTS 失败: %s', e)
            return False
        if result.returncode == 0:
            logger.debug('[B站直播] 已使用 ffplay 本机播放 TTS: %s', audio_path)
            return True
        logger.debug('[B站直播] ffplay 播放 TTS 返回失败: %s', (result.stderr or '').strip()[:180])
        return False

    def _play_bili_live_tts_audio_with_powershell(self, audio_path: str) -> bool:
        powershell = shutil.which('powershell') or shutil.which('powershell.exe') or shutil.which('pwsh')
        if not powershell:
            return False
        script = "\n$ErrorActionPreference = 'Stop'\nAdd-Type -AssemblyName PresentationCore\n$path = $env:BILI_LIVE_TTS_AUDIO_PATH\nif (-not $path -or -not (Test-Path -LiteralPath $path)) { exit 2 }\n$player = New-Object System.Windows.Media.MediaPlayer\n$player.Open([System.Uri]::new($path))\n$deadline = [DateTime]::UtcNow.AddSeconds(5)\nwhile (-not $player.NaturalDuration.HasTimeSpan -and [DateTime]::UtcNow -lt $deadline) {\n    Start-Sleep -Milliseconds 50\n}\n$player.Volume = 1.0\n$player.Play()\nif ($player.NaturalDuration.HasTimeSpan) {\n    $duration = [int]$player.NaturalDuration.TimeSpan.TotalMilliseconds\n    $sleep = [Math]::Min([Math]::Max($duration + 300, 500), 120000)\n    Start-Sleep -Milliseconds $sleep\n} else {\n    Start-Sleep -Milliseconds 15000\n}\n$player.Stop()\n$player.Close()\n".strip()
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        env = dict(os.environ)
        env['BILI_LIVE_TTS_AUDIO_PATH'] = audio_path
        try:
            result = subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore', check=False, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except Exception as e:
            logger.debug('[B站直播] PowerShell MediaPlayer 播放 TTS 失败: %s', e)
            return False
        if result.returncode == 0:
            logger.debug('[B站直播] 已使用 PowerShell MediaPlayer 本机播放 TTS: %s', audio_path)
            return True
        logger.debug('[B站直播] PowerShell MediaPlayer 播放 TTS 返回失败: %s', (result.stderr or '').strip()[:180])
        return False

    def _play_bili_live_tts_audio_with_winsound(self, audio_path: str) -> bool:
        try:
            import winsound
            winsound.PlaySound(audio_path, winsound.SND_FILENAME | winsound.SND_NODEFAULT)
            logger.debug('[B站直播] 已使用 winsound 本机播放 TTS: %s', audio_path)
            return True
        except Exception as e:
            logger.debug('[B站直播] winsound 播放 TTS 失败: %s', e)
            return False

    async def _convert_bili_live_tts_spoken_text(self, session_id: str, text: str, tts_provider: Any) -> str:
        # TTS text preparation is owned by V2.  The provider receives the
        # visible reply after control markup has been stripped; no external
        # companion callback is required for a live response.
        return self._strip_tts_blocks_from_text(text) or text

    def _companion_tts_live_subtitle_enabled(self) -> bool:
        return bool(self.config.get('bili_live_auto_reply_sync_tts_subtitle', True))

    async def _after_bili_live_tts_audio_generated(self, audio_path: str, spoken_text: str, *, subtitle_text: str='') -> None:
        # Kept as a no-op for old in-memory callers.  Playback, mouth sync and
        # subtitles are scheduled directly by this native Live extension.
        return None

    def _mark_tts_modify_forced_voice(self, event: AstrMessageEvent, handlers: list[Any]) -> None:
        for handler in handlers:
            owner = getattr(getattr(handler, 'handler', None), '__self__', None)
            if owner is None:
                continue
            mark_llm = getattr(owner, '_mark_pending_llm_response_event', None)
            mark_voice = getattr(owner, '_mark_pending_forced_voice_event', None)
            if not callable(mark_llm) or not callable(mark_voice):
                continue
            try:
                mark_llm(event)
                mark_voice(event)
                logger.debug('[B站直播] 已为自动回应标记 TTS 强制语音。')
                return
            except Exception as e:
                logger.debug(f'[B站直播] 标记 TTS 强制语音失败: {e}')
                return

    def _strip_tts_blocks_from_plain_chain(self, chain: list[Any]) -> list[Any]:
        cleaned_chain: list[Any] = []
        for component in chain:
            if isinstance(component, Plain):
                text = self._strip_tts_blocks_from_text(getattr(component, 'text', '') or '')
                text = self._dedupe_repeated_plain_text(text)
                if text:
                    component.text = text
                    cleaned_chain.append(component)
                continue
            cleaned_chain.append(component)
        return cleaned_chain or chain

    def _strip_tts_blocks_from_text(self, text: str) -> str:
        cleaned = self._strip_bili_reply_control_markup(str(text or ''))
        cleaned = re.sub('\\s+', ' ', cleaned).strip()
        return cleaned

    def _ensure_visible_text_after_voice(self, chain: list[Any], reply_text: str) -> list[Any]:
        visible_text = self._dedupe_repeated_plain_text(self._strip_tts_blocks_from_text(reply_text))
        if not visible_text or not any((isinstance(component, Record) for component in chain)):
            return chain
        existing_plain = [self._dedupe_repeated_plain_text(self._strip_tts_blocks_from_text(getattr(component, 'text', '') or '')) for component in chain if isinstance(component, Plain)]
        if any((text == visible_text for text in existing_plain if text)):
            return chain
        return [*chain, Plain(visible_text)]
