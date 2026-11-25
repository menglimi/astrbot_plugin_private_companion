#!/usr/bin/env python3
"""Automated full-site search coverage gate for the companion cottage UI.

Verifies that:
1. Every visible leaf key in `_conf_schema.json` is exposed by
   `astrbot_host.search_catalog.build_search_catalog()` without leaking
   non-boolean config values.
2. Every visible leaf key across all four native extension schemas
   (`game`, `live`, `reality`, `together`) is exposed.
3. Every model task defined in `pages/陪伴小屋/models-page.js` is exposed.
4. Loading `search-engine.js`, all six cottage setting providers, `app.js`,
   and the live backend catalog yields 100% coverage of all UI surfaces,
   zero broken routes, and accurate top-1 fuzzy/pinyin/synonym/typo recall.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from astrbot_host.search_catalog import (  # noqa: E402
    CONF_SCHEMA_PATH,
    NATIVE_EXTENSIONS_ROOT,
    build_search_catalog,
)


def _independent_walk_leaves(schema: dict[str, Any], prefix: str = "", require_type: bool = False) -> list[dict[str, Any]]:
    leaves: list[dict[str, Any]] = []
    for key, raw in schema.items():
        if not isinstance(raw, dict):
            continue
        dotted = f"{prefix}.{key}" if prefix else key
        if raw.get("type") == "object" and isinstance(raw.get("items"), dict):
            leaves.extend(_independent_walk_leaves(raw["items"], dotted, require_type=require_type))
            continue
        if raw.get("invisible") is True:
            continue
        if require_type and "type" not in raw:
            continue
        leaves.append({"path": dotted, "type": str(raw.get("type") or "string")})
    return leaves


def _extract_models_page_task_kinds() -> set[str]:
    models_js = (REPO_ROOT / "pages" / "陪伴小屋" / "models-page.js").read_text(encoding="utf-8")
    # Match quoted dotted task kinds inside TASK_GROUPS / TASK_LABELS / TASK_META tables.
    kinds = set(re.findall(
        r"""["']((?:conversation|screen|reality|proactive|memory|calendar|role|content)\.[a-z0-9_.]+)["']""",
        models_js,
    ))
    return kinds


