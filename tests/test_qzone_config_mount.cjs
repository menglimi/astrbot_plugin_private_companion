/* #310 QQ 空间配置编辑器的挂载时序：首次进入时它到底挂没挂上。
 *
 * 用户看到的是「QQ 空间设置」那一块永远停在「正在读取设置…」，或者干脆没有。
 * 这不是编辑器本身的问题（config-editor.js 有自己的 test_config_editor.cjs），
 * 而是**谁在什么时候把编辑器挂到挂载点上**的问题：
 *
 *   paintPanel()                     整块画面板。此刻状态还没读到，qzoneBody()
 *                                    只画一句「正在读取…」，#qzoneConfigWorkspace
 *                                    **还不存在**；
 *   mountPanelWorkspaces()           「配置分区」那一趟按容器探测 → 什么都没得挂；
 *                                    同一趟最后才 mountQzoneSection() 发起异步读状态；
 *   异步读到状态                      qzoneRun 的 finally 调 paintQzone()，
 *                                    整块重写 #actionQzoneBody —— 这时
 *                                    #qzoneConfigWorkspace 才出现，而没有任何人
 *                                    再挂一次编辑器。
 *
 * 于是挂载点里留着 configEditors() 的初始内容（consoleLoading('正在读取设置…')），
 * 一句永远等不到的假话。同理，paintQzone() 每次重画（切刷空间范围、点赞/回复完成、
 * 停用二次确认…）都会把已挂好的编辑器连同旧节点一起丢掉。
 *
 * 本文件**真跑** app.js：把它装进一个「只认 id」的极简 DOM，用真实的
 * paintPanel()/paintQzone()/mountPanelWorkspaces() 复现时序，不用浏览器、不用 jsdom。
 * CompanionConfigEditors 用替身顶替——它是有独立测试的协作者模块，这里要验的是
 * 「何时挂、挂几次、卸没卸」，替身只做真模块的合同：create(kinds, getRoot, views,
 * endpoint) 返回带 restore/paint/read/unmount 的句柄，接管 root 的内容，并模拟
 * 工作台对未保存草稿的保存与恢复。
 *
 * 用 node --test 运行。
 *
 * 同一个「真跑 app.js」的装置也用来验凭据行：它是面板上另一处曾经说假话的
 * 地方（「尚未使用 / 没有可用来源」）。刷新凭据的响应可能带着请求开始时的
 * 旧快照，所以页面必须在成功之后重读一次状态，并且 unknown 只能说「尚未检查」。
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const DIR = path.join(__dirname, "..", "pages", "陪伴小屋");
const APP = fs.readFileSync(path.join(DIR, "app.js"), "utf8");
const INDEX = fs.readFileSync(path.join(DIR, "index.html"), "utf8");
const STATIC_IDS = [...INDEX.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);

/* 取一段 HTML 里某个 id 元素的内层 HTML（标签配平）。生成出来的标记都是规整的
   开闭标签，够用；不追求通用，只为能断言「挂载点里现在是什么」。 */
function innerHtmlOf(html, id) {
  const open = new RegExp(`<([a-z][\\w-]*)\\b[^>]*\\bid="${id}"[^>]*>`).exec(html);
  if (!open) return null;
  const rest = html.slice(open.index + open[0].length);
  const closing = new RegExp(`<(/?)${open[1]}\\b[^>]*>`, "g");
  let depth = 1;
  let match;
  while ((match = closing.exec(rest))) {
    depth += match[1] ? -1 : 1;
    if (depth === 0) return rest.slice(0, match.index);
  }
  return rest;
}

/* 一个「只认 id」的 DOM 替身。真实浏览器里 innerHTML 一换，里面的节点就是**新节点**；
   这里照这个语义办：谁被重新渲染，谁整棵子树里的旧节点就作废，id 指向新节点。
   节点身份是新是旧正是本文件要验的东西，所以不能把同一个对象一直复用。 */
