/*
 * 「全部功能」里的开关名称必须自解释。
 *
 * 回归背景：QQ 空间的 comment_enabled 曾被简写成「允许回复」，脱离
 * 「QQ 空间设置」上下文后看起来像普通聊天回复；同时没有 help 的字段
 * 会把字段键当作说明再打印一遍，出现两行同名代码。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const DIR = path.join(__dirname, "..", "pages", "陪伴小屋");
const editors = fs.readFileSync(path.join(DIR, "config-editor.js"), "utf8");
const app = fs.readFileSync(path.join(DIR, "app.js"), "utf8");

test("QQ 空间动作开关在名称里写明作用域", () => {
  assert.match(editors, /comment_enabled:\s*"允许回复空间评论"/);
  assert.match(editors, /like_enabled:\s*"允许给空间动态点赞"/);
  assert.match(editors, /publish_enabled:\s*"允许在空间发说说"/);
  assert.match(editors, /comment_enabled:\s*"[^"]*不影响私聊或群聊/);
});

test("全部功能把 QQ 空间动作归入独立分组", () => {
  assert.match(app, /"title":"QQ 空间"/);
  assert.match(app, /"keys":\[[^\]]*"comment_enabled"[^\]]*\]/);
});

test("长配置组必须启用分区侧栏，不能把主题铺成瀑布流", () => {
  assert.match(editors, /navigationLimit:\s*CONTACT_SECTIONS\.length/);
  assert.match(editors, /navigationLimit:\s*QZONE_SECTIONS\.length/);
});

test("没有说明的字段不再把字段键重复印成说明", () => {
  assert.doesNotMatch(app, /row\.help\|\|row\.field/);
});
