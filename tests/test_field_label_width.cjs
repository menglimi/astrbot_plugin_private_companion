/*
 * 字段标签列宽度的回归护栏。
 *
 * 7.1.x 多次出现「标题与说明被挤成一字一行、开关留在最右侧」：根因都是
 * `.wb-field-main` 的标签列用了 `minmax(0,1fr)`，控制列又有固定下限，
 * 窄容器里标签列先被压到 0。这里锁定几个真实入口，防止后续页面样式
 * 再把第一列改回 0 宽。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const DIR = path.join(__dirname, "..", "pages", "陪伴小屋");
const read = name => fs.readFileSync(path.join(DIR, name), "utf8");
const rulesFor = (source, selector) => {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return [...source.matchAll(new RegExp(escaped + "\\s*\\{([^}]*)\\}", "gm"))]
    .map(match => match[1]).filter(rule => /grid-template-columns/.test(rule));
};

test("基础工作台字段标签列保留最小宽度", () => {
  const rules = rulesFor(read("workbench.css"), ".wb-field-main");
  assert.ok(rules.some(rule => /grid-template-columns:\s*minmax\(12em,1fr\)/.test(rule)),
    ".wb-field-main 的标签列又变成 minmax(0,1fr)：窄容器会把标题压成一字一行");
});

test("表达页回复工作台标签列不被 ID 样式压没", () => {
  const source = read("workbench.css");
  const rules = rulesFor(source, "#replyWorkspace .wb-field-main");
  assert.ok(rules.some(rule => /grid-template-columns:\s*minmax\(12em,1fr\)/.test(rule)),
    "#replyWorkspace 的 ID 样式需要保留 12em 标签列");
  assert.match(source, /#replyWorkspace \.wb-field-main>\.wb-label[\s\S]*?min-width:12em/,
    "标签本身也需要 12em 最小宽度");
});

test("表达学习与表情检索的字段标签列不再从 0 开始", () => {
  const learning = read("learning-hub.css");
  assert.ok(rulesFor(learning, "#expressionWorkspace .wb-field-main")
    .some(rule => /grid-template-columns:\s*minmax\((?:10|8)em,1fr\)/.test(rule)),
    "表达学习卡片的标签列不能从 0 开始");
  const expression = read("expression-config.css");
  assert.ok(rulesFor(expression,
    ".expression-reaction-workspace>#reactionConfigWorkspace .wb-field-main")
    .some(rule => /grid-template-columns:\s*minmax\(8em,1fr\)/.test(rule)),
    "表情检索卡片的标签列不能从 0 开始");
});

test("产出页字段标签列不再从 0 开始", () => {
  assert.ok(rulesFor(read("output-page.css"), ".output-page .wb-field-main")
    .some(rule => /grid-template-columns:\s*minmax\(8em,1fr\)/.test(rule)),
    "产出页字段标签列不能从 0 开始");
});

test("所有 field-main 双列声明都不再让标签列从 0 开始", () => {
  const offenders = [];
  for (const name of fs.readdirSync(DIR).filter(file => file.endsWith(".css"))) {
    const source = read(name);
    for (const match of source.matchAll(/([^{}]+)\{[^}]*grid-template-columns:\s*minmax\(0,1fr\)\s+minmax\([^}]+\}/g)) {
      const selector = match[1].trim();
      if (/field-main|field\b|label/.test(selector)) offenders.push(name + " :: " + selector);
    }
  }
  assert.deepEqual(offenders, [], "这些选择器仍可能把字段标签列压成 0 宽：\n" + offenders.join("\n"));
});
