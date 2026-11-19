"use strict";
window.CompanionUnderstandingPage = (() => {
  const tabs = [
    ["overview", "总览"], ["image", "图片理解"], ["video", "视频理解"],
    ["file", "文件理解"], ["web", "网页理解"], ["forwards", "合并转发"],
  ];
  let root = null;
  let active = "overview";
  let replyStatus = null;
  let hostStatus = null;

  const esc = value => String(value ?? "").replace(/[&<>"']/g,
    character => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[character]));
  const settings = () => replyStatus?.settings || {};
  const number = (name, unit) => {
    const value = Number(settings()[name]);
    return Number.isFinite(value) ? `${value.toLocaleString("zh-CN")} ${unit}` : "—";
  };
  const status = name => typeof settings()[name] === "boolean"
    ? settings()[name] ? "已启用" : "已关闭" : "—";

  function formatCount() {
    const value = settings().file_understanding_format_groups;
    if (Array.isArray(value)) return value.length;
    if (typeof value !== "string" || !value.trim()) return null;
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) return parsed.length;
    } catch {}
    return value.split(/[\r\n,;，；]+/).filter(item => item.trim()).length;
  }

  function permissionCount() {
    const names = [
      "image_understanding_private_enabled", "image_understanding_group_enabled",
      "video_understanding_private_enabled", "video_understanding_group_enabled",
      "file_understanding_private_enabled", "file_understanding_group_enabled",
    ];
    const values = names.map(name => settings()[name]);
    return values.every(value => typeof value === "boolean")
      ? values.filter(Boolean).length : null;
  }

  function metricsFor(tab) {
    if (tab === "overview") {
      const count = permissionCount();
      return [
        ["视觉转述缓存上限", number("private_image_vision_cache_max_items", "条"), status("enable_private_image_vision_cache")],
        ["识图失败冷却", number("private_image_provider_failure_cooldown_seconds", "秒"), ""],
        ["视频理解", status("video_understanding_enabled"), ""],
        ["场景许可", "6 项", count === null ? "—" : `已启用 ${count} 项`],
      ];
    }
    if (tab === "image") return [
      ["视觉转述缓存上限", number("private_image_vision_cache_max_items", "条"), status("enable_private_image_vision_cache")],
      ["识图失败冷却", number("private_image_provider_failure_cooldown_seconds", "秒"), ""],
      ["上下文描述条数", number("context_image_caption_max_items", "张"), status("enable_context_image_captioning")],
      ["AstrBot 原生动图优化", status("astrbot_animated_image_understanding_enabled"), ""],
    ];
    if (tab === "video") return [
      ["视频理解状态", status("video_understanding_enabled"), ""],
      ["转述字数上限", number("video_understanding_max_chars", "字"), ""],
      ["转述等待时间", number("video_understanding_wait_seconds", "秒"), ""],
    ];
    if (tab === "file") {
      const count = formatCount();
      const parsers = replyStatus?.documents || {};
      const suffixes = Object.keys(parsers);
      const ready = suffixes.filter(name => parsers[name] === true).length;
      const missing = suffixes.filter(name => parsers[name] !== true)
        .map(name => name.replace(/^\./, "").toUpperCase());
      return [
        ["纳入格式", count === null ? "—" : `${count} / 5 类`, ""],
        ["解析预算", `${number("file_understanding_max_size_mb", "MB")} · ${number("file_understanding_timeout_seconds", "秒")}`, ""],
        ["文件理解状态", status("file_understanding_enabled"), ""],
        ["解析组件", suffixes.length ? `${ready} / ${suffixes.length} 可用` : "—",
          missing.length ? `缺少 ${missing.join("、")}` : ""],
      ];
    }
    if (tab === "web") {
      const web = replyStatus?.web || {};
      const value = name => Number.isFinite(Number(web[name])) ? Number(web[name]) : null;
      return [
        ["链接读取", web.enabled === false ? "已关闭" : web.enabled === true ? "已启用" : "—", ""],
        ["每轮条数", value("max_links") === null ? "—" : `${value("max_links")} 条`, ""],
        ["单页正文", value("max_chars") === null ? "—" : `${value("max_chars")} 字`, ""],
        ["抓取超时", value("timeout_seconds") === null ? "—" : `${value("timeout_seconds")} 秒`, ""],
      ];
    }
    const intake = hostStatus?.forwardIntake || {};
    const forward = name => Number.isFinite(Number(intake[name])) ? String(Number(intake[name])) : "—";
    return [
      ["单条消息最大转发数", forward("max_forwards"), ""],
      ["单条转发最大节点数", forward("max_nodes"), ""],
      ["最大嵌套深度", forward("max_depth"), ""],
    ];
  }

  function paintMetrics() {
    if (!root) return;
    for (const node of root.querySelectorAll("[data-understanding-stats]")) {
      const items = metricsFor(node.dataset.understandingStats);
      node.innerHTML = items.map(([label, value, detail]) => `<article class="understanding-stat">
        <span>${esc(label)}</span><strong>${esc(value)}</strong>${detail ? `<small>${esc(detail)}</small>` : ""}
      </article>`).join("");
    }
  }

  let onTabChange = null;

  function select(tab, {focus = false} = {}) {
    if (!tabs.some(([id]) => id === tab)) return false;
    const changed = active !== tab;
    active = tab;
    if (!root) return true;
    root.dataset.understandingActive = tab;
    for (const button of root.querySelectorAll("[data-understanding-tab]")) {
      const selected = button.dataset.understandingTab === tab;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      if (selected && focus) button.focus({preventScroll: true});
    }
    for (const panel of root.querySelectorAll("[data-understanding-panel]")) {
      panel.hidden = panel.dataset.understandingPanel !== tab;
    }
    const heading = root.closest(".shell-panel-body")?.previousElementSibling
      ?.querySelector("[data-understanding-heading]");
    if (heading) heading.textContent = tabs.find(([id]) => id === tab)?.[1] || "总览";
    const editor = root.querySelector("#contextImageWorkspace");
    const slot = root.querySelector(`[data-understanding-editor-slot="${tab}"]`);
    if (editor && slot && editor.parentElement !== slot) slot.appendChild(editor);
    window.CompanionReplyPage?.setPerceptionModule?.(tab);
    paintMetrics();
    if (changed) onTabChange?.(tab);
    return true;
  }

  function onClick(event) {
    const button = event.target?.closest?.("[data-understanding-tab]");
    if (button && root?.contains(button)) {
      select(button.dataset.understandingTab);
      return;
    }
    const jump = event.target?.closest?.("[data-understanding-scroll]");
    if (jump && root?.contains(jump)) {
      root.querySelector("#understandingPermissions")?.scrollIntoView?.({behavior: "smooth", block: "start"});
    }
  }

  function onKeydown(event) {
    const button = event.target?.closest?.("[data-understanding-tab]");
    if (!button || !root?.contains(button)) return;
    const current = tabs.findIndex(([id]) => id === button.dataset.understandingTab);
    let next = -1;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (current + 1) % tabs.length;
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (current - 1 + tabs.length) % tabs.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = tabs.length - 1;
    if (next < 0) return;
    event.preventDefault();
    select(tabs[next][0], {focus: true});
  }

  function mount(element, {onTabChange: onChange} = {}) {
    if (root && root !== element) unmount();
    if (!element) return unmount();
    root = element;
    onTabChange = typeof onChange === "function" ? onChange : null;
    root.addEventListener("click", onClick);
    root.addEventListener("keydown", onKeydown);
    select(root.dataset.understandingActive || active);
  }

  function unmount() {
    if (root) {
      root.removeEventListener("click", onClick);
      root.removeEventListener("keydown", onKeydown);
    }
    root = null;
    onTabChange = null;
  }

  function updateReplyStatus(value) {
    replyStatus = value || null;
    paintMetrics();
  }

  function updateHostOptions(value) {
    hostStatus = value || null;
    paintMetrics();
  }

  return {mount, unmount, select, activeTab: () => active, updateReplyStatus, updateHostOptions};
})();
