/* The shared host is the only plugin runtime; these checks guard that contract.
 *
 * 备份与 V1 迁移都走共享宿主注册表；迁移按角色挂载，页面先选目标与角色，
 * 再对着那个角色的地址侦察、试算、执行。
 *
 * 已知差异（由本文件第一条用例锁住，变了就会红）：
 *   账号与会话：`AccountSessionWebAPI` 读取原生会话；账号下的角色包住人格，
 *     保存映射后自动接入窗口，不再要求手动操作运行层。
 *   多目标专属：`GroupMemoryWebAPI`、`GroupReplyWebAPI`（群公共记忆与群回复）
 *
 * 目录和页面都不依赖运行模式字段；域路由由当前目标目录提供。
 *
 * 用 node --test 运行。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..");
const HOST = path.join(ROOT, "astrbot_host");
const PAGE = path.join(ROOT, "pages", "陪伴小屋");

const read = file => fs.readFileSync(file, "utf8");
const registeredApis = source => new Set([...source.matchAll(/(\w+WebAPI)\s*\(/g)]
  .map(match => match[1]).filter(name => name !== "StagingWebAPI"));

test("the plugin entry and management page use the shared host only", () => {
  const multi = registeredApis(read(path.join(HOST, "multi_api.py")));
  const entry = read(path.join(ROOT, "main.py"));
  const hostPage = read(path.join(ROOT, "pages", "宿主管理", "app.js"));
  assert.match(entry, /self\.staging = AstrBotMultiController\(/);
  assert.doesNotMatch(entry, /controller = AstrBotMultiController if/);
  assert.doesNotMatch(hostPage, /prefix: "staging"|mode === "single"|mode !== "multi"/);
  assert.match(hostPage, /api\("POST", accountApi, \{enabled: true, accounts,\s*personas, groups: bindingConfig\?\.groups \|\| \[\]\}\)/);
  const mappingSave = hostPage.slice(hostPage.indexOf("async function saveBindingConfig()"),
    hostPage.indexOf("function renderStatus("));
  assert.doesNotMatch(mappingSave, /sessions\s*:/, "role-only edits must preserve native private routes");
  assert.doesNotMatch(hostPage, /accounts\/session|bindingWindowRow|runAction\(/);
  const html = read(path.join(ROOT, "pages", "宿主管理", "workspace.html"));
  assert.match(html, /账号 → 角色 \/ 人格 → 会话窗口/);
  assert.doesNotMatch(html, /id="(?:probe|start|stop|save-session|edit-sessions)"/);
  assert.ok(multi.has("AccountSessionWebAPI"));
  assert.ok(multi.has("GroupMemoryWebAPI") && multi.has("GroupReplyWebAPI"));
});

test("an unanswered mapping save releases the editor without replaying it", () => {
  const hostPage = read(path.join(ROOT, "pages", "宿主管理", "app.js"));
  assert.match(hostPage, /host_operation_result_unknown/);
  assert.match(hostPage, /Promise\.race\(\[pending, timeout\]\)/);
  assert.match(hostPage, /请求暂未返回，当前无法确认最终状态/);
  assert.match(hostPage, /finally\s*\{\s*setBusy\(false\);\s*updateBindingCheck\(\);\s*\}/);
});

test("the topology does not turn unknown sessions into the default persona", () => {
  const topology = read(path.join(ROOT, "pages", "宿主管理", "app.js"));
  assert.match(topology, /name === "\[%None\]" \? "未指定人格" : !name \? "未确定人格"/);
  assert.match(topology, /target\.persona_override \|\| target\.persona_id \|\| ""/);
  assert.doesNotMatch(topology, /listedPersonas\[0\]/,
    "a role may contain several personas, so its first persona cannot stand in for a session");
  assert.doesNotMatch(topology, /target\.persona_id \|\| "\[%None\]"/,
    "a session without a recognized persona cannot be marked as using AstrBot's no-persona entry");
  assert.doesNotMatch(topology, /persona\?\.persona_id \|\| "\[%None\]"/,
    "an unknown persona must not appear to have native windows");
});

test("the backup entry is available and its restore preview explains scope", () => {
  const app = read(path.join(PAGE, "app.js"));
  const at = app.indexOf('id="backupWorkspace"');
  assert.ok(at > 0, "app.js 里找不到备份工作区的挂载点");
  const fnStart = app.indexOf("function renderDataOriginal");
  assert.ok(fnStart > 0 && fnStart < at, "找不到包含备份挂载点的渲染函数");
  const head = app.slice(fnStart, at);
  /* Backups apply to the shared installation and stay outside target branches. */
  assert.doesNotMatch(head, /multi\s*\?/, "备份入口不再按模式分支");
  assert.match(app, /function renderHostWorkspace\(\)/);

  /* 还原是整根替换，影响面必须写给用户：后端随预览下发 scope，页面渲染它。
     话术不锁死（它改过一轮：从「影响范围」改成直接说清会替换掉什么），
     锁的是"这一行确实由后端下发的 scope 驱动、并且点名了目标与角色数"。 */
  const api = read(path.join(HOST, "backup_api.py"));
  assert.match(api, /_installation_scope/, "后端要下发这次还原覆盖多少目标与角色");
  assert.match(api, /"scope": self\._installation_scope\(\)/);
  const page = read(path.join(PAGE, "backup-page.js"));
  assert.match(page, /state\.scope/, "备份页要渲染后端给的影响范围");
  assert.match(page, /backup-preview-scope/);
  assert.match(page, /scope\.targets/, "影响面要点名覆盖几个目标");
  assert.match(page, /scope\.roles/, "影响面要点名覆盖几个角色");
});

