"""Cross-stack i18n and catalog contract tests for 「全部功能」 and /config metadata.

Dynamically inspects ``astrbot_host/configuration_api.py`` ``SECTIONS``, the real
``FieldRegistry`` field definitions from ``companion_v2``, and the frontend
``pages/陪伴小屋/{workbench-page,config-editor,app,models-page,maintenance-page}.js``
files so any newly added config field, boolean switch, management error code, or
search tab that lacks Chinese metadata fails CI immediately.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import subprocess
import unittest

from companion_v2.configuration import management as mgmt
from companion_v2.conversation.content.cover_assets import (
    configuration_fields as content_cover_fields,
)
from companion_v2.conversation.media.output_settings import (
    configuration_fields as image_output_fields,
)
from companion_v2.model_gateway.budget import SOFT_FIELDS
from companion_v2.system_prompts.configuration import (
    configuration_fields as system_prompt_fields,
)


ROOT = Path(__file__).resolve().parent.parent
PAGE_DIR = ROOT / "pages" / "陪伴小屋"
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
MAINTENANCE_ONLY_SECTIONS = frozenset({"system.prompts"})
INSTALLATION_SECTIONS = frozenset(
    {"proactive.contact", "reaction", "budget", "private_access_installation"}
)


def _backend_sections() -> dict[str, str]:
    """Parse SECTIONS from astrbot_host/configuration_api.py without importing AstrBot."""
    source = (ROOT / "astrbot_host" / "configuration_api.py").read_text("utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SECTIONS":
                    assert isinstance(node.value, ast.Dict)
                    return {
                        key.value: value.id
                        for key, value in zip(node.value.keys, node.value.values)
                        if isinstance(key, ast.Constant) and isinstance(value, ast.Name)
                    }
    raise AssertionError("SECTIONS dict not found in astrbot_host/configuration_api.py")


def _field_definitions_by_group():
    return {
        mgmt.QUOTA_GROUP: mgmt.quota_fields(),
        mgmt.REVIEW_GROUP: mgmt.review_fields(),
        mgmt.IMAGE_OUTPUT_GROUP: image_output_fields(),
        mgmt.BUSY_GROUP: mgmt.busy_fields(),
        mgmt.AUTHORING_GROUP: (*mgmt.authoring_fields(), *content_cover_fields()),
        mgmt.REACTION_GROUP: mgmt.reaction_fields(),
        mgmt.BUDGET_GROUP: SOFT_FIELDS,
        mgmt.RECALL_GROUP: mgmt.recall_fields(),
        mgmt.SCREEN_GROUP: mgmt.screen_fields(),
        mgmt.WEB_GROUP: mgmt.web_fields(),
        mgmt.RELAY_GROUP: mgmt.relay_fields(),
        mgmt.QZONE_GROUP: mgmt.qzone_fields(),
        mgmt.PERSON_GROUP: mgmt.person_fields(),
        mgmt.PRIVATE_ACCESS_GROUP: mgmt.private_access_fields(),
        mgmt.SKILLS_GROUP: mgmt.skill_configuration_fields(),
        mgmt.EXPRESSION_GROUP: mgmt.expression_fields(),
        mgmt.KNOWLEDGE_GROUP: mgmt.knowledge_fields(),
        mgmt.LIFE_GROUP: mgmt.life_fields(),
        mgmt.ROLE_PROFILE_GROUP: mgmt.role_profile_fields(),
        mgmt.RELATIONSHIP_PROFILE_GROUP: mgmt.relationship_profile_fields(),
        mgmt.SYSTEM_PROMPT_GROUP: system_prompt_fields(),
        mgmt.LIBRARY_GROUP: mgmt.library_fields(),
        mgmt.WARDROBE_GROUP: mgmt.wardrobe_fields(),
        mgmt.REFERENCE_GROUP: mgmt.reference_fields(),
        mgmt.PARTICIPATION_GROUP: mgmt.participation_fields(),
        mgmt.CONTACT_GROUP: mgmt.contact_fields(),
        mgmt.PHONE_GROUP: mgmt.phone_fields(),
    }


def _backend_section_payloads() -> dict[str, dict]:
    """Build exact /config section snapshots from the live backend field definitions."""
    sections_map = _backend_sections()
    by_group = _field_definitions_by_group()
    payloads: dict[str, dict] = {}
    for sec_name, cls_name in sections_map.items():
        if sec_name in MAINTENANCE_ONLY_SECTIONS:
            continue
        cls = getattr(mgmt, cls_name)
        gid = cls.group
        visible = getattr(cls, "visible_fields", None)
        defs = by_group[gid]
        prefix = gid + "."
        scope = "installation" if sec_name in INSTALLATION_SECTIONS else "role"
        settings = {}
        fields = {}
        for fdef in defs:
            short = fdef.field_id.removeprefix(prefix)
            if visible is not None and short not in visible:
                continue
            settings[short] = fdef.default
            fields[short] = {
                "kind": fdef.kind.value,
                "default": fdef.default,
                "write_layer": scope,
            }
        payloads[sec_name] = {
            "section": sec_name,
            "group_id": gid,
            "scope": scope,
            "edit_token": "a" * 64,
            "settings": settings,
            "fields": fields,
        }
    return payloads


def _run_node(script: str, stdin_payload: dict | None = None) -> dict:
    completed = subprocess.run(
        ["node", "-"],
        input=script if stdin_payload is None else f"const INPUT = {json.dumps(stdin_payload, ensure_ascii=False)};\n{script}",
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=str(ROOT),
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(f"node contract probe failed ({completed.returncode}):\n{completed.stderr}\n{completed.stdout}")
    return json.loads(completed.stdout)


class AllFeaturesI18nContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.backend_payloads = _backend_section_payloads()
        cls.probe = _run_node(
            r"""
            const fs = require("node:fs");
            const path = require("node:path");
            const vm = require("node:vm");

            const DIR = path.join(process.cwd(), "pages", "陪伴小屋");
            const read = name => fs.readFileSync(path.join(DIR, name), "utf8");
            const storage = {getItem: () => null, setItem() {}, removeItem() {}};
            const ctx = vm.createContext({
              window: {},
              document: {
                documentElement: {dataset: {}},
                querySelector: () => null,
                querySelectorAll: () => [],
                addEventListener() {},
              },
              sessionStorage: storage,
              localStorage: storage,
              Intl, Date, Object, Array, Number, String, JSON, Math, URLSearchParams,
              setTimeout, clearTimeout,
              I: name => `<i data-lucide="${name}"></i>`,
              escapeHTML: value => String(value ?? ""),
              normalizeSearchText: value => String(value ?? "").normalize("NFKC").toLowerCase(),
              button: (text, attrs, kind) => `<button class="button ${kind}" ${attrs}>${text}</button>`,
            });
            for (const file of [
              "workbench-page.js", "config-editor.js", "reply-page.js",
              "host-options-page.js", "state-maintenance.js",
              "memory-settings-section.js", "perception-native-page.js",
            ]) {
              vm.runInContext(read(file), ctx, {filename: file});
            }

            const appSrc = read("app.js");
            const sAll = appSrc.indexOf("const allFeatureDomainOrder=");
            const eAll = appSrc.indexOf("/* 分发表只保留");
            ctx.window.CompanionProduct = {scopeKey: () => "role-contract"};
            ctx.product = ctx.window.CompanionProduct;
            vm.runInContext(appSrc.slice(sAll, eAll) + `
              window.__all = {state: allFeaturesState, board: allFeatureBoardGroups,
                index: allFeatureIndex, render: renderAllFeatureBoard};`, ctx, {filename: "app-all-features.js"});

            ctx.window.__all.state.sections = INPUT.sections;
            ctx.window.__all.state.endpoints = Object.fromEntries(
              Object.keys(INPUT.sections).map(name => [name, INPUT.installation.includes(name) ? "config" : "host/targets/t/roles/r/config"])
            );
            ctx.window.__all.state.draft = Object.fromEntries(
              Object.entries(INPUT.sections).map(([name, sec]) => [
                name,
                Object.fromEntries(
                  Object.entries(sec.fields)
                    .filter(([, meta]) => meta.kind === "boolean")
                    .map(([f]) => [f, Boolean(sec.settings[f])])
                ),
              ])
            );
            ctx.window.__all.state.loaded = true;

            const board = ctx.window.__all.board();
            const indexKeys = Object.keys(ctx.window.__all.index());
            const renderedHtml = ctx.window.__all.render();

            const sLoc = appSrc.indexOf("const configSearchLocations={");
            const eLoc = appSrc.indexOf("let visibleSearchResults=[];");
            vm.runInContext(appSrc.slice(sLoc, eLoc) + `
              window.__search = {configSearchLocation, replySearchLocation};`, ctx, {filename: "app-search-loc.js"});
            const pageLabelsMatch = appSrc.match(/const pageLabels=(\{[^}]+\});/);
            const pageLabels = pageLabelsMatch ? vm.runInNewContext(`(${pageLabelsMatch[1]})`) : {};

            const providers = [
              ctx.window.CompanionConfigEditors,
              ctx.window.CompanionReplyPage,
              ctx.window.CompanionHostOptionsPage,
              ctx.window.CompanionStateMaintenance,
              ctx.window.CompanionMemorySettingsSection,
              ctx.window.CompanionNativePerceptionPage,
            ];
            const unmappedSearch = [];
            const missingPageLabels = new Set();
            for (const provider of providers) {
              for (const entry of provider?.searchEntries?.() || []) {
                const loc = entry.group === "conversation.presentation"
                  ? ctx.window.__search.replySearchLocation(entry)
                  : ctx.window.__search.configSearchLocation(entry);
                if (!loc) {
                  unmappedSearch.push(`${entry.group}.${entry.field}`);
                } else if (!pageLabels[loc.tab]) {
                  missingPageLabels.add(loc.tab);
                }
              }
            }

            const specSummary = {};
            for (const [sec, def] of Object.entries(ctx.window.CompanionConfigEditors.spec)) {
              specSummary[sec] = {
                title: def.title || "",
                labels: def.labels || {},
                helps: def.helps || {},
              };
            }
            console.log(JSON.stringify({
              spec: specSummary,
              wbErrors: ctx.window.CompanionWorkbench.errors,
              ceErrorsCount: Object.keys(ctx.window.CompanionConfigEditors.errors).length,
              board,
              indexKeys,
              renderedHtmlHasRawFallback: /<code>([^<]+)<\/code><small>\1<\/small>/.test(renderedHtml),
              pageLabels,
              unmappedSearch,
              missingPageLabels: [...missingPageLabels],
            }));
            """,
            {
                "sections": cls.backend_payloads,
                "installation": sorted(INSTALLATION_SECTIONS),
            },
        )

    def test_100_percent_config_fields_have_chinese_labels_and_bool_helps(self) -> None:
        spec = self.probe["spec"]
        missing_labels = []
        missing_bool_helps = []
        total_fields = 0
        total_booleans = 0

        for sec_name, payload in self.backend_payloads.items():
            self.assertIn(sec_name, spec, f"config-editor.js 缺少分区声明: {sec_name}")
            sec_spec = spec[sec_name]
            self.assertRegex(sec_spec["title"], CJK_RE, f"分区 {sec_name} 缺少中文标题")
            for field_name, meta in payload["fields"].items():
                total_fields += 1
                label = sec_spec["labels"].get(field_name, "")
                if not label or label == field_name or not CJK_RE.search(label):
                    missing_labels.append(f"{sec_name}.{field_name}={label!r}")
                if meta["kind"] == "boolean":
                    total_booleans += 1
                    help_text = sec_spec["helps"].get(field_name, "")
                    if not help_text or help_text == field_name or not CJK_RE.search(help_text):
                        missing_bool_helps.append(f"{sec_name}.{field_name}={help_text!r}")

        self.assertGreaterEqual(total_fields, 450)
        self.assertGreaterEqual(total_booleans, 130)
        self.assertEqual(missing_labels, [], f"以下 /config 字段缺少中文 label: {missing_labels}")
        self.assertEqual(missing_bool_helps, [], f"以下布尔字段缺少中文 help: {missing_bool_helps}")

    def test_all_feature_board_zero_collisions_zero_triplicates_zero_other_bucket(self) -> None:
        expected_booleans = {
            f"{sec_name}.{field_name}"
            for sec_name, payload in self.backend_payloads.items()
            for field_name, meta in payload["fields"].items()
            if meta["kind"] == "boolean"
        }
        index_keys = set(self.probe["indexKeys"])
        self.assertEqual(
            index_keys,
            expected_booleans,
            f"allFeatureIndex() 存在丢键碰撞或多余键: missing={sorted(expected_booleans - index_keys)}",
        )

        board = self.probe["board"]
        domains = [group["domain"] for group in board]
        self.assertNotIn("其他功能", domains, "所有 V2 分区均应归入明确领域，「其他功能」必须为 0 项")

        switch_rows = [
            row
            for group in board
            for row in group["rows"]
            if not row.get("link")
        ]
        row_keys = [row["key"] for row in switch_rows]
        self.assertEqual(len(row_keys), len(set(row_keys)), "全部功能面板存在重复复合键")
        self.assertEqual(set(row_keys), expected_booleans, "面板开关总数必须与后端全部布尔配置 100% 一致")

        for row in switch_rows:
            self.assertNotEqual(row["label"], row["field"], f"{row['key']} 标题退化为裸英文键")
            self.assertNotEqual(row["help"], row["field"], f"{row['key']} 简介退化为裸英文键")
            self.assertRegex(row["label"], CJK_RE, f"{row['key']} 标题不含中文: {row['label']!r}")
            self.assertRegex(row["help"], CJK_RE, f"{row['key']} 简介不含中文: {row['help']!r}")
            expected_scope = "所有角色共用" if row["name"] in INSTALLATION_SECTIONS else "当前角色/窗口"
            self.assertEqual(row["scopeLabel"], expected_scope, f"{row['key']} 作用域角标错误")

        self.assertFalse(
            self.probe["renderedHtmlHasRawFallback"],
            "渲染 HTML 不得出现 <code>key</code><small>key</small> 重复英文回退",
        )

    def test_unified_error_dictionary_covers_all_management_prefixes_and_audit_codes(self) -> None:
        wb_errors = self.probe["wbErrors"]
        self.assertEqual(self.probe["ceErrorsCount"], len(wb_errors))

        mgmt_source = (ROOT / "companion_v2" / "configuration" / "management.py").read_text("utf-8")
        prefixes = sorted(set(re.findall(r'error_prefix\s*=\s*"([^"]+)"', mgmt_source)))
        missing = []
        for prefix in prefixes:
            for suffix in ("scope_invalid", "target_changed", "configuration_invalid"):
                code = f"{prefix}_{suffix}"
                if code not in wb_errors or not CJK_RE.search(wb_errors[code]):
                    missing.append(code)
        for required in (
            "invalid_probability",
            "invalid_weight",
            "busy_gate_settings_invalid",
            "qzone_scene_list_invalid",
            "life_sleep_variation_invalid",
            "life_sleep_window_invalid",
            "configuration_section_read_failed",
        ):
            if required not in wb_errors or not CJK_RE.search(wb_errors[required]):
                missing.append(required)
        self.assertEqual(missing, [], f"workbench-page.js errors 缺少以下错误码中文映射: {missing}")

    def test_search_locations_and_page_labels_have_zero_english_leaks(self) -> None:
        self.assertEqual(
            self.probe["unmappedSearch"],
            [],
            f"以下可搜索配置项未映射跳转位置: {self.probe['unmappedSearch']}",
        )
        self.assertEqual(
            self.probe["missingPageLabels"],
            [],
            f"renderSearch() pageLabels 缺少以下页签中文名: {self.probe['missingPageLabels']}",
        )
        for tab, label in self.probe["pageLabels"].items():
            self.assertRegex(label, CJK_RE, f"pageLabels[{tab!r}] 必须为中文: {label!r}")

    def test_models_and_maintenance_cover_poke_reply_and_reality_text(self) -> None:
        models_src = (PAGE_DIR / "models-page.js").read_text("utf-8")
        maint_src = (PAGE_DIR / "maintenance-page.js").read_text("utf-8")
        for task_kind in ("conversation.poke_reply", "reality.text"):
            self.assertIn(f'"{task_kind}"', models_src, f"models-page.js 缺少任务名: {task_kind}")
            self.assertIn(f'"{task_kind}"', maint_src, f"maintenance-page.js 缺少任务名: {task_kind}")


if __name__ == "__main__":
    unittest.main()
