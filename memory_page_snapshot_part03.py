# -*- coding: utf-8 -*-
"""MemoryPageSnapshotServicePart03Mixin。

由 tools/split_mixin_domain.py 从 memory_page_snapshot.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 73 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 MemoryPageSnapshotService）。
"""
from __future__ import annotations

import os
from pathlib import Path
try:  # package import
    from .memory_page_snapshot_shared import MemoryPageSnapshotError
except ImportError:  # direct test/import from the plugin directory
    from memory_page_snapshot_shared import MemoryPageSnapshotError
try:  # package import
    from .memory_page_snapshot_shared import _PhotoBlob
except ImportError:  # direct test/import from the plugin directory
    from memory_page_snapshot_shared import _PhotoBlob
try:  # package import
    from .memory_page_snapshot_shared import _PhotoRegistration
except ImportError:  # direct test/import from the plugin directory
    from memory_page_snapshot_shared import _PhotoRegistration



class MemoryPageSnapshotServicePart03Mixin:
    """MemoryPageSnapshotServicePart03Mixin（从 MemoryPageSnapshotService 拆出）。"""


    @staticmethod
    def _open_nofollow(root: Path, parts: tuple[str, ...]) -> int:
        if not parts:
            raise MemoryPageSnapshotError("memory_page_photo_unavailable")
        nofollow = getattr(os, "O_NOFOLLOW", None)
        directory = getattr(os, "O_DIRECTORY", None)
        nonblock = getattr(os, "O_NONBLOCK", 0)
        binary = getattr(os, "O_BINARY", 0)
        cloexec = getattr(os, "O_CLOEXEC", 0)
        if nofollow is not None and directory is not None and os.open in os.supports_dir_fd:
            directory_flags = os.O_RDONLY | directory | nofollow | cloexec
            file_flags = os.O_RDONLY | nofollow | nonblock | binary | cloexec
            opened_directory = -1
            try:
                opened_directory = os.open(os.fspath(root), directory_flags)
                for part in parts[:-1]:
                    next_directory = os.open(part, directory_flags, dir_fd=opened_directory)
                    os.close(opened_directory)
                    opened_directory = next_directory
                return os.open(parts[-1], file_flags, dir_fd=opened_directory)
            except (OSError, TypeError, NotImplementedError):
                raise MemoryPageSnapshotError("memory_page_photo_unavailable") from None
            finally:
                if opened_directory >= 0:
                    try:
                        os.close(opened_directory)
                    except OSError:
                        pass

        # Windows has no dir_fd/O_NOFOLLOW. Resolve the existing path immediately
        # before opening and require it to remain under the already trusted root.
        try:
            candidate = root.joinpath(*parts)
            if candidate.is_symlink():
                raise MemoryPageSnapshotError("memory_page_photo_unavailable")
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            return os.open(os.fspath(resolved), os.O_RDONLY | nonblock | binary | cloexec)
        except MemoryPageSnapshotError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise MemoryPageSnapshotError("memory_page_photo_unavailable") from None

    @staticmethod
    def _detect_image_mime(content: bytes) -> str:
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if content.startswith(b"BM"):
            return "image/bmp"
        if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return "image/webp"
        if (
            len(content) >= 12
            and content[4:8] == b"ftyp"
            and content[8:12] in {b"avif", b"avis"}
        ):
            return "image/avif"
        return ""

    @classmethod
    def _read_photo_registration_sync(cls, registration: _PhotoRegistration) -> _PhotoBlob:
        try:
            return cls._read_authorized_photo(
                registration.root,
                registration.parts,
                expected=registration,
            )
        except MemoryPageSnapshotError as error:
            if error.code == "memory_page_photo_unavailable":
                raise MemoryPageSnapshotError("memory_page_photo_changed") from None
            raise
