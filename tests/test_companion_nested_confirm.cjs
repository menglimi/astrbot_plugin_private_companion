#!/usr/bin/env node
/*
 * 陪伴小屋嵌套确认框的浏览器回归。
 *
 * 场景（真实小屋 + 真实 identity 工作区 bundle，只替换控制台 SDK 数据）：
 *   765px 插件视口 → 2400px 工作区 iframe（sandbox，独立渲染进程）
 *   点「允许此账号私聊」后，确认层必须在插件视口内可见、可点；
 *   取消写 0 次，确认恰好写 1 次到 acquaintance/stranger-target。
 *
 * 最小依赖：Node ≥ 22（内置 WebSocket）与一个 Chromium 系浏览器；不需要 Playwright。
 * 浏览器查找顺序：COMPANION_FIXTURE_BROWSER 环境变量 → 各平台常见安装路径。
 * 找不到浏览器时整条用例 skip。
 */
"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { startFixtureServer } = require("../tools/serve_companion_layout_fixture.cjs");

const CANDIDATES = [
  process.env.COMPANION_FIXTURE_BROWSER,
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function findBrowser() {
  return CANDIDATES.find((candidate) => {
    try { return fs.statSync(candidate).isFile(); } catch { return false; }
  }) || null;
}

/** 极简 CDP：只覆盖本用例需要的方法，扁平会话模型。 */
class Cdp {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 1;
    this.pending = new Map();
    this.events = [];
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      if (message.id) {
        const item = this.pending.get(message.id);
        if (!item) return;
        this.pending.delete(message.id);
        if (message.error) item.reject(new Error(`${item.method}: ${message.error.message}`));
        else item.resolve(message.result);
        return;
      }
      this.events.push(message);
    });
  }

  static async connect(url) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve, { once: true });
      socket.addEventListener("error", () => reject(new Error("CDP websocket failed")), { once: true });
    });
    return new Cdp(socket);
  }

  send(method, params = {}, sessionId) {
    const id = this.nextId++;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    this.socket.send(JSON.stringify(payload));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject, method }));
  }

  evaluate(expression, { sessionId, awaitPromise = true } = {}) {
    return this.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise }, sessionId)
      .then((result) => {
        if (result.exceptionDetails) {
          throw new Error("page evaluate failed: " + (result.exceptionDetails.text
            || result.exceptionDetails.exception?.description));
        }
        return result.result?.value;
      });
  }
}

