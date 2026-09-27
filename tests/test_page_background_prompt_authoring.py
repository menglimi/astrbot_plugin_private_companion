from __future__ import annotations

import importlib.util
import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "astrbot_plugin_private_companion"
if PACKAGE not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = package
    spec.loader.exec_module(package)

from astrbot_plugin_private_companion import command_handlers, page_api, photo_reference_metadata
from astrbot_plugin_private_companion.conversation_prompt_section import (
    prompt_heading_ref,
    render_prompt_content,
)


def _page_api_domain_sources() -> list[str]:
    """宿主 page_api.py + 全部 page_api_* 域 mixin 模块的源码。

    页面 API 已按域拆分为多个文件，section key 的声明会随着方法一起搬家，
    所以任何「扫描页面 API 源码」的断言都必须合并扫描全部域模块。
    这里按文件 glob 动态发现域模块，新增域时无需再改本测试。
    """
    domain_paths = sorted(ROOT.glob("page_api_*.py"))
    sources: list[str] = []
    for path in [ROOT / "page_api.py", *domain_paths]:
        try:
            sources.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return sources


class PageBackgroundPromptAuthoringTests(unittest.TestCase):
    def test_page_body_renderer_preserves_wire_bytes(self) -> None:
        content = "  第一行\n\nJSON: {\"ok\":true}\n尾部  "

        self.assertEqual(
            content,
            page_api._render_page_background_prompt(
                key="background.test.body",
                title="后台正文测试",
                content=content,
            ),
        )

    def test_page_document_keeps_system_and_user_channels_separate(self) -> None:
        system = "系统规则\n保留换行"
        user = "用户输入\n{\"value\":1}"

        rendered_system, rendered_user = page_api._render_page_background_prompt_pair(
            key="background.test.pair",
            system_title="后台系统测试",
            system_content=system,
            user_title="后台用户测试",
            user_content=user,
        )

        self.assertEqual(system, rendered_system)
        self.assertEqual(user, rendered_user)

    def test_heading_reference_is_owned_by_prompt_renderer_module(self) -> None:
        self.assertEqual(
            "【待确认说话方式】",
            render_prompt_content(prompt_heading_ref("待确认说话方式")),
        )

    def test_page_llm_prompt_producers_declare_stable_section_keys(self) -> None:
        # 页面 API 已按域拆成多个 mixin 模块；section key 的声明跟着方法走，
        # 因此要合并扫描宿主模块与各 mixin 模块。
        source = "\n".join(_page_api_domain_sources())
        expected = (
            "background.reaction_library.analysis",
            "background.photo_reference_selection_trial",
            "background.troubleshooting.model_diagnostics",
            "background.troubleshooting.skill_similarity",
            "background.roleplay_draft",
            "background.persona_standardization",
            "background.persona_standardization.repair",
            "background.persona_standardization.expand",
            "background.persona_style.scenarios",
            "background.persona_style.scenarios_repair",
            "background.persona_style.scenario_retry",
            "background.persona_style.summary",
            "background.roleplay_draft.repair",
            "background.provider_test.vision",
            "background.provider_test.text",
        )
        for key in expected:
            with self.subTest(key=key):
                self.assertIn(f'key="{key}"', source)

    def test_manual_diagnosis_and_reference_review_use_section_authoring(self) -> None:
        manual_source = inspect.getsource(command_handlers.CommandHandlersMixin._companion_manual_model_answer)
        reference_source = inspect.getsource(photo_reference_metadata.build_reference_metadata_review_prompt)

        self.assertIn("prompt_section(", manual_source)
        self.assertIn("PromptRenderMode.LABELED_BLOCK", manual_source)
        self.assertNotIn("【用户问题】", manual_source)
        self.assertIn("prompt_document(", reference_source)
        self.assertIn("PromptRenderMode.BODY_ONLY", reference_source)


if __name__ == "__main__":
    unittest.main()
