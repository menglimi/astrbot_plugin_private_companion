// 陪伴小屋 7.2.7 核心工作台布局、样式级联与排版回归护栏。
//
// 锁定以下几类关键契约，防止静默退回：
//   1. CSS 语法与转义卫生：零字面量 \n/\r/\t、零残留 */、零非法 -var(...) 写法；
//   2. 样式表级联顺序：index.html + performance-gate.js 保证 bundle.css 始终早于
//      ui-field-align.css 进入级联、原位插入不反超后续静态链接、性能模式跳过完整样式；
//   3. 容器驱动的两行工作台架构：workbench.css 用 @container（wb-card / wb-group / wb-field）
//      实现「行 1 标题 ↔ 控件、行 2 通栏说明」、空 .wb-field-side 折叠、完整长控件通栏列表，
//      且 ui-field-align.css 不再包含 12em/320px/700px 强制覆盖；
//   4. 专项页面排版修复：
//      - 首页生活观察（home.css）：去除 62ch 截断、顶对齐、.home-empty 不拉伸按钮；
//      - 决策与防骚扰（decision-page.css）：单组/尾组通栏、.guard-signal-params 零撑破重叠、
//        .guard-directory 横向滚动、.decision-tabs 自动换行；
//      - 理解页（understanding-page.css）：media_permissions 两列成对布局、web 页签屏蔽共享工作台、
//        指标卡按实际数量分列无 3+1 孤儿；
//      - 表达页与角色策略（expression-config.css / text-page.css / voice-page.css / learning-policy.css / self-page.css）：
//        容器驱动两列卡片、零重复类名 hack、#pokePolicyWorkspace 不倒挂、checks 页签始终可达、
//        无 1 行说明硬截断与未定义色板变量。
//
// 用 node --test 运行。
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const PAGE = path.join(ROOT, "pages", "陪伴小屋");
const read = (name) => fs.readFileSync(path.join(PAGE, name), "utf8");

const OWNED_CSS_FILES = [
  "ui-field-align.css",
  "workbench.css",
  "home.css",
  "decision-page.css",
  "text-page.css",
  "voice-page.css",
  "learning-policy.css",
  "understanding-page.css",
  "expression-config.css",
  "self-page.css",
];

function stripComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, "");
}

function scanCssSyntaxIssues(source, label, { checkNegVar = false } = {}) {
  const issues = [];
  const lines = source.split(/\r?\n/);
  let i = 0;
  let line = 1;
  let inString = null;

  while (i < source.length) {
    const ch = source[i];
    const next = source[i + 1] || "";

    if (inString) {
      if (ch === "\\") {
        i += 2;
        continue;
      }
      if (ch === inString) {
        inString = null;
      } else if (ch === "\n") {
        line += 1;
      }
      i += 1;
      continue;
    }

    if (ch === "/" && next === "*") {
      const end = source.indexOf("*/", i + 2);
      if (end < 0) {
        issues.push(`${label}:${line}: unterminated comment`);
        break;
      }
      for (let k = i; k < end + 2; k += 1) {
        if (source[k] === "\n") line += 1;
      }
      i = end + 2;
      continue;
    }

    if (ch === "*" && next === "/") {
      issues.push(`${label}:${line}: stray comment close */ (${lines[line - 1].trim()})`);
      i += 2;
      continue;
    }

    if (ch === "\"" || ch === "'") {
      inString = ch;
      i += 1;
      continue;
    }

    if (ch === "\\" && (next === "n" || next === "r" || next === "t")) {
      issues.push(`${label}:${line}: literal \\${next} outside string (${lines[line - 1].trim()})`);
      i += 2;
      continue;
    }

    if (checkNegVar && ch === "-" && source.slice(i, i + 5) === "-var(") {
      const prev = source[i - 1] || "";
      if (!/[A-Za-z0-9_-]/.test(prev)) {
        issues.push(`${label}:${line}: invalid -var(...) syntax (${lines[line - 1].trim()})`);
      }
    }

    if (ch === "\n") line += 1;
    i += 1;
  }

  return issues;
}

/* 解析规则表，保留 @media/@container 上下文，便于按断点和选择器精准断言。 */
function parseCssRules(source) {
  const clean = stripComments(source);
  const rules = [];
  let pos = 0;

  function readBlock(contexts) {
    let prelude = "";
    while (pos < clean.length) {
      const ch = clean[pos];
      if (ch === "{") {
        const header = prelude.replace(/\s+/g, " ").trim();
        prelude = "";
        pos += 1;
        if (/^@(?:media|container|supports)\b/i.test(header)) {
          readBlock([...contexts, header]);
        } else if (header.startsWith("@")) {
          let depth = 1;
          while (pos < clean.length && depth > 0) {
            if (clean[pos] === "{") depth += 1;
            else if (clean[pos] === "}") depth -= 1;
            pos += 1;
          }
        } else {
          let depth = 1;
          let body = "";
          while (pos < clean.length && depth > 0) {
            if (clean[pos] === "{") depth += 1;
            else if (clean[pos] === "}") {
              depth -= 1;
              if (depth === 0) {
                pos += 1;
                break;
              }
            }
            body += clean[pos];
            pos += 1;
          }
          const normalizedBody = body.replace(/\s+/g, " ").trim();
          for (const sel of splitSelectorList(header)) {
            if (sel) rules.push({ contexts, selector: sel, body: normalizedBody });
          }
        }
        continue;
      }
      if (ch === "}") {
        pos += 1;
        return;
      }
      if (ch === ";") {
        prelude = "";
        pos += 1;
        continue;
      }
      prelude += ch;
      pos += 1;
    }
  }

  readBlock([]);
  return rules;
}

