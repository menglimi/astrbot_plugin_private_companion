"""Best-effort native TTS for an already sealed conversation part."""

import asyncio
import base64
import inspect
import io
import os
import re
import logging
from pathlib import Path
import shutil
import struct
import subprocess
import wave
from dataclasses import replace

from astrbot.core.utils.astrbot_path import get_astrbot_data_path, get_astrbot_temp_path

from companion_v2.conversation.voice_markup import SINGING_PROVIDER_CUE, strip_singing_provider_cue
from companion_v2.conversation.voice_reply import (
    VoiceReplyPolicy, requested_voice_language, with_voice_override,
)
from companion_v2.foundation.refs import ScopeKind

from .fish_audio import supports_singsing


logger = logging.getLogger(__name__)
MAX_RAW_AUDIO_BYTES = 64_000_000
MAX_VOICE_BYTES = 10_000_000
WAV_COMPRESS_THRESHOLD_BYTES = 2_000_000
MP3_ADAPTIVE_BITRATES = (128_000, 96_000, 64_000)
_MP3_SAMPLE_RATES = (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000)
DEFAULT_MIMO_TOOL_NAME = "mimo_tts_speak"


class VoiceAudioTooLargeError(ValueError):
    """Raised when synthesized audio exceeds the safe payload limit after compression."""

    def __init__(self, actual_bytes, max_bytes, *, duration_seconds=None, audio_format="", detail=""):
        self.actual_bytes = int(actual_bytes)
        self.max_bytes = int(max_bytes)
        self.duration_seconds = duration_seconds
        self.audio_format = str(audio_format or "")
        self.detail = str(detail or "")
        parts = [f"{self.actual_bytes} > {self.max_bytes} bytes"]
        if self.audio_format:
            parts.append(f"format={self.audio_format}")
        if self.duration_seconds is not None:
            parts.append(f"duration={self.duration_seconds:.1f}s")
        if self.detail:
            parts.append(self.detail)
        super().__init__("voice audio exceeds safe limit (" + ", ".join(parts) + ")")


def _mimo_plugin_from_handler(handler):
    """Find the plugin object behind a registered tool handler."""
    pending, visited = [handler], set()
    while pending:
        current = pending.pop(0)
        if current is None or id(current) in visited:
            continue
        visited.add(id(current))
        owner = getattr(current, "__self__", None)
        if owner is not None:
            pending.append(owner)
        pending.extend(list(getattr(current, "args", ()) or ()))
        wrapped = getattr(current, "func", None)
        if wrapped is not None and wrapped is not current:
            pending.append(wrapped)
        if (callable(getattr(current, "synthesize_text", None))
                or callable(getattr(current, "text_to_speech", None))):
            return current
    return None


def _find_mimo_voice_clone(context, scope):
    """Resolve the V1-style MiMo Voice Clone tool bridge without importing it."""
    getter = getattr(context, "get_llm_tool_manager", None)
    if not callable(getter):
        return None
    try:
        manager = getter()
    except Exception:
        return None
    if manager is None:
        return None
    tool = None
    for name in ("get_tool", "get_func"):
        method = getattr(manager, name, None)
        if not callable(method):
            continue
        try:
            tool = method(DEFAULT_MIMO_TOOL_NAME)
        except Exception:
            tool = None
        if tool is not None:
            break
    if tool is None:
        return None
    plugin = _mimo_plugin_from_handler(getattr(tool, "handler", None))
    if plugin is None:
        return None
    return _MimoVoiceCloneAdapter(plugin, scope)