function createDom({omitStatic = []} = {}) {
  const nodes = new Map();
  function makeElement(id) {
    const element = {
      id: id || "", dataset: {}, style: {}, hidden: false, open: false,
      value: "", textContent: "", className: "", src: "", tabIndex: 0,
      classList: {add() {}, remove() {}, toggle() {}, contains: () => false},
      _html: "", _live: true, _descendants: new Set(),
      addEventListener() {}, removeEventListener() {}, dispatchEvent() {},
      setAttribute() {}, removeAttribute() {}, getAttribute: () => null, hasAttribute: () => false,
      appendChild() {}, removeChild() {}, insertBefore() {}, replaceChildren() {},
      querySelector: () => null, querySelectorAll: () => [], closest: () => null, contains: () => false,
      focus() {}, blur() {}, click() {}, remove() {}, scrollIntoView() {},
      replaceWith(next) {
        element._live = false;
        if (next && next.id) { nodes.set(next.id, next); next._live = true; }
      },
      getBoundingClientRect: () => ({width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0}),
      get isConnected() { return element._live; },
      get innerHTML() { return element._html; },
      set innerHTML(value) {
        element._html = String(value ?? "");
        const doomed = new Set();
        const collect = ids => {
          for (const id of ids) {
            if (doomed.has(id)) continue;
            doomed.add(id);
            const node = nodes.get(id);
            if (node) collect(node._descendants);
          }
        };
        collect(element._descendants);
        for (const id of doomed) {
          const stale = nodes.get(id);
          if (stale) stale._live = false;
        }
        const ids = [...element._html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
        element._descendants = new Set(ids);
        for (const childId of ids) {
          const fresh = makeElement(childId);
          fresh._html = innerHtmlOf(element._html, childId) ?? "";
          fresh._descendants = new Set([...fresh._html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]));
          nodes.set(childId, fresh);
        }
      },
    };
    return element;
  }
  for (const id of STATIC_IDS) {
    if (!omitStatic.includes(id)) nodes.set(id, makeElement(id));
  }
  const document = {
    readyState: "complete", hidden: false,
    documentElement: makeElement(""), head: makeElement(""), body: makeElement(""), title: "",
    addEventListener() {}, removeEventListener() {},
    getElementById(id) {
      const element = nodes.get(id);
      return element && element._live ? element : null;
    },
    querySelector(selector) {
      const id = /^#([\w-]+)$/.exec(String(selector).trim());
      return id ? document.getElementById(id[1]) : null;
    },
    querySelectorAll: () => [],
    createElement: () => makeElement(""),
    createTextNode: () => makeElement(""),
    createComment: () => makeElement(""),
    createDocumentFragment: () => makeElement(""),
  };
  return {document};
}

const ROLE = {
  role_id: "role-first", role_name: "小伴",
  config_api: "/astrbot_plugin_private_companion/targets/first/roles/role-first/config",
  reply_api: "/astrbot_plugin_private_companion/targets/first/roles/role-first/reply",
  state_api: "/astrbot_plugin_private_companion/targets/first/roles/role-first/state",
  life_api: "/astrbot_plugin_private_companion/targets/first/roles/role-first/life",
  growth_api: "/astrbot_plugin_private_companion/targets/first/roles/role-first/growth",
  memory_api: "/astrbot_plugin_private_companion/targets/first/roles/role-first/memory",
  relationships_api: "/astrbot_plugin_private_companion/targets/first/roles/role-first/relationships",
};

function qzoneStatus() {
  return {
    installed: true, active: true, role_enabled: true, package_version: "1.0.0",
    /* 进程刚启动、还没取过一次凭据时的真实初值：unknown 只表示「还没检查」，
       不是「没有可用来源」。 */
    credential: {state: "unknown", source: "none", uin_masked: "", reason: ""},
    consent: {feed: true, like: true, comment: true, publish: true},
    runtime: {settings: {enabled: true, like_enabled: true, comment_enabled: true, publish_enabled: true}},
    catalogue: {scenes: {}, topics: []},
  };
}

/* 装页面脚本。app.js 是页面脚本，最后三行（probeHost/syncFromHash/armHomeAgendaClock）
   会把整页真正跑起来；本文件只验面板挂载时序，所以让加载停在启动段之前：缺
   #globalSearch 时它在 $('#globalSearch').addEventListener 处抛 TypeError。这与
   tests/test_app_globals.cjs 里「加载期缺宿主 API 不算缺陷」是同一处理，只是这里
   抛点必须落在启动段——若哪天顶层代码更早中断，下面的 paintPanel 取不到，测试会
   立刻以另一种方式失败，不会被静默放过。 */
