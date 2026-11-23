/* Game permissions follow the selected private conversation. */
"use strict";
window.CompanionGameAccess = (() => {
  const product = window.CompanionProduct;
  const labels = {gomoku:"五子棋", tictactoe:"井字棋", pig_dice:"掷骰竞分", blackjack:"二十一点",
    xiangqi:"中国象棋", turtle_soup:"海龟汤", draw_guess:"你画我猜"};
  const esc = value => String(value ?? "").replace(/[&<>"']/g,
    c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const errors = {game_access_changed:"授权已变化，请重新读取后保存。",
    game_private_required:"先选择一个已绑定的私聊窗口。",
    game_provider_unavailable:"游戏模块尚未加载。",
    game_provider_changed:"游戏版本已变化，请重新读取。",
    capability_not_active:"先启用游戏模块，再允许当前私聊使用。",
    core_access_required:"当前账号没有管理此授权的权限。"};
  const pending = new Map();
  let root, path = "", scope = "", status, generation = 0, busy = false, notice = "";
  const current = ticket => ticket === generation && root?.isConnected && scope === product.scopeKey();
  function privateChoices() {
    const roleId = product.selection?.().role_id,botId=product.selection?.().bot_id;
    return (product.targets?.() || []).filter(target => target.scope_kind === "private"
      && (!botId||target.bot_id===botId) && target.roles?.some(role => role.role_id === roleId));
  }
  function targetLabel(target) {
    return [product.accountLabel?.(target), product.targetLabel?.(target)].filter(Boolean).join(" · ");
  }
  function render() {
    if (!root?.isConnected) return;
    const target = product.target?.(), choices = privateChoices();
    const label = [product.scopeLabel?.(), target && targetLabel(target)].filter(Boolean).join(" · ");
    root.innerHTML = `<section class="extension-live-panel" aria-label="当前私聊游戏权限">
      <h3>当前私聊游戏权限</h3><p>${esc(label || "当前私聊窗口")}</p>
      ${choices.length ? `<label>授权对象 <select data-game-access-target aria-label="选择游戏授权的私聊窗口" ${busy ? "disabled" : ""}>
        ${target?.scope_kind !== "private" ? '<option value="">请选择私聊窗口</option>' : ""}
        ${choices.map(item => `<option value="${esc(item.target_id)}" ${item.target_id === target?.target_id ? "selected" : ""}>${esc(targetLabel(item))}</option>`).join("")}
      </select></label>` : ""}
      <p>允许后，角色可以创建真实棋局和房间，网页与聊天接续同一局。</p>
      ${status ? status.games.map(game => `<div class="dialog-actions"><strong>${labels[game.game_type] || esc(game.game_type)}</strong>
        <span>${game.invokable ? "可调用" : game.consented ? "已允许，功能未就绪" : "未允许"}</span>
        ${status.can_manage ? `<button class="button small" type="button" data-game-access="${esc(game.game_type)}"
          ${busy ? "disabled" : ""}>${pending.has(`${scope}:${game.game_type}`) ? `核对${labels[game.game_type] || esc(game.game_type)}原请求` : `${game.consented ? "撤回" : "允许"}${labels[game.game_type] || esc(game.game_type)}`}</button>` : ""}</div>`).join("") : ""}
      <p role="status" aria-live="polite">${esc(notice || (busy ? "正在读取…" : ""))}</p>
      <button class="button small" type="button" data-game-access="refresh" ${busy ? "disabled" : ""}>重新读取游戏权限</button>
    </section>`;
  }
  async function load() {
    const ticket = generation;
    if (!current(ticket) || busy) return;
    busy = true; notice = ""; render();
    try {
      if (!path || !product.hosted()) throw {code:"game_private_required"};
      const value = await product.request("GET", path);
      if (!current(ticket)) return;
      if (!Array.isArray(value?.games) || typeof value.edit_token !== "string") throw {code:"invalid_response"};
      status = value;
    } catch (error) {
      if (current(ticket)) { status = null; notice = errors[error.code] || "游戏权限读取失败，请重新读取。"; }
    } finally { if (current(ticket)) { busy = false; render(); } }
  }
  async function save(kind) {
    const ticket = generation;
    if (!current(ticket) || busy || !status || !path) return;
    const row = status.games.find(item => item.game_type === kind);
    if (!row || !status.can_manage) return;
    const key = `${scope}:${kind}`;
    const command = pending.get(key) || {game_type:kind, enabled:!row.consented,
      edit_token:status.edit_token, operation_key:`game-${kind}-${crypto.randomUUID()}`};
    pending.set(key, command);
    let refresh = false;
    busy = true; notice = "正在保存…"; render();
    try {
      const result = await product.request("POST", path + "/configure", command);
      if (!Array.isArray(result?.status?.games)) throw {code:"invalid_response"};
      if (pending.get(key) === command) pending.delete(key);
      if (!current(ticket)) return;
      status = result.status;
      notice = command.enabled ? `已允许当前私聊使用${labels[kind]}。` : `已撤回当前私聊的${labels[kind]}授权。`;
      try { await product.refreshIntegrations?.(); } catch (_) {}
      if (current(ticket)) window.CompanionNativeExtensionPanel?.syncRuntime?.("game", result.runtime);
    } catch (error) {
      if (["game_access_changed","game_provider_changed","game_access_request_invalid","capability_not_active"].includes(error.code)) {
        if (pending.get(key) === command) pending.delete(key);
        refresh = ["game_access_changed","game_provider_changed"].includes(error.code);
      }
      if (current(ticket)) notice = errors[error.code] || "保存结果尚未确认，点击同一按钮可核对原请求。";
    } finally { if (current(ticket)) { busy = false; render(); } }
    if (refresh && current(ticket)) await load();
  }
  function click(event) {
    const kind = event.target.closest("[data-game-access]")?.dataset.gameAccess;
    if (kind === "refresh") void load();
    else if (labels[kind]) void save(kind);
  }
  async function change(event) {
    if (!event.target.matches?.("[data-game-access-target]") || busy) return;
    const choice = privateChoices().find(item => item.target_id === event.target.value);
    if (!choice) return;
    const ticket = generation, node = root, roleId = product.selection().role_id;
    busy = true; notice = "正在切换私聊窗口…"; render();
    try {
      if (await product.choose(choice.target_id, roleId, true) === false) {
        if (ticket === generation && root === node) { busy = false; notice = ""; render(); }
        return;
      }
      if (ticket === generation && node?.isConnected && root === node) mount(node);
    } catch (error) {
      if (ticket === generation && root === node) {
        busy = false; status = null; notice = "私聊窗口读取失败，请重新读取。"; render();
      }
    }
  }
  function unmount() {
    ++generation;
    root?.removeEventListener("click", click);
    root?.removeEventListener("change", change);
    root = null; status = null; busy = false; notice = "";
  }
  function mount(node) {
    unmount(); root = node; if (!root) return;
    scope = product.scopeKey();
    const base = (product.windowRole ? product.windowRole() : product.role())?.game_access_api;
    path = base ? product.relative(base) : "";
    root.addEventListener("click", click);
    root.addEventListener("change", change);
    void load();
  }
  function refresh() {
    if (root?.isConnected && !busy) void load();
  }
  return {mount, unmount, refresh};
})();
