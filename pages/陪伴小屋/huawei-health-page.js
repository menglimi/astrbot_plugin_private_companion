/* Huawei Health account binding inside Perception. */
"use strict";
window.CompanionHuaweiHealthPage = (() => {
  const product = window.CompanionProduct;
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const cookieNames = ["CASTGC", "sid", "hwid_cas_sid", "HuaweiID_CAS_ISCASLOGIN", "CASLOGINSITE", "LOGINACCSITE"];
  const labels = {
    bound: "已绑定", unbound: "未绑定", cookie: "Cookie", token: "访问令牌",
    IDLE: "未开始", STARTING: "正在启动浏览器", WAITING: "等待登录", NEED_SMS: "等待手机验证码",
    NEED_CRED: "等待账号密码", NEED_TRUST: "等待信任选择", DONE: "登录成功", FAILED: "登录失败",
  };
  let root = null, status = null, perception = null, perceptionError = "", busy = false, notice = "", generation = 0, pollTimer = null;
  const draft = {
    account: "", password: "", cookieFields: {}, cookieJson: "", sms: "",
    autoTrust: true, preferAuto: false, visibleFallback: false,
  };

  const active = ticket => root?.isConnected && ticket === generation;
  const errorText = failure => {
    const code = failure?.data?.code || failure?.code || failure?.message || "";
    if (code === "huawei_health_cookie_invalid") {
      return `Cookie 导入失败：${String(failure?.reason || "请核对格式与关键项后重试。").slice(0, 300)}`;
    }
    return ({
      dashboard_identity_required: "请从已登录的 AstrBot 管理页面打开陪伴小屋。",
      staging_not_initialized: "角色数据尚未就绪，请先完成账号与会话映射并启动。",
      huawei_health_request_invalid: "请求格式有误，请刷新后重试。",
      huawei_health_status_failed: "运动健康状态暂不可读，请刷新后重试。",
      huawei_health_cookie_failed: "Cookie 导入失败，请核对格式后重试。",
      huawei_health_credentials_failed: "账号密码保存失败；请确认当前 Windows 用户可以使用本机凭据加密。",
      huawei_health_login_failed: "运动健康登录暂时无法启动，请检查浏览器与凭据。",
      huawei_health_fetch_failed: "运动健康取数失败，请重新登录或稍后重试。",
      huawei_health_private_required: "请选择一个已绑定的私聊角色后再绑定运动健康。",
      bridge_timeout: "请求超时；浏览器登录可能需要较长时间，请刷新状态核对。",
    })[code] || String(failure?.message || failure || "操作未完成，请刷新核对。").slice(0, 300);
  };
  function selectedScope() {
    const target = product.roleTarget?.() || product.target?.() || {};
    const role = product.role?.() || {};
    const result = {};
    if (typeof target.target_id === "string" && target.target_id) result.target_id = target.target_id;
    if (typeof role.role_id === "string" && role.role_id) result.role_id = role.role_id;
    return result;
  }
  async function call(method, path, body, timeoutMs = 15000) {
    const payload = method === "POST" ? {...(body || {}), ...selectedScope()} : body;
    try {
      return await product.request(method, "huawei-health/" + path, payload, {timeoutMs});
    } catch (failure) { throw new Error(errorText(failure)); }
  }
  function setNotice(text) { notice = String(text || ""); render(); }
  async function loadPerception() {
    try {
      perception = await product.request("GET", "perception-native");
      perceptionError = "";
    } catch (failure) {
      perception = null;
      perceptionError = errorText(failure);
    }
  }
  async function configurePerception(changes) {
    const current = perception || await product.request("GET", "perception-native");
    await product.request("POST", "perception-native/configure", {
      section: "model",
      command: {
        operation_key: crypto.randomUUID(),
        edit_token: current.model.edit_token,
        changes,
      },
    }, {timeoutMs: 15000});
    perception = await product.request("GET", "perception-native");
    return true;
  }
  async function ensureHealthPerception() {
    try {
      if (perception?.model?.settings?.health_enabled === true) return true;
      await configurePerception({health_enabled: true});
      return true;
    } catch (_) { return false; }
  }
  function fmtLeft(seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value) || value <= 0) return "无 / 已过期";
    if (value >= 3600) return `${Math.floor(value / 3600)} 小时 ${Math.floor((value % 3600) / 60)} 分`;
    return `${Math.max(1, Math.floor(value / 60))} 分钟`;
  }
  function loginState() {
    const login = status?.login || {};
    return labels[login.state] || login.state || "未读取";
  }
  function tokenState() {
    const token = status?.token || {};
    return token.cached ? `有效 · 剩余 ${fmtLeft(token.left)}` : "无有效令牌";
  }
  function perceptionMarkup() {
    const settings = perception?.model?.settings;
    if (!settings) {
      return `<section class="hw-health-switches"><div><h5>对话与主动</h5><p>${esc(perceptionError || "先在「账号与会话」选择一个私聊角色，才能控制健康摘要是否进入对话和主动联系。")} 绑定账号本身不需要选择角色。</p></div></section>`;
    }
    return `<section class="hw-health-switches">
      <div><h5>对话与主动</h5><p>健康数据只在当前私聊角色使用；群聊和其他窗口不会读取。</p></div>
      <label class="hw-health-check"><input type="checkbox" data-hw-perception-field="health_enabled" ${settings.health_enabled === true ? "checked" : ""}> 健康摘要进入当前私聊</label>
      <label class="hw-health-check"><input type="checkbox" data-hw-perception-field="health_proactive_enabled" ${settings.health_proactive_enabled === true ? "checked" : ""}> 允许健康事件参与主动联系</label>
    </section>`;
  }

  function bindingMarkup() {
    const cookie = status?.cookie || {}, credentials = status?.credentials || {}, login = status?.login || {};
    return `<div class="hw-health-status">
      <div><span>绑定状态</span><strong class="${status?.bound ? "is-ok" : "is-warn"}">${status?.bound ? "已保存登录材料" : "尚未绑定"}</strong></div>
      <div><span>Cookie</span><strong>${cookie.exists ? `${Number(cookie.count) || 0} 条` : "无"}</strong></div>
      <div><span>账号密码</span><strong>${credentials.state === "ok" ? "已加密保存" : credentials.state === "unavailable" ? "当前系统不支持" : "未保存"}</strong></div>
      <div><span>访问令牌</span><strong>${tokenState()}</strong></div>
      <div><span>登录会话</span><strong>${esc(loginState())}</strong></div>
      <div><span>浏览器</span><strong>${status?.browser?.found ? "已找到 Edge / Chrome" : "未找到，请填写浏览器路径"}</strong></div>
    </div>
    ${login.message ? `<p class="hw-health-note">${esc(login.message)}</p>` : ""}`;
  }
  function loginMarkup() {
    const login = status?.login || {}, need = login.need || {};
    return `<div class="hw-health-login">
      <div class="hw-health-actions">
        <button class="button small primary" type="button" data-hw-action="login-start" ${busy ? "disabled" : ""}>打开浏览器登录</button>
        <button class="button small" type="button" data-hw-action="login-cancel" ${busy ? "disabled" : ""}>取消登录</button>
        <button class="button small" type="button" data-hw-action="status" ${busy ? "disabled" : ""}>刷新状态</button>
      </div>
      <label class="hw-health-check"><input type="checkbox" data-hw-login-prefer-auto> 使用已保存账号密码无头自动登录</label>
      <label class="hw-health-check"><input type="checkbox" data-hw-login-auto-trust checked> 自动信任此浏览器</label>
      <label class="hw-health-check"><input type="checkbox" data-hw-login-visible-fallback> 自动失败时打开可见窗口</label>
      <div class="hw-health-login-state"><span>${esc(loginState())}</span><small>${login.deadline_left ? `剩余 ${Math.max(0, Number(login.deadline_left))} 秒` : ""}</small></div>
      ${need.sms || /NEED_SMS/.test(login.state || "") ? `<div class="hw-health-inline"><input data-hw-sms placeholder="手机验证码" inputmode="numeric" maxlength="8"><button class="button small" type="button" data-hw-action="login-sms" ${busy ? "disabled" : ""}>提交验证码</button></div>` : ""}
      ${need.trust || /NEED_TRUST/.test(login.state || "") ? `<div class="hw-health-actions">
        <button class="button small" type="button" data-hw-trust="true" ${busy ? "disabled" : ""}>信任此浏览器</button>
        <button class="button small" type="button" data-hw-trust="false" ${busy ? "disabled" : ""}>不信任</button>
        <button class="button small" type="button" data-hw-trust="later" ${busy ? "disabled" : ""}>稍后再说</button>
      </div>` : ""}
    </div>`;
  }
  function summaryMarkup() {
    const latest = status?.latest || {};
    return `<div class="hw-health-summary">
      <div class="hw-health-summary-head"><strong>最近健康摘要</strong><span>${latest.observed_at ? esc(latest.observed_at.replace("T", " ").slice(0, 19)) : "尚未拉取"}</span></div>
      <pre>${esc(latest.summary || "绑定后点「拉取最新健康数据」，这里会显示运动健康中的睡眠、静息心率、HRV 和运动记录。")}</pre>
      <button class="button small primary" type="button" data-hw-action="refresh" ${busy || !status?.bound ? "disabled" : ""}>拉取最新健康数据</button>
    </div>`;
  }
  function render() {
    if (!root) return;
    if (!status) {
      root.innerHTML = `<p class="memory-notice" role="status">正在读取运动健康绑定…</p>`;
      return;
    }
    root.innerHTML = `<section class="hw-health-card">
      <header><div><span class="perception-section-index">HEALTH</span><h4>运动健康</h4><p>当前数据源：华为运动健康。绑定属于当前 AstrBot 安装；健康摘要只在开启「健康感知」的私聊中使用，不进入群聊，也不会修改运动健康账号。</p></div><span class="hw-health-badge ${status.token?.cached ? "is-ok" : "is-warn"}">${status.token?.cached ? "已连接" : status.bound ? "待验证" : "未绑定"}</span></header>
      ${perceptionMarkup()}
      ${bindingMarkup()}
      ${notice ? `<p class="hw-health-note" role="status">${esc(notice)}</p>` : ""}
      <div class="hw-health-grid">
        <section><h5>方式一 · 账号密码登录</h5>
          <p>凭据保存在本机 DPAPI 加密文件中；页面不会回填密码。保存后可无头登录，遇到验证码在这里提交。</p>
          <div class="hw-health-fields"><label>运动健康账号<input data-hw-account autocomplete="username"></label><label>密码<input data-hw-password type="password" autocomplete="new-password"></label></div>
          <div class="hw-health-actions"><button class="button small primary" type="button" data-hw-action="credential-save" ${busy ? "disabled" : ""}>保存账号密码</button><button class="button small" type="button" data-hw-action="credential-clear" ${busy ? "disabled" : ""}>清除凭据</button></div>
          ${loginMarkup()}
        </section>
        <section><h5>方式二 · Cookie 导入</h5>
          <p>从已登录的 <code>health.cloud.huawei.com</code> 导出 Cookie。可填下方六项，或直接粘贴 Cookie-Editor 导出的整段 JSON。</p>
          <p>关键项 <code>CASTGC</code>、<code>sid</code>、<code>hwid_cas_sid</code> 在登录域 <code>.id1.cloud.huawei.com</code> 下，不在 health 域；Cookie-Editor 里要切到该域再导出，只导 health 域会缺项并被拒绝。</p>
          <div class="hw-health-cookie-grid">${cookieNames.map(name => `<label>${esc(name)}<input data-hw-cookie="${esc(name)}" autocomplete="off"></label>`).join("")}</div>
          <textarea data-hw-cookie-json rows="3" placeholder='Cookie-Editor JSON，可留空'></textarea>
          <div class="hw-health-actions"><button class="button small primary" type="button" data-hw-action="cookie-import" ${busy ? "disabled" : ""}>导入 Cookie</button><button class="button small" type="button" data-hw-action="cookie-clear" ${busy ? "disabled" : ""}>清除 Cookie</button></div>
        </section>
      </div>
      ${summaryMarkup()}
      <p class="hw-health-note">账号密码模式和 Cookie 模式二选一即可。token 过期后会自动用已保存的登录材料续期；如果服务要求二次验证，请回来完成验证码。</p>
    </section>`;
    restoreDraft();
  }
  function captureDraft() {
    if (!root) return;
    const value = selector => root.querySelector(selector)?.value;
    const checked = selector => {
      const field = root.querySelector(selector);
      return field ? field.checked === true : undefined;
    };
    const account = value("[data-hw-account]");
    const password = value("[data-hw-password]");
    const cookieJson = value("[data-hw-cookie-json]");
    const sms = value("[data-hw-sms]");
    if (account !== undefined) draft.account = account;
    if (password !== undefined) draft.password = password;
    if (cookieJson !== undefined) draft.cookieJson = cookieJson;
    if (sms !== undefined) draft.sms = sms;
    for (const name of cookieNames) {
      const field = value(`[data-hw-cookie="${name}"]`);
      if (field !== undefined) draft.cookieFields[name] = field;
    }
    const autoTrust = checked("[data-hw-login-auto-trust]");
    const preferAuto = checked("[data-hw-login-prefer-auto]");
    const visibleFallback = checked("[data-hw-login-visible-fallback]");
    if (autoTrust !== undefined) draft.autoTrust = autoTrust;
    if (preferAuto !== undefined) draft.preferAuto = preferAuto;
    if (visibleFallback !== undefined) draft.visibleFallback = visibleFallback;
  }
  function restoreDraft() {
    if (!root) return;
    const setValue = (selector, value) => {
      const field = root.querySelector(selector);
      if (field) field.value = value;
    };
    const setChecked = (selector, value) => {
      const field = root.querySelector(selector);
      if (field) field.checked = value;
    };
    setValue("[data-hw-account]", draft.account);
    setValue("[data-hw-password]", draft.password);
    setValue("[data-hw-cookie-json]", draft.cookieJson);
    setValue("[data-hw-sms]", draft.sms);
    for (const name of cookieNames) setValue(`[data-hw-cookie="${name}"]`, draft.cookieFields[name] || "");
    setChecked("[data-hw-login-auto-trust]", draft.autoTrust);
    setChecked("[data-hw-login-prefer-auto]", draft.preferAuto);
    setChecked("[data-hw-login-visible-fallback]", draft.visibleFallback);
  }
  function onClick(event) {
    const button = event?.target?.closest?.("[data-hw-action],[data-hw-trust]");
    if (!button || (root?.contains && !root.contains(button))) return;
    captureDraft();
    const trust = button.dataset?.hwTrust;
    if (trust !== undefined) { void run("login-trust", trust); return; }
    const action = button.dataset?.hwAction;
    if (action) void run(action);
  }
  function onFormChange(event) {
    captureDraft();
    const field = event?.target?.dataset?.hwPerceptionField;
    if (field) void savePerceptionToggle(field, event.target.checked === true);
  }
  async function savePerceptionToggle(field, value) {
    if (busy) return;
    busy = true; notice = "正在保存健康感知开关…"; render();
    const ticket = generation;
    try {
      await configurePerception({[field]: value});
      if (active(ticket)) notice = value ? "健康感知开关已开启。" : "健康感知开关已关闭。";
    } catch (failure) {
      if (active(ticket)) notice = errorText(failure);
    } finally {
      if (active(ticket)) { busy = false; render(); }
    }
  }
  function resetDraft() {
    draft.account = ""; draft.password = ""; draft.cookieFields = {}; draft.cookieJson = ""; draft.sms = "";
    draft.autoTrust = true; draft.preferAuto = false; draft.visibleFallback = false;
  }
  async function load() {
    const ticket = ++generation;
    try {
      const next = await call("GET", "status");
      if (!active(ticket)) return;
      status = next;
      await loadPerception();
      if (active(ticket)) render();
    } catch (failure) {
      if (!active(ticket)) return;
      root.innerHTML = `<p class="memory-notice" role="status">${esc(failure.message)}</p><button class="button small" type="button" data-hw-action="status">重新读取</button>`;
    }
  }
  async function run(action, choice = "") {
    if (busy) return;
    captureDraft();
    busy = true; notice = ""; render();
    const ticket = generation;
    try {
      if (action === "status") {
        const next = await call("GET", "status");
        if (active(ticket)) status = next;
      } else if (action === "cookie-import") {
        const fields = {};
        for (const name of cookieNames) fields[name] = draft.cookieFields[name] || "";
        const json = draft.cookieJson;
        const result = await call("POST", "cookie/import", {json, fields});
        if (active(ticket)) status = result.status || status;
        notice = "Cookie 已导入。点「拉取最新健康数据」完成 OAuth 并验证取数。";
      } else if (action === "cookie-clear") {
        const result = await call("POST", "cookie/clear", {});
        if (active(ticket)) status = result.status || status;
        notice = "Cookie 已清除。";
      } else if (action === "credential-save") {
        const account = draft.account;
        const password = draft.password;
        if (!account.trim() || !password) throw new Error("账号和密码都不能为空。");
        const result = await call("POST", "credentials", {account, password});
        if (active(ticket)) status = result.status || status;
        draft.password = "";
        notice = "账号密码已加密保存。点「打开浏览器登录」开始授权。";
      } else if (action === "credential-clear") {
        const result = await call("POST", "credentials/clear", {});
        if (active(ticket)) status = result.status || status;
        notice = "账号密码已清除。";
      } else if (action === "login-start") {
        const account = draft.account.trim();
        const password = draft.password;
        if (account && password) {
          const saved = await call("POST", "credentials", {account, password});
          if (active(ticket)) status = saved.status || status;
        } else if (draft.preferAuto && status?.credentials?.state !== "ok") {
          throw new Error("使用无头自动登录前，请先填写并保存运动健康账号密码。");
        }
        await call("POST", "login/start", {
          auto_trust: draft.autoTrust,
          prefer_auto: draft.preferAuto,
          visible_fallback: draft.visibleFallback,
        });
        notice = "登录浏览器已启动。若需要验证码，请在本卡下方提交。";
        startPolling();
      } else if (action === "login-cancel") {
        await call("POST", "login/cancel", {});
        notice = "已取消登录。";
      } else if (action === "login-sms") {
        const code = draft.sms.trim();
        if (!code) throw new Error("请先填写手机验证码。");
        await call("POST", "login/sms", {code});
        draft.sms = "";
        notice = "验证码已提交。";
        startPolling();
      } else if (action === "login-trust") {
        if (!["true", "false", "later"].includes(choice)) throw new Error("信任选择无效，请刷新后重试。");
        await call("POST", "login/trust", {choice});
        notice = "信任选择已提交。";
        startPolling();
      } else if (action === "refresh") {
        notice = "正在通过运动健康取数，首次可能需要 30 秒完成 OAuth…";
        const result = await call("POST", "refresh", {}, 120000);
        if (active(ticket)) status = result.status || status;
        const enabled = await ensureHealthPerception();
        notice = enabled ? "运动健康摘要已更新，并已打开健康感知。" : "运动健康摘要已更新；请打开上方「健康感知」开关。";
      }
      if (action === "status" || action === "cookie-import" || action === "cookie-clear" ||
          action === "credential-save" || action === "credential-clear" || action === "login-cancel" ||
          action === "refresh") {
        const next = await call("GET", "status");
        if (active(ticket)) status = next;
      }
      if (active(ticket)) render();
    } catch (failure) {
      if (active(ticket)) { notice = failure.message; render(); }
    } finally {
      if (active(ticket)) { busy = false; render(); }
    }
  }
  function startPolling() {
    clearInterval(pollTimer);
    let count = 0;
    pollTimer = setInterval(async () => {
      count += 1;
      if (!root?.isConnected || count > 90) { clearInterval(pollTimer); pollTimer = null; return; }
      try {
        const next = await call("GET", "status");
        if (!root?.isConnected) return;
        status = next.status || next; render();
        const state = status.login?.state;
        if (state === "DONE" || state === "FAILED" || state === "IDLE") {
          clearInterval(pollTimer); pollTimer = null;
          if (state === "DONE") {
            await ensureHealthPerception();
            setNotice("登录成功，健康感知已打开。现在可以拉取最新健康数据。");
          }
        }
      } catch (_) { /* keep polling until the bounded count expires */ }
    }, 2000);
  }
  function mount(node) {
    unmount();
    root = node;
    root.addEventListener("click", onClick);
    root.addEventListener("input", onFormChange);
    root.addEventListener("change", onFormChange);
    load();
  }
  function unmount() {
    generation++;
    clearInterval(pollTimer); pollTimer = null;
    root?.removeEventListener?.("click", onClick);
    root?.removeEventListener?.("input", onFormChange);
    root?.removeEventListener?.("change", onFormChange);
    root = null; status = null; perception = null; perceptionError = ""; busy = false; notice = "";
    resetDraft();
  }
  return {mount, unmount};
})();
