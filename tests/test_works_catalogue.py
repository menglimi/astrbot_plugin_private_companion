"""The works catalogue lists only active works of the requested role."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from companion_v2.content.catalogue import content_database, read_works

COLUMNS = ("owner_key, work_id, owner_json, title, work_type, author_json, "
           "audience_json, revision, head_version, state, origin_json, created_at")


def _database(root: Path) -> Path:
    path = content_database(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(f"CREATE TABLE content_v2_works ({COLUMNS})")
    connection.commit()
    connection.close()
    return path


def _insert(path: Path, *, work_id: str, title: str, role_id: str, state: str = "active",
            installation_id: str = "installation-1", bot_id: str = "bot-1",
            created_at: str = "2026-11-09T10:00:00+00:00") -> None:
    owner = json.dumps({"installation_id": installation_id, "bot_id": bot_id,
                        "role_id": role_id, "owner_key": "relationship:person"})
    connection = sqlite3.connect(path)
    connection.execute("INSERT INTO content_v2_works VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("owner-" + work_id, work_id, owner, title, "novel", "{}", "{}", 2,
                        "version-1", state, "{}", created_at))
    connection.commit()
    connection.close()


class WorksCatalogueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="companion-works-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_only_active_works_of_the_requested_role_are_returned(self):
        path = _database(self.root)
        _insert(path, work_id="work-a", title="第一本", role_id="role-1")
        _insert(path, work_id="work-b", title="撤回的", role_id="role-1", state="retracted")
        _insert(path, work_id="work-c", title="别的角色", role_id="role-2")
        _insert(path, work_id="work-d", title="别的账号", role_id="role-1", bot_id="bot-2")
        result = read_works(path, installation_id="installation-1", bot_id="bot-1", role_id="role-1")
        self.assertTrue(result["available"])
        self.assertEqual(result["count"], 1)
        self.assertEqual([work["title"] for work in result["works"]], ["第一本"])
        self.assertEqual(result["works"][0]["work_id"], "work-a")
        self.assertEqual(result["works"][0]["revision"], 2)

    def test_missing_database_reports_unavailable_instead_of_raising(self):
        result = read_works(content_database(self.root), installation_id="installation-1",
                            bot_id="bot-1", role_id="role-1")
        self.assertEqual(result, {"available": False, "count": 0, "works": []})

    def test_limit_is_bounded_and_newest_first(self):
        path = _database(self.root)
        for index in range(3):
            _insert(path, work_id=f"work-{index}", title=f"作品 {index}", role_id="role-1",
                    created_at=f"2026-11-0{index + 1}T10:00:00+00:00")
        result = read_works(path, installation_id="installation-1", bot_id="bot-1",
                            role_id="role-1", limit=2)
        self.assertEqual(result["count"], 2)
        self.assertEqual([work["work_id"] for work in result["works"]], ["work-2", "work-1"])


if __name__ == "__main__":
    unittest.main()
