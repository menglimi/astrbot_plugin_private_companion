/* 「全部功能」页：目录必须覆盖各领域，并保留分段直达。 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "pages", "陪伴小屋", "app.js"), "utf8");
const start = source.indexOf("const allFunctionsGroups=");
const end = source.indexOf("/* 分发表只保留");
if (start < 0 || end < 0) throw new Error("all-functions section not found");
const context = vm.createContext({
  window: {},
  I: name => `<i data-lucide="${name}"></i>`,
  escapeHTML: value => String(value),
  normalizeSearchText: value => String(value ?? "").normalize("NFKC").toLowerCase(),
  button: (text, attrs, kind) => `<button class="button ${kind}" ${attrs}>${text}</button>`,
});
vm.runInContext(source.slice(start, end) + `
window.allFunctionsTest = {groups: allFunctionsGroups, render: renderAllFunctionsPage};`, context);

test("功能目录按组覆盖账号、关系、感知、生活、产出与维护", () => {
  const groups = context.window.allFunctionsTest.groups;
  assert.equal(groups.length, 6);
  const titles = groups.map(([title]) => title);
  assert.equal(titles.join("|"), "开始与账号|人物与关系|对话与感知|生活与成长|角色与产出|数据与维护");
  const keys = groups.flatMap(([, items]) => items.map(([key]) => key));
  for (const required of ["setup", "host", "models", "identity-people", "memory", "perception",
    "expression", "learning", "output", "maintenance", "data"]) {
    assert.ok(keys.includes(required), `missing ${required}`);
  }
});

test("卡片与分段标签都带可导航目标", () => {
  const html = context.window.allFunctionsTest.render();
  assert.match(html, /全部功能/);
  assert.match(html, /data-all-go="manage\|learning"/);
  assert.match(html, /data-all-go="manage\|learning\|expression"/);
  assert.match(html, /data-all-go="life\|diary\|reading"/);
  assert.match(html, /data-open-search/);
});

test("全部功能已接入 manage 路由而不是浮层", () => {
  assert.match(source, /const manageTabs=\[\s*\['all','layout-grid','全部功能'\]/);
  assert.match(source, /all:renderAllFunctionsPage/);
  assert.match(source, /if\(el\.dataset\.openNavigation!==undefined\)\{navigate\('manage','all'\);return;\}/);
  assert.match(source, /if\(el\.dataset\.allGo\)/);
});
