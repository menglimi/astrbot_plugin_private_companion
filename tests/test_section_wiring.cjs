/* 配置分区三方对账。
 *
 * 一个分区要在页面上真的能用，三处必须同时存在：
 *   1. 后端 astrbot_host/configuration_api.py 的 SECTIONS 里注册了编辑器
 *   2. 前端 config-editor.js 的 spec 里声明了它（标题、字段标签）
 *   3. app.js 里有 mountConfigEditor(..., ['它'])
 *
 * 缺第三处的后果最隐蔽：编辑器和声明都在，字段读得到、写得进，
 * 但页面上没有任何容器去渲染它——用户点不到，而且不会有任何报错。
 * busy_gate 就这样静默缺了七个字段（含关键词、延迟窗口、主动恢复缓冲），
 * 从功能上看是好的，从界面上看是不存在的。
 *
 * 用 node --test 运行。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.join(__dirname, "..");
const PAGE = path.join(ROOT, "pages", "陪伴小屋");
const BACKEND = path.join(ROOT, "astrbot_host", "configuration_api.py");
/* system.prompts is edited by maintenance-page.js through its own
   sections=system.prompts request; it is not a config-editor card. Keep it
   out of the config-editor three-way count instead of pretending it has a
   spec that would never be mounted. */
const MAINTENANCE_SECTIONS = new Set(["system.prompts"]);