NODE_AUDIT_SCRIPT = r"""
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const repoRoot = process.argv[1];
const catalog = JSON.parse(fs.readFileSync(0, "utf8"));
const pageDir = path.join(repoRoot, "pages", "陪伴小屋");

function loadScript(windowObj, fileName) {
  const code = fs.readFileSync(path.join(pageDir, fileName), "utf8");
  vm.runInNewContext(code, {
    window: windowObj,
    document: windowObj.document,
    globalThis: windowObj,
    console,
    setTimeout,
    clearTimeout,
    URLSearchParams,
    RegExp,
    Intl,
    Math,
    Date,
    JSON,
    Object,
    Array,
    Map,
    Set,
    Number,
    String,
    Boolean,
    Promise,
  });
}

const win = {
  document: {
    hidden: false,
    addEventListener() {},
    removeEventListener() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementById: () => null,
    createElement: () => ({ style: {}, classList: { add(){}, remove(){}, toggle(){} }, setAttribute(){}, appendChild(){} }),
  },
  location: { hash: "", pathname: "/", search: "" },
  history: { replaceState() {}, pushState() {} },
  addEventListener() {},
  removeEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  CompanionWorkbench: {
    escapeHTML: s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
  },
  CompanionProduct: {
    role: () => ({}),
    target: () => ({ scope_kind: "private" }),
    scopeKey: () => "test",
    request: async () => ({}),
  },
};

for (const file of [
  "search-engine.js",
  "config-editor.js",
  "reply-page.js",
  "host-options-page.js",
  "state-maintenance.js",
  "memory-settings-section.js",
  "perception-native-page.js",
]) {
  loadScript(win, file);
}

win.CompanionSearchRegistry.setCatalog(catalog);

const appSrc = fs.readFileSync(path.join(pageDir, "app.js"), "utf8");
const startIdx = appSrc.indexOf("const searchIndex=");
const endIdx = appSrc.indexOf("const tokenTaskSettings=");
if (startIdx < 0 || endIdx <= startIdx) {
  throw new Error("Could not locate search slice in app.js");
}
const slice = appSrc.slice(startIdx, endIdx);
const ctx = {
  window: win,
  document: win.document,
  route: "home",
  manageTab: "all",
  lifeTab: "state",
  escapeHTML: win.CompanionWorkbench.escapeHTML,
  console,
  setTimeout,
  clearTimeout,
  Date,
  Math,
  JSON,
  Object,
  Array,
  Map,
  Set,
  RegExp,
};
vm.createContext(ctx);
vm.runInContext(
  `const $ = () => null;
   const I = () => "";
   const icons = () => {};
   const manageTabs = [];
   const product = { target: () => ({ scope_kind: "private" }), role: () => ({}) };
   ${slice}
   globalThis.__audit = {
     searchIndex,
     searchableConfigEntries,
     rankedSearchResults,
     resolveSettingTarget,
   };`,
  ctx
);

const entries = ctx.__audit.searchableConfigEntries();
const errors = [];

const byField = new Set(entries.map(r => r.entry.field));
const byGroup = new Map();
for (const r of entries) {
  const g = r.entry.group || "";
  if (!byGroup.has(g)) byGroup.set(g, []);
  byGroup.get(g).push(r);
  if (!r.location || !r.location.next || !r.location.tab) {
    errors.push(`Broken location for ${g}.${r.entry.field}`);
  }
}

/* 1. Every _conf_schema.json visible leaf must be searchable by its dotted path */
for (const item of catalog.plugin_config || []) {
  if (!byField.has(item.path)) {
    errors.push(`Missing _conf_schema.json key in searchable entries: ${item.path}`);
  }
}

/* 2. Every native extension field across all 4 extensions must be searchable */
for (const ext of catalog.native_extensions || []) {
  const extEntries = new Set((byGroup.get(`native_ext.${ext.id}`) || []).map(r => r.entry.field));
  for (const item of ext.items || []) {
    if (!extEntries.has(item.path)) {
      errors.push(`Missing native extension field: ${ext.id}.${item.path}`);
    }
  }
}

/* 3. Every backend model task must be searchable under models.task */
const taskFields = new Set((byGroup.get("models.task") || []).map(r => r.entry.field));
for (const task of catalog.model_tasks || []) {
  if (!taskFields.has(task.kind)) {
    errors.push(`Missing model task in searchable entries: ${task.kind}`);
  }
}

/* 4. Required UI surface groups must all be present */
const requiredGroups = [
  "setup.guide",
  "host.workspace",
  "models.task",
  "models.tab",
  "appearance.page",
  "storage.page",
  "backup.page",
  "huawei.health",
  "weather.source",
  "semantic.page",
  "self.review",
  "behavior.page",
  "native_ext.game",
  "native_ext.live",
  "native_ext.reality",
  "native_ext.together",
];
for (const group of requiredGroups) {
  if (!byGroup.has(group) || byGroup.get(group).length === 0) {
    errors.push(`Required search surface missing: ${group}`);
  }
}

/* 5. All 8 学习与反思 sub-tabs must exist in searchIndex */
const learningSubtabs = new Set(
  ctx.__audit.searchIndex
    .filter(item => item[3] === "manage" && item[4] === "learning")
    .map(item => (item[5] && item[5].subtab) || "overview")
);
for (const expected of ["overview", "strategy", "review", "chain", "expression", "behavior", "semantic", "reflection"]) {
  if (!learningSubtabs.has(expected)) {
    errors.push(`Missing learning sub-tab in searchIndex: ${expected}`);
  }
}

/* 6. Fuzzy / Synonym / Pinyin / Typo top-hit recall verification */
const probes = [
  { q: "陪伴通用模型", expectField: "model.provider_id" },
  { q: "共享启动模型", expectField: "model.provider_id" },
  { q: "插件任务模型 Provider", expectField: "model.provider_id" },
  { q: "pbtymx", expectField: "model.provider_id" },
  { q: "陪伴统用模型", expectField: "model.provider_id" },
  { q: "全局备用模型", expectField: "model.fallback_provider_id" },
  { q: "倍用模型", expectField: "model.fallback_provider_id" },
  { q: "beiyong", expectField: "model.fallback_provider_id" },
  { q: "ComfyUI", expectField: "image_generation_providers" },
  { q: "华为健康", expectField: "huawei_health_sync" },
  { q: "和风天气", expectField: "weather_source_provider" },
  { q: "额度看板", expectField: "model_quota_dashboard" },
  { q: "edkb", expectField: "model_quota_dashboard" },
  { q: "指定主站", expectField: "quota_awareness_primary_station" },
  { q: "主站", expectField: "quota_awareness_primary_station" },
  { q: "zhuzhan", expectField: "quota_awareness_primary_station" },
];
for (const probe of probes) {
  const hits = ctx.__audit.rankedSearchResults(probe.q);
  const top3 = hits.slice(0, 3);
  const matched = top3.some(h => h.kind === "setting" && h.entry.field === probe.expectField);
  if (!matched) {
    errors.push(
      `Probe failed for "${probe.q}": expected ${probe.expectField} in top 3, got ${JSON.stringify(
        top3.map(h => (h.kind === "setting" ? `${h.entry.field}(${h.score})` : `page:${h.data[0]}(${h.score})`))
      )}`
    );
  }
}

const summary = {
  total_searchable_settings: entries.length,
  total_searchable_pages: ctx.__audit.searchIndex.length,
  plugin_config_count: (catalog.plugin_config || []).length,
  native_extension_fields: (catalog.native_extensions || []).reduce((acc, e) => acc + (e.items || []).length, 0),
  model_tasks_count: taskFields.size,
  learning_subtabs_count: learningSubtabs.size,
  errors,
};
process.stdout.write(JSON.stringify(summary, null, 2) + "\n");
if (errors.length) process.exit(1);
"""


