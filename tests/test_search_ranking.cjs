/* 全局搜索的相关度：配置项必须能被搜到，页面入口不受影响。
   直接从 app.js 的真实函数段求值，避免在测试里复制一份打分逻辑。 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "pages", "陪伴小屋", "app.js"), "utf8");
const start = source.indexOf("const searchIndex=");
const end = source.indexOf("function updateSearchSelection");
if (start < 0 || end < 0) throw new Error("search section not found");
const window = {};
const context = vm.createContext({
  window, route: "life", manageTab: "host", lifeTab: "home",
  escapeHTML: value => String(value).replace(/[&<>"']/g, ch => ({"&": "&amp;", "<": "&lt;",
    ">": "&gt;", '"': "&quot;", "'": "&#39;"}[ch])),
  JSON, Math, Object, String, RegExp, Set, Map, Date, console,
});
vm.runInContext(source.slice(start, end) + `
window.searchTest = {rankedSearchResults, searchableConfigEntries, settingSearchScore, searchTerms,
  invalidateSearchCache};`, context);

/* 一条真实形状的配置项索引来源：标签、用途、字段键和别名都该参与检索。 */
const configEntry = () => ({group: "proactive.contact", groupTitle: "主动联系偏好",
  field: "balance_awareness_enabled", label: "允许因余额变化联系", help: "余额感知开关",
  section: "balance", sectionTitle: "余额感知", sectionCount: 23, aliases: ["余额提醒"]});

function withEditors(entries) {
  window.CompanionConfigEditors = {searchEntries: () => entries};
  window.searchTest.invalidateSearchCache();
}

test("配置项按标签搜得到，且分数为正而不是被过滤掉", () => {
  withEditors([configEntry()]);
  const results = window.searchTest.rankedSearchResults("允许因余额变化联系");
  const setting = results.find(result => result.kind === "setting");
  assert.ok(setting, "配置项应当出现在搜索结果里");
  assert.equal(setting.entry.label, "允许因余额变化联系");
  assert.equal(setting.entry.field, "balance_awareness_enabled");
  assert.ok(setting.score > 0, "配置项得分必须为正，否则会被过滤掉");
  assert.ok(results.every(result => result.score > 0), "结果里不应出现零分或负分条目");
});

test("配置项按分区标题、用途和别名同样搜得到", () => {
  withEditors([configEntry()]);
  for (const query of ["余额感知", "余额感知开关", "余额提醒", "balance_awareness_enabled"]) {
    const results = window.searchTest.rankedSearchResults(query);
    const setting = results.find(result => result.kind === "setting");
    assert.ok(setting, `${query} 应当命中配置项`);
    assert.ok(setting.score > 0, `${query} 的配置项得分必须为正`);
  }
});

test("配置项与页面入口一起参与排序，配置项不被页面结果挤掉", () => {
  withEditors([configEntry()]);
  const results = [...window.searchTest.rankedSearchResults("余额感知")];
  assert.ok(results.some(result => result.kind === "setting"));
  const sorted = [...results].sort((left, right) => right.score - left.score);
  assert.deepEqual(results.map(result => result.score), sorted.map(result => result.score),
    "结果应当按相关度从高到低排列");
});

test("原本能搜到的页面入口不受影响", () => {
  withEditors([configEntry()]);
  const results = window.searchTest.rankedSearchResults("日记");
  const pages = results.filter(result => result.kind === "page");
  assert.ok(pages.length > 0, "页面入口仍应命中");
  assert.equal(pages[0].data[0], "日记与生活", "页面入口的排序保持稳定");
  assert.ok(pages.every(result => result.score > 0));
  assert.equal(results.some(result => result.kind === "setting"), false,
    "没有命中配置项的查询不应凭空返回配置项");
});

test("打分函数同时接受索引包装对象和原始条目两种形状", () => {
  withEditors([configEntry()]);
  const wrapped = window.searchTest.searchableConfigEntries()[0];
  const raw = configEntry();
  assert.ok(window.searchTest.settingSearchScore(wrapped, ["余额"], "余额") > 0,
    "索引包装对象（含预归一化 texts）应当得分");
  assert.ok(window.searchTest.settingSearchScore(raw, ["余额"], "余额") > 0,
    "原始条目（没有 texts）也应当得分");
  assert.equal(window.searchTest.settingSearchScore(raw, ["不存在"], "不存在"), -1,
    "不匹配仍返回 -1");
});
