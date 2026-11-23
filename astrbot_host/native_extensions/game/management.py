"""Explicit cottage management actions for native Game assets and tunnel."""

from __future__ import annotations

import asyncio
import importlib.util
from typing import Any

from companion_v2.execution.models import TaskError


GAME_MANAGEMENT_ACTIONS = frozenset({
    "xiangqi_install",
    "xiangqi_check",
    "cloudflared_install",
    "cloudflared_check",
    "tunnel_start",
    "tunnel_stop",
})


def _xiangqi_guidance(status: dict[str, Any]) -> str:
    if status.get("available"):
        source = "自定义路径" if status.get("configured") else "托管安装"
        if status.get("configured_missing"):
            return f"所填写的 xiangqi.engine_path 不存在，当前回退使用{source}的 Pikafish 引擎。"
        return f"Pikafish 象棋引擎已就绪（{source}）。"
    if status.get("configured_missing"):
        return "所填写的 xiangqi.engine_path 不存在；请核对路径，或清空路径后点击「下载 Pikafish 引擎」。"
    if status.get("path") and not status.get("available"):
        return "已检测到 Pikafish 可执行文件，但同目录缺少 pikafish.nnue 神经网络文件，请将两者放在同一目录。"
    if not status.get("allow_download", True):
        return "当前配置已禁止自动下载 Pikafish；请开启「允许管理台下载 Pikafish」，或在设置中填写本地引擎路径。"
    if importlib.util.find_spec("py7zr") is None:
        return "下载安装包需要 py7zr 依赖；若环境未安装 py7zr，也可手动准备 pikafish 与 pikafish.nnue 后在设置中填写路径。"
    return "尚未安装 Pikafish 引擎；点击「下载 Pikafish 引擎」即可自动安装，或在设置中指定已有引擎路径。"


def _tunnel_guidance(status: dict[str, Any], service: Any) -> str:
    configured_base = str(getattr(service, "public_base_url", "") or getattr(service, "external_base_url", "") or "")
    if configured_base:
        return f"已配置固定外部地址（{configured_base}），无需依赖临时公网隧道。"
    if status.get("ready") and status.get("url"):
        return f"临时公网通道运行中：{status['url']}"
    if status.get("installed"):
        source_labels = {
            "configured": "自定义路径",
            "managed": "托管目录",
            "system": "系统 PATH",
            "bundled": "插件目录",
        }
        source = source_labels.get(str(status.get("source") or ""), "已就绪")
        if status.get("configured_missing"):
            return f"所填写的 server.cloudflared_path 不存在，当前回退使用{source}的 cloudflared。"
        return f"已检测到 cloudflared（{source}），可按需提供临时公网访问。"
    if status.get("configured_missing"):
        return "所填写的 server.cloudflared_path 不存在；请核对路径，或清空路径后点击「下载 cloudflared」。"
    if not status.get("allow_download", True):
        return "当前配置已禁止下载 cloudflared；可开启下载开关、配置自定义路径，或改用局域网 / 固定 HTTPS 地址。"
    return "未检测到 cloudflared；点击「下载 cloudflared」即可自动安装到托管目录，或配置局域网 / 固定 HTTPS 地址。"


def snapshot(service: Any) -> dict[str, Any]:
    """Read local game engine, tunnel and room status without network I/O."""
    engine = dict(service.xiangqi_engine.status())
    engine["guidance"] = _xiangqi_guidance(engine)
    tunnel = dict(service.quick_tunnel.status())
    tunnel["guidance"] = _tunnel_guidance(tunnel, service)
    native_provider = getattr(service, "_native_provider", None)
    native_games = native_provider.list_web_games() if native_provider is not None else []
    rooms = [room.admin_snapshot() for room in service.manager.rooms.values()]
    return {
        "server": {
            "enabled": bool(service.server_enabled),
            "running": bool(service.room_server.running),
            "host": str(service.server_host),
            "port": service.room_server.port if service.room_server.running else service.server_port,
            "public_base_url": str(service.public_base_url or ""),
            "external_base_url": str(service.external_base_url or ""),
            "access_host": str(service.access_host or ""),
            "local_base_url": str(service.room_server.local_base_url),
        },
        "xiangqi_engine": engine,
        "tunnel": tunnel,
        "native_games": native_games,
        "rooms": rooms,
        "actions": sorted(GAME_MANAGEMENT_ACTIONS),
        "error_code": getattr(service, "_management_error", None),
        "error_message": getattr(service, "_management_error_message", ""),
    }