/* configEditorsCreatable=false 模拟 create() 返回 null：缺 endpoint、缺
   CompanionWorkbench，或请求的分区没有声明。qzone 目前在 config-editor.js 的
   spec 里已经声明，所以这里模拟的是「创建失败」，不是「spec 里少了 qzone」。 */
function harness({configEditorsCreatable = true} = {}) {
  const {document} = createDom({omitStatic: ["globalSearch"]});
  const editors = [];
  /* 真工作台会把未保存的草稿按分区写进 sessionStorage，重挂时再恢复。这里用
     一张 Map 做同样的事：这样「卸载后重挂」的实现也能通过草稿断言，而不是只有
     「把节点搬回新位置」那一种实现能过。 */
  const savedEditorState = new Map();
  const requests = [];
  const status = qzoneStatus();
  const shell = {section: "action", furniture: null, view: null,
    selectSection() {}, selectView() {}, selectFurniture() {}};
  const product = {
    role: () => ROLE, roleLabel: () => "小伴", scopeKey: () => "first|role-first",
    appearance: () => ({motion: "full"}), targets: () => [], manifest: null,
    connect: () => new Promise(() => {}),
    bridgeNow: () => true, bridgeFor: async () => {},
    request: async (method, route, body) => { requests.push({method, route, body}); return status; },
    relative: route => route, workspace: name => `<p>${name}</p>`, workspaceHint: () => "",
    syncLight: () => null, lightOf: () => null, capability: () => ({assembled: false}),
    unmountWorkspace() {},
    statusLabel: () => "", stateLabel: () => "", errorLabel: () => "",
    extension: () => null, providerDetail: () => null, followLight: () => null,
    setFollowLight() {}, setAppearance() {}, mountWorkspace() {}, refreshSummary() {},
    unavailableReason: () => "", when: () => "", data: () => null,
    connection: () => null, hosted: () => false, target: () => null, roleTarget: () => null,
    targetLabel: () => "", subjectLabel: () => "", workspaceAddress: () => "",
    stateValues: () => null, lifeActivities: () => null, lifeJournals: () => null,
  };
  const window = {
    document, console, navigator: {language: "zh-CN"},
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    sessionStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    addEventListener() {}, removeEventListener() {},
    matchMedia: () => ({matches: false, addEventListener() {}, removeEventListener() {}}),
    location: {hash: "", href: "http://localhost/", search: "", pathname: "/"},
    history: {replaceState() {}, pushState() {}},
    innerWidth: 1440, innerHeight: 900, devicePixelRatio: 1, scrollY: 0,
    getComputedStyle: () => ({getPropertyValue: () => ""}),
    requestAnimationFrame: callback => { callback(0); return 0; }, cancelAnimationFrame() {},
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    CompanionProduct: product,
    CompanionShell: shell,
    CompanionConfigEditors: {
      create(kinds, getRoot, views, endpoint) {
        void views;
        if (!configEditorsCreatable) return null;
        const root = getRoot();
        const isQzone = kinds.includes("qzone");
        const stateKey = `${endpoint}|${kinds.join(",")}`;
        const saved = savedEditorState.get(stateKey) || {draft: "", token: ""};
        const record = {kinds, endpoint, root, unmounts: 0, reads: 0, draft: saved.draft, token: saved.token, __testQzone: isQzone};
        editors.push(record);
        if (root) root.innerHTML = `<div data-config-host="${kinds.join(",")}"></div>`;
        return {
          __testQzone: isQzone,
          restore() {}, paint() {},
          read() { record.reads += 1; },
          unmount() {
            record.unmounts += 1;
            savedEditorState.set(stateKey, {draft: record.draft, token: record.token});
          },
        };
      },
    },
  };
  const context = vm.createContext({
    window, document, console, navigator: window.navigator,
    localStorage: window.localStorage, sessionStorage: window.sessionStorage,
    location: window.location, history: window.history,
    setTimeout: window.setTimeout, clearTimeout: window.clearTimeout,
    setInterval: window.setInterval, clearInterval: window.clearInterval,
    queueMicrotask, structuredClone,
    requestAnimationFrame: window.requestAnimationFrame, cancelAnimationFrame() {},
    matchMedia: window.matchMedia, getComputedStyle: window.getComputedStyle,
    Intl, URL, URLSearchParams, TextEncoder, TextDecoder,
  });
  let loadFailure = null;
  try { vm.runInContext(APP, context, {filename: "app.js"}); }
  catch (error) { loadFailure = error; }

  const run = code => vm.runInContext(code, context);
  const host = () => document.getElementById("qzoneConfigWorkspace");
  const qzoneEditors = () => editors.filter(record => record.__testQzone);
  const live = () => qzoneEditors().filter(record => record.unmounts === 0);
  const mounted = () => [...run("configEditorMounts")].filter(mount => mount?.__testQzone);
  const settle = async () => {
    for (let round = 0; round < 4; round += 1) await new Promise(resolve => setImmediate(resolve));
  };
  return {context, document, run, host, live, mounted, qzoneEditors, settle, editors, requests, status, shell, product,
    loadFailure};
}