function splitSelectorList(input) {
  const parts = [];
  let depth = 0;
  let quote = null;
  let buf = "";
  for (let i = 0; i < input.length; i += 1) {
    const ch = input[i];
    if (quote) {
      buf += ch;
      if (ch === "\\") {
        buf += input[++i] || "";
      } else if (ch === quote) {
        quote = null;
      }
      continue;
    }
    if (ch === "\"" || ch === "'") {
      quote = ch;
      buf += ch;
      continue;
    }
    if (ch === "(" || ch === "[") depth += 1;
    else if (ch === ")" || ch === "]") depth = Math.max(0, depth - 1);
    else if (ch === "," && depth === 0) {
      parts.push(buf.trim());
      buf = "";
      continue;
    }
    buf += ch;
  }
  if (buf.trim()) parts.push(buf.trim());
  return parts;
}

function compareSpec(a, b) {
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return 0;
}

/* W3C Selectors Level 4 优先级计算：(id, class/attr/pseudo-class, type/pseudo-element)。 */
function specificity(selector) {
  let a = 0;
  let b = 0;
  let c = 0;
  let i = 0;
  const s = selector.trim();

  function readParen(start) {
    let depth = 1;
    let j = start;
    let quote = null;
    while (j < s.length && depth > 0) {
      const ch = s[j];
      if (quote) {
        if (ch === "\\") j += 1;
        else if (ch === quote) quote = null;
      } else if (ch === "\"" || ch === "'") {
        quote = ch;
      } else if (ch === "(") {
        depth += 1;
      } else if (ch === ")") {
        depth -= 1;
        if (depth === 0) return [s.slice(start, j), j + 1];
      }
      j += 1;
    }
    return [s.slice(start), s.length];
  }

  while (i < s.length) {
    const ch = s[i];
    if (/\s|[>+~,*]/.test(ch)) {
      i += 1;
      continue;
    }
    if (ch === "#") {
      const m = /^#[A-Za-z0-9_-]+/.exec(s.slice(i));
      a += 1;
      i += m ? m[0].length : 1;
      continue;
    }
    if (ch === ".") {
      const m = /^\.[A-Za-z0-9_-]+/.exec(s.slice(i));
      b += 1;
      i += m ? m[0].length : 1;
      continue;
    }
    if (ch === "[") {
      const end = s.indexOf("]", i + 1);
      b += 1;
      i = end >= 0 ? end + 1 : s.length;
      continue;
    }
    if (ch === ":") {
      if (s[i + 1] === ":") {
        const m = /^::[A-Za-z-]+/.exec(s.slice(i));
        c += 1;
        i += m ? m[0].length : 2;
        if (s[i] === "(") [, i] = readParen(i + 1);
        continue;
      }
      const m = /^:([A-Za-z-]+)/.exec(s.slice(i));
      if (!m) {
        i += 1;
        continue;
      }
      const name = m[1].toLowerCase();
      i += m[0].length;
      if (s[i] === "(") {
        const [arg, nextPos] = readParen(i + 1);
        i = nextPos;
        if (name === "where") continue;
        if (name === "is" || name === "not" || name === "has") {
          let best = [0, 0, 0];
          for (const part of splitSelectorList(arg)) {
            const sub = specificity(part.replace(/^[>+~]\s*/, ""));
            if (compareSpec(sub, best) > 0) best = sub;
          }
          a += best[0];
          b += best[1];
          c += best[2];
          continue;
        }
        b += 1;
        continue;
      }
      b += 1;
      continue;
    }
    const typeMatch = /^[A-Za-z][A-Za-z0-9-]*/.exec(s.slice(i));
    if (typeMatch) {
      c += 1;
      i += typeMatch[0].length;
      continue;
    }
    i += 1;
  }

  return [a, b, c];
}

