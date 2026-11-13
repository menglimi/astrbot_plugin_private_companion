/* V2-owned perception settings for the selected private target and role. */
"use strict";
window.CompanionNativePerceptionPage = (() => {
  const product = window.CompanionProduct;
  const prefix = "/astrbot_plugin_private_companion/";
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const errors = {
    configuration_revision_conflict:"配置已变化，草稿仍在。刷新后核对，再采用最新版本保存。",
    idempotency_key_conflict:"原请求与当前内容不一致，请刷新后核对。",
    core_access_required:"当前账号没有管理权限。",
    dashboard_identity_required:"请从已登录的管理界面打开陪伴小屋。",
    contact_private_required:"这项设置只适用于私聊对象。",
    staging_not_initialized:"角色数据尚未就绪，请在「账号与会话」保存角色映射；已保存可重新读取状态。",
    staging_host_terminated:"插件已停止，请重新打开插件页面。",
    bridge_timeout:"连接超时，原请求可能已保存；请核对后重试。",
    invalid_response:"配置数据不完整，请刷新核对。",
  };
  /* 后端把机器码放在信封的 message 上（页面桥只转发 message），取码要同时看
     data.code 与 message，不能只认前者。 */
  const CODE = /^[a-z][a-z0-9_]{2,60}$/;
  const codeOf = wrapped => wrapped?.data?.code
    || (CODE.test(wrapped?.message || "") ? wrapped.message : "") || "";
  const newsSourceOptions = [
    ["host", "默认数据源"],
    ["disabled", "停用资讯源"],
    ["bilibili", "B 站动态"],
    ["rss", "RSS / Atom"],
    ["json", "HTTPS JSON"],
    ["search", "网络搜索"],
    ["hot", "热榜"],
  ];
  const fields = {
    environment:[],
    model:[
      ["calendar_enabled","提供当前日期信息","bool","只提供日期背景，不修改角色日程。"],
      ["calendar_timezone","对话日期时区","text","IANA 时区名称。"],
      ["holiday_enabled","提供节假日与工作日","bool","识别节假日与调休；无日历库时按周末判断。"],
      ["holiday_country","节假日识别地区","select","",[["CN","中国大陆"],["WEEKEND_ONLY","仅按周末识别"]]],
      ["lunar_enabled","提供农历日期","bool","本地换算，无需联网。"],
      ["automatic_solar_terms_enabled","提供节气日期","bool","需要已安装节气表。"],
      ["almanac_enabled","提供黄历宜忌","bool","仅提供历法数据中的宜忌。"],
      ["platform_enabled","标明当前平台","bool",""],
      ["model_route_enabled","标明模型路由","bool",""],
      ["configured_modalities_enabled","标明已配置的输入模态","bool","不授予图像、语音或视频权限。"],
      ["guidance","感知表达偏好","textarea","最多 2 KiB。"],
    ],
    news:[
      ["news_share_enabled","允许分享外部资讯","bool","只会考虑有来源、时间和链接的实际条目。"],
      ["news.enabled","允许资讯作为联系契机","bool","仍受主动联系总开关与免打扰约束。"],
      ["news_check_interval_minutes","资讯检查间隔（分钟）","number","15 至 1440 分钟。"],
      ["news_source_kind","资讯来源","select","B 站动态使用发布者动态；RSS / JSON 必须是 HTTPS 订阅地址；网络搜索按「网络探索」的关注方向检索。",newsSourceOptions],
      ["news_source_uid","B 站 UID","text","仅在来源选择 B 站动态时填写十进制 UID。"],
      ["news_source_url","RSS / JSON 地址","text","仅在来源选择 RSS / Atom 或 HTTPS JSON 时填写地址。"],
    ],
  };
  let nodes = null, path = "", scope = "", status = null, drafts = {}, notices = {}, busy = false;
  let generation = 0, abort = null;
  let refreshTimer = null;
  const subscribers = new Set();
  /* 工作区会随路由卸载，已读取的配置与未提交的普通字段草稿按角色范围
     留在当前页面内存。这里不写 localStorage，也不缓存天气凭据（本页没有
     凭据字段）；宿主返回新 revision 后 merge 会自然替换对应快照。 */
  const stateByScope = new Map();
  const cacheKey = () => `${scope}:${path}`;
  function rememberState() {
    if (status) stateByScope.set(cacheKey(), {status, drafts});
  }
  const active = ticket => ticket === generation && nodes?.environment?.isConnected && scope === product.scopeKey();
  const error = (code, confirmed = false) => Object.assign(new Error(errors[code] || "操作未完成，请刷新核对。"), {code, confirmed});

  /* 同 memory-page.js / state-page.js：桥要插件相对路径，切掉插件目录名。 */
  function relative(value) {
    if (typeof value !== "string" || !value.startsWith(prefix)) throw error("invalid_response");
    const route = value.slice(prefix.length);
    if (!route || /[?#%\\:]/.test(route) || route.split("/").some(part => !part || part === "." || part === "..")) throw error("invalid_response");
    return route;
  }
  async function api(method, body) {
    let timer;
    try {
    /* 等桥就绪，而不是当场读 window.AstrBotPluginPage。
       主页面此前不引页面桥 SDK，桥是在 product.connect() 里动态补上的；
       这一页若在它补好之前就发请求，读到的是 undefined，用户看到的是
       「未连接」——一个只在几百毫秒窗口里出现的假故障。 */
      const bridge = window.CompanionProduct.bridgeNow() || await window.CompanionProduct.bridgeFor();
      const suffix = arguments.length > 2 ? arguments[2] : (method === "GET" ? "" : "/configure");
      const call = method === "GET" ? bridge.apiGet(path + suffix) : bridge.apiPost(path + suffix, body);
      const response = await Promise.race([call, new Promise((_, reject) => {
        timer = setTimeout(() => reject(error("bridge_timeout")), 15000);
      })]);
      const wrapped = ["ok","error"].includes(response?.status) && response?.data !== undefined ? response
        : ["ok","error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
      if (wrapped?.status === "error") throw error(codeOf(wrapped) || "invalid_response", true);
      const value = wrapped ? wrapped.data : response;
      if (!value || typeof value !== "object" || Array.isArray(value)) throw error("invalid_response");
      return value;
    } finally { clearTimeout(timer); }
  }
  function checked(value) {
    const environmentFields=["planning_timezone","planning_annual_calendars","planning_weather_enabled",
      "planning_weather_location","planning_weather_max_age_minutes"];
    return environmentFields.every(name=>typeof value?.environment?.settings?.[name]
        ===(name==="planning_weather_enabled"?"boolean":name==="planning_weather_max_age_minutes"?"number":"string"))
      &&value?.environment?.sources&&["model","news"].every(section => value?.[section]?.settings
      &&(value.environment.sources.weather_provider === undefined
        || typeof value.environment.sources.weather_provider === "string")
      && typeof value[section].edit_token === "string"
      && fields[section].every(([name, , type]) =>
        ((name === "health_enabled" || name === "health_proactive_enabled") && value[section].settings[name] === undefined)
        || typeof value[section].settings[name] === (type === "bool" ? "boolean" : type === "number" ? "number" : "string")));
  }
  function publicSnapshot() {
    if (!status) return {ready:false, loading:!notices.load, error:notices.load || ""};
    const environment = status.environment.settings || {}, model = status.model.settings || {};
    const contactStatus = status.contact || {};
    const contact = contactStatus.settings || {};
    let locationName = "";
    try { locationName = JSON.parse(environment.planning_weather_location || "{}").name || ""; } catch (_) {}
    return {
      ready:true,
      environment:{weatherReady:!!status.environment.sources?.weather_ready,
        alertsReady:!!status.environment.sources?.alerts_ready, locationName,
        weatherEnabled:environment.planning_weather_enabled === true,
        timezone:environment.planning_timezone || ""},
      model:{calendarEnabled:model.calendar_enabled === true, timezone:model.calendar_timezone || "",
        healthEnabled:model.health_enabled === true,
        lunarEnabled:model.lunar_enabled === true, solarTermsEnabled:model.automatic_solar_terms_enabled === true,
        almanacEnabled:model.almanac_enabled === true, platformEnabled:model.platform_enabled === true,
        routeEnabled:model.model_route_enabled === true, modalitiesEnabled:model.configured_modalities_enabled === true},
      contact:{dateCheckinEnabled:contact.date_checkin_enabled === true,
        dates:typeof contactStatus.dates === "string" ? contactStatus.dates
          : typeof contact.important_dates === "string" ? contact.important_dates : "[]",
        lookaheadDays:Number.isInteger(contactStatus.lookaheadDays)
          ? contactStatus.lookaheadDays : Number.isInteger(contact.important_date_lookahead_days)
            ? contact.important_date_lookahead_days : 0,
        timezone:contact.quiet_timezone || "",
        balanceAwarenessEnabled:contact.balance_awareness_enabled === true,
        automaticAllowed:status.contact?.effective?.automatic_allowed === true},
      news:{ready:!!status.news.source?.ready, assembled:!!(status.news.source?.assembled ?? status.news.source?.ready),
        label:status.news.source?.label || "",
        sourceKind:status.news.source?.kind || contact.news_source_kind || "",
        networkStatus:status.news.diagnostic?.network_status || "not_checked",
        itemCount:Number(status.news.diagnostic?.item_count) || 0,
        feedItems:Array.isArray(status.news.diagnostic?.items) ? status.news.diagnostic.items.slice(0, 32) : [],
        explorationEnabled:contact.web_exploration_enabled === true,
        explorationInterests:contact.web_exploration_interests || "",
        explorationIntervalHours:contact.web_exploration_min_interval_hours ?? null,
        explorationResultCount:contact.web_exploration_result_count ?? null,
        externalSelfLinkEnabled:contact.external_event_self_link_enabled === true,
        lastAttemptAt:status.news.diagnostic?.last_attempt_at || "",
        lastCheckedAt:status.news.diagnostic?.last_checked_at || "",
        lastSuccessAt:status.news.diagnostic?.last_success_at || status.news.diagnostic?.last_checked_at || "",
        failureCode:status.news.diagnostic?.failure?.code || ""},
    };
  }
  function publishStatus() {
    const snapshot = publicSnapshot();
    for (const listener of [...subscribers]) {
      try { listener(snapshot); } catch (_) {}
    }
  }
  function values(_section, data) { return {...data.settings}; }
  function merge(next, force = "") {
    if (!checked(next)) throw error("invalid_response");
    if (next.model?.settings && typeof next.model.settings.health_enabled !== "boolean")
      next.model.settings.health_enabled = false;
    if (next.model?.settings && typeof next.model.settings.health_proactive_enabled !== "boolean")
      next.model.settings.health_proactive_enabled = false;
    status = next;
    for (const section of Object.keys(fields)) {
      const current = drafts[section], entry = next[section], latest = values(section, entry);
      if (!current || section === force || (!current.touched.size && !current.pending)) {
        drafts[section] = {values:{...latest}, original:latest, token:entry.edit_token,
          touched:new Set(), stale:false, pending:null};
      } else if (current.token !== entry.edit_token) {
        current.stale = true;
      }
    }
    rememberState();
    publishStatus();
  }
  function field(section, spec, draft) {
    const [name, label, type, hint, options] = spec, value = draft.values[name];
    const id = "native-" + section + "-" + name.replaceAll(".", "-");
    const common = `id="${id}" data-native-field="${esc(name)}"`;
    if (type === "bool") return `<div class="perception-setting perception-setting-toggle"><span><strong>${label}</strong>${hint ? `<small>${hint}</small>` : ""}</span><label class="model-switch"><input ${common} type="checkbox" ${value ? "checked" : ""}><i></i></label></div>`;
    if (type === "select") return `<label class="perception-setting" for="${id}"><strong>${label}</strong><select ${common}>${(options || []).map(([option, text]) => `<option value="${esc(option)}" ${value === option ? "selected" : ""}>${esc(text)}</option>`).join("")}</select>${hint ? `<small>${hint}</small>` : ""}</label>`;
    const control = type === "textarea"
      ? `<textarea ${common} rows="3" spellcheck="false">${esc(value)}</textarea>`
      /* 只有纬度经度容忍小数；其余整数字段必须 step=1，
         否则浏览器会放过 12.5 这类值，到后端才被拒。 */
      : `<input ${common} type="${type === "number" ? "number" : "text"}" value="${esc(value)}" ${type === "number" ? `step="${name.startsWith("location_") ? "any" : "1"}"` : 'autocomplete="off"'}>`;
    return `<label class="perception-setting" for="${id}"><strong>${label}</strong>${control}${hint ? `<small>${hint}</small>` : ""}</label>`;
  }
  function context(section) {
    if (section === "environment") {
      const sources = status.environment.sources || {};
      const providers = {qweather:"和风天气", "open-meteo":"Open-Meteo"};
      const provider = providers[sources.weather_provider] || "自动选择";
      return `<div class="native-perception-meta"><span>${sources.weather_ready ? `当前实况来源 · ${provider}` : "实况天气源尚未就绪"}</span><span>${sources.alerts_ready ? "官方预警源已就绪 · 和风天气" : "官方预警源尚未就绪 · 需和风凭据"}</span></div>`;
    }
    if (section === "model" || section === "calendar") return "";
    const news = status.news, source = news.source || {};
    const diagnostic = news.diagnostic || {}, failure = diagnostic.failure || {};
    const assembled = source.assembled ?? source.ready;
    const sourceText = assembled ? `资讯源已装配 · ${esc(source.label || "未命名")}` : "资讯源未装配";
    const networkStatus = diagnostic.network_status || (failure.code ? "failed"
      : diagnostic.last_checked_at ? "succeeded" : assembled ? "not_checked" : "not_configured");
    const checkText = networkStatus === "succeeded"
      ? `网络检查通过 · ${esc(diagnostic.last_checked_at || "")} · ${Number(diagnostic.item_count) || 0} 条`
      : networkStatus === "failed" ? `最近尝试失败${diagnostic.last_attempt_at ? ` · ${esc(diagnostic.last_attempt_at)}` : ""}`
      : networkStatus === "not_configured" ? "网络状态：未配置资讯源" : "后台维护：等待首次同步 · 网络状态：尚未检查";
    const successText = networkStatus === "failed" && diagnostic.last_checked_at
      ? `上次成功检查 ${esc(diagnostic.last_checked_at)}`
      : failure.code ? "最近检查未完成，请核对资讯来源与连接。" : "";
    return `<div class="native-perception-meta"><span>${sourceText}</span><span>${checkText}</span>${successText ? `<span>${successText}</span>` : ""}<span>${news.contact_enabled ? "主动联系总开关已开启" : "主动联系总开关已关闭"}</span><span>${news.automatic_enabled ? "后台联系已开启" : "后台联系已关闭"}</span></div><p class="perception-note">检查来源不会发送消息。</p><button class="button small" type="button" data-native-action="diagnose" data-native-section="news" ${busy ? "disabled" : ""}>检查来源</button>`;
  }
  function feedMarkup(news) {
    const diagnostic = news?.diagnostic || {}, items = Array.isArray(diagnostic.items) ? diagnostic.items.slice(0, 8) : [];
    const label = news?.source?.label || "外部资讯源";
    const isDaily = /AI\s*(早报|日报)|橘鸦/i.test(label);
    const last = diagnostic.last_success_at || diagnostic.last_checked_at || "";
    const lastText = last ? `最近同步 ${esc(last)}` : "等待后台首次同步";
    const itemMarkup = items.length ? `<ol class="native-news-feed-list">${items.map(item => {
      const published = typeof item.published_at === "string" ? item.published_at.slice(0, 16).replace("T", " ") : "日期待核对";
      return `<li><a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer"><strong>${esc(item.title || "未命名资讯")}</strong><span>${esc(item.source || label)} · ${esc(published)}</span><p>${esc(item.summary || "暂无摘要")}</p></a></li>`;
    }).join("")}</ol>` : `<p class="memory-notice native-news-feed-empty">${diagnostic.network_status === "failed" ? "最近同步失败；后台会按间隔自动重试。" : "后台正在维护来源，首次同步完成后会在这里显示。"}</p>`;
    return `<section class="native-news-feed" aria-label="已获取资讯"><header><div><span class="perception-section-index">PERSISTED FEED</span><h4>${isDaily ? "AI 日报" : "已获取资讯"}</h4><p>${esc(label)} · ${lastText} · 已保留 ${items.length} 条</p></div><span class="native-news-feed-state">后台自动维护</span></header>${itemMarkup}</section>`;
  }
  function renderSection(section) {
    const node = nodes?.[section]; if (!node) return;
    if (!product.hosted()) { node.innerHTML = '<p class="memory-notice" role="status">角色与会话目录暂未读到。</p><div class="native-perception-actions"><button class="button small" type="button" data-workspace-retry>重新读取</button></div>'; return; }
    if (!path) { node.innerHTML = '<p class="memory-notice">请选择有管理权限的私聊对象和角色。</p>'; return; }
    if (!status) {
      node.innerHTML = `<p class="memory-notice" role="status">${esc(notices.load || "正在读取当前配置…")}</p><button class="button small" type="button" data-native-action="refresh">重新读取</button>`;
      return;
    }
    if (section === "environment") {
      const settings=status.environment.settings||{};
      let location={};
      try {location=JSON.parse(settings.planning_weather_location||"{}");} catch (_) {}
      if(!location||typeof location!=="object"||Array.isArray(location))location={};
      node.innerHTML=`${context(section)}<div class="native-environment-summary"><dl>
        <div><dt>角色所在地</dt><dd>${esc(location.name||"尚未设置")}</dd></div>
        <div><dt>角色天气</dt><dd>${settings.planning_weather_enabled?"已开启":"已关闭"} · 同一角色的私聊共用</dd></div>
        <div><dt>天气信息时效</dt><dd>${esc(settings.planning_weather_max_age_minutes)} 分钟</dd></div>
      </dl>
        <div class="native-perception-actions"><button class="button small" type="button" data-shell-furniture="calendar">打开日程与规划</button><button class="button small" type="button" data-native-action="refresh">刷新状态</button></div></div>`;
      return;
    }
    const sourceSection=section==="calendar"?"model":section;
    const draft = drafts[sourceSection], pending = !!draft.pending;
    const message = draft.stale ? errors.configuration_revision_conflict : notices[sourceSection] || "";
    if (section === "news") {
      node.innerHTML = `${context(section)}${feedMarkup(status.news)}<div class="native-perception-actions"><button class="button small" type="button" data-native-action="refresh" ${busy ? "disabled" : ""}>刷新状态</button></div><p class="perception-save-status" role="status" aria-live="polite">${esc(message)}</p>`;
      return;
    }
    const specs=sourceSection==="model"?(section==="calendar"?fields.model.slice(0,7):fields.model.slice(7)):fields[section];
    const title=section==="calendar"?"日期与历法":({model:"来源标识与表达偏好"})[section];
    const formMarkup=`<form class="perception-settings-form native-perception-form" data-native-section="${sourceSection}">
      <div class="perception-settings-heading"><div><h4>${title}</h4></div>
      <button class="button small" type="submit" ${busy ? "disabled" : ""}>${pending ? "核对原保存" : "保存设置"}</button></div>
      <fieldset class="perception-settings-grid native-perception-fieldset" ${pending || busy ? "disabled" : ""}>${specs.map(spec => field(sourceSection, spec, draft)).join("")}</fieldset>
      <p class="perception-save-status ${draft.stale ? "native-perception-warning" : ""}" role="status" aria-live="polite">${esc(message)}</p>
      <div class="native-perception-actions"><button class="button small" type="button" data-native-action="refresh" ${busy ? "disabled" : ""}>刷新</button>${draft.stale ? `<button class="button small" type="button" data-native-action="rebase" data-native-section="${sourceSection}" ${busy ? "disabled" : ""}>采用最新版本</button>` : ""}</div>
    </form>`;
    node.innerHTML = `${context(section)}<details class="perception-settings-disclosure" open><summary><span>${title}</span></summary>${formMarkup}</details>`;
  }
  function render() { for (const section of ["environment","calendar","model","news"]) renderSection(section); }
  async function load() {
    if (!path || busy) return;
    const ticket = ++generation;
    notices.load = "";
    if (!status) render();
    try { const next = await api("GET"); if (active(ticket)) {
      merge(next);
      notices.load = "";
      for (const section of Object.keys(fields)) notices[section] = "";
      render();
    } }
    catch (failure) { if (active(ticket)) {
      notices.load = errors[failure.code || failure.message] || "读取失败，请重试。";
      /* 读取失败时清掉旧状态，避免把上一份环境、模型和资讯配置继续画成
         当前生效值。输入过但尚未保存的草稿留在内存，并在下一次成功读取时由
         merge() 按令牌核对；没有 status 时 publicSnapshot() 也会明确 ready:false。 */
      status = null;
      stateByScope.delete(cacheKey());
      for (const draft of Object.values(drafts)) {
        if (draft && (draft.touched?.size || draft.pending)) draft.stale = true;
      }
      for (const section of Object.keys(fields)) notices[section] = notices.load;
      publishStatus();
      render();
    } }
  }
  function timezone(value) {
    try { new Intl.DateTimeFormat("zh-CN", {timeZone:value}).format(); return true; } catch (_) { return false; }
  }
  function patch(section, draft) {
    const changes = {};
    for (const [name, , type] of fields[section]) {
      if (!draft.touched.has(name)) continue;
      let value = draft.values[name];
      if (type === "number") value = Number(value);
      if (value !== draft.original[name]) changes[name] = value;
    }
    if (section === "model") {
      if (changes.calendar_timezone && !timezone(changes.calendar_timezone)) throw new Error("请填写有效的对话日期时区。");
      if (typeof changes.guidance === "string" && new TextEncoder().encode(changes.guidance).length > 2048)
        throw new Error("感知表达偏好不能超过 2 KiB。");
    }
    if (section === "news" && Object.hasOwn(changes, "news_check_interval_minutes")
        && (!Number.isInteger(changes.news_check_interval_minutes) || changes.news_check_interval_minutes < 15
            || changes.news_check_interval_minutes > 1440)) throw new Error("资讯检查间隔应为 15 至 1440 分钟的整数。");
    if (section === "news") {
      const kind = changes.news_source_kind ?? draft.original.news_source_kind;
      const uid = String(changes.news_source_uid ?? draft.values.news_source_uid ?? "").trim();
      const url = String(changes.news_source_url ?? draft.values.news_source_url ?? "").trim();
      if (kind === "bilibili" && !/^[1-9][0-9]{0,19}$/.test(uid)) throw new Error("B 站来源需要填写十进制 UID。");
      if ((kind === "rss" || kind === "json") && !/^https:\/\/[^\s#]+$/i.test(url)) throw new Error("RSS / JSON 来源需要填写 HTTPS 地址。");
    }
    return changes;
  }
  async function save(section) {
    if (!path || !status || busy) return;
    const draft = drafts[section];
    if (draft.stale) { notices[section] = errors.configuration_revision_conflict; renderSection(section); return; }
    if (!draft.pending) {
      let changes;
      try { changes = patch(section, draft); } catch (failure) { notices[section] = failure.message; renderSection(section); return; }
      if (!Object.keys(changes).length) { notices[section] = "没有待保存的修改。"; renderSection(section); return; }
      draft.pending = {operation_key:crypto.randomUUID(), edit_token:draft.token, changes};
    }
    const ticket = generation;
    busy = true; notices[section] = "正在保存…"; render();
    try {
      const response = await api("POST", {section, command:draft.pending});
      if (!active(ticket)) return;
      if (!response.status || !checked(response.status)) throw error("invalid_response");
      merge(response.status, section);
      const setup = response.result?.health_setup;
      notices[section] = setup?.state === "warning"
        ? "已保存；" + (setup.message || "健康感知尚未完全就绪。")
        : setup?.enabled === true ? "已保存，健康感知已开启。" : "已保存。";
    } catch (failure) {
      if (!active(ticket)) return;
      if (failure.confirmed) {
        draft.pending = null;
        if (failure.code === "configuration_revision_conflict") draft.stale = true;
      }
      notices[section] = errors[failure.code || failure.message] || "保存未完成，请核对原请求。";
    } finally { if (active(ticket)) { busy = false; render(); } }
  }
  async function diagnose() {
    if (!path || !status || busy) return;
    const ticket = generation;
    busy = true; notices.news = "正在检查资讯源…"; render();
    try {
      const response = await api("POST", {}, "/news-diagnostic");
      if (!active(ticket)) return;
      if (!response.status || !checked(response.status)) throw error("invalid_response");
      merge(response.status);
      notices.news = response.diagnostic?.failure ? "检查完成，来源返回错误。" : "检查完成。";
    } catch (failure) {
      if (active(ticket)) notices.news = errors[failure.code || failure.message] || "来源检查失败，请重试。";
    } finally { if (active(ticket)) { busy = false; render(); } }
  }
  function rebase(section) {
    const draft = drafts[section], latest = values(section, status[section]);
    for (const [name] of fields[section]) if (!draft.touched.has(name)) draft.values[name] = latest[name];
    draft.original = latest;
    draft.token = status[section].edit_token;
    draft.stale = false;
    draft.pending = null;
    notices[section] = "草稿仍在；请核对后保存。";
    renderSection(section);
  }
  function mount(places) {
    unmount(); nodes = places; scope = product.scopeKey();
    abort = new AbortController();
    try { path = product.hosted() && product.role()?.perception_native_api
      ? relative(product.role().perception_native_api) : ""; }
    catch (failure) { path = ""; notices.load = failure.message; }
    const cached = path ? stateByScope.get(cacheKey()) : null;
    if (cached?.status && cached?.drafts) {
      status = cached.status;
      drafts = cached.drafts;
      publishStatus();
    }
    for (const [nodeName, section] of [["environment","environment"],["calendar","model"],["model","model"],["news","news"]]) {
      const node = nodes?.[nodeName]; if (!node) continue;
      node.addEventListener("input", event => {
        const input = event.target.closest("[data-native-field]");
        if (!input || !drafts[section]) return;
        const name = input.dataset.nativeField;
        drafts[section].values[name] = input.type === "checkbox" ? input.checked : input.value;
        drafts[section].touched.add(name);
        notices[section] = "";
      }, {signal:abort.signal});
      node.addEventListener("change", event => {
        const input = event.target.closest("[data-native-field]");
        if (!input || !drafts[section]) return;
        drafts[section].values[input.dataset.nativeField] = input.type === "checkbox" ? input.checked : input.value;
        drafts[section].touched.add(input.dataset.nativeField);
      }, {signal:abort.signal});
      node.addEventListener("submit", event => { event.preventDefault(); save(section); }, {signal:abort.signal});
      node.addEventListener("click", event => {
        const action = event.target.closest("[data-native-action]")?.dataset.nativeAction;
        if (action === "refresh") load();
        if (action === "rebase") rebase(section);
        if (action === "diagnose" && section === "news") diagnose();
      }, {signal:abort.signal});
    }
    render(); load();
    /* 后台同步独立于页面操作：来源抓取完成后，当前打开的页面也应自动看到
       最新日报/资讯，不能要求用户手动刷新整个拓展页。页面隐藏时暂停轮询，
       重新回到前台后立即读取一次。 */
    const schedule = typeof window.setInterval === "function" ? window.setInterval.bind(window)
      : (typeof setInterval === "function" ? setInterval : null);
    if (schedule && typeof document !== "undefined") refreshTimer = schedule(() => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      if (busy) return;
      load();
    }, 30000);
    const onVisibility = () => {
      if (document.visibilityState === "visible" && !busy) load();
    };
    if (typeof document !== "undefined" && typeof document.addEventListener === "function")
      document.addEventListener("visibilitychange", onVisibility, {signal:abort.signal});
  }
  function unmount() {
    generation++; abort?.abort(); abort = null;
    if (refreshTimer !== null) {
      const cancel = typeof window.clearInterval === "function" ? window.clearInterval.bind(window)
        : (typeof clearInterval === "function" ? clearInterval : null);
      if (cancel) cancel(refreshTimer);
      refreshTimer = null;
    }
    nodes = null; path = ""; scope = ""; status = null; drafts = {}; notices = {}; busy = false;
    publishStatus();
  }
  /* 页面级未保存守卫（page-guards.js）用：只要有一段还有改过但没提交的字段就算脏。
     感知页的草稿按分段存在内存里，切分段能留住，切页就会随工作区卸载——
     所以离开前必须问一声。 */
  function isDirty() {
    return Object.values(drafts || {}).some(draft => {
      if (!draft || !draft.touched || !draft.touched.size) return false;
      return [...draft.touched].some(name => String(draft.values?.[name]) !== String(draft.original?.[name]));
    });
  }
  function changedFields() {
    return Object.entries(drafts).flatMap(([section, draft]) => (fields[section] || [])
      .filter(([name]) => draft.touched?.has(name) && String(draft.values?.[name]) !== String(draft.original?.[name]))
      .map(([, label]) => label));
  }
  async function saveBeforeLeave() {
    for (const [section, draft] of Object.entries(drafts)) {
      if (![...(draft.touched || [])].some(name => String(draft.values[name]) !== String(draft.original[name]))) continue;
      if (draft.pending) return {error:'上次保存结果待核对，请在感知配置中核对原请求。'};
      await save(section);
      if (drafts[section]?.pending || drafts[section]?.stale) return {error:notices[section] || '保存尚未确认。'};
    }
    return !isDirty() ? true : {error:Object.values(notices).find(Boolean) || '感知配置未保存，请回到配置中检查。'};
  }
  function discardChanges() {
    if (busy) return {error:'感知配置正在处理，请稍后切换。'};
    for (const draft of Object.values(drafts)) {
      draft.values = {...draft.original}; draft.touched = new Set();
    }
    rememberState(); render();
    return true;
  }
  /* 全局搜索用：感知页自己的字段表（日期、节假日、农历、黄历、平台标识等）。
     这一组不在 config-editor 的 spec 里，此前整组搜不到。
     「资讯」段的字段其实是主动联系的 news.*，spec 里已经能搜到，这里不再重复索引。 */
  const sectionTitles = {environment: "环境", model: "模型"};
  function searchEntries() {
    return Object.entries(fields).filter(([section]) => sectionTitles[section]).flatMap(([section, list]) => (list || []).map(([field, label, , help]) => ({
      group: "conversation.perception", groupTitle: "感知", field,
      label: label || field, help: help || "",
      section, sectionTitle: sectionTitles[section],
      sectionCount: Object.keys(sectionTitles).length,
    })));
  }
  return {mount, unmount, refresh:load, searchEntries, isDirty, changedFields, saveBeforeLeave, discardChanges, subscribe(listener) {
    if (typeof listener !== "function") return () => {};
    subscribers.add(listener); listener(publicSnapshot());
    return () => subscribers.delete(listener);
  }};
})();
