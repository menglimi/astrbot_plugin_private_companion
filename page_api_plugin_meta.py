# -*- coding: utf-8 -*-
"""plugin_meta 域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 90 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import re
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from pathlib import Path
from typing import Any



class PrivateCompanionPageApiPluginMetaMixin:
    """plugin_meta 域（从 PrivateCompanionPageApi 拆出）。"""


    def _astrbot_config_candidate_paths(self) -> list[Path]:
        root = Path(get_astrbot_data_path())
        home_root = Path.home() / ".astrbot"
        candidate_roots = [
            root,
            home_root / "data",
            home_root / "backend" / "data",
            home_root / "backend" / "app" / "data",
        ]
        paths: list[Path] = []
        seen: set[str] = set()

        def add_path(path: Path) -> None:
            try:
                key = str(path.resolve()).lower()
            except Exception:
                key = str(path).lower()
            if key in seen:
                return
            seen.add(key)
            paths.append(path)

        for candidate_root in candidate_roots:
            add_path(candidate_root / "cmd_config.json")
            config_dir = candidate_root / "config"
            if config_dir.exists():
                for path in sorted(config_dir.glob("abconf_*.json")):
                    add_path(path)
        return paths

    def _plugin_version(self) -> str:
        for source in (self.plugin, getattr(self.plugin, "metadata", None)):
            for attr in ("version", "__version__", "plugin_version"):
                value = getattr(source, attr, None)
                if value:
                    return str(value).strip()
        try:
            metadata_path = Path(__file__).with_name("metadata.yaml")
            text = metadata_path.read_text(encoding="utf-8")
            match = re.search(r"(?m)^version:\s*['\"]?([^'\"\s#]+)", text)
            if match:
                return match.group(1).strip()
        except Exception:
            pass
        return "unknown"

    def _strip_runtime_data(self, value: Any) -> Any:
        runtime_keys = {
            "recent_messages",
            "recent_message_ids",
            "recent_replies",
            "recent_group_messages",
            "proactive_sending",
            "proactive_audit_log",
            "proactive_candidates",
            "pending_followup_event",
            "pending_timer_events",
            "pending_atrelay_requests",
            "suspended_proactive",
            "input_status",
            "current_input_status",
            "recall_message_cache",
            "image_cache",
            "visual_summary_cache",
            "token_usage",
            "token_stats",
            "troubleshooting_records",
            "maintenance_records",
        }
        runtime_prefixes = (
            "recent_",
            "pending_",
            "last_message",
            "last_reply",
            "last_sent",
            "last_proactive",
            "cooldown_",
            "session_",
        )
        if isinstance(value, dict):
            cleaned: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if key_text in runtime_keys or any(key_text.startswith(prefix) for prefix in runtime_prefixes):
                    continue
                if key_text.endswith("_cache") or key_text.endswith("_audit_log"):
                    continue
                cleaned[key_text] = self._strip_runtime_data(item)
            return cleaned
        if isinstance(value, list):
            return [self._strip_runtime_data(item) for item in value]
        return value
