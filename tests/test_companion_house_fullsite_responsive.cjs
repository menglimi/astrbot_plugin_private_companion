const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const read = rel => fs.readFileSync(path.join(ROOT, rel), "utf8").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
const stripComments = css => css.replace(/\/\*[\s\S]*?\*\//g, "");

const OWNED_COTTAGE_CSS = [
  "pages/陪伴小屋/perception-subpages.css",
  "pages/陪伴小屋/output-page.css",
  "pages/陪伴小屋/contact-page.css",
  "pages/陪伴小屋/maintenance-page.css",
  "pages/陪伴小屋/models-page.css",
  "pages/陪伴小屋/state-page.css",
  "pages/陪伴小屋/native-extension-panel.css",
  "pages/陪伴小屋/appearance-page.css",
];

const OWNED_WORKSPACE_CSS = [
  "pages/关系与权限/style.css",
  "pages/宿主管理/style.css",
];

const ALL_OWNED_CSS = [...OWNED_COTTAGE_CSS, ...OWNED_WORKSPACE_CSS];

function findBareFrColumns(css) {
  const clean = stripComments(css);
  const hits = [];
  for (const m of clean.matchAll(/grid-template-columns\s*:\s*([^;{}]+)/g)) {
    let val = m[1];
    for (let i = 0; i < 6; i++) {
      const next = val.replace(/minmax\([^()]*\)/g, "MM").replace(/min\([^()]*\)/g, "MN");
      if (next === val) break;
      val = next;
    }
    if (/(?<![a-zA-Z0-9_-])(?:\d*\.\d+|\d+)fr\b/.test(val)) {
      hits.push(m[1].trim());
    }
  }
  return hits;
}

function findSelfQueryingContainers(css) {
  const clean = stripComments(css);
  const owners = new Map();
  for (const m of clean.matchAll(/([^{};]+)\{([^{}]*container-name\s*:\s*([a-zA-Z0-9_-]+)[^{}]*)\}/g)) {
    const name = m[3].trim();
    if (!owners.has(name)) owners.set(name, new Set());
    for (const sel of m[1].split(",")) {
      const s = sel.trim();
      if (s) owners.get(name).add(s);
    }
  }
  const hits = [];
  for (const m of clean.matchAll(/@container\s+([a-zA-Z0-9_-]+)\s*[^{]*\{/g)) {
    const name = m[1];
    const set = owners.get(name);
    if (!set) continue;
    let depth = 1;
    let i = m.index + m[0].length;
    while (i < clean.length && depth > 0) {
      if (clean[i] === "{") depth++;
      else if (clean[i] === "}") depth--;
      i++;
    }
    const body = clean.slice(m.index + m[0].length, i - 1);
    for (const rm of body.matchAll(/([^{};]+)\{/g)) {
      for (const sel of rm[1].split(",")) {
        if (set.has(sel.trim())) hits.push(`@container ${name} -> ${sel.trim()}`);
      }
    }
  }
  return hits;
}

test("全站十份响应式样式表：语法平衡、零 -var()、零裸 fr、零未保护 auto-fit、零自查询容器", () => {
  for (const rel of ALL_OWNED_CSS) {
    const css = read(rel);
    const clean = stripComments(css);

    assert.equal(
      (clean.match(/\{/g) || []).length,
      (clean.match(/\}/g) || []).length,
      `${rel} 花括号应成对闭合`
    );
    assert.equal(
      (clean.match(/\(/g) || []).length,
      (clean.match(/\)/g) || []).length,
      `${rel} 圆括号应成对闭合`
    );
    assert.doesNotMatch(clean, /-var\s*\(/, `${rel} 不得存在非法的 -var() 负号语法`);
    assert.deepEqual(findBareFrColumns(css), [], `${rel} 的 grid-template-columns 不得包含裸 fr 轨道`);
    assert.doesNotMatch(
      clean,
      /repeat\(\s*auto-(?:fit|fill)\s*,\s*minmax\(\s*\d+(?:\.\d+)?px\s*,/,
      `${rel} 的 auto-fit/auto-fill 轨道必须用 min(..., 100%) 防止窄容器溢出`
    );
    assert.deepEqual(findSelfQueryingContainers(css), [], `${rel} 不得在 @container 内查询容器自身`);
  }
});

test("全站样式表：零未定义变量，且小屋样式引用的 --t-* 与 --m-* 均有声明", () => {
  const tokensCss = stripComments(read("pages/陪伴小屋/tokens.css"));
  const declaredTokens = new Set([...tokensCss.matchAll(/(--t-[a-zA-Z0-9_-]+)\s*:/g)].map(m => m[1]));
  const modelsCss = stripComments(read("pages/陪伴小屋/models-page.css"));
  const declaredModelVars = new Set([...modelsCss.matchAll(/(--m-[a-zA-Z0-9_-]+)\s*:/g)].map(m => m[1]));
  const missingModelVars = [...modelsCss.matchAll(/var\(\s*(--m-[a-zA-Z0-9_-]+)/g)]
    .map(m => m[1])
    .filter(name => !declaredModelVars.has(name));
  assert.deepEqual([...new Set(missingModelVars)], [], "models-page.css 引用了未声明的 --m-* 变量");

  const forbiddenVars = /var\(\s*--(mono|m-muted|surface-soft|font-mono|t-danger-ink)\b/;

  for (const rel of ALL_OWNED_CSS) {
    const clean = stripComments(read(rel));
    if (rel !== "pages/关系与权限/style.css") {
      assert.doesNotMatch(clean, forbiddenVars, `${rel} 不得引用未定义变量`);
    } else {
      assert.doesNotMatch(
        clean,
        /var\(\s*--(mono|m-muted|font-mono|t-danger-ink)\b/,
        `${rel} 不得引用未定义变量`
      );
    }
  }

  for (const rel of OWNED_COTTAGE_CSS) {
    const clean = stripComments(read(rel));
    const missing = [...clean.matchAll(/var\(\s*(--t-[a-zA-Z0-9_-]+)/g)]
      .map(m => m[1])
      .filter(name => !declaredTokens.has(name));
    assert.deepEqual([...new Set(missing)], [], `${rel} 引用了 tokens.css 未声明的 --t-* 令牌`);
  }
});

test("工作区样式投影与源码保持一致（identity / host）", () => {
  assert.equal(
    read("pages/关系与权限/style.css"),
    read("pages/陪伴小屋/workspaces/identity/style.css"),
    "关系与权限样式应与 workspaces/identity/style.css 同步一致"
  );
  assert.equal(
    read("pages/宿主管理/style.css"),
    read("pages/陪伴小屋/workspaces/host/style.css"),
    "宿主管理样式应与 workspaces/host/style.css 同步一致"
  );
});

test("perception-subpages.css：5 槽导航、消除 (2,2) 网格碰撞、健康卡片通栏与容器折行", () => {
  const css = stripComments(read("pages/陪伴小屋/perception-subpages.css"));
  assert.match(css, /\.perception-config-workspace\s*\{[^}]*container-name:\s*perception-workspace/);
  assert.match(css, /\.perception-config-nav\s*\{[^}]*repeat\(\s*5\s*,\s*minmax\(\s*0\s*,\s*1fr\s*\)\)/);
  assert.match(css, /#perception-detail-model-route\s*\{[^}]*grid-row:\s*1(?:\s*;|})/);
  assert.doesNotMatch(css, /#perception-detail-model-route\s*\{[^}]*span\s+2/);
  assert.match(css, /#perception-detail-health\s*\{[^}]*grid-column:\s*1\s*\/\s*-1/);
  assert.match(css, /@container\s+perception-workspace\s*\(\s*max-width:\s*960px\s*\)[\s\S]*?\.reality-config-shell\s*\{[^}]*grid-template-columns:\s*minmax\(\s*0\s*,\s*1fr\s*\)/);
});

test("output-page.css：修复 7/1 与 4/1 画幅拼写错误、容器折行与审图模型下拉宽度", () => {
  const css = stripComments(read("pages/陪伴小屋/output-page.css"));
  assert.doesNotMatch(css, /aspect-ratio\s*:\s*(?:7|4)\s*\/\s*1\b/, "不得残留 7/1 或 4/1 压扁画幅");
  assert.match(css, /\.image-materials-gallery-image\s*\{[^}]*aspect-ratio:\s*4\s*\/\s*3/);
  assert.match(css, /\.output-page\s*\{[^}]*container-name:\s*output-page/);
  assert.match(css, /@container\s+output-page\s*\(\s*max-width:\s*960px\s*\)[\s\S]*?\.output-overview/);
  assert.match(css, /\.output-endpoint-grid\s*\{[^}]*repeat\(\s*auto-fit\s*,\s*minmax\(\s*min\(/);
  assert.match(css, /review_provider[\s\S]*?max-width:\s*none/);
});

test("contact-page.css：修复拨盘区 170px 挤压、素材工具栏溢出与滑块负边距", () => {
  const css = stripComments(read("pages/陪伴小屋/contact-page.css"));
  assert.match(css, /\.contact-workspace,\s*#contactWorkspace\s*\{[^}]*container-name:\s*contact-workspace/);
  assert.match(css, /\.ct-main\s*\{[^}]*container-name:\s*ct-main/);
  assert.match(css, /@container\s+ct-main\s*\(\s*max-width:\s*780px\s*\)[\s\S]*?\.ct-controls\s*\{[^}]*grid-template-columns:\s*minmax\(\s*0\s*,\s*1fr\s*\)/);
  assert.match(css, /\.ct-material-tools\s*\{[^}]*repeat\(\s*auto-fit\s*,\s*minmax\(\s*min\(\s*160px\s*,\s*100%\s*\)\s*,\s*1fr\s*\)\)/);
  assert.match(css, /\.ct-field-pair\s*\{[^}]*repeat\(\s*2\s*,\s*minmax\(\s*0\s*,\s*1fr\s*\)\)/);
  assert.match(css, /calc\(\s*\(\s*var\(--ct-track\)\s*-\s*var\(--ct-thumb\)\s*\)\s*\/\s*2\s*\)/);
});

test("maintenance-page.css：5 列页签导航、缓存/联动容器折行与高对比语义色", () => {
  const css = stripComments(read("pages/陪伴小屋/maintenance-page.css"));
  assert.match(css, /\.data-page-nav\s*\{[^}]*repeat\(\s*5\s*,\s*minmax\(\s*0\s*,\s*1fr\s*\)\)/);
  assert.match(css, /@container\s+maint-cache\s*\(\s*max-width:\s*960px\s*\)[\s\S]*?\.maint-cache-split/);
  assert.match(css, /\.maint-memory-linkage\s+dl\s*\{[^}]*repeat\(\s*auto-fit\s*,\s*minmax\(\s*min\(\s*130px\s*,\s*100%\s*\)\s*,\s*1fr\s*\)\)/);
  assert.match(css, /\.maint-cache-donut\s+strong\s*\{[^}]*color:\s*var\(--t-ok-ink\)/);
  assert.match(css, /\[data-direction="up"\]\s*\{[^}]*color:\s*var\(--t-ok-ink\)/);
  assert.match(css, /var\(--numeric-font\)/);
  assert.match(css, /var\(--t-risk-ink\)/);
});

test("models-page.css：去双重内边距、修复图片路由导航层叠冲突与 904px 网格收敛", () => {
  const css = stripComments(read("pages/陪伴小屋/models-page.css"));
  assert.match(css, /#modelWorkspace\s*\{[^}]*padding:\s*0/);
  assert.match(css, /\.model-qgrid[\s\S]*?min\(\s*380px\s*,\s*100%\s*\)/);
  assert.match(css, /\.model-img-nav\s*\{[^}]*display:\s*grid/);
  assert.doesNotMatch(css, /\.model-img-nav\s*\{[^}]*display:\s*flex/);
  assert.match(css, /\.model-workspace\s*\{[^}]*container-name:\s*model-workspace/);
  assert.match(css, /\.model-hrs\s+span:nth-child\(even\)\s*\{[^}]*visibility:\s*hidden/);
});

test("state-page.css：修复头部副文案对比度、12 月份窄容器折行与深色日期控件兼容", () => {
  const css = stripComments(read("pages/陪伴小屋/state-page.css"));
  assert.match(css, /\.state-hero\s+p\s*\{[^}]*var\(--t-text-invert\)/);
  assert.doesNotMatch(css, /\.state-hero\s+p\s*\{[^}]*var\(--t-text-soft\)/);
  assert.match(css, /@container\s+state-workspace\s*\(\s*max-width:\s*960px\s*\)[\s\S]*?\.state-feature-content-planning/);
  assert.match(css, /@container\s+state-annual\s*\(\s*max-width:\s*560px\s*\)[\s\S]*?\.state-annual-months\s*\{[^}]*repeat\(\s*6\s*,/);
  assert.doesNotMatch(css, /\.state-annual-row\s+input\[type=date\]\s*\{[^}]*color-scheme:\s*light/);
});

test("native-extension-panel.css：去重折叠符号、2 列开关网格、吸顶防遮挡与零硬编码 rgba", () => {
  const css = stripComments(read("pages/陪伴小屋/native-extension-panel.css"));
  assert.doesNotMatch(css, /\.mobile-connection-guide\s+summary::after/, "已有 Lucide 图标，不得重复渲染 +/- 伪元素");
  assert.match(css, /\.mobile-config-section-body\s*\{[^}]*repeat\(\s*2\s*,\s*minmax\(\s*0\s*,\s*1fr\s*\)\)/);
  assert.match(css, /@container\s+mobile-config\s*\(\s*max-width:\s*660px\s*\)[\s\S]*?\.mobile-config-section-body/);
  assert.match(css, /top:\s*max\(\s*73px\s*,\s*calc\(\s*57px\s*\+\s*env\(\s*safe-area-inset-top\s*\)\s*\)\s*\)/);
  assert.doesNotMatch(css, /rgba\s*\(/i, "native-extension-panel.css 应全面使用语义色板与 color-mix");
});

test("appearance-page.css：904px 视口与容器下自动折叠顶栏预览网格", () => {
  const css = stripComments(read("pages/陪伴小屋/appearance-page.css"));
  assert.match(css, /\.ap-page\s*\{[^}]*container-name:\s*ap-page/);
  assert.match(css, /\.ap-preview\s*\{[^}]*container-name:\s*ap-preview/);
  assert.match(css, /@container\s+ap-page\s*\(\s*max-width:\s*960px\s*\)[\s\S]*?\.ap-top-grid\s*\{[^}]*grid-template-columns:\s*minmax\(\s*0\s*,\s*1fr\s*\)/);
  assert.match(css, /@media\s*\(\s*max-width:\s*1280px\s*\)[\s\S]*?\.ap-top-grid\s*\{[^}]*grid-template-columns:\s*minmax\(\s*0\s*,\s*1fr\s*\)/);
});

test("关系与权限/style.css：904px 双栏折叠、调参不挤压、621–880px 作用域网格修复与响应式 SVG", () => {
  const css = stripComments(read("pages/关系与权限/style.css"));
  assert.match(css, /\.relationship-app\s*\{[^}]*container-name:\s*relationship-app/);
  assert.match(css, /@container\s+relationship-app\s*\(\s*max-width:\s*920px\s*\)[\s\S]*?\.people-layout\s*\{[^}]*grid-template-columns:\s*minmax\(\s*0\s*,\s*1fr\s*\)/);
  assert.match(css, /\.member-contact-purpose-tuning-fields\s*\{[^}]*repeat\(\s*auto-fit\s*,\s*minmax\(\s*min\(\s*220px\s*,\s*100%\s*\)\s*,\s*1fr\s*\)\)/);
  assert.match(css, /@media\s*\(\s*max-width:\s*880px\s*\)[\s\S]*?\.scope-item\s*\{[^}]*grid-column:\s*auto/);
  assert.match(css, /\.relationship-graph-svg\s*\{[^}]*min-width:\s*0/);
  assert.match(css, /\.radar-graph\s*\{[^}]*min-width:\s*0/);
  assert.match(css, /\.member-network-svg\s*\{[^}]*min-width:\s*0/);
  assert.match(css, /\.member-avatar\s*\{[^}]*color-mix\(/);
  assert.match(css, /:root\[data-radius="sharp"\]/);
  assert.match(css, /:root\[data-density="compact"\]/);
  assert.match(css, /:root\[data-motion="off"\]/);
});

test("宿主管理/style.css：3 列状态概览、暖纸令牌与六套主题、容器折行与响应式拓扑 SVG", () => {
  const css = stripComments(read("pages/宿主管理/style.css"));
  assert.match(css, /\.status-grid\s*\{[^}]*repeat\(\s*3\s*,\s*minmax\(\s*0\s*,\s*1fr\s*\)\)/);
  assert.doesNotMatch(css, /\.status-grid\s*\{[^}]*repeat\(\s*4\s*,/, "状态概览仅有 3 项，不得设为 4 列留白");
  assert.doesNotMatch(css, /#4f53a8|#f5f5fa/i, "不得残留旧版冷紫色默认主题");
  for (const theme of ["courtyard", "dawn", "dusk", "night", "walnut", "contrast"]) {
    assert.match(css, new RegExp(`:root\\[data-theme="${theme}"\\]`), `应兼容主题 ${theme}`);
  }
  assert.match(css, /\.button-primary\s*\{[^}]*var\(--t-accent-solid/);
  assert.match(css, /\.shell\s*\{[^}]*container-name:\s*host-shell/);
  assert.match(css, /@container\s+host-shell\s*\(\s*max-width:\s*780px\s*\)[\s\S]*?\.topology-canvas\s*\{[^}]*grid-template-columns:\s*minmax\(\s*0\s*,\s*1fr\s*\)/);
  assert.match(css, /\.session-group-network-viewport\s+svg\s*\{[^}]*min-width:\s*0/);
});

function lastTopLevelRuleEnd(css, selectorPrefix) {
  const clean = stripComments(css);
  let i = 0;
  let lastPos = -1;
  while (i < clean.length) {
    while (i < clean.length && /\s/.test(clean[i])) i++;
    if (i >= clean.length) break;
    const start = i;
    if (clean[i] === "@") {
      let j = i;
      while (j < clean.length && clean[j] !== "{" && clean[j] !== ";") j++;
      if (j >= clean.length) break;
      if (clean[j] === ";") { i = j + 1; continue; }
      let depth = 1;
      let k = j + 1;
      while (k < clean.length && depth > 0) {
        if (clean[k] === "{") depth++;
        else if (clean[k] === "}") depth--;
        k++;
      }
      i = k;
    } else {
      const j = clean.indexOf("{", i);
      if (j === -1) break;
      const sel = clean.slice(i, j).trim();
      let depth = 1;
      let k = j + 1;
      while (k < clean.length && depth > 0) {
        if (clean[k] === "{") depth++;
        else if (clean[k] === "}") depth--;
        k++;
      }
      if (sel.split(",").some(s => s.trim() === selectorPrefix)) lastPos = start;
      i = k;
    }
  }
  return lastPos;
}

test("关键响应式 @container / @media 规则按源码顺序排在对应桌面声明之后（防级联遮蔽）", () => {
  const checks = [
    ["pages/陪伴小屋/models-page.css", ".model-img-route-grid", "@container model-workspace (max-width:720px)"],
    ["pages/陪伴小屋/models-page.css", ".model-img-overview", "@container model-workspace (max-width:720px)"],
    ["pages/陪伴小屋/models-page.css", ".model-img-workbench", "@media(max-width:960px)"],
    ["pages/陪伴小屋/state-page.css", ".state-config-content-presentation", "@container state-workspace (max-width:720px)"],
    ["pages/关系与权限/style.css", ".member-hero-stats", "@container person-detail (max-width: 560px)"],
    ["pages/关系与权限/style.css", ".member-share-fields", "@container person-detail (max-width: 620px)"],
    ["pages/宿主管理/style.css", ".topology-canvas", "@container host-shell (max-width: 780px)"],
  ];
  for (const [rel, desktopSelector, responsiveMarker] of checks) {
    const css = stripComments(read(rel));
    const desktopIdx = lastTopLevelRuleEnd(css, desktopSelector);
    const respIdx = css.lastIndexOf(responsiveMarker);
    assert.ok(desktopIdx >= 0, `${rel} 应包含顶层桌面声明 ${desktopSelector}`);
    assert.ok(respIdx > desktopIdx, `${rel} 的 ${responsiveMarker} 必须位于顶层桌面声明 ${desktopSelector} 之后`);
  }
});

test("窄 @container 断点不被紧随其后的更宽 @media 覆盖（maintenance / state / appearance）", () => {
  const maint = stripComments(read("pages/陪伴小屋/maintenance-page.css"));
  const maintMedia1280 = maint.indexOf("@media (max-width:1280px)");
  const maintMedia1100 = maint.indexOf("@media (max-width:1100px)", maintMedia1280);
  const maintMedia980 = maint.indexOf("@media (max-width:980px)", maintMedia1100);
  const maintContainer960 = maint.indexOf("@container maint-cache (max-width:960px)", maintMedia980);
  const maintContainer640 = maint.indexOf("@container maint-cache (max-width:640px)", maintContainer960);
  const maintMedia740 = maint.indexOf("@media (max-width:740px)", maintContainer640);
  assert.ok(maintMedia1280 >= 0 && maintMedia1100 > maintMedia1280 && maintMedia980 > maintMedia1100);
  assert.ok(maintContainer960 > maintMedia980, "maint-cache 960px 容器必须排在 1280/1100/980px 媒体查询之后");
  assert.ok(maintContainer640 > maintContainer960, "maint-cache 640px 容器必须排在 960px 容器之后");
  assert.ok(maintMedia740 > maintContainer640, "maint-cache 740px/520px 移动端媒体查询应保持在后");

  const state = stripComments(read("pages/陪伴小屋/state-page.css"));
  const annual560 = state.indexOf("@container state-annual (max-width:560px)");
  const annualMedia650 = state.indexOf("@media(max-width:650px)", annual560);
  const annual340 = state.indexOf("@container state-annual (max-width:340px)");
  assert.ok(annual560 >= 0 && annualMedia650 > annual560, "state-annual 560px 容器应在 650px 媒体查询之前");
  assert.ok(annual340 > annualMedia650, "state-annual 340px 4 列容器必须排在 650px 6 列媒体查询之后");

  const ap = stripComments(read("pages/陪伴小屋/appearance-page.css"));
  const apMedia1280 = ap.indexOf("@media(max-width:1280px)");
  const apMedia1100 = ap.indexOf("@media(max-width:1100px)");
  const apContainer960 = ap.indexOf("@container ap-page (max-width:960px)");
  const apContainer680 = ap.indexOf("@container ap-page (max-width:680px)");
  const apMedia760 = ap.indexOf("@media(max-width:760px)");
  const apMedia480 = ap.indexOf("@media(max-width:480px)");
  assert.ok(apMedia1280 >= 0 && apMedia1100 > apMedia1280);
  assert.ok(apContainer960 > apMedia1100, "ap-page 960px 容器必须排在 1280/1100px 媒体查询之后");
  assert.ok(apContainer680 > apContainer960, "ap-page 680px 容器必须排在 960px 容器之后");
  assert.ok(apMedia760 > apContainer680 && apMedia480 > apMedia760, "ap-page 760/480px 移动端规则必须保持在容器查询之后");
});

test("视觉打磨 P1 回归：模型页签不拆字、感知时钟单行不折末位、节律盘环心收紧不碰刻度", () => {
  const modelsCss = stripComments(read("pages/陪伴小屋/models-page.css"));
  assert.match(
    modelsCss,
    /\.model-tabs\s*\{[^}]*flex-wrap:\s*nowrap[^}]*overflow-x:\s*auto/,
    "models-page.css 的 .model-tabs 应采用单行自然内容宽与平滑横滑，避免孤儿折行条",
  );
  assert.match(
    modelsCss,
    /\.model-tabs-sep\s*\{[^}]*width:\s*1px/,
    "models-page.css 应提供模型能力与计价/额度分组微分隔线 .model-tabs-sep",
  );
  assert.match(
    modelsCss,
    /\.model-tabs button\s*\{[^}]*white-space:\s*nowrap/,
    "models-page.css 桌面 .model-tabs button 必须声明 white-space:nowrap 防止功能模型/计价与汇率拆行",
  );
  assert.match(
    modelsCss,
    /\.model-tabs button\s*\{[^}]*flex-shrink:\s*0/,
    "models-page.css 桌面 .model-tabs button 应保持 flex-shrink:0 与自然内容宽",
  );

  const semanticCss = stripComments(read("pages/陪伴小屋/semantic-page.css"));
  assert.match(
    semanticCss,
    /\.perception-hero\.is-time\s*\{[^}]*container-type:\s*inline-size/,
    "semantic-page.css 的 .perception-hero.is-time 应提供 inline-size 容器供时钟流体缩放",
  );
  assert.match(
    semanticCss,
    /\.perception-hero\.is-time\s+\.perception-hero-copy\s+strong\s+b\s*\{[^}]*white-space:\s*nowrap/,
    "感知时钟必须声明 white-space:nowrap，禁止 HH:MM:SS 末位换行",
  );
  assert.match(
    semanticCss,
    /\.perception-hero\.is-time\s+\.perception-hero-copy\s+strong\s+b\s*\{[^}]*clamp\([^;]*cqi/,
    "感知时钟应使用 clamp(... cqi ...) 与视口 fallback 自适应字号",
  );

  const contactCss = stripComments(read("pages/陪伴小屋/contact-page.css"));
  assert.match(
    contactCss,
    /\.ct-center\s*\{[^}]*width:\s*30%[^}]*max-width:\s*112px/s,
    "contact-page.css 的 .ct-center 必须收紧至 width:30%; max-width:112px 避免压到 3/6 点钟向柱子",
  );
});

test("治理与维护页顶栏回归：「每日巡视设置」收纳进 .shell-panel-actions 顶栏，移除独立 .maintenance-settings-entry 占行", () => {
  const maintCss = stripComments(read("pages/陪伴小屋/maintenance-page.css"));
  const shellCss = stripComments(read("pages/陪伴小屋/shell.css"));
  const appJs = read("pages/陪伴小屋/app.js");

  const pageBlock = maintCss.match(/\.maintenance-page\s*\{([^}]*)\}/);
  assert.ok(pageBlock, "maintenance-page.css 应定义 .maintenance-page");
  assert.match(pageBlock[1], /grid-template-columns:\s*minmax\(\s*0\s*,\s*1fr\s*\)/);
  assert.match(pageBlock[1], /gap:\s*var\(--sp-[23]\)/);
  assert.doesNotMatch(
    pageBlock[1],
    /gap:\s*var\(--sp-[5678]\)/,
    ".maintenance-page 顶层网格间距不得再使用 --sp-5/6 造成顶部空白断层",
  );
  assert.doesNotMatch(
    maintCss,
    /\.maintenance-settings-entry\b/,
    "maintenance-page.css 不应再保留独立占行的 .maintenance-settings-entry 样式",
  );
  assert.doesNotMatch(
    appJs,
    /maintenance-settings-entry/,
    "app.js 不应再在正文顶部渲染独立占行的 .maintenance-settings-entry",
  );
  assert.match(
    appJs,
    /class="shell-panel-actions"/,
    "panelHead 应使用统一的右侧 .shell-panel-actions 操作簇",
  );
  assert.doesNotMatch(
    appJs,
    /class="panel-spacer"/,
    "panelHead 不应再使用割裂的 .panel-spacer",
  );
  assert.match(
    shellCss,
    /\.shell-panel-actions\s*\{[^}]*margin-left:\s*auto[^}]*display:\s*flex/s,
    "shell.css 应将 .shell-panel-actions 右对齐并横向排布",
  );
  assert.match(
    shellCss,
    /@media\s*\(\s*max-width:\s*720px\s*\)\s*\{[\s\S]*?\.shell-panel-actions\s*\{[^}]*margin-left:\s*0[^}]*justify-content:\s*flex-start/s,
    "shell.css 在 <= 720px 窄屏应自然换行并左对齐 .shell-panel-actions",
  );
  assert.doesNotMatch(
    maintCss,
    /@media\s*\(\s*max-width:\s*740px\s*\)\s*\{[^@]*\.maintenance-page\s*\{[^}]*gap:\s*var\(--sp-[5678]\)/,
    "740px 以下移动端的 .maintenance-page 不得回退到 --sp-5/6 大间距",
  );
});
