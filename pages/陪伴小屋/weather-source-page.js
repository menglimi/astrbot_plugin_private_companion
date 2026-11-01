/* Host-owned weather credentials stay in memory while editing and are never cached. */
"use strict";
window.CompanionWeatherSourcePage = (() => {
  const product = window.CompanionProduct;
  const endpoint = "host-options/weather";
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const providers = {auto:"自动选择（优先和风）",qweather:"和风天气", "open-meteo":"Open-Meteo"};
  const authModes = {
    credential: {
      label: "API Key / 现成 JWT",
      summary: "只填一个凭据；已配置时可留空保留。",
    },
    signed_jwt: {
      label: "Ed25519 私钥自动签发",
      summary: "不填 API Key／JWT；kid、sub 和私钥必须同时提供。",
    },
  };
  const errors = {
    weather_source_invalid:"请按当前认证方式核对：API Key／现成 JWT 只填凭据；私钥签发必须同时填写 kid、sub 和私钥。API Host 与缓存间隔也需符合格式。",
    weather_source_credential_required:"替换环境变量中的 Host 时，请同时填写对应的和风凭据或私钥签发字段。",
    host_options_conflict:"数据源已被其他页面修改。请重新读取后核对并保存。",
    host_options_save_failed:"数据源未能保存，请重试。",
    host_options_save_unavailable:"当前宿主不支持保存插件配置。",
    core_access_required:"当前账号没有管理权限。",
    dashboard_identity_required:"请从已登录的 AstrBot 管理界面打开。",
    staging_not_initialized:"角色数据尚未就绪，请先保存角色映射。",
    staging_host_terminated:"插件已停止，请重新打开插件页面。",
    invalid_response:"数据源配置未能读取，请重新读取。",
  };
  let root = null, status = null, draft = null, busy = false, pending = false, stale = false;
  let generation = 0, abort = null, message = "", leavePrompt = null;
  const askConfirm = options => typeof window.CompanionConfirm === "function"
    ? window.CompanionConfirm(options) : Promise.resolve(false);
  /* 只缓存不含凭据原文的读取状态。编辑中的 credential 永远不会进入这个
     Map；切换角色或会话时使用不同 scope，避免把天气配置串给别的对象。 */
  const statusByScope = new Map();
  const currentScope = () => { try { return product.scopeKey?.() || "default"; } catch (_) { return "default"; } };
  const active = ticket => ticket === generation && root?.isConnected;
  const text = value => String(value ?? "").trim();
  const settingText = (value, name) => text(value?.settings?.[name]);
  const savedAuthMode = value => {
    if (!value) return "credential";
    const settings = value.settings || {};
    return value.credential_kind === "signed_jwt" || settings.has_jwt_private_key
      || settings.jwt_credential_id || settings.jwt_project_id || settings.jwt_developer_id
      ? "signed_jwt" : "credential";
  };
  const selectedAuthMode = () => draft?.auth_mode === "signed_jwt" ? "signed_jwt" : "credential";
  const credentialSummary = value => {
    const kind = {api_key:"API Key",jwt:"现成 JWT",signed_jwt:"自动签发 JWT"}[value.credential_kind] || "凭据";
    const source = {plugin:"页面配置",environment:"环境变量",none:"尚未配置"}[value.credential_source] || "尚未配置";
    return value.has_credential
      ? {kind, source, text:`${source} · ${kind} 已配置`}
      : {kind, source, text:"尚未配置和风凭据"};
  };
  /* has_credential 由宿主按凭据真实可用性判定（环境变量半配置、私钥不可读均为
     false），是「已配置」的唯一依据；字段齐全不等于可用，不能单看 kind/source。 */
  const signedReady = value => Boolean(value?.has_credential)
    && ((value?.credential_kind === "signed_jwt" && value?.credential_source === "environment")
      || Boolean(value?.settings?.jwt_credential_id && value?.settings?.jwt_project_id
        && value?.settings?.has_jwt_private_key));
  const dirty = () => !!draft && (!status
    ? draft.credential !== "" || draft.jwt_private_key !== "" || pending || stale
    : draft.credential !== "" || draft.jwt_private_key !== "" || Object.keys(status.settings).some(key => String(draft[key]) !== String(status.settings[key])));
  async function request(method, body) {
    const bridge = product.bridgeNow() || await product.bridgeFor();
    let timer;
    try {
      const response = await Promise.race([
        method === "GET" ? bridge.apiGet(endpoint) : bridge.apiPost(endpoint, body),
        new Promise((_, reject) => {timer = setTimeout(() => reject(new Error("bridge_timeout")), 15000);}),
      ]);
      const wrapped = ["ok","error"].includes(response?.status) && response?.data !== undefined ? response
        : ["ok","error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
      if (wrapped?.status === "error") throw Object.assign(new Error(wrapped.data?.code || wrapped.message || "invalid_response"), {confirmed:true});
      const value = wrapped ? wrapped.data : response;
      if (!value?.settings || !Object.hasOwn(providers, value.settings.provider)
          || typeof value.settings.api_host !== "string" || !Number.isInteger(value.settings.refresh_minutes)
          || typeof value.edit_token !== "string" || !/^[a-f0-9]{64}$/.test(value.edit_token)
          || typeof value.has_credential !== "boolean") throw new Error("invalid_response");
      return value;
    } finally {clearTimeout(timer);}
  }
  function render() {
    if (!root) return;
    if (!status) {
      root.innerHTML = `<p class="memory-notice" role="status">${esc(message || "正在读取天气数据源…")}</p>${message ? '<button type="button" class="button small" data-weather-source-action="reload">重新读取</button>' : ""}`;
      return;
    }
    const settings = status.settings || {};
    const mode = selectedAuthMode();
    const credentials = credentialSummary(status);
    const modeConfigured = mode === "credential"
      ? status.has_credential && status.credential_kind !== "signed_jwt"
      : signedReady(status);
    const authState = modeConfigured ? "所选方式已配置"
      : status.has_credential ? "当前使用其他方式" : "尚未配置凭据";
    const credentialHint = status.has_credential
      ? `${credentials.text}。留空保留现有凭据；填写新值会替换当前凭据。`
      : "填写 API Key 或现成 JWT，页面会自动识别；JWT 不需要 Bearer 前缀。";
    const environmentNote = status.credential_source === "environment"
      ? "当前凭据来自环境变量；若要修改 Host，需要同时填写一份页面凭据。" : "";
    const openMeteoNote = draft.provider === "open-meteo"
      ? "当前选择 Open-Meteo，和风凭据不会参与实况拉取；切回「和风天气」或「自动选择」后生效。" : "";
    const authCards = Object.entries(authModes).map(([value, item]) => `
      <label class="weather-source-auth-option ${mode === value ? "is-selected" : ""}">
        <input type="radio" name="auth_mode" value="${value}" ${mode === value ? "checked" : ""}>
        <span><strong>${item.label}</strong><small>${item.summary}</small></span>
      </label>`).join("");
    root.innerHTML = `<section class="weather-source-settings" aria-labelledby="weather-source-title">
      <header class="weather-source-heading"><div><h4 id="weather-source-title">数据源与和风凭据</h4><p class="weather-source-intro">先选择数据源，再按一种认证方式填写；不同认证方式不需要同时填满。</p></div><span class="weather-source-state">${status.source_state === "invalid" ? "配置待完善" : `当前 · ${esc(providers[status.actual_provider] || "未就绪")}`}</span></header>
      <form data-weather-source-form>
        <fieldset class="weather-source-grid" ${busy || pending ? "disabled" : ""}>
          <label class="perception-setting" for="weather-source-provider"><strong>天气数据源</strong><select id="weather-source-provider" name="provider">${Object.entries(providers).map(([value,label]) => `<option value="${value}" ${draft.provider === value ? "selected" : ""}>${label}</option>`).join("")}</select><small>自动模式有和风凭据时使用和风，否则使用 Open-Meteo。</small></label>
          <label class="perception-setting" for="weather-source-refresh"><strong>天气缓存刷新间隔（分钟）</strong><input id="weather-source-refresh" name="refresh_minutes" type="number" min="1" max="120" step="1" value="${esc(draft.refresh_minutes)}" required><small>1–120 分钟；控制实况数据源的读取频率。</small></label>
          <label class="perception-setting weather-source-wide" for="weather-source-host"><strong>和风天气 API Host</strong><input id="weather-source-host" name="api_host" type="text" maxlength="253" value="${esc(draft.api_host)}" autocomplete="off" spellcheck="false" placeholder="例如 abc123.re.qweatherapi.com"><small>填写和风控制台分配的专属 Host，也可粘贴 HTTPS Host；不含接口路径。</small></label>
          <section class="weather-source-auth-card" aria-labelledby="weather-source-auth-title">
            <div class="weather-source-auth-heading"><div><h5 id="weather-source-auth-title">和风认证</h5><p>和风实况与官方预警共用这一组凭据。</p></div><span class="weather-source-badge ${modeConfigured ? "is-configured" : ""}">${esc(authState)}</span></div>
            <p class="weather-source-auth-summary">${esc(credentials.text)}。${modeConfigured ? "当前所选方式已配置，可直接留空对应凭据输入框。" : "请完成所选认证方式要求的字段后再保存。"}</p>
            <div class="weather-source-auth-options" role="radiogroup" aria-label="和风认证方式">${authCards}</div>
            <div class="weather-source-auth-panel" ${mode === "credential" ? "" : "hidden"}>
              <label class="perception-setting" for="weather-source-credential"><strong>和风凭据（API Key 或现成 JWT）</strong><input id="weather-source-credential" name="credential" type="password" maxlength="4096" value="${esc(draft.credential)}" autocomplete="new-password" spellcheck="false" placeholder="${status.has_credential ? (status.credential_source === "environment" ? "环境变量已配置，留空继续使用" : "已配置，留空保留现有凭据") : "粘贴 API Key 或现成 JWT"}"><small>${esc(credentialHint)}</small></label>
              ${environmentNote ? `<p class="weather-source-inline-note">${esc(environmentNote)}</p>` : ""}
              <p class="weather-source-inline-note">选择 API Key／现成 JWT 时，下面的 kid、sub、iss 和私钥都不需要填写。</p>
            </div>
            <div class="weather-source-auth-panel" ${mode === "signed_jwt" ? "" : "hidden"}>
              <p class="weather-source-panel-note">选择自动签发时，不需要填写上方 API Key／JWT。kid、sub 与 Ed25519 私钥必须同时提供，iss 可选。</p>
              <div class="weather-source-signed-grid">
                <label class="perception-setting" for="weather-source-jwt-kid"><strong>JWT 凭据 ID（kid）</strong><input id="weather-source-jwt-kid" name="jwt_credential_id" type="text" maxlength="64" value="${esc(draft.jwt_credential_id || "")}" autocomplete="off" spellcheck="false" placeholder="QWeather 控制台的凭据 ID"><small>自动签发时必填。</small></label>
                <label class="perception-setting" for="weather-source-jwt-sub"><strong>JWT 项目 ID（sub）</strong><input id="weather-source-jwt-sub" name="jwt_project_id" type="text" maxlength="64" value="${esc(draft.jwt_project_id || "")}" autocomplete="off" spellcheck="false" placeholder="QWeather 项目 ID"><small>自动签发时必填。</small></label>
                <label class="perception-setting" for="weather-source-jwt-iss"><strong>JWT 开发者 ID（iss，可选）</strong><input id="weather-source-jwt-iss" name="jwt_developer_id" type="text" maxlength="64" value="${esc(draft.jwt_developer_id || "")}" autocomplete="off" spellcheck="false" placeholder="仅项目要求时填写"><small>仅在你的和风项目要求 iss 时填写。</small></label>
                <label class="perception-setting weather-source-private-key" for="weather-source-jwt-key"><strong>Ed25519 私钥（PEM）</strong><textarea id="weather-source-jwt-key" name="jwt_private_key" rows="4" maxlength="16384" autocomplete="off" spellcheck="false" placeholder="${settings.has_jwt_private_key ? "已配置，留空保留现有私钥" : "-----BEGIN PRIVATE KEY-----"}"></textarea><small>保存后不再回显私钥原文；留空表示保留已保存的私钥。</small></label>
              </div>
            </div>
            ${openMeteoNote ? `<p class="weather-source-inline-note">${esc(openMeteoNote)}</p>` : ""}
          </section>
        </fieldset>
        <div class="weather-source-footer"><p>和风实况与官方预警共用这组凭据。保存后页面只读取配置状态，不回显密钥原文。</p><div class="native-perception-actions"><button class="button small" type="button" data-weather-source-action="reload" ${busy ? "disabled" : ""}>重新读取</button>${status.credential_source === "plugin" ? `<button class="button small" type="button" data-weather-source-action="clear" ${busy || pending ? "disabled" : ""}>清除页面凭据</button>` : ""}<button class="button primary small" type="submit" ${busy || pending || stale ? "disabled" : ""}>${busy ? "正在保存…" : "保存数据源"}</button></div></div>
        <p class="perception-save-status" role="status" aria-live="polite">${esc(message)}</p>
      </form>
    </section>`;
  }
  async function load() {
    if (busy) return;
    const ticket = generation;
    const preserveDraft = dirty();
    try {
      const next = await request("GET");
      if (!active(ticket)) return;
      status = next; statusByScope.set(currentScope(), next);
      draft = {...next.settings, credential:"", jwt_private_key:"", auth_mode:savedAuthMode(next)}; pending = stale = false;
      message = next.source_state === "invalid" ? errors.weather_source_invalid : "";
    } catch (failure) {if (active(ticket)) {
      /* 读取失败后不再画上一份数据源与凭据状态；凭据输入若尚未保存则留在
         内存里，等成功重读后再核对，绝不写入缓存。 */
      status = null;
      statusByScope.delete(currentScope());
      if (!preserveDraft) draft = null;
      stale = preserveDraft;
      message = errors[failure.code || failure.message] || errors.invalid_response;
    }}
    if (active(ticket)) render();
  }
  async function save(clear = false) {
    if (!status || busy || pending || stale) return;
    const ticket = generation, changes = {};
    let modeChanged = false;
    if (clear) changes.clear_credentials = true;
    else {
      const mode = selectedAuthMode();
      const previousMode = savedAuthMode(status);
      const credential = text(draft.credential);
      if (mode === "credential" && previousMode === "signed_jwt" && !credential) {
        message = "已切换到 API Key／现成 JWT，请填写新的和风凭据后再保存。";
        render(); return;
      }
      if (mode === "signed_jwt") {
        const kid = text(draft.jwt_credential_id), project = text(draft.jwt_project_id);
        const privateKey = text(draft.jwt_private_key) || status.settings.has_jwt_private_key;
        /* 只有宿主确认可用的环境签名组才能留空保存；半配置或私钥不可读
           （has_credential=false）仍要补齐 kid、sub 和私钥。 */
        const environmentSigned = status.has_credential
          && status.credential_kind === "signed_jwt" && status.credential_source === "environment";
        if (!environmentSigned && (!kid || !project || !privateKey)) {
          message = "私钥自动签发需要同时填写 JWT 凭据 ID（kid）、项目 ID（sub）和 Ed25519 私钥。";
          render(); return;
        }
      }
      for (const name of ["provider","api_host","refresh_minutes"]) {
        const value = name === "refresh_minutes" ? Number(draft[name]) : text(draft[name]);
        if (value !== (status.settings[name] ?? "")) changes[name] = value;
      }
      if (mode === "credential") {
        if (credential) changes.credential = credential;
      } else {
        for (const name of ["jwt_credential_id","jwt_project_id","jwt_developer_id"]) {
          const value = text(draft[name]);
          if (value !== settingText(status, name)) changes[name] = value;
        }
        if (text(draft.jwt_private_key)) changes.jwt_private_key = text(draft.jwt_private_key);
      }
      modeChanged = mode !== previousMode;
      if (!Object.keys(changes).length) {
        message = modeChanged ? "认证方式选择未带来新的配置；请填写对应字段或切换回当前方式。" : "没有需要保存的修改。";
        render(); return;
      }
    }
    busy = true; message = "正在保存数据源…"; render();
    try {
      const next = await request("POST", {edit_token:status.edit_token,changes});
      if (!active(ticket)) return;
      status = next; statusByScope.set(currentScope(), next); draft = {...next.settings,credential:"",jwt_private_key:"",auth_mode:savedAuthMode(next)};
      message = clear ? "页面凭据已清除，当前来源已更新。" : modeChanged ? "认证方式已切换，数据源已保存并立即生效。" : "数据源已保存并立即生效。";
      window.CompanionNativePerceptionPage?.refresh();
      document.dispatchEvent(new CustomEvent("companion:weather-source-updated"));
    } catch (failure) {
      if (!active(ticket)) return;
      if (failure.confirmed) {
        message = errors[failure.code || failure.message] || "保存未完成，请核对配置后重试。";
        stale = (failure.code || failure.message) === "host_options_conflict";
      } else {
        pending = true; draft.credential = "";
        message = "保存结果尚未确认。请重新读取配置核对，再填写需要更新的凭据。";
      }
    } finally {if (active(ticket)) {busy = false; render();}}
  }
  function mount(node) {
    unmount(); root = node; abort = new AbortController();
    const cached = statusByScope.get(currentScope());
    if (cached) { status = cached; draft = {...cached.settings, credential:"", jwt_private_key:"", auth_mode:savedAuthMode(cached)}; }
    root.addEventListener("input", event => {
      if (draft && Object.hasOwn(draft,event.target.name)) draft[event.target.name] = event.target.value;
    }, {signal:abort.signal});
    root.addEventListener("change", event => {
      if (!draft || !Object.hasOwn(draft,event.target.name)) return;
      draft[event.target.name] = event.target.value;
      if (event.target.name === "auth_mode" || event.target.name === "provider") {
        message = "";
        render();
      }
    }, {signal:abort.signal});
    root.addEventListener("submit", event => {if (event.target.matches("[data-weather-source-form]")) {event.preventDefault(); save();}}, {signal:abort.signal});
    root.addEventListener("click", async event => {
      const action = event.target.closest("[data-weather-source-action]")?.dataset.weatherSourceAction;
      if (action === "reload") {
        if (dirty() && !await askConfirm({title:"放弃天气数据源修改",message:"重新读取会清空未保存的数据源修改。",confirmText:"重新读取"})) return;
        load();
      }
      if (action === "clear" && await askConfirm({title:"清除页面凭据",message:"清除页面保存的和风凭据？若存在环境变量凭据，将恢复使用它。",confirmText:"清除凭据",danger:true})) save(true);
    }, {signal:abort.signal});
    render(); load();
  }
  function canLeave() {
    if (busy) return false;
    if (!dirty()) return true;
    if (!leavePrompt) {
      leavePrompt = askConfirm({title:"天气数据源尚未保存",message:"离开此页会丢弃未保存的数据源修改。",confirmText:"离开并丢弃",danger:true})
        .finally(() => { leavePrompt = null; });
    }
    return leavePrompt;
  }
  function unmount() {
    generation++; abort?.abort(); abort = null; root = status = draft = null; leavePrompt = null;
    busy = pending = stale = false; message = "";
  }
  window.addEventListener("beforeunload", event => {if (dirty() || busy) {event.preventDefault(); event.returnValue = "";}});
  return {mount,unmount,canLeave};
})();
