/* 页面样式是否真的"被引用"。
 *
 * index.html 现在只加载一份 bundle.css —— 它由 css-sources.json 里列出的文件
 * 按顺序拼成（tools/sync_framework_page.py），首屏因此只有一次样式请求。
 * 所以「某张样式表生效」这件事现在由两步保证：
 *   1. 它在 css-sources.json 里；
 *   2. index.html 带缓存串引用 bundle.css。
 * 几个样式测试都要查这件事，放在这里，免得各写一遍、各漏一处。
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const DIR = path.join(__dirname, "..", "pages", "陪伴小屋");

function bundledSources() {
  return JSON.parse(fs.readFileSync(path.join(DIR, "css-sources.json"), "utf8"));
}

function assertStylesheetBundled(name, detail) {
  assert.ok(bundledSources().includes(name),
    `css-sources.json 没有列出 ${name}：${detail || "这张表的规则不会进 bundle.css"}`);
  assert.match(fs.readFileSync(path.join(DIR, "index.html"), "utf8"),
    /href="bundle\.css\?v=[\w.-]+"/,
    "index.html 没有带缓存串引用 bundle.css");
}

module.exports = {DIR, bundledSources, assertStylesheetBundled};
