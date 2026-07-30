# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PanelPersistenceFailureUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")

    def test_import_keeps_preview_when_config_persistence_fails(self) -> None:
        block = self.script.split("async function applyConfigImport() {", 1)[1].split(
            "\n}\n\nasync function restoreConfigBackup", 1
        )[0]

        self.assertLess(
            block.index("if (!actionResultPersisted(result)) return result;"),
            block.index("state.configImportPreview = null;"),
        )
        self.assertIn("return result;", block)
        self.assertNotIn('showToast("配置已导入', block)
        self.assertIn(
            'runAction(applyConfigImport, "配置已导入，已自动备份导入前状态"',
            self.script,
        )

    def test_restore_does_not_report_success_after_persistence_failure(self) -> None:
        block = self.script.split("async function restoreConfigBackup(id) {", 1)[1].split(
            "\n}\n\nasync function loadImageCache", 1
        )[0]

        self.assertIn("if (!actionResultPersisted(result)) return result;", block)
        self.assertIn("return result;", block)
        self.assertNotIn('showToast("已从备份恢复")', block)
        self.assertIn(
            'runAction(() => restoreConfigBackup(button.dataset.configRestore), "已从备份恢复"',
            self.script,
        )

    def test_experimental_toggles_rollback_after_request_or_save_failure(self) -> None:
        self.assertGreaterEqual(
            self.script.count("const previousValue = Object.prototype.hasOwnProperty.call(state.featureDraft"),
            2,
        )
        self.assertIn("state.featureDraft[key] = previousValue;", self.script)
        self.assertIn("reflectExperimentalToggleChange(key, previousValue);", self.script)
        self.assertIn("state.featureDraft[toggleKey] = previousValue;", self.script)
        self.assertIn("reflectExperimentalToggleChange(toggleKey, previousValue);", self.script)
        self.assertGreaterEqual(self.script.count("if (actionResultPersisted(result))"), 3)


if __name__ == "__main__":
    unittest.main()