class _MimoVoiceCloneAdapter:
    """Expose MiMo Voice Clone's public service as a TTS-provider-shaped object."""

    name = "MiMo TTS Voice Clone plugin"
    provider_type = "tts"
    model_name = "mimo-v2.5-tts-voiceclone"

    def __init__(self, plugin, scope):
        self.plugin = plugin
        self.scope = scope

    def get_model(self):
        return self.model_name

    def _event_user_id(self):
        scope = self.scope
        return str(getattr(scope, "session_id", "") or "").strip()

    def _event_session_id(self):
        scope = self.scope
        kind = "GroupMessage" if getattr(getattr(scope, "kind", None), "value", "") == "group" else "FriendMessage"
        return f"{getattr(scope, 'platform', '')}:{kind}:{getattr(scope, 'session_id', '')}"

    @staticmethod
    def _supported_kwargs(method, values):
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return values
        if any(parameter.kind == inspect.Parameter.VAR_KEYWORD
               for parameter in signature.parameters.values()):
            return values
        return {key: value for key, value in values.items() if key in signature.parameters}

    @staticmethod
    async def _await_result(result):
        return await result if inspect.isawaitable(result) else result

    @staticmethod
    def _audio_path_from_result(result):
        if isinstance(result, (list, tuple)):
            result = result[0] if result else ""
        if isinstance(result, os.PathLike):
            return os.fspath(result)
        if isinstance(result, str):
            return result.strip()
        for attribute in ("audio_path", "output_path", "path", "file", "url"):
            value = getattr(result, attribute, "")
            if isinstance(value, os.PathLike):
                return os.fspath(value)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    async def get_audio(self, text):
        synthesize = getattr(self.plugin, "synthesize_text", None)
        if callable(synthesize):
            kwargs = self._supported_kwargs(synthesize, {
                "voice_name": None,
                "context": "",
                "user_id": self._event_user_id(),
                "group_id": self._event_session_id(),
                "split": False,
            })
            result = await self._await_result(synthesize(str(text or ""), **kwargs))
            return self._audio_path_from_result(result)
        compatibility = getattr(self.plugin, "text_to_speech", None)
        if callable(compatibility):
            kwargs = self._supported_kwargs(compatibility, {
                "voice_name": "",
                "context": "",
                "target_umo": self._event_session_id(),
                "session_id": self._event_session_id(),
            })
            result = await self._await_result(compatibility(str(text or ""), **kwargs))
            return self._audio_path_from_result(result)
        return ""


def tts_provider_catalog(context):
    """Describe loaded TTS providers without exposing credentials."""
    try:
        providers = tuple(context.get_all_tts_providers())
    except Exception:
        return {"state": "unavailable", "providers": []}
    entries = {}
    for provider in providers:
        try:
            metadata = provider.meta()
            identifier = metadata.id
        except Exception:
            continue
        if type(identifier) is not str or not identifier.strip() or len(identifier) > 128:
            continue
        config = getattr(provider, "provider_config", None)
        config = config if isinstance(config, dict) else {}
        item = {"id": identifier.strip()}
        model = config.get("model")
        if not (type(model) is str and model.strip()):
            model = getattr(metadata, "model", None)
        if type(model) is str and model.strip() and len(model.strip()) <= 160:
            item["model"] = model.strip()
        for key in ("fishaudio-tts-character", "character", "voice", "voice_name", "azure_tts_voice",
                    "dashscope_tts_voice", "gemini_tts_voice_name", "elevenlabs-tts-voice-id"):
            value = config.get(key)
            if type(value) is str and value.strip() and len(value) <= 160:
                item["voice"] = value.strip()
                break
        entries[identifier.strip()] = item
    return {"state": "ready", "providers": [entries[key] for key in sorted(entries)]}


def _provider_with_overrides(provider, model="", voice=""):
    """Clone one loaded provider with per-turn model/voice fields when possible."""
    if not model and not voice:
        return provider
    config = getattr(provider, "provider_config", None)
    settings = getattr(provider, "provider_settings", None)
    if not isinstance(config, dict):
        return provider
    candidate = dict(config)
    if model:
        candidate["model"] = model
    if voice:
        keys = [key for key in candidate if key.endswith("-voice") or key.endswith("_voice")
                or key in ("voice", "voice_name", "character")]
        fish_key = "fishaudio-tts-character"
        if fish_key in candidate or any("fishaudio" in key.lower() for key in candidate):
            if re.fullmatch(r"[a-fA-F0-9]{32}", voice):
                candidate["fishaudio-tts-reference-id"] = voice
            else:
                candidate[fish_key] = voice
        elif keys:
            candidate[keys[0]] = voice
        else:
            return provider
    try:
        return type(provider)(candidate, dict(settings) if isinstance(settings, dict) else {})
    except Exception:
        logger.warning("[CompanionV2] TTS per-turn provider override unavailable type=%s",
                       type(provider).__name__)
        return provider


def _voice_alias_compact(value):
    return re.sub(r"[^a-z0-9.]+", "", str(value or "").casefold())