function backendSections() {
  const source = fs.readFileSync(BACKEND, "utf8");
  const block = source.match(/SECTIONS = \{([\s\S]*?)\n\n/);
  assert.ok(block, "configuration_api.py 里找不到 SECTIONS 定义");
  /* 每个分区形如 "组名": XxxManagement, */
  return [...block[1].matchAll(/"([a-z_.]+)"\s*:\s*[A-Za-z]+Management/g)]
    .map(match => match[1])
    .filter(name => !MAINTENANCE_SECTIONS.has(name));
}

function frontendSpec() {
  const context = vm.createContext({window: {}, document: {}, sessionStorage: {}, Intl, Date, Object});
  vm.runInContext(fs.readFileSync(path.join(PAGE, "workbench-page.js"), "utf8"), context);
  vm.runInContext(fs.readFileSync(path.join(PAGE, "config-editor.js"), "utf8"), context);
  return Object.keys(context.window.CompanionConfigEditors.spec);
}

function appMounts() {
  const source = fs.readFileSync(path.join(PAGE, "app.js"), "utf8");
  const mounted = new Set();
  /* 同时匹配单引号与双引号两种写法。 */
  for (const group of source.matchAll(/mountConfigEditor\([^,]+,\s*\[([^\]]*)\]/g)) {
    for (const kind of group[1].matchAll(/["']([^"']+)["']/g)) mounted.add(kind[1]);
  }
  if (/mountSelfRelationshipEditor\(\$\('#relationshipProfileConfigWorkspace'\)\)/.test(source))
    mounted.add("relationship_profile");
  return mounted;
}

test("后端 SECTIONS 的每个分区都在前端 spec 里声明", () => {
  const backend = backendSections();
  const spec = new Set(frontendSpec());
  const missing = backend.filter(name => !spec.has(name));
  assert.deepStrictEqual(missing, [],
    `后端注册了但这些分区在前端 spec 里没有声明：${missing.join(", ")}`);
});

test("前端 spec 的每个分区都在后端注册过", () => {
  const spec = frontendSpec();
  const backend = new Set(backendSections());
  const extra = spec.filter(name => !backend.has(name));
  assert.deepStrictEqual(extra, [],
    `前端声明了这些分区但后端没有注册：${extra.join(", ")}`);
});

test("每个分区都有挂载点", () => {
  /* 这条是 busy_gate 缺口的护栏。三方里少任何一处都会让字段「点不到」，
     而缺挂载点最难发现——没有报错，只是页面上没有那个容器。 */
  const spec = frontendSpec();
  const mounted = appMounts();
  const unmounted = spec.filter(name => !mounted.has(name));
  assert.deepStrictEqual(unmounted, [],
    `以下分区有编辑器、有声明，却没有挂载点，字段在页面上点不到：${unmounted.join(", ")}`);
});

test("挂载的 kind 都在 spec 里存在", () => {
  /* 反向：挂了一个拼错的名字，页面会渲染一个空的分区而不报错。 */
  const spec = new Set(frontendSpec());
  const unknown = [...appMounts()].filter(name => !spec.has(name));
  assert.deepStrictEqual(unknown, [],
    `app.js 挂载了 spec 里不存在的分区名：${unknown.join(", ")}`);
});

test("三方数量一致", () => {
  const backend = backendSections();
  const spec = frontendSpec();
  const mounted = appMounts();
  assert.strictEqual(spec.length, backend.length,
    `后端 ${backend.length} 个分区，前端 spec ${spec.length} 个`);
  assert.strictEqual(mounted.size, spec.length,
    `spec ${spec.length} 个分区，实际挂载 ${mounted.size} 个`);
});

/* === 孤立模块 ===
 *
 * 第三处缺口的另一个变体，比「缺挂载点」更难发现：**整份模块从来没被加载过**。
 * backup-page.js 就是这样——19 KB、六条真实路由、一台两阶段状态机都写完了，
 * 但 index.html 里没有它的 <script>，于是 renderData() 插的那个
 * #backupWorkspace 永远停在「正在读取快照列表…」。
 *
 * 这不会报错，也不会显示任何异常：一个永远转圈的加载提示看起来就像网慢。
 * 所以这里对账「页面上出现过的挂载容器」与「真的被加载的模块」。
 */
function pageModules() {
  const html = fs.readFileSync(path.join(PAGE, "index.html"), "utf8");
  /* src="xxx.js?v=..." —— 只取文件名，去掉查询串。
     注意要允许路径里的斜杠（assets/lucide.min.js），只截到 ? 为止。 */
  return new Set([...html.matchAll(/src="([^"?]+\.js)(?:\?[^"]*)?"/g)]
    .map(match => path.basename(match[1])));
}

function mountContainers() {
  /* app.js 里所有 $('  #xxx ') 探测式挂载：它插什么容器就挂什么容器。
     取的是「探测的容器 id」而不是「挂载的模块」，两边都要能列出。 */
  const source = fs.readFileSync(path.join(PAGE, "app.js"), "utf8");
  return new Set([...source.matchAll(/\$\('#([a-zA-Z]+Workspace|backupWorkspace|setupGuideHost|memorySettingsHost)'\)/g)]
    .map(match => match[1]));
}

test("已使用的页面模块都被 index.html 加载", () => {
  const loaded = pageModules();
  const consumers = [...loaded].filter(file => fs.existsSync(path.join(PAGE, file)))
    .map(file => fs.readFileSync(path.join(PAGE, file), "utf8"));
  const declared = new Map();
  /* 模块自己声明的全局名，用来判断它属于哪个文件。 */
  for (const entry of fs.readdirSync(PAGE)) {
    if (!entry.endsWith(".js")) continue;
    const source = fs.readFileSync(path.join(PAGE, entry), "utf8");
    const global = source.match(/window\.(Companion[A-Za-z]+Page)\s*=/);
    if (global) declared.set(global[1], entry);
  }
  /* 只有被当前入口脚本引用的模块才有可用性风险；未接入的独立模块不代表
     活跃页面缺少依赖。容器与挂载清单继续由下面两条测试逐一核对。 */
  const orphans = [...declared].filter(([global, file]) => !loaded.has(file)
    && consumers.some(source => source.includes(global))).map(([, file]) => file);
  assert.deepStrictEqual(orphans, [],
    `这些模块声明了页面对象却从未被加载，挂载点会永远停在加载中：${orphans.join(", ")}`);
});

test("每个挂载容器都有对应的模块被加载", () => {
  /* 反向：app.js 探测了一个容器、却没有任何模块会去填它。 */
  const containers = mountContainers();
  const app = fs.readFileSync(path.join(PAGE, "app.js"), "utf8");
  const unfilled = [...containers].filter(id => {
    /* 容器 id 出现在 app.js 的挂载调用里才算有人填。 */
    /* setupGuideHost 由一个包装函数挂，不是直接调模块，所以单独看它。 */
    if (id === "setupGuideHost") return !app.includes("mountSetupGuide()");
    const owners = {backupWorkspace: "CompanionBackupPage",
      memorySettingsHost: "CompanionMemorySettingsSection", memoryWorkspace: "CompanionMemoryPage",
      modelWorkspace: "CompanionModelsPage", semanticWorkspace: "CompanionSemanticPage",
      replyWorkspace: "CompanionReplyPage", roleStateWorkspace: "CompanionStatePage",
      /* 自带版式的整页模块：容器由各自的渲染函数产出，模块在面板挂载清单里挂。 */
      contactWorkspace: "CompanionContactPage", learningWorkspace: "CompanionLearningPage"};
    const owner = owners[id];
    return owner ? !app.includes(owner + "?.mount(") : false;
  });
  assert.deepStrictEqual(unfilled, [],
    `这些容器在 app.js 里被探测却没有模块去填：${unfilled.join(", ")}`);
});

/* === 自带版式的整页模块 ===
 *
 * 上面那条只保证「容器有人填」，不保证**填它的那条路径还活着**。
 *
 * 2026-09-30 缺口：外壳重排后左栏走 section 面板，而主动与联系、学习与反思的
 * 挂载仍只写在老的 manage 路由分支里（按 manageTab 判断）。于是从新版小屋点
 * 「主动与联系」，面板渲染出的是空的 <section id="contactWorkspace">，钟面永远
 * 不出现——没有报错、没有加载提示，就是一块空白，看起来像这一页还没做完。
 *
 * 这类缺口不会自己暴露：渲染函数在、容器在、模块也在，只有「谁去挂它」这一环
 * 掉了。所以这里按「渲染函数产出什么容器」对账「面板挂载清单挂了什么」。 */
test("渲染出的每个工作区容器都在面板挂载清单里", () => {
  const app = fs.readFileSync(path.join(PAGE, "app.js"), "utf8");
  const panel = app.match(/function mountPanelWorkspaces\(\)\{([\s\S]*?)\n\}/);
  assert.ok(panel, "app.js 里找不到 mountPanelWorkspaces()");
  /* 配置分区由 renderConfigEditors() 统一挂载，它和 mountPanelWorkspaces() 是同一环
     的两半：面板先调 renderConfigEditors()，后者再逐个探测容器。只扫前者会把所有
     <section id="…ConfigWorkspace"> 误判成孤儿——groupInterjectionConfigWorkspace
     就是这样被报成「面板会是一块空白」的，而它其实挂得好好的。 */
  const editors = app.match(/function renderConfigEditors\(\)\{([\s\S]*?)\n\}/);
  const mounted = panel[1] + (editors ? editors[1] : "");

  /* 只取字面量 id：拼出来的（模板变量）对不上也不该对，那属于另一类接线。 */
  const rendered = new Set([...app.matchAll(/<section id="([a-zA-Z]+Workspace)"/g)]
    .map(match => match[1]));
  assert.ok(rendered.size > 0, "app.js 里没有任何 <section id=\"…Workspace\">，这条对账失去意义");

  const missing = [...rendered].filter(id => !mounted.includes("#" + id));
  assert.deepStrictEqual(missing, [],
    `这些工作区容器由渲染函数产出却不在面板挂载清单里，面板会是一块空白：${missing.join(", ")}`);
});
