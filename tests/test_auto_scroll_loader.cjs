const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const LOADER_PATH = path.join(ROOT, "pages", "陪伴小屋", "auto-scroll-loader.js");
const INDEX_PATH = path.join(ROOT, "pages", "陪伴小屋", "index.html");
const CSS_SOURCES_PATH = path.join(ROOT, "pages", "陪伴小屋", "css-sources.json");
const DESIGNER_APP_PATH = path.join(ROOT, "pages", "新的她", "app.js");

function parseAttrs(raw) {
  const attrs = {};
  const re = /([a-zA-Z0-9_:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>/]+)))?/g;
  let m;
  while ((m = re.exec(raw))) {
    attrs[m[1]] = m[2] ?? m[3] ?? m[4] ?? "";
  }
  return attrs;
}

class FakeElement {
  constructor(tag = "div", attrs = {}) {
    this.tagName = String(tag).toUpperCase();
    this.attrs = { ...attrs };
    this.dataset = {};
    for (const [k, v] of Object.entries(attrs)) {
      if (k.startsWith("data-")) {
        const camel = k.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
        this.dataset[camel] = v;
      }
    }
    this.checked = "checked" in attrs;
    this.disabled = "disabled" in attrs;
    this.hidden = "hidden" in attrs;
    this.scrollTop = 0;
    this.scrollCalls = [];
    this.scrollIntoViewCalls = [];
    this.listeners = new Map();
    this._innerHTML = "";
    this.children = [];
    this.parentElement = null;
    this.isConnected = true;
    this.id = attrs.id || "";
    this.className = attrs.class || "";
    this.textContent = "";
    this.value = attrs.value || "";
  }

  get innerHTML() {
    return this._innerHTML;
  }

  set innerHTML(html) {
    this._innerHTML = String(html || "");
    this.children = [];
    const tagRe = /<(div|button|input|label|span|p|article)\b([^>]*)>/gi;
    let m;
    while ((m = tagRe.exec(this._innerHTML))) {
      const child = new FakeElement(m[1], parseAttrs(m[2]));
      child.parentElement = this;
      this.children.push(child);
    }
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
  }

  setAttribute(name, value) {
    this.attrs[name] = String(value);
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
    if (name.startsWith("data-")) {
      const camel = name.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
      this.dataset[camel] = String(value);
    }
  }

  hasAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attrs, name);
  }

  matches(selector) {
    const parts = String(selector || "").split(",").map((s) => s.trim()).filter(Boolean);
    return parts.some((part) => {
      const attrMatch = part.match(/^\[([a-zA-Z0-9_:-]+)(?:="([^"]*)")?\]$/);
      if (attrMatch) {
        const [, name, val] = attrMatch;
        if (!this.hasAttribute(name)) return false;
        return val === undefined ? true : this.getAttribute(name) === val;
      }
      if (part.startsWith("#")) return this.id === part.slice(1);
      if (part.startsWith(".")) {
        return this.className.split(/\s+/).includes(part.slice(1));
      }
      return this.tagName.toLowerCase() === part.toLowerCase();
    });
  }

  closest(selector) {
    if (this.matches(selector)) return this;
    return this.parentElement ? this.parentElement.closest(selector) : null;
  }

  querySelector(selector) {
    for (const child of this.children) {
      if (child.matches(selector)) return child;
      const deep = child.querySelector(selector);
      if (deep) return deep;
    }
    return null;
  }

  querySelectorAll(selector) {
    const out = [];
    for (const child of this.children) {
      if (child.matches(selector)) out.push(child);
      out.push(...child.querySelectorAll(selector));
    }
    return out;
  }

  append(...nodes) {
    for (const n of nodes) {
      if (n && typeof n === "object") {
        n.parentElement = this;
        this.children.push(n);
      }
    }
  }

  appendChild(node) {
    this.append(node);
    return node;
  }

  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
  }

  remove() {
    if (!this.parentElement) return;
    this.parentElement.children = this.parentElement.children.filter((c) => c !== this);
    this.parentElement = null;
  }

  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(fn);
  }

  removeEventListener(type, fn) {
    this.listeners.get(type)?.delete(fn);
  }

  dispatchEvent(event) {
    const ev = {
      type: event.type,
      target: event.target || this,
      defaultPrevented: false,
      propagationStopped: false,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { this.propagationStopped = true; },
      ...event,
    };
    for (const fn of Array.from(this.listeners.get(ev.type) || [])) {
      fn(ev);
    }
    if (!ev.propagationStopped && this.parentElement) {
      this.parentElement.dispatchEvent(ev);
    }
    return ev;
  }

  scrollTo(opts) {
    this.scrollCalls.push(opts);
    if (opts && typeof opts.top === "number") this.scrollTop = opts.top;
  }

  scrollIntoView(opts) {
    this.scrollIntoViewCalls.push(opts);
  }
}

