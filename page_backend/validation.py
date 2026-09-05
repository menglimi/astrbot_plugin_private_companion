from __future__ import annotations

from pathlib import Path


def normalized_backup_name(value: object) -> str:
    """Preserve the legacy basename policy while centralising validation."""
    safe_name = Path(str(value or "")).name
    if not safe_name or not safe_name.endswith(".json"):
        raise ValueError("没有找到要恢复的备份文件")
    return safe_name
