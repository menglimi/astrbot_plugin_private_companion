/* 「全部功能」页：按 6.7.0 的功能开关总表组织真实布尔配置项。
   外层是功能卡，点开卡片才看到详解与配置项。 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "pages", "陪伴小屋", "app.js"), "utf8");
const start = source.indexOf("const allFeatureDomainOrder=");
const end = source.indexOf("/* 分发表只保留");
if (start < 0 || end < 0) throw new Error("all-functions section not found");
const window = {
  CompanionProduct: {scopeKey: () => "role-a"},
  CompanionConfigEditors: {spec: {
    "expression.learning": {
      title: "表达学习", note: "从真实对话里提炼表达习惯。",
      labels: {sample_window_minutes: "学习窗口", review_threshold: "触发概率"},
      helps: {sample_window_minutes: "只统计最近这段时间的对话。",
        review_threshold: "越高越少触发学习。"},
      order: ["enable_expression_learning", "sample_window_minutes", "review_threshold"],
    },
  }},
};
const context = vm.createContext({
  window,
  document: {querySelector: () => null, querySelectorAll: () => []},
  setTimeout,
  I: name => `<i data-lucide="${name}"></i>`,
  escapeHTML: value => String(value ?? ""),
  normalizeSearchText: value => String(value ?? "").normalize("NFKC").toLowerCase(),
  button: (text, attrs, kind) => `<button class="button ${kind}" ${attrs}>${text}</button>`,
});
vm.runInContext(source.slice(start, end) + `
window.allFeatureTest = {state: allFeaturesState, render: renderAllFeatureBoard,
  domains: allFeatureDomainOrder, domainOf: allFeatureDomainOf};`, context);

function loadFixture() {
  const api = context.window.allFeatureTest;
  api.state.sections = {
    "expression.learning": {
      group_id: "expression.learning", scope: "role", edit_token: "token-a",
      settings: {enable_expression_learning: true, sample_review_enabled: true,
        sample_window_minutes: 30, review_threshold: 0.4},
      fields: {
        enable_expression_learning: {kind: "boolean"},
        sample_review_enabled: {kind: "boolean"},
        sample_window_minutes: {kind: "integer", minimum: 1, maximum: 60},
        review_threshold: {kind: "probability", minimum: 0, maximum: 1},
      },
    },
    "group_participation": {
      group_id: "group_participation", scope: "role", edit_token: "token-b",
      settings: {enable_group_companion: true},
      fields: {enable_group_companion: {kind: "boolean"}},
    },
  };
  api.state.draft = {
    "expression.learning": {enable_expression_learning: true, sample_review_enabled: true,
      sample_window_minutes: 30, review_threshold: 0.4},
    "group_participation": {enable_group_companion: true},
  };
  api.state.loaded = true;
  api.state.loading = false;
  api.state.attempted = true;
  api.state.error = "";
  api.state.openField = "";
  return api;
}

test("外层是功能卡，开关不在卡片上", () => {
  const api = loadFixture();
  const html = api.render();
  assert.match(html, /全部功能/);
  assert.match(html, /通用能力/);
  assert.match(html, /群聊功能/);
  assert.match(html, /data-all-function-open="enable_expression_learning"/);
  assert.match(html, /查看详解与配置/);
  assert.match(html, /保存更改/);
  assert.doesNotMatch(html, /data-all-feature-toggle/);
  assert.doesNotMatch(html, /data-all-feature-group="expression\.learning"/);
});

test("点开卡片后显示详解、开关和配置项", () => {
  const api = loadFixture();
  api.state.openField = "enable_expression_learning";
  const html = api.render();
  assert.match(html, /data-all-function-back/);
  assert.match(html, /← 全部功能/);
  assert.match(html, /表达学习/);
  assert.match(html, /从选定私聊或群聊提取抽象表达特征/);
  assert.match(html, /配置项/);
  assert.match(html, /data-all-feature-field="enable_expression_learning"/);
  assert.match(html, /data-all-feature-field="sample_window_minutes"/);
  assert.match(html, /学习窗口/);
  assert.match(html, /只统计最近这段时间的对话/);
  assert.match(html, /data-all-feature-field="review_threshold"/);
  assert.match(html, /data-all-feature-kind="probability"/);
  assert.match(html, /value="40"/);
  assert.match(html, /同区域的其它开关/);
});

test("领域顺序沿用 6.7.0 的能力分类", () => {
  const domains = context.window.allFeatureTest.domains;
  assert.equal(domains[0], "通用能力");
  assert.ok(domains.includes("私聊陪伴"));
  assert.ok(domains.includes("群聊功能"));
  assert.ok(domains.includes("长线主动"));
  assert.equal(context.window.allFeatureTest.domainOf["group_participation"], "群聊功能");
});

test("全部功能回到小屋内的浮层，而不是跳去独立页面", () => {
  assert.match(source, /const manageTabs=\[\s*\['all','layout-grid','全部功能'\]/);
  /* 旧深链接 #manage/all 仍由 renderAllFunctionsPage 兜底转去功能地图。 */
  assert.match(source, /all:renderAllFunctionsPage/);
  /* 左栏那颗「全部功能」按钮打开浮层：关掉还停在原页面，不必重载小屋。 */
  assert.match(source, /if\(el\.dataset\.openNavigation!==undefined\)\{openNavigation\(\);return;\}/);
  assert.match(source, /function openNavigation\(\)\{[\s\S]*?\$\('#navigationDialog'\)[\s\S]*?showModal\(\)/);
});
