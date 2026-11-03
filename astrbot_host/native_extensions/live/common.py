"""
AstrBot 插件：我会直播圈米养你
将直播弹幕、Live2D 演出、字幕和嘴型联动组织成一套直播陪伴体验。
"""
import asyncio
import base64
import copy
from collections import deque
import datetime
import importlib
import inspect
import json
import math
import os
import platform
import re
import shutil
import subprocess
import wave
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from astrbot.api.star import Context
from astrbot.api import llm_tool, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api import logger
from astrbot.api.message_components import Plain, Record
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import AssistantMessageSegment
from astrbot_host.astrbot_compat import MainAgentBuildConfig, build_main_agent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.star.star_handler import EventType, star_handlers_registry
from .vts_client import VTSClient, VTSClientError, VTSConnectionError, VTSRealtimeClient, VTSResponseError, VTSTimeoutError
from .vts_discovery import auto_discover, get_install_info
from .bilibili_live import BilibiliBlivedmClient, BilibiliLaplaceClient, BilibiliLiveClient, BilibiliLiveArea, BilibiliOpenLiveClient, LiveDanmakuEvent, fetch_bilibili_live_areas, probe_bilibili_live_room
from .l2d_mixin import Live2DMixin
from .mouth_sync_mixin import MouthSyncMixin
from .soullink_mixin import SoullinkMixin
from .soullink_runtime import SoullinkRuntimeBridge
from .subtitle_mixin import SubtitleMixin
from .twitch_mixin import TwitchMixin
from .vts_parameter_scheduler import VTSParameterScheduler
DEFAULT_HOST = 'localhost'
DEFAULT_PORT = 8001
KV_KEY_TOKEN = 'vts_auth_token'
KV_KEY_BILI_REPLY_SESSION = 'bili_live_reply_session'
_active_live_stream_companion: Any | None = None

# Old development builds exposed the live relationship/state options with a
# ``private_companion_`` prefix.  Keep the aliases in one place so an existing
# installation can be read once while the public V2 page only exposes native
# Live settings.
from companion_v2.legacy.native_extension_configuration import LIVE_RENAMES as LIVE_CONFIG_MIGRATIONS


def migrate_live_config_keys(config: Any) -> int:
    """Copy legacy Live keys into their V2-native names without deleting data."""
    changed = 0
    for old_key, new_key in LIVE_CONFIG_MIGRATIONS.items():
        try:
            has_new = new_key in config
        except Exception:
            has_new = bool(getattr(config, 'get', lambda *_: None)(new_key, None) is not None)
        if has_new:
            continue
        try:
            value = config.get(old_key, None)
        except Exception:
            value = None
        if value is None:
            continue
        try:
            config[new_key] = value
            changed += 1
        except Exception:
            continue
    return changed

def _migrate_l2d_template_keys(config: Any) -> int:
    """补齐 AstrBot 4.26.6 template_list 保存校验需要的模板标识。"""
    entries = config.get('l2d_hotkeys', []) if hasattr(config, 'get') else []
    if not isinstance(entries, list):
        return 0
    changed = 0
    for entry in entries:
        if isinstance(entry, dict) and entry.get('__template_key') != 'default':
            entry['__template_key'] = 'default'
            changed += 1
    return changed

def _resolve_event_hook_call(current: Any, args: tuple[Any, ...], expected_payload_size: int) -> tuple[Any, tuple[Any, ...] | None]:
    """兼容 AstrBot 热重载后偶发的重复插件实例绑定。"""
    call_args = (current, *args)
    if len(call_args) <= expected_payload_size:
        return (current, None)
    runtime_candidates = call_args[:-expected_payload_size]
    runtime = runtime_candidates[-1]
    if not hasattr(runtime, 'config'):
        runtime = current
    return (runtime, call_args[-expected_payload_size:])

