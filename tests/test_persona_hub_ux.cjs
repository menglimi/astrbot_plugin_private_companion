"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const read = rel => fs.readFileSync(path.join(ROOT, rel), "utf8");

test("persona hub trims redundant mode cards and guide bar into a single slim sticky bar", () => {
  const appSource = read("pages/陪伴小屋/app.js");
  const stylesCss = read("pages/陪伴小屋/styles.css");

  assert.match(stylesCss, /\.persona-hub-bar\{[^}]*position:sticky;top:0;/);
  assert.doesNotMatch(stylesCss, /\.persona-mode-grid\{/);
  assert.doesNotMatch(stylesCss, /\.persona-guide\{/);
  assert.match(appSource, /class="persona-hub-back" data-self-pane="profile"/);
  assert.match(appSource, /data-persona-switch="\$\{mode\.id\}"/);

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
    return {
      dataset,
      attrs,
      classList: {toggle(name, on) { if (on) classes.add(name); else classes.delete(name); }, has: name => classes.has(name)},
      setAttribute(k, v) { attrs[k] = String(v); },
      getAttribute(k) { return attrs[k]; },
    };
  };
  const tabButtons = ["questionnaire", "create", "optimize"].map(id =>
    makeButton({personaRecreation: id, personaSwitch: id}, {"aria-selected": String(id === "questionnaire")}));
  const panel = {attrs: {}, setAttribute(k, v) { this.attrs[k] = String(v); }};
  let mounted = 0;

  const throwingStorage = {
    getItem() { throw new Error("SecurityError"); },
    setItem() { throw new Error("SecurityError"); },
  };
  const ctx = vm.createContext({
    localStorage: throwingStorage,
    I: name => `<i data-icon="${name}"></i>`,
    roleName: () => "测试角色",
    product: {workspace: (kind, tab) => `<section data-workspace="${kind}" data-tab="${tab || ""}"></section>`, mountWorkspace() { mounted++; }},
    paintPanel() {},
    requestAnimationFrame: cb => cb(),
    document: {
      addEventListener(type, fn) { listeners[type] = fn; },
      querySelectorAll(sel) {
        if (sel === '[role="tab"][data-persona-recreation]') return tabButtons;
        return [];
      },
      getElementById(id) {
        if (id === "persona-workspace-panel") return panel;
        return null;
      },
    },
  });
  vm.runInContext(appSource.slice(startHelpers, endHelpers) + "\n" + appSource.slice(startHub, endHub), ctx);

  assert.equal(vm.runInContext("personaRecreationMode", ctx), "questionnaire");
  const html = ctx.renderPersonaPage();
  assert.match(html, /class="persona-hub-bar"/);
  assert.match(html, /data-self-pane="profile"/);
  assert.match(html, /role="tab" id="persona-mode-questionnaire"[^>]*aria-selected="true"/);
  assert.doesNotMatch(html, /role="tab"[^>]*aria-pressed/);
  assert.doesNotMatch(html, /persona-mode-grid/);
  assert.doesNotMatch(html, /persona-guide/);
  assert.match(html, /class="persona-helper"/);

  // Restoring an optimize draft from the designer iframe updates aria-selected and aria-labelledby cleanly.
  listeners["companion:persona-mode-change"]({detail: {mode: "optimize"}});
  assert.equal(vm.runInContext("personaRecreationMode", ctx), "optimize");
  const optTab = tabButtons.find(b => b.dataset.personaRecreation === "optimize");
  const qTab = tabButtons.find(b => b.dataset.personaRecreation === "questionnaire");
  assert.equal(optTab.getAttribute("aria-selected"), "true");
  assert.equal(optTab.getAttribute("tabindex"), "0");
  assert.equal(qTab.getAttribute("aria-selected"), "false");
  assert.equal(qTab.getAttribute("tabindex"), "-1");
  assert.equal(panel.attrs["aria-labelledby"], "persona-mode-optimize");

  // Cross-mode handoff from designer switches back to questionnaire cleanly.
  listeners["companion:persona-handoff"]({detail: {text: "人设草稿"}});
  assert.equal(vm.runInContext("personaRecreationMode", ctx), "questionnaire");
  assert.equal(mounted, 1);
});

test("persona workshop beginner UX: embedded dedup, no emoji lock, modern picker, habits alias, and memory fallback", () => {
  const html = read("pages/人物设定工坊/workspace.html");
  const css = read("pages/人物设定工坊/style.css");
  const js = read("pages/人物设定工坊/app.js");

  // Title, copy fixes, embedded dedup, and zero Emoji lock
  assert.match(html, /<title>规范人格 · 陪伴<\/title>/);
  assert.doesNotMatch(js, /创建人格目前只适用于私聊对象/);
  assert.match(js, /persona_model_output_invalid/);
  assert.match(css, /\.is-embedded \.pw-head\s*\{\s*display:\s*none\s*!important;\s*\}/);
  assert.doesNotMatch(css, /🔒/);
  assert.match(html, /class="seg-ico seg-ico-lock"/);
  assert.match(html, /class="seg-opt">可选</);

  // Modern searchable card picker, strength cards, provider select, collapsed optional clues, stopwatch + cancel, Step 5 adopt-all + save guide
  assert.doesNotMatch(html, /<select[^>]*size=/);
  assert.match(html, /id="persona-search"/);
  assert.match(html, /id="persona-picker-grid"/);
  assert.match(html, /id="workshop-provider"/);
  assert.match(html, /id="optional-clues" class="clues-collapse"/);
  assert.doesNotMatch(html, /id="optional-clues"[^>]*\bopen\b/);
  assert.match(html, /id="clues-count"[^>]*>0\/10</);
  assert.match(html, /rec-badge">推荐</);
  assert.match(html, /id="gen-progress"/);
  assert.match(html, /id="cancel-generation"/);
  assert.match(html, /id="adopt-all-right"/);
  assert.match(html, /id="save-success-guide"/);
  assert.match(html, /id="copy-saved-full"/);
  assert.match(html, /id="reference-material" rows="6"/);
  assert.match(css, /#reference-material\{min-height:10\.5em\}/);

  // Memory fallback when localStorage throws SecurityError
  assert.match(js, /const memoryDrafts = new Map\(\);/);
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

  // extractCanonical handles BOTH canonical "兴趣习惯与表达" and legacy "表达习惯与日常" without dropping habits
  const secStart = js.indexOf("  const sectionRows = [");
  const secEnd = js.indexOf("  const questionKeys =", secStart);
  const extStart = js.indexOf("  function emptySections()");
  const extEnd = js.indexOf("  const persona =", extStart);
  const canStart = js.indexOf("  function canonical(values, style, examples)");
  const canEnd = js.indexOf("  function classifySource(text)", canStart);
  const parseCtx = vm.createContext({
    fixed: {basic: "# 基本要求", safety: "# 对话安全", init: "# 初始化"},
  });
  vm.runInContext(
    js.slice(secStart, secEnd) + "\n" + js.slice(extStart, extEnd) + "\n" + js.slice(canStart, canEnd),
    parseCtx,
  );
  const canonicalDoc = parseCtx.canonical({
    identity: "领航员",
    motivations: "守护航线",
    habits: "喜欢泡麦茶，口头禅是慢慢来。",
    boundaries: "不透漏住址",
  }, "", "");
  assert.match(canonicalDoc, /## 兴趣习惯与表达/);
  const parsedCanonical = parseCtx.extractCanonical(canonicalDoc);
  assert.equal(parsedCanonical.sections.motivations, "守护航线");
  assert.equal(parsedCanonical.sections.habits, "喜欢泡麦茶，口头禅是慢慢来。");

  const legacyDoc = canonicalDoc.replace("## 兴趣习惯与表达", "## 表达习惯与日常");
  const parsedLegacy = parseCtx.extractCanonical(legacyDoc);
  assert.equal(parsedLegacy.sections.motivations, "守护航线");
  assert.equal(parsedLegacy.sections.habits, "喜欢泡麦茶，口头禅是慢慢来。");
});

test("persona designer beginner UX: auto model select, optimize quick chips, localStorage sync, and workshop handoff", () => {
  const html = read("pages/新的她/workspace.html");
  const js = read("pages/新的她/app.js");
  const mem = read("pages/陪伴小屋/persona-designer-memory.js");

  assert.doesNotMatch(html, /<select[^>]*size=/);
  assert.match(html, /id="persona-card-grid"/);
  assert.match(html, /id="quick-reset"/);
  assert.match(html, /id="send-to-workshop"/);
  for (const chip of ["说话太像 AI 客服", "回复太啰嗦", "人设容易崩", "口吻再自然点"]) {
    assert.ok(js.includes(chip), `optimize starter should include chip: ${chip}`);
  }
  assert.match(js, /ensureDefaultModelSelected/);
  assert.match(js, /maybeAutoPreloadOptimizePersona/);
  assert.match(js, /companion\.persona-designer\.v1\./);
  assert.match(mem, /designer\/local-draft/);
});

test("card pickers keep hidden selects out of tab order and support roving tabindex keyboard navigation", () => {
  const workshopHtml = read("pages/人物设定工坊/workspace.html");
  const designerHtml = read("pages/新的她/workspace.html");
  const workshopJs = read("pages/人物设定工坊/app.js");
  const designerJs = read("pages/新的她/app.js");

  assert.match(workshopHtml, /id="source-persona"[^>]*tabindex="-1"[^>]*aria-hidden="true"/);
  assert.match(designerHtml, /id="persona-select"[^>]*tabindex="-1"[^>]*aria-hidden="true"/);
  assert.doesNotMatch(designerHtml, /id="persona-select"[^>]*\brequired\b/);

  for (const [label, src] of [["workshop", workshopJs], ["designer", designerJs]]) {
    assert.match(src, /ArrowRight.*ArrowDown/, `${label} must handle ArrowRight/ArrowDown`);
    assert.match(src, /ArrowLeft.*ArrowUp/, `${label} must handle ArrowLeft/ArrowUp`);
    assert.match(src, /event\.key === "Home"/, `${label} must handle Home`);
    assert.match(src, /event\.key === "End"/, `${label} must handle End`);
    assert.match(src, /card\.tabIndex = index === tabIndexTarget \? 0 : -1/, `${label} must use roving tabindex`);
    assert.match(src, /target\.focus\(\{preventScroll: true\}\)/, `${label} must restore focus after repaint`);
  }
});

test("designer writeDirectLocal enforces 500KB limit, poll syncs background mode, and workshop generateDraft clears rightRaw", () => {
  const designerJs = read("pages/新的她/app.js");
  const workshopJs = read("pages/人物设定工坊/app.js");

  // 1. writeDirectLocal enforces MAX_LOCAL_DRAFT_BYTES = 500000
  const start = designerJs.indexOf("  const MAX_LOCAL_DRAFT_BYTES = 500000;");
  const end = designerJs.indexOf("  function unwrap(", start);
  assert.ok(start > 0 && end > start);
  const store = new Map();
  const ctx = vm.createContext({
    LOCAL_PREFIX: "companion.persona-designer.v1.",
    localStorage: {
      getItem: k => store.has(k) ? store.get(k) : null,
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: k => store.delete(k),
    },
  });
  vm.runInContext(designerJs.slice(start, end), ctx);
  ctx.writeDirectLocal("create", {draft: "small"});
  assert.ok(store.has("companion.persona-designer.v1.create"));
  ctx.writeDirectLocal("optimize", {draft: "x".repeat(500001)});
  assert.ok(!store.has("companion.persona-designer.v1.optimize"), "payloads over 500KB must not be written");

  // 2. poll() calls persist(mode) and persist syncs both active and completed background mode
  assert.match(designerJs, /function persist\(syncMode = null\)/);
  assert.match(designerJs, /await persist\(mode\);/);

  // 3. workshop generateDraft clears rightRaw on draft.sections and preserves extractCanonical style/examples
  const genStart = workshopJs.indexOf("  async function generateDraft()");
  const genEnd = workshopJs.indexOf("  async function generateTryouts()", genStart);
  const genBlock = workshopJs.slice(genStart, genEnd);
  assert.match(genBlock, /if \(draft\.sections\) \{[\s\S]*rightStyle = ""; rightExamples = ""; rightRaw = "";[\s\S]*\} else \{/);
  assert.doesNotMatch(genBlock, /\}\s*\n\s*rightStyle = ""; rightExamples = "";/);
});
