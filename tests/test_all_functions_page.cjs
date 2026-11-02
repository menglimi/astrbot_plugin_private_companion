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
const window = {CompanionProduct: {scopeKey: () => "role-a"}};
const context = vm.createContext({
  window,
  document: {querySelector: () => null, querySelectorAll: () => []},
  setTimeout,
  I: name => `<i data-lucide="${name}"></i>`,
  escapeHTML: value => String(value),
  normalizeSearchText: value => String(value ?? "").normalize("NFKC").toLowerCase(),
  button: (text, attrs, kind) => `<button class="button ${kind}" ${attrs}>${text}</button>`,
});
vm.runInContext(source.slice(start, end) + `
window.allFeatureTest = {state: allFeaturesState, render: renderAllFeatureBoard,
  domains: allFeatureDomainOrder, domainOf: allFeatureDomainOf};`, context);

test("真实布尔配置按领域分组，并带可保存的开关", () => {
  const testApi = context.window.allFeatureTest;
  testApi.state.sections = {
    "expression.learning": {
      group_id: "expression.learning", scope: "role", edit_token: "token-a",
      settings: {sample_review_enabled: true, style_review_enabled: false},
      fields: {sample_review_enabled: {kind: "boolean"}, style_review_enabled: {kind: "boolean"}},
    },
    "group_participation": {
      group_id: "group_participation", scope: "role", edit_token: "token-b",
      settings: {behavior_enabled: true},
      fields: {behavior_enabled: {kind: "boolean"}},
    },
  };
  testApi.state.draft = {
    "expression.learning": {sample_review_enabled: true, style_review_enabled: false},
    "group_participation": {behavior_enabled: true},
  };
  testApi.state.loaded = true;
  testApi.state.loading = false;
  testApi.state.attempted = true;
  testApi.state.error = "";
  const html = testApi.render();
  assert.match(html, /全部功能/);
  assert.match(html, /通用能力/);
  assert.match(html, /群聊功能/);
  assert.match(html, /data-all-feature-group="expression\.learning"/);
  assert.match(html, /data-all-feature-field="sample_review_enabled"/);
  assert.match(html, /保存更改/);
});

test("领域顺序沿用 6.7.0 的能力分类", () => {
  const domains = context.window.allFeatureTest.domains;
  assert.equal(domains[0], "通用能力");
  assert.ok(domains.includes("私聊陪伴"));
  assert.ok(domains.includes("群聊功能"));
  assert.ok(domains.includes("长线主动"));
  assert.equal(context.window.allFeatureTest.domainOf["group_participation"], "群聊功能");
});

test("全部功能已接入 manage 路由而不是浮层", () => {
  assert.match(source, /const manageTabs=\[\s*\['all','layout-grid','全部功能'\]/);
  assert.match(source, /all:renderAllFunctionsPage/);
  assert.match(source, /if\(el\.dataset\.openNavigation!==undefined\)\{navigate\('manage','all'\);return;\}/);
});