test("全站样式表无字面量 \\n 或残留 */，本 PR 负责样式表无非法 -var() 且关键选择器正常解析", () => {
  const cssFiles = fs.readdirSync(PAGE).filter((name) => name.endsWith(".css"));
  const issues = [];
  for (const name of cssFiles) {
    issues.push(...scanCssSyntaxIssues(
      read(name),
      `pages/陪伴小屋/${name}`,
      { checkNegVar: OWNED_CSS_FILES.includes(name) },
    ));
  }
  assert.deepStrictEqual(issues, [], issues.join("\n"));

  for (const file of ["text-page.css", "bundle.css"]) {
    const rules = parseCssRules(read(file));
    const quoteTag = rules.find((r) => r.selector === ".qq-quote-tag");
    assert.ok(quoteTag, `${file} 缺少合法顶层选择器 .qq-quote-tag`);
    assert.match(quoteTag.body, /display:\s*inline-block/);
    assert.match(quoteTag.body, /color:\s*var\(--t-accent-ink\)/);
  }

  for (const file of ["workbench.css", "bundle.css"]) {
    const rules = parseCssRules(read(file));
    for (const sel of [
      ".wb-field:has(.wb-quotes)",
      ".wb-field:has(.wb-tagset)",
      ".wb-field:has(.wb-date)",
      ".wb-field:has(.wb-replacement-editor)",
    ]) {
      const hit = rules.find((r) => r.selector === sel && /grid-column:\s*1\s*\/\s*-1/.test(r.body));
      assert.ok(hit, `${file} 的 ${sel} 通栏规则未正常解析`);
    }
  }
});

test("index.html 与 performance-gate.js 保证 bundle.css 始终在 ui-field-align.css 之前进入级联", async () => {
  const indexHtml = read("index.html");
  const start = indexHtml.indexOf('<template id="companion-full-assets">');
  const end = indexHtml.indexOf("</template>", start);
  assert.ok(start > 0 && end > start, "缺少 companion-full-assets 模板");

  const templateHtml = indexHtml.slice(start, end);
  const templateLinks = [...templateHtml.matchAll(/<link\s+rel="stylesheet"\s+href="([^"?]+)[^"]*"/g)].map((m) => m[1]);
  assert.ok(templateLinks.includes("bundle.css"), "模板内缺少 bundle.css");
  assert.ok(templateLinks.includes("ui-field-align.css"), "模板内缺少 ui-field-align.css");
  assert.ok(
    templateLinks.indexOf("bundle.css") < templateLinks.indexOf("ui-field-align.css"),
    `模板内 bundle.css 必须早于 ui-field-align.css，实际顺序：${templateLinks.join(" -> ")}`,
  );

  const outsideTemplate = indexHtml.slice(0, start) + indexHtml.slice(end);
  assert.doesNotMatch(outsideTemplate, /href="ui-field-align\.css/, "ui-field-align.css 不应留在 template 之外");

  const gateSource = read("performance-gate.js");

  function makeNode(tagName, attrs = {}) {
    const listeners = new Map();
    const attrMap = new Map(Object.entries(attrs));
    const node = {
      nodeType: 1,
      tagName,
      parentNode: null,
      async: true,
      defer: true,
      getAttribute(k) { return attrMap.has(k) ? attrMap.get(k) : null; },
      setAttribute(k, v) { attrMap.set(k, String(v)); },
      addEventListener(type, fn) {
        if (!listeners.has(type)) listeners.set(type, []);
        listeners.get(type).push(fn);
      },
      dispatch(type) {
        for (const fn of (listeners.get(type) || []).splice(0)) fn();
      },
      cloneNode() {
        return makeNode(tagName, Object.fromEntries(attrMap.entries()));
      },
    };
    return node;
  }

  const headChildren = [];
  const head = {
    childNodes: headChildren,
    appendChild(node) {
      node.parentNode = head;
      headChildren.push(node);
      return node;
    },
    insertBefore(node, ref) {
      node.parentNode = head;
      const idx = ref ? headChildren.indexOf(ref) : -1;
      if (idx >= 0) headChildren.splice(idx, 0, node);
      else headChildren.push(node);
      return node;
    },
  };

  const templateNodes = templateLinks.map((href) => makeNode("LINK", { rel: "stylesheet", href }));
  const templateEl = makeNode("TEMPLATE", { id: "companion-full-assets" });
  templateEl.content = { childNodes: templateNodes };
  const trailingStaticLink = makeNode("LINK", { rel: "stylesheet", href: "trailing-override.css" });
  head.appendChild(templateEl);
  head.appendChild(trailingStaticLink);

  const rootAttrs = new Set(["data-companion-booting"]);
  const rootEl = {
    dataset: {},
    removeAttribute(name) { rootAttrs.delete(name); },
    hasAttribute(name) { return rootAttrs.has(name); },
  };

  const timers = [];
  const sandbox = {
    document: {
      documentElement: rootEl,
      head,
      body: { replaceChildren() {}, className: "" },
      getElementById(id) { return id === "companion-full-assets" ? templateEl : null; },
      createElement(tag) { return makeNode(tag.toUpperCase()); },
    },
    window: {
      AstrBotPluginPage: {
        ready: async () => ({}),
        apiGet: async (endpoint) => {
          assert.equal(endpoint, "host/performance");
          return { status: "ok", data: { enabled: false } };
        },
      },
      location: { reload() {} },
    },
    setTimeout(fn, ms) {
      const id = timers.length + 1;
      timers.push({ id, fn, ms, cleared: false });
      return id;
    },
    clearTimeout(id) {
      const item = timers.find((t) => t.id === id);
      if (item) item.cleared = true;
    },
  };

  vm.runInNewContext(gateSource, sandbox);
  await new Promise((resolve) => setImmediate(resolve));

  const sheetOrder = headChildren
    .filter((n) => n.tagName === "LINK" && n.getAttribute("rel") === "stylesheet")
    .map((n) => n.getAttribute("href"));

  assert.deepStrictEqual(sheetOrder, [...templateLinks, "trailing-override.css"]);
  assert.ok(sheetOrder.indexOf("bundle.css") < sheetOrder.indexOf("ui-field-align.css"));

  const activatedLinks = headChildren.filter((n) => n.tagName === "LINK" && n !== trailingStaticLink);
  assert.equal(rootEl.hasAttribute("data-companion-booting"), true);
  for (const link of activatedLinks) link.dispatch("load");
  for (const t of timers.filter((item) => !item.cleared && item.ms === 0)) t.fn();
  assert.equal(rootEl.hasAttribute("data-companion-booting"), false);

  const liteHeadChildren = [];
  const liteHead = {
    childNodes: liteHeadChildren,
    appendChild(node) { liteHeadChildren.push(node); return node; },
    insertBefore(node) { liteHeadChildren.push(node); return node; },
  };
  const liteRootAttrs = new Set(["data-companion-booting"]);
  const liteRoot = {
    dataset: {},
    removeAttribute(name) { liteRootAttrs.delete(name); },
    hasAttribute(name) { return liteRootAttrs.has(name); },
  };
  const liteSandbox = {
    document: {
      title: "",
      documentElement: liteRoot,
      head: liteHead,
      body: { replaceChildren() {}, className: "" },
      getElementById(id) { return id === "companion-full-assets" ? templateEl : null; },
      createElement(tag) {
        const el = makeNode(tag.toUpperCase());
        el.appendChild = () => el;
        return el;
      },
    },
    window: {
      AstrBotPluginPage: {
        ready: async () => ({}),
        apiGet: async () => ({ status: "ok", data: { enabled: true } }),
      },
      location: { reload() {} },
    },
    setTimeout() { return 1; },
    clearTimeout() {},
  };
  vm.runInNewContext(gateSource, liteSandbox);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(liteHeadChildren.length, 0, "性能模式开启时不应加载任何完整样式表");
  assert.equal(liteRoot.dataset.companionPerformance, "1");
});

