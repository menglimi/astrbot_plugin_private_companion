"""Check and back up an offline AstrBot Companion 6.x installation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path
from tempfile import TemporaryDirectory

if __package__:
    from .plugin_identity import PLUGIN_ID, PLUGIN_VERSION
else:
    from plugin_identity import PLUGIN_ID, PLUGIN_VERSION


CONFIG_NAME = f"{PLUGIN_ID}_config.json"
MANIFEST_NAME = "manifest.json"


def _read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"需要 JSON 对象：{path}")
    return value


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _signature(path: Path) -> tuple[int, ...]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or _is_link(info):
        raise ValueError(f"请核对链接或非普通文件：{path}")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _sqlite_path(value: object, data: Path, source: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        return source / "companions.db"
    return Path(os.path.abspath(data.parent / value.strip()))


def _raise(error: OSError) -> None:
    raise error


def inspect_installation(data: Path) -> tuple[dict, dict[str, Path]]:
    config = data / "config" / CONFIG_NAME
    source = data / "plugin_data" / PLUGIN_ID
    _signature(config)
    settings = _read_object(config)
    markers = ("basic_config", "legacy_compat_config", "target_user_ids", "LLM_PROVIDER_ID")
    if not any(key in settings for key in markers):
        raise ValueError("无法识别陪伴 6.x 配置，请核对 AstrBot 的 data 目录。")
    if not source.is_dir() or _is_link(source.lstat()):
        raise ValueError(f"请核对旧版数据目录：{source}")

    basic = settings.get("basic_config")
    basic = basic if isinstance(basic, dict) else {}

    def setting(key: str):
        return basic.get(key, settings.get(f"basic_config.{key}", settings.get(key)))

    backend = setting("storage_backend")
    marker = source / ".storage-backend-state.json"
    state = _read_object(marker) if marker.is_file() else {}
    state_backend = state.get("backend")
    if not backend and not state_backend:
        backend = "sqlite" if (source / "companions.db").is_file() else "json"
    issues = []
    if backend and state_backend and backend != state_backend:
        issues.append("面板配置与运行记录的存储后端不一致，请在 6.x 面板核对。")
    database_paths = set()
    if backend == "sqlite":
        database_paths.add(_sqlite_path(setting("storage_sqlite_path"), data, source))
    if state_backend == "sqlite":
        database_paths.add(_sqlite_path(state.get("sqlite_path"), data, source))
    if len(database_paths) > 1:
        issues.append("SQLite 配置路径与运行记录不一致，请在 6.x 面板核对。")
    if (state_backend or backend) == "json":
        profile = source / "companions.json"
        try:
            _read_object(profile)
        except (OSError, ValueError):
            issues.append("主角色 JSON 资料无法读取，请核对 6.x 存储状态。")

    files = {f"config/{CONFIG_NAME}": config}
    for folder, directories, filenames in os.walk(source, onerror=_raise):
        current = Path(folder)
        if current == source and "companion-7" in directories:
            directories.remove("companion-7")
        for name in directories:
            child = current / name
            if _is_link(child.lstat()):
                raise ValueError(f"请单独备份目录链接及其实际目标：{child}")
        for name in filenames:
            path = current / name
            _signature(path)
            files[f"plugin_data/{PLUGIN_ID}/{path.relative_to(source).as_posix()}"] = path

    external = {}
    local_paths = set(files.values())
    for index, database in enumerate(sorted(database_paths), 1):
        if not database.is_file():
            issues.append(f"找不到 SQLite 数据库：{database}")
            continue
        if database in local_paths:
            continue
        destination = f"external-sqlite/{index}/{database.name}"
        external[destination] = str(database)
        for suffix in ("", "-wal", "-shm", "-journal"):
            path = database.with_name(database.name + suffix)
            if path.exists():
                _signature(path)
                files[destination + suffix] = path

    return {
        "plugin_version": PLUGIN_VERSION,
        "backend": state_backend or backend,
        "issues": issues,
        "external_sqlite": external,
        "files": len(files),
    }, files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def backup_installation(data: Path, target: Path) -> dict:
    data, target = data.resolve(), target.resolve()
    if target.is_relative_to(data) or target.exists():
        raise ValueError("请选择 AstrBot data 目录之外、尚未存在的备份目录。")
    report, files = inspect_installation(data)
    if any(path.is_relative_to(target) for path in files.values()):
        raise ValueError("备份目录须与旧版配置、资料和自定义数据库目录分开。")
    signatures = {name: _signature(path) for name, path in files.items()}
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".companion-6-backup-", dir=target.parent) as temporary:
        staged = Path(temporary) / "backup"
        staged.mkdir(mode=0o700)
        hashes = {}
        for name, path in sorted(files.items()):
            output = staged / name
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, output)
            hashes[name] = _sha256(output)
        _, current_files = inspect_installation(data)
        if current_files != files or any(_signature(path) != signatures[name] for name, path in files.items()):
            raise ValueError("备份期间旧版数据发生变化。请停止 AstrBot 后重新执行。")
        (staged / MANIFEST_NAME).write_text(
            json.dumps({**report, "sha256": hashes}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        verify_backup(staged)
        staged.rename(target)
    return {**report, "backup_dir": str(target)}


def verify_backup(backup: Path) -> dict:
    backup = backup.resolve()
    manifest = _read_object(backup / MANIFEST_NAME)
    hashes = manifest.get("sha256")
    if not isinstance(hashes, dict) or f"config/{CONFIG_NAME}" not in hashes or len(hashes) != manifest.get("files"):
        raise ValueError("备份清单缺少配置或 SHA-256 记录。")
    for name, expected in hashes.items():
        path = (backup / name).resolve()
        if not path.is_relative_to(backup) or _sha256(path) != expected:
            raise ValueError(f"备份校验失败：{name}")
    return {"backup_dir": str(backup), "verified_files": len(hashes)}


def main() -> int:
    parser = argparse.ArgumentParser(description="在停止 AstrBot 后检查或备份陪伴插件 6.x 的配置与旧数据。")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--data-dir", type=Path, help="AstrBot 的 data 目录")
    inputs.add_argument("--verify-backup", type=Path, help="校验已生成的备份目录")
    parser.add_argument("--backup-dir", type=Path, help="把备份写入指定的新目录")
    args = parser.parse_args()
    if args.verify_backup is not None and args.backup_dir is not None:
        parser.error("--verify-backup 不能与 --backup-dir 同时使用")
    try:
        if args.verify_backup is not None:
            report = verify_backup(args.verify_backup)
        elif args.backup_dir is not None:
            report = backup_installation(args.data_dir, args.backup_dir)
        else:
            report, _ = inspect_installation(args.data_dir.resolve())
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report.get("issues") else 0


if __name__ == "__main__":
    raise SystemExit(main())
