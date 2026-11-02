"""Enable one installed capability for a role, through the capability repository.

背景：手机房间端点的准入要求 `capability_role_selections` 里有该扩展的 enabled 行；
Together 目前没有「角色启用」入口，所以手机端永远是 `mobile_together_not_authorized`。
这个脚本按正式接口补上那一行，默认 dry-run，`--apply` 前备份数据库。

用法（建议先停宿主）：

    python tools/enable_role_capability.py --data-root <runtime 目录> \
        --installation-id <installation> --role-id role-... --bot-id bot-... \
        --extension together
    python tools/enable_role_capability.py ... --apply
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from companion_v2.capabilities.models import CapabilityDesiredState  # noqa: E402
from companion_v2.capabilities.repositories import CapabilityRepository  # noqa: E402
from companion_v2.foundation.refs import SubjectKind, SubjectRef  # noqa: E402
from companion_v2.storage.runtime import StorageRuntime  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--database", type=Path, help="companion-v2.sqlite3；缺省用 data-root 下的同名文件")
    parser.add_argument("--installation-id", required=True)
    parser.add_argument("--role-id", required=True)
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--extension", default="together")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.data_root.is_dir():
        parser.error("--data-root 必须存在")

    runtime = StorageRuntime.open(data_root=args.data_root, installation_id=args.installation_id)
    try:
        repository = CapabilityRepository(runtime)
        installed = repository.installation(extension_id=args.extension)
        if not installed.is_success:
            raise SystemExit("扩展未安装：" + installed.failure.code)
        record = installed.unwrap()
        if record.desired_state is not CapabilityDesiredState.ACTIVE:
            raise SystemExit("扩展不是 active：" + record.desired_state.value)
        row = runtime.read_one(
            "SELECT revision, enabled FROM capability_role_selections "
            "WHERE extension_id = ? AND role_kind = ? AND role_id = ? AND bot_id = ?",
            (args.extension, SubjectKind.BOT.value, args.role_id, args.bot_id))
        current = "无" if row is None else f"enabled={row['enabled']} revision={row['revision']}"
        print(f"扩展 {args.extension} {record.package_version}，角色行：{current}")
        if row is not None and row["enabled"]:
            print("无需修改：该角色已经启用。")
            return 0
        if not args.apply:
            print("[dry-run] 将写入 capability_role_selections(enabled=1)。确认后加 --apply。")
            return 0
        database = args.database or (args.data_root / "companion-v2.sqlite3")
        if not database.is_file():
            raise SystemExit("找不到数据库文件：" + str(database))
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = database.with_name(database.name + ".bak-role-enable-" + stamp)
        shutil.copy2(database, backup)
        print("已备份：", backup)
        repository.set_role_enabled(
            extension_id=args.extension,
            role=SubjectRef(args.installation_id, SubjectKind.BOT, args.role_id),
            bot_id=args.bot_id, enabled=True,
            expected_revision=row["revision"] if row is not None else 0,
            observed_at=datetime.now(timezone.utc)).unwrap()
        print("已启用。重启宿主后手机端应能通过房间准入。")
        return 0
    finally:
        runtime.close()


if __name__ == "__main__":
    sys.exit(main())
