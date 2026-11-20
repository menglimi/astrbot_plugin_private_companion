"use strict";

window.CompanionStoragePage = (() => {
  const ROUTE = "storage";
  const text = value => String(value ?? "").replace(/[&<>"']/g,
    character => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character]));
  const formatBytes = value => {
    const size = Number(value);
    if (!Number.isFinite(size) || size <= 0) return "0 B";
    if (size < 1024) return `${size} B`;
    if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`;
    if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} MB`;
    return `${(size / 1024 ** 3).toFixed(2)} GB`;
  };
  const labels = {database: "运行资料", backups: "备份快照", other: "其他文件"};
  let root = null, bridge = null, version = 0;
  let state = {loading: true, busy: false, error: false, message: "", status: null,
    form: {path: "", backup_path: "", retention_count: 10, retention_days: 90}, confirming: false};

  function live(ticket) { return ticket === version && root?.isConnected; }
  function stop() { version += 1; root = null; bridge = null; }
  function sizeBar(status) {
    const categories = status?.inventory?.categories || {};
    const total = Number(status?.inventory?.total_bytes) || 0;
    const segments = ["database", "backups", "other"].map(key => {
      const bytes = Number(categories[key]?.bytes) || 0;
      const width = total ? Math.max(bytes ? 1 : 0, bytes / total * 100) : 0;
      return `<span class="storage-usage-segment is-${key}" style="width:${width}%" title="${labels[key]} ${formatBytes(bytes)}"></span>`;
    }).join("");
    const legend = ["database", "backups", "other"].map(key =>
      `<div class="storage-usage-item"><span class="storage-usage-mark is-${key}"></span><span>${labels[key]}</span><strong>${formatBytes(categories[key]?.bytes)}</strong><small>${Number(categories[key]?.files) || 0} 个文件</small></div>`
    ).join("");
    return `<div class="storage-usage-bar" role="img" aria-label="插件存储占用 ${formatBytes(total)}">${segments || "<span></span>"}</div><div class="storage-usage-legend">${legend}</div>`;
  }
  function render() {
    if (!root) return;
    if (state.loading) {
      root.innerHTML = `<div class="storage-loading" role="status">正在读取插件存储…</div>`;
      return;
    }
    const status = state.status;
    const inventory = status?.inventory || {};
    const preview = status?.cleanup_preview || {};
    const message = state.message
      ? `<p class="storage-message${state.error ? " is-error" : ""}" role="status">${text(state.message)}</p>` : "";
    const locationState = status?.restart_required
      ? `<p class="storage-location-note is-pending" role="status">待重启生效 · ${text(status.desired_root)}</p>`
      : `<p class="storage-location-note">当前目录 · ${text(status?.active_root || "")}</p>`;
    const backupLocation = `<p class="storage-location-note">当前目录 · ${text(status?.backup_root || "")}</p><small>磁盘可用 ${formatBytes(status?.inventory?.backup_free_bytes ?? status?.inventory?.free_bytes)}</small>`;
    const cleanup = Number(preview.remove_count) > 0
      ? `<div class="storage-cleanup-preview"><div><strong>可清理 ${preview.remove_count} 份</strong><span>${formatBytes(preview.remove_bytes)}</span></div>${state.confirming
        ? `<button class="button small danger" type="button" data-storage-clean-confirm${state.busy ? " disabled" : ""}>确认清理</button><button class="button small" type="button" data-storage-clean-cancel>取消</button>`
        : `<button class="button small" type="button" data-storage-clean-review${state.busy ? " disabled" : ""}>清理快照</button>`}</div>`
      : `<p class="storage-cleanup-empty">${preview.snapshot_count ? "当前快照均在保留范围内。" : "暂无快照"}</p>`;
    const diskFree = (status?.restart_required ? status.destination_free_bytes : inventory.free_bytes) == null
      ? "—" : formatBytes(status?.restart_required ? status.destination_free_bytes : inventory.free_bytes);
    root.innerHTML = `<div class="storage-dashboard">
      <section class="storage-usage-card" aria-label="插件资料占用">
        <header class="storage-usage-header"><div><span>插件资料占用</span><strong>${formatBytes(inventory.total_bytes)}</strong></div><div><span>${status?.restart_required ? "目标磁盘可用" : "所在磁盘可用"}</span><strong>${diskFree}</strong></div></header>
        ${sizeBar(status)}
      </section>
      <form class="storage-settings" data-storage-form>
        <section class="storage-panel"><h4>存储位置</h4><div class="storage-location-grid">
          <label class="storage-path-field"><span class="storage-field-label">运行资料位置 <em>重启生效</em></span><input name="path" type="text" autocomplete="off" spellcheck="false" placeholder="留空使用 AstrBot 默认目录" value="${text(state.form.path)}"><small>填写其他磁盘上的绝对目录。</small>${locationState}</label>
          <label class="storage-path-field"><span class="storage-field-label">备份快照位置 <em>${state.form.backup_path ? "独立目录" : "跟随资料"}</em></span><input name="backup_path" type="text" autocomplete="off" spellcheck="false" placeholder="留空跟随运行资料位置" value="${text(state.form.backup_path)}"><small>切换目录时迁移已有快照。</small>${backupLocation}</label>
        </div></section>
        <section class="storage-panel"><h4>备份清理策略</h4><fieldset class="storage-retention"><label>每个运行档最多保留<input name="retention_count" type="number" min="1" max="200" step="1" value="${text(state.form.retention_count)}">份</label><label>保留最近<input name="retention_days" type="number" min="1" max="3650" step="1" value="${text(state.form.retention_days)}">天</label><small>新建快照时自动清理，始终保留最新一份。</small></fieldset>
          ${cleanup}<div class="storage-actions"><button class="button primary small" type="submit"${state.busy ? " disabled" : ""}>保存存储设置</button><button class="button small" type="button" data-storage-refresh${state.busy ? " disabled" : ""}>重新读取</button></div>
        </section>
        <section class="storage-panel"><h4>运行缓存</h4>
          <p class="storage-cache-note">图片转述、失败冷却和诊断报告都可以随时重建；清理只释放当前进程里的缓存，不会删除对话、记忆、日记或已发送的图片。</p>
          <div class="storage-actions"><button class="button small" type="button" data-storage-cache-clear${state.busy ? " disabled" : ""}>清理运行缓存</button></div>
        </section>
      </form>
      ${message}
    </div>`;
  }
  async function getBridge() {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      if (window.AstrBotPluginPage?.apiGet) return window.AstrBotPluginPage;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    throw Object.assign(new Error("bridge_unavailable"), {code: "bridge_unavailable"});
  }
  async function call(method, body) {
    bridge = bridge || await getBridge();
    const response = await (method === "GET" ? bridge.apiGet(ROUTE) : bridge.apiPost(ROUTE, body));
    const wrapped = ["ok", "error"].includes(response?.status) && response?.data !== undefined ? response
      : ["ok", "error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    if (wrapped?.status === "error") {
      throw Object.assign(new Error(wrapped.reason || wrapped.message || "storage_request_invalid"),
        {code: wrapped.data?.code || wrapped.message || "storage_request_invalid"});
    }
    const result = wrapped ? wrapped.data : response;
    if (!result || typeof result !== "object" || Array.isArray(result)) {
      throw Object.assign(new Error("invalid_response"), {code: "invalid_response"});
    }
    return result;
  }
  async function load(ticket = version) {
    state.loading = true;
    render();
    try {
      const result = await call("GET");
      if (!live(ticket)) return;
      state.status = result;
      state.form = {path: result.selected_path || "",
        backup_path: result.selected_backup_path || "",
        retention_count: result.policy?.retention_count ?? 10,
        retention_days: result.policy?.retention_days ?? 90};
      state.loading = false;
      state.error = false;
      state.message = "";
    } catch (error) {
      if (!live(ticket)) return;
      state.loading = false;
      state.error = true;
      state.message = error.code === "dashboard_identity_required" ? "请从 AstrBot 管理界面登录后查看。"
        : error.code === "storage_location_unavailable" ? "存储磁盘暂不可用；资料不会自动写回默认位置。"
        : "插件存储读取失败，请重试并检查管理界面连接。";
    }
    render();
  }
  async function update(body, ticket = version) {
    state.busy = true;
    state.message = "";
    render();
    try {
      const result = await call("POST", body);
      if (!live(ticket)) return;
      if (result.inventory) {
        state.status = result;
        state.form = {path: result.selected_path || "",
          backup_path: result.selected_backup_path || "",
          retention_count: result.policy?.retention_count ?? state.form.retention_count,
          retention_days: result.policy?.retention_days ?? state.form.retention_days};
      }
      const cache = result.cache_result || {};
      state.message = body.action === "clean"
        ? `已清理 ${result.cleanup_result?.removed_count || 0} 份快照，释放 ${formatBytes(result.cleanup_result?.removed_bytes)}。`
        : body.action === "cache"
        ? `已清理运行缓存：图片转述 ${Number(cache.image_narration) || 0} 条、失败记录 ${Number(cache.image_narration_failures) || 0} 条、诊断报告 ${Number(cache.diagnostics) || 0} 条。`
        : result.restart_required ? "设置已保存，运行资料位置将在重启后切换。" : "存储设置已保存。";
      state.error = false;
      state.confirming = false;
    } catch (error) {
      if (!live(ticket)) return;
      state.error = true;
      const candidate = error.code || error.message;
      const code = typeof candidate === "string" && /^[a-z][a-z0-9_]{2,80}$/.test(candidate) ? candidate : "";
      const messages = {
        storage_config_save_failed: "存储设置未能保存，请检查配置文件是否可写。",
        storage_request_invalid: "存储目录或清理设置不合法，请核对绝对路径与保留范围。",
        storage_operation_failed: "插件进程无法访问或迁移存储文件。请检查目录及磁盘连接；网络磁盘可填写对应的共享路径。",
        dashboard_identity_required: "请从已登录的 AstrBot 管理页面保存存储设置。",
        core_access_required: "当前管理身份没有修改存储设置的权限。",
      };
      state.message = messages[code] || "操作未完成，请核对存储目录与清理设置后重试。";
      if (code) state.message += `（${code}）`;
    }
    state.busy = false;
    render();
  }
  function mount(node) {
    stop();
    root = node;
    const ticket = version;
    state = {loading: true, busy: false, error: false, message: "", status: null,
      form: {path: "", backup_path: "", retention_count: 10, retention_days: 90}, confirming: false};
    root.addEventListener("input", event => {
      if (event.target?.name === "path") state.form.path = event.target.value;
      if (event.target?.name === "backup_path") state.form.backup_path = event.target.value;
      if (event.target?.name === "retention_count") state.form.retention_count = event.target.value;
      if (event.target?.name === "retention_days") state.form.retention_days = event.target.value;
    });
    root.addEventListener("submit", event => {
      if (!event.target?.matches("[data-storage-form]")) return;
      event.preventDefault();
      update({action: "save", path: String(state.form.path || "").trim(),
        backup_path: String(state.form.backup_path || "").trim(),
        retention_count: Number(state.form.retention_count), retention_days: Number(state.form.retention_days)}, ticket);
    });
    root.addEventListener("click", event => {
      const button = event.target?.closest("button");
      if (!button || state.busy) return;
      if (button.hasAttribute("data-storage-refresh")) load(ticket);
      else if (button.hasAttribute("data-storage-cache-clear")) update({action: "cache"}, ticket);
      else if (button.hasAttribute("data-storage-clean-review")) { state.confirming = true; render(); }
      else if (button.hasAttribute("data-storage-clean-cancel")) { state.confirming = false; render(); }
      else if (button.hasAttribute("data-storage-clean-confirm")) update({action: "clean"}, ticket);
    });
    render();
    load(ticket);
  }
  return {mount, unmount: stop};
})();
