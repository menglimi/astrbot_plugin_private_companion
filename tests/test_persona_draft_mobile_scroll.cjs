/*
 * 手机端「整理角色资料」对话框必须整体滚动。
 *
 * 根因：桌面布局把 shell 固定成 96dvh，四/五行 grid 中输出区用
 * minmax(0,1fr) 吃掉剩余高度；手机端 compose 本身已经超过一屏，
 * 输出区和底部按钮被 dialog 的 overflow:hidden 直接裁掉，无法滑动。
 * 这里锁定手机断点里 dialog 自身可滚动、shell 不再固定高度、输出区
 * 不再单独裁切。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(path.join(__dirname, "..", "pages", "陪伴小屋", "self-page.css"), "utf8");
const start = source.indexOf("@media(max-width:650px){");
const next = source.indexOf("@media(max-width:380px)", start);
const body = start >= 0 ? source.slice(start, next > start ? next : start + 4000) : "";

test("手机端整理资料对话框自己可以纵向滚动", () => {
  assert.ok(body, "找不到 max-width:650px 的手机断点");
  assert.match(body, /\.role-profile-draft-dialog\{[^}]*overflow-y:auto/,
    "对话框必须 overflow-y:auto，否则 compose 一高就会裁掉下方内容");
  assert.match(body, /\.role-profile-draft-shell\{[^}]*max-height:none/,
    "手机端 shell 不能继续固定 96dvh 高度");
  assert.match(body, /\.role-profile-draft-output\{[^}]*overflow:visible/,
    "手机端输出区应跟随整页滚动，不再单独裁切");
});
