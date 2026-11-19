/* Performance-mode gate for the cottage page.
 *
 * The heavy stylesheet/script tags live in an inert <template>.  This file asks
 * the plugin for the official performance setting before any of them are
 * activated.  When the check cannot complete we keep the legacy full page as a
 * safe fallback; a missing optional optimization must never break the UI.
 */
(function () {
  "use strict";

  const TEMPLATE_ID = "companion-full-assets";
  const ENDPOINT = "host/performance";
  const BRIDGE_WAIT_MS = 1400;
  const REQUEST_TIMEOUT_MS = 3200;
  const root = document.documentElement;
  let settled = false;

  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function unwrap(response) {
    if (!response || typeof response !== "object") return {};
    if (response.status === "error") {
      const error = new Error(String(response.message || "performance_mode_unavailable"));
      error.code = String(response.message || "performance_mode_unavailable");
      throw error;
    }
    if (response.status === "ok" && response.data && typeof response.data === "object") {
      return response.data;
    }
    if (Object.keys(response).length === 1 && response.data && typeof response.data === "object") {
      return response.data;
    }
    return response;
  }

  async function withTimeout(promise, ms, code) {
    let timer;
    try {
      return await Promise.race([
        promise,
        new Promise((_, reject) => {
          timer = setTimeout(() => {
            const error = new Error(code);
            error.code = code;
            reject(error);
          }, ms);
        }),
      ]);
    } finally {
      clearTimeout(timer);
    }
  }

  async function readPerformanceStatus() {
    const bridge = window.AstrBotPluginPage;
    if (!bridge || typeof bridge.apiGet !== "function") {
      throw new Error("performance_bridge_unavailable");
    }
    if (typeof bridge.ready === "function") {
      try {
        await withTimeout(bridge.ready(), BRIDGE_WAIT_MS, "performance_bridge_timeout");
      } catch (_) {
        /* The host may expose request APIs before it publishes context. */
      }
    }
    return unwrap(await withTimeout(
      bridge.apiGet(ENDPOINT, {}),
      REQUEST_TIMEOUT_MS,
      "performance_mode_timeout",
    ));
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function showLite(status) {
    if (settled) return;
    settled = true;
    root.dataset.companionPerformance = "1";
    root.dataset.companionPerformanceReady = "";
    root.removeAttribute("data-companion-booting");
    document.title = "陪伴小屋 · 性能模式";

    const page = element("main", "companion-performance-page");
    const card = element("section", "companion-performance-card");
    card.appendChild(element("div", "companion-performance-badge", "性能模式已启用"));
    card.appendChild(element("h1", "", "陪伴小屋已切换为轻量模式"));
    card.appendChild(element("p", "", "当前设备只加载这张轻量状态页。3D 小屋、动画、全部工作区样式和脚本均未加载，页面管理接口也已精简。"));

    const list = element("ul");
    ["聊天、记忆、角色和已配置的主动功能继续运行。",
      "陪伴小屋不再提供高级配置；请使用 AstrBot 官方插件配置页。",
      "关闭性能模式后需要重载插件，完整小屋才会恢复。"].forEach((text) => {
      list.appendChild(element("li", "", text));
    });
    card.appendChild(list);

    const actions = element("div", "companion-performance-actions");
    const reload = element("button", "companion-performance-button", "重新检测");
    reload.type = "button";
    reload.addEventListener("click", () => window.location.reload());
    actions.appendChild(reload);
    card.appendChild(actions);
    card.appendChild(element("p", "companion-performance-note",
      "切换方法：返回 AstrBot 插件配置页顶部，关闭「性能模式」，保存并重载插件。"));

    page.appendChild(card);
    document.body.replaceChildren(page);
    document.body.className = "companion-performance-body";
    if (typeof status?.message === "string" && status.message) {
      card.setAttribute("data-status-message", status.message);
    }
  }

  function activateFull() {
    if (settled) return;
    settled = true;
    root.dataset.companionPerformance = "0";
    const template = document.getElementById(TEMPLATE_ID);
    if (!template) {
      /* No inert bundle means an older/custom page; keep normal behavior. */
      root.removeAttribute("data-companion-booting");
      return;
    }

    let pendingStyles = 0;
    let released = false;
    let releasing = false;
    const release = () => {
      if (released || releasing) return;
      releasing = true;
      const finish = () => {
        if (released) return;
        released = true;
        root.dataset.companionPerformanceReady = "";
        root.removeAttribute("data-companion-booting");
      };
      /* The stylesheet load event can fire before the new CSSOM has been
         applied.  Let two frames pass so the boot frame does not flash as
         `visibility:hidden`; the timeout covers a backgrounded tab. */
      if (typeof requestAnimationFrame === "function") {
        requestAnimationFrame(() => requestAnimationFrame(finish));
        setTimeout(finish, 250);
      } else {
        setTimeout(finish, 0);
      }
    };

    const nodes = Array.from(template.content.childNodes, (node) => node.cloneNode(true));
    for (const node of nodes) {
      if (node.nodeType !== 1) continue;
      if (node.tagName === "LINK" && node.getAttribute("rel") === "stylesheet") {
        pendingStyles += 1;
        node.addEventListener("load", () => {
          pendingStyles -= 1;
          if (pendingStyles <= 0) release();
        }, { once: true });
        node.addEventListener("error", () => {
          pendingStyles -= 1;
          if (pendingStyles <= 0) release();
        }, { once: true });
      }
      if (node.tagName === "SCRIPT") {
        /* Dynamic classic scripts default to async.  Explicitly preserve the
           author order: app.js runs after the shell dependencies and before the
           larger page modules, which re-render once all of them have landed. */
        node.async = false;
        node.defer = false;
      }
      document.head.appendChild(node);
    }

    if (pendingStyles === 0) release();
    /* A stalled stylesheet must not leave the page hidden forever. */
    setTimeout(release, 5000);
  }

  readPerformanceStatus()
    .then((status) => {
      if (status && status.enabled === true) showLite(status);
      else activateFull();
    })
    .catch(() => {
      /* Fail open: the full page is preferable to an unavailable dashboard. */
      activateFull();
    });
})();