def _voice_aliases(value):
    compact = _voice_alias_compact(value)
    if not compact:
        return set()
    aliases = {compact}
    no_pro = compact.replace("pro", "")
    aliases.add(no_pro)
    if no_pro.startswith("s") and len(no_pro) > 1:
        aliases.add(no_pro[1:])
    if compact.startswith("s") and len(compact) > 1:
        aliases.add(compact[1:])
    return {alias for alias in aliases if len(alias) >= 3}


def admin_voice_override(policy, text, catalog):
    """Apply an explicit core-admin TTS model/voice/provider request to one turn."""
    if not isinstance(text, str) or not isinstance(catalog, dict):
        return None
    if not re.search(r"(?:语音|朗读|念|音色|声音|模型|tts|voice|model|speak|read)", text, re.I):
        return None
    if re.search(r"(?:不要|别|取消|停止|不用|avoid|don't|do not).{0,18}(?:语音|朗读|音色|声音|tts|voice|speak|read)", text, re.I):
        return None
    compact_text = _voice_alias_compact(text)
    selected = None
    selected_length = 0
    for item in catalog.get("providers") or []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        aliases = set()
        for value in (item.get("id"), item.get("model"), item.get("voice"),
                      item.get("provider_type"), item.get("label")):
            aliases.update(_voice_aliases(value))
        for alias in aliases:
            if len(alias) > selected_length and alias in compact_text:
                selected, selected_length = item, len(alias)
    model_hint = ""
    voice_hint = ""
    model_match = (re.search(r"(?:模型|model)\s*[:：=]?\s*([A-Za-z0-9._:-]+)", text, re.I)
                   or re.search(r"([A-Za-z0-9._:-]+)\s*(?:模型|model)", text, re.I))
    if model_match:
        model_hint = model_match.group(1).strip()
    voice_match = re.search(r"(?:音色|声音|角色|voice)\s*[:：=]?\s*([^\s，。；;]{1,80})", text, re.I)
    if voice_match:
        voice_hint = voice_match.group(1).strip()
    if selected is None and not model_hint and not voice_hint:
        return None
    if selected is not None and not re.search(
            r"(?:用|使用|换成|切换|指定|来|说|回复|朗读|念|use|switch|speak|read)", text, re.I):
        return None
    base = policy if isinstance(policy, VoiceReplyPolicy) else VoiceReplyPolicy()
    language = requested_voice_language(text)
    if not language:
        language_names = {
            "ja": r"(?:日语|日語|日文|日本语|日本語|japanese)",
            "en": r"(?:英语|英語|英文|english)",
            "zh": r"(?:中文|汉语|漢語|普通话|国语|國語|chinese)",
        }
        for code, names in language_names.items():
            if re.search(rf"(?:用|以|换成|切换(?:到|成)?)\s*(?:{names})", text, re.I):
                language = code
                break
    forced = replace(base, language=language or base.language,
        trigger_mode="always", probability=1.0, private_probability=-1.0,
        group_probability=-1.0, cooldown_seconds=0,
        private_cooldown_seconds=-1, group_cooldown_seconds=-1)
    return with_voice_override(forced,
        provider_id=selected.get("id", "") if selected else "",
        model=model_hint if selected is None else "",
        voice=voice_hint if selected is None else "")


def _finalize_streamed_wav(audio):
    """Replace a streaming WAV's placeholder lengths with received lengths."""
    if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return audio
    riff_size = struct.unpack_from("<I", audio, 4)[0]
    cursor = 12
    while cursor + 8 <= len(audio):
        size = struct.unpack_from("<I", audio, cursor + 4)[0]
        if (audio[cursor:cursor + 4] == b"data" and size in {0xFFFFFF00, 0xFFFFFFFF}
                and riff_size > len(audio) - 8):
            finalized = bytearray(audio)
            struct.pack_into("<I", finalized, 4, len(audio) - 8)
            struct.pack_into("<I", finalized, cursor + 4, len(audio) - cursor - 8)
            return bytes(finalized)
        cursor += 8 + size + (size & 1)
    return audio


def _is_wav(audio):
    return len(audio) >= 12 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"


def _is_mp3(audio):
    return (len(audio) >= 3 and audio[:3] == b"ID3") or (
        len(audio) >= 2 and audio[0] == 0xFF and (audio[1] & 0xE0) == 0xE0
    )


