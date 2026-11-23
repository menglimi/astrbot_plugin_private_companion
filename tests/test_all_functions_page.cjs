/* 「全部功能」页：以 config-editor.js 为单一文案真源，按 V2 功能领域组织真实布尔配置项。 */
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
      title: "表达学习策略", note: "从真实对话里提炼表达习惯。",
      labels: {private_learning_enabled: "私聊表达学习", sample_window_minutes: "学习窗口"},
      helps: {private_learning_enabled: "从和你的私聊里学习称呼、语气和习惯；只学表达，不记隐私。",
        sample_window_minutes: "只统计最近这段时间的对话。"},
      order: ["private_learning_enabled", "sample_window_minutes"],
    },
    group_participation: {
      title: "群聊参与", note: "群聊氛围与互动策略。",
      labels: {enable_group_social_context: "启用群聊氛围参考"},
      helps: {enable_group_social_context: "关闭时下面五项氛围参考都不起作用，角色只按对话本身理解群聊。"},
      order: ["enable_group_social_context"],
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
      settings: {private_learning_enabled: true},
      fields: {private_learning_enabled: {kind: "boolean"}},
    },
    "group_participation": {
      group_id: "conversation.group_participation", scope: "window", edit_token: "token-b",
      settings: {enable_group_social_context: true},
      fields: {enable_group_social_context: {kind: "boolean"}},
    },
  };
  api.state.draft = {
    "expression.learning": {private_learning_enabled: true},
    "group_participation": {enable_group_social_context: true},
  };
  api.state.loaded = true;
  api.state.loading = false;
  api.state.attempted = true;
  api.state.error = "";
  api.state.notice = "";
  return api;
}

test("功能总表按 V2 领域分组渲染真实布尔开关并显示中文文案", () => {
  const api = loadFixture();
  const html = api.render();
  assert.match(html, /全部功能/);
  assert.match(html, /学习与成长/);
  assert.match(html, /群聊参与与行为/);
  assert.match(html, /私聊表达学习/);
  assert.match(html, /启用群聊氛围参考/);
  assert.match(html, /data-all-feature-toggle/);
  assert.match(html, /data-all-feature-open=/);
  assert.match(html, /data-all-feature-save/);
  assert.match(html, /private_learning_enabled/);
  assert.match(html, /enable_group_social_context/);
  assert.doesNotMatch(html, /data-all-function-open/);
  assert.doesNotMatch(html, /查看详解与配置/);
});

test("没有改动时保存按钮不可用，改动后计数", () => {
  const api = loadFixture();
  const clean = api.render();
  assert.match(clean, /保存更改/);
  assert.match(clean, /data-all-feature-save disabled/);
  api.state.draft["expression.learning"].private_learning_enabled = false;
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
  assert.ok(html.includes("学习与成长"));
  assert.ok(html.includes("群聊参与与行为"));
});

test("领域顺序覆盖 V2 真实配置分区", () => {
  const domains = context.window.allFeatureTest.domains;
  assert.equal(domains[0], "跨窗口与跨群递话");
  assert.ok(domains.includes("群聊参与与行为"));
  assert.ok(domains.includes("日记、睡眠与休息"));
  assert.ok(domains.includes("生活节奏与防打扰"));
  assert.ok(domains.includes("QQ 空间互动"));
  assert.equal(context.window.allFeatureTest.domainOf["group_participation"], "群聊参与与行为");
  assert.equal(context.window.allFeatureTest.domainOf["life"], "日记、睡眠与休息");
});

test("全部功能回到小屋内的浮层，而不是跳去独立页面", () => {
  assert.match(source, /const manageTabs=\[\s*\['all','layout-grid','全部功能'\]/);
  assert.match(source, /all:renderAllFunctionsPage/);
  assert.match(source, /if\(el\.dataset\.openNavigation!==undefined\)\{navigate\('manage','all'\);return;\}/);
  assert.match(source, /#navigationDialog/);
});