function createLoaderHarness(options = {}) {
  let now = options.startNow ?? 10_000;
  let nextTimerId = 1;
  const timers = new Map();
  const observers = [];
  const windowListeners = new Map();
  const windowScrollCalls = [];
  const store = new Map(Object.entries(options.initialStorage || {}));
  let storageThrows = Boolean(options.storageThrows);

  const localStorage = {
    getItem(key) {
      if (storageThrows) throw new Error("SecurityError: storage blocked");
      return store.has(key) ? store.get(key) : null;
    },
    setItem(key, value) {
      if (storageThrows) throw new Error("SecurityError: storage blocked");
      store.set(key, String(value));
    },
    removeItem(key) {
      if (storageThrows) throw new Error("SecurityError: storage blocked");
      store.delete(key);
    },
  };

  class FakeIntersectionObserver {
    constructor(callback, opts = {}) {
      this.callback = callback;
      this.options = opts;
      this.observed = new Set();
      this.disconnected = false;
      observers.push(this);
    }
    observe(el) {
      this.observed.add(el);
    }
    unobserve(el) {
      this.observed.delete(el);
    }
    disconnect() {
      this.disconnected = true;
      this.observed.clear();
    }
    trigger(isIntersecting = true) {
      if (this.disconnected) return;
      const targets = Array.from(this.observed);
      const entries = (targets.length ? targets : [null]).map((target) => ({
        target,
        isIntersecting,
      }));
      this.callback(entries, this);
    }
  }

  const fakeDate = class extends Date {
    constructor(...args) {
      super(...(args.length ? args : [now]));
    }
    static now() {
      return now;
    }
  };

  const sandbox = {
    Date: fakeDate,
    Math,
    Number,
    String,
    Boolean,
    Array,
    Object,
    Map,
    Set,
    JSON,
    Promise,
    Error,
    console,
    localStorage: options.noStorage ? undefined : localStorage,
    IntersectionObserver: options.noIO ? undefined : FakeIntersectionObserver,
    document: {
      documentElement: { dataset: { motion: options.motion || "normal" } },
    },
    matchMedia: (query) => ({
      matches: Boolean(options.reducedMotion && query.includes("prefers-reduced-motion")),
    }),
    scrollTo(opts) {
      windowScrollCalls.push(opts);
    },
    addEventListener(type, fn) {
      if (!windowListeners.has(type)) windowListeners.set(type, new Set());
      windowListeners.get(type).add(fn);
    },
    removeEventListener(type, fn) {
      windowListeners.get(type)?.delete(fn);
    },
    setTimeout(fn, delay = 0) {
      const id = nextTimerId++;
      timers.set(id, { fn, runAt: now + Math.max(0, Number(delay) || 0) });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  vm.runInNewContext(fs.readFileSync(LOADER_PATH, "utf8"), sandbox, {
    filename: "auto-scroll-loader.js",
  });

  function advanceTime(ms) {
    const target = now + ms;
    while (true) {
      let earliest = null;
      for (const [id, entry] of timers.entries()) {
        if (entry.runAt <= target && (!earliest || entry.runAt < earliest.entry.runAt)) {
          earliest = { id, entry };
        }
      }
      if (!earliest) break;
      now = earliest.entry.runAt;
      timers.delete(earliest.id);
      earliest.entry.fn();
    }
    now = target;
  }

  function dispatchWindow(type, extra = {}) {
    for (const fn of Array.from(windowListeners.get(type) || [])) {
      fn({ type, ...extra });
    }
  }

  function activeObservers() {
    return observers.filter((obs) => !obs.disconnected);
  }

  return {
    window: sandbox,
    CompanionAutoScroll: sandbox.CompanionAutoScroll,
    observers,
    activeObservers,
    store,
    timers,
    windowListeners,
    windowScrollCalls,
    advanceTime,
    dispatchWindow,
    setStorageThrows(val) { storageThrows = val; },
  };
}

async function flushMicrotasks(rounds = 4) {
  for (let i = 0; i < rounds; i += 1) {
    await Promise.resolve();
  }
}

test("CompanionAutoScroll: auto-loads on intersection, respects 260ms debounce and concurrency lock", async () => {
  const h = createLoaderHarness();
  const host = new FakeElement("div");
  let page = 1;
  let cursor = "c-1";
  let loaded = 10;
  let resolveSecond = null;
  let calls = 0;

  const ctrl = h.CompanionAutoScroll.mount({
    host,
    hasMore: () => Boolean(cursor),
    loadedCount: () => loaded,
    cursor: () => cursor,
    minIntervalMs: 260,
    onLoadMore: () => {
      calls += 1;
      if (calls === 1) {
        page += 1;
        cursor = "c-2";
        loaded += 10;
        return Promise.resolve();
      }
      return new Promise((resolve) => {
        resolveSecond = () => {
          page += 1;
          cursor = "";
          loaded += 5;
          resolve();
        };
      });
    },
  });

  assert.equal(h.activeObservers().length, 1);
  assert.equal(h.activeObservers()[0].options.root, null);
  assert.equal(h.activeObservers()[0].options.rootMargin, "280px 0px");

  // First intersection triggers immediately
  h.activeObservers()[0].trigger(true);
  await flushMicrotasks();
  assert.equal(calls, 1);
  assert.equal(loaded, 20);

  // Immediately re-intersecting within 260ms must be throttled, not double-fired
  h.advanceTime(100);
  h.activeObservers()[0].trigger(true);
  await flushMicrotasks();
  assert.equal(calls, 1);

  // After remaining 160ms elapses, second load starts
  h.advanceTime(160);
  await flushMicrotasks();
  assert.equal(calls, 2);
  assert.equal(ctrl.getState().inFlight, true);

  // Concurrent intersections while in-flight must be ignored by re-entrancy lock
  h.advanceTime(500);
  h.activeObservers()[0].trigger(true);
  await flushMicrotasks();
  assert.equal(calls, 2);

  // Finish second load -> reaches complete state (>=16 items -> shows back-to-top)
  resolveSecond();
  await flushMicrotasks();
  assert.equal(loaded, 25);
  assert.match(host.innerHTML, /已加载全部 25 项/);
  assert.match(host.innerHTML, /data-casl-action="top"/);
  assert.equal(h.activeObservers().length, 0);

  ctrl.destroy();
});

test("CompanionAutoScroll: stall guard halts loop when cursor and count fail to advance, but allows cursor-only advance", async () => {
  const h = createLoaderHarness();
  const host = new FakeElement("div");
  let cursor = "c-1";
  let loaded = 5;
  let calls = 0;

  const ctrl = h.CompanionAutoScroll.mount({
    host,
    hasMore: () => Boolean(cursor),
    loadedCount: () => loaded,
    cursor: () => cursor,
    onLoadMore: async () => {
      calls += 1;
      if (calls === 1) {
        // Empty filtered page: count stays 5, but cursor advances -> must NOT stall
        cursor = "c-2";
      } else if (calls === 2) {
        // Stuck cursor: neither cursor nor count advances -> MUST stall
      } else {
        // Manual retry succeeds
        cursor = "c-3";
        loaded = 10;
      }
    },
  });

  h.activeObservers()[0].trigger(true);
  await flushMicrotasks();
  assert.equal(calls, 1);
  assert.equal(ctrl.getState().stalled, false);

  h.advanceTime(300);
  await flushMicrotasks();
  assert.equal(calls, 2);
  assert.equal(ctrl.getState().stalled, true);
  assert.match(host.innerHTML, /data-casl-state="stalled"/);
  assert.match(host.innerHTML, /点击重试/);
  assert.equal(h.activeObservers().length, 0);

  // Further timer advance must not auto-trigger while stalled
  h.advanceTime(1000);
  await flushMicrotasks();
  assert.equal(calls, 2);

  // Clicking manual retry clears stall and resumes
  const retryBtn = host.querySelector('[data-casl-action="retry"]');
  assert.ok(retryBtn);
  retryBtn.dispatchEvent({ type: "click" });
  await flushMicrotasks();
  assert.equal(calls, 3);
  assert.equal(ctrl.getState().stalled, false);
  assert.equal(loaded, 10);

  ctrl.destroy();
});

test("CompanionAutoScroll: continuous auto-burst safety valve pauses at maxAutoBurst and resets on manual scroll or continue", async () => {
  const h = createLoaderHarness();
  const host = new FakeElement("div");
  let page = 0;

  const ctrl = h.CompanionAutoScroll.mount({
    host,
    maxAutoBurst: 3,
    minIntervalMs: 260,
    hasMore: () => page < 10,
    loadedCount: () => page * 5,
    cursor: () => `p-${page}`,
    onLoadMore: async () => {
      page += 1;
    },
  });

  // Trigger continuous auto-loads with sentinel staying visible
  h.activeObservers()[0].trigger(true);
  await flushMicrotasks();
  assert.equal(page, 1);

  h.advanceTime(260);
  await flushMicrotasks();
  assert.equal(page, 2);

  h.advanceTime(260);
  await flushMicrotasks();
  assert.equal(page, 3);
  assert.equal(ctrl.getState().burstPaused, true);
  assert.match(host.innerHTML, /data-casl-state="paused"/);
  assert.match(host.innerHTML, /点击继续加载/);

  // Wait longer: 4th auto load must NOT fire while paused
  h.advanceTime(1000);
  await flushMicrotasks();
  assert.equal(page, 3);

  // User manual scroll resets burst counter & resumes observer
  h.dispatchWindow("scroll");
  assert.equal(ctrl.getState().burstPaused, false);
  assert.equal(ctrl.getState().burstCount, 0);
  assert.equal(h.activeObservers().length, 1);

  // Hit pause again, then verify clicking "点击继续加载" recovers
  for (let i = 0; i < 3; i += 1) {
    if (i === 0) h.activeObservers()[0].trigger(true);
    else h.advanceTime(260);
    await flushMicrotasks();
  }
  assert.equal(page, 6);
  assert.equal(ctrl.getState().burstPaused, true);

  const continueBtn = host.querySelector('[data-casl-action="more"]');
  assert.ok(continueBtn);
  continueBtn.dispatchEvent({ type: "click" });
  await flushMicrotasks();
  assert.equal(page, 7);
  assert.equal(ctrl.getState().burstPaused, false);
  assert.equal(ctrl.getState().burstCount, 0);

  ctrl.destroy();
});

test("CompanionAutoScroll: error circuit breaker halts auto-loading and manual retry recovers", async () => {
  const h = createLoaderHarness();
  const host = new FakeElement("div");
  let failNext = true;
  let loaded = 4;
  let cursor = "c-1";
  let retryCalls = 0;

  const ctrl = h.CompanionAutoScroll.mount({
    host,
    hasMore: () => Boolean(cursor),
    loadedCount: () => loaded,
    cursor: () => cursor,
    onLoadMore: async () => {
      if (failNext) throw new Error("网络超时，请稍后重试");
      loaded += 4;
      cursor = "";
    },
    onRetry: async () => {
      retryCalls += 1;
      failNext = false;
      loaded += 4;
      cursor = "";
    },
  });

  h.activeObservers()[0].trigger(true);
  await flushMicrotasks();
  assert.equal(ctrl.getState().hasError, true);
  assert.match(host.innerHTML, /data-casl-state="error"/);
  assert.match(host.innerHTML, /网络超时，请稍后重试/);
  assert.equal(h.activeObservers().length, 0);

  const retryBtn = host.querySelector('[data-casl-action="retry"]');
  assert.ok(retryBtn);
  retryBtn.dispatchEvent({ type: "click" });
  await flushMicrotasks();
  assert.equal(retryCalls, 1);
  assert.equal(ctrl.getState().hasError, false);
  assert.equal(loaded, 8);
  assert.match(host.innerHTML, /已加载全部 8 项/);
  // Below threshold (16) -> no back-to-top button
  assert.doesNotMatch(host.innerHTML, /data-casl-action="top"/);

  ctrl.destroy();
});

test("CompanionAutoScroll: localStorage persistence, cross-instance sync, and SecurityError sandbox fallback", async () => {
  const h = createLoaderHarness();
  const hostA = new FakeElement("div");
  const hostB = new FakeElement("div");
  const toggles = [];

  const a = h.CompanionAutoScroll.mount({
    host: hostA,
    hasMore: () => true,
    loadedCount: () => 5,
    onLoadMore: async () => {},
    onToggle: (v) => toggles.push(["A", v]),
  });
  const b = h.CompanionAutoScroll.mount({
    host: hostB,
    hasMore: () => true,
    loadedCount: () => 5,
    onLoadMore: async () => {},
    onToggle: (v) => toggles.push(["B", v]),
  });

  assert.equal(a.isEnabled(), true);
  assert.equal(b.isEnabled(), true);
  assert.equal(h.activeObservers().length, 2);

  // Toggle off via checkbox in A -> persists "0" and disables B too
  const cbA = hostA.querySelector("[data-casl-toggle]");
  assert.ok(cbA);
  cbA.checked = false;
  cbA.dispatchEvent({ type: "change" });

  assert.equal(h.store.get("companion.autoload.v1"), "0");
  assert.equal(a.isEnabled(), false);
  assert.equal(b.isEnabled(), false);
  assert.equal(h.activeObservers().length, 0);
  assert.deepEqual(toggles, [["A", false], ["B", false]]);

  a.destroy();
  b.destroy();

  // Sandboxed opaque-origin iframe where localStorage throws SecurityError
  const sandboxed = createLoaderHarness({ storageThrows: true });
  const hostS = new FakeElement("div");
  const s = sandboxed.CompanionAutoScroll.mount({
    host: hostS,
    hasMore: () => true,
    loadedCount: () => 2,
    onLoadMore: async () => {},
  });
  assert.equal(s.isEnabled(), true);
  assert.doesNotThrow(() => s.setEnabled(false));
  assert.equal(s.isEnabled(), false);
  assert.equal(sandboxed.CompanionAutoScroll.isEnabled(), false);
  s.destroy();
});

test("CompanionAutoScroll: nested scrollRoot, graceful degradation without IO, refresh re-bind, and destroy cleanup", async () => {
  const h = createLoaderHarness({ reducedMotion: true });
  const scrollBox = new FakeElement("div", { class: "mem-corpus" });
  const host1 = new FakeElement("div");
  let loaded = 20;
  let cursor = "c-2";

  const ctrl = h.CompanionAutoScroll.mount({
    host: host1,
    scrollRoot: scrollBox,
    rootMargin: "160px 0px",
    hasMore: () => Boolean(cursor),
    loadedCount: () => loaded,
    cursor: () => cursor,
    onLoadMore: async () => {
      loaded += 5;
      cursor = "";
    },
  });

  assert.equal(h.activeObservers()[0].options.root, scrollBox);
  assert.equal(h.activeObservers()[0].options.rootMargin, "160px 0px");
  assert.equal(scrollBox.listeners.get("scroll").size, 1);

  // Simulate innerHTML rebuild producing a new host node
  const host2 = new FakeElement("div");
  ctrl.refresh(host2, scrollBox);
  assert.equal(h.activeObservers().length, 1);
  assert.equal(host1.listeners.get("click").size, 0);

  // Trigger load to reach end-of-list (>=16 items) and click back-to-top
  h.activeObservers()[0].trigger(true);
  await flushMicrotasks();
  const topBtn = host2.querySelector('[data-casl-action="top"]');
  assert.ok(topBtn);
  topBtn.dispatchEvent({ type: "click" });
  assert.equal(scrollBox.scrollCalls.length, 1);
  assert.equal(scrollBox.scrollCalls[0].top, 0);
  assert.equal(scrollBox.scrollCalls[0].behavior, "auto");

  // Destroy cleans up scroll listeners, window listeners, timers, and observers
  ctrl.destroy();
  assert.equal(h.activeObservers().length, 0);
  assert.equal(scrollBox.listeners.get("scroll").size, 0);
  assert.equal(h.windowListeners.get("storage")?.size || 0, 0);

  // Graceful degradation when IntersectionObserver is unavailable
  const noIo = createLoaderHarness({ noIO: true });
  const fallbackHost = new FakeElement("div");
  let manualLoads = 0;
  const fallbackCtrl = noIo.CompanionAutoScroll.mount({
    host: fallbackHost,
    hasMore: () => true,
    loadedCount: () => 3,
    manualButtonAttrs: { "data-sample-more": "" },
    manualButtonLabel: "读取更早的产出",
    onLoadMore: async () => { manualLoads += 1; },
  });
  assert.match(fallbackHost.innerHTML, /data-casl-state="fallback"/);
  assert.match(fallbackHost.innerHTML, /data-sample-more/);
  assert.match(fallbackHost.innerHTML, /读取更早的产出/);
  const btn = fallbackHost.querySelector("[data-sample-more]");
  assert.ok(btn);
  btn.dispatchEvent({ type: "click" });
  await flushMicrotasks();
  assert.equal(manualLoads, 1);
  fallbackCtrl.destroy();
});

test("CompanionAutoScroll: empty list renders nothing to keep page empty-states clean", () => {
  const h = createLoaderHarness();
  const host = new FakeElement("div");
  const ctrl = h.CompanionAutoScroll.mount({
    host,
    hasMore: () => false,
    loadedCount: () => 0,
    onLoadMore: async () => {},
  });
  assert.equal(host.innerHTML, "");
  assert.equal(h.activeObservers().length, 0);
  ctrl.destroy();
});

test("Asset registration: index.html loads auto-scroll-loader.js before consumer pages and module-ready.js; css-sources.json includes auto-scroll-loader.css", () => {
  const html = fs.readFileSync(INDEX_PATH, "utf8");
  const loaderPos = html.indexOf('src="auto-scroll-loader.js');
  const semanticPos = html.indexOf('src="semantic-page.js');
  const appPos = html.indexOf('src="app.js');
  const readyPos = html.indexOf('src="module-ready.js');
  assert.ok(loaderPos > 0, "index.html must include auto-scroll-loader.js");
  assert.ok(loaderPos < semanticPos, "auto-scroll-loader.js must load before semantic-page.js");
  assert.ok(loaderPos < appPos, "auto-scroll-loader.js must load before app.js");
  assert.ok(loaderPos < readyPos, "auto-scroll-loader.js must load before module-ready.js");

  const cssSources = JSON.parse(fs.readFileSync(CSS_SOURCES_PATH, "utf8"));
  assert.ok(cssSources.includes("auto-scroll-loader.css"), "css-sources.json must include auto-scroll-loader.css");

  // Verify standalone iframe designer app includes isomorphic nested-root auto-scroll & non-destructive append error guard
  const designerCode = fs.readFileSync(DESIGNER_APP_PATH, "utf8");
  assert.match(designerCode, /companion\.autoload\.v1/);
  assert.match(designerCode, /root:\s*list,\s*rootMargin:\s*"160px 0px"/);
  assert.match(designerCode, /draftAppendError\s*=\s*errorText\(error\)/);
  assert.match(designerCode, /drafts-dialog"\)\.addEventListener\("close"/);
});

test("Integration: semantic-page.js mounts CompanionAutoScroll, auto-loads next page, and cleans up on unmount", async () => {
  const h = createLoaderHarness();
  const requests = [];
  const semanticSource = fs.readFileSync(path.join(ROOT, "pages", "陪伴小屋", "semantic-page.js"), "utf8");
  h.window.AbortController = AbortController;
  h.window.CompanionWorkbench = {
    scopeHintMarkup: () => "",
    scopeHintReset: () => {},
    scopeHintKeydown: () => {},
    scopeHintClick: () => false,
    dismissScopeHints: () => {},
  };
  h.window.CompanionProduct = {
    hosted: () => true,
    scopeKey: () => "t1::r1",
    target: () => ({ target_id: "t1", scope_kind: "private" }),
    selection: () => ({ target_id: "t1", role_id: "r1" }),
    bridgeNow: () => ({
      apiGet: async (_route, params) => {
        requests.push(params);
        if (!params.after) {
          return {
            status: "ok",
            data: {
              state: "ready",
              selection_ready: true,
              reaction_ready: true,
              settings: { revision: 1 },
              providers: {},
              catalog: { reaction: { enabled: 2, total: 3, pending: 0, recognized: 2 } },
              items: [
                { id: "r-1", kind: "reaction", enabled: true, revision: 1, payload: { name: "开心" } },
                { id: "r-2", kind: "reaction", enabled: true, revision: 1, payload: { name: "害羞" } },
              ],
              next_after: "cur-2",
            },
          };
        }
        return {
          status: "ok",
          data: {
            state: "ready",
            selection_ready: true,
            reaction_ready: true,
            settings: { revision: 1 },
            providers: {},
            catalog: { reaction: { enabled: 3, total: 3, pending: 0, recognized: 3 } },
            items: [
              { id: "r-3", kind: "reaction", enabled: true, revision: 1, payload: { name: "撒花" } },
            ],
            next_after: null,
          },
        };
      },
      apiPost: async () => ({ status: "ok", data: {} }),
    }),
    unwrap: (r) => r.data,
  };
  h.window.scrollY = 0;

  vm.runInNewContext(semanticSource, h.window, { filename: "semantic-page.js" });

  const root = new FakeElement("div");
  root.classList = { toggle() {} };
  h.window.CompanionSemanticPage.mount(root, { view: "expression" });
  await flushMicrotasks(8);

  assert.equal(requests.length, 1);
  assert.equal(h.activeObservers().length, 1, "expression gallery should observe auto-scroll sentinel");
  assert.match(root.innerHTML, /data-semantic-autoscroll/);

  // Intersecting sentinel triggers page 2 load
  h.activeObservers()[0].trigger(true);
  await flushMicrotasks(8);

  assert.equal(requests.length, 2);
  assert.equal(requests[1].after, "cur-2");
  assert.match(root.innerHTML, /撒花/);
  assert.equal(h.activeObservers().length, 0, "observer disconnects once all items loaded");

  h.window.CompanionSemanticPage.unmount();
  assert.equal(h.activeObservers().length, 0);
});

test("Integration: output-page.js delegates album sentinel & header toggle to CompanionAutoScroll", async () => {
  const h = createLoaderHarness();
  const albumCalls = [];
  const outputSource = fs.readFileSync(path.join(ROOT, "pages", "陪伴小屋", "output-page.js"), "utf8");
  h.window.AbortController = AbortController;
  h.window.Intl = Intl;
  h.window.CompanionProduct = {
    scopeKey: () => "t1::r1",
    role: () => ({
      role_id: "r1",
      model_api: "/astrbot_plugin_private_companion/m",
      image_material_api: "/astrbot_plugin_private_companion/im",
    }),
    targets: () => [{
      scope_kind: "private",
      platform_id: "p1",
      account_id: "a1",
      bot_id: "b1",
      roles: [{ role_id: "r1", image_material_api: "/astrbot_plugin_private_companion/im" }],
    }],
    relative: (p) => p.replace("/astrbot_plugin_private_companion/", ""),
    bridgeFor: async () => ({
      apiGet: async () => ({ status: "ok", data: { enabled: true, endpoints: [], operation_routes: { generate: [], edit: [] }, revision: "1" } }),
    }),
    unwrap: (r) => r.data,
    request: async (_method, route, params) => {
      if (route.endsWith("/album")) {
        albumCalls.push(params);
        if (!params.cursor) {
          return {
            items: [{ image_id: "img-1", thumbnail: "data:image/png;base64,AA", created_at: "2026-11-27T01:00:00Z", action: "image.generate" }],
            total: 2,
            next_cursor: "alb-2",
          };
        }
        return {
          items: [{ image_id: "img-2", thumbnail: "data:image/png;base64,BB", created_at: "2026-11-27T00:00:00Z", action: "image.generate" }],
          total: 2,
          next_cursor: null,
        };
      }
      return { items: [], endpoints: [], enabled: true, revision: "1" };
    },
  };

  vm.runInNewContext(outputSource, h.window, { filename: "output-page.js" });

  const root = new FakeElement("div");
  const albumWorkspace = new FakeElement("div", { id: "outputAlbumWorkspace" });
  const lightbox = new FakeElement("div", { id: "outputAlbumLightbox" });
  root.append(albumWorkspace, lightbox);

  h.window.CompanionOutputPage.mount(root);
  await flushMicrotasks(10);

  assert.equal(albumCalls.length, 1);
  assert.equal(h.activeObservers().length, 1, "output album should mount CompanionAutoScroll observer");
  assert.ok(albumWorkspace.querySelector("[data-output-album-sentinel]"), "sentinel keeps data-output-album-sentinel attribute");

  // Header toggle writes shared companion.autoload.v1 preference and disconnects observer
  const toggleCb = albumWorkspace.querySelector("[data-output-album-autoload]");
  assert.ok(toggleCb);
  toggleCb.checked = false;
  toggleCb.dispatchEvent({ type: "change" });
  assert.equal(h.store.get("companion.autoload.v1"), "0");
  assert.equal(h.activeObservers().length, 0);

  // Re-enabling restores observer and intersection loads page 2
  toggleCb.checked = true;
  toggleCb.dispatchEvent({ type: "change" });
  assert.equal(h.store.get("companion.autoload.v1"), "1");
  assert.equal(h.activeObservers().length, 1);

  h.activeObservers()[0].trigger(true);
  await flushMicrotasks(10);
  assert.equal(albumCalls.length, 2);
  assert.equal(albumCalls[1].cursor, "alb-2");
  assert.match(albumWorkspace.innerHTML, /img-2/);
  assert.equal(h.activeObservers().length, 0);

  h.window.CompanionOutputPage.unmount();
});

