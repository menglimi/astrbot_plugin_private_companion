/*
 * 嵌套布局夹具：把“工作区确认框落到插件视口外”的几何条件固定下来。
 *
 * 结构（与真实控制台一致的三层）：
 *   本页（浏览器视口）
 *   └─ iframe#plugin 765px  … 陪伴小屋页面视口，等同于控制台插件页高度
 *      └─ iframe.product-workspace-frame 2400px  … 真实 identity 工作区（srcdoc，sandbox）
 *
 * 装配方式复用真实小屋：`CompanionProduct.workspace('identity','people')`
 * 生成工作区外壳，`CompanionProduct.mountWorkspace()` 走真实的 bundle 加载与
 * 消息协议。夹具只把外壳放到页面里并滚到生产现场的位置，不复制工作区代码。
 *
 * 生产取证（issue2-deploy-70ed3b8/reload-readonly-20261103.md）：
 *   iframe 高 2400、插件视口 765、iframe 在插件视口内 top=-252.703125；
 *   工作区里的 <dialog> 在 iframe 内 top≈1091.8，映射回插件视口≈839，落在
 *   765 之外，所以用户只能看到遮罩。
 *
 * 断言方在工作区 frame 里执行真实点击并读取 dialog 矩形，再调用本页的
 * `await __fixture.verdict(rect)`（它会重新摆位后测绘）：
 *   red  : 宿主确认层没有打开，且映射后的 dialog 整个落在视口下方
 *   green: 宿主 #confirmationDialog 打开且完整落在插件视口内，约 216px 高
 */