/* 加载期那一抛必须是启动段的那一抛：函数都在，页面还没开始跑。 */
function loadPage(options) {
  const page = harness(options);
  assert.ok(page.loadFailure === null || /addEventListener/.test(page.loadFailure.message),
    `app.js 加载在预期之外的地方中断：${page.loadFailure?.message}`);
  for (const name of ["paintPanel", "paintQzone", "mountPanelWorkspaces", "renderConfigEditors"]) {
    assert.equal(typeof page.context[name], "function", `app.js 里取不到 ${name}()`);
  }
  assert.equal(page.run("qzoneState.phase"), "idle", "QQ 空间状态初始应为 idle");
  return page;
}

/* 进入行动页并等异步状态读完——这正是缺陷发生的那条路。 */
async function enterActionPage(page, {warm = false} = {}) {
  page.run("paintPanel()");
  await page.settle();
  assert.equal(page.run("qzoneState.phase"), "ready", "异步状态没有读完，后面的事件顺序就不是要验的那个了");
  if (warm) {
    page.run("paintPanel()");
    await page.settle();
  }
}

function assertSingleEditorOnCurrentHost(page) {
  const host = page.host();
  assert.ok(host, "当前应当有 QQ 空间配置挂载点");
  const live = page.live();
  assert.equal(live.length, 1, `应当恰好有一个活着的 QQ 空间配置编辑器，实际 ${live.length} 个`);
  assert.equal(live[0].root.isConnected, true, "活着的编辑器挂在已经从文档里被换掉的节点上");
  assert.ok(live[0].root === host, "活着的编辑器不是当前这个挂载点节点");
  assert.deepEqual([...live[0].kinds], ["qzone"]);
  assert.equal(page.qzoneEditors().filter(record => record.root === host).length, 1,
    "同一个挂载点节点上建出了第二个编辑器");
  assert.equal(page.mounted().length, 1, "configEditorMounts 里应当恰好留一个 QQ 空间挂载");
}
function assertNoEditorOnDeadNode(page) {
  for (const record of page.qzoneEditors()) {
    if (record.unmounts === 0) {
      assert.ok(record.root === page.host(), "有挂载被留在了已经丢弃的节点上（监听不会有人摘）");
    } else {
      assert.equal(record.unmounts, 1, "同一个挂载被卸载了不止一次");
    }
  }
}

test("首次进入：异步读到状态后，配置编辑器要挂到刚出现的挂载点上", async () => {
  const page = loadPage();
  page.run("paintPanel()");
  /* 这一刻状态还没读完。当前实现在状态未就绪时根本画不出挂载点；以后若改成
     先画配置区骨架，这里也不该判失败——真正要验的是读完状态之后的结果。 */
  assert.notEqual(page.run("qzoneState.phase"), "ready",
    "paintPanel() 之后状态已经读完，验的就不是首次进入的时序了");

  await page.settle();
  assert.equal(page.run("qzoneState.phase"), "ready");
  assert.ok(page.requests.some(call => call.method === "GET" && call.route === "qzone-extension/status"),
    "状态应当是通过 qzone-extension/status 这条桥路由异步读到的");
  const host = page.host();
  assert.ok(host, "读到状态后 qzoneBody() 应该画出 #qzoneConfigWorkspace");
  assert.doesNotMatch(host.innerHTML, /正在读取设置/,
    "配置区停在「正在读取设置…」：挂载点出现了，但没有人把编辑器挂上去");
  assertSingleEditorOnCurrentHost(page);
});

