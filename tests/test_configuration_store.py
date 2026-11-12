"""The side configuration store copies the old rows once and stays authoritative."""

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from companion_v2.configuration.store import open_configuration_store
from companion_v2.storage.runtime import StorageRuntime


NOW = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc).isoformat()
INSTALLATION = "configuration-store-test"


class ConfigurationStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-config-store-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "runtime"
        self.runtime = StorageRuntime.open(data_root=self.root, installation_id=INSTALLATION)
        self.addCleanup(self.runtime.close)
        target = {"installation_id": INSTALLATION, "layer": "installation"}
        with self.runtime.transaction() as connection:
            connection.execute("INSERT INTO configuration_targets VALUES (?, ?, ?, ?)",
                ("target-1", json.dumps(target), 1, NOW))
            connection.execute("INSERT INTO configuration_values VALUES (?, ?, ?, ?)",
                ("target-1", "output.prompt", json.dumps("keep me"), 1))
            connection.execute("INSERT INTO configuration_consumers VALUES (?, ?, ?)",
                ("model_gateway", 2, 0))
            connection.execute(
                """INSERT INTO operations(operation_id, owner, installation_id, platform, scope_kind,
                    native_scope_id, bot_id, account_id, conversation_id, session_id, lineage_id,
                    operation_revision, idempotency_key, parameter_hash, state, commit_state,
                    created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("op-1", "configuration", INSTALLATION, "onebot", "private", "native-1", "bot-1",
                 "account-1", "conversation-1", "session-1", "lineage-1", 1, "key-1", "hash-1",
                 "completed", "committed", NOW, NOW))
            connection.execute(
                "INSERT INTO configuration_changes(operation_id, target_key, group_id, changed, result_json, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("op-1", "target-1", "output", 1, json.dumps({"revision": 1}), NOW))

    def test_existing_rows_are_copied_into_the_side_store(self):
        store = open_configuration_store(self.runtime)
        self.assertIsNotNone(store)
        self.addCleanup(store.close)
        value = store.read_one("SELECT value_json FROM configuration_values WHERE target_key = ? AND field_id = ?",
                               ("target-1", "output.prompt"))
        self.assertEqual(json.loads(value["value_json"]), "keep me")
        consumer = store.read_one("SELECT generation FROM configuration_consumers WHERE consumer_id = ?",
                                  ("model_gateway",))
        self.assertEqual(consumer["generation"], 2)
        change = store.read_one("SELECT operation_id FROM configuration_changes WHERE operation_id = ?", ("op-1",))
        self.assertEqual(change["operation_id"], "op-1")
        operation = store.read_one("SELECT owner FROM operations WHERE operation_id = ?", ("op-1",))
        self.assertEqual(operation["owner"], "configuration")

    def test_a_second_open_does_not_copy_twice_or_change_values(self):
        first = open_configuration_store(self.runtime)
        self.assertIsNotNone(first)
        first.close()
        second = open_configuration_store(self.runtime)
        self.assertIsNotNone(second)
        self.addCleanup(second.close)
        count = second.read_one("SELECT COUNT(*) AS n FROM configuration_values", ())
        self.assertEqual(count["n"], 1)


if __name__ == "__main__":
    unittest.main()
