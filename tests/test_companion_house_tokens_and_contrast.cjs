/* 设计令牌统一、暗色主题对比度与死样式清理回归测试。
 *
 * 守住四组不变量：
 *   1. pages/陪伴小屋/ 全站零未定义语义/尺度/组件 CSS 自定义属性；
 *   2. 六套主题（含 night / walnut 深色主题）的关键前景/背景组合均满足 WCAG AA >= 4.5:1，
 *      #detailDialog、#toast、.maint-cache-donut strong 与工作区暗色遮罩无对比度退化；
 *   3. 三个嵌入式工作区（persona / designer / relationships）六套主题完整对齐且
 *      workspace-bundles/*.js 均已注入 --sp-* / --r-* / --fs-* 尺度令牌；
 *   4. 「全部功能」筛选分隔线不依赖 :nth-child(2n)、导航弹层走 CSS Grid、死样式不再回流。
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const ROOT = path.resolve(__dirname, "..");
const PAGE = path.join(ROOT, "pages", "陪伴小屋");
const readPage = name => fs.readFileSync(path.join(PAGE, name), "utf8");
const stripComments = css => css.replace(/\/\*[\s\S]*?\*\//g, "");

function hexToRgb(hex) {
  const clean = hex.replace(/^#/, "");
  const full = clean.length === 3 ? clean.split("").map(ch => ch + ch).join("") : clean;
  return [0, 2, 4].map(offset => parseInt(full.slice(offset, offset + 2), 16));
}

function relativeLuminance([r, g, b]) {
  const channel = v => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrastRatio(fgHex, bgHex) {
  const l1 = relativeLuminance(hexToRgb(fgHex));
  const l2 = relativeLuminance(hexToRgb(bgHex));
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

function extractThemeMaps(css) {
  const clean = stripComments(css);
  const themes = new Map();
  for (const match of clean.matchAll(/([^{}]+)\{([^{}]+)\}/g)) {
    const [, selector, body] = match;
    for (const name of ["dawn", "courtyard", "dusk", "night", "walnut", "contrast"]) {
      if (!selector.includes(`data-theme="${name}"`)) continue;
      if (!themes.has(name)) themes.set(name, {});
      const map = themes.get(name);
      for (const decl of body.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+)/gi)) {
        map[decl[1]] = decl[2].trim();
      }
    }
  }
  return themes;
}

test("pages/陪伴小屋/*.css 不再存在未定义的语义/尺度/兼容 CSS 自定义属性", () => {
  const files = fs.readdirSync(PAGE).filter(name => name.endsWith(".css") && name !== "bundle.css");
  const declared = new Set();
  const referenced = new Map();

  for (const name of files) {
    const clean = stripComments(readPage(name));
    for (const match of clean.matchAll(/(--[a-zA-Z0-9_-]+)\s*:/g)) {
      declared.add(match[1]);
    }
    for (const match of clean.matchAll(/var\(\s*(--[a-zA-Z0-9_-]+)/g)) {
      const token = match[1];
      if (!referenced.has(token)) referenced.set(token, new Set());
      referenced.get(token).add(name);
    }
  }

  const guardedTokens = [
    "--t-bg", "--t-muted", "--t-ink", "--t-danger-ink", "--t-warn-soft",
    "--m-muted", "--m-line2", "--m-ink2",
    "--surface", "--surface-soft", "--surface-muted", "--line-strong",
    "--danger", "--amber", "--mono", "--font-mono", "--radius-md", "--shadow-1",
  ];
  for (const token of guardedTokens) {
    if (referenced.has(token)) {
      assert.ok(declared.has(token),
        `变量 ${token} 被 [${[...referenced.get(token)].join(", ")}] 引用但未声明`);
    }
  }

  // 所有 --t-* / --sp-* / --r-* / --fs-* 引用都必须有声明
  const missingCore = [];
  for (const [token, owners] of referenced) {
    if (/^--(?:t|sp|r|fs)-/.test(token) && !declared.has(token)) {
      missingCore.push(`${token} (${[...owners].join(", ")})`);
    }
  }
  assert.deepEqual(missingCore, [], `存在未声明的核心令牌：${missingCore.join("；")}`);

  // 确认各源文件已直接清理掉坏引用
  assert.doesNotMatch(readPage("memory-page.css"), /var\(--t-bg\b/);
  assert.doesNotMatch(readPage("setup-guide.css"), /var\(--radius-md\b|var\(--shadow-1\b/);
  assert.doesNotMatch(readPage("backup-page.css"), /var\(--m-line2\b|var\(--m-ink2\b/);
  assert.doesNotMatch(readPage("migration-config-table.css"), /var\(--t-warn-soft\b|#fff4df|#946020/i);
  assert.doesNotMatch(readPage("weather-source.css"), /var\(--font-mono\b/);
  assert.doesNotMatch(readPage("screen-access.js"), /var\(--fs-small\b/);
  assert.doesNotMatch(readPage("styles.css"), /-\s*var\(--/);
  assert.doesNotMatch(readPage("styles.css"), /\.all-functions-error\{[^}]*var\(--danger\)/);
  assert.doesNotMatch(readPage("styles.css"), /#87521f/i);
});

test("setup-guide.css 将旧变量别名提升至 .setup-guide-host 与 .setup-shell 且无硬编码色值", () => {
  const setup = stripComments(readPage("setup-guide.css"));
  assert.match(setup, /\.setup-guide-host,\s*\.setup-shell,\s*\.setup-legacy\s*\{[^}]*--surface:\s*var\(--t-surface\)/s);
  assert.doesNotMatch(setup, /\brgba?\(|#[0-9a-fA-F]{3,8}\b/,
    "setup-guide.css 不应残留绕开令牌的硬编码色值");
});

test("六套主题在 tokens.css 中的核心前景/背景对比度均达到 WCAG AA (>= 4.5:1)", () => {
  const tokensCss = readPage("tokens.css");
  const themes = extractThemeMaps(tokensCss);
  assert.equal(themes.size, 6);

  const requiredPairs = [
    ["--t-on-solid", "--t-accent-solid"],
    ["--t-on-solid", "--t-accent-solid-hover"],
    ["--t-text", "--t-canvas"],
    ["--t-text", "--t-surface"],
    ["--t-text", "--t-surface-raised"],
    ["--t-text-soft", "--t-surface"],
    ["--t-text-soft", "--t-surface-soft"],
    ["--t-ok-ink", "--t-surface"],
    ["--t-ok-ink", "--t-surface-soft"],
    ["--t-ok-ink", "--t-ok-quiet"],
    ["--t-warn-ink", "--t-warn-quiet"],
    ["--t-risk-ink", "--t-risk-quiet"],
    ["--t-text-invert", "--t-risk"],
    ["--t-text-invert", "--t-text-strong"],
    ["--t-accent-ink", "--t-accent-quiet"],
  ];

  for (const [themeName, map] of themes) {
    for (const [fg, bg] of requiredPairs) {
      assert.ok(map[fg] && map[bg], `${themeName} 缺少 ${fg} 或 ${bg}`);
      const ratio = contrastRatio(map[fg], map[bg]);
      assert.ok(ratio >= 4.5,
        `${themeName}: ${fg} (${map[fg]}) on ${bg} (${map[bg]}) 对比度仅 ${ratio.toFixed(2)}:1 (< 4.5:1)`);
    }
  }

  // 暗色主题下遮罩不能混入浅色 --t-text，须使用深色 --t-canvas
  assert.match(tokensCss,
    /:root\[data-theme="night"\][\s\S]*?:root\[data-theme="walnut"\][^{]*\{[^}]*--scrim:\s*color-mix\(in srgb,\s*var\(--t-canvas\)/);
  assert.doesNotMatch(readPage("confirmation-dialog.css"),
    /box-shadow:[^;]*var\(--t-text\)/,
    "confirmation-dialog.css 阴影不能由 --t-text 派生，否则深色主题会出现白色光晕");
});

test("theme-skins.css 覆盖深色弹层、冷紫控件与 #3FE0A6 荧光绿读数，且自身零硬编码色值", () => {
  const skins = stripComments(readPage("theme-skins.css"));
  const bundle = readPage("bundle.css");

  assert.doesNotMatch(skins, /#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(/,
    "theme-skins.css 必须完全走语义令牌，不得写死色值");

  for (const pattern of [
    /\.detail-dialog\{[^}]*background:var\(--t-surface-raised\)/,
    /\.dialog-head\{[^}]*background:var\(--t-veil\)/,
    /\.source-box\{[^}]*background:var\(--t-surface-soft\)/,
    /\.switch\[aria-checked="true"\]\{[^}]*background:var\(--t-accent-solid\)/,
    /#toast\{[^}]*background:var\(--t-text-strong\)[^}]*color:var\(--t-text-invert\)/,
    /\.maint-cache-donut strong,\s*\.maint-cache-rate-copy \.maint-cache-period-comparison\[data-direction="up"\]\{color:var\(--t-ok-ink\)\}/,
  ]) {
    assert.match(skins, pattern);
  }

  // 确认 bundle.css 中 theme-skins.css 排在 styles.css 和 maintenance-page.css 之后
  const stylesPos = bundle.indexOf("/* styles.css */");
  const maintPos = bundle.indexOf("/* maintenance-page.css */");
  const skinsPos = bundle.indexOf("/* theme-skins.css */");
  assert.ok(stylesPos >= 0 && maintPos > stylesPos && skinsPos > maintPos,
    "theme-skins.css 必须位于 styles.css 与 maintenance-page.css 之后才能稳定覆盖");
});

