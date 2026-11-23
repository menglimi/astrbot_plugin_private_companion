from __future__ import annotations

import asyncio
import hashlib
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

import aiohttp
from astrbot.api import logger

QUICK_TUNNEL_PATTERN = re.compile(
    r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE
)
CLOUDFLARED_RELEASE_API = (
    "https://api.github.com/repos/cloudflare/cloudflared/releases/latest"
)


class QuickTunnel:
    """Manage an optional Cloudflare Quick Tunnel subprocess."""

    def __init__(
        self,
        local_url: str,
        search_paths: list[Path] | None = None,
        *,
        configured_path: str = "",
        download_dir: Path | None = None,
        download_proxy: str = "",
        allow_download: bool = True,
    ) -> None:
        self.local_url = str(local_url or "").rstrip("/")
        self.search_paths = [Path(path) for path in search_paths or []]
        self.configured_path = str(configured_path or "").strip()
        self.download_dir = Path(download_dir) if download_dir else None
        self.download_proxy = str(download_proxy or "").strip()
        self.allow_download = bool(allow_download)
        self.url = ""
        self.started_at = 0.0
        self.error = ""
        self._reachable = False
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._probe_task: asyncio.Task | None = None
        self._start_lock = asyncio.Lock()
        self._install_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return bool(self._process and self._process.returncode is None and self.url)

    @property
    def ready(self) -> bool:
        return self.running and self._reachable

    def _configured_candidate(self) -> Path | None:
        if not self.configured_path:
            return None
        candidate = Path(self.configured_path).expanduser()
        return candidate.resolve() if candidate.is_file() else None

    def _search_roots(self) -> list[Path]:
        roots: list[Path] = []
        if self.download_dir is not None:
            roots.append(self.download_dir)
        for root in self.search_paths:
            if root not in roots:
                roots.append(root)
        return roots

    def binary_path(self) -> Path | None:
        """Find cloudflared without downloading or modifying the host."""
        configured = self._configured_candidate()
        if configured:
            return configured
        command = shutil.which("cloudflared")
        if command:
            return Path(command).resolve()
        names = (
            ("cloudflared.exe", "cloudflared") if os.name == "nt" else ("cloudflared",)
        )
        for root in self._search_roots():
            for name in names:
                candidate = root / name
                if candidate.is_file():
                    return candidate.resolve()
        return None

    def binary_source(self) -> str:
        binary = self.binary_path()
        if binary is None:
            return ""
        configured = self._configured_candidate()
        if configured == binary:
            return "configured"
        command = shutil.which("cloudflared")
        if command:
            try:
                if Path(command).resolve() == binary:
                    return "system"
            except OSError:
                pass
        if self.download_dir is not None:
            try:
                resolved_download = self.download_dir.resolve()
            except OSError:
                resolved_download = self.download_dir
            if resolved_download in binary.parents or self.download_dir in binary.parents:
                return "managed"
        return "bundled"

    def status(self) -> dict[str, object]:
        """Return a dashboard-safe tunnel status."""
        binary = self.binary_path()
        configured_missing = bool(self.configured_path and self._configured_candidate() is None)
        status_error = self.error
        if configured_missing and not status_error:
            status_error = f"配置的 cloudflared 路径不存在：{self.configured_path}"
        return {
            "installed": binary is not None,
            "path": str(binary) if binary else "",
            "source": self.binary_source(),
            "download_dir": str(self.download_dir) if self.download_dir else "",
            "configured_path": self.configured_path,
            "configured_missing": configured_missing,
            "running": self.running,
            "ready": self.ready,
            "url": self.url if self.running else "",
            "started_at": self.started_at if self.running else 0.0,
            "allow_download": self.allow_download,
            "platform": self._platform_label(),
            "error": status_error,
        }

    async def install_latest(self) -> dict[str, object]:
        """Download the latest official cloudflared binary through the configured proxy."""
        if not self.allow_download:
            raise PermissionError("插件配置已禁止从管理台下载 cloudflared")
        if self.download_dir is None:
            raise RuntimeError("cloudflared 下载目录尚未配置")
        async with self._install_lock:
            release = await self._latest_release()
            asset = self._select_asset(release)
            if asset is None:
                raise RuntimeError(
                    f"当前平台暂无 cloudflared 安装包：{self._platform_label()}"
                )
            url = str(asset.get("browser_download_url") or "")
            name = str(asset.get("name") or "")
            if not url or not name:
                raise RuntimeError("cloudflared 发行资产信息不完整")
            self.download_dir.mkdir(parents=True, exist_ok=True)
            binary_name = "cloudflared.exe" if os.name == "nt" else "cloudflared"
            with tempfile.TemporaryDirectory(
                prefix="cloudflared-install-", dir=self.download_dir.parent
            ) as temporary:
                temporary_root = Path(temporary)
                archive = temporary_root / name
                await self._download(url, archive)
                asset_digest = str(asset.get("digest") or "")
                if asset_digest.startswith("sha256:"):
                    actual = await asyncio.to_thread(self._sha256, archive)
                    if actual != asset_digest.removeprefix("sha256:").lower():
                        raise RuntimeError("cloudflared 下载文件校验失败，已拒绝安装")
                payload = await asyncio.to_thread(self._extract_binary, archive, name)
                if len(payload) < 1024 * 1024:
                    raise RuntimeError("cloudflared 下载文件异常过小，已拒绝安装")
                digest_url = self._find_digest_url(release, name)
                if digest_url:
                    expected = await self._download_text(digest_url)
                    digest = await asyncio.to_thread(self._sha256, archive)
                    if digest not in expected.lower():
                        raise RuntimeError("cloudflared 下载文件校验失败，已拒绝安装")
                staged = temporary_root / binary_name
                staged.write_bytes(payload)
                if os.name != "nt":
                    staged.chmod(staged.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                target = self.download_dir / binary_name
                staged.replace(target)
            self.error = ""
            logger.info("[GameCompanion] cloudflared 已安装: path=%s", target)
            return self.status()

    async def _latest_release(self) -> dict[str, object]:
        timeout = aiohttp.ClientTimeout(total=30)
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "AstrBot-GameCompanion"}
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(
                    CLOUDFLARED_RELEASE_API,
                    proxy=self._effective_proxy(),
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError) as exc:
            raise RuntimeError(f"读取 cloudflared 最新发行信息失败：{exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("assets"), list):
            raise RuntimeError("cloudflared 发行信息格式无效")
        return payload

    def _select_asset(self, release: dict[str, object]) -> dict[str, object] | None:
        assets = release.get("assets")
        if not isinstance(assets, list):
            return None
        label = self._platform_label()
        names = {str(item.get("name") or ""): item for item in assets if isinstance(item, dict)}
        candidates = {
            "linux-amd64": ("cloudflared-linux-amd64",),
            "linux-arm64": ("cloudflared-linux-arm64",),
            "linux-arm": ("cloudflared-linux-arm",),
            "windows-amd64": ("cloudflared-windows-amd64.exe",),
            "windows-arm64": ("cloudflared-windows-arm64.exe",),
            "darwin-amd64": ("cloudflared-darwin-amd64.tgz", "cloudflared-darwin-amd64"),
            "darwin-arm64": ("cloudflared-darwin-arm64.tgz", "cloudflared-darwin-arm64"),
        }.get(label, ())
        for name in candidates:
            if name in names:
                return names[name]
        return None

    @staticmethod
    def _find_digest_url(release: dict[str, object], name: str) -> str:
        assets = release.get("assets")
        if not isinstance(assets, list):
            return ""
        for item in assets:
            if not isinstance(item, dict):
                continue
            candidate = str(item.get("name") or "").lower()
            if candidate in {f"{name}.sha256", f"{name}.sha256sum"}:
                return str(item.get("browser_download_url") or "")
        return ""

    async def _download(self, url: str, target: Path) -> None:
        timeout = aiohttp.ClientTimeout(total=180)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "AstrBot-GameCompanion"}) as session:
                async with session.get(url, proxy=self._effective_proxy(), allow_redirects=True) as response:
                    response.raise_for_status()
                    with target.open("wb") as output:
                        async for chunk in response.content.iter_chunked(1024 * 256):
                            output.write(chunk)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError) as exc:
            raise RuntimeError(f"下载 cloudflared 失败：{exc}") from exc

    async def _download_text(self, url: str) -> str:
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": "AstrBot-GameCompanion"},
            ) as session:
                async with session.get(url, proxy=self._effective_proxy()) as response:
                    response.raise_for_status()
                    return await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError) as exc:
            raise RuntimeError(f"下载 cloudflared 校验信息失败：{exc}") from exc

    @staticmethod
    def _extract_binary(archive: Path, name: str) -> bytes:
        try:
            if name.endswith((".tgz", ".tar.gz")):
                with tarfile.open(archive, "r:gz") as bundle:
                    member = next(
                        (
                            item
                            for item in bundle.getmembers()
                            if Path(item.name).name == "cloudflared"
                        ),
                        None,
                    )
                    if member is None:
                        raise RuntimeError("cloudflared 压缩包缺少可执行文件")
                    stream = bundle.extractfile(member)
                    if stream is None:
                        raise RuntimeError("无法读取 cloudflared 压缩包内容")
                    return stream.read()
            return archive.read_bytes()
        except RuntimeError:
            raise
        except (OSError, tarfile.TarError, EOFError, ValueError) as exc:
            raise RuntimeError(f"读取 cloudflared 安装包失败：{exc}") from exc

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _effective_proxy(self) -> str | None:
        return (
            self.download_proxy
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("http_proxy")
            or None
        )

    @staticmethod
    def _platform_label() -> str:
        system = platform.system().lower()
        machine = platform.machine().lower()
        if system == "linux":
            return "linux-arm64" if machine in {"aarch64", "arm64"} else "linux-arm" if machine.startswith("armv7") else "linux-amd64"
        if system == "windows":
            return "windows-arm64" if machine in {"aarch64", "arm64"} else "windows-amd64"
        if system == "darwin":
            return "darwin-arm64" if machine in {"aarch64", "arm64"} else "darwin-amd64"
        return f"{system}-{machine}"

    async def start(self, timeout: float = 40.0) -> str:
        """Start cloudflared and wait for its temporary HTTPS URL."""
        async with self._start_lock:
            if self.ready:
                return self.url
            binary = self.binary_path()
            if binary is None:
                raise RuntimeError("未找到 cloudflared，请先安装、配置路径或从游戏管理台下载")
            await self.stop()
            self.url = ""
            self.started_at = 0.0
            self.error = ""
            self._reachable = False
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                self._process = await asyncio.create_subprocess_exec(
                    str(binary), "tunnel", "--no-autoupdate", "--url", self.local_url,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    creationflags=creationflags,
                )
            except OSError as exc:
                raise RuntimeError(f"无法启动 cloudflared：{exc}") from exc
            logger.info("[GameCompanion] 正在启动 cloudflared: path=%s local=%s", binary, self.local_url)
            self._reader_task = asyncio.create_task(self._read_output())
            deadline = asyncio.get_running_loop().time() + max(2.0, timeout)
            while not self.url and asyncio.get_running_loop().time() < deadline:
                if self._process.returncode is not None:
                    break
                await asyncio.sleep(0.1)
            if not self.url or self._process.returncode is not None:
                message = self.error or "cloudflared 未返回临时公网地址"
                await self.stop()
                raise RuntimeError(message)
            remaining = max(2.0, deadline - asyncio.get_running_loop().time())
            if not await self._wait_until_reachable(self.url, self._process, timeout=remaining):
                message = self.error or "临时公网地址尚未生效，请稍后重试"
                await self.stop()
                raise RuntimeError(message)
            self._probe_task = asyncio.create_task(self._monitor_reachability(self.url, self._process))
            return self.url

    async def stop(self) -> None:
        """Stop the subprocess and clear the temporary public URL."""
        process, reader, probe = self._process, self._reader_task, self._probe_task
        self._process = self._reader_task = self._probe_task = None
        self.url = ""
        self.started_at = 0.0
        self._reachable = False
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=4)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        if reader is not None and not reader.done():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        if probe is not None and probe is not asyncio.current_task() and not probe.done():
            probe.cancel()
            await asyncio.gather(probe, return_exceptions=True)

    async def _probe_health(self, public_url: str) -> bool:
        timeout = aiohttp.ClientTimeout(total=4.0)
        try:
            async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
                async with session.get(f"{public_url.rstrip('/')}/health", allow_redirects=False) as response:
                    return response.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    async def _wait_until_reachable(self, public_url: str, process: asyncio.subprocess.Process | None, *, timeout: float) -> bool:
        deadline = asyncio.get_running_loop().time() + max(2.0, timeout)
        consecutive_successes = 0
        while self._process is process and process is not None and process.returncode is None and self.url == public_url and asyncio.get_running_loop().time() < deadline:
            if await self._probe_health(public_url):
                consecutive_successes += 1
                if consecutive_successes >= 2:
                    self._reachable = True
                    self.error = ""
                    logger.info("[GameCompanion] cloudflared 公网健康检查通过: url=%s", public_url)
                    return True
            else:
                consecutive_successes = 0
            await asyncio.sleep(1.0)
        self.error = "临时公网地址未通过连通性检查，已停止签发该链接"
        return False

    async def _monitor_reachability(self, public_url: str, process: asyncio.subprocess.Process | None, *, interval: float = 15.0, failure_limit: int = 3) -> None:
        failures = 0
        try:
            while self._process is process and process is not None and process.returncode is None and self.url == public_url:
                await asyncio.sleep(max(0.0, interval))
                if await self._probe_health(public_url):
                    failures = 0
                    self._reachable = True
                    self.error = ""
                    continue
                failures += 1
                if failures >= max(1, failure_limit):
                    self._reachable = False
                    self.error = "临时公网地址已失去连通性，正在重新建立通道"
                    logger.warning("[GameCompanion] cloudflared 公网健康检查连续失败: url=%s", public_url)
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._reachable = False
            self.error = f"公网地址连通性检查失败：{exc}"

    async def _read_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self.error = "无法读取 cloudflared 输出"
            return
        lines: list[str] = []
        try:
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    lines.append(line)
                    del lines[:-8]
                match = QUICK_TUNNEL_PATTERN.search(line)
                if match and not self.url:
                    self.url = match.group(0).rstrip("/")
                    self.started_at = time.time()
            await process.wait()
            if self._process is process:
                self._reachable = False
                detail = lines[-1][-300:] if lines else "未提供错误信息"
                self.error = f"cloudflared 已退出（代码 {process.returncode}）：{detail}"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = f"读取 cloudflared 状态失败：{exc}"
