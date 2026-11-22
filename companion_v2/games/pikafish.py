from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import random
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

import aiohttp

MOVE_PATTERN = re.compile(r"^[a-i][0-9][a-i][0-9]$")
PERFT_PATTERN = re.compile(r"^([a-i][0-9][a-i][0-9]):\s+\d+$")
PV_PATTERN = re.compile(r"\bmultipv\s+(\d+)\b.*\bpv\s+([a-i][0-9][a-i][0-9])\b")
BESTMOVE_PATTERN = re.compile(r"^bestmove\s+([a-i][0-9][a-i][0-9]|\(none\))")

PIKAFISH_REPOSITORY = "official-pikafish/Pikafish"
PIKAFISH_RELEASE_API = (
    f"https://api.github.com/repos/{PIKAFISH_REPOSITORY}/releases/latest"
)


class PikafishService:
    """Install and run one serialized Pikafish UCI process for all rooms."""

    def __init__(
        self,
        *,
        data_dir: Path,
        configured_path: str = "",
        download_proxy: str = "",
        allow_download: bool = True,
        auto_download: bool = False,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.install_root = self.data_dir / "pikafish"
        self.configured_path = str(configured_path or "").strip()
        self.download_proxy = str(download_proxy or "").strip()
        self.allow_download = bool(allow_download)
        self.auto_download = bool(auto_download)
        self.error = ""
        self.version = ""
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._lines: asyncio.Queue[str | None] = asyncio.Queue()
        self._lifecycle_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._install_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return bool(self._process and self._process.returncode is None)

    def _configured_candidate(self) -> Path | None:
        if not self.configured_path:
            return None
        configured = Path(self.configured_path).expanduser()
        return configured.resolve() if configured.is_file() else None

    def binary_path(self) -> Path | None:
        configured = self._configured_candidate()
        if configured is not None:
            return configured
        metadata = self._install_metadata()
        recorded = str(metadata.get("binary") or "").strip()
        if recorded:
            candidate = self.install_root / recorded
            if candidate.is_file():
                return candidate.resolve()
        names = ("pikafish.exe", "pikafish") if os.name == "nt" else ("pikafish",)
        for name in names:
            fallback = self.install_root / name
            if fallback.is_file():
                return fallback.resolve()
        return None

    def status(self) -> dict[str, object]:
        binary = self.binary_path()
        configured = self._configured_candidate()
        configured_missing = bool(self.configured_path and configured is None)
        metadata = self._install_metadata()
        network = binary.parent / "pikafish.nnue" if binary else None
        available = bool(binary and network and network.is_file())
        status_error = self.error
        if configured_missing and not status_error:
            status_error = f"配置的 Pikafish 路径不存在：{self.configured_path}"
        elif binary and not available and not status_error:
            status_error = f"缺少神经网络文件：{network}"
        try:
            resolved_install = self.install_root.resolve()
        except OSError:
            resolved_install = self.install_root
        managed = bool(binary and (resolved_install in binary.parents or self.install_root in binary.parents))
        return {
            "available": available,
            "running": self.running,
            "version": self.version or str(metadata.get("version") or ""),
            "path": str(binary) if binary else "",
            "nnue_path": str(network) if (network and network.is_file()) else "",
            "install_root": str(self.install_root),
            "configured_path": self.configured_path,
            "configured_missing": configured_missing,
            "managed": managed,
            "configured": bool(binary and configured == binary),
            "allow_download": self.allow_download,
            "auto_download": self.auto_download,
            "platform": self._platform_label(),
            "error": status_error,
        }

    async def ensure_ready(self) -> None:
        if self.running:
            return
        if self.binary_path() is None and self.auto_download:
            await self.install_latest()
        binary = self.binary_path()
        if binary is None:
            raise RuntimeError(
                "象棋引擎尚未安装，请在游戏管理台安装 Pikafish，或在插件配置中填写引擎路径"
            )
        async with self._lifecycle_lock:
            if self.running:
                return
            try:
                await self._start(binary)
            except Exception as exc:
                self.error = str(exc)
                raise

    async def close(self) -> None:
        async with self._lifecycle_lock:
            await self._stop_process()

    async def legal_moves(self, moves: list[str]) -> list[str]:
        await self.ensure_ready()
        self._validate_history(moves)
        try:
            async with self._command_lock:
                await self._position(moves)
                lines = await self._run_until("go perft 1", "Nodes searched:", timeout=8)
        except Exception as exc:
            self.error = str(exc)
            await self.close()
            raise
        legal = [
            match.group(1)
            for line in lines
            if (match := PERFT_PATTERN.match(line))
        ]
        return legal

    async def choose_move(self, moves: list[str], difficulty: str) -> str:
        await self.ensure_ready()
        self._validate_history(moves)
        normalized = difficulty if difficulty in {"easy", "normal", "hard"} else "normal"
        multipv = {"easy": 8, "normal": 3, "hard": 1}[normalized]
        command = {
            "easy": "go depth 3",
            "normal": "go movetime 280",
            "hard": "go movetime 900",
        }[normalized]
        try:
            async with self._command_lock:
                await self._write(f"setoption name MultiPV value {multipv}")
                await self._position(moves)
                lines = await self._run_until(command, "bestmove ", timeout=8)
        except Exception as exc:
            self.error = str(exc)
            await self.close()
            raise
        candidates: dict[int, str] = {}
        bestmove = ""
        for line in lines:
            if match := PV_PATTERN.search(line):
                candidates[int(match.group(1))] = match.group(2)
            if match := BESTMOVE_PATTERN.match(line):
                bestmove = "" if match.group(1) == "(none)" else match.group(1)
        ordered = [candidates[index] for index in sorted(candidates)]
        if bestmove and bestmove not in ordered:
            ordered.insert(0, bestmove)
        if not ordered:
            raise RuntimeError("Pikafish 没有返回可用着法")
        if normalized == "hard":
            return ordered[0]
        if normalized == "normal":
            pool = ordered[:3]
            weights = [0.68, 0.23, 0.09][: len(pool)]
            return random.choices(pool, weights=weights, k=1)[0]
        pool = ordered[2:8] or ordered
        return random.choice(pool)

    async def install_latest(self) -> dict[str, object]:
        if not self.allow_download:
            raise PermissionError("插件配置已禁止从管理台下载象棋引擎")
        if self.configured_path and Path(self.configured_path).expanduser().is_file():
            raise ValueError(
                "当前使用插件配置中指定的 Pikafish；请先清空引擎路径再安装托管版本"
            )
        async with self._install_lock:
            release = await self._latest_release()
            version = str(release.get("tag_name") or "").strip()
            assets = release.get("assets")
            asset = next(
                (
                    item
                    for item in assets
                    if isinstance(item, dict)
                    and str(item.get("name") or "").lower().endswith(".7z")
                ),
                None,
            ) if isinstance(assets, list) else None
            if not version or not asset:
                raise RuntimeError("Pikafish 最新发行信息不完整")
            url = str(asset.get("browser_download_url") or "")
            digest = str(asset.get("digest") or "")
            if not url or not digest.startswith("sha256:"):
                raise RuntimeError("Pikafish 发行资产缺少下载地址或 SHA-256 摘要")
            member = self._archive_binary_member()
            self.install_root.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix="pikafish-install-", dir=self.install_root.parent
            ) as temporary:
                temporary_root = Path(temporary)
                archive_path = temporary_root / "pikafish.7z"
                await self._download(url, archive_path)
                actual = await asyncio.to_thread(self._sha256, archive_path)
                expected = digest.removeprefix("sha256:").lower()
                if actual != expected:
                    raise RuntimeError("Pikafish 下载文件校验失败，已拒绝安装")
                extracted = temporary_root / "archive"
                await asyncio.to_thread(
                    self._extract_archive,
                    archive_path,
                    extracted,
                    [member, "pikafish.nnue", "Copying.txt", "NNUE-License.md", "AUTHORS"],
                )
                staging = temporary_root / "managed"
                staging.mkdir()
                binary_name = "pikafish.exe" if os.name == "nt" else "pikafish"
                shutil.copy2(extracted / member, staging / binary_name)
                shutil.copy2(extracted / "pikafish.nnue", staging / "pikafish.nnue")
                for name in ("Copying.txt", "NNUE-License.md", "AUTHORS"):
                    source = extracted / name
                    if source.is_file():
                        shutil.copy2(source, staging / name)
                if os.name != "nt":
                    (staging / binary_name).chmod(
                        (staging / binary_name).stat().st_mode | stat.S_IXUSR
                    )
                metadata = {
                    "version": version,
                    "binary": binary_name,
                    "sha256": actual,
                    "asset": str(asset.get("name") or ""),
                    "source": url,
                }
                (staging / "install.json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                async with self._command_lock:
                    await self.close()
                    backup = self.install_root.with_name("pikafish.previous")
                    if backup.exists():
                        shutil.rmtree(backup)
                    had_previous = self.install_root.exists()
                    if had_previous:
                        self.install_root.rename(backup)
                    try:
                        staging.rename(self.install_root)
                    except Exception:
                        if (
                            had_previous
                            and backup.exists()
                            and not self.install_root.exists()
                        ):
                            backup.rename(self.install_root)
                        raise
                    try:
                        self.error = ""
                        self.version = version
                        await self.ensure_ready()
                    except Exception as exc:
                        await self.close()
                        if self.install_root.exists():
                            shutil.rmtree(self.install_root)
                        if backup.exists():
                            backup.rename(self.install_root)
                        self.version = ""
                        self.error = str(exc)
                        raise
                    else:
                        if backup.exists():
                            shutil.rmtree(backup)
            return self.status()

    async def _start(self, binary: Path) -> None:
        network = binary.parent / "pikafish.nnue"
        if not network.is_file():
            raise RuntimeError(f"Pikafish 神经网络文件不存在：{network}")
        self.error = ""
        self._lines = asyncio.Queue()
        command = [str(binary)]
        # Development and CI fixtures sometimes provide a POSIX-style Python
        # UCI script without a .exe suffix. Windows cannot launch that file
        # directly, so use the active interpreter when its shebang identifies
        # Python while leaving real engine binaries untouched.
        if os.name == "nt":
            try:
                header = binary.read_bytes()[:128]
            except OSError:
                header = b""
            if header.startswith(b"#!") and b"python" in header.lower():
                command = [sys.executable, str(binary)]
        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(binary.parent),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            raise RuntimeError(f"无法启动 Pikafish：{exc}") from exc
        self._reader_task = asyncio.create_task(self._read_output())
        try:
            lines = await self._run_until("uci", "uciok", timeout=8)
            identity = next((line for line in lines if line.startswith("id name ")), "")
            self.version = identity.removeprefix("id name ").strip()
            await self._write("setoption name Threads value 1")
            await self._write("setoption name Hash value 64")
            await self._run_until("isready", "readyok", timeout=8)
        except Exception:
            await self._stop_process()
            raise

    async def _stop_process(self) -> None:
        process, reader = self._process, self._reader_task
        self._process = None
        self._reader_task = None
        if process is not None and process.returncode is None:
            try:
                if process.stdin is not None:
                    process.stdin.write(b"quit\n")
                    await process.stdin.drain()
                await asyncio.wait_for(process.wait(), timeout=3)
            except (BrokenPipeError, ConnectionError, asyncio.TimeoutError):
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        if reader is not None and not reader.done():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    async def _position(self, moves: list[str]) -> None:
        suffix = f" moves {' '.join(moves)}" if moves else ""
        await self._write(f"position startpos{suffix}")

    async def _run_until(
        self, command: str, terminator: str, *, timeout: float
    ) -> list[str]:
        await self._write(command)
        lines: list[str] = []
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RuntimeError(f"Pikafish 命令超时：{command}")
            line = await asyncio.wait_for(self._lines.get(), timeout=remaining)
            if line is None:
                raise RuntimeError(self.error or "Pikafish 进程已退出")
            lines.append(line)
            if line.startswith(terminator) or line == terminator:
                return lines

    async def _write(self, command: str) -> None:
        process = self._process
        if process is None or process.returncode is not None or process.stdin is None:
            raise RuntimeError(self.error or "Pikafish 尚未运行")
        process.stdin.write((command + "\n").encode("ascii"))
        await process.stdin.drain()

    async def _read_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while raw := await process.stdout.readline():
                await self._lines.put(raw.decode("utf-8", errors="replace").strip())
            await process.wait()
            if self._process is process:
                self.error = f"Pikafish 已退出（代码 {process.returncode}）"
                await self._lines.put(None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = f"读取 Pikafish 输出失败：{exc}"
            await self._lines.put(None)

    async def _latest_release(self) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=30)
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "AstrBot-GameCompanion",
        }
        proxy = self._effective_proxy()
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(PIKAFISH_RELEASE_API, proxy=proxy) as response:
                    if response.status != 200:
                        raise RuntimeError(
                            f"读取 Pikafish 发行信息失败：HTTP {response.status}"
                        )
                    payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise RuntimeError(f"读取 Pikafish 发行信息失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Pikafish 发行信息格式无效")
        return payload

    async def _download(self, url: str, target: Path) -> None:
        timeout = aiohttp.ClientTimeout(total=180)
        proxy = self._effective_proxy()
        headers = {"User-Agent": "AstrBot-GameCompanion"}
        size = 0
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url, proxy=proxy, allow_redirects=True) as response:
                    if response.status != 200:
                        raise RuntimeError(f"下载 Pikafish 失败：HTTP {response.status}")
                    with target.open("wb") as stream:
                        async for chunk in response.content.iter_chunked(1024 * 1024):
                            size += len(chunk)
                            if size > 160 * 1024 * 1024:
                                raise RuntimeError("Pikafish 下载文件超过安全大小限制")
                            stream.write(chunk)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise RuntimeError(f"下载 Pikafish 失败：{exc}") from exc
        if size < 1024 * 1024:
            raise RuntimeError("Pikafish 下载文件异常过小")

    def _archive_binary_member(self) -> str:
        system = platform.system().lower()
        machine = platform.machine().lower()
        if system == "linux" and machine in {"x86_64", "amd64"}:
            flags = self._cpu_flags()
            return (
                "Linux/pikafish-avx2"
                if "avx2" in flags
                else "Linux/pikafish-sse41-popcnt"
            )
        if system == "windows" and machine in {"x86_64", "amd64"}:
            return "Windows/pikafish-avx2.exe"
        if system == "darwin" and machine in {"arm64", "aarch64"}:
            return "MacOS/pikafish-apple-silicon"
        raise RuntimeError(
            f"当前平台暂无 Pikafish 自动安装包：{platform.system()} {platform.machine()}"
        )

    def _platform_label(self) -> str:
        try:
            return self._archive_binary_member()
        except RuntimeError:
            return f"{platform.system()} {platform.machine()}"

    def _install_metadata(self) -> dict[str, Any]:
        path = self.install_root / "install.json"
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _effective_proxy(self) -> str | None:
        return (
            self.download_proxy
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
            or None
        )

    @staticmethod
    def _cpu_flags() -> set[str]:
        try:
            content = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return set()
        for line in content.splitlines():
            if line.lower().startswith("flags") and ":" in line:
                return set(line.split(":", 1)[1].strip().lower().split())
        return set()

    @staticmethod
    def _validate_history(moves: list[str]) -> None:
        if any(not MOVE_PATTERN.fullmatch(move) for move in moves):
            raise ValueError("象棋着法历史包含无效 ICCS 坐标")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _extract_archive(
        archive_path: Path, target: Path, members: list[str]
    ) -> None:
        try:
            import py7zr
        except ImportError as exc:
            raise RuntimeError("缺少 py7zr，无法解压 Pikafish 安装包") from exc
        target.mkdir(parents=True, exist_ok=True)
        with py7zr.SevenZipFile(archive_path, mode="r") as archive:
            names = set(archive.getnames())
            missing = [member for member in members[:2] if member not in names]
            if missing:
                raise RuntimeError("Pikafish 安装包缺少必要文件：" + "、".join(missing))
            archive.extract(path=target, targets=[member for member in members if member in names])