test("三大嵌入式工作区（persona / designer / relationships）补齐六套主题且暗色对比度达标", () => {
  const workspaces = [
    ["persona", path.join(ROOT, "pages", "人物设定工坊", "style.css")],
    ["designer", path.join(ROOT, "pages", "新的她", "style.css")],
    ["relationships", path.join(ROOT, "pages", "关系与记忆", "style.css")],
  ];

  for (const [name, canonicalPath] of workspaces) {
    const css = fs.readFileSync(canonicalPath, "utf8");
    const projected = fs.readFileSync(path.join(PAGE, "workspaces", name, "style.css"), "utf8");
    assert.equal(projected.replace(/\r\n/g, "\n").trim(), css.replace(/\r\n/g, "\n").trim(),
      `workspaces/${name}/style.css 与源码页不同步`);

    const themes = extractThemeMaps(css);
    for (const id of ["dawn", "courtyard", "dusk", "night", "walnut", "contrast"]) {
      assert.ok(themes.has(id), `${name} 缺少 data-theme="${id}"`);
    }

    for (const darkId of ["night", "walnut"]) {
      const dark = themes.get(darkId);
      assert.equal(dark["color-scheme"], undefined, "color-scheme 不是自定义属性");
      const blockMatch = stripComments(css).match(
        new RegExp(`:root\\[data-theme="${darkId}"\\][^{]*\\{([^{}]+)\\}`));
      assert.ok(blockMatch, `${name} 找不到 ${darkId} 规则块`);
      assert.match(blockMatch[1], /color-scheme\s*:\s*dark/,
        `${name} 的 ${darkId} 必须声明 color-scheme: dark`);
      assert.ok(dark["--page"] && dark["--surface"] && dark["--ink"],
        `${name} 的 ${darkId} 必须声明暗色表面与文字变量`);
      assert.ok(relativeLuminance(hexToRgb(dark["--page"])) < 0.05,
        `${name} 的 ${darkId} 背景必须是深色，实测 ${dark["--page"]}`);
      assert.ok(contrastRatio(dark["--ink"], dark["--surface"]) >= 7.0,
        `${name} 的 ${darkId} 正文/表面对比度不足`);
      assert.ok(contrastRatio(dark["--on-solid"], dark["--accent-solid"]) >= 4.5,
        `${name} 的 ${darkId} 主按钮对比度不足`);
    }
  }

  // relationships 专属修复：无非法 box-shadow、无冷紫默认底、.primary 走 solid 令牌、--pad-card 同步负边距
  const relCss = fs.readFileSync(path.join(ROOT, "pages", "关系与记忆", "style.css"), "utf8");
  assert.doesNotMatch(relCss, /box-shadow:\s*0\s+22px\s+80px\s+var\(--shadow\)/);
  assert.doesNotMatch(relCss, /--accent:\s*#4f53a8/i);
  assert.match(relCss, /\.primary\s*\{[^}]*var\(--t-accent-solid/);
  assert.match(relCss, /\.record-list\s*\{[^}]*calc\(var\(--pad-card/);
});

test("tools/sync_framework_page.py 向全部六个工作区包同步 --sp-* / --r-* / --fs-* 与暗色遮罩", () => {
  const bundleDir = path.join(PAGE, "workspace-bundles");
  for (const name of ["host", "life", "relationships", "identity", "persona", "designer"]) {
    const raw = fs.readFileSync(path.join(bundleDir, `${name}.js`), "utf8");
    const html = JSON.parse(raw.slice(raw.indexOf(']="') + 2, raw.lastIndexOf('";') + 1));
    const style = (html.match(/<style>([\s\S]*?)<\/style>/) || [])[1] || "";
    assert.ok(style.length > 0, `${name}.js 缺少内联样式`);
    for (const token of ["--sp-1:", "--sp-4:", "--r-xs:", "--r-md:", "--fs-micro:", "--fs-body:", "--t-canvas:", "--t-accent-solid:", "--scrim:"]) {
      assert.ok(style.includes(token), `workspace-bundles/${name}.js 缺少 ${token}`);
    }
    assert.match(style, /:root\[data-theme="night"\][\s\S]*?--backdrop:/,
      `workspace-bundles/${name}.js 缺少暗色主题遮罩覆盖`);
  }
});

test("全部功能筛选分隔线、导航弹层网格与死样式清理均保持生效", () => {
  const styles = stripComments(readPage("styles.css"));
  assert.doesNotMatch(styles, /\.all-feature-row:nth-child\(2n\)/,
    "all-functions 不得再用 :nth-child(2n) 切分隔线，否则搜索 [hidden] 过滤会错位");
  assert.match(styles, /\.all-feature-list\{[^}]*margin-right:-1px;margin-bottom:-1px/);
  assert.doesNotMatch(styles, /@media\(max-width:900px\)\{[^}]*\.all-functions-summary\{grid-template-columns:1fr\}/,
    "all-functions-summary 不应在 <=900px 掉到 1 列后又在 <=560px 切回 3 列");

  const nav = stripComments(readPage("navigation.css"));
  assert.doesNotMatch(nav, /#[0-9a-fA-F]{3,8}\b/, "navigation.css 不应残留硬编码色值");
  assert.doesNotMatch(nav, /width:calc\(50% - 4px\)/, "navigation.css 应使用 CSS Grid 替代 inline-flex 50% 计算");
  assert.match(nav, /\.navigation-group\{[^}]*display:grid;grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(nav, /\.navigation-dialog::backdrop\{[^}]*var\(--scrim\)/);

  assert.equal(fs.existsSync(path.join(PAGE, "topic-page.css")), false,
    "零引用的死文件 topic-page.css 应保持移除");
  const action = stripComments(readPage("action-page.css"));
  assert.doesNotMatch(action, /\.action-overview\b|\.action-config-card\b/,
    "action-page.css 已废弃的旧概览/旧配置卡规则不应回流");
  assert.match(action, /\.action-qzone\{/);
  assert.match(action, /\.action-capability-row\{/);
  assert.match(action, /\.action-kicker\{/);
});

test("视觉打磨回归：.voxel-hint 深色令牌化、搜索底栏与清空按钮对齐及画中画底部滚动避让", () => {
  const styles = stripComments(readPage("styles.css"));
  const voxelHint = styles.match(/\.voxel-hint\{([^}]*)\}/);
  assert.ok(voxelHint, "styles.css 应定义 .voxel-hint");
  assert.doesNotMatch(voxelHint[1], /#f8faff|#55617b|#ffffff/i,
    ".voxel-hint 不得保留硬编码浅色 hex，防止 night/walnut 暗色主题下出现白底药丸");
  assert.match(voxelHint[1], /var\(--t-surface-raised\)/);
  assert.match(voxelHint[1], /var\(--t-text-soft\)/);
  assert.match(voxelHint[1], /var\(--t-line-firm\)/);

  assert.match(styles, /\.search-clear\{[^}]*white-space:\s*nowrap/);
  assert.match(styles, /\.search-hint\{[^}]*justify-content:\s*space-between/);
  assert.match(styles, /\.search-hint-esc\{[^}]*display:\s*inline-flex[^}]*white-space:\s*nowrap/);

  const shell = stripComments(readPage("shell.css"));
  assert.match(
    shell,
    /#homeShell\[data-away="true"\]\s+\.shell-panel-body\s*\{[^}]*padding-bottom:\s*calc\([^;]*var\(--shell-pip-h/s,
    "离开首页显示画中画时，.shell-panel-body 必须预留充足底部滚动避让空间",
  );
  assert.match(
    shell,
    /#homeShell\[data-away="true"\]\s+\.shell-panel-body\s*\{[^}]*scroll-padding-bottom:\s*calc\(/s,
    ".shell-panel-body 应同步设置 scroll-padding-bottom 避免锚点/控件贴底被画中画遮住",
  );
});