def _audio_format_label(audio):
    if _is_wav(audio):
        return "wav"
    if _is_mp3(audio):
        return "mp3"
    if audio[:4] == b"OggS":
        return "ogg"
    if audio[:4] == b"fLaC":
        return "flac"
    return "unknown"


def _wav_duration_seconds(audio):
    if not _is_wav(audio):
        return None
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            rate = wav.getframerate()
            return wav.getnframes() / rate if rate > 0 else None
    except Exception:
        return None


def _nearest_mp3_rate(rate):
    if rate in _MP3_SAMPLE_RATES:
        return rate
    return min(_MP3_SAMPLE_RATES, key=lambda candidate: abs(candidate - rate))


def _compress_wav_to_mp3_pyav(audio, bitrate):
    import av

    in_buffer = io.BytesIO(audio)
    out_buffer = io.BytesIO()
    in_container = av.open(in_buffer, mode="r")
    try:
        audio_streams = getattr(in_container.streams, "audio", ())
        if not audio_streams:
            return None
        in_stream = audio_streams[0]
        target_rate = _nearest_mp3_rate(int(getattr(in_stream, "rate", 44100) or 44100))
        channels = int(getattr(in_stream, "channels", 1) or 1)
        layout = "stereo" if channels >= 2 else "mono"
        out_container = av.open(out_buffer, mode="w", format="mp3")
        try:
            out_stream = out_container.add_stream("libmp3lame", rate=target_rate, layout=layout)
            out_stream.bit_rate = int(bitrate)
            resampler = av.AudioResampler(format=out_stream.format.name, layout=layout, rate=out_stream.rate)
            for frame in in_container.decode(audio=0):
                for resampled in resampler.resample(frame):
                    for packet in out_stream.encode(resampled):
                        out_container.mux(packet)
            for resampled in resampler.resample(None):
                for packet in out_stream.encode(resampled):
                    out_container.mux(packet)
            for packet in out_stream.encode(None):
                out_container.mux(packet)
        finally:
            out_container.close()
    finally:
        in_container.close()
    encoded = out_buffer.getvalue()
    return encoded if encoded else None


def _compress_wav_to_mp3_ffmpeg(audio, bitrate):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    result = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-f", "wav", "-i", "pipe:0",
            "-codec:a", "libmp3lame", "-b:a", f"{int(bitrate) // 1000}k",
            "-f", "mp3", "pipe:1",
        ],
        input=audio,
        capture_output=True,
        timeout=60,
        check=True,
    )
    return result.stdout if result.stdout else None


def _compress_wav_to_mp3(audio, bitrate=128_000):
    """Compress WAV bytes to MP3 (prefer PyAV libmp3lame, fallback to ffmpeg CLI)."""
    try:
        encoded = _compress_wav_to_mp3_pyav(audio, bitrate)
        if encoded:
            return encoded, "pyav"
    except Exception as error:
        logger.debug("[CompanionV2] PyAV WAV->MP3 压缩失败，准备回退 ffmpeg error_type=%s",
                     type(error).__name__)
    try:
        encoded = _compress_wav_to_mp3_ffmpeg(audio, bitrate)
        if encoded:
            return encoded, "ffmpeg"
    except Exception as error:
        logger.warning("[CompanionV2] ffmpeg WAV->MP3 压缩失败 error_type=%s", type(error).__name__)
    return None, ""


