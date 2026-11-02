from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CompanionDeploymentSkillTests(unittest.TestCase):
    def test_skill_is_packaged_with_valid_frontmatter(self):
        path = ROOT / "skills" / "companion-deployment" / "SKILL.md"
        self.assertTrue(path.is_file(), "deployment skill must be packaged")
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        header, body = text.split("\n---\n", 1)
        self.assertIn("name: companion-deployment", header)
        self.assertIn("description:", header)
        for section in ("StoragePathError", "target window", "TTS", "MiMo", "role_bindings",
                        "Ask before changing anything", "Wait for an explicit"):
            self.assertIn(section, body)

    def test_intake_reference_is_packaged(self):
        path = ROOT / "skills" / "companion-deployment" / "references" / "intake.md"
        self.assertTrue(path.is_file())
        self.assertIn("Confirmation template", path.read_text(encoding="utf-8"))

    def test_diagnostics_reference_is_packaged(self):
        path = ROOT / "skills" / "companion-deployment" / "references" / "diagnostics.md"
        self.assertTrue(path.is_file())
        self.assertIn("host/status", path.read_text(encoding="utf-8"))

    def test_release_builder_includes_the_skills_directory(self):
        from tools.build_v2_release import CORE_DIRS
        self.assertIn("skills", CORE_DIRS)
