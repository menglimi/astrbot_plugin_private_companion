"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const read = rel => fs.readFileSync(path.join(ROOT, rel), "utf8");

test("persona hub renders sticky navigation bar, compact tabs, and syncs aria-selected cleanly", () => {
  const appSource = read("pages/陪伴小屋/app.js");
  const stylesCss = read("pages/陪伴小屋/styles.css");

  assert.match(stylesCss, /\.persona-hub-bar\{[^}]*position:sticky;top:0;/);
  assert.match(appSource, /class="persona-hub-back" data-self-pane="profile"/);
  assert.match(appSource, /data-persona-switch="\$\{mode\.id\}"/);

  // Extract pure persona hub helpers and test syncPersonaModeUI + sandbox storage fallback.
  const startHelpers = appSource.indexOf("const PERSONA_MODE_IDS=");
  const endHelpers = appSource.indexOf("let maintenancePane=");
  const startHub = appSource.indexOf("const PERSONA_HUB_MODES=");
  const endHub = appSource.indexOf("function renderHostWorkspace()");
  assert.ok(startHelpers > 0 && endHelpers > startHelpers);
  assert.ok(startHub > 0 && endHub > startHub);

  const listeners = {};
  const makeButton = (dataset, initialAttrs = {}) => {
    const attrs = {...initialAttrs};
    const classes = new Set();
    const badge = {textContent: ""};
    return {
      dataset,
      attrs,
      badge,
      classList: {toggle(name, on) { if (on) classes.add(name); else classes.delete(name); }, has: name => classes.has(name)},
      setAttribute(k, v) { attrs[k] = String(v); },
      getAttribute(k) { return attrs[k]; },
      querySelector(sel) { return sel === ".persona-mode-current" ? badge : null; },
    };
  };
  const tabButtons = ["questionnaire", "create", "optimize"].map(id =>
    makeButton({personaRecreation: id}, {"aria-selected": String(id === "questionnaire")}));
  const switchButtons = ["questionnaire", "create", "optimize"].map(id =>
    makeButton({personaSwitch: id}, {"aria-pressed": String(id === "questionnaire")}));
  const panel = {attrs: {}, setAttribute(k, v) { this.attrs[k] = String(v); }};
  const stepsEl = {innerHTML: ""};

  const throwingStorage = {
    getItem() { throw new Error("SecurityError"); },
    setItem() { throw new Error("SecurityError"); },
  };
  const ctx = vm.createContext({
    localStorage: throwingStorage,
    I: name => `<i data-icon="${name}"></i>`,
    roleName: () => "测试角色",
    product: {workspace: (kind, tab) => `<section data-workspace="${kind}" data-tab="${tab || ""}"></section>`, mountWorkspace() {}},
    paintPanel() {},
    requestAnimationFrame: cb => cb(),
    document: {
      addEventListener(type, fn) { listeners[type] = fn; },
      querySelectorAll(sel) {
        if (sel === '[role="tab"][data-persona-recreation]') return tabButtons;
        if (sel === "[data-persona-switch]") return switchButtons;
        return [];
      },
      getElementById(id) {
        if (id === "persona-workspace-panel") return panel;
        if (id === "persona-guide-steps") return stepsEl;
        return null;
      },
    },
  });
  vm.runInContext(appSource.slice(startHelpers, endHelpers) + "\n" + appSource.slice(startHub, endHub), ctx);

  // Initial mode degrades cleanly when localStorage throws SecurityError.
  assert.equal(vm.runInContext("personaRecreationMode", ctx), "questionnaire");
  const html = ctx.renderPersonaPage();
  assert.match(html, /class="persona-hub-bar"/);
  assert.match(html, /data-self-pane="profile"/);
  assert.match(html, /role="tab" id="persona-mode-questionnaire"[^>]*aria-selected="true"/);
  assert.doesNotMatch(html, /role="tab"[^>]*aria-pressed/);

  // Restoring an optimize draft from the designer iframe updates aria-selected, quick switch, aria-labelledby, and steps.
  listeners["companion:persona-mode-change"]({detail: {mode: "optimize"}});
  assert.equal(vm.runInContext("personaRecreationMode", ctx), "optimize");
  const optTab = tabButtons.find(b => b.dataset.personaRecreation === "optimize");
  const qTab = tabButtons.find(b => b.dataset.personaRecreation === "questionnaire");
  assert.equal(optTab.getAttribute("aria-selected"), "true");
  assert.equal(optTab.getAttribute("aria-pressed"), undefined);
  assert.equal(optTab.getAttribute("tabindex"), "0");
  assert.equal(optTab.badge.textContent, "当前");
  assert.equal(qTab.getAttribute("aria-selected"), "false");
  assert.equal(qTab.getAttribute("tabindex"), "-1");
  assert.equal(qTab.badge.textContent, "");
  const optSwitch = switchButtons.find(b => b.dataset.personaSwitch === "optimize");
  assert.equal(optSwitch.getAttribute("aria-pressed"), "true");
  assert.equal(panel.attrs["aria-labelledby"], "persona-mode-optimize");
  assert.match(stepsEl.innerHTML, /载入人设指出痛点/);
});

test("persona workshop reference-material fits full placeholder and keeps in-memory drafts without localStorage", () => {
  const html = read("pages/人物设定工坊/workspace.html");
  const css = read("pages/人物设定工坊/style.css");
  const js = read("pages/人物设定工坊/app.js");

  assert.match(html, /id="reference-material" rows="6"/);
  assert.match(css, /#reference-material\{min-height:10\.5em\}/);
  assert.match(js, /const memoryDrafts = new Map\(\);/);
  assert.match(js, /function readStoredDraft\(key\)/);
  assert.match(js, /function writeStoredDraft\(key, payload\)/);

  const start = js.indexOf("  const memoryDrafts = new Map();");
  const end = js.indexOf("  function persist()", start);
  assert.ok(start > 0 && end > start);
  const ctx = vm.createContext({
    Map,
    encodeURIComponent,
    sourceId: "persona-a",
    localStorage: {
      getItem() { throw new Error("SecurityError"); },
      setItem() { throw new Error("SecurityError"); },
    },
  });
  vm.runInContext(js.slice(start, end), ctx);
  const key = ctx.workspaceKey();
  ctx.writeStoredDraft(key, JSON.stringify({answers: {identity: "测角色"}}));
  assert.deepEqual(JSON.parse(ctx.readStoredDraft(key)), {answers: {identity: "测角色"}});
});
