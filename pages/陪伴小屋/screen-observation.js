/* Last proactive original and its exact saved analysis; viewing never captures. */
"use strict";
window.CompanionScreenObservation = (() => {
  const product = window.CompanionProduct;
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const states = {succeeded:"识屏完成", failed:"识屏失败", uncertain:"识屏结果未确认", cancelled:"识屏已取消",
    expired:"识屏已超时", dead_letter:"识屏未完成", held:"等待识屏", running:"正在识屏", ready:"等待识屏", not_started:"尚未完成识屏"};
  /* 屏幕域文案与同页「屏幕读取权限」卡保持同一口径。未装配、角色未启用、
     能力暂停都会让读取失败或返回空记录，之前它们全落进「请核对当前私聊授权」
     的兜底句——把没装配说成授权问题。现在这句兜底不再断言原因：表里没有的
     码交给产品总表（CompanionProduct.errorLabel），再没有就只报中性提示并
     带出错误码。 */
  const errors = {core_access_required:"只有核心管理员可以查看屏幕原件与结果。",
    capability_consent_revoked:"当前私聊的屏幕授权已撤回，记录仍保留。",
    screen_read_consent_missing:"当前私聊未授权读取屏幕，记录仍保留。",
    screen_process_consent_missing:"当前私聊未授权处理屏幕画面，记录仍保留。",
    source_revoked:"这次屏幕来源已撤回。", asset_revoked:"这次屏幕原件已撤回。",
    screen_observation_changed:"已取得更新的画面，请重新读取。",
    screen_original_unavailable:"保存的原件当前不可用。", screen_private_required:"请选择已绑定的私聊窗口。",
    screen_capture_not_found:"还没有保存过主动识屏记录。",
    capability_not_installed:"当前尚未装配屏幕陪伴。",
    capability_not_admissible:"屏幕陪伴能力已暂停或移除，需要先恢复能力。",
    capability_not_active:"屏幕陪伴能力已暂停或移除，需要先恢复能力。",
    capability_role_not_enabled:"当前 Bot 的这个人格尚未启用屏幕陪伴。",
    capability_role_disabled:"当前 Bot 的这个人格尚未启用屏幕陪伴。"};
  let node = null, path = "", ticket = 0, scope = "", record = null, original = null, busy = false,
    notice = "", noticeCode = "";
  const current = n => n === ticket && node?.isConnected && scope === product.scopeKey();
  function errorText(code) {
    return errors[code] || product.errorLabel?.(code)
      || `暂时无法查看保存记录${code ? `（${code}）` : ""}，请重新读取。`;
  }
  function time(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "时间未记录" : date.toLocaleString("zh-CN", {hour12:false}); }
  function render() {
    if (!node?.isConnected) return;
    const item = record?.observation, analysis = item?.analysis;
    const video = item?.media_kind === "video", label = video ? "录像" : "截图";
    node.innerHTML = `<section class="screen-observation-panel" aria-label="最近一次主动识屏">
      <header><div><strong>最近一次主动识屏</strong><small>保留原件与对应识屏结果</small></div>
        <button type="button" class="button small" data-screen-review="refresh" ${busy ? "disabled" : ""}>重新读取</button></header>
      ${notice ? `<p role="status">${escape(notice)}</p>` : ""}
      ${noticeCode === "capability_not_installed"
        ? '<div class="dialog-actions"><button type="button" class="button small primary" data-detail="cap-screen">去装配屏幕陪伴</button></div>' : ""}
      ${item ? `<p class="screen-observation-meta">${escape(time(item.captured_at))} · ${escape(label)} · ${item.mode === "diagnostic" ? "真实测试" : "自动观察"}</p>
        <p><strong>${escape(states[analysis?.state] || "识屏结果未确认")}</strong>${analysis?.provider ? ` · ${escape(analysis.provider)}` : ""}</p>
        ${analysis?.text ? `<p class="screen-observation-text">${escape(analysis.text)}</p>` : '<p>本次原件已保留，尚无确认的识屏结果。</p>'}
        ${record.latest_attempt?.task_id !== item.peek_task_id ? '<p>最近一次采集未产生新原件，仍保留上次画面。</p>' : ""}
        <button type="button" class="button small" data-screen-review="original" ${busy ? "disabled" : ""}>${original ? "收起" : "查看"}${label}原件</button>
        ${original ? video ? `<video class="screen-observation-media" controls preload="metadata" src="${escape(original)}"></video>`
          : `<img class="screen-observation-media" src="${escape(original)}" alt="最近一次主动识屏截图原件">` : ""}`
        : !notice ? `<p>${busy ? "正在读取保存记录…" : "尚无已保存的主动识屏原件。"}</p>` : ""}
    </section>`;
  }
  async function load(showOriginal = false) {
    if (busy || !path) return;
    const n = ticket; busy = true; notice = ""; noticeCode = ""; render();
    try {
      if (showOriginal) {
        const captureId = record?.observation?.capture_id;
        if (!captureId) return;
        const value = await product.request("GET", path + "/last-original", {capture_id:captureId});
        if (!current(n)) return;
        const video = record.observation.media_kind === "video";
        if (value.capture_id !== captureId || typeof value.data_url !== "string"
            || !(video ? /^data:video\/(?:mp4|webm);base64,/ : /^data:image\/(?:png|jpeg|webp);base64,/).test(value.data_url)) throw {code:"screen_original_unavailable"};
        original = value.data_url;
      } else {
        original = null;
        const value = await product.request("GET", path + "/last-observation");
        if (!current(n)) return;
        if (value && !value.observation && typeof value.reason === "string" && value.reason) {
          noticeCode = value.reason; notice = errorText(value.reason); record = null;
        } else record = value;
      }
    } catch (error) {
      if (!current(n)) return;
      original = null; record = null;
      noticeCode = typeof error?.code === "string" ? error.code : "";
      notice = errorText(noticeCode);
    } finally { if (current(n)) { busy = false; render(); } }
  }
  function click(event) {
    const action = event.target.closest("[data-screen-review]")?.dataset.screenReview;
    if (busy) return;
    if (action === "refresh") void load();
    else if (action === "original") { if (original) { original = null; render(); } else void load(true); }
  }
  function unmount() { ++ticket; node?.removeEventListener("click", click); node = null; record = null; original = null; busy = false; notice = ""; noticeCode = ""; }
  function mount(host) {
    unmount(); node = host; if (!node) return;
    scope = product.scopeKey();
    try { path = product.relative(product.role()?.screen_access_api || ""); } catch (_) { path = ""; }
    if (!path || product.target()?.scope_kind !== "private") { notice = "请选择已绑定的私聊窗口查看最后一次主动识屏。"; render(); return; }
    node.addEventListener("click", click); render(); void load();
  }
  return {mount, unmount};
})();
