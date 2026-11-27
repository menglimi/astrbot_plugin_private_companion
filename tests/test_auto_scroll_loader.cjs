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
    const tagRe = /<(div|button|input|label|span|p|article|select|section|form)\b([^>]*)>/gi;
    let m;
    while ((m = tagRe.exec(this._innerHTML))) {
      const child = new FakeElement(m[1], parseAttrs(m[2]));
      child.parentElement = this;
      this.children.push(child);
    }
  }

  getAttribute(name) {
    if (Object.prototype.hasOwnProperty.call(this.attrs, name)) return this.attrs[name];
    if (name.startsWith("data-")) {
      const camel = name.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
      if (Object.prototype.hasOwnProperty.call(this.dataset, camel)) return String(this.dataset[camel]);
    }
    return null;
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
    if (Object.prototype.hasOwnProperty.call(this.attrs, name)) return true;
    if (name.startsWith("data-")) {
      const camel = name.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
      return Object.prototype.hasOwnProperty.call(this.dataset, camel);
    }
    return false;
  }

  contains(node) {
    if (node === this) return true;
    for (const child of this.children) {
      if (child.contains(node)) return true;
    }
    return false;
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
  h.activeObservers()[0].trigger(true);
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

  // Trigger continuous auto-loads with sentinel staying visible after each render
  h.activeObservers()[0].trigger(true);
  await flushMicrotasks();
  assert.equal(page, 1);

  h.advanceTime(260);
  h.activeObservers()[0].trigger(true);
  await flushMicrotasks();
  assert.equal(page, 2);

  h.advanceTime(260);
  h.activeObservers()[0].trigger(true);
  await flushMicrotasks();
  assert.equal(page, 3);
  assert.equal(ctrl.getState().burstPaused, true);
  assert.match(host.innerHTML, /data-casl-state="paused"/);
  assert.match(host.innerHTML, /点击继续加载/);

  // Wait longer: 4th auto load must NOT fire while paused
  h.advanceTime(1000);
  await flushMicrotasks();
  assert.equal(page, 3);

  // User manual scroll (after settle window) resets burst counter & resumes observer
  h.dispatchWindow("scroll");
  assert.equal(ctrl.getState().burstPaused, false);
  assert.equal(ctrl.getState().burstCount, 0);
  assert.equal(h.activeObservers().length, 1);

  // Hit pause again, then verify clicking "点击继续加载" recovers
  for (let i = 0; i < 3; i += 1) {
    if (i > 0) h.advanceTime(260);
    h.activeObservers()[0].trigger(true);
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

test("CompanionAutoScroll: slow request (>=260ms) does not cascade runaway auto-loads before new sentinel intersects", async () => {
  const h = createLoaderHarness();
  const host = new FakeElement("div");
  let page = 1;
  let calls = 0;

  const ctrl = h.CompanionAutoScroll.mount({
    host,
    minIntervalMs: 260,
    maxAutoBurst: 12,
    hasMore: () => page < 20,
    loadedCount: () => page * 10,
    cursor: () => `cur-${page}`,
    onLoadMore: async () => {
      calls += 1;
      // Simulate real API round-trip >= 260ms so elapsed >= minIntervalMs upon resolve
      h.advanceTime(350);
      page += 1;
    },
  });

  // Single scroll-to-bottom triggers page 2; new items push rebuilt sentinel out of viewport
  h.activeObservers()[0].trigger(true);
  await flushMicrotasks();
  assert.equal(calls, 1);
  assert.equal(page, 2);
  assert.equal(ctrl.getState().intersecting, false, "rebuilt sentinel must start non-intersecting until measured");

  // Even after waiting long past minIntervalMs, must NOT auto-load again without new IO intersection
  h.advanceTime(2000);
  await flushMicrotasks();
  assert.equal(calls, 1, "must not cascade-load when new sentinel has not intersected");

  // Explicitly reporting non-intersection keeps calls at 1
  h.activeObservers()[0].trigger(false);
  h.advanceTime(500);
  await flushMicrotasks();
  assert.equal(calls, 1);

  // Only when user actually scrolls again and new sentinel intersects does next load fire
  h.activeObservers()[0].trigger(true);
  await flushMicrotasks();
  assert.equal(calls, 2);
  ctrl.destroy();
});

test("CompanionAutoScroll: refresh(null) detaches host and scroll listeners without leaking onto window", () => {
  const h = createLoaderHarness();
  const scrollBox = new FakeElement("div", { class: "mem-corpus" });
  const host = new FakeElement("div");

  const ctrl = h.CompanionAutoScroll.mount({
    host,
    scrollRoot: scrollBox,
    hasMore: () => true,
    loadedCount: () => 5,
    onLoadMore: async () => {},
  });

  assert.equal(host.listeners.get("click").size, 1);
  assert.equal(host.listeners.get("change").size, 1);
  assert.equal(scrollBox.listeners.get("scroll").size, 1);
  assert.equal(h.windowListeners.get("scroll")?.size || 0, 0);

  // Detaching via refresh(null) must release old host & scroll listeners and NOT bind window
  ctrl.refresh(null, null);
  assert.equal(host.listeners.get("click").size, 0, "old host click listener must be removed");
  assert.equal(host.listeners.get("change").size, 0, "old host change listener must be removed");
  assert.equal(scrollBox.listeners.get("scroll").size, 0, "old scrollRoot scroll listener must be removed");
  assert.equal(h.windowListeners.get("scroll")?.size || 0, 0, "must not attach scroll listener to window when host is null");
  assert.equal(h.windowListeners.get("wheel")?.size || 0, 0, "must not attach wheel listener to window when host is null");
  assert.equal(h.activeObservers().length, 0);

  ctrl.destroy();
});

test("CompanionAutoScroll: programmatic restoreScroll during load settle window does not clear burstCount, while wheel resets immediately", async () => {
  const h = createLoaderHarness();
  const host = new FakeElement("div");
  let page = 0;

  const ctrl = h.CompanionAutoScroll.mount({
    host,
    maxAutoBurst: 2,
    minIntervalMs: 260,
    hasMore: () => page < 10,
    loadedCount: () => page * 5,
    cursor: () => `p-${page}`,
    onLoadMore: async () => {
      page += 1;
      // Simulate page restoreScroll() firing window scroll during load
      h.dispatchWindow("scroll");
    },
  });

  // Load 1 + immediate async programmatic scroll (within 150ms settle window)
  h.activeObservers()[0].trigger(true);
  await flushMicrotasks();
  h.advanceTime(40);
  h.dispatchWindow("scroll");
  assert.equal(ctrl.getState().burstCount, 1, "programmatic scroll inside settle window must not reset burstCount");

  // Advance past minIntervalMs without firing user scroll, then trigger load 2 -> hits safety valve
  h.advanceTime(220);
  h.activeObservers()[0].trigger(true);
  await flushMicrotasks();
  h.advanceTime(40);
  h.dispatchWindow("scroll");
  assert.equal(page, 2);
  assert.equal(ctrl.getState().burstCount, 2);
  assert.equal(ctrl.getState().burstPaused, true, "maxAutoBurst=2 must pause even with restoreScroll events");

  // Genuine user wheel event immediately resets burstPaused even inside settle window
  h.dispatchWindow("wheel");
  assert.equal(ctrl.getState().burstCount, 0);
  assert.equal(ctrl.getState().burstPaused, false);
  assert.equal(h.activeObservers().length, 1);

  ctrl.destroy();
});

test("Integration: memory-page.js preserves cursor & corpus on append failure, avoids misleading fallback, and recovers on retry", async () => {
  const h = createLoaderHarness();
  const memorySource = fs.readFileSync(path.join(ROOT, "pages", "陪伴小屋", "memory-page.js"), "utf8");
  const getCalls = [];
  const postCalls = [];
  let failFactsAppendOnce = true;
  let failQueryAppendOnce = true;

  h.window.CSS = { escape: (v) => String(v) };
  h.window.Intl = Intl;
  h.window.crypto = { randomUUID: () => "uuid-1" };
  h.window.requestAnimationFrame = () => 0;
  h.window.performance = { now: () => 0 };
  h.window.document = {
    documentElement: { dataset: { motion: "normal" } },
    addEventListener: () => {},
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: (tag) => new FakeElement(tag),
    body: { classList: { add() {}, remove() {}, toggle() {} } },
    title: "",
    hidden: false,
  };
  h.window.AstrBotPluginPage = {
    ready: async () => ({ pluginName: "astrbot_plugin_private_companion" }),
    apiGet: async (route, params) => {
      getCalls.push({ route, params });
      if (route === "page/ownership") return { owner: "self", reason: "owner_module_absent", display_name: "" };
      if (route === "page/targets") {
        return {
          status: "ok",
          data: {
            installation_id: "inst",
            operator_id: "op",
            targets: [{
              target_id: "t1", scope_kind: "private", participant_id: "u1", platform_id: "p1", account_id: "a1",
              roles: [{ role_id: "r1", memory_api: "/astrbot_plugin_private_companion/memory" }],
            }],
          },
        };
      }
      if (route === "memory/overview") {
        return { status: "ok", data: { local: { facts: { total: 6 }, reflections: { total: 0, by_state: {} } } } };
      }
      if (route === "memory") {
        const after = params?.after_id || "";
        if (!after) {
          return {
            status: "ok",
            data: {
              items: [{ atom_id: "a1", revision: 1, state: "active", representation: "喜欢晴天" }],
              next_cursor: "cur-p2",
            },
          };
        }
        if (after === "cur-p2") {
          return { status: "ok", data: { items: [{ atom_id: "a2", revision: 1, state: "active", representation: "养了一只猫" }], next_cursor: "cur-p3" } };
        }
        if (after === "cur-p3") {
          return { status: "ok", data: { items: [{ atom_id: "a3", revision: 1, state: "active", representation: "爱喝拿铁" }], next_cursor: "cur-p4" } };
        }
        if (after === "cur-p4") {
          return { status: "ok", data: { items: [{ atom_id: "a4", revision: 1, state: "active", representation: "早睡早起" }], next_cursor: "cur-facts-5" } };
        }
        if (after === "cur-facts-5" && failFactsAppendOnce) {
          failFactsAppendOnce = false;
          throw new Error("Failed to fetch");
        }
        return {
          status: "ok",
          data: {
            items: [{ atom_id: "a5", revision: 1, state: "active", representation: "周末爱看电影" }],
            next_cursor: null,
          },
        };
      }
      return { status: "ok", data: {} };
    },
    apiPost: async (route, body) => {
      postCalls.push({ route, body });
      if (route === "memory/query") {
        if (!body.cursor) {
          return {
            status: "ok",
            data: {
              session_id: "qs-1",
              items: [{ atom_id: "q1", excerpt: "第一页命中片段" }],
              next_cursor: "cur-q-2",
            },
          };
        }
        if (failQueryAppendOnce) {
          failQueryAppendOnce = false;
          throw new Error("Network down");
        }
        return {
          status: "ok",
          data: {
            session_id: "qs-1",
            items: [{ atom_id: "q2", excerpt: "第二页命中片段" }],
            next_cursor: null,
          },
        };
      }
      return { status: "ok", data: {} };
    },
  };
  h.window.CompanionProduct = {
    hosted: () => true,
    selection: () => null,
    choose: () => {},
    bridgeNow: () => h.window.AstrBotPluginPage,
    bridgeFor: async () => h.window.AstrBotPluginPage,
  };

  vm.runInNewContext(memorySource, h.window, { filename: "memory-page.js" });

  const root = new FakeElement("div");
  await h.window.CompanionMemoryPage.mount(root);
  await flushMicrotasks(12);

  assert.match(root.innerHTML, /喜欢晴天/);
  assert.equal(h.activeObservers().length, 1);

  // Trigger page-2 facts load -> fails with codeless network error
  h.activeObservers()[0].trigger(true);
  await flushMicrotasks(12);

  assert.match(root.innerHTML, /喜欢晴天/, "loaded facts must be preserved on append failure");
  assert.match(root.innerHTML, /继续读取更早事实时失败，已保留已加载记录。/, "codeless error must use Chinese fallback");
  assert.doesNotMatch(root.innerHTML, /暂无具体原因/, "must not show misleading '暂无具体原因'");

  // Clicking retry must still have data.next === 'cur-facts-2' and load page 2!
  const retryFactsBtn = root.querySelector('[data-casl-action="retry"]');
  assert.ok(retryFactsBtn, "retry button must be rendered");
  retryFactsBtn.dispatchEvent({ type: "click" });
  await flushMicrotasks(12);

  const appendCalls = getCalls.filter((c) => c.route === "memory" && c.params?.after_id === "cur-facts-5");
  assert.equal(appendCalls.length, 2, "retry must re-request using preserved data.next cursor");
  assert.match(root.innerHTML, /周末爱看电影/, "retry must append page 5 facts");

  // Switch to query area and verify runQuery({next: true}) append error fallback + retry
  const queryTab = root.querySelector("#memory-area-query");
  assert.ok(queryTab);
  queryTab.dispatchEvent({ type: "click" });
  await flushMicrotasks(8);

  const qInput = root.querySelector("#memory-query-text");
  const qKind = root.querySelector("#memory-query-kind");
  const qForm = root.querySelector("#memory-query-form");
  qInput.value = "晴天";
  qKind.value = "all";
  qForm.dispatchEvent({ type: "submit" });
  await flushMicrotasks(12);

  assert.match(root.innerHTML, /第一页命中片段/);
  assert.equal(h.activeObservers().length, 1);

  // Next query page fails with codeless network error
  h.activeObservers()[0].trigger(true);
  await flushMicrotasks(12);

  assert.match(root.innerHTML, /第一页命中片段/, "query items must stay preserved on next-page failure");
  assert.match(root.innerHTML, /读取下一页查询结果失败，已保留当前结果。/);
  assert.doesNotMatch(root.innerHTML, /暂无具体原因/);

  const retryQueryBtn = root.querySelector('[data-casl-action="retry"]');
  assert.ok(retryQueryBtn);
  retryQueryBtn.dispatchEvent({ type: "click" });
  await flushMicrotasks(12);

  assert.match(root.innerHTML, /第二页命中片段/, "query retry must append page 2 results");
  h.window.CompanionMemoryPage.unmount();
});

test("Integration: pages/新的她/app.js handles dialog close race with draftReqSeq, syncs storage preference, and has warn/data-motion CSS", async () => {
  const h = createLoaderHarness();
  const designerJs = fs.readFileSync(DESIGNER_APP_PATH, "utf8");
  const designerCss = fs.readFileSync(path.join(ROOT, "pages", "新的她", "style.css"), "utf8");
  assert.match(designerCss, /\.draft-scroll-bar\[data-state="warn"\]/);
  assert.match(designerCss, /:root:is\(\[data-motion="low"\],\[data-motion="off"\]\)\s+\.draft-scroll-dots\s+i/);

  const elementsById = new Map();
  function makeDesignerEl(tag, id = "") {
    const el = new FakeElement(tag, id ? { id } : {});
    el.open = false;
    el.classList = { add() {}, remove() {}, toggle() {} };
    el.focus = () => {};
    el.select = () => {};
    el.showModal = () => { el.open = true; };
    el.close = () => {
      if (el.open) {
        el.open = false;
        el.dispatchEvent({ type: "close" });
      }
    };
    return el;
  }
  const modeSwitch = makeDesignerEl("div");
  h.window.parent = h.window;
  h.window.crypto = { getRandomValues: (arr) => arr };
  h.window.Intl = Intl;
  h.window.document = {
    documentElement: {
      dataset: { motion: "normal" },
      classList: { add() {}, remove() {}, toggle() {} },
    },
    getElementById(id) {
      if (!elementsById.has(id)) elementsById.set(id, makeDesignerEl(id.endsWith("dialog") ? "dialog" : "div", id));
      return elementsById.get(id);
    },
    createElement(tag) {
      return makeDesignerEl(tag);
    },
    querySelector(sel) {
      if (sel === ".mode-switch") return modeSwitch;
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };

  const pendingDraftReqs = [];
  h.window.AstrBotPluginPage = {
    ready: async () => ({ tab: "" }),
    apiGet: async (route, params) => {
      if (route === "designer/client-state") return { status: "ok", data: { state: null } };
      if (route === "designer/options") return { status: "ok", data: { models: [], personas: [] } };
      if (route === "designer/drafts") {
        return new Promise((resolve) => {
          pendingDraftReqs.push({ params, resolve });
        });
      }
      return { status: "ok", data: {} };
    },
    apiPost: async () => ({ status: "ok", data: {} }),
  };

  vm.runInNewContext(designerJs, h.window, { filename: "designer-app.js" });
  await flushMicrotasks(10);

  const openBtn = elementsById.get("open-drafts");
  const dialog = elementsById.get("drafts-dialog");
  const draftList = elementsById.get("draft-list");

  // 1) Open dialog -> request #1 in-flight -> close dialog before response arrives
  openBtn.dispatchEvent({ type: "click" });
  await flushMicrotasks(4);
  assert.equal(pendingDraftReqs.length, 1);
  assert.equal(dialog.open, true);

  dialog.close();
  assert.equal(h.activeObservers().length, 0);

  // Stale request #1 settles after dialog closed -> MUST NOT resurrect observer
  pendingDraftReqs[0].resolve({
    status: "ok",
    data: { items: [{ id: "d1", title: "旧稿", created_at: "2026-11-27T01:00:00Z" }], next_before: 10 },
  });
  await flushMicrotasks(8);
  assert.equal(h.activeObservers().length, 0, "closed dialog must not resurrect IntersectionObserver");

  // 2) Open dialog -> request #2 pending -> close and immediately reopen -> request #3 must NOT be swallowed
  openBtn.dispatchEvent({ type: "click" });
  await flushMicrotasks(4);
  assert.equal(pendingDraftReqs.length, 2);
  dialog.close();

  // Also test cross-page localStorage update before reopen
  h.store.set("companion.autoload.v1", "0");
  openBtn.dispatchEvent({ type: "click" });
  await flushMicrotasks(4);
  assert.equal(pendingDraftReqs.length, 3, "reopening dialog must start fresh request rather than being blocked by stale loadingDrafts");

  // Resolve stale #2 first -> ignored
  pendingDraftReqs[1].resolve({
    status: "ok",
    data: { items: [{ id: "d-stale", title: "过期响应", created_at: "2026-11-27T01:00:00Z" }], next_before: 99 },
  });
  await flushMicrotasks(6);

  // Resolve current #3 -> rendered, and respects companion.autoload.v1 === '0' (observer not attached)
  pendingDraftReqs[2].resolve({
    status: "ok",
    data: { items: [{ id: "d-fresh", title: "最新草稿", created_at: "2026-11-27T02:00:00Z" }], next_before: 20 },
  });
  await flushMicrotasks(8);
  assert.equal(h.activeObservers().length, 0, "reopening dialog must re-read companion.autoload.v1='0'");

  // 3) Cross-tab storage event turns autoload back ON -> mounts observer immediately
  h.store.set("companion.autoload.v1", "1");
  h.dispatchWindow("storage", { key: "companion.autoload.v1", newValue: "1" });
  await flushMicrotasks(4);
  assert.equal(h.activeObservers().length, 1, "storage event enabling autoload must attach observer");
  const toggleInput = draftList.querySelector("input");
  assert.ok(toggleInput);
  assert.equal(toggleInput.checked, true);

  dialog.close();
  assert.equal(h.activeObservers().length, 0);
});