async function launchBrowser(browser, profile) {
  const child = spawn(browser, [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=Translate",
    "--remote-debugging-port=0",
    "--user-data-dir=" + profile,
    "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });

  const endpoint = await new Promise((resolve, reject) => {
    let buffered = "";
    const timer = setTimeout(() => reject(new Error("browser did not expose CDP endpoint")), 30000);
    child.stderr.on("data", (chunk) => {
      buffered += String(chunk);
      const match = buffered.match(/DevTools listening on (ws:\/\/\S+)/);
      if (!match) return;
      clearTimeout(timer);
      resolve(match[1]);
    });
    child.once("exit", (code) => {
      clearTimeout(timer);
      reject(new Error(`browser exited before CDP was ready (${code})`));
    });
  });
  return { child, endpoint };
}

function hostDialogState() {
  const plugin = document.getElementById("plugin");
  const doc = plugin && plugin.contentDocument;
  const dialog = doc && doc.getElementById("confirmationDialog");
  const rect = dialog && dialog.open ? dialog.getBoundingClientRect() : null;
  return {
    open: Boolean(rect),
    title: dialog ? doc.getElementById("confirmationTitle").textContent : "",
    message: dialog ? doc.getElementById("confirmationMessage").textContent : "",
    rect: rect ? { top: rect.top, bottom: rect.bottom, height: rect.height } : null,
    viewport: { width: plugin.clientWidth, height: plugin.clientHeight },
    requests: (plugin && plugin.contentWindow && plugin.contentWindow.__fixtureRequests) || [],
    page: window.__fixtureState || null,
  };
}

test("嵌套工作区的确认层在插件视口内可见可操作，取消 0 写、确认 1 写", async (t) => {
  const browser = findBrowser();
  if (!browser) {
    t.skip("没有找到 Chromium 系浏览器；设置 COMPANION_FIXTURE_BROWSER 指向可执行文件");
    return;
  }
  if (typeof WebSocket !== "function") {
    t.skip("需要 Node ≥ 22（内置 WebSocket）");
    return;
  }

  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "companion-confirm-"));
  const fixture = await startFixtureServer({ port: 0 });
  const { child, endpoint } = await launchBrowser(browser, profile);
  let cdp = null;
  try {
    cdp = await Cdp.connect(endpoint);

    const openPage = async () => {
      const created = await cdp.send("Target.createTarget", { url: "about:blank" });
      const attached = await cdp.send("Target.attachToTarget", { targetId: created.targetId, flatten: true });
      const sessionId = attached.sessionId;
      await cdp.send("Page.enable", {}, sessionId);
      await cdp.send("Runtime.enable", {}, sessionId);
      await cdp.send("Target.setAutoAttach", { autoAttach: true, waitForDebuggerOnStart: false, flatten: true }, sessionId);
      await cdp.send("Page.navigate", { url: fixture.fixture }, sessionId);
      return sessionId;
    };

    const page = await openPage();
    const waitFor = async (describe, probe, { timeout = 30000, interval = 200 } = {}) => {
      const started = Date.now();
      let last;
      while (Date.now() - started < timeout) {
        last = await probe();
        if (last) return last;
        await sleep(interval);
      }
      throw new Error(`timed out waiting for ${describe}; last=${JSON.stringify(last)}`);
    };

    /** 工作区 iframe 是 sandbox 的 OOP frame，只能在它自己的 CDP 会话里点。 */
    const workspaceSessionWithRow = () => waitFor("workspace row", async () => {
      const frames = cdp.events
        .filter((event) => event.method === "Target.attachedToTarget" && event.params?.targetInfo?.type === "iframe")
        .map((event) => event.params.sessionId);
      for (const sessionId of frames) {
        const found = await cdp.evaluate(
          "Boolean(document.querySelector('button[data-action=allow-stranger]'))", { sessionId })
          .catch(() => false);
        if (found) return sessionId;
      }
      return null;
    });

    await waitFor("fixture page ready", () => cdp.evaluate(
      "Boolean(window.__fixtureState && window.__fixtureState.status === 'ready')",
      { sessionId: page }));

    // ---- 场景一：取消不写 ----
    let workspace = await workspaceSessionWithRow();
    await cdp.evaluate("document.querySelector('button[data-action=allow-stranger]').click()", { sessionId: workspace });
    const opened = await waitFor("host dialog open", async () => {
      const state = await cdp.evaluate(`(${hostDialogState.toString()})()`, { sessionId: page });
      return state.open ? state : null;
    });
    assert.match(opened.title, /允许此账号私聊/, "宿主确认层标题");
    assert.match(opened.message, /权限沿用现有设置/, "确认框说明沿用现有设置");
    assert.ok(opened.rect.top >= 0 && opened.rect.bottom <= opened.viewport.height + 1,
      `确认层应完整落在插件视口内：${JSON.stringify(opened.rect)} viewport=${JSON.stringify(opened.viewport)}`);
    assert.equal(await cdp.evaluate("Boolean(document.querySelector('.stranger-dialog'))", { sessionId: workspace }),
      false, "工作区不应再自己开一层确认框");

    const postsBefore = (await cdp.evaluate(`(${hostDialogState.toString()})()`, { sessionId: page })).requests
      .filter((item) => item.method === "POST").length;
    await cdp.evaluate("document.getElementById('plugin').contentDocument.getElementById('confirmationCancel').click()",
      { sessionId: page });
    await sleep(500);
    const afterCancel = await cdp.evaluate(`(${hostDialogState.toString()})()`, { sessionId: page });
    assert.equal(afterCancel.open, false, "取消后宿主确认层关闭");
    assert.equal(afterCancel.requests.filter((item) => item.method === "POST").length, postsBefore, "取消不写配置");

    // ---- 场景一之二：人在确认层上停留超过普通请求期限 ----
    // 工作区请求默认 18s 到期；人的确认不能沿用这个期限，否则期限内没点，
    // 宿主那层还开着，工作区却偷偷又开一层，之后的选择还会丢。
    await cdp.evaluate("document.querySelector('button[data-action=allow-stranger]').click()", { sessionId: workspace });
    await waitFor("host dialog open before the long wait", async () => {
      const state = await cdp.evaluate(`(${hostDialogState.toString()})()`, { sessionId: page });
      return state.open ? state : null;
    });
    await sleep(20000);
    const held = await cdp.evaluate(`(${hostDialogState.toString()})()`, { sessionId: page });
    assert.equal(held.open, true, "过了普通请求期限，宿主确认层仍必须是同一个（不能被超时关掉）");
    assert.equal(await cdp.evaluate("Boolean(document.querySelector('.stranger-dialog'))", { sessionId: workspace }),
      false, "工作区不能在等待期后偷偷再开内联确认框");
    assert.equal(held.requests.filter((item) => item.method === "POST").length, postsBefore, "等待期不写配置");
    await cdp.evaluate("document.getElementById('plugin').contentDocument.getElementById('confirmationCancel').click()",
      { sessionId: page });
    await sleep(500);
    assert.equal((await cdp.evaluate(`(${hostDialogState.toString()})()`, { sessionId: page })).open, false,
      "等待期后取消仍然关闭宿主确认层");

    // ---- 场景二：等待期后确认仍然恰好写一次 ----
    await cdp.evaluate("document.querySelector('button[data-action=allow-stranger]').click()", { sessionId: workspace });
    await waitFor("host dialog open again", async () => {
      const state = await cdp.evaluate(`(${hostDialogState.toString()})()`, { sessionId: page });
      return state.open ? state : null;
    });
    await sleep(20000);
    assert.equal((await cdp.evaluate(`(${hostDialogState.toString()})()`, { sessionId: page })).open, true,
      "等待期后确认层仍可操作");
    await cdp.evaluate("document.getElementById('plugin').contentDocument.getElementById('confirmationAccept').click()",
      { sessionId: page });
    const written = await waitFor("single stranger-target write", async () => {
      const state = await cdp.evaluate(`(${hostDialogState.toString()})()`, { sessionId: page });
      const posts = state.requests.filter((item) => item.method === "POST");
      return posts.length === 1 ? { ...state, posts } : null;
    });
    assert.equal(written.posts[0].path, "acquaintance/stranger-target", "写入端点");
    assert.deepEqual(written.posts[0].body, { platform_id: "qq-main", login: "100", peer: "999" }, "写入体只含这条路由");
    await waitFor("row marked added", async () => {
      const row = await cdp.evaluate(`(() => {
        const button = document.querySelector('button[data-action=allow-stranger]');
        const chip = document.querySelector('.stranger-row .stranger-state');
        return button ? { disabled: button.disabled, chip: chip ? chip.textContent : "" } : null;
      })()`, { sessionId: workspace });
      return row && row.disabled && row.chip === "已添加" ? row : null;
    });
  } finally {
    try { cdp && cdp.socket.close(); } catch { /* 关闭失败不影响断言结果 */ }
    try { child.kill(); } catch { /* 浏览器可能已退出 */ }
    await fixture.close().catch(() => {});
    fs.rmSync(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  }
});
