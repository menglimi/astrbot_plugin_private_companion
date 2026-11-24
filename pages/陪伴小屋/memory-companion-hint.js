/* 长期记忆搭档「我会牢牢记住你」检测、提示卡片与安装指引弹窗。
 *
 * 复用方：
 *   - 首页右侧概览（app.js）
 *   - 开始使用向导（setup-guide.js）
 *   - 事实与经历页与整理策略（memory-page.js / memory-settings-section.js）
 *   - 外观与设置页恢复提醒开关（appearance-page.js）
 *
 * 永久忽略（不再提示）持久化在服务端按 Dashboard 账号存储（/page/hint-preferences），
 * 不依赖沙盒 iframe 里不可用的 localStorage。
 */
"use strict";
window.CompanionMemoryCompanionHint = (() => {
  const PLUGIN_ID = "astrbot_plugin_memory_companion";
  const DISPLAY_NAME = "我会牢牢记住你";
  const REPO_URL = "https://github.com/menglimi/astrbot_plugin_memory_companion";
  const HINT_KEY = "memoryCompanion";
  const NEEDS_HINT = new Set(["missing", "disabled", "bridge_disabled"]);

  let cached = null;
  let inflight = null;
  let sessionDismissed = false;
  let dialogNode = null;
  const listeners = new Set();

  const esc = value => String(value ?? "").replace(/[&<>"']/g,
    ch => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[ch]));

  const product = () => window.CompanionProduct;

  function emit() {
    const snapshot = cached ? {...cached} : null;
    for (const fn of [...listeners]) {
      try { fn(snapshot); } catch (_) {}
    }
  }

  function subscribe(fn) {
    if (typeof fn !== "function") return () => {};
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  function normalize(raw) {
    if (!raw || typeof raw !== "object") return null;
    const state = ["connected", "disabled", "bridge_disabled", "missing", "external_other"].includes(raw.state)
      ? raw.state : "missing";
    return {
      state,
      plugin_id: typeof raw.plugin_id === "string" && raw.plugin_id ? raw.plugin_id : PLUGIN_ID,
      display_name: typeof raw.display_name === "string" && raw.display_name ? raw.display_name : DISPLAY_NAME,
      repo_url: typeof raw.repo_url === "string" && raw.repo_url ? raw.repo_url : REPO_URL,
      owner: typeof raw.owner === "string" ? raw.owner : (state === "connected" || state === "external_other" ? "external" : "self"),
      reason: typeof raw.reason === "string" ? raw.reason : "",
      external_name: typeof raw.external_name === "string" ? raw.external_name : "",
      hint_dismissed: Boolean(raw.hint_dismissed),
    };
  }

  function seed(raw) {
    const next = normalize(raw);
    if (!next) return cached;
    cached = next;
    emit();
    return {...cached};
  }

  async function load({force = false} = {}) {
    if (!force && cached) return {...cached};
    if (inflight && !force) return inflight;
    const api = product();
    if (!api || typeof api.request !== "function") return cached ? {...cached} : null;
    const request = (async () => {
      try {
        const result = await api.request("GET", "page/memory-companion");
        const next = normalize(result);
        if (next) {
          cached = next;
          emit();
        }
        return cached ? {...cached} : null;
      } catch (_) {
        return cached ? {...cached} : null;
      }
    })();
    inflight = request;
    try { return await request; }
    finally { if (inflight === request) inflight = null; }
  }

  function current() {
    return cached ? {...cached} : null;
  }

  function needsAttention(status = cached) {
    return Boolean(status && NEEDS_HINT.has(status.state));
  }

  function shouldShowBanner(status = cached) {
    return Boolean(status && needsAttention(status) && !status.hint_dismissed && !sessionDismissed);
  }

  function dismissSession() {
    sessionDismissed = true;
    emit();
  }

  async function setDismissed(dismissed) {
    const flag = Boolean(dismissed);
    const api = product();
    if (!api || typeof api.request !== "function") {
      throw new Error("页面连接尚未就绪，请稍后再试。");
    }
    const result = await api.request("POST", "page/hint-preferences", {
      key: HINT_KEY,
      dismissed: flag,
    });
    const nextDismissed = Boolean(result?.dismissedHints?.[HINT_KEY] ?? flag);
    if (cached) cached = {...cached, hint_dismissed: nextDismissed};
    else cached = normalize({state: "missing", hint_dismissed: nextDismissed});
    if (!nextDismissed) sessionDismissed = false;
    emit();
    return nextDismissed;
  }

  function copyInfo(status) {
    const info = normalize(status) || normalize({state: "missing"});
    if (info.state === "connected") {
      return {
        tone: "ok",
        badge: "已接通",
        title: `长期记忆搭档「${info.display_name}」已连接`,
        body: "长期记忆、人物画像与深度回忆已完整接管，当前与陪伴小屋无缝协同，无需额外配置。",
      };
    }
    if (info.state === "external_other") {
      const name = info.external_name || "外部记忆插件";
      return {
        tone: "ok",
        badge: "外部接管",
        title: `已接入外部记忆插件〈${name}〉`,
        body: "当前模型侧长期记忆由外部记忆插件提供，本插件事实库继续在后台保留本地事实记录。",
      };
    }
    if (info.state === "disabled") {
      return {
        tone: "warn",
        badge: "已安装未启用",
        title: `已安装「${info.display_name}」，但尚未启用`,
        body: "当前仅启用本插件轻量内置记忆，长期记忆与深度回忆不完整。前往 AstrBot「插件管理」启用该插件并重载即可自动接管。",
      };
    }
    if (info.state === "bridge_disabled") {
      return {
        tone: "warn",
        badge: "桥接未开启",
        title: `「${info.display_name}」协作桥接未开启`,
        body: "插件已安装并启用，但与陪伴插件的协作桥接未开启或暂不可读。在记忆插件配置中打开桥接开关后重载即可恢复协同。",
      };
    }
    return {
      tone: "warn",
      badge: "仅轻量内置记忆",
      title: `当前仅启用轻量内置记忆，建议安装「${info.display_name}」`,
      body: "本插件自带的事实库只保留基础事实与近期经历，长期记忆与深度回忆并不完整。强烈建议安装配合插件「我会牢牢记住你」，安装后自动接管并无缝协同。",
    };
  }

  function renderCard(status, {
    compact = false,
    allowDismiss = false,
    showWhenConnected = false,
    context = "overview",
  } = {}) {
    const info = normalize(status);
    if (!info) return "";
    if (!showWhenConnected && !needsAttention(info)) return "";
    if (allowDismiss && !showWhenConnected && (info.hint_dismissed || sessionDismissed)) return "";
    const meta = copyInfo(info);
    const guideLabel = info.state === "disabled" ? "查看启用步骤"
      : info.state === "bridge_disabled" ? "查看桥接说明"
      : "查看安装指南";
    return `<section class="mc-hint-card is-${meta.tone}${compact ? " is-compact" : ""}" data-mc-hint-card data-mc-hint-context="${esc(context)}" data-mc-hint-state="${esc(info.state)}" aria-label="长期记忆搭档状态">
      <div class="mc-hint-head">
        <span class="mc-hint-badge">${esc(meta.badge)}</span>
        <strong class="mc-hint-title">${esc(meta.title)}</strong>
      </div>
      <p class="mc-hint-body">${esc(meta.body)}</p>
      <div class="mc-hint-actions">
        ${needsAttention(info) ? `<button type="button" class="button small primary" data-mc-hint-action="open-guide">${esc(guideLabel)}</button>` : ""}
        <button type="button" class="button small" data-mc-hint-action="recheck">重新检测</button>
        ${allowDismiss && needsAttention(info) ? `<button type="button" class="button small ghost" data-mc-hint-action="dismiss-session">稍后提醒</button>
          <button type="button" class="button small ghost" data-mc-hint-action="dismiss-permanent">不再提示</button>` : ""}
      </div>
    </section>`;
  }

  async function copyText(text, statusEl, label) {
    let copied = false;
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        copied = true;
      }
    } catch (_) {}
    if (!copied && typeof document !== "undefined" && document.createElement) {
      try {
        const area = document.createElement("textarea");
        area.value = text;
        area.setAttribute("readonly", "");
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        copied = document.execCommand?.("copy") || false;
        area.remove();
      } catch (_) {}
    }
    if (statusEl) {
      statusEl.textContent = copied ? `已复制${label}：${text}` : `请手动复制${label}：${text}`;
    }
  }

  function renderDialogBody(status) {
    const info = normalize(status) || normalize({state: "missing"});
    const meta = copyInfo(info);
    const steps = info.state === "disabled" ? [
      "打开 AstrBot 管理面板左侧「插件管理」。",
      `找到「${esc(info.display_name)}」（<code>${esc(info.plugin_id)}</code>），点击开启启用开关。`,
      "等待插件加载完成，回到本页点击「重新检测」即可自动接通。",
    ] : info.state === "bridge_disabled" ? [
      `打开 AstrBot 管理面板 → 插件管理 → 找到「${esc(info.display_name)}」并进入配置。`,
      "确认 <code>private_companion_bridge.enabled</code>（与陪伴插件协作桥接）已开启并保存。",
      "重载插件后回到本页点击「重新检测」，状态将自动切换为已接通。",
    ] : [
      "打开 AstrBot 管理面板左侧「插件市场」，搜索「我会牢牢记住你」或插件 ID <code>astrbot_plugin_memory_companion</code> 直接安装；也可通过下方 GitHub 仓库地址手动安装。",
      "安装完成后确保插件处于已启用状态，并在该插件配置中保持与「我会永远陪着你」协作桥接开启（默认已开启）。",
      "完成后回到本页点击「重新检测」；接通后「我会永远陪着你」会自动让出模型侧记忆，避免重复写入。",
    ];
    return `<div class="mc-dialog-surface">
      <header class="mc-dialog-head">
        <div>
          <span class="mc-hint-badge is-${meta.tone}">${esc(meta.badge)}</span>
          <h3 id="mcHintDialogTitle">安装与接通「${esc(info.display_name)}」</h3>
        </div>
        <button type="button" class="button small" data-mc-dialog-action="close" aria-label="关闭弹窗">关闭</button>
      </header>
      <p class="mc-dialog-lead">${esc(meta.body)}</p>
      <div class="mc-dialog-compare">
        <div>
          <h4>本插件轻量内置事实库</h4>
          <ul>
            <li>保留基础单条事实、共同经历与日程联动</li>
            <li>适合无额外依赖时兜底运行</li>
            <li>不包含完整人物记忆图谱与深度跨期回忆</li>
          </ul>
        </div>
        <div class="is-recommended">
          <h4>配合「${esc(info.display_name)}」（推荐）</h4>
          <ul>
            <li>专注长期记忆沉淀、语义检索与深度回忆</li>
            <li>自动接管模型侧记忆读写，无双写冲突</li>
            <li>与陪伴小屋的生活、情绪与主动联系无缝协作</li>
          </ul>
        </div>
      </div>
      <ol class="mc-dialog-steps">${steps.map(item => `<li>${item}</li>`).join("")}</ol>
      <div class="mc-dialog-copy-grid">
        <div class="mc-dialog-copy-row">
          <span>插件 ID</span>
          <code>${esc(info.plugin_id)}</code>
          <button type="button" class="button small" data-mc-dialog-action="copy-id">复制 ID</button>
        </div>
        <div class="mc-dialog-copy-row">
          <span>仓库地址</span>
          <code>${esc(info.repo_url)}</code>
          <button type="button" class="button small" data-mc-dialog-action="copy-url">复制链接</button>
        </div>
      </div>
      <p class="mc-dialog-status" data-mc-dialog-status role="status" aria-live="polite"></p>
      <footer class="mc-dialog-actions">
        <button type="button" class="button primary" data-mc-dialog-action="recheck">我已安装 / 启用，重新检测</button>
        <button type="button" class="button" data-mc-dialog-action="close">稍后再看</button>
      </footer>
    </div>`;
  }

  function openGuideDialog(status = cached) {
    if (typeof document === "undefined" || !document.createElement || !document.body) return;
    if (!dialogNode || !dialogNode.isConnected) {
      dialogNode = document.createElement("dialog");
      dialogNode.className = "mc-hint-dialog";
      dialogNode.setAttribute("aria-labelledby", "mcHintDialogTitle");
      dialogNode.addEventListener("click", async event => {
        if (event.target === dialogNode) {
          dialogNode.close?.();
          return;
        }
        const button = event.target?.closest?.("[data-mc-dialog-action]");
        if (!button) return;
        const action = button.dataset.mcDialogAction;
        const statusEl = dialogNode.querySelector("[data-mc-dialog-status]");
        const info = current() || normalize({state: "missing"});
        if (action === "close") {
          dialogNode.close?.();
          return;
        }
        if (action === "copy-id") {
          await copyText(info.plugin_id, statusEl, "插件 ID");
          return;
        }
        if (action === "copy-url") {
          await copyText(info.repo_url, statusEl, "仓库地址");
          return;
        }
        if (action === "recheck") {
          button.disabled = true;
          const prevLabel = button.textContent;
          button.textContent = "检测中…";
          if (statusEl) statusEl.textContent = "正在重新检测「我会牢牢记住你」连接状态…";
          const prevScroll = typeof dialogNode.scrollTop === "number" ? dialogNode.scrollTop : 0;
          const prevState = info.state;
          const next = await load({force: true});
          const resolved = next || info;
          const statusText = resolved.state === "connected"
            ? "已检测到「我会牢牢记住你」成功接通！"
            : resolved.state === "external_other"
            ? `已接入外部记忆插件〈${resolved.external_name || "外部记忆插件"}〉！`
            : resolved.state === "disabled"
            ? "检测到插件已安装但尚未启用，请到 AstrBot 插件管理启用后再测。"
            : resolved.state === "bridge_disabled"
            ? "插件已启用，但协作桥接尚未打开，请检查记忆插件桥接开关。"
            : "暂未检测到「我会牢牢记住你」，安装并重载插件后再点击重新检测。";
          if (resolved.state === prevState) {
            button.disabled = false;
            button.textContent = prevLabel || "我已安装 / 启用，重新检测";
            if (statusEl) statusEl.textContent = statusText;
          } else {
            dialogNode.innerHTML = renderDialogBody(resolved);
            const refreshedStatus = dialogNode.querySelector("[data-mc-dialog-status]");
            if (refreshedStatus) refreshedStatus.textContent = statusText;
            if (typeof dialogNode.scrollTop === "number") dialogNode.scrollTop = prevScroll;
          }
        }
      });
      document.body.appendChild(dialogNode);
    }
    dialogNode.innerHTML = renderDialogBody(status || current());
    if (typeof dialogNode.showModal === "function") {
      if (!dialogNode.open) dialogNode.showModal();
    } else {
      dialogNode.setAttribute("open", "");
    }
  }

  async function handleAction(action, {onUpdate, notify} = {}) {
    if (action === "open-guide") {
      openGuideDialog(current());
      return true;
    }
    if (action === "recheck") {
      const next = await load({force: true});
      onUpdate?.(next);
      notify?.(next?.state === "connected"
        ? "已检测到「我会牢牢记住你」成功连接。"
        : "已刷新记忆搭档检测状态。");
      return true;
    }
    if (action === "dismiss-session") {
      dismissSession();
      onUpdate?.(current());
      notify?.("本次会话已暂时收起提醒，下次打开或到「开始使用 / 记忆页」仍可查看。");
      return true;
    }
    if (action === "dismiss-permanent") {
      try {
        await setDismissed(true);
        onUpdate?.(current());
        notify?.("已关闭此提醒；日后随时可在「外观与设置 → 提示与引导」重新打开。");
      } catch (error) {
        notify?.(error?.message || "保存偏好失败，请稍后重试。");
      }
      return true;
    }
    return false;
  }

  return {
    PLUGIN_ID,
    DISPLAY_NAME,
    REPO_URL,
    HINT_KEY,
    load,
    seed,
    current,
    needsAttention,
    shouldShowBanner,
    dismissSession,
    setDismissed,
    renderCard,
    openGuideDialog,
    handleAction,
    subscribe,
  };
})();