def _set_error(service: Any, code: str | None, message: str = "") -> None:
    service._management_error = code
    service._management_error_message = message


async def action(service: Any, name: str, payload: dict[str, Any], current) -> dict[str, Any]:
    """Execute one explicit game management action outside the host control lock."""
    if name not in GAME_MANAGEMENT_ACTIONS or payload:
        raise ValueError("不支持的游戏管理操作")
    if not hasattr(service, "_management_lock"):
        service._management_lock = asyncio.Lock()
    async with service._management_lock:
        current()
        try:
            if name == "xiangqi_install":
                try:
                    status = await asyncio.wait_for(service.xiangqi_engine.install_latest(), timeout=240)
                except PermissionError as exc:
                    raise TaskError("native_game_download_disabled", str(exc)) from exc
                except ValueError as exc:
                    raise TaskError("native_game_configured_path_active", str(exc)) from exc
                except (RuntimeError, OSError) as exc:
                    raise TaskError("native_game_xiangqi_install_failed", str(exc)) from exc
                current()
                _set_error(service, None)
                return {"completed": True, "xiangqi_engine": status}

            if name == "xiangqi_check":
                status = service.xiangqi_engine.status()
                if status.get("available"):
                    try:
                        await asyncio.wait_for(service.xiangqi_engine.ensure_ready(), timeout=15)
                    except (RuntimeError, OSError) as exc:
                        raise TaskError("native_game_xiangqi_check_failed", str(exc)) from exc
                    status = service.xiangqi_engine.status()
                current()
                _set_error(service, None)
                return {"completed": True, "xiangqi_engine": status}

            if name == "cloudflared_install":
                try:
                    status = await asyncio.wait_for(service.quick_tunnel.install_latest(), timeout=240)
                except PermissionError as exc:
                    raise TaskError("native_game_download_disabled", str(exc)) from exc
                except (RuntimeError, OSError) as exc:
                    raise TaskError("native_game_cloudflared_install_failed", str(exc)) from exc
                current()
                _set_error(service, None)
                return {"completed": True, "tunnel": status}

            if name == "cloudflared_check":
                status = service.quick_tunnel.status()
                current()
                _set_error(service, None)
                return {"completed": True, "tunnel": status}

            if name == "tunnel_start":
                if not service.server_enabled:
                    raise TaskError("native_game_server_disabled", "请先启用游戏页面服务后再启动公网通道")
                if service._configured_access_base():
                    raise TaskError("native_game_external_url_configured", "已配置固定外部访问地址，无需启动临时公网通道")
                try:
                    if not service.room_server.running:
                        await service.room_server.start()
                    service.quick_tunnel.local_url = service.room_server.local_base_url
                    url = await asyncio.wait_for(service.quick_tunnel.start(timeout=40), timeout=50)
                except (RuntimeError, OSError) as exc:
                    raise TaskError("native_game_tunnel_failed", str(exc)) from exc
                current()
                _set_error(service, None)
                return {"completed": True, "url": url, "tunnel": service.quick_tunnel.status()}

            if name == "tunnel_stop":
                native_provider = getattr(service, "_native_provider", None)
                native_games = native_provider.list_web_games() if native_provider is not None else []
                if service.manager.rooms or native_games:
                    raise TaskError("native_game_active_games_exist", "仍有活动对局，不能停止访问通道")
                await service.quick_tunnel.stop()
                current()
                _set_error(service, None)
                return {"completed": True, "tunnel": service.quick_tunnel.status()}

            raise ValueError("不支持的游戏管理操作")
        except asyncio.TimeoutError as exc:
            _set_error(service, "native_management_result_unknown", "操作超时，请刷新管理状态核对结果")
            raise TaskError("native_management_result_unknown", "操作超时，请刷新管理状态核对结果") from exc
        except TaskError as exc:
            _set_error(service, exc.code, str(exc))
            raise
        except Exception as exc:
            _set_error(service, "native_game_action_failed", str(exc) or "游戏管理操作未完成")
            raise TaskError("native_game_action_failed", str(exc) or "游戏管理操作未完成") from exc
