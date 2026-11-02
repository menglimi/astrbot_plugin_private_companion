"""V1 迁移发现：三个共用主状态来源必须走 ``profile_source.primary_profile()``。

``companions.json`` 只是 V1 的一种存储布局，不是存储本身。``storage_backend`` /
``storage_sqlite_path`` / ``.storage-backend-state.json`` 指向的那份文件或库才是数据
导入页真正会读的东西。发现阶段按目录里写死的文件名判存在性时两个方向都会错：

* 纯 SQLite 用户：库在、JSON 不在 → 三个来源全被报「没有这份数据」；
* JSON 还在、配置指向的库坏了 → 三个来源全被报「已就绪」，导入时才拿到 0 条。

用真的临时目录而不是 mock：判错的正是文件系统这一层。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
import tempfile
import unittest

from companion_v2.legacy.migration import LegacyMigration, MigrationState

PROFILE_SOURCES = ("private.core_profiles",
                   "private.authoritative_memory",
                   "private.group_relationship_graph")


class ProfileDiscoveryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def write_json(self, name: str, payload) -> pathlib.Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8", newline="\n")
        return path

    def write_sqlite(self, name: str, payload) -> pathlib.Path:
        """写一份 V1 的旧版主状态库（``store_sections``、user_version 1、无 store_meta）。"""
        path = self.root / name
        connection = sqlite3.connect(path)
        try:
            connection.executescript("""
                CREATE TABLE store_sections (
                    section_name TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    checksum TEXT NOT NULL,
                    schema_version INTEGER NOT NULL
                );
                PRAGMA user_version = 1;
            """)
            for section, value in payload.items():
                raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                connection.execute("INSERT INTO store_sections VALUES (?, ?, 1.0, ?, 1)",
                                   (section, raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()))
            connection.commit()
        finally:
            connection.close()
        return path

    def marker(self, backend: str, sqlite_path: str = "") -> None:
        self.write_json(".storage-backend-state.json",
                        {"backend": backend, "sqlite_path": sqlite_path})

    def findings(self, **kwargs) -> dict:
        result = LegacyMigration(v1_root=self.root, **kwargs).discover()
        self.assertTrue(result.is_success, result.failure)
        return {item.source_id: item for item in result.unwrap()}

    def assert_all_sources(self, findings: dict, state: MigrationState) -> None:
        for source_id in PROFILE_SOURCES:
            with self.subTest(source_id=source_id):
                self.assertEqual(findings[source_id].state, state.value)


class SqliteOnlyProfileTests(ProfileDiscoveryTestCase):
    def test_sqlite_only_installation_is_discovered(self) -> None:
        database = self.write_sqlite("companions.db", {"groups": {"db-group": {}}})
        self.marker("sqlite")

        findings = self.findings()

        self.assert_all_sources(findings, MigrationState.DATA_READY)
        core = findings["private.core_profiles"]
        self.assertEqual(core.paths, ("companions.db",))
        self.assertEqual(core.byte_size, database.stat().st_size)

    def test_v1_configuration_selects_the_sqlite_store_without_a_marker(self) -> None:
        # 没有后端标记时，V1 插件配置里的 storage_backend 是唯一的权威。
        self.write_json("companions.json", {"groups": {"stale-json": {}}})
        self.write_sqlite("companions.db", {"groups": {"db-group": {}}})

        findings = self.findings(v1_configuration={"basic_config": {"storage_backend": "sqlite"}})

        self.assert_all_sources(findings, MigrationState.DATA_READY)
        self.assertEqual(findings["private.core_profiles"].paths, ("companions.db",))


class UnreadableProfileTests(ProfileDiscoveryTestCase):
    def test_json_present_with_a_broken_sqlite_store_is_reported_unreadable(self) -> None:
        # 回归锁：改前 companions.json 读得出来，三个来源全报 data_ready，而导入页
        # 按配置去读 companions.db，只会拿到 0 条。
        self.write_json("companions.json", {"groups": {"stale-json": {}}})
        (self.root / "companions.db").write_bytes(b"definitely not sqlite")
        self.marker("sqlite")

        findings = self.findings()

        self.assert_all_sources(findings, MigrationState.UNREADABLE)
        core = findings["private.core_profiles"]
        self.assertEqual(core.paths, ("companions.db",))
        self.assertIn("数据存在但读不出来", core.detail)
        self.assertIn("profile_database_unreadable", core.detail)

    def test_backend_authority_conflict_is_reported_unreadable(self) -> None:
        self.write_json("companions.json", {"groups": {"json-group": {}}})
        self.write_sqlite("companions.db", {"groups": {"db-group": {}}})
        self.marker("sqlite")

        findings = self.findings(v1_configuration={"storage_backend": "json"})

        self.assert_all_sources(findings, MigrationState.UNREADABLE)
        self.assertIn("core_profile_authority_conflict",
                      findings["private.authoritative_memory"].detail)

    def test_a_store_above_the_read_limit_is_reported_unreadable(self) -> None:
        # 64 MiB 上限本身不在本改动范围；锁定的是「解析器读不出来 ⇒ 发现阶段也报读不到」。
        from companion_v2.legacy import profile_source

        self.write_json("companions.json", {"groups": {"json-group": {}}})
        original = profile_source.MAX_PROFILE_JSON_BYTES
        self.addCleanup(setattr, profile_source, "MAX_PROFILE_JSON_BYTES", original)
        profile_source.MAX_PROFILE_JSON_BYTES = 16

        self.assert_all_sources(self.findings(), MigrationState.UNREADABLE)

    def test_missing_selected_store_does_not_fall_back_to_a_stale_json_file(self) -> None:
        # JSON 是被配置放弃的旧副本：不猜它，也不把「读不到」说成「已就绪」。
        self.write_json("companions.json", {"groups": {"stale-json": {}}})
        self.marker("sqlite")

        self.assert_all_sources(self.findings(), MigrationState.ABSENT)

    def test_json_only_the_strict_reader_rejects_is_reported_unreadable(self) -> None:
        # 发现页过去用本模块的宽松 read_json（接受重复键与 NaN），导入页用严格解析器。
        # 两张页面现在共用同一个判断：严格解析器不收的，发现页也说读不到。
        cases = {
            "duplicate_key": '{"groups": {}, "groups": {}}',
            "nan": '{"groups": {}, "score": NaN}',
            "not_an_object": '["groups"]',
        }
        for case, raw in cases.items():
            with self.subTest(case=case):
                (self.root / "companions.json").write_text(raw, encoding="utf-8", newline="\n")

                self.assert_all_sources(self.findings(), MigrationState.UNREADABLE)


class HealthyProfileTests(ProfileDiscoveryTestCase):
    def test_healthy_json_installation_is_unchanged(self) -> None:
        path = self.write_json("companions.json", {"groups": {"json-group": {}}})

        findings = self.findings()

        self.assert_all_sources(findings, MigrationState.DATA_READY)
        core = findings["private.core_profiles"]
        self.assertEqual(core.paths, ("companions.json",))
        self.assertEqual(core.byte_size, path.stat().st_size)
        self.assertEqual(core.detail, "数据导入器已就绪；请在数据迁移步骤中预览并选择目标。")

    def test_secondary_persona_directory_is_still_named_beside_the_primary_store(self) -> None:
        # persona_profiles 不是主状态，但它属于这一条来源；换成权威解析后不能弄丢。
        self.write_json("companions.json", {"groups": {"json-group": {}}})
        (self.root / "persona_profiles").mkdir()
        (self.root / "persona_profiles" / "persona-a.json").write_text(
            json.dumps({"bot_diaries": []}), encoding="utf-8", newline="\n")

        core = self.findings()["private.core_profiles"]

        self.assertEqual(core.state, MigrationState.DATA_READY.value)
        self.assertEqual(core.paths, ("companions.json", "persona_profiles"))

    def test_persona_only_installation_still_reports_the_profiles_it_has(self) -> None:
        # 没有主状态文件、只有次人格档案：这一条来源仍有可导入的数据，另两条没有。
        directory = self.root / "persona_profiles"
        directory.mkdir()
        (directory / "persona-a.json").write_text(
            json.dumps({"bot_diaries": []}), encoding="utf-8", newline="\n")

        findings = self.findings()

        self.assertEqual(findings["private.core_profiles"].state, MigrationState.DATA_READY.value)
        self.assertEqual(findings["private.core_profiles"].paths, ("persona_profiles",))
        self.assertEqual(findings["private.authoritative_memory"].state,
                         MigrationState.ABSENT.value)
        self.assertEqual(findings["private.group_relationship_graph"].state,
                         MigrationState.ABSENT.value)


class RepeatedDiscoveryTests(ProfileDiscoveryTestCase):
    def test_repeated_discovery_is_idempotent(self) -> None:
        self.write_sqlite("companions.db", {"groups": {"db-group": {}}})
        self.marker("sqlite")
        migration = LegacyMigration(v1_root=self.root)

        first = [item.document() for item in migration.discover().unwrap()]

        self.assertEqual([item.document() for item in migration.discover().unwrap()], first)
        # 第二个实例从空缓存开始，结论必须一致。
        self.assertEqual([item.document() for item in LegacyMigration(v1_root=self.root)
                          .discover().unwrap()], first)

    def test_unreadable_discovery_is_repeated_identically(self) -> None:
        self.write_json("companions.json", {"groups": {"json-group": {}}})
        (self.root / "companions.db").write_bytes(b"broken")
        self.marker("sqlite")
        migration = LegacyMigration(v1_root=self.root)

        first = [item.document() for item in migration.discover().unwrap()]

        self.assertEqual([item.document() for item in migration.discover().unwrap()], first)
        self.assertEqual({item["source_id"]: item["state"] for item in first}
                         ["private.core_profiles"], MigrationState.UNREADABLE.value)

    def test_one_discovery_pass_reads_the_store_once(self) -> None:
        # 三个来源问的是同一个问题：一次发现只读一遍快照，而不是三遍。
        from companion_v2.legacy import profile_source

        self.write_json("companions.json", {"groups": {"json-group": {}}})
        reads = []
        original = profile_source.read_json

        def counting_read(path, *args, **kwargs):
            reads.append(pathlib.Path(str(path)).name)
            return original(path, *args, **kwargs)

        profile_source.read_json = counting_read
        self.addCleanup(setattr, profile_source, "read_json", original)

        self.findings()

        self.assertEqual(reads.count("companions.json"), 1)


if __name__ == "__main__":
    unittest.main()