test("核心工作台采用容器驱动两行结构，ui-field-align.css 不再覆盖 .wb-field-main/.wb-control", () => {
  const alignSrc = stripComments(read("ui-field-align.css"));
  assert.doesNotMatch(alignSrc, /minmax\(\s*9em\s*,\s*12em\s*\)/, "ui-field-align.css 不得恢复 12em 窄列挤压");
  assert.doesNotMatch(alignSrc, /minmax\(\s*min\(\s*700px\s*,\s*100%\s*\)/, "ui-field-align.css 不得恢复 700px 单列锁死");
  assert.doesNotMatch(alignSrc, /max-width:\s*320px/, "ui-field-align.css 不得全局将控件压死在 320px");
  assert.doesNotMatch(alignSrc, /\.wb-field-main\b/, "ui-field-align.css 不应再覆盖 .wb-field-main");
  assert.doesNotMatch(alignSrc, /\.wb-control\b/, "ui-field-align.css 不应再覆盖 .wb-control");

  const wbRules = parseCssRules(read("workbench.css"));
  const wbCard = wbRules.find((r) => r.selector === ".wb-card" && /container:\s*wb-card\s*\/\s*inline-size/.test(r.body));
  const wbGroup = wbRules.find((r) => r.selector === ".wb-group" && /container:\s*wb-group\s*\/\s*inline-size/.test(r.body));
  const wbField = wbRules.find((r) => r.selector === ".wb-field" && /container:\s*wb-field\s*\/\s*inline-size/.test(r.body));
  assert.ok(wbCard && wbGroup && wbField, "workbench.css 应在 .wb-card/.wb-group/.wb-field 声明 inline-size 容器");

  const wbHelp = wbRules.find((r) => r.contexts.length === 0 && r.selector === ".wb-field-main>.wb-help");
  assert.ok(wbHelp, "workbench.css 缺少 .wb-field-main>.wb-help 规则");
  assert.match(wbHelp.body, /grid-column:\s*1\s*\/\s*-1/, ".wb-help 必须跨满整行，不再挤在左侧窄列");
  assert.match(wbHelp.body, /max-width:\s*none/, ".wb-help 不应被 62ch 截断");

  const wbCtrl = wbRules.find((r) => r.contexts.length === 0 && r.selector === ".wb-field-main>.wb-control");
  assert.ok(wbCtrl && /grid-column:\s*2\b/.test(wbCtrl.body) && /grid-row:\s*1\b/.test(wbCtrl.body) && /justify-self:\s*end/.test(wbCtrl.body));

  const emptySide = wbRules.find((r) => r.selector === ".wb-field>.wb-field-side:empty" && /display:\s*none/.test(r.body));
  assert.ok(emptySide, "空 .wb-field-side 必须折叠，不浪费右侧 64px 槽位");

  /* 全部 10 类复杂控件必须走全宽上下结构，且以 :where() 保持低优先级不抢占页面专有样式。 */
  const complexMain = wbRules.find(
    (r) => r.contexts.length === 0
      && r.selector.startsWith(":where(.wb-field:has(")
      && r.selector.endsWith(">.wb-field-main")
      && /grid-template-columns:\s*minmax\(0,\s*1fr\)/.test(r.body),
  );
  assert.ok(complexMain, "workbench.css 缺少低优先级复杂控件通栏规则");
  for (const ctl of [
    "textarea", ".wb-word-editor", ".wb-keywords", ".wb-replacement-editor",
    ".wb-source-picker", ".wb-order-editor", ".wb-date", ".wb-tagset", ".wb-quotes", ".wb-chipset",
  ]) {
    assert.ok(complexMain.selector.includes(ctl), `复杂控件 ${ctl} 未纳入全宽上下排布列表`);
  }
  assert.ok(compareSpec(specificity(complexMain.selector), [0, 2, 0]) <= 0,
    "复杂控件默认规则优先级应 <= (0,2,0)，避免压坏页面自定义排版");

  const narrowSwitch = wbRules.find(
    (r) => r.contexts.some((c) => /@container\s+wb-field\s+\(max-width:\s*499px\)/.test(c))
      && r.selector.includes(".wb-switch")
      && r.selector.endsWith(">.wb-field-main"),
  );
  const narrowOther = wbRules.find(
    (r) => r.contexts.some((c) => /@container\s+wb-field\s+\(max-width:\s*499px\)/.test(c))
      && r.selector.includes(":not(:has(.wb-switch))")
      && r.selector.endsWith(">.wb-field-main")
      && /grid-template-columns:\s*minmax\(0,\s*1fr\)/.test(r.body),
  );
  assert.ok(narrowSwitch && narrowOther, "workbench.css 应在 @container wb-field (max-width:499px) 中区分开关与输入控件");

  /* 窄容器与移动端 .wb-field>.wb-field-side 折行规则必须排在桌面 grid-column:3 之后，
     且优先级 >= (0,2,0)，避免恢复继承按钮落入溢出隐式第 3 列。 */
  const desktopSideIdx = wbRules.findIndex(
    (r) => r.contexts.length === 0
      && r.selector === ".wb-field>.wb-field-side"
      && /grid-column:\s*3\b/.test(r.body),
  );
  const desktopNoRailSideIdx = wbRules.findIndex(
    (r) => r.contexts.length === 0
      && r.selector === ".wb-field:where(:not(:has(>.wb-rail)))>.wb-field-side"
      && /grid-column:\s*2\b/.test(r.body),
  );
  assert.ok(desktopSideIdx >= 0 && desktopNoRailSideIdx >= 0, "缺少桌面端 .wb-field-side 列定位规则");
  const desktopSideSpec = specificity(wbRules[desktopSideIdx].selector);

  for (const [label, ctxPattern] of [
    ["@container wb-field (max-width:499px)", /@container\s+wb-field\s+\(max-width:\s*499px\)/],
    ["@container wb-group (max-width:520px)", /@container\s+wb-group\s+\(max-width:\s*520px\)/],
    ["@media (max-width:760px)", /@media\s*\(max-width:\s*760px\)/],
  ]) {
    const sideIdx = wbRules.findIndex(
      (r) => r.contexts.some((c) => ctxPattern.test(c))
        && r.selector === ".wb-field>.wb-field-side"
        && /grid-column:\s*2\b/.test(r.body),
    );
    const noRailIdx = wbRules.findIndex(
      (r) => r.contexts.some((c) => ctxPattern.test(c))
        && r.selector === ".wb-field:where(:not(:has(>.wb-rail)))>.wb-field-side"
        && /grid-column:\s*1\b/.test(r.body),
    );
    assert.ok(sideIdx > desktopSideIdx, `${label} 的 .wb-field>.wb-field-side{grid-column:2} 必须位于桌面 grid-column:3 之后`);
    assert.ok(compareSpec(specificity(wbRules[sideIdx].selector), desktopSideSpec) >= 0);
    assert.ok(noRailIdx > desktopNoRailSideIdx, `${label} 的无层轨 .wb-field-side{grid-column:1} 必须位于桌面 grid-column:2 之后`);
  }
  const shadowedBeforeDesktop = wbRules.slice(0, desktopSideIdx).filter(
    (r) => r.selector === ".wb-field>.wb-field-side" && /grid-column:\s*2\b/.test(r.body),
  );
  assert.deepStrictEqual(shadowedBeforeDesktop, [], "不应在桌面 grid-column:3 之前残留被遮蔽的 .wb-field>.wb-field-side 规则");
});

test("Issue #3：首页生活观察正文不限 62ch、行顶对齐且空态按钮不拉满整宽", () => {
  const homeRules = parseCssRules(read("home.css"));
  const homeRow = homeRules.find((r) => r.contexts.length === 0 && r.selector === ".home-row");
  assert.ok(homeRow && /align-items:\s*flex-start/.test(homeRow.body), ".home-row 必须顶对齐");

  const rowSmall = homeRules.find((r) => r.contexts.length === 0 && r.selector === ".home-row-main small");
  assert.ok(rowSmall, "home.css 缺少 .home-row-main small");
  assert.match(rowSmall.body, /max-width:\s*none/);
  assert.doesNotMatch(rowSmall.body, /max-width:\s*62ch/);
  assert.match(rowSmall.body, /overflow-wrap:\s*anywhere/);

  const homeEmpty = homeRules.find((r) => r.contexts.length === 0 && r.selector === ".home-empty");
  assert.ok(homeEmpty && /align-items:\s*flex-start/.test(homeEmpty.body), ".home-empty 必须 align-items:flex-start");
});

test("Issue #6 & #7：决策卡片无半宽空洞，防骚扰阈值卡输入框零撑破零重叠", () => {
  const decSrc = stripComments(read("decision-page.css"));
  const decRules = parseCssRules(decSrc);

  const tabs = decRules.find((r) => r.contexts.length === 0 && r.selector === ".decision-tabs");
  assert.ok(tabs && /flex-wrap:\s*wrap/.test(tabs.body), ".decision-tabs 桌面端应允许换行");

  const oddGroup = decRules.find(
    (r) => r.selector.includes(".decision-editors")
      && r.selector.includes(":only-of-type")
      && /grid-column:\s*1\s*\/\s*-1/.test(r.body),
  );
  assert.ok(oddGroup, "决策页单一分组卡片（如玩笑护栏）应通栏，避免右侧 50% 空洞");

  const guardSignals = decRules.find((r) => r.selector === ".guard-signals");
  assert.ok(guardSignals && /repeat\(auto-fill,\s*minmax\(min\(250px,\s*100%\),\s*1fr\)\)/.test(guardSignals.body));

  const guardSignal = decRules.find((r) => r.selector === ".guard-signal");
  assert.ok(guardSignal && /min-width:\s*0/.test(guardSignal.body) && /overflow:\s*hidden/.test(guardSignal.body),
    ".guard-signal 必须声明 min-width:0 与 overflow:hidden");

  const params = decRules.find((r) => r.selector === ".guard-signal-params");
  assert.ok(params && /grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/.test(params.body),
    ".guard-signal-params 必须使用 repeat(2,minmax(0,1fr))，禁止裸 1fr 1fr");

  const paramLabel = decRules.find((r) => r.selector === ".guard-signal-params label");
  assert.ok(paramLabel && /min-width:\s*0/.test(paramLabel.body));

  const paramInput = decRules.find((r) => r.selector === ".guard-signal-params input");
  assert.ok(
    paramInput
      && /box-sizing:\s*border-box/.test(paramInput.body)
      && /width:\s*100%/.test(paramInput.body)
      && /min-width:\s*0/.test(paramInput.body),
    ".guard-signal-params input 必须具备 box-sizing:border-box、width:100% 与 min-width:0",
  );

  const guardDir = decRules.find((r) => r.selector === ".guard-directory");
  assert.ok(guardDir && /overflow-x:\s*auto/.test(guardDir.body) && !/overflow:\s*hidden/.test(guardDir.body),
    ".guard-directory 必须支持横向滚动而非裁切表格");

  const guardPanelCount = decRules.filter((r) => r.selector === ".guard-panel").length;
  assert.equal(guardPanelCount, 1, "decision-page.css 不应保留重复死规则块 .guard-panel");
  assert.ok(decRules.some((r) => r.selector === ".decision-empty"), "decision-page.css 应定义 .decision-empty");
});

test("Issue #10 与表达/角色策略页：容器驱动两列卡片、零重复类名 hack、平台检查可达", () => {
  const exprSrc = stripComments(read("expression-config.css"));
  const exprRules = parseCssRules(exprSrc);
  const textSrc = stripComments(read("text-page.css"));
  const textRules = parseCssRules(textSrc);
  const voiceRules = parseCssRules(read("voice-page.css"));
  const policyRules = parseCssRules(read("learning-policy.css"));

  assert.doesNotMatch(exprSrc, /\.wb-field-main\.wb-field-main|\.wb-control\.wb-control/,
    "expression-config.css 不应保留重复类名优先级 hack");
  assert.doesNotMatch(textSrc, /\.wb-field-main\.wb-field-main|\.wb-control\.wb-control/,
    "text-page.css 不应保留重复类名优先级 hack");

  /* #reactionConfigWorkspace 两列卡片由 @container expression-modules (min-width:720px) 触发，覆盖 1280/1440px。 */
  const reactionGrid = exprRules.find(
    (r) => r.contexts.some((c) => /@container\s+expression-modules\s+\(min-width:\s*720px\)/.test(c))
      && r.selector === ".expression-reaction-workspace>#reactionConfigWorkspace .wb-fields"
      && /repeat\(2,\s*minmax\(0,\s*1fr\)\)/.test(r.body),
  );
  assert.ok(reactionGrid, "#reactionConfigWorkspace 应在 @container expression-modules (min-width:720px) 启用两列卡片");

  /* #pokePolicyWorkspace 在单列折行时必须同时复位 grid-row:auto，不得倒挂到主设置上方。 */
  const pokeResetContainer = exprRules.find(
    (r) => r.contexts.some((c) => /@container\s+expression-modules\s+\(max-width:\s*1080px\)/.test(c))
      && r.selector === ".expression-platform-workspace>#pokePolicyWorkspace"
      && /grid-row:\s*auto/.test(r.body),
  );
  const pokeResetMedia = exprRules.find(
    (r) => r.contexts.some((c) => /max-width:\s*1100px/.test(c))
      && r.selector === ".expression-platform-workspace>#pokePolicyWorkspace"
      && /grid-row:\s*auto/.test(r.body),
  );
  assert.ok(pokeResetContainer && pokeResetMedia, "#pokePolicyWorkspace 折行时必须显式复位 grid-row:auto");

  /* 平台页 <=760px 容器不得隐藏侧栏导致「发送前检查」不可达。 */
  const hiddenPlatformSidebar = exprRules.find(
    (r) => r.contexts.some((c) => /max-width:\s*760px/.test(c))
      && r.selector === "#replyWorkspace.expression-platform-focus .wb-section-sidebar"
      && /display:\s*none/.test(r.body),
  );
  assert.equal(hiddenPlatformSidebar, undefined, "平台页窄容器不应隐藏 .wb-section-sidebar");

  /* 语音页不再把 .wb-help 硬截断为 1 行，且文字/语音页 .wb-field-side 不落第 3 隐式列。 */
  assert.ok(
    !voiceRules.some((r) => r.selector.includes(".wb-help") && /-webkit-line-clamp:\s*1\b/.test(r.body)),
    "voice-page.css 不应将 .wb-help 硬截断为 1 行",
  );
  assert.ok(
    voiceRules.some((r) => r.selector === "#replyWorkspace.expression-voice-focus .wb-field>.wb-field-side" && /grid-column:\s*2/.test(r.body)),
  );
  assert.ok(
    textRules.some((r) => r.selector === "#replyWorkspace.expression-text-focus .wb-field>.wb-field-side" && /grid-column:\s*2/.test(r.body)),
  );

  const textDesktopFields = textRules.filter(
    (r) => r.contexts.length === 0 && r.selector.endsWith(".wb-fields") && /grid-template-columns/.test(r.body),
  );
  const textMobileFields = textRules.find(
    (r) => r.contexts.some((c) => /max-width:\s*900px/.test(c))
      && r.selector.endsWith(".wb-fields")
      && /grid-template-columns:\s*minmax\(0,\s*1fr\)/.test(r.body),
  );
  assert.ok(textMobileFields, "text-page.css 缺少 <=900px .wb-fields 单列断点");
  for (const desk of textDesktopFields) {
    assert.ok(compareSpec(specificity(textMobileFields.selector), specificity(desk.selector)) >= 0);
  }

  /* 角色策略页采用容器驱动两列布局，不再锁死 320px 列或 <=1280px 360px 留白。 */
  const policyTwoCol = policyRules.find(
    (r) => r.contexts.some((c) => /@container\s+learning-policy\s+\(min-width:\s*720px\)/.test(c))
      && r.selector === ".learning-policy-workspaces .wb-fields"
      && /repeat\(2,\s*minmax\(0,\s*1fr\)\)/.test(r.body),
  );
  assert.ok(policyTwoCol, "learning-policy.css 应在 @container learning-policy (min-width:720px) 启用两列");
  assert.doesNotMatch(stripComments(read("learning-policy.css")), /max-width:\s*360px|320px/);
});

test("Issue #12：理解页 media_permissions 两列成对、屏蔽 web 页签泄漏、消除 3+1 孤儿指标卡", () => {
  const rules = parseCssRules(read("understanding-page.css"));

  const pairsGrid = rules.find(
    (r) => r.contexts.length === 0
      && r.selector === '.understanding-permissions [data-wb-group="media_permissions"] .wb-fields'
      && /grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/.test(r.body),
  );
  assert.ok(pairsGrid, "media_permissions 应默认使用两列成对网格（私聊 | 群聊）");

  const pairsStack = rules.find(
    (r) => r.contexts.some((c) => /@container\s+understanding-page\s+\(max-width:\s*600px\)/.test(c))
      && r.selector === '.understanding-permissions [data-wb-group="media_permissions"] .wb-fields'
      && /grid-template-columns:\s*minmax\(0,\s*1fr\)/.test(r.body),
  );
  assert.ok(pairsStack, "media_permissions 应在窄容器回落单列");

  const webHide = rules.find(
    (r) => r.selector === ".understanding-panel[data-understanding-panel=web] #contextImageWorkspace"
      && /display:\s*none/.test(r.body),
  );
  assert.ok(webHide, "网页理解页签必须隐藏共享媒体工作台，防止泄漏总览 6 项许可开关");

  const threeColStats = rules.find(
    (r) => r.contexts.length === 0
      && r.selector.includes(".understanding-stats")
      && /repeat\(3,\s*minmax\(0,\s*1fr\)\)/.test(r.body),
  );
  assert.ok(
    threeColStats
      && threeColStats.selector.includes("forwards")
      && !threeColStats.selector.includes("video")
      && !threeColStats.selector.includes(":not([data-understanding-panel=overview])"),
    "仅 3 项指标的 forwards 使用 3 列，overview/image/video/file/web（均为 4 卡）保持 4 列无 3+1 孤儿卡",
  );
});

test("清理项：.text-hero 无失效 grid 属性，self-page.css 无重复 key_lore::before 或未定义变量", () => {
  const textRules = parseCssRules(read("text-page.css"));
  const heroRules = textRules.filter((r) => r.selector === ".text-hero");
  assert.ok(heroRules.length >= 1);
  for (const r of heroRules) {
    assert.doesNotMatch(r.body, /\bgap\s*:/, ".text-hero 是 display:block，不应残留 gap");
    assert.doesNotMatch(r.body, /grid-template-columns\s*:/, ".text-hero 是 display:block，不应残留 grid-template-columns");
  }

  const selfSrc = stripComments(read("self-page.css"));
  const selfRules = parseCssRules(selfSrc);
  const keyLoreBefore = selfRules.filter(
    (r) => r.selector === '.self-page .wb-journal .wb-group[data-wb-group="identity"] .wb-field[data-wb-row="key_lore"]::before',
  );
  assert.equal(keyLoreBefore.length, 1, "self-page.css 的 key_lore::before 应只有一条完整定义");
  assert.match(keyLoreBefore[0].body, /position:\s*static/);
  assert.doesNotMatch(selfSrc, /var\(--t-muted\)|var\(--t-ink\)/, "self-page.css 不应引用未定义的 --t-muted / --t-ink");
});

test("视觉打磨回归：语音保存条跨列、搜索弹窗按钮/底栏、表达去重标题、自我页芯片换行与决策 4 卡无 3+1", () => {
  const voiceRules = parseCssRules(read("voice-page.css"));
  const voiceActions = voiceRules.find(
    (r) => r.contexts.length === 0 && r.selector === "#replyWorkspace.expression-voice-focus .wb-actions",
  );
  assert.ok(voiceActions, "voice-page.css 应定义 #replyWorkspace.expression-voice-focus .wb-actions");
  assert.match(voiceActions.body, /grid-column:\s*1\s*\/\s*-1/, "语音页底部保存条必须跨满两列，不能漂入第 2 列");

  const indexHtml = read("index.html");
  assert.match(
    indexHtml,
    /data-clear-search[^>]*>清空<\/button>/,
    "搜索清除按钮应使用明确「清空」文本，避免与关闭按钮呈现两个相同 × 图标",
  );
  assert.match(
    indexHtml,
    /<div class="search-hint"><span>↑ ↓ 选择 · Enter 打开<\/span><span[^>]*><kbd>Esc<\/kbd>\s*关闭<\/span><\/div>/,
    "搜索底部提示应把 <kbd>Esc</kbd> 关闭包裹在同一 span 中，避免 space-between 拆散",
  );

  const exprRules = parseCssRules(read("expression-config.css"));
  const dupHeadHide = exprRules.find(
    (r) => r.contexts.length === 0
      && r.selector === ".expression-module-panel>.console-section>.console-section-head"
      && /display:\s*none/.test(r.body),
  );
  assert.ok(dupHeadHide, "表达页应隐藏面板内重复的 .console-section-head，由顶部 h1 统一承担标题");

  const selfRules = parseCssRules(read("self-page.css"));
  const chipsWrap = selfRules.find(
    (r) => r.contexts.some((c) => /@container\s+self-journal\s+\(min-width:\s*651px\)/.test(c))
      && r.selector === ".section-chips"
      && /flex-wrap:\s*wrap/.test(r.body)
      && /overflow-x:\s*visible/.test(r.body),
  );
  assert.ok(chipsWrap, "self-page.css 桌面宽容器下 .section-chips 应自动换行，避免横向滚动条截断按钮");

  const decisionRules = parseCssRules(read("decision-page.css"));
  const fourWide = decisionRules.find(
    (r) => r.contexts.some((c) => /@container\s+decision-page\s+\(min-width:\s*720px\)/.test(c))
      && r.selector === ".decision-stats.decision-stats-four"
      && /repeat\(4,\s*minmax\(0,\s*1fr\)\)/.test(r.body),
  );
  const fourNarrow = decisionRules.find(
    (r) => r.contexts.some((c) => /@container\s+decision-page\s+\(max-width:\s*719px\)/.test(c))
      && r.selector === ".decision-stats.decision-stats-four"
      && /repeat\(2,\s*minmax\(0,\s*1fr\)\)/.test(r.body),
  );
  assert.ok(fourWide, "decision-page.css >=720px 容器下 4 张 KPI 应保持 4 列");
  assert.ok(fourNarrow, "decision-page.css <720px 容器下 4 张 KPI 应切为 2 列而非 3+1");
});