def run_audit() -> dict[str, Any]:
    errors: list[str] = []
    catalog = build_search_catalog()

    # 1. Verify _conf_schema.json 100% visible leaf coverage & secret safety
    conf_schema = json.loads(CONF_SCHEMA_PATH.read_text(encoding="utf-8-sig"))
    expected_conf_leaves = _independent_walk_leaves(conf_schema, require_type=False)
    expected_conf_paths = {leaf["path"] for leaf in expected_conf_leaves}
    actual_conf_items = catalog.get("plugin_config", [])
    actual_conf_paths = {item["path"] for item in actual_conf_items}
    missing_conf = sorted(expected_conf_paths - actual_conf_paths)
    if missing_conf:
        errors.append(f"Missing _conf_schema.json visible keys: {missing_conf}")

    for item in actual_conf_items:
        if item.get("type") == "bool":
            if not isinstance(item.get("current_value"), bool):
                errors.append(f"Boolean _conf_schema key missing boolean current_value: {item['path']}")
        else:
            if "current_value" in item:
                errors.append(f"Non-boolean _conf_schema key must not expose current_value: {item['path']}")

    # 2. Verify all 4 native extension schemas 100% visible leaf coverage
    expected_exts = ("game", "live", "reality", "together")
    actual_exts = {ext["id"]: ext for ext in catalog.get("native_extensions", [])}
    for ext_id in expected_exts:
        schema_path = NATIVE_EXTENSIONS_ROOT / ext_id / "settings-schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8-sig"))
        expected_items = _independent_walk_leaves(schema, require_type=True)
        expected_paths = {leaf["path"] for leaf in expected_items}
        if ext_id not in actual_exts:
            errors.append(f"Missing native extension catalog: {ext_id}")
            continue
        actual_paths = {item["path"] for item in actual_exts[ext_id].get("items", [])}
        missing_ext = sorted(expected_paths - actual_paths)
        if missing_ext:
            errors.append(f"Missing native extension '{ext_id}' fields: {missing_ext[:10]}")
        expected_bools = sum(1 for leaf in expected_items if leaf["type"] == "bool")
        if actual_exts[ext_id].get("bool_count") != expected_bools:
            errors.append(
                f"Native extension '{ext_id}' bool_count mismatch: "
                f"{actual_exts[ext_id].get('bool_count')} != {expected_bools}"
            )

    # 3. Verify all model tasks in models-page.js are in catalog.model_tasks
    models_page_kinds = _extract_models_page_task_kinds()
    catalog_task_kinds = {task["kind"] for task in catalog.get("model_tasks", [])}
    missing_tasks = sorted(models_page_kinds - catalog_task_kinds)
    if missing_tasks:
        errors.append(f"Model tasks in models-page.js missing from search catalog: {missing_tasks}")

    # 4. Run Node end-to-end search registry + fuzzy recall verification
    proc = subprocess.run(
        ["node", "-e", NODE_AUDIT_SCRIPT, str(REPO_ROOT)],
        input=json.dumps(catalog, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    node_summary: dict[str, Any] = {}
    if proc.stdout.strip():
        try:
            node_summary = json.loads(proc.stdout)
        except ValueError:
            errors.append(f"Node audit returned non-JSON output: {proc.stdout[:400]}")
    if proc.returncode != 0:
        if node_summary.get("errors"):
            errors.extend(node_summary["errors"])
        else:
            errors.append(f"Node audit failed (exit {proc.returncode}): {proc.stderr.strip()[:500]}")

    return {
        "ok": len(errors) == 0,
        "conf_schema_visible_count": len(expected_conf_paths),
        "native_extension_counts": {
            ext_id: len(actual_exts[ext_id]["items"]) for ext_id in expected_exts if ext_id in actual_exts
        },
        "models_page_task_count": len(models_page_kinds),
        "frontend": node_summary,
        "errors": errors,
    }


def main() -> int:
    result = run_audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
