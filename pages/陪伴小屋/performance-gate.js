/* Both profiles boot the same dashboard. Only assets explicitly marked as
 * full-mode visuals are omitted from the initial load on low-resource devices. */
(function () {
  "use strict";

  const TEMPLATE_ID = "companion-full-assets";
  const ENDPOINT = "host/performance";
  const BRIDGE_WAIT_MS = 1400;
  const REQUEST_TIMEOUT_MS = 3200;
  const root = document.documentElement;
  function unwrap(response) {
    // Some bridge versions wrap the plugin response in an extra {data} layer.
    if (response && Object.keys(response).length === 1 && response.data) response = response.data;
    if (response?.status === "error") return null;
    return response?.status === "ok" ? response.data : response;
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

  function activateDashboard(lite) {
    root.dataset.performanceMode = lite ? "lite" : "full";
    // The normal reveal waits for the 3D entrance. Lite has no startup scene.
    if (lite) root.dataset.roomReady = "";
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

    for (const asset of template.content.children) {
      if (lite && asset.hasAttribute("data-full-only")) continue;
      const node = asset.cloneNode(true);
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

  // A missing setting (e.g. an older host or standalone preview) uses the standard page.
  readPerformanceStatus().then(
    (status) => activateDashboard(status?.enabled === true),
    () => activateDashboard(false),
  );
})();