class LiveNativeExtensionAPI:
    """Small V2-internal surface for the Together live presentation service."""

    def __init__(self, plugin: 'VTubeStudioPlugin') -> None:
        self._plugin = plugin

    def companion_v2_status(self) -> dict[str, Any]:
        """Expose metadata without connecting OBS, VTS or a livestream service."""
        return {'schema': 'companion.provider-status.v1', 'plugin_id': 'astrbot_plugin_private_companion.live', 'enabled': self._plugin.native_v2_enabled, 'runtime_ready': self._plugin._native_provider is not None}

    def companion_v2_native(self):
        return self._plugin._native_provider if self._plugin.native_v2_enabled else None

    async def push_subtitle(self, text: str, *, source: str='native') -> bool:
        if not self._plugin._is_subtitle_enabled():
            return False
        await self._plugin._push_subtitle(text, source=source)
        return True

    async def start_mouth_sync(self, audio_path: str, *, source: str='native') -> bool:
        plugin = self._plugin
        path = plugin._normalize_local_audio_path(audio_path)
        if not path or not plugin._is_mouth_sync_enabled():
            return False
        source_key = str(source or 'native').strip()[:80] or 'native'

        async def run() -> None:
            prepared_path = path
            cleanup_path = ''
            try:
                if Path(path).suffix.lower() != '.wav':
                    ffmpeg = shutil.which('ffmpeg')
                    if not ffmpeg:
                        logger.debug('[嘴型] Together 音频不是 wav 且未找到 ffmpeg，跳过嘴型联动')
                        return
                    cleanup_path = str(Path(path).with_name(f'{Path(path).stem}.mouth-{uuid.uuid4().hex[:8]}.wav'))
                    result = await asyncio.to_thread(subprocess.run, [ffmpeg, '-y', '-loglevel', 'error', '-i', path, '-ac', '1', '-ar', '24000', cleanup_path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
                    if result.returncode != 0 or not os.path.isfile(cleanup_path):
                        return
                    prepared_path = cleanup_path
                await plugin._run_mouth_sync(prepared_path)
            finally:
                if cleanup_path:
                    try:
                        os.remove(cleanup_path)
                    except OSError:
                        pass
        task = asyncio.create_task(run())
        plugin._mouth_sync_tasks.add(task)
        tasks = plugin._external_mouth_sync_tasks.setdefault(source_key, set())
        tasks.add(task)

        def finish(finished: asyncio.Task) -> None:
            plugin._mouth_sync_tasks.discard(finished)
            source_tasks = plugin._external_mouth_sync_tasks.get(source_key)
            if isinstance(source_tasks, set):
                source_tasks.discard(finished)
                if not source_tasks:
                    plugin._external_mouth_sync_tasks.pop(source_key, None)
            try:
                finished.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug('[嘴型] Together 音频嘴型任务失败: %s', exc)
        task.add_done_callback(finish)
        return True

    async def stop_mouth_sync(self, *, source: str='native') -> int:
        source_key = str(source or 'native').strip()[:80] or 'native'
        tasks = list(self._plugin._external_mouth_sync_tasks.pop(source_key, set()))
        for task in tasks:
            if isinstance(task, asyncio.Task) and (not task.done()):
                task.cancel()
        return len(tasks)

class SyntheticBiliLiveWakeEvent(AstrMessageEvent):

    def __init__(self, *, template_event: Optional[AstrMessageEvent], context: Context, session: MessageSession, message: str) -> None:
        message_obj = AstrBotMessage()
        message_obj.type = session.message_type
        message_obj.self_id = session.session_id
        message_obj.session_id = session.session_id
        message_obj.message_id = f'bili_live_auto_{uuid.uuid4().hex}'
        message_obj.sender = MessageMember(user_id=session.session_id, nickname='BiliLive')
        message_obj.message = [Plain(message)]
        message_obj.message_str = message
        message_obj.raw_message = message
        message_obj.timestamp = int(time.time())
        platform_meta = None
        if template_event:
            try:
                platform_meta = template_event.get_platform_metadata()
            except Exception:
                platform_meta = getattr(template_event, 'platform_meta', None)
        if platform_meta is None:
            platform_meta = PlatformMetadata(name=session.platform_id, description='SyntheticBiliLiveWake', id=session.platform_id)
        super().__init__(message, message_obj, platform_meta, session.session_id)
        self.session = session
        self.context_obj = context
        self.is_at_or_wake_command = True
        self.is_wake = True

__all__ = [name for name in globals() if not name.startswith("__")]
