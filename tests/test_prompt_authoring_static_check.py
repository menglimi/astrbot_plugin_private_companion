from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "private_companion_ci_static_checks",
    ROOT / "scripts" / "ci_static_checks.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load CI static checks")
CI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CI)


class PromptAuthoringStaticCheckTests(unittest.TestCase):
    def _check_source(self, source: str, *, filename: str = "sample.py") -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / filename
            path.write_text(source, encoding="utf-8")
            CI.check_prompt_authoring(
                Path(directory),
                enforce_allowlist_usage=False,
            )

    def _finding(self, source: str, *, filename: str = "sample.py") -> str:
        with self.assertRaises(SystemExit) as raised:
            self._check_source(source, filename=filename)
        return str(raised.exception)

    def test_canonical_keyword_section_is_accepted(self) -> None:
        self._check_source(
            "from prompt import prompt_section\n"
            "value = prompt_section(key='reply.style', title='回复风格', "
            "source='sample', content='保持自然')\n"
        )

    def test_repository_prompt_authoring_passes_the_allowlist(self) -> None:
        CI.check_prompt_authoring(ROOT)

    def test_legacy_heading_and_conversation_xml_are_rejected(self) -> None:
        heading = self._finding("prompt = '【手写标题】\\n正文'\n")
        xml = self._finding("prompt = '<private_companion_context><section title=\"x\">y</section></private_companion_context>'\n")

        self.assertIn("raw_legacy_heading", heading)
        self.assertIn("【手写标题】", heading)
        self.assertIn("raw_conversation_xml", xml)

    def test_legacy_or_incomplete_prompt_section_calls_are_rejected(self) -> None:
        positional = self._finding("value = prompt_section('标题', '正文')\n")
        incomplete = self._finding("value = prompt_section(title='标题', content='正文')\n")
        direct = self._finding("value = PromptSection('标题', '正文')\n")

        self.assertIn("legacy_prompt_section_call", positional)
        self.assertIn("positional arguments", positional)
        self.assertIn("missing key/title/source", incomplete)
        self.assertIn("direct_prompt_section_constructor", direct)

    def test_split_literal_cannot_hide_a_legacy_heading(self) -> None:
        finding = self._finding("prompt = '【手写' + '标题】正文'\n")
        self.assertIn("raw_legacy_heading", finding)
        self.assertIn("【手写标题】", finding)

    def test_logger_and_regex_pattern_literals_are_not_prompt_authoring(self) -> None:
        self._check_source(
            "import logging, re\n"
            "logger = logging.getLogger(__name__)\n"
            "logger.info('【日志标签】 message')\n"
            "matched = re.search(r'【([^】]+)】', 'user text')\n"
        )

    def test_regex_replacement_text_is_still_checked(self) -> None:
        finding = self._finding("import re\nvalue = re.sub('x', '【新提示标题】', 'x')\n")
        self.assertIn("raw_legacy_heading", finding)

    def test_prompt_surface_rejects_loose_fields_but_accepts_a_section(self) -> None:
        finding = self._finding(
            "surface.add('reply.style', '正文', title='回复风格', source='sample')\n"
        )
        key_finding = self._finding("prompt_surface.add(key='reply.style')\n")
        self.assertIn("loose_prompt_surface_add", finding)
        self.assertIn("loose_prompt_surface_add", key_finding)

        self._check_source("prompt_surface.add(section, priority=10)\n")

    def test_delivery_batches_cannot_be_authored_as_model_visible_sections(self) -> None:
        for key in ("reply.style.batch", "passive.static", "passive.dynamic"):
            with self.subTest(key=key):
                finding = self._finding(
                    "value = prompt_section("
                    f"key='{key}', title='投递分组', source='sample', "
                    "content='', children=sections)\n"
                )
                self.assertIn("prompt_delivery_batch_section", finding)
                self.assertIn(key, finding)

    def test_parent_and_direct_child_cannot_repeat_the_same_title(self) -> None:
        finding = self._finding(
            "value = prompt_section(key='parent', title='重复标题', source='sample', "
            "content='正文', children=(prompt_section(key='child', title='重复标题', "
            "source='sample', content='子正文'),))\n"
        )

        self.assertIn("duplicate_prompt_child_title", finding)
        self.assertIn("重复标题", finding)

    def test_function_cannot_author_the_same_section_identity_in_multiple_branches(self) -> None:
        finding = self._finding(
            "def build(enabled):\n"
            "    if enabled:\n"
            "        return prompt_section(key='feature.context', title='功能上下文', "
            "source='sample', content='命中')\n"
            "    return prompt_section(key='feature.context', title='功能上下文', "
            "source='sample', content='')\n"
        )

        self.assertIn("duplicate_prompt_key_in_function", finding)
        self.assertIn("duplicate_prompt_title_in_function", finding)

    def test_nested_section_builder_has_an_independent_identity_scope(self) -> None:
        self._check_source(
            "def outer():\n"
            "    def build(content):\n"
            "        return prompt_section(key='feature.context', title='功能上下文', "
            "source='sample', content=content)\n"
            "    return build('正文')\n"
        )

    def test_conversation_plan_rejects_loose_fields_and_legacy_flags(self) -> None:
        add_finding = self._finding(
            "plan.add(key='reply.style', content='正文', structured=True)\n"
        )
        materialize_finding = self._finding(
            "plan.materialize_system_block(req, title='标题', content='正文')\n"
        )

        self.assertIn("loose_conversation_plan_call", add_finding)
        self.assertIn("structured", add_finding)
        self.assertIn("loose_conversation_plan_call", materialize_finding)
        self._check_source(
            "plan.add(section=section, marker='m', priority=10)\n"
            "plan.materialize_system_block(req, section=section, marker='m')\n"
        )

    def test_new_legacy_control_parameter_is_rejected(self) -> None:
        for flag in ("include_heading", "as_section", "as_sections"):
            with self.subTest(flag=flag):
                finding = self._finding(f"def build(*, {flag}=False):\n    return ''\n")
                self.assertIn("legacy_prompt_control_flag", finding)
                self.assertIn(flag, finding)

    def test_unused_allowlist_entry_is_rejected(self) -> None:
        stale_entry = CI._prompt_allow(
            CI._PROMPT_RULE_LEGACY_HEADING,
            "sample.py",
            "missing",
            ("【旧标题】",),
            "test-only compatibility entry",
            "test migration completes",
        )
        with patch.object(CI, "_PROMPT_AUTHORING_ALLOWLIST", (stale_entry,)):
            with self.assertRaisesRegex(SystemExit, "stale_prompt_allowlist"):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "sample.py"
                    path.write_text("value = 'plain'\n", encoding="utf-8")
                    CI.check_prompt_authoring(Path(directory))

    def test_renderer_allowlist_is_exact_and_documented(self) -> None:
        self._check_source(
            "def _render_labeled_section(section):\n"
            "    return f'【{section.title}】\\n{section.content}'\n",
            filename="conversation_prompt_section_part02.py",
        )
        for entry in CI._PROMPT_AUTHORING_ALLOWLIST:
            self.assertTrue(str(entry.get("reason") or "").strip())
            self.assertTrue(str(entry.get("remove_when") or "").strip())
            self.assertIsInstance(entry.get("tokens"), tuple)
            self.assertTrue(entry["tokens"])


if __name__ == "__main__":
    unittest.main()
