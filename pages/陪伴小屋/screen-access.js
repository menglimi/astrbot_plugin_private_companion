/* Independent V2 consent for the native Screen capture tool. */
"use strict";
window.CompanionScreenAccess = (() => {
  const product = window.CompanionProduct;
  const messages = {
    screen_provider_unavailable:"未检测到可用的 Screen 原生扩展。请在 Screen 插件配置中启用 native_v2 并重载该插件后重试。",
    screen_provider_changed:"屏幕陪伴扩展已变化，请重新加载 V2 运行实例。",
    screen_package_changed:"屏幕陪伴包版本已变化。请升级已装配版本后再调整私聊授权。",
    screen_access_changed:"授权状态已变化，请重新读取后再保存。",
    capability_not_installed:"当前尚未启用屏幕陪伴。",
    capability_not_active:"屏幕陪伴能力已暂停或移除，需要先恢复能力。",
    capability_role_disabled:"当前 Bot 的这个人格尚未启用屏幕陪伴。",
    capability_role_not_enabled:"当前 Bot 的这个人格尚未启用屏幕陪伴。",
    capability_role_revision_conflict:"角色启用状态已变化，请重新读取后再试。",
    capability_not_admissible:"屏幕陪伴能力已暂停或移除，需要先恢复能力。",
    screen_read_consent_missing:"当前私聊尚未允许读取屏幕原件。",
    screen_process_consent_missing:"当前私聊尚未允许处理屏幕画面。",
    screen_tool_unavailable:"屏幕采集工具尚未进入当前对话。",
    screen_private_required:"屏幕读取只适用于已绑定的私聊窗口。",
    screen_target_unavailable:"当前私聊没有可用的屏幕授权地址，请在账号与会话中刷新目标后重试。",
    staging_not_initialized:"角色数据尚未就绪，请在「账号与会话」保存角色映射；已保存可重新读取状态。",
    staging_host_terminated:"V2 已停止，请重新加载插件。",
    core_access_required:"当前账号没有管理此授权的权限。",
    dashboard_identity_required:"请从已登录的 AstrBot 管理界面打开。",
    staging_screen_session_active:"V2 仍在运行。请先停止当前运行实例，再回来升级屏幕陪伴包。",
    staging_screen_upgrade_conflict:"屏幕陪伴安装状态已变化，请点击重新读取后核对。",
    staging_screen_upgrade_invalid:"当前安装版本不能直接升级，请检查 V2 屏幕陪伴包版本。",
    staging_screen_upgrade_failed:"屏幕陪伴升级未完成，请重新读取状态后再试。",
    bridge_timeout:"连接超时。可用同一个操作编号核对原请求。",
  };
  let node = null, path = "", status = null, pending = null, busy = false, notice = "", generation = 0;
  const pendingByScope = new Map();
  /* 路由切换会卸载工作区，但最近一次已确认的授权状态仍属于当前
     私聊范围。只放在本页进程内，按范围和实际路由隔离，不落盘。 */
  const statusByScope = new Map();
  const statusKey = (scope, route) => `${scope}:${route}`;
  const current = ticket => ticket === generation && node?.isConnected && path && path === currentPath();
  function currentPath() {
    try { return product.relative(product.role()?.screen_access_api || ""); } catch (_) { return ""; }
  }
  function errorText(error) { return messages[error?.code] || messages[error?.message] || "暂时无法核对屏幕授权，请重新读取。"; }
  function identityLine() {
    const target = product.target();
    return [product.scopeLabel?.(), target && product.targetLabel?.(target)].filter(Boolean).join(" · ") || "当前私聊窗口";
  }
  function privateWindows() {
    const roleId = product.selection?.().role_id;
    return (product.targets?.() || []).filter(target => target.scope_kind === "private"
      && target.roles?.some(role => role.role_id === roleId));
  }
  function scopeIssue() {
    const target = product.target();
    if (target?.scope_kind === "group") return "屏幕读取仅适用于已绑定的私聊窗口；请切换到私聊对象。";
    if (!product.hosted()) return "请从已登录的 AstrBot Dashboard 打开陪伴小屋。";
    if (!path) return messages.screen_target_unavailable;
    return "";
  }
  function escapeHTML(value) { return String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;", "'":"&#39;"}[char])); }
  function render() {
    if (!node?.isConnected) return;
    const ready = status?.invokable === true;
    const canManage = status?.can_manage !== false;
    const notInstalled = status?.installed === false;
    const paused = Boolean(status) && !notInstalled && status.active === false;
    const roleControlled = canManage && status?.auto_consent === true;
    const roleDisabled = status?.role_enabled === false;
    const unavailable = notInstalled ? "未装配" : paused ? "能力未就绪" : "";
    const command = pending;
    const issue = scopeIssue();
    const packageChanged = status?.reason === "screen_package_changed";
    const packageNotice = packageChanged
      ? status.upgrade_available
        ? `已装配版本 ${status.installed_package_version}，当前版本 ${status.package_version}。升级会保留当前私聊的屏幕授权。`
        : "已发现屏幕陪伴包版本变化，但当前安装状态不能直接升级。请在宿主管理核对屏幕陪伴能力后重新读取。"
      : "";
    const text = notice || issue || (!canManage ? messages.core_access_required
      : unavailable ? (notInstalled ? messages.capability_not_installed
        : messages[status?.reason] || "屏幕陪伴尚未就绪，请先在设备与终端里恢复模块。")
      : roleControlled && roleDisabled ? "已装配，但尚未为此角色启用屏幕陪伴；启用后本角色的私聊窗口即可按需看屏。"
      : ready ? "屏幕工具已进入当前私聊；只有本轮确实需要桌面画面时才会采集。"
      : packageNotice || (status ? messages[status.reason] || "当前私聊尚未允许屏幕读取。" : "正在读取屏幕授权…"));
    const action = issue || !canManage || unavailable ? "" : command ? "retry"
      : roleControlled ? (roleDisabled ? "role-enable" : "role-disable")
      : status ? (status.consented ? "disable" : "enable") : "";
    const label = unavailable || (command ? (busy ? "正在核对…" : "核对原请求")
      : roleControlled ? (roleDisabled ? "启用此角色" : "停用此角色")
      : status?.consented ? "撤回当前私聊授权" : "允许当前私聊按需看屏");
    const stateLabel = !canManage ? "只读" : issue ? "仅限私聊" : notInstalled ? "未装配" : paused ? "未就绪"
      : roleControlled ? (roleDisabled ? "未启用" : "已启用")
      : ready ? "已授权" : status ? "未就绪" : busy ? "核对中" : "暂不可用";
    const windows = privateWindows();
    const selectedWindow = windows.some(target => target.target_id === product.target()?.target_id);
    const selector = windows.length ? `<label class="config-field"><span>授权私聊</span><select data-screen-private-window aria-label="识屏授权私聊" ${busy ? "disabled" : ""}>${selectedWindow ? "" : '<option value="" selected>选择私聊窗口</option>'}${windows.map(target => `<option value="${escapeHTML(target.target_id)}" ${target.target_id === product.target()?.target_id ? "selected" : ""}>${escapeHTML(product.targetLabel?.(target) || target.display_name || target.participant_id || "私聊窗口")}</option>`).join("")}</select></label>` : "";
    node.innerHTML = `<div class="reality-consent-head"><div><strong>屏幕读取权限</strong><small>${escapeHTML(identityLine())}</small></div>
      <button class="button small" type="button" data-screen-access-action="refresh" ${busy ? "disabled" : ""}>重新读取</button></div>
      ${selector}
      <article class="reality-consent-panel ${ready ? "is-ready" : ""}">
        <header><span class="reality-consent-mark"><i data-lucide="monitor-dot"></i></span><div><h4>按需采集当前桌面</h4><small>按需采集画面；主动观察会保留原件及对应识屏结果，可在这里查看。</small></div>
          <span class="reality-consent-state ${ready ? "is-ready" : ""}">${stateLabel}</span></header>
        <p role="status" aria-live="polite">${escapeHTML(text)}</p>
        <div class="dialog-actions">${issue || !canManage ? "" : unavailable
          ? `<button class="button small" type="button" disabled>${unavailable}</button>`
          : action ? `<button class="button small ${status?.consented ? "secondary" : "primary"}" type="button" data-screen-access-action="${action}" ${busy ? "disabled" : ""}>${busy ? "正在保存…" : label}</button>` : ""}
          ${!issue && canManage && status?.upgrade_available ? `<button class="button small primary" type="button" data-screen-access-action="upgrade" ${busy ? "disabled" : ""}>升级屏幕陪伴</button>` : ""}</div>
      </article>`;
    window.lucide?.createIcons({attrs:{"aria-hidden":"true"}});
  }
  function nonce() { return crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`; }
  async function load(ticket = generation) {
    if (!node?.isConnected || busy) return;
    if (scopeIssue()) { render(); return; }
    if (!current(ticket)) return;
    busy = true; notice = ""; render();
    try {
      if (!product.hosted() || !path) throw {code:"screen_private_required"};
      const result = await product.request("GET", path);
      if (!current(ticket)) return;
      if (typeof result?.invokable !== "boolean" || typeof result?.consented !== "boolean" || typeof result?.edit_token !== "string") throw {code:"invalid_response"};
      status = result; pending = pendingByScope.get(product.scopeKey()) || null;
      statusByScope.set(statusKey(product.scopeKey(), path), result);
    } catch (error) {
      if (current(ticket)) {
        status = null;
        statusByScope.delete(statusKey(product.scopeKey(), path));
        notice = errorText(error);
      }
    } finally {
      if (current(ticket)) { busy = false; render(); }
    }
  }
  async function save(enabled) {
    if (!current(generation) || busy || !path || (!status && !pending)) return;
    const ticket = generation, scope = product.scopeKey();
    pending ||= {operation_key:`screen-${nonce()}`, edit_token:status.edit_token, enabled};
    pendingByScope.set(scope, pending); busy = true; notice = ""; render();
    try {
      const result = await product.request("POST", path + "/configure", pending);
      if (!current(ticket)) return;
      if (typeof result?.status?.invokable !== "boolean" || typeof result?.status?.consented !== "boolean") throw {code:"invalid_response"};
      status = result.status; statusByScope.set(statusKey(scope, path), result.status);
      pendingByScope.delete(scope); pending = null;
      notice = enabled ? "已保存：当前私聊可以在需要时按需看屏。" : "已撤回当前私聊的屏幕读取授权。";
    } catch (error) {
      if (current(ticket)) {
        notice = errorText(error);
        if (["screen_access_changed","screen_package_changed","screen_provider_changed","screen_provider_unavailable","screen_access_request_invalid","core_access_required","dashboard_identity_required"].includes(error.code || error.message)) {
          pendingByScope.delete(scope); pending = null;
          statusByScope.delete(statusKey(scope, path)); status = null;
        }
      }
    } finally { if (current(ticket)) { busy = false; render(); } }
  }
  async function saveRole(enabled) {
    if (!current(generation) || busy || !path || !status) return;
    const ticket = generation, scope = product.scopeKey();
    const command = {operation_key:`screen-role-${nonce()}`,
      expected_revision:Number.isInteger(status.role_revision) ? status.role_revision : 0, enabled};
    busy = true; notice = ""; render();
    try {
      const result = await product.request("POST", path + "/role-selection", command);
      if (!current(ticket)) return;
      if (typeof result?.status?.invokable !== "boolean" || typeof result?.status?.consented !== "boolean")
        throw {code:"invalid_response"};
      status = result.status; statusByScope.set(statusKey(scope, path), result.status);
      notice = enabled ? "已为此角色启用屏幕陪伴。"
        : "已为此角色停用屏幕陪伴；本角色的私聊窗口不再提供识屏。";
    } catch (error) {
      if (current(ticket)) notice = errorText(error);
    } finally { if (current(ticket)) { busy = false; render(); } }
  }
  async function upgrade() {
    if (!current(generation) || busy || !status?.upgrade_available || !status.can_manage
        || typeof status.installed_package_version !== "string"
        || typeof status.installed_artifact_hash !== "string"
        || !Number.isInteger(status.installed_revision)) return;
    const ticket = generation;
    const confirmed = await window.CompanionConfirm?.({
      title: "升级屏幕陪伴",
      message: `将屏幕陪伴从 ${status.installed_package_version} 升级到 ${status.package_version}。当前私聊授权会保留；升级期间不会启动 V2。确认继续？`,
      confirmText: "确认升级",
    });
    if (!confirmed || !current(ticket) || busy) return;
    const previous = {
      previous_version: status.installed_package_version,
      previous_hash: status.installed_artifact_hash,
      expected_revision: status.installed_revision,
    };
    busy = true; notice = "正在升级屏幕陪伴…"; render();
    try {
      const result = await product.request("POST", "staging/upgrade-screen", previous);
      if (!current(ticket)) return;
      if (result?.upgraded !== true || result.package_version !== status.package_version) throw {code:"invalid_response"};
      busy = false;
      status = null;
      statusByScope.delete(statusKey(product.scopeKey(), path));
      await load(ticket);
      if (!current(ticket)) return;
      if (status?.package_version === result.package_version && !status.upgrade_available) {
        notice = `屏幕陪伴已升级到 ${result.package_version}。`;
      } else if (!notice) {
        notice = "升级已提交，但新版本状态还未确认；请重新读取后核对。";
      }
    } catch (error) {
      if (current(ticket)) {
        status = null;
        statusByScope.delete(statusKey(product.scopeKey(), path));
        notice = (error?.code === "bridge_timeout" || error?.message === "bridge_timeout")
          ? "没有收到升级回执。请重新读取状态；如果版本已更新，就不需要再次升级。"
          : errorText(error);
      }
    } finally { if (current(ticket)) { busy = false; render(); } }
  }
  function click(event) {
    const action = event.target.closest("[data-screen-access-action]")?.dataset.screenAccessAction;
    if (!action) return;
    if (action === "refresh") { void load(); return; }
    if (action === "retry") { void save(pending?.enabled); return; }
    if (action === "upgrade") { void upgrade(); return; }
    if (action === "role-enable") { void saveRole(true); return; }
    if (action === "role-disable") { void saveRole(false); return; }
    if (action === "enable" || action === "disable") void save(action === "enable");
  }
  function change(event) {
    const select = event.target.closest("[data-screen-private-window]");
    if (busy || !select || !privateWindows().some(target => target.target_id === select.value)) return;
    void product.choose(select.value, product.selection().role_id);
  }
  function unmount() { ++generation; node?.removeEventListener("click", click); node?.removeEventListener("change", change); node = null; path = ""; status = null; pending = null; busy = false; notice = ""; }
  function mount(host) {
    unmount(); node = host; if (!node) return; path = currentPath();
    const scope = product.scopeKey();
    status = statusByScope.get(statusKey(scope, path)) || null;
    pending = pendingByScope.get(scope) || null;
    node.addEventListener("click", click); node.addEventListener("change", change); render(); void load();
  }
  return {mount, unmount};
})();