test("重复重画：每次都只有一个编辑器，且挂在当前节点上，旧节点上不留活挂载", async () => {
  const page = loadPage();
  await enterActionPage(page, {warm: true});
  assertSingleEditorOnCurrentHost(page);

  page.run("paintQzone()");
  assertSingleEditorOnCurrentHost(page);
  assertNoEditorOnDeadNode(page);

  /* 两次重画挤在同一轮里（中间没有等异步）：第二次换掉的正是第一次刚建好的
     节点。这一趟同时覆盖原来的「并发两次重画」用例。 */
  page.run("paintQzone();paintQzone()");
  assertSingleEditorOnCurrentHost(page);
  assertNoEditorOnDeadNode(page);
});

test("重画不丢草稿与已读令牌：用户正在改的内容留在原地", async () => {
  const page = loadPage();
  await enterActionPage(page, {warm: true});
  const editor = page.live()[0];
  /* 模拟用户正在编辑器里改：草稿与「已读到的 edit_token」都只存在实例里。 */
  editor.draft = "改到一半的草稿";
  editor.token = "a".repeat(64);

  /* 例：用户点了「刷新凭据」——qzoneRun 的 finally 会走一次 paintQzone()。 */
  page.run("paintQzone()");

  const host = page.host();
  assert.ok(host, "重画之后挂载点应当还在");
  assert.equal(page.live().length, 1, "重画之后应当还是一个活着的编辑器");
  const kept = page.live()[0];
  /* 这里只认用户能看到的结果：草稿和已读取的令牌还在，编辑器挂在当前挂载点上。
     搬节点、或卸载后重挂（替身会像工作台那样恢复草稿与令牌），两种实现都应当
     通过；不要求必须是同一个实例或同一个 DOM 节点。 */
  assert.ok(kept.root === host, "活着的编辑器不在当前挂载点上");
  assert.equal(kept.draft, "改到一半的草稿", "重画把未保存的草稿丢了");
  assert.equal(kept.token, "a".repeat(64), "重画把已读到的 edit_token 丢了");
});

test("离开再进入：离开时卸载，回来时挂在当前节点上", async () => {
  const page = loadPage();
  await enterActionPage(page, {warm: true});
  assertSingleEditorOnCurrentHost(page);

  page.shell.section = "output";
  page.run("paintPanel()");
  await page.settle();
  assert.equal(page.host(), null, "离开行动页后不该还有 QQ 空间挂载点");
  assert.equal(page.live().length, 0, "挂载点没了，编辑器必须卸载，不能挂在已丢弃的节点上");
  assert.equal(page.mounted().length, 0);

  page.shell.section = "action";
  page.run("paintPanel()");
  await page.settle();
  assert.ok(page.host(), "回到行动页且状态已缓存时，面板应直接画出挂载点");
  assertSingleEditorOnCurrentHost(page);
  assertNoEditorOnDeadNode(page);
});

test("挂载点消失（停用扩展）：编辑器跟着卸载，不留在死节点上", async () => {
  const page = loadPage();
  await enterActionPage(page, {warm: true});
  assertSingleEditorOnCurrentHost(page);

  page.status.installed = false;
  page.run("paintQzone()");
  assert.equal(page.host(), null, "未安装时不该再画配置挂载点");
  assert.equal(page.live().length, 0, "挂载点消失时必须卸载编辑器");
  assert.equal(page.mounted().length, 0);

  const before = page.qzoneEditors().length;
  page.run("paintQzone()");
  assert.equal(page.qzoneEditors().length, before, "没有挂载点时重画不该凭空建编辑器");
  assert.equal(page.live().length, 0);
});