test("迁移始终先选目标与角色，地址来自页面目录", () => {
  const source = read(path.join(PAGE, "setup-guide.js"));
  assert.doesNotMatch(source, /targetsMode|state\.mode\b|setup-mode-badge/);
  /* 路由按角色挂载：地址从页面目录取，并且必须交给页面桥的**插件内相对路径**
     约定（前导斜杠与插件目录名都不能出现）。用哪个 helper 实现不锁死：
     `product().relative()` 与手写剥前缀都算数，锁的是"别把绝对地址交出去"。 */
  assert.match(source, /function legacyChoices\(\)/, "多目标下要能列出可选的目标与角色");
  assert.match(source, /function legacyAddress\(\)/);
  const normalized = source.includes("relative(role.legacy_api)")
    || /legacy_api\)\s*\.replace\(/.test(source)
    || /\.replace\(\s*\/\^\\\//.test(source);
  assert.ok(normalized, "地址要相对路径化（交给页面桥的必须是插件内相对路径）");
  /* 第 1 步先解释角色，再给当前角色卡或页内创建；没角色时不只是一个空按钮。 */
  assert.match(source, /function legacyRoleCardMarkup\(\)/, "第 1 步要有角色引导卡");
  assert.match(source, /还没有可接收旧版资料的角色/, "没选角色时要显示引导创建说明");
  assert.match(source, /data-go="manage" data-manage="host"/, "没角色时要能去角色管理新建");
  /* 三个动作都要走 legacyAddress()，没有地址就不发请求。 */
  for (const action of ["legacy-scan", "legacy-plan", "legacy-apply"]) {
    const at = source.indexOf(`action === "${action}"`);
    assert.ok(at > 0, `找不到 ${action} 的处理`);
    assert.match(source.slice(at, at + 200), /if \(!legacyAddress\(\)\)/,
      `${action} 必须在地址为空时直接返回`);
  }
  /* 试算阶段就报缺哪些生效范围。 */
  assert.match(source, /function legacyScopeStatus/, "缺的生效范围要在试算/侦察阶段列出来");
});
