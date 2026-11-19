/* 外观与设置。
 *
 * 主题、密度、圆角、字号、动效先写到 :root 的 data-* 属性并立即生效，
 * 真实色源是 tokens.css 的六套色板与 product-page.js 的 setAppearance()。
 * 页面运行在 AstrBot 的 opaque-origin iframe 里，localStorage 不可用，
 * 所以 product-page.js 会通过桥接 API 把偏好按 Dashboard 账号保存。
 * 这一页仍然**不能**用「读取 → 草稿 → 保存」那套规格——它的正确形状是
 * 「改一下，立刻看到」：没有保存按钮，没有未保存草稿。这一点是刻意的，
 * 把即时生效的东西做成两步会让人以为还需要再点一次。
 *
 * 与 app.js 里那份旧内联面板的关系：旧面板按「一组一行的选项列表」排版，
 * 六个主题平铺成两列，选了之后除了色卡上的小方块变色，屏幕上没有任何
 * 别的反馈——密度、圆角、字号三项更是完全看不到效果，只能选中后去别处
 * 看整页变没变。这一页把它重做成「预览窗 + 控件」，四个维度都作用在同一块
 * 样板上，改一下就能看见。
 */
"use strict";
window.CompanionAppearancePage = (() => {
  const catalog = () => window.CompanionTheme;
  const product = () => window.CompanionProduct;

  /* 六个主题、四组维度。分组来自 theme.js 的 mode（明/暗），
     与色板本身的注释一致：浅色四套按一天的光线顺序，深色两套按陈设风格。 */
  const LIGHT_ORDER = ["dawn", "courtyard", "dusk", "contrast"];
  const DARK_ORDER = ["night", "walnut"];

  const DIMENSIONS = [
    ["density", "显示密度", [["compact", "紧凑"], ["standard", "标准"], ["roomy", "宽松"]],
     "改变卡片内边距与区块之间的距离，字号不动。"],
    ["radius", "圆角", [["sharp", "方正"], ["standard", "标准"], ["round", "圆润"]],
     "卡片、按钮与输入框的边角。方正适合信息密集，圆润更柔和。"],
    ["typeScale", "字号", [["s", "小"], ["m", "标准"], ["l", "大"], ["xl", "特大"]],
     "整体缩放正文与说明文字。与密度相乘，所以「字号调小 + 宽松排版」也是对的。"],
    ["motion", "动效", [["full", "完整"], ["low", "减弱"], ["off", "关闭"]],
     "减弱只缩短时长，关闭还会停掉入场与循环动画。"],
  ];

  /* 预览窗展示一段完整的工作区结构，让表面、排版与控件状态一起参与主题比较。 */
  const PREVIEW = `
    <div class="apv-shell" aria-hidden="true">
      <aside class="apv-rail">
        <div class="apv-brand"><span class="apv-brand-mark">屋</span><strong>陪伴小屋</strong></div>
        <span class="apv-nav active">今日空间</span>
        <span class="apv-nav">状态记录</span>
        <span class="apv-nav">事实与经历</span>
        <span class="apv-nav-rule"></span>
        <span class="apv-nav">外观设置</span>
      </aside>
      <div class="apv-main">
        <div class="apv-topbar"><span>小屋 / 今日</span><span>显示主题示例</span></div>
        <div class="apv-content">
          <div class="apv-heading">
            <span><small>星期四 · 慢一点也没关系</small><strong>今天的生活</strong></span>
            <span class="apv-tag">状态平稳</span>
          </div>
          <div class="apv-card">
            <div class="apv-head">
              <span class="apv-dot"></span>
              <strong>此刻的感受</strong>
              <span class="apv-status">刚刚记录</span>
            </div>
            <p class="apv-line">午后的光落在窗边，适合喝杯茶、读一会儿书。</p>
            <div class="apv-row">
              <span class="apv-chip">核心记忆</span>
              <span class="apv-chip quiet">来源可追溯</span>
            </div>
            <div class="apv-actions">
              <button type="button" class="button small" tabindex="-1">查看详情</button>
              <button type="button" class="button small primary" tabindex="-1">记下一刻</button>
            </div>
          </div>
          <div class="apv-card apv-task-card">
            <div class="apv-head">
              <span class="apv-dot"></span>
              <strong>今天的三件事</strong>
              <span class="apv-status">1 / 3 已完成</span>
            </div>
            <div class="apv-row"><span class="apv-chip">散步 30 分钟</span><span class="apv-chip quiet">已完成</span></div>
            <div class="apv-row"><span class="apv-chip">采购周末食材</span><span class="apv-chip quiet">待确认</span></div>
          </div>
          <div class="apv-card apv-record-card">
            <div class="apv-head">
              <span class="apv-dot"></span>
              <strong>最近的记录</strong>
              <span class="apv-status">今天 · 2 条</span>
            </div>
            <ul class="apv-record-list">
              <li><time>09:20</time><span>在阳台给薄荷浇了水，晒了会儿太阳</span></li>
              <li><time>13:05</time><span>午饭后散步 30 分钟，路过花店</span></li>
            </ul>
          </div>
          <div class="apv-field">
            <label>留一句给小屋</label>
            <input type="text" value="今晚早点休息" readonly tabindex="-1">
          </div>
        </div>
      </div>
    </div>
  `;

  let root = null, state = null, version = 0;

  const esc = value => String(value ?? "").replace(/[&<>"']/g,
    ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

  function blank() {
    return { ready: false, theme: "courtyard", density: "standard", radius: "standard",
             typeScale: "m", motion: "full", followLight: false, largeText: false,
             systemReduced: false, notice: "" };
  }

  /* 读当前外观。product 还没就绪时（宿主注入比页面脚本晚）用默认值，
     并在挂载时补一次读取——不能因此整页空白。 */
  function read() {
    const next = blank();
    const api = product();
    const current = api && typeof api.appearance === "function" ? api.appearance() : null;
    if (current && typeof current === "object") Object.assign(next, {
      theme: current.theme || next.theme, density: current.density || next.density,
      radius: current.radius || next.radius, typeScale: current.typeScale || next.typeScale,
      motion: current.motion || next.motion });
    if (api && typeof api.followLight === "function") next.followLight = !!api.followLight();
    next.largeText = !!window.CompanionDisplayPreferences?.largeText?.();
    next.systemReduced = !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    next.ready = !!current;
    return next;
  }

  /* 生效中的主题可能是色板里的任意一套；theme.js 负责把旧名字与坏值收敛。 */
  const themeInfo = id => (catalog()?.THEMES || []).find(item => item.id === id) || null;
  const themeLabel = id => themeInfo(id)?.label || id;

  function themeCard(item) {
    const active = state.theme === item.id;
    /* 色卡里的颜色不传内联色值：给预览元素带上 data-theme，
       tokens.css 的 [data-theme=...] 生效范围选择器就会把那套色板的真实令牌
       喂给它。于是「色卡里画出来的」就是「选中后真实的」，
       不需要另抄一份色值——抄一份就会在两处之间漂移。 */
    return `<button type="button" class="ap-theme${active ? " active" : ""}"
      data-ap-theme="${esc(item.id)}" aria-pressed="${active}">
      <span class="ap-theme-swatch" data-theme="${esc(item.id)}" aria-hidden="true">
        <i class="ap-sw-bar"></i><i class="ap-sw-panel"></i><i class="ap-sw-line"></i><i class="ap-sw-line short"></i>
        <i class="ap-sw-dot"></i>
      </span>
      <span class="ap-theme-copy"><strong>${esc(item.label)}</strong><small>${esc(item.note)}</small></span>
      <span class="ap-theme-meta"><span class="ap-theme-current">当前</span></span>
    </button>`;
  }

  function themeSection() {
    const themes = catalog()?.THEMES || [];
    const group = (label, note, ids) => {
      const items = ids.map(id => themes.find(item => item.id === id)).filter(Boolean);
      if (!items.length) return "";
      return `<div class="ap-theme-group"><div class="ap-group-label"><strong>${label} · ${items.length} 套</strong><small>${note}</small></div>
        <div class="ap-theme-grid">${items.map(themeCard).join("")}</div></div>`;
    };
    const lightCount = themes.filter(item => item.mode !== "dark").length;
    const darkCount = themes.filter(item => item.mode === "dark").length;
    return `<section class="ap-surface ap-theme-panel" aria-label="主题">
      <header class="ap-panel-head"><div class="ap-panel-title"><h2>主题</h2><p>色卡里的颜色就是主题令牌原色。</p></div>
        <span class="ap-count-badge">日间 ${lightCount} · 夜间 ${darkCount}</span></header>
      ${group("日间", "按一天的光线顺序", LIGHT_ORDER)}${group("夜间", "按陈设风格", DARK_ORDER)}
      <label class="ap-switch-row">
        <span><b>跟随小屋光照</b><small>小屋昼夜变化时自动切换主题。</small></span>
        <span class="ap-switch${state.followLight ? " on" : ""}" role="switch" tabindex="0"
          aria-checked="${state.followLight}" data-ap-follow aria-label="跟随小屋光照"></span>
      </label>
    </section>`;
  }

  function dimensionSection() {
    return `<section class="ap-surface ap-dimension-panel" aria-label="显示维度">
      <header class="ap-panel-head"><div class="ap-panel-title"><h2>显示维度</h2>
        <p>改动后当前页面与上方预览同步更新。</p></div></header>
      <div class="ap-dimension-grid">
      ${DIMENSIONS.map(([name, label, options, hint]) => `
        <div class="ap-dim">
          <div class="ap-dim-copy"><b>${label}</b><small>${hint}</small></div>
          <div class="ap-seg" role="group" aria-label="${label}">
            ${options.map(([value, text]) => `<button type="button" data-ap-dim="${name}"
              data-ap-value="${value}" aria-pressed="${state[name] === value}"
              class="${state[name] === value ? "active" : ""}">${text}</button>`).join("")}
          </div>
        </div>`).join("")}
      </div>
    </section>`;
  }

  function assistSection() {
    return `<section class="ap-surface ap-assist-panel" aria-label="可读性">
      <header class="ap-panel-head"><div class="ap-panel-title"><h2>可读性</h2>
        <p>辅助说明文字；系统状态只读取，不在这里修改。</p></div></header>
      <div class="ap-assist-grid">
      <label class="ap-switch-row">
        <span><b>放大辅助文字</b><small>放大说明与详情文字。</small></span>
        <span class="ap-switch${state.largeText ? " on" : ""}" role="switch" tabindex="0"
          aria-checked="${state.largeText}" data-ap-large aria-label="放大辅助文字"></span>
      </label>
      <div class="ap-switch-row static">
        <span><b>尊重系统减少动态效果</b><small>与动效设置取更严格的一项。</small></span>
        <span class="ap-pill${state.systemReduced ? " on" : ""}">${state.systemReduced ? "系统已开启" : "系统未开启"}</span>
      </div>
      </div>
    </section>`;
  }

  function previewSection() {
    /* 预览窗是这一页的核心：四个维度都作用在它身上。
       它按当前设置渲染内外两套属性，所以「改了会怎样」当场可见，
       不必切出去看整页变没变。 */
    return `<section class="ap-surface ap-preview-panel" aria-label="预览">
      <header class="ap-panel-head"><div class="ap-panel-title"><h2>预览</h2>
        <p>五个维度作用在这块样板上，结构为示意，不是真实数据。</p></div>
        <span class="ap-preview-badge">按当前设置渲染</span></header>
      <div class="ap-preview" data-theme="${esc(state.theme)}"
        data-density="${esc(state.density)}" data-radius="${esc(state.radius)}"
        data-type-scale="${esc(state.typeScale)}" data-motion="${esc(state.motion)}">
        <div class="ap-preview-inner">${PREVIEW}</div>
      </div>
      <p class="ap-preview-note">
        当前：<b>${esc(themeLabel(state.theme))}</b> · ${esc(dimText("density"))} · ${esc(dimText("radius"))}
        · ${esc(dimText("typeScale"))} · ${esc(dimText("motion"))}${state.largeText ? " · 辅助文字已放大" : ""}
      </p>
    </section>`;
  }

  function dimText(name) {
    const spec = DIMENSIONS.find(([key]) => key === name);
    if (!spec) return "";
    const found = spec[2].find(([value]) => value === state[name]);
    return found ? `${spec[1]}：${found[1]}` : "";
  }

  function render() {
    if (!root) return;
    const ticket = version;
    if (!state) state = blank();
    root.innerHTML = `<div class="ap-page">
      <p class="ap-intro">这些偏好会按当前 Dashboard 账号保存到插件数据，点击后立即生效。主题、密度、圆角、字号与动效都会同步反映在下方预览中。</p>
      <section class="ap-section" aria-labelledby="ap-section-preview-title">
        <header class="ap-section-head">
          <span class="ap-section-number">01</span>
          <span class="ap-section-copy"><h2 id="ap-section-preview-title">预览与主题</h2>
            <small>左窗按当前设置实时渲染工作区结构，右侧选主题后也会立即反映在左窗。</small></span>
          <span class="ap-section-badge">选一套 · 左窗即刻变</span>
        </header>
        <div class="ap-reset-row">
          <span><b>显示偏好</b><small>保存到当前账号 · 同一账号换设备也会同步</small></span>
          <button type="button" class="button small" data-ap-reset>恢复默认外观</button>
        </div>
        <div class="ap-top-grid">${previewSection()}${themeSection()}</div>
      </section>
      <section class="ap-section" aria-labelledby="ap-section-dimensions-title">
        <header class="ap-section-head">
          <span class="ap-section-number amber">02</span>
          <span class="ap-section-copy"><h2 id="ap-section-dimensions-title">显示维度与可读性</h2>
            <small>四个显示维度共同决定排版；可读性辅助项让说明文字更易阅读。</small></span>
          <span class="ap-section-badge amber">全部即时生效</span>
        </header>
        ${dimensionSection()}
        ${assistSection()}
      </section>
      <p class="ap-footer-note">主题、密度、圆角、字号、动效与辅助文字保存在当前 AstrBot 账号下；使用同一 Dashboard 账号登录其他设备时会同步。</p>
    </div>${state.notice ? `<div class="ap-notice" role="status">${esc(state.notice)}</div>` : ""}`;
    if (!ticket || ticket !== version) return;
  }

  /* 改一项：交给 product 落盘与广播，然后只更新受影响的部分。
     整页重渲染会打断主题叠化（覆层是整页快照，重排会让它盖在已经变过的
     内容上），所以主题切换只同步按下态，不重渲染。 */
  function apply(name, value) {
    const api = product();
    if (!api || typeof api.setAppearance !== "function") {
      state.notice = "页面连接尚未就绪，请稍后重新读取外观设置。";
      render();
      return;
    }
    const applied = api.setAppearance(name, value);
    state[name] = applied || value;
    if (name === "theme") {
      syncPressed("[data-ap-theme]", "apTheme", state.theme);
      refreshPreview();
      refreshSummary();
      return;
    }
    /* 非主题维度不触发叠化，可以整页重渲染保持一致性。 */
    render();
  }

  function syncPressed(selector, dataKey, active) {
    root.querySelectorAll(selector).forEach(node => {
      const on = node.dataset[dataKey] === active;
      node.classList.toggle("active", on);
      node.setAttribute("aria-pressed", String(on));
    });
  }

  /* 预览窗只换属性，不动 DOM 结构——重建会让输入框失焦、让过渡重新开始。 */
  function refreshPreview() {
    const box = root.querySelector(".ap-preview");
    if (box) box.dataset.theme = state.theme;
  }

  function refreshSummary() {
    const note = root.querySelector(".ap-preview-note");
    if (note) note.innerHTML = `当前：<b>${esc(themeLabel(state.theme))}</b> · `
      + `${esc(dimText("density"))} · ${esc(dimText("radius"))} · ${esc(dimText("typeScale"))} `
      + `· ${esc(dimText("motion"))}${state.largeText ? " · 辅助文字已放大" : ""}`;
  }

  function setSwitch(node, on) {
    node.classList.toggle("on", on);
    node.setAttribute("aria-checked", String(on));
  }

  /* 大字模式。它在外壳里实现为 body 上的一個类（见 app.js 的 state.largeText
     与 updateAppearance 的 classList.toggle('large-text')），不在 product 的
     外观维度里——那个维度只管主题与四个显示维度。
     设置通过外壳 API 落盘并同步全局样式。 */
  function toggleLargeText() {
    const preferences = window.CompanionDisplayPreferences;
    if (preferences && typeof preferences.setLargeText === "function") {
      state.largeText = !!preferences.setLargeText(!state.largeText);
      return true;
    }
    const node = document.querySelector("[data-display='largeText']");
    if (node) { node.click(); return true; }
    return true;
  }

  /* 由 app.js 在小屋光照换了主题之后调用（跟随光照那一跳）。
     保留节点、只重读——重挂会重建叠化覆层，看着像闪了一下。 */
  function refresh() {
    if (!root) return;
    const next = read();
    if (!next.ready) return;
    Object.assign(state, next);
    render();
  }

  function onClick(event) {
    const target = event.target;
    const theme = target.closest("[data-ap-theme]");
    if (theme) { apply("theme", theme.dataset.apTheme); return; }
    const dim = target.closest("[data-ap-dim]");
    if (dim) { apply(dim.dataset.apDim, dim.dataset.apValue); return; }
    if (target.closest("[data-ap-reset]")) {
      const api = product();
      if (!api || typeof api.resetAppearance !== "function") return;
      const restored = api.resetAppearance();
      Object.assign(state, restored);
      render();
      return;
    }
    const follow = target.closest("[data-ap-follow]");
    if (follow) {
      const api = product();
      if (!api || typeof api.setFollowLight !== "function") return;
      state.followLight = !!api.setFollowLight(!state.followLight);
      setSwitch(follow, state.followLight);
      return;
    }
    if (target.closest("[data-ap-large]")) {
      /* 外壳持有该偏好，页面只同步它的值与开关状态。 */
      toggleLargeText();
      if (!window.CompanionDisplayPreferences?.setLargeText) state.largeText = !state.largeText;
      setSwitch(target.closest("[data-ap-large]"), state.largeText);
      refreshSummary();
      return;
    }
  }

  function onKeydown(event) {
    if (event.key !== " " && event.key !== "Enter") return;
    const switcher = event.target.closest?.("[data-ap-follow],[data-ap-large]");
    if (!switcher) return;
    event.preventDefault();
    switcher.click();
  }

  function mount(node) {
    if (!node) return;
    unmount();
    root = node;
    state = read();
    const ticket = ++version;
    render();
    root.addEventListener("click", onClick);
    root.addEventListener("keydown", onKeydown);
    /* product.appearance() 在宿主注入完成前读不到值。挂载时如果不是 ready，
       补一次读取——不轮询，只补一拍，避免在预览环境里空转。 */
    if (!state.ready) setTimeout(() => {
      if (ticket !== version || !root) return;
      const next = read();
      if (!next.ready) return;
      Object.assign(state, next);
      render();
    }, 400);
  }

  function unmount() {
    if (root) { root.removeEventListener("click", onClick); root.removeEventListener("keydown", onKeydown); }
    root = null; state = null;
    ++version;
  }

  return {mount, unmount, refresh};
})();