def _prepare_audio_bytes(audio, *, max_bytes=None, compress_threshold=None):
    """Finalize streamed WAV headers, auto-compress large WAVs to MP3, and enforce payload limits."""
    max_bytes = MAX_VOICE_BYTES if max_bytes is None else max_bytes
    compress_threshold = WAV_COMPRESS_THRESHOLD_BYTES if compress_threshold is None else compress_threshold
    audio = _finalize_streamed_wav(audio)
    fmt = _audio_format_label(audio)
    duration = _wav_duration_seconds(audio) if fmt == "wav" else None
    if fmt == "wav" and len(audio) > compress_threshold:
        raw_bytes = len(audio)
        smallest = None
        for bitrate in MP3_ADAPTIVE_BITRATES:
            encoded, backend = _compress_wav_to_mp3(audio, bitrate=bitrate)
            if not encoded:
                break
            if smallest is None or len(encoded) < len(smallest):
                smallest = encoded
            if len(encoded) <= max_bytes:
                logger.info(
                    "[CompanionV2] 语音 WAV 已自动压缩为 MP3 backend=%s raw_bytes=%d encoded_bytes=%d "
                    "bitrate=%dkbps duration=%s",
                    backend, raw_bytes, len(encoded), bitrate // 1000,
                    f"{duration:.1f}s" if duration is not None else "unknown",
                )
                return encoded
        if smallest is not None and len(smallest) > max_bytes:
            logger.warning(
                "[CompanionV2] 语音压缩后仍超过安全体积上限 actual_bytes=%d raw_bytes=%d max_bytes=%d "
                "format=mp3 duration=%s",
                len(smallest), raw_bytes, max_bytes,
                f"{duration:.1f}s" if duration is not None else "unknown",
            )
            raise VoiceAudioTooLargeError(
                len(smallest), max_bytes, duration_seconds=duration,
                audio_format="mp3", detail=f"raw_wav_bytes={raw_bytes}",
            )
        if raw_bytes <= max_bytes:
            return audio
        logger.warning(
            "[CompanionV2] 语音 WAV 超过安全体积上限且无法压缩 actual_bytes=%d max_bytes=%d "
            "format=wav duration=%s",
            raw_bytes, max_bytes, f"{duration:.1f}s" if duration is not None else "unknown",
        )
        raise VoiceAudioTooLargeError(
            raw_bytes, max_bytes, duration_seconds=duration,
            audio_format="wav", detail="compression_unavailable",
        )
    if len(audio) > max_bytes:
        logger.warning(
            "[CompanionV2] 语音音频超过安全体积上限 actual_bytes=%d max_bytes=%d format=%s duration=%s",
            len(audio), max_bytes, fmt, f"{duration:.1f}s" if duration is not None else "unknown",
        )
        raise VoiceAudioTooLargeError(
            len(audio), max_bytes, duration_seconds=duration, audio_format=fmt,
        )
    return audio


def _read_audio(path_value, *, allow_data=False):
    if not isinstance(path_value, (str, Path)) or not str(path_value):
        return None
    path = Path(path_value).resolve(strict=True)
    roots = [Path(get_astrbot_temp_path()).resolve()]
    if allow_data:
        roots.append(Path(get_astrbot_data_path()).resolve())
    if not any(path.is_relative_to(root) for root in roots) or not path.is_file():
        return None
    with path.open("rb") as stream:
        audio = stream.read(MAX_RAW_AUDIO_BYTES + 1)
    if not audio:
        return None
    if len(audio) > MAX_RAW_AUDIO_BYTES:
        logger.warning(
            "[CompanionV2] 语音原始文件超过读取上限 actual_bytes=>%d max_raw_bytes=%d path=%s",
            MAX_RAW_AUDIO_BYTES, MAX_RAW_AUDIO_BYTES, path.name,
        )
        raise VoiceAudioTooLargeError(
            len(audio), MAX_RAW_AUDIO_BYTES, audio_format=_audio_format_label(audio),
            detail="raw_file_exceeds_read_limit",
        )
    return _prepare_audio_bytes(audio)


def _configured_tts_provider(context, provider_id):
    provider = context.get_provider_by_id(provider_id)
    return provider if provider is not None and provider in context.get_all_tts_providers() else None


async def _session_tts_provider(context, umo):
    getter = getattr(context, "get_using_tts_provider_async", None)
    return await getter(umo) if callable(getter) else context.get_using_tts_provider(umo)


def singsing_config_origin(event):
    """AstrBot 4.29.4 selects the profile before applying session isolation."""
    umo = event.unified_msg_origin
    if not event.get_extra("_session_isolated", False):
        return umo
    original_id = getattr(getattr(event, "message_obj", None), "session_id", "")
    parts = umo.split(":", 2)
    if len(parts) != 3 or not isinstance(original_id, str) or not original_id:
        return ""
    return ":".join((*parts[:2], original_id))


