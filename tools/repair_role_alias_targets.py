"""Merge configuration written under a legacy role display name into its real role.

旧配置可能把角色显示名当成 role_id 存进 configuration_targets，而当前绑定使用的是
规范 id（role-…）。同一个角色的设置在库里就分成两份：页面按规范 id 读取，看到的是
另一份（常常是空的），表现为“人格被清空”。

本脚本只做一件事：把显示名 target 下“规范 target 还没有的字段”补过去。
- 默认 dry-run，只打印计划；`--apply` 才写库，并在写前备份数据库文件。
- 规范 target 已有的字段一律保留，不覆盖；值冲突只报告，不合并。
- 幂等：重复运行不会重复写入。

用法（建议先停宿主）：

    python tools/repair_role_alias_targets.py --db <companion-v2.sqlite3> \
        --config <astrbot_plugin_private_companion_config.json>
    python tools/repair_role_alias_targets.py ... --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 显示名规则必须和运行时代码一致，不能再抄一份后缀表。
from astrbot_host.identity_derivation import role_display_name


def load_roles(config_path: Path):
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    personas = config.get("multi_host", {}).get("personas", [])
    roles = {}
    for row in personas:
        role_id = str(row.get("role_id") or "").strip()
        bot_id = str(row.get("bot_id") or "").strip()
        persona_id = str(row.get("persona_id") or "").strip()
        if not role_id or not bot_id:
            continue
        roles.setdefault((bot_id, role_id), []).append(persona_id)
    return roles, personas


def target_fields(connection, target_key):
    rows = connection.execute(
        "SELECT field_id, value_json FROM configuration_values WHERE target_key = ? ORDER BY field_id",
        (target_key,)).fetchall()
    return {row["field_id"]: row["value_json"] for row in rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="companion-v2.sqlite3")
    parser.add_argument("--config", type=Path, required=True,
                        help="astrbot_plugin_private_companion_config.json")
    parser.add_argument("--apply", action="store_true", help="写入；缺省只打印计划")
    args = parser.parse_args()
    if not args.db.is_file() or not args.config.is_file():
        parser.error("--db 与 --config 都必须指向存在的文件")

    roles, _personas = load_roles(args.config)
    canonical = {role for (_bot, role) in roles}
    by_display = {}
    for (bot_id, role), persona_ids in roles.items():
        by_display.setdefault((bot_id, role_display_name(role, persona_ids)), []).append(role)

    connection = sqlite3.connect(str(args.db))
    connection.row_factory = sqlite3.Row
    targets = []
    for row in connection.execute("SELECT target_key, target_json, revision, updated_at FROM configuration_targets"):
        target = json.loads(row["target_json"])
        if target.get("layer") not in ("role", "window"):
            continue
        targets.append((row, target))

    plan = []
    for row, target in targets:
        role_id = str(target.get("role_id") or "")
        if not role_id or role_id in canonical:
            continue
        candidates = by_display.get((str(target.get("bot_id") or ""), role_id), [])
        if len(candidates) != 1:
            print(f"跳过 {row['target_key'][:10]}：角色 {role_id!r} 无法唯一对应到已注册角色")
            continue
        destination = next(((other, other_target) for other, other_target in targets
                            if other_target.get("layer") == target.get("layer")
                            and other_target.get("role_id") == candidates[0]
                            and other_target.get("bot_id") == target.get("bot_id")
                            and other_target.get("scope") == target.get("scope")), None)
        if destination is None:
            print(f"跳过 {row['target_key'][:10]}：找不到 {candidates[0]} 在 {target.get('layer')} 层的对应 target")
            continue
        source_values = target_fields(connection, row["target_key"])
        destination_values = target_fields(connection, destination[0]["target_key"])
        missing = {key: value for key, value in source_values.items() if key not in destination_values}
        conflicts = [key for key in source_values if key in destination_values
                     and destination_values[key] != source_values[key]]
        plan.append({"source": row, "target": target, "destination": destination,
                     "missing": missing, "conflicts": conflicts,
                     "source_total": len(source_values), "destination_total": len(destination_values)})

    print(f"发现 {len(plan)} 个显示名角色 target 需要归并：")
    for item in plan:
        source_target = item["target"]
        print(f"  {item['source']['target_key'][:10]} {source_target.get('layer')} "
              f"{source_target.get('role_id')!r} -> {item['destination'][1].get('role_id')!r}："
              f"源 {item['source_total']} 项，待补 {len(item['missing'])} 项，冲突 {len(item['conflicts'])} 项")
        for key in list(item["missing"])[:8]:
            print(f"      + {key}")
        for key in item["conflicts"][:5]:
            print(f"      ! 冲突保留规范值：{key}")
    if not plan:
        return 0
    if not args.apply:
        print("\n[dry-run] 未写入。确认后加 --apply 执行（建议先停宿主）。")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = args.db.with_name(args.db.name + ".bak-role-alias-" + stamp)
    connection.close()
    shutil.copy2(args.db, backup)
    print(f"\n已备份：{backup}")

    connection = sqlite3.connect(str(args.db))
    connection.row_factory = sqlite3.Row
    written = 0
    with connection:  # single transaction
        for item in plan:
            destination_key = item["destination"][0]["target_key"]
            if not item["missing"]:
                continue
            revision = int(item["destination"][0]["revision"] or 0) + 1
            connection.executemany(
                "INSERT INTO configuration_values (target_key, field_id, value_json, revision) VALUES (?,?,?,?)",
                ((destination_key, key, value, revision) for key, value in item["missing"].items()))
            updated = item["destination"][0]["updated_at"]
            connection.execute(
                "UPDATE configuration_targets SET revision = ?, updated_at = ? WHERE target_key = ?",
                (revision, updated, destination_key))
            written += len(item["missing"])
    connection.close()
    print(f"完成：写入 {written} 项。源 target 保留未删除；重启宿主后按规范角色读取即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
