/* 创作书架不能把未装配的用户送进没有装配按钮的产出页。 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "..", "pages", "陪伴小屋", "app.js"), "utf8");

test("创作书架点击先打开带装配按钮的管理弹层", () => {
  assert.doesNotMatch(source, /if\(id==='books'\)\{navigate\('manage','output'\)/);
  assert.match(source, /if\(id==='books'\)\{openDetailDialog\('创作书架',contentWorkspaceSurface\(\)\);return;\}/);
  assert.match(source, /function contentWorkspaceSurface\(\)\{/);
  assert.match(source, /function bundledCapabilitySurface\(key\)/);
  assert.match(source, /command\('assemble','装配拓展','small primary'\)/);
  assert.match(source, /打开 Content 作品库/);
});

test("产出页 Content 卡片在未装配时也提供装配按钮", () => {
  assert.match(source, /function contentAssembleButton\(\)\{/);
  assert.match(source, /装配 Content 拓展/);
  assert.match(source, /data-builtin-extension="content" data-builtin-extension-action="assemble"/);
  assert.match(source, /outputProviderLine\('content','Content 授权'\)\}\$\{contentAssembleButton\(\)\}/);
  assert.match(source, /else if\(action==='assemble'\)render\(\);/);
});