async def _singsing_session_enabled(context, umo, config_umo):
    from astrbot.core.star.session_llm_manager import SessionServiceManager

    if not umo or not config_umo:
        return False
    settings = context.get_config(config_umo).get("provider_tts_settings", {})
    probability = settings.get("trigger_probability", 1)
    try:
        auto_tts_disabled = type(probability) is not bool and float(probability) == 0
    except (TypeError, ValueError, OverflowError):
        auto_tts_disabled = False
    return (settings.get("enable") is True and auto_tts_disabled
            and await SessionServiceManager.is_tts_enabled_for_session(umo) is True)


async def singsing_route_available(context, umo, provider_id, *, config_umo=None):
    async def check():
        if not await _singsing_session_enabled(context, umo, umo if config_umo is None else config_umo):
            return False
        provider = (_configured_tts_provider(context, provider_id) if provider_id
                    else await _session_tts_provider(context, umo))
        return supports_singsing(provider)

    try:
        return await asyncio.wait_for(check(), timeout=1)
    except Exception:
        return False


class NativeVoiceReply:
    def __init__(self, context):
        self.context = context

    async def _synthesize(self, provider, text, timeout_seconds, *, allow_data=False):
        if provider is None:
            return None
        async def synthesize():
            method = getattr(provider, "get_audio", None) or getattr(provider, "synthesize_text", None)
            if not callable(method):
                return None
            result = method(text.strip()) if inspect.iscoroutinefunction(method) else await asyncio.to_thread(
                method, text.strip())
            path = await result if inspect.isawaitable(result) else result
            if isinstance(path, (list, tuple)):
                path = path[0] if path else None
            return await asyncio.to_thread(_read_audio, path, allow_data=allow_data)
        return await asyncio.wait_for(synthesize(), timeout=timeout_seconds)

    async def render(self, task_context, text, *, provider_id="", timeout_seconds=120,
                     model_override="", voice_override="", singing=False, singing_umo="", singing_config_umo=None):
        scope = task_context.scope
        kind = "GroupMessage" if scope.kind is ScopeKind.GROUP else "FriendMessage"
        umo = singing_umo if singing and singing_umo else f"{scope.platform}:{kind}:{scope.session_id}"
        try:
            if singing:
                config_umo = umo if singing_config_umo is None else singing_config_umo
                if not await asyncio.wait_for(_singsing_session_enabled(self.context, umo, config_umo),
                                              timeout=timeout_seconds):
                    return None
            provider = None
            explicit_missing = False
            if provider_id:
                provider = _configured_tts_provider(self.context, provider_id)
                if provider is None:
                    explicit_missing = True
                    logger.warning("[CompanionV2] 指定 TTS Provider 未加载，回退默认语音服务 provider=%s", provider_id)
            if provider is None and not explicit_missing:
                provider = await _session_tts_provider(self.context, umo)
            if explicit_missing:
                return None
            if singing and not supports_singsing(provider):
                return None
            if provider is not None and (model_override or voice_override):
                provider = _provider_with_overrides(provider, model_override, voice_override)
            if singing:
                text = strip_singing_provider_cue(text).strip()
                if not text or not supports_singsing(provider):
                    return None
                text = SINGING_PROVIDER_CUE + text
            audio = await self._synthesize(provider, text, timeout_seconds)
            if audio is None and not singing:
                # V2 normally uses AstrBot's TTS provider.  The V1 MiMo Voice
                # Clone integration was a tool bridge, not a provider; keep it
                # as an explicit fallback when no host provider can synthesize.
                mimo = _find_mimo_voice_clone(self.context, scope)
                if mimo is not None:
                    logger.info("[CompanionV2] AstrBot TTS 不可用，尝试 MiMo Voice Clone 工具桥")
                    audio = await self._synthesize(mimo, text, timeout_seconds, allow_data=True)
            if audio is not None:
                return {"type": "record", "data": {"file": "base64://" + base64.b64encode(audio).decode("ascii")}}
        except VoiceAudioTooLargeError as error:
            logger.warning(
                "[CompanionV2] 语音合成结果超过体积上限，已放弃发送 provider=%s actual_bytes=%d "
                "max_bytes=%d format=%s duration=%s",
                provider_id or "default", error.actual_bytes, error.max_bytes, error.audio_format or "unknown",
                f"{error.duration_seconds:.1f}s" if error.duration_seconds is not None else "unknown",
            )
            raise
        except Exception as error:
            logger.warning("V2 voice synthesis unavailable: %s", type(error).__name__)
        return None
