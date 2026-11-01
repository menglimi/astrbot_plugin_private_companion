/* 全局搜索的排序与命中高亮，直接从 app.js 的真实函数段求值。 */
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
  window,
  escapeHTML: value => String(value).replace(/[&<>"']/g, ch => ({"&": "&amp;", "<": "&lt;",
    ">": "&gt;", '"': "&quot;", "'": "&#39;"}[ch])),
  JSON, Math, Object, String, RegExp, Set, Map, console,
});
vm.runInContext(source.slice(start, end) + `
window.searchTest = {rankedSearchResults, markSearchText, settingSearchScore, scoreNormalizedText, searchTerms};`,
context);

test("分词会把中英文标点和空白拆开", () => {
  assert.deepEqual([...window.searchTest.searchTerms("日记，生活/睡眠|阅读")],
    ["日记", "生活", "睡眠", "阅读"]);
});

test("页面结果按相关度排序并标出命中片段", () => {
  const results = window.searchTest.rankedSearchResults("日记");
  assert.ok(results.length > 0);
  assert.equal(results[0].data[0], "日记与生活");
  const marked = window.searchTest.markSearchText("日记与生活 <草稿>", ["日记"]);
  assert.match(marked, /<mark>日记<\/mark>/);
  assert.match(marked, /&lt;草稿&gt;/);
});

test("配置项评分复用预归一化文本且多关键词取并集", () => {
  const result = {
    entry: {label: "主动联系"},
    texts: {label: "主动联系", field: "proactive.contact", sectionTitle: "联系设置",
      groupTitle: "主动联系", help: "免打扰与频率", aliases: ["proactive"]},
  };
  assert.ok(window.searchTest.settingSearchScore(result, ["主动"], "主动") > 0);
  assert.ok(window.searchTest.settingSearchScore(result, ["主动", "频率"], "主动 频率") > 0);
  assert.equal(window.searchTest.settingSearchScore(result, ["不存在"], "不存在"), -1);
});
