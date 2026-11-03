"""把唯一维护源码同步到 AstrBot 运行目录。

源码是唯一可编辑副本；运行目录是构建产物，不在这里直接改。这个脚本只做
三件事：检查差异、生成带备份的同步计划、按计划原子替换文件。默认只检查，
写入必须显式 ``--apply``，且应用前会再次核对计划里记录的源文件与运行文件。

用法（在仓库根目录执行）：

    python tools/sync_runtime.py --check
    python tools/sync_runtime.py --plan
    python tools/sync_runtime.py --apply --plan-path <plan.json>
    python tools/sync_runtime.py --plan --include companion_v2/conversation/media/quota.py

运行目录里的 ``data/``、``__pycache__/``、``.git/`` 和发布时生成的
``RELEASE_FILES.json`` 不会被同步覆盖。差异备份写在仓库外的
``Z:\\CompanionUpgrade\\backups\\runtime-sync-<时间>/before``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

SOURCE = Path(__file__).resolve().parents[1]
DEFAULT_LIVE = Path(r"C:\Users\99505\.astrbot\data\plugins\astrbot_plugin_private_companion")
DEFAULT_BACKUP_ROOT = Path(r"Z:\CompanionUpgrade\backups")
IGNORED_PARTS = {"data", "__pycache__", ".git", ".pytest_cache"}
TEXT_SUFFIXES = {".py", ".js", ".css", ".html", ".json", ".md", ".yaml", ".yml", ".txt", ".ini", ".toml"}


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def repository_files(source: Path = SOURCE) -> list[str]:
    """Return tracked and untracked-but-not-ignored files, sorted."""
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=source)
    raw += subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=source)
    return sorted({name for name in raw.decode("utf-8").split("\0") if name})


def target(root: Path, name: str) -> Path:
    path = root / name
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("路径越界：" + name)
    return path


def compare(source: Path, live: Path, names: list[str] | None = None):
    """Compare the reviewed source against the live installation.

    Missing or different source files are drift. Files that exist only in the
    live installation are reported separately because release builds add
    ``RELEASE_FILES.json`` and runtime state lives under ignored directories.
    """
    names = repository_files(source) if names is None else names
    missing, different = [], []
    for name in names:
        source_path = target(source, name)
        if not source_path.is_file():
            raise FileNotFoundError("源码缺少文件：" + name)
        live_path = target(live, name)
        if not live_path.is_file():
            missing.append(name)
        elif source_path.read_bytes() != live_path.read_bytes():
            different.append(name)
    expected = set(names)
    extra = []
    for path in live.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(live)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if relative.as_posix() not in expected:
            extra.append(relative.as_posix())
    return {"missing": missing, "different": sorted(different), "extra": sorted(extra)}


def print_report(report, *, limit: int = 40) -> None:
    print("缺失 %d 个，内容不同 %d 个，运行目录独有 %d 个" % (
        len(report["missing"]), len(report["different"]), len(report["extra"])))
    for title, key in (("缺失", "missing"), ("不同", "different"), ("独有", "extra")):
        rows = report[key]
        if not rows:
            continue
        print("%s：" % title)
        for name in rows[:limit]:
            print("  - " + name)
        if len(rows) > limit:
            print("  … 另有 %d 个" % (len(rows) - limit))


def make_plan(source: Path, live: Path, backup_root: Path, names: list[str] | None = None,
              remove_names: list[str] | None = None) -> Path:
    report = compare(source, live, names)
    verify_files = sorted(set(names)) if names is not None else None
    changes = sorted(set(report["missing"]) | set(report["different"]))
    removals = sorted(set(remove_names or []))
    for name in removals:
        if any(part in IGNORED_PARTS for part in Path(name).parts):
            raise ValueError("不能删除运行数据：" + name)
        if target(source, name).exists():
            raise ValueError("源码仍包含待删除文件：" + name)
        if not target(live, name).is_file():
            raise FileNotFoundError("运行目录缺少待删除文件：" + name)
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    directory = backup_root / ("runtime-sync-" + stamp)
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in changes + removals:
        source_path = target(source, name)
        live_path = target(live, name)
        before = live_path.read_bytes() if live_path.is_file() else None
        if before is not None:
            backup = target(directory / "before", name)
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(before)
            if backup.read_bytes() != before:
                raise OSError("备份校验失败：" + name)
        rows.append({
            "path": name,
            "before": sha(before) if before is not None else None,
            "after": None if name in removals else sha(source_path.read_bytes()),
        })
    plan = {
        "source": str(source),
        "live": str(live),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verify_files": verify_files,
        "changes": rows,
        "extra_in_live": report["extra"],
    }
    path = directory / "plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("已生成同步计划：%s" % path)
    print_report(report)
    return path


def apply_plan(plan_path: Path) -> None:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    source = Path(plan["source"])
    live = Path(plan["live"])
    rows = plan["changes"]
    for row in rows:
        source_path = target(source, row["path"])
        live_path = target(live, row["path"])
        after = None if row["after"] is None else sha(source_path.read_bytes())
        if after != row["after"] or (after is None and source_path.exists()):
            raise ValueError("源码在生成计划后变化：" + row["path"])
        before = sha(live_path.read_bytes()) if live_path.is_file() else None
        if before != row["before"]:
            raise ValueError("运行文件在生成计划后变化：" + row["path"])
    for row in rows:
        source_path = target(source, row["path"])
        live_path = target(live, row["path"])
        if row["after"] is None:
            live_path.unlink()
            continue
        live_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = live_path.with_name(live_path.name + ".companion-sync.tmp")
        shutil.copy2(source_path, temporary)
        os.replace(temporary, live_path)
    verify_files = plan.get("verify_files")
    if verify_files is None:
        verify_files = repository_files(source)
    elif type(verify_files) is not list or any(type(name) is not str for name in verify_files):
        raise ValueError("同步计划的校验文件列表无效")
    mismatches = [name for name in verify_files
                  if target(source, name).read_bytes() != target(live, name).read_bytes()]
    mismatches.extend(row["path"] for row in rows
                      if row["after"] is None and target(live, row["path"]).exists())
    if mismatches:
        raise AssertionError("同步后仍有差异：" + "、".join(mismatches))
    print("已同步 %d 个文件；源码与运行目录一致。" % len(rows))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查差异（默认）")
    parser.add_argument("--plan", action="store_true", help="生成带备份的同步计划，不写入运行目录")
    parser.add_argument("--apply", action="store_true", help="按计划写入运行目录")
    parser.add_argument("--plan-path", type=Path, help="要应用的 plan.json")
    parser.add_argument("--include", action="append", dest="include_files",
                        help="只检查或计划同步指定的源码相对路径；可重复传入")
    parser.add_argument("--remove", action="append", dest="remove_files",
                        help="计划删除运行目录中已从源码移除的指定文件；可重复传入")
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--live", type=Path, default=DEFAULT_LIVE)
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    args = parser.parse_args(argv)

    if not (args.source / ".git").exists():
        print("源码目录不是 Git 仓库：%s" % args.source, file=sys.stderr)
        return 2
    if not args.live.is_dir():
        print("运行目录不存在：%s" % args.live, file=sys.stderr)
        return 2
    if args.apply:
        if args.plan_path is None:
            print("--apply 需要同时指定 --plan-path", file=sys.stderr)
            return 2
        if args.include_files or args.remove_files:
            print("--apply 使用计划文件中的校验范围，不要传入 --include 或 --remove", file=sys.stderr)
            return 2
        apply_plan(args.plan_path)
        return 0
    if args.plan:
        make_plan(args.source, args.live, args.backup_root, args.include_files, args.remove_files)
        return 0
    if args.remove_files:
        print("--remove 只能与 --plan 一起使用", file=sys.stderr)
        return 2
    report = compare(args.source, args.live, args.include_files)
    print_report(report)
    return 1 if report["missing"] or report["different"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
