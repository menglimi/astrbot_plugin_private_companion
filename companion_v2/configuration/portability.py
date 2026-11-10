"""Portable configuration values, without any personal data.

"Configuration" here means the registered switch/number/text settings the pages
edit.  Role profiles, relationship notes, memories, journals and other records
are deliberately outside this document: they belong to one installation's data,
not to a shareable settings backup.
"""

from __future__ import annotations

SCHEMA = "companion.config-export.v1"
# 自我页（角色档案）、关系网（关系与相识档案）都不是配置，导入会把别处的资料带进来。
EXCLUDED_SECTIONS = frozenset({"role_profile", "relationship_profile", "person"})


def portable(name: str, sections) -> bool:
    return name in sections and name not in EXCLUDED_SECTIONS


def changes_for(settings, allowed) -> dict:
    """Keep only known short field names; unknown/extra values are ignored."""
    if not isinstance(settings, dict) or not allowed:
        return {}
    return {name: value for name, value in settings.items()
            if isinstance(name, str) and name in allowed}