test("配置编辑器创建失败时，挂载点换成一句实话而不是转圈", async () => {
  const page = loadPage({configEditorsCreatable: false});
  await enterActionPage(page);
  const host = page.host();
  assert.ok(host, "挂载点仍然应该存在——状态已经读到、扩展已安装");
  assert.doesNotMatch(host.innerHTML, /正在读取设置/,
    "挂不上编辑器时不能留一句「正在读取设置…」，那是在撒谎");
  /* 不断言具体文案：只要求挂载点里是一条 config-editors-hint 说明。文案以后
     可以改，但不能再回到永久加载态。 */
  assert.match(host.innerHTML, /class="config-editors-hint"/,
    "挂不上时应当显示一条说明，而不是一直停在加载态");
  assert.equal(page.live().length, 0);
  assert.equal(page.mounted().length, 0);

  page.run("paintQzone()");
  assert.equal(page.live().length, 0, "创建失败时重画也不该建出空壳编辑器");
  assert.match(page.host().innerHTML, /class="config-editors-hint"/);
});

/* ---- 凭据行：说「尚未检查」，还是「没有可用来源」 ---------------------------- */

test("凭据还没读过：显示「尚未检查」，只有失败才说「没有可用来源」", async () => {
  const page = loadPage();
  await enterActionPage(page);

  const row = page.run("qzoneCredentialMarkup(qzoneStatus().credential)");
  assert.match(row, /尚未检查/, "unknown 不该被写成「尚未使用」");
  assert.match(row, /刷新凭据或读一次空间即可确认/, "unknown 要告诉用户下一步怎么做");
  assert.doesNotMatch(row, /没有可用来源/, "还没检查过不能断言没有来源");
  assert.match(page.document.getElementById("actionQzoneBody").innerHTML, /尚未检查/,
    "状态行里应当直接显示这句实话");

  /* 真的失败（比如没有可用的 OneBot 连接）才有资格说「没有可用来源」。 */
  const failed = page.run(
    "qzoneCredentialMarkup({state:'failed',source:'none',reason:'没有可用的 OneBot 连接'})");
  assert.match(failed, /读取失败/);
  assert.match(failed, /没有可用来源/);
  assert.match(failed, /没有可用的 OneBot 连接/);
});

test("刷新凭据：响应带的是刷新前快照，也必须重读状态并按新结果说话", async () => {
  const page = loadPage();
  await enterActionPage(page);
  const stale = {...page.status,
    credential: {state: "unknown", source: "none", uin_masked: "", reason: ""}};
  const fresh = {...page.status,
    credential: {state: "ready", source: "runtime", uin_masked: "1******89", reason: ""}};
  const calls = [];
  page.product.request = async (method, route) => {
    calls.push({method, route});
    /* 模拟修复前的宿主：refresh 的成功响应里还带着刷新前的凭据状态。 */
    return method === "POST" && /refresh-credentials$/.test(route) ? stale : fresh;
  };

  await page.run("qzoneRefreshCredentials()");

  assert.deepEqual(calls.map(call => `${call.method} ${call.route}`),
    ["POST qzone-extension/refresh-credentials", "GET qzone-extension/status"],
    "刷新凭据之后必须再读一次状态");
  assert.equal(page.run("qzoneState.status.credential.state"), "ready",
    "页面采用了响应里的旧快照，而不是重读到的状态");
  const notice = page.run("qzoneState.notice");
  assert.equal(notice.error, false);
  assert.match(notice.text, /凭据已刷新/);
  const row = page.run("qzoneCredentialMarkup(qzoneStatus().credential)");
  assert.match(row, /已就绪/);
  assert.match(row, /取自当前 Bot 登录/);
  assert.doesNotMatch(row, /尚未检查|没有可用来源/);
});

test("刷新后仍是 unknown：提示语不冒充「已刷新」", async () => {
  const page = loadPage();
  await enterActionPage(page);
  const unknown = {...page.status,
    credential: {state: "unknown", source: "none", uin_masked: "", reason: ""}};
  page.product.request = async () => unknown;

  await page.run("qzoneRefreshCredentials()");

  const notice = page.run("qzoneState.notice");
  assert.equal(notice.error, true);
  assert.match(notice.text, /还没有被读取/);
  assert.doesNotMatch(notice.text, /凭据已刷新/);
});