(function () {
  "use strict";
  const plugin = document.getElementById("plugin");
  const params = new URLSearchParams(location.search);
  const viewport = Number(params.get("viewport") || 765);
  const targetFrameTop = Number(params.get("frameTop") || -252.703125);
  plugin.style.height = viewport + "px";

  const state = { status: "loading", error: null, viewport, targetFrameTop, appliedFrameTop: null };
  window.__fixtureState = state;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const shell = () => plugin.contentWindow;
  const shellDoc = () => plugin.contentDocument;
  /* 只认夹具自己挂的那一块：小屋渲染流程在别的容器里也可能再挂工作区，
     两个 iframe 的坐标互不相同，混用会让映射结论失真。 */
  const hostEl = () => shellDoc()?.getElementById("fixture-workspace") || null;
  const frameEl = () => hostEl()?.querySelector(".product-workspace-frame") || null;
  const sectionEl = () => hostEl()?.querySelector('.product-workspace[data-workspace="identity"]') || null;

  async function until(predicate, timeout, label) {
    const started = Date.now();
    for (;;) {
      let value = null;
      try { value = predicate(); } catch { value = null; }
      if (value) return value;
      if (Date.now() - started > timeout) throw new Error("fixture timeout: " + label);
      await sleep(50);
    }
  }

  /* 用真实嵌入入口挂工作区。页面上原有的控制台工作区块先移除，避免同一个
     页面出现两个 iframe；这一步只动夹具自己放进去的 DOM。 */
  async function mountWorkspace() {
    const win = shell();
    const doc = shellDoc();
    await until(() => win.CompanionProduct, 20000, "CompanionProduct");
    const product = win.CompanionProduct;
    await until(() => {
      const html = product.workspace("identity", "people");
      return typeof html === "string" && html.includes('product-workspace"');
    }, 20000, "role catalog");
    for (const old of doc.querySelectorAll(".product-workspace")) old.remove();
    const host = doc.createElement("div");
    host.id = "fixture-workspace";
    host.innerHTML = product.workspace("identity", "people");
    doc.body.append(host);
    product.mountWorkspace();
  }

  function scrollerOf(node) {
    const win = shell();
    for (let current = node; current && current !== shellDoc().body; current = current.parentElement) {
      const style = win.getComputedStyle(current);
      if (/(auto|scroll)/.test(style.overflowY) && current.scrollHeight > current.clientHeight + 4) return current;
    }
    return null;
  }

  /* 相对插件视口把工作区 iframe 摆到生产现场的位置：top = -252.703125。
     真正滚动的容器可能是窗口，也可能是容器面板，两种都按实测矩形修正。 */
  async function placeFrame() {
    const frame = frameEl();
    if (!frame) return null;
    frame.scrollIntoView({ block: "start", inline: "nearest" });
    await sleep(80);
    let rect = frame.getBoundingClientRect();
    shell().scrollBy(0, rect.top - targetFrameTop);
    await sleep(80);
    rect = frame.getBoundingClientRect();
    if (Math.abs(rect.top - targetFrameTop) > 1) {
      const scroller = scrollerOf(frame.parentElement);
      if (scroller) {
        scroller.scrollTop += rect.top - targetFrameTop;
        await sleep(80);
        rect = frame.getBoundingClientRect();
      }
    }
    state.appliedFrameTop = rect.top;
    return rect;
  }

  function measure() {
    const win = shell();
    const frame = frameEl();
    const rect = frame ? frame.getBoundingClientRect() : null;
    const hostDialog = shellDoc()?.getElementById("confirmationDialog") || null;
    const hostRect = hostDialog && hostDialog.open ? hostDialog.getBoundingClientRect() : null;
    return {
      viewport: win ? { width: win.innerWidth, height: win.innerHeight } : null,
      scrollTop: win ? (shellDoc().scrollingElement || shellDoc().documentElement).scrollTop : null,
      loadState: sectionEl()?.dataset.loadState || "",
      workspaceTab: sectionEl()?.dataset.workspaceTab || "",
      workspaceHeight: rect ? rect.height : null,
      frame: rect ? { top: rect.top, left: rect.left, width: rect.width, height: rect.height } : null,
      hostDialog: {
        open: Boolean(hostDialog?.open),
        rect: hostRect ? { top: hostRect.top, left: hostRect.left, width: hostRect.width, height: hostRect.height } : null,
      },
      requests: win ? (win.__fixtureRequests || []).slice() : [],
    };
  }

  /* 异步：先按生产位置重新摆一次 iframe 再量。点击会让浏览器为了露出焦点
     元素滚动外层容器，若用点击后的滚动位置量，结论会失真。 */
  async function verdict(workspaceRect) {
    await placeFrame();
    const current = measure();
    const frame = current.frame;
    if (!frame || !workspaceRect) return { complete: false, reason: "not-ready" };
    const mapped = {
      top: frame.top + workspaceRect.top,
      bottom: frame.top + workspaceRect.bottom,
    };
    const dialogInside = mapped.top >= 0 && mapped.bottom <= current.viewport.height;
    const fullyBelow = mapped.top >= current.viewport.height;
    return {
      complete: true,
      viewportHeight: current.viewport.height,
      frame: frame,
      workspaceHeight: current.workspaceHeight,
      workspaceTab: current.workspaceTab,
      mapped,
      hostDialog: current.hostDialog,
      red: !current.hostDialog.open && fullyBelow,
      green: current.hostDialog.open && dialogInside
        && current.hostDialog.rect.top >= 0
        && current.hostDialog.rect.top + current.hostDialog.rect.height <= current.viewport.height,
      requests: current.requests,
    };
  }

  function diagnostics() {
    const doc = shellDoc();
    const host = hostEl();
    const frame = frameEl();
    const section = sectionEl();
    return {
      hostPresent: Boolean(host),
      sectionHtml: section ? section.outerHTML.slice(0, 300) : null,
      loadState: section?.dataset.loadState || "",
      srcdocLength: frame ? (frame.srcdoc || "").length : 0,
      frameHeight: frame ? frame.getBoundingClientRect().height : null,
      workspaces: doc ? doc.querySelectorAll(".product-workspace").length : null,
      productPresent: Boolean(shell()?.CompanionProduct),
      bodyChildren: doc ? doc.body.children.length : null,
    };
  }

  /* 小屋启动阶段会重画外壳；夹具容器被重画带走时重新挂一次即可，最多 10 次。 */
  async function ensureMounted() {
    let lastState = "";
    for (let attempt = 0; attempt < 10; attempt += 1) {
      if (!hostEl()) await mountWorkspace();
      const section = sectionEl();
      lastState = section?.dataset.loadState || "";
      if (lastState === "ready") return;
      await sleep(300);
    }
    throw new Error("workspace ready (load state " + JSON.stringify(lastState) + ")");
  }

  window.__fixture = { viewport, targetFrameTop, state, ready: null, measure, verdict, mountWorkspace, ensureMounted, diagnostics };

  window.__fixture.ready = (async () => {
    try {
      await until(() => plugin.contentDocument, 15000, "plugin document");
      await until(() => shellDoc()?.body, 15000, "plugin body");
      await ensureMounted();
      /* 高度由工作区按自身内容 postMessage 上报（生产现场 2400）。这里只等它
         完成一次布局；具体高度记进 measure()，断言按真实坐标计算。 */
      await until(() => {
        const rect = frameEl()?.getBoundingClientRect();
        return rect && rect.height >= 620;
      }, 15000, "workspace laid out");
      await placeFrame();
      state.status = "ready";
      return measure();
    } catch (error) {
      state.status = "error";
      state.error = String(error && error.message || error);
      try { state.diagnostics = diagnostics(); } catch (diagnosticError) { state.diagnostics = String(diagnosticError); }
      throw error;
    }
  })();

  window.__fixture.ready.catch(() => {});
})();
