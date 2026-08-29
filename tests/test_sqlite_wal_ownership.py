from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
METHOD_NAMES = (
    "_companion_owned_sqlite_path",
    "_sqlite_wal_candidate_paths",
    "_apply_sqlite_wal_to_file",
    "_apply_sqlite_wal_optimizations",
)


def _load_methods() -> dict[str, Any]:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin"
    )
    selected = [
        copy.deepcopy(node)
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in METHOD_NAMES
    ]
    for node in selected:
        node.decorator_list = []
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Any": Any,
        "Path": Path,
        "asyncio": asyncio,
        "sqlite3": sqlite3,
        "logger": types.SimpleNamespace(
            info=lambda *_args, **_kwargs: None,
            warning=lambda *_args, **_kwargs: None,
        ),
        "_single_line": lambda value, limit=240: " ".join(
            str(value or "").split()
        )[:limit],
    }
    exec(compile(module, str(ROOT / "main.py"), "exec"), namespace)
    return {name: namespace[name] for name in METHOD_NAMES}


METHODS = _load_methods()


class _WalHost:
    _companion_owned_sqlite_path = METHODS["_companion_owned_sqlite_path"]
    _sqlite_wal_candidate_paths = METHODS["_sqlite_wal_candidate_paths"]
    _apply_sqlite_wal_to_file = METHODS["_apply_sqlite_wal_to_file"]
    _apply_sqlite_wal_optimizations = METHODS["_apply_sqlite_wal_optimizations"]


def _create_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("CREATE TABLE sample(value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample(value) VALUES('unchanged')")
        connection.commit()
    finally:
        connection.close()


def _journal_mode(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        return str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    finally:
        connection.close()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SqliteWalOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_optimizer_tunes_only_registered_companion_databases(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "companion"
            profiles_dir = data_dir / "persona_profiles"
            own_paths = {
                data_dir / "companions.db",
                profiles_dir / "secondary.db",
                data_dir / "req041_migration_control.db",
                data_dir / "req041_migration_outbox.db",
                data_dir / "req041_relationship.db",
            }
            for path in own_paths:
                _create_database(path)

            external_paths = {
                root / "astrbot" / "data_v4.db",
                root
                / "astrbot"
                / "plugin_data"
                / "astrbot_plugin_livingmemory"
                / "conversations.db",
                root
                / "astrbot"
                / "plugin_data"
                / "astrbot_plugin_livingmemory"
                / "livingmemory.db",
                root
                / "astrbot"
                / "plugin_data"
                / "astrbot_plugin_livingmemory"
                / "livingmemory_graph_documents.db",
                root / "astrbot" / "knowledge_base" / "kb.db",
            }
            for path in external_paths:
                _create_database(path)

            escaped_profile = profiles_dir / "escaped.db"
            try:
                escaped_profile.symlink_to(next(iter(external_paths)))
            except OSError:
                escaped_profile = None

            host = _WalHost()
            host.data_dir = str(data_dir)
            host.storage_backend = "sqlite"
            host.store_manager = types.SimpleNamespace(
                backend=types.SimpleNamespace(db_path=data_dir / "companions.db")
            )
            host._persona_profiles_dir = str(profiles_dir)
            host.req041_migration_coordinator = types.SimpleNamespace(
                path=data_dir / "req041_migration_control.db"
            )
            host.req041_migration_outbox = types.SimpleNamespace(
                path=data_dir / "req041_migration_outbox.db"
            )
            host.req041_relationship_store = types.SimpleNamespace(
                path=data_dir / "req041_relationship.db"
            )

            before = {
                path: (_digest(path), _journal_mode(path))
                for path in external_paths
            }
            self.assertTrue(all(mode == "delete" for _digest_value, mode in before.values()))

            self.assertEqual(own_paths, set(host._sqlite_wal_candidate_paths()))
            await host._apply_sqlite_wal_optimizations()

            self.assertTrue(all(_journal_mode(path) == "wal" for path in own_paths))
            self.assertEqual(
                before,
                {
                    path: (_digest(path), _journal_mode(path)) for path in external_paths
                },
            )
            self.assertTrue(
                all(
                    not path.with_name(path.name + "-wal").exists()
                    for path in external_paths
                )
            )
            if escaped_profile is not None:
                self.assertNotIn(escaped_profile, host._sqlite_wal_candidate_paths())

    async def test_external_active_store_is_rejected_even_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "companion"
            data_dir.mkdir()
            external = root / "data_v4.db"
            _create_database(external)
            before = (_digest(external), _journal_mode(external))
            self.assertEqual("delete", before[1])

            host = _WalHost()
            host.data_dir = str(data_dir)
            host.storage_backend = "sqlite"
            host.store_manager = types.SimpleNamespace(
                backend=types.SimpleNamespace(db_path=external)
            )
            host._persona_profiles_dir = str(data_dir / "persona_profiles")

            self.assertEqual([], host._sqlite_wal_candidate_paths())
            with self.assertRaisesRegex(ValueError, "sqlite_path_not_companion_owned"):
                host._apply_sqlite_wal_to_file(external)
            await host._apply_sqlite_wal_optimizations()

            self.assertEqual(before, (_digest(external), _journal_mode(external)))

    def test_wal_path_has_no_host_engine_discovery(self) -> None:
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        owner = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin"
        )
        method_names = {
            node.name
            for node in owner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        self.assertNotIn("_iter_possible_sqlalchemy_engines", method_names)
        self.assertNotIn("_install_sqlite_wal_engine_hooks", method_names)
        self.assertNotIn("_apply_sqlite_pragmas_to_dbapi_connection", method_names)
        wal_source = "\n".join(
            ast.unparse(node)
            for node in owner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in METHOD_NAMES
        )
        self.assertNotIn("sqlalchemy", wal_source.lower())
        self.assertNotIn("gc.get_objects", wal_source)
        self.assertNotIn("get_astrbot_data_path", wal_source)


if __name__ == "__main__":
    unittest.main()
