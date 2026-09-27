# -*- coding: utf-8 -*-
"""TtsEnhancementPlaybackDeliveryMixin。

由 tools/split_mixin_domain.py 从 tts_enhancement.py 机械抽取（11 个方法 + 0 个模块级名字 + 0 个类级赋值 / 506 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 TtsEnhancementMixin）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import struct
import subprocess
import time
import urllib.request
from .helpers import _single_line
from .tts_enhancement_shared import FISH_AUDIO_S1_CUE_PATTERN, FISH_AUDIO_S2_CUE_PATTERN, TTS_TAG_PATTERN, logger
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from .tts_enhancement_shared import Plain
from .tts_enhancement_shared import Record



class TtsEnhancementPlaybackDeliveryMixin:
    """TtsEnhancementPlaybackDeliveryMixin（从 TtsEnhancementMixin 拆出）。"""


    def _play_tts_audio_file_windows_silent(
        self,
        path: str,
        *,
        volume: int = 35,
        fade_in_ms: int = 0,
    ) -> None:
        source_path = path
        if Path(path).suffix.lower() == ".wav":
            path = self._prepare_windows_wav_for_playback(path)
        try:
            if self._run_windows_media_player_script(path, use_wpf=True, volume=volume, fade_in_ms=fade_in_ms):
                return
            if self._run_windows_media_player_script(path, use_wpf=False, volume=volume, fade_in_ms=fade_in_ms):
                return
            raise RuntimeError("Windows 后台播放器均未能播放该音频")
        finally:
            source = Path(source_path)
            playback = Path(path)
            expected = source.with_name(f"{source.stem}.playback.wav")
            if playback != source and playback == expected:
                try:
                    playback.unlink(missing_ok=True)
                except Exception as exc:
                    logger.debug(
                        "清理 TTS 播放修复文件失败: %s",
                        _single_line(exc, 120),
                    )

    def _prepare_windows_wav_for_playback(self, path: str) -> str:
        source = Path(path)
        try:
            data = source.read_bytes()
            if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
                return path
            riff_size = struct.unpack_from("<I", data, 4)[0]
            pos = 12
            fmt_chunk: bytes | None = None
            data_start = 0
            data_size = 0
            while pos + 8 <= len(data):
                chunk_id = data[pos:pos + 4]
                chunk_size = struct.unpack_from("<I", data, pos + 4)[0]
                chunk_start = pos + 8
                remaining = max(0, len(data) - chunk_start)
                actual_size = min(chunk_size, remaining)
                if chunk_id == b"fmt ":
                    fmt_chunk = data[chunk_start:chunk_start + actual_size]
                elif chunk_id == b"data":
                    data_start = chunk_start
                    data_size = actual_size
                    break
                if chunk_size > remaining:
                    break
                pos = chunk_start + chunk_size + (chunk_size % 2)
            if not fmt_chunk or not data_start or data_size <= 0:
                return path
            if riff_size == len(data) - 8:
                declared_data_size = struct.unpack_from("<I", data, data_start - 4)[0]
                if declared_data_size == data_size:
                    return path
            fixed = source.with_name(f"{source.stem}.playback.wav")
            payload = data[data_start:data_start + data_size]
            riff_payload_size = 4 + (8 + len(fmt_chunk)) + (8 + len(payload))
            with fixed.open("wb") as f:
                f.write(b"RIFF")
                f.write(struct.pack("<I", riff_payload_size))
                f.write(b"WAVE")
                f.write(b"fmt ")
                f.write(struct.pack("<I", len(fmt_chunk)))
                f.write(fmt_chunk)
                f.write(b"data")
                f.write(struct.pack("<I", len(payload)))
                f.write(payload)
            return str(fixed)
        except Exception as exc:
            logger.debug("修正 WAV 播放头失败，使用原文件: %s", _single_line(exc, 120))
            return path

    def _run_windows_media_player_script(
        self,
        path: str,
        *,
        use_wpf: bool,
        volume: int = 35,
        fade_in_ms: int = 0,
    ) -> bool:
        volume = max(0, min(100, int(volume)))
        fade_in_ms = max(0, min(5000, int(fade_in_ms)))
        playback_env = os.environ.copy()
        playback_env["PRIVATE_COMPANION_TTS_AUDIO_PATH"] = str(Path(path).expanduser().resolve())
        playback_env["PRIVATE_COMPANION_TTS_VOLUME"] = str(volume)
        playback_env["PRIVATE_COMPANION_TTS_FADE_MS"] = str(fade_in_ms)
        if use_wpf:
            script = (
                "$p = [System.IO.Path]::GetFullPath($env:PRIVATE_COMPANION_TTS_AUDIO_PATH); "
                "$vol = [Math]::Max(0, [Math]::Min(100, [int]$env:PRIVATE_COMPANION_TTS_VOLUME)); "
                "$fade = [Math]::Max(0, [Math]::Min(5000, [int]$env:PRIVATE_COMPANION_TTS_FADE_MS)); "
                "Add-Type -AssemblyName PresentationCore; "
                "$player = New-Object System.Windows.Media.MediaPlayer; "
                "$player.Volume = $(if ($fade -gt 0) { 0 } else { $vol / 100.0 }); "
                "$player.Open([Uri]::new($p)); "
                "$deadline = (Get-Date).AddSeconds(10); "
                "while (-not $player.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 50 }; "
                "$duration = if ($player.NaturalDuration.HasTimeSpan) { $player.NaturalDuration.TimeSpan.TotalMilliseconds } else { 5000 }; "
                "$player.Play(); "
                "if ($fade -gt 0) { $stepMs = [Math]::Max(20, [int]($fade / 10)); for ($i = 1; $i -le 10; $i++) { Start-Sleep -Milliseconds $stepMs; $player.Volume = ($vol / 100.0) * ($i / 10.0) } }; "
                "Start-Sleep -Milliseconds ([Math]::Min([Math]::Max([int]$duration + 300, 800), 90000)); "
                "$player.Close()"
            )
            args = ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", script]
        else:
            script = (
                "$p = [System.IO.Path]::GetFullPath($env:PRIVATE_COMPANION_TTS_AUDIO_PATH); "
                "$vol = [Math]::Max(0, [Math]::Min(100, [int]$env:PRIVATE_COMPANION_TTS_VOLUME)); "
                "$fade = [Math]::Max(0, [Math]::Min(5000, [int]$env:PRIVATE_COMPANION_TTS_FADE_MS)); "
                "$player = New-Object -ComObject WMPlayer.OCX; "
                "$player.settings.volume = $(if ($fade -gt 0) { 0 } else { $vol }); "
                "$player.URL = $p; "
                "$player.controls.play(); "
                "if ($fade -gt 0) { $stepMs = [Math]::Max(20, [int]($fade / 10)); for ($i = 1; $i -le 10; $i++) { Start-Sleep -Milliseconds $stepMs; $player.settings.volume = [int]($vol * $i / 10) } }; "
                "$deadline = (Get-Date).AddSeconds(90); "
                "while ($player.playState -notin 1,8 -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 100 }; "
                "$player.close()"
            )
            args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=95,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=playback_env,
        )
        if result.returncode == 0:
            return True
        logger.debug(
            "Windows 静默播放方式失败: mode=%s code=%s err=%s",
            "wpf" if use_wpf else "wmp",
            result.returncode,
            _single_line(result.stderr or result.stdout, 160),
        )
        return False

    async def _post_tts_live_subtitle(self, text: str) -> None:
        if not bool(self._tts_setting("enable_tts_live_subtitle_sync", False)):
            return
        cleaned = _single_line(text, 500)
        if not cleaned:
            return
        url = str(self._tts_setting("tts_live_subtitle_url", "") or "").strip() or "http://127.0.0.1:18081/show"

        def _post() -> None:
            payload = json.dumps({"text": cleaned}, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=2.0) as response:
                response.read(256)

        try:
            await asyncio.to_thread(_post)
            logger.info("已同步 TTS 文本到直播打字机字幕: %s", _single_line(cleaned, 80))
        except Exception as exc:
            logger.debug("TTS 直播字幕同步失败: %s", _single_line(exc, 120))

    async def _after_tts_audio_generated(
        self,
        audio_path: str,
        spoken_text: str,
        *,
        source: str = "",
        subtitle_text: str = "",
        allow_local_playback: bool = True,
    ) -> None:
        is_live_reply = source == "bili_live_auto_reply"
        visible_text = subtitle_text or spoken_text
        subtitle_task = (
            self._create_tts_background_task(
                self._post_tts_live_subtitle(visible_text),
                label="tts_live_subtitle",
            )
            if is_live_reply
            else None
        )
        local_playback_enabled = bool(self._tts_setting("enable_tts_local_playback", False))
        live_only = bool(self._tts_setting("enable_tts_local_playback_live_only", False))
        should_play_local = (
            allow_local_playback
            and local_playback_enabled
            and (is_live_reply or not live_only)
        )
        if should_play_local:
            interval = max(0.0, float(self._tts_setting("tts_local_playback_min_interval_seconds", 0.0) or 0.0))
            now = time.time()
            retry_after = float(getattr(self, "_tts_local_playback_retry_after", 0.0) or 0.0)
            if retry_after > now:
                logger.debug(
                    "TTS 本机播放处于失败退避: remain=%.1fs failures=%s",
                    retry_after - now,
                    int(getattr(self, "_tts_local_playback_failures", 0) or 0),
                )
                if subtitle_task is not None:
                    await subtitle_task
                return
            if interval <= 0 or now - float(getattr(self, "_tts_local_playback_last_at", 0.0) or 0.0) >= interval:
                self._tts_local_playback_last_at = now
                try:
                    await asyncio.to_thread(self._open_tts_audio_file_local, audio_path)
                    self._tts_local_playback_failures = 0
                    self._tts_local_playback_retry_after = 0.0
                    logger.info(
                        "已触发 TTS 本机播放: source=%s live_only=%s path=%s",
                        source or "unknown",
                        live_only,
                        _single_line(audio_path, 160),
                    )
                except Exception as exc:
                    failures = int(getattr(self, "_tts_local_playback_failures", 0) or 0) + 1
                    retry_seconds = min(1800.0, 30.0 * (2 ** min(6, failures - 1)))
                    self._tts_local_playback_failures = failures
                    self._tts_local_playback_retry_after = now + retry_seconds
                    logger.warning(
                        "TTS 本机播放失败,已进入退避: failures=%s retry=%.0fs error=%s",
                        failures,
                        retry_seconds,
                        _single_line(exc, 120),
                    )
        if subtitle_task is not None:
            await subtitle_task

    def _tts_visible_fallback_text(
        self,
        text: str,
        fallback_plain: str = "",
        *,
        event: Any = None,
    ) -> str:
        normalized = self._normalize_tts_tags(str(text or ""))
        visible = re.sub(r"<tts\b[^>]*>.*?</tts>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
        visible = re.sub(TTS_TAG_PATTERN, "", visible).strip()
        if visible:
            return self._sanitize_tts_visible_text(
                visible,
                max_chars=self._tts_complete_text_limit(visible, 800),
            )
        fallback = str(fallback_plain or "").strip()
        if fallback:
            return self._sanitize_tts_visible_text(
                fallback,
                max_chars=self._tts_complete_text_limit(fallback, 800),
            )
        if self._tts_voice_language_for_event(event) == "zh":
            visible_zh = re.sub(TTS_TAG_PATTERN, "", normalized).strip()
            return self._sanitize_tts_visible_text(
                visible_zh,
                max_chars=self._tts_complete_text_limit(visible_zh, 800),
            )
        return ""

    def _tts_plain_markup_fallback_text(self, text: Any) -> str:
        """Demote a failed TTS structure to its original visible plain text."""
        normalized = self._normalize_tts_tags(str(text or ""))
        plain = self._strip_any_tts_markup(normalized)
        return self._sanitize_tts_visible_text(
            plain,
            max_chars=self._tts_complete_text_limit(plain, 800),
        )

    def _enforce_full_tts_scope_markup(
        self,
        text: str,
        *,
        source_text: str = "",
        event: Any = None,
        prefer_authored_voice: bool = False,
    ) -> tuple[str, str]:
        """Turn any tagged full-scope reply into one structurally complete voice block."""
        normalized = self._normalize_tts_tags(str(text or ""))
        if self._tts_setting("tts_conversion_scope", "partial") != "full":
            return normalized, ""
        if not re.search(r"<tts\b[^>]*>.*?</tts>", normalized, flags=re.IGNORECASE | re.DOTALL):
            return normalized, ""

        voice_lang = self._tts_voice_language_for_event(event)
        delivery_mode = self._tts_setting("tts_delivery_mode", "voice_and_text")
        foreign_text_mode = self._tts_setting("tts_foreign_text_mode", "translation")
        complete_limit = self._tts_complete_text_limit(
            source_text or normalized,
            1600,
        )
        full_source = self._sanitize_tts_visible_text(source_text, max_chars=complete_limit)
        if (
            prefer_authored_voice
            and voice_lang == "zh"
            and full_source
        ):
            authored_matches = list(
                re.finditer(
                    r"<tts\b[^>]*>.*?</tts>",
                    normalized,
                    flags=re.IGNORECASE | re.DOTALL,
                )
            )
            if (
                len(authored_matches) == 1
                and not normalized[: authored_matches[0].start()].strip()
            ):
                authored_units = self._tts_full_scope_content_units(
                    authored_matches[0].group(0)
                )
                source_units = self._tts_full_scope_content_units(full_source)
                authored_block = authored_matches[0].group(0)
                authored_content = self._tts_full_scope_content_text(authored_block)
                source_content = self._tts_full_scope_content_text(full_source)
                has_control_cue = bool(
                    FISH_AUDIO_S2_CUE_PATTERN.search(authored_block)
                    or FISH_AUDIO_S1_CUE_PATTERN.search(authored_block)
                )
                compressed_coverage = (
                    authored_units >= 6
                    and source_units > authored_units
                    and has_control_cue
                    and SequenceMatcher(
                        None,
                        source_content,
                        authored_content,
                        autojunk=False,
                    ).ratio()
                    >= 0.7
                )
                if (
                    authored_units
                    and source_units
                    and (
                        authored_units >= max(6, int(source_units * 0.7))
                        or compressed_coverage
                    )
                ):
                    logger.info(
                        "TTS全量后处理保留模型完整语音稿: spoken_units=%s source_units=%s",
                        authored_units,
                        source_units,
                    )
                    return normalized, ""
        if (
            not full_source
            and voice_lang != "zh"
            and delivery_mode == "voice_and_text"
            and foreign_text_mode in {"translation", "bilingual"}
        ):
            matches = list(
                re.finditer(
                    r"<tts\b[^>]*>.*?</tts>",
                    normalized,
                    flags=re.IGNORECASE | re.DOTALL,
                )
            )
            if len(matches) == 1 and not normalized[: matches[0].start()].strip():
                spoken = self._normalize_tts_spoken_text(
                    matches[0].group(0),
                    provider_kind="generic",
                )
                visible_after = self._sanitize_tts_visible_text(
                    normalized[matches[0].end() :],
                    max_chars=self._tts_complete_text_limit(normalized, 1600),
                )
                spoken_units = len(
                    re.findall(
                        r"[\u3040-\u30ff\u31f0-\u31ff\u4e00-\u9fffA-Za-z0-9]",
                        spoken,
                    )
                )
                visible_units = len(
                    re.findall(
                        r"[\u4e00-\u9fffA-Za-z0-9]",
                        visible_after,
                    )
                )
                # One complete foreign block followed by Chinese is the canonical
                # voice-and-translation form, so keep the authored spoken text.
                if (
                    spoken
                    and visible_after
                    and visible_units <= max(8, spoken_units * 2)
                    and not self._tts_text_needs_language_conversion(
                        spoken,
                        provider_kind="generic",
                        event=event,
                    )
                    and self._tts_visible_text_is_allowed_after_voice(visible_after)
                ):
                    logger.info(
                        "TTS全量快速标签已保留主模型外语块,中文仅作可见译文: voice=%s visible=%s",
                        _single_line(spoken, 80),
                        _single_line(visible_after, 80),
                    )
                    return normalized, ""
            # A noncanonical full reply may contain leading text, multiple blocks, or
            # substantially more visible content than the foreign block can cover.
            # Rebuild those cases from the complete visible source.
            visible = re.sub(
                r"<tts\b[^>]*>.*?</tts>",
                "",
                normalized,
                flags=re.IGNORECASE | re.DOTALL,
            )
            full_source = self._sanitize_tts_visible_text(
                visible,
                max_chars=self._tts_complete_text_limit(visible, 1600),
            )
        if not full_source:
            full_source = self._sanitize_tts_visible_text(
                self._strip_any_tts_markup(normalized),
                max_chars=self._tts_complete_text_limit(normalized, 1600),
            )
        if not full_source:
            return normalized, ""
        return f"<tts>{full_source}</tts>", full_source

    @staticmethod
    def _tts_full_scope_content_text(text: Any) -> str:
        """Return readable full-scope text after removing markup and cues."""
        source = str(text or "")
        source = re.sub(
            r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>",
            "",
            source,
            flags=re.IGNORECASE,
        )
        source = FISH_AUDIO_S2_CUE_PATTERN.sub("", source)
        source = FISH_AUDIO_S1_CUE_PATTERN.sub("", source)
        return source

    @classmethod
    def _tts_full_scope_content_units(cls, text: Any) -> int:
        """Count readable content units of a full-scope spoken draft."""
        source = cls._tts_full_scope_content_text(text)
        return len(
            re.findall(
                r"[\u3040-\u30ff\u31f0-\u31ff\u4e00-\u9fffA-Za-z0-9]",
                source,
            )
        )

    async def _finalize_tts_delivery_chain(
        self,
        output: list[Any],
        *,
        event: Any,
        provider_kind: str,
        fallback_plain: str,
        successful_spoken: list[str],
        suppress_visible: bool,
    ) -> list[Any]:
        records = [comp for comp in output if isinstance(comp, Record)]
        if not records:
            return output
        if suppress_visible:
            return records
        if self._tts_setting("tts_delivery_mode", "voice_and_text") == "voice_only":
            if self._tts_setting("tts_conversion_scope", "partial") == "full":
                return records
            remaining_text = "\n".join(
                str(getattr(comp, "text", "") or "").strip()
                for comp in output
                if isinstance(comp, Plain) and str(getattr(comp, "text", "") or "").strip()
            ).strip()
            remaining_plain = self._mark_tts_visible_plain(remaining_text, max_chars=1400)
            return records + ([remaining_plain] if remaining_plain is not None else [])
        plain_text = "\n".join(
            str(getattr(comp, "text", "") or "").strip()
            for comp in output
            if isinstance(comp, Plain) and str(getattr(comp, "text", "") or "").strip()
        ).strip()
        spoken_text = "\n".join(item for item in successful_spoken if item).strip()
        voice_lang = self._tts_voice_language_for_event(event)
        if voice_lang == "zh":
            visible_source = fallback_plain or plain_text or spoken_text
            visible_text = self._sanitize_tts_visible_text(
                visible_source,
                max_chars=self._tts_complete_text_limit(visible_source, 1200),
            )
        else:
            foreign_mode = self._tts_setting("tts_foreign_text_mode", "translation")
            foreign_visible_requested = self._event_explicitly_requests_foreign_visible_text(
                event,
                voice_language=voice_lang,
            )
            translated_text = ""
            if fallback_plain and self._tts_visible_text_is_allowed_after_voice(fallback_plain):
                translated_text = self._sanitize_tts_visible_text(fallback_plain, max_chars=1200)
            elif self._tts_visible_text_is_allowed_after_voice(plain_text):
                translated_text = self._sanitize_tts_visible_text(plain_text, max_chars=1200)
            if not translated_text and foreign_mode in {"translation", "bilingual"} and successful_spoken:
                translations = [
                    await self._translate_tts_spoken_to_chinese(item, event, provider_kind=provider_kind)
                    for item in successful_spoken
                ]
                translated_text = "\n".join(item for item in translations if item).strip()
            if foreign_visible_requested or foreign_mode == "original":
                visible_text = spoken_text
            elif foreign_mode == "bilingual":
                visible_text = "\n".join(item for item in (spoken_text, translated_text) if item).strip()
            else:
                visible_text = translated_text
        visible_plain = self._mark_tts_visible_plain(
            visible_text,
            max_chars=self._tts_complete_text_limit(visible_text, 1400),
        )
        return records + ([visible_plain] if visible_plain is not None else [])
