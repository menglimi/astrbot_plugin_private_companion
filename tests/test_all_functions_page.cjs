/* 「全部功能」页：按 6.7.0 的功能开关总表组织真实布尔配置项。 */
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
      settings: {enable_expression_learning: true},
      fields: {enable_expression_learning: {kind: "boolean"}},
    },
    "group_participation": {
      group_id: "group_participation", scope: "role", edit_token: "token-b",
      settings: {enable_group_companion: true},
      fields: {enable_group_companion: {kind: "boolean"}},
    },
  };
  api.state.draft = {
    "expression.learning": {enable_expression_learning: true},
    "group_participation": {enable_group_companion: true},
  };
  api.state.loaded = true;
  api.state.loading = false;
  api.state.attempted = true;
  api.state.error = "";
  api.state.notice = "";
  return api;
}

test("功能总表按 6.7.0 分组渲染真实布尔开关", () => {
  const api = loadFixture();
  const html = api.render();
  assert.match(html, /全部功能/);
  assert.match(html, /通用能力/);
  assert.match(html, /群聊功能/);
  assert.match(html, /data-all-feature-toggle/);
  assert.match(html, /data-all-feature-open=/);
  assert.match(html, /data-all-feature-save/);
  assert.match(html, /enable_expression_learning/);
  assert.match(html, /enable_group_companion/);
  assert.doesNotMatch(html, /data-all-function-open/);
  assert.doesNotMatch(html, /查看详解与配置/);
});

test("没有改动时保存按钮不可用，改动后计数", () => {
  const api = loadFixture();
  const clean = api.render();
  assert.match(clean, /保存更改/);
  assert.match(clean, /data-all-feature-save disabled/);
  api.state.draft["expression.learning"].enable_expression_learning = false;
  const dirty = api.render();
  assert.match(dirty, /保存更改（1）/);
  assert.doesNotMatch(dirty, /data-all-feature-save disabled/);
});

test("领域导航与筛选输入来自当前总表", () => {
  const api = loadFixture();
  const html = api.render();
  assert.match(html, /data-all-feature-jump="all-feature-0"/);
  assert.match(html, /data-all-feature-filter/);
  assert.match(html, /all-functions-summary/);
  assert.match(html, /data-all-feature-domain="all"/);
  assert.match(html, /data-all-feature-status="enabled"/);
  assert.ok(html.includes("通用能力"));
  assert.ok(html.includes("群聊功能"));
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
  assert.match(source, /all:renderAllFunctionsPage/);
  assert.match(source, /if\(el\.dataset\.openNavigation!==undefined\)\{navigate\('manage','all'\);return;\}/);
  assert.match(source, /#navigationDialog/);
});
