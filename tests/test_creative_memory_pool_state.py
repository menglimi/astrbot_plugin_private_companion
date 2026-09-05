from __future__ import annotations

from astrbot_plugin_private_companion.constants import CREATIVE_MEMORY_MAX_ENTRIES
from astrbot_plugin_private_companion.creative import CreativeMixin


class _Harness(CreativeMixin):
    pass


def test_restart_normalizes_memory_pool_ownership_and_quota() -> None:
    project = {"id": "owned", "creative_memory_pool": []}
    pool = project["creative_memory_pool"]
    pool.append({"id": "foreign", "project_id": "other", "content": "wrong owner", "importance": 5, "created_at": 9999})
    pool.append({"id": "legacy", "content": "legacy local entry", "importance": 4, "created_at": 9998})
    for index in range(CREATIVE_MEMORY_MAX_ENTRIES + 5):
        pool.append({
            "id": f"local-{index}",
            "project_id": "owned",
            "content": f"local {index}",
            "importance": 1,
            "created_at": index,
        })

    normalized = _Harness()._get_or_create_memory_pool(project)

    assert normalized is pool
    assert len(normalized) == CREATIVE_MEMORY_MAX_ENTRIES
    assert all(entry["project_id"] == "owned" for entry in normalized)
    assert "foreign" not in {entry["id"] for entry in normalized}
    assert "legacy" in {entry["id"] for entry in normalized}
