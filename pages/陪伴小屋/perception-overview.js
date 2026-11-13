"use strict";
window.CompanionPerceptionOverviewPage = (() => {
  const product = window.CompanionProduct;
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  const modelAvailabilityLabel = value => ({ready:"已就绪",disabled:"已关闭",missing:"尚未配置",unavailable:"暂不可用",
    unknown:"状态待核对",invalid:"配置待核对",failed:"读取失败",unsupported:"暂不支持"})[value] || "状态待核对";
  let root = null, scope = "", snapshot = null, runtimeSnapshot = null, runtimeState = "loading";
  let modelStatus = null, modelState = "loading", voiceStatus = null, voiceState = "loading";
  let imageStatus = null, imageState = "loading", semanticStatus = null, semanticState = "loading";
  let recallStatus = null, recallState = "loading", recallError = "", mihomeStatus = null, mihomeState = "loading";
  let huaweiHealth = null, huaweiHealthState = "loading", huaweiHealthError = "";
  let materialPool = null, materialPoolState = "loading", materialPoolError = "";
  let itemPreviewMarkup = new Map(), activePreviewItemId = "";
  let balanceConfig = null, balanceDraft = null, balanceExpanded = false, balanceConfigLoading = false;
  let balanceWorkspace = null, balanceMarkup = "", selectedDestination = "", selectedModelTab = "", selectedItemId = "";
  let balanceStatus = null, balanceStatusLoading = false, balanceStatusRefreshQueued = false, balanceStatusError = "";
  let balanceSaving = false, balanceTesting = false, balanceTestResult = null;
  let balanceResultScope = "", balanceResultConfigToken = "", balanceResultConfigKey = "", balanceResultIsDraft = false;
  let balanceMessage = "", balanceError = "", balanceTestError = "";
  let unsubscribe = null, clockTimer = null, abort = null, generation = 0, lastDay = "";
  /* 感知页每次换路由都会重新挂载。最近一次成功快照只在当前页面内存中
     按角色/会话和宿主路由保留，返回时先展示它，再异步核对最新状态。 */
  const overviewCache = new Map();
  /* 余额属于当前角色/会话，但不属于某个具体的感知路由。单独存一份，
     这样从仪表盘切到配置、再切回时不会因为下方配置工作区被卸载而丢掉
     最近成功读数。只挑选可安全展示的字段；API 令牌和 edit token 永不缓存。 */
  const balanceCache = new Map();
  let overviewRouteKey = "";
  function activeScopeKey() {
    try {
      const scoped = product.scopeKey?.();
      if (scoped != null && String(scoped).trim()) return String(scoped);
      const role = product.role?.() || {};
      const selection = product.selection?.() || {};
      const fallback = [selection.installation_id, selection.target_id, role.installation_id,
        role.target_id, role.role_id, role.model_api].filter(value => value != null && String(value).trim());
      return fallback.length ? fallback.join(":") : "default";
    } catch (_) { return "default"; }
  }
  function routeKey() {
    try {
      const role = product.role?.() || {};
      return [role.model_api, role.perception_native_api, role.screen_access_api, role.reality_access_api].join("|");
    } catch (_) { return ""; }
  }
  function overviewCacheEntry() {
    const key = `${scope}:${overviewRouteKey}`;
    let entry = overviewCache.get(key);
    if (!entry) { entry = {}; overviewCache.set(key, entry); }
    return entry;
  }
  function rememberOverview(name, value, stateName, stateValue) {
    const entry = overviewCacheEntry();
    if (value != null) entry[name] = value;
    if (stateName) entry[stateName] = stateValue;
  }
  function forgetOverview(name, stateName) {
    const entry = overviewCacheEntry();
    delete entry[name];
    if (stateName) delete entry[stateName];
  }

  function safeBalanceConfig(value) {
    if (!value || typeof value !== "object") return null;
    return {
      source: value.source || "none",
      source_state: value.source_state || "not_configured",
      provider: value.provider || "newapi",
      url: typeof value.url === "string" ? value.url : "",
      user_id: typeof value.user_id === "string" ? value.user_id : "",
      value_divisor: String(value.value_divisor || "500000"),
      has_api_key: value.has_api_key === true,
      advanced_enabled: value.advanced_enabled === true,
      advanced_base_url: typeof value.advanced_base_url === "string" ? value.advanced_base_url : "",
      advanced_script: typeof value.advanced_script === "string" ? value.advanced_script : "",
      has_advanced_api_key: value.has_advanced_api_key === true,
      has_advanced_access_token: value.has_advanced_access_token === true,
      advanced_user_id: typeof value.advanced_user_id === "string" ? value.advanced_user_id : "",
      has_advanced_proxy_url: value.has_advanced_proxy_url === true,
    };
  }

  function safeBalanceDraft(value) {
    if (!value || typeof value !== "object") return null;
    return {
      url: typeof value.url === "string" ? value.url : "",
      user_id: typeof value.user_id === "string" ? value.user_id : "",
      value_divisor: String(value.value_divisor || "500000"),
      /* Never retain typed credentials when leaving this role/page. */
      api_key: "", advanced_api_key: "", advanced_access_token: "", advanced_proxy_url: "",
      advanced_user_id: typeof value.advanced_user_id === "string" ? value.advanced_user_id : "",
      advanced_enabled: value.advanced_enabled === true,
      advanced_base_url: typeof value.advanced_base_url === "string" ? value.advanced_base_url : "",
      advanced_script: typeof value.advanced_script === "string" ? value.advanced_script : "",
      clear_api_key: value.clear_api_key === true,
      clear_advanced_api_key: value.clear_advanced_api_key === true,
      clear_advanced_access_token: value.clear_advanced_access_token === true,
      clear_advanced_proxy_url: value.clear_advanced_proxy_url === true,
      clear_configuration: value.clear_configuration === true,
    };
  }

  function safeBalanceObservation(value) {
    if (!value || typeof value !== "object") return null;
    const amount = Number(value.amount);
    if (value.available !== true || !Number.isFinite(amount) || amount < 0) return null;
    return {
      tested: value.tested === true,
      available: true,
      plan_name: typeof value.plan_name === "string" ? value.plan_name : "",
      extra: typeof value.extra === "string" ? value.extra : "",
      provider: typeof value.provider === "string" ? value.provider : "newapi",
      provider_label: typeof value.provider_label === "string" ? value.provider_label : "NewAPI",
      currency_label: typeof value.currency_label === "string" ? value.currency_label : "额度",
      amount: formatBalanceAmount(amount),
      observed_at: typeof value.observed_at === "string" ? value.observed_at : "",
    };
  }

  function safeBalanceStatus(value) {
    if (!value || typeof value !== "object" || typeof value.configured !== "boolean"
        || typeof value.available !== "boolean") return null;
    const amount = Number(value.amount);
    if (value.available && (!Number.isFinite(amount) || amount < 0)) return null;
    return {
      configured:value.configured, available:value.available,
      message:typeof value.message === "string" ? value.message : "",
      plan_name:typeof value.plan_name === "string" ? value.plan_name : "",
      extra:typeof value.extra === "string" ? value.extra : "",
      provider:typeof value.provider === "string" ? value.provider : "newapi",
      provider_label:typeof value.provider_label === "string" ? value.provider_label : "NewAPI",
      currency_label:typeof value.currency_label === "string" ? value.currency_label : "额度",
      amount:value.available ? formatBalanceAmount(amount) : null,
      observed_at:typeof value.observed_at === "string" ? value.observed_at : "",
      state:typeof value.state === "string" ? value.state : value.available ? "available" : "unavailable",
    };
  }

  function balanceStatusMessage(value) {
    if (value?.available) return "";
    if (value?.state === "not_configured" || value?.configured === false) return "尚未配置服务余额数据源。";
    if (value?.state === "configuration_invalid") return "余额数据源配置无效，请检查配置。";
    return value?.message || "余额读取失败，请检查数据源、脚本或网络。";
  }

  function balanceConfigKey(value) {
    const config = safeBalanceConfig(value);
    return config ? JSON.stringify(config) : "";
  }

  function rememberBalanceCache() {
    if (!scope) return;
    const entry = {};
    const config = safeBalanceConfig(balanceConfig);
    const draft = safeBalanceDraft(balanceDraft);
    const observation = safeBalanceObservation(balanceTestResult);
    const status = safeBalanceStatus(balanceStatus);
    if (config) entry.config = config;
    if (draft) entry.draft = draft;
    if (status) entry.status = status;
    if (observation) {
      entry.observation = observation;
      entry.observationIsDraft = balanceResultIsDraft === true;
      entry.observationConfigKey = balanceResultConfigKey || "";
    }
    if (Object.keys(entry).length) balanceCache.set(scope, entry);
    else balanceCache.delete(scope);
  }

  function restoreBalanceCache(nextScope) {
    const entry = balanceCache.get(nextScope) || {};
    balanceConfig = entry.config ? {...entry.config} : null;
    balanceDraft = entry.draft ? {...entry.draft, api_key: "", advanced_api_key: "", advanced_access_token: "", advanced_proxy_url: ""} : null;
    balanceStatus = safeBalanceStatus(entry.status);
    balanceStatusError = balanceStatus ? balanceStatusMessage(balanceStatus) : "";
    balanceTestResult = entry.observation ? {...entry.observation} : null;
    balanceResultScope = balanceTestResult ? nextScope : "";
    balanceResultConfigToken = "";
    balanceResultConfigKey = entry.observationConfigKey || "";
    balanceResultIsDraft = entry.observationIsDraft === true;
  }

  function formatBalanceAmount(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) && numeric >= 0 ? numeric.toFixed(2) : String(value ?? "");
  }
  function clearBalanceResult() {
    balanceTestResult = null;
    balanceResultConfigToken = "";
    balanceResultConfigKey = "";
    balanceResultIsDraft = false;
    rememberBalanceCache();
  }

  function validRoot(ticket) {
    return ticket === generation && !!root?.isConnected && scope === activeScopeKey();
  }
  function localParts(date, timezone) {
    const parts = new Intl.DateTimeFormat("en-CA-u-nu-latn", {timeZone:timezone, year:"numeric", month:"2-digit", day:"2-digit"})
      .formatToParts(date);
    return Object.fromEntries(parts.filter(part => part.type !== "literal").map(part => [part.type, Number(part.value)]));
  }
  function dayNumber(parts) { return Math.floor(Date.UTC(parts.year, parts.month - 1, parts.day) / 86400000); }
  function chineseParts(date, timezone) {
    const parts = new Intl.DateTimeFormat("en-u-ca-chinese-nu-latn", {timeZone:timezone, month:"numeric", day:"numeric"})
      .formatToParts(date);
    const month = parts.find(part => part.type === "month")?.value || "";
    const day = Number(parts.find(part => part.type === "day")?.value);
    const numericMonth = Number(month.replace(/bis/i, ""));
    return {month:numericMonth, day, leap:/bis/i.test(month)};
  }
  function upcomingDates(data, now = new Date()) {
    const contact = data?.contact;
    if (!contact) return [];
    const lookahead = Number(contact.lookaheadDays);
    if (!Number.isInteger(lookahead) || lookahead < 0 || lookahead > 30) return [];
    let rows;
    try { rows = JSON.parse(contact.dates || "[]"); } catch (_) { return []; }
    if (!Array.isArray(rows) || !rows.length) return [];
    const timezone = contact.timezone || data.environment?.timezone || data.model?.timezone || "UTC";
    let today;
    try { today = localParts(now, timezone); } catch (_) { return []; }
    const todayNumber = dayNumber(today), found = [];
    for (const row of rows) {
      if (!row || typeof row.label !== "string") continue;
      /* The runtime already projects profile dates in the local timezone.
         Prefer its authoritative date/days_until pair; keep the legacy
         month/day calculation for older bridges and cached snapshots. */
      if (typeof row.date === "string" && Number.isInteger(row.days_until)) {
        if (row.days_until >= 0 && row.days_until <= lookahead) {
          found.push({label: row.label, days: row.days_until,
                      calendar: row.calendar === "lunar" ? "lunar" : "gregorian"});
        }
        continue;
      }
      if (!Number.isInteger(row.month) || !Number.isInteger(row.day)) continue;
      if (row.calendar === "lunar") {
        let formatterReady = true;
        try { chineseParts(now, timezone); } catch (_) { formatterReady = false; }
        if (!formatterReady) continue;
        for (let days = 0; days <= lookahead; days++) {
          const candidate = new Date(Date.UTC(today.year, today.month - 1, today.day + days, 12));
          let lunar;
          try { lunar = chineseParts(candidate, timezone); } catch (_) { break; }
          if (lunar.month === row.month && lunar.day === row.day && lunar.leap === (row.leap_month === true)) {
            found.push({label:row.label, days, calendar:"lunar"});
            break;
          }
        }
        continue;
      }
      if (row.calendar && row.calendar !== "gregorian") continue;
      for (let year = today.year; year <= today.year + 1; year++) {
        const candidate = new Date(Date.UTC(year, row.month - 1, row.day, 12));
        if (candidate.getUTCMonth() !== row.month - 1 || candidate.getUTCDate() !== row.day) continue;
        const days = dayNumber({year, month:row.month, day:row.day}) - todayNumber;
        if (days >= 0 && days <= lookahead) {
          found.push({label:row.label, days, calendar:"gregorian"});
          break;
        }
      }
    }
    return found.sort((a,b) => a.days - b.days || a.label.localeCompare(b.label, "zh-CN")).slice(0, 4);
  }
  function calendarDetailsMarkup(calendar) {
    if (!calendar || calendar.status === "disabled") return "";
    const rows = [];
    if (calendar.holiday?.status === "available") rows.push(["日历", calendar.holiday.label]);
    if (calendar.lunar_status === "available" && calendar.lunar_date) rows.push(["农历", calendar.lunar_date]);
    const terms = calendar.automatic_solar_terms || {};
    if (terms.status === "available") {
      if (terms.today?.length) rows.push(["节气", terms.today.join("、")]);
      else if (terms.upcoming?.length) {
        const next = terms.upcoming[0];
        rows.push(["节气", `${next.days_ahead} 天后 ${next.names.join("、")}`]);
      }
    }
    if (calendar.almanac?.status === "available") rows.push(["轻量黄历", `宜 ${calendar.almanac.yi} · 忌 ${calendar.almanac.ji}`]);
    return rows.length ? `<dl class="perception-calendar-details">${rows.map(([label,value]) =>
      `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>` : "";
  }
  function stateValue(value) {
    if (typeof value === "string" || typeof value === "number") return String(value);
    if (value && typeof value === "object") {
      const candidate = value.label ?? value.name ?? value.value ?? value.description;
      if (typeof candidate === "string" || typeof candidate === "number") return String(candidate);
    }
    return "";
  }
  function ageLabel(value) {
    const date = typeof value === "number" ? new Date(value * 1000) : new Date(value || "");
    if (Number.isNaN(date.getTime())) return "时间未知";
    const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
    if (seconds < 60) return "刚刚";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
    return new Intl.DateTimeFormat("zh-CN", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit"}).format(date);
  }
  function reasonLabel(code) {
    return ({capability_consent_missing:"等待此私聊授权", capability_consent_revoked:"此私聊已撤回授权",
      camera_consent_missing:"等待此私聊授权", camera_tool_unavailable:"摄像头工具暂不可用",
      capability_role_disabled:"此角色未启用手机读取", reality_provider_unavailable:"Reality 扩展未就绪",
      reality_tool_unavailable:"手机读取工具暂不可用", no_recent_snapshot:"授权已就绪，尚无最近快照",
      no_recent_observation:"摄像头授权已就绪，尚无最近单帧结果",
      reality_private_required:"仅在当前私聊中读取", screen_repository_unavailable:"屏幕解读服务未就绪",
      screen_consent_missing:"等待此私聊屏幕授权", screen_consent_revoked:"此私聊已撤回屏幕授权",
      capability_not_installed:"Reality 能力尚未登记", capability_not_active:"Reality 能力已停用"})[code]
      || "当前没有可显示的数据";
  }
  function modelRoute(kind = "conversation.reply") {
    const tasks = modelStatus?.tasks;
    const task = Array.isArray(tasks) ? tasks.find(item => item?.kind === kind) : null;
    const fields = task?.fields || {};
    const providerId = fields.provider?.value;
    const provider = modelStatus?.providers?.find(item => item?.id === providerId);
    const fallbackId = fields.fallback?.value;
    const fallback = modelStatus?.providers?.find(item => item?.id === fallbackId);
    if (!task || !providerId) return null;
    return {model:provider?.model || providerId, provider:providerId,
      fallback:fallback ? fallback.model || fallback.id : "",
      source:fields.provider?.source || "default",
      fallback_source:fields.fallback?.source || "default",
      host:modelStatus?.host_route || null,
      layer:modelStatus?.configuration_layer || "role"};
  }
  function providerSignal(id) {
    const provider = product.capability(id);
    if (!provider) return {state:"unknown", title:"尚未读取扩展", detail:"能力目录尚未提供状态"};
    const names = {connected:"已连接", installed:"已安装", missing:"未安装", disabled:"已停用",
      waiting:"等待就绪", degraded:"状态待检查", conflict:"重复安装", stopped:"插件已停止"};
    let state = provider.integration_state || "unknown";
    let title = names[state] || "状态未知";
    if (state === "connected" && provider.runtime_ready === false) { state = "waiting"; title = "运行未就绪"; }
    const detail = provider.reason || (provider.runtime_ready === true ? "扩展报告运行就绪" : "设备工作区负责确认采集授权");
    return {state:state === "connected" ? "ready" : state === "missing" || state === "disabled" ? "off" : "pending", title, detail};
  }
  /* === 服务端日历是日期语义的唯一基准 ===
     公历、星期、农历、节假日与节气都由服务端 project_local_date 按角色时区算出，
     随 /runtime 快照下发（页面从来没有自己算这些的权限）。
     只有"秒"必须本地走：用服务端给的时区推进，跨日则重新拉取服务端日历，
     因为农历与节假日跨日会变，本地推算会给出错误的一天。 */
  function calendarContext() {
    const value = runtimeSnapshot?.calendar;
    if (!value || typeof value !== "object") return null;
    if (value.status === "disabled" || typeof value.local_date !== "string") return null;
    return value;
  }
  function calendarTags(value) {
    const tags = [];
    const add = item => { if (typeof item === "string" && item && tags.indexOf(item) < 0) tags.push(item); };
    const configured = value.configured_annual_calendar?.listed_today;
    if (configured && typeof configured === "object") {
      add(configured.holiday);
      (Array.isArray(configured.solar_terms) ? configured.solar_terms : []).forEach(add);
    }
    if (value.holiday?.status === "available" && value.holiday?.kind === "holiday") add(value.holiday.label);
    add(value.fixed_gregorian_holiday);
    return tags.slice(0, 2);
  }
  function serverDateLabel() {
    const value = calendarContext();
    if (!value) return "";
    const bits = [];
    const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.local_date);
    if (parts) bits.push(`${Number(parts[2])} 月 ${Number(parts[3])} 日`);
    if (typeof value.weekday === "string" && value.weekday) bits.push(value.weekday);
    if (value.lunar_status === "available" && typeof value.lunar_date === "string" && value.lunar_date)
      bits.push(value.lunar_date.startsWith("农历") ? value.lunar_date : `农历${value.lunar_date}`);
    return bits.join(" · ");
  }
  function clockZone() {
    return calendarContext()?.timezone || snapshot?.model?.timezone || snapshot?.environment?.timezone
      || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  }
  function updateClock() {
    if (!root?.isConnected) return;
    const calendar = calendarContext();
    const timezone = clockZone(), now = new Date();
    const write = (selector, text) => {
      const node = root.querySelector(selector);
      if (node) node.textContent = text;
      return node;
    };
    try {
      const formatter = new Intl.DateTimeFormat("zh-CN", {timeZone:timezone, hour:"2-digit", minute:"2-digit", second:"2-digit", hourCycle:"h23"});
      const dateFormatter = new Intl.DateTimeFormat("zh-CN", {timeZone:timezone, year:"numeric", month:"long", day:"numeric"});
      const weekdayFormatter = new Intl.DateTimeFormat("zh-CN", {timeZone:timezone, weekday:"long"});
      write("[data-context-clock-time]", formatter.format(now));
      write("[data-context-clock-zone]", timezone);
      /* 服务端日历在手就用它的农历与节假日；读不到才退回本机推算，且不再假装有农历。 */
      const serverLabel = serverDateLabel();
      write("[data-context-clock-date]", serverLabel || dateFormatter.format(now));
      write("[data-context-clock-lunar]", serverLabel ? "" : weekdayFormatter.format(now));
      const today = calendar ? calendarTags(calendar)[0] || "" : "";
      const tag = write("[data-context-clock-tag]", today);
      if (tag) tag.hidden = !today;
      const parts = localParts(now, timezone), day = `${parts.year}-${parts.month}-${parts.day}`;
      /* 跨日不能本地推算：重新拉取 /runtime，让服务端按新的一天算农历与节假日。 */
      if (lastDay && day !== lastDay) { lastDay = day; refreshRuntime(); return; }
      lastDay = day;
    } catch (_) {
      write("[data-context-clock-time]", now.toLocaleTimeString("zh-CN", {hour12:false}));
      write("[data-context-clock-zone]", "本机时区");
    }
  }
  const balanceScriptExamples = {
  "generic": "({\n  request: {\n    url: \"{{baseUrl}}/user/balance\",\n    method: \"GET\",\n    headers: {\n      \"Authorization\": \"Bearer {{apiKey}}\",\n      \"User-Agent\": \"astrbot_plugin_private_companion\"\n    }\n  },\n  extractor: function(response) {\n    return {\n      isValid: response.is_active !== false,\n      remaining: response.balance,\n      unit: \"USD\"\n    };\n  }\n})",
  "newapi": "({\n  request: {\n    url: \"{{baseUrl}}/api/user/self\",\n    method: \"GET\",\n    headers: {\n      \"Content-Type\": \"application/json\",\n      \"Authorization\": \"Bearer {{accessToken}}\",\n      \"User-Agent\": \"astrbot_plugin_private_companion\",\n      \"New-Api-User\": \"{{userId}}\"\n    },\n  },\n  extractor: function(response) {\n    if (response.success && response.data) {\n      return {\n        planName: response.data.group || \"默认套餐\",\n        remaining: response.data.quota / 500000,\n        used: response.data.used_quota / 500000,\n        total: (response.data.quota + response.data.used_quota) / 500000,\n        unit: \"USD\",\n      };\n    }\n    return {\n      isValid: false,\n      invalidMessage: response.message || \"查询失败\"\n    };\n  },\n})",
  "sub2api": "({\n  request: {\n    url: \"{{baseUrl}}\".replace(/\\/+$/, \"\").replace(/\\/v1$/, \"\") + \"/v1/usage\",\n    method: \"GET\",\n    headers: {\n      \"Authorization\": \"Bearer {{apiKey}}\",\n      \"User-Agent\": \"astrbot_plugin_private_companion\"\n    }\n  },\n  extractor: function(response) {\n    var quota = response.quota || {};\n    var result = {\n      isValid: response.isValid !== false,\n      unit: response.unit || quota.unit || \"USD\"\n    };\n    if (typeof response.planName === \"string\") result.planName = response.planName;\n    if (typeof response.remaining === \"number\" && response.remaining >= 0) {\n      result.remaining = response.remaining;\n    }\n    if (quota.limit > 0) {\n      result.used = quota.used;\n      result.total = quota.limit;\n    }\n    if (!result.isValid) {\n      result.invalidMessage = response.status ? String(response.status) : \"查询失败\";\n    }\n    return result;\n  },\n})"
};
  let balancePreset = "generic";
  const balanceTextFields = ["url", "user_id", "value_divisor", "api_key", "advanced_base_url",
    "advanced_api_key", "advanced_script", "advanced_proxy_url", "advanced_access_token", "advanced_user_id", "advanced_provider_id"];
  const balanceCheckFields = ["clear_configuration", "advanced_enabled", "advanced_from_astrbot", "clear_api_key",
    "clear_advanced_api_key", "clear_advanced_proxy_url", "clear_advanced_access_token"];
  function emptyBalanceDraft(config) {
    return {url:config?.url || "", user_id:config?.user_id || "",
      value_divisor:config?.value_divisor || "500000", api_key:"", clear_configuration:false,
      advanced_enabled:config?.advanced_enabled === true,
      advanced_from_astrbot:config?.advanced_from_astrbot === true, advanced_provider_id:config?.advanced_provider_id || "",
      advanced_base_url:config?.advanced_base_url || "",
      advanced_script:config?.advanced_script || balanceScriptExamples.generic,
      advanced_api_key:"", advanced_access_token:"", advanced_user_id:config?.advanced_user_id || "",
      advanced_proxy_url:"", clear_api_key:false, clear_advanced_access_token:false,
      clear_advanced_api_key:false, clear_advanced_proxy_url:false};
  }
  function rememberBalanceDraft(form) {
    const draft = balanceDraft || emptyBalanceDraft(balanceConfig);
    for (const name of balanceTextFields) {
      const field = form.querySelector(`[name="${name}"]`);
      if (field) draft[name] = String(field.value || "");
    }
    for (const name of balanceCheckFields) {
      const field = form.querySelector(`[name="${name}"]`);
      if (field) draft[name] = field.checked === true;
    }
    balanceDraft = draft;
    return draft;
  }
  function balanceDraftIsCurrent() {
    if (!balanceConfig || !balanceDraft || balanceDraft.clear_configuration
        || balanceConfig.source_state !== "configured"
        || balanceDraft.advanced_enabled !== (balanceConfig.advanced_enabled === true)) return false;
    if (balanceDraft.advanced_enabled) return balanceDraft.advanced_from_astrbot === (balanceConfig.advanced_from_astrbot === true)
      && balanceDraft.advanced_provider_id === (balanceConfig.advanced_provider_id || "")
      && (balanceDraft.advanced_from_astrbot || (balanceDraft.advanced_base_url === (balanceConfig.advanced_base_url || "")
        && !balanceDraft.advanced_api_key && !balanceDraft.advanced_proxy_url
        && !balanceDraft.clear_advanced_api_key && !balanceDraft.clear_advanced_proxy_url))
      && balanceDraft.advanced_script === (balanceConfig.advanced_script || "")
      && balanceDraft.advanced_user_id === (balanceConfig.advanced_user_id || "")
      && !balanceDraft.advanced_access_token && !balanceDraft.clear_advanced_access_token;
    return balanceDraft.url === (balanceConfig.url || "")
      && balanceDraft.user_id === (balanceConfig.user_id || "")
      && balanceDraft.value_divisor === (balanceConfig.value_divisor || "500000")
      && !balanceDraft.api_key && !balanceDraft.clear_api_key;
  }
  function balanceDraftTestable(draft) {
    return !draft.clear_configuration && (balanceDraftIsCurrent() || (draft.advanced_enabled
      ? !!draft.advanced_script.trim() : !!(draft.url && (draft.api_key || balanceConfig?.has_api_key))));
  }
  function balanceDraftError(draft) {
    if (draft.advanced_enabled) {
      if (!draft.advanced_script.trim()) return "请填写 request + extractor 余额脚本。";
      if (draft.advanced_from_astrbot) {
        const id = draft.advanced_provider_id || balanceConfig?.astrbot_default_provider_id;
        if (!id || !(balanceConfig?.astrbot_providers || []).some(item => item.id === id && item.enabled !== false))
          return "所选 AstrBot 供应商不可用，请检查默认模型或重新选择供应商。";
      }
      if (!draft.advanced_from_astrbot && draft.advanced_base_url && !/^https:\/\//i.test(draft.advanced_base_url)) return "高级基础地址必须使用 HTTPS。";
      if (!draft.advanced_from_astrbot && draft.advanced_proxy_url && !/^(http|socks5):\/\//i.test(draft.advanced_proxy_url)) return "代理地址必须使用 http:// 或 socks5://。";
      if (draft.advanced_user_id && !/^[0-9]{1,19}$/.test(draft.advanced_user_id)) return "高级 NewAPI 用户 ID 必须为数字。";
      if (draft.clear_advanced_access_token && draft.advanced_access_token) return "请分别选择更换或清除访问令牌。";
      if (!draft.advanced_from_astrbot && ((draft.clear_advanced_api_key && draft.advanced_api_key) || (draft.clear_advanced_proxy_url && draft.advanced_proxy_url)))
        return "请分别选择更换或清除凭据。";
      return "";
    }
    if (!draft.url || !/^https:\/\//i.test(draft.url)) return "请填写有效的 HTTPS NewAPI 地址。";
    if ((!draft.api_key && !balanceConfig?.has_api_key) || draft.clear_api_key) return "请填写带 profile:read 权限的 NewAPI 管理令牌。";
    if (draft.user_id && !/^[1-9][0-9]{0,18}$/.test(draft.user_id)) return "NewAPI 用户 ID 必须为正整数。";
    if (!draft.value_divisor || !Number.isFinite(Number(draft.value_divisor)) || Number(draft.value_divisor) <= 0)
      return "额度单位除数必须是大于 0 的数字。";
    return "";
  }
  function balanceDraftChanges(draft) {
    if (draft.advanced_enabled) return {advanced_enabled:true,
      advanced_from_astrbot:draft.advanced_from_astrbot, advanced_provider_id:draft.advanced_provider_id,
      advanced_script:draft.advanced_script,
      advanced_access_token:draft.advanced_access_token, advanced_user_id:draft.advanced_user_id,
      clear_advanced_access_token:draft.clear_advanced_access_token,
      ...(!draft.advanced_from_astrbot ? {advanced_base_url:draft.advanced_base_url,
        advanced_api_key:draft.advanced_api_key, advanced_proxy_url:draft.advanced_proxy_url,
        clear_advanced_api_key:draft.clear_advanced_api_key,
        clear_advanced_proxy_url:draft.clear_advanced_proxy_url} : {})};
    const changes = {url:draft.url, api_key:draft.api_key, user_id:draft.user_id, value_divisor:draft.value_divisor};
    if (balanceConfig?.advanced_enabled) changes.advanced_enabled = false;
    if (draft.clear_api_key) changes.clear_api_key = true;
    return changes;
  }
  function balanceConfigMarkup() {
    if (!balanceExpanded) return "";
    if (balanceConfigLoading) return '<div id="perception-balance-source" class="perception-balance-config" role="status">正在读取余额数据源…</div>';
    if (!balanceConfig) return `<div id="perception-balance-source" class="perception-balance-config" role="status">
      <p>${esc(balanceError || "余额数据源配置暂不可读。")}</p><button type="button" data-balance-reload ${balanceSaving || balanceTesting ? "disabled" : ""}>重新读取</button></div>`;
    const draft = balanceDraft || emptyBalanceDraft(balanceConfig);
    const busy = balanceConfigLoading || balanceSaving || balanceTesting;
    const balanceActionLabel = balanceDraftIsCurrent() ? "重新读取余额" : "测试连接";
    const testable = balanceDraftTestable(draft);
    const displayObservation = balanceResultIsDraft ? balanceTestResult
      : balanceStatus?.available ? balanceStatus : balanceTestResult;
    const displayMessage = displayObservation
      ? (balanceResultIsDraft ? "草稿测试成功" : "当前余额已读取") + "：" + formatBalanceAmount(displayObservation.amount)
        + " " + (displayObservation.currency_label || "额度") + " · " + (displayObservation.observed_at || "时间未知")
        + (displayObservation.plan_name ? " · " + displayObservation.plan_name : "")
        + (displayObservation.extra ? " · " + displayObservation.extra : "")
      : balanceStatusLoading ? "正在读取当前余额…" : balanceStatusError
        || (balanceStatus?.state === "not_configured" ? "尚未配置服务余额数据源。" : "");
    const sourceMessage = balanceConfig.source === "plugin" ? (balanceConfig.advanced_enabled
      ? "当前使用高级脚本查询；旧 NewAPI 配置已停用。" : "本页保存的 NewAPI 数据源优先用于查询与主动联系。")
      : balanceConfig.source === "environment" ? "当前数据源来自插件环境配置；在这里保存后将优先使用本页配置。"
        : balanceConfig.source_state === "invalid" ? "检测到数据源配置，但读取失败；请检查地址、令牌和环境变量。"
          : "填写 NewAPI 地址和管理令牌后，插件即可查询服务额度。";
    const sourceState = balanceConfig.source_state === "invalid" ? "数据源配置无效"
      : balanceConfig.source === "plugin" ? balanceConfig.advanced_enabled ? "高级数据源已配置"
        : balanceConfig.has_api_key ? "NewAPI 数据源已配置" : "NewAPI 令牌缺失"
      : balanceConfig.source === "environment" ? "正在使用插件环境配置"
        : "尚未配置数据源";
    return `<form id="perception-balance-source" class="perception-balance-config" data-balance-form novalidate aria-label="余额感知数据源配置">
      <div class="perception-balance-config-head"><span>${icon("wallet-cards")}</span><div><strong>余额数据源</strong><small>${esc(sourceState)} · ${esc(sourceMessage)}</small></div></div>
      <fieldset class="perception-balance-legacy" aria-label="原有 NewAPI 配置" ${busy || draft.clear_configuration || draft.advanced_enabled ? "disabled" : ""}>
      <label class="perception-balance-field"><span>NewAPI 地址</span><input name="url" data-balance-field="url" type="url" inputmode="url" autocomplete="url" maxlength="2048"
        placeholder="https://newapi.example.com" value="${esc(draft.url)}" ${busy || draft.clear_configuration ? "disabled" : ""} required></label>
      <label class="perception-balance-field"><span>NewAPI 用户 ID（可选）</span><input name="user_id" data-balance-field="user_id" type="text" inputmode="numeric" autocomplete="off" maxlength="19"
        pattern="[1-9][0-9]{0,18}" placeholder="默认使用令牌所属账号" value="${esc(draft.user_id)}" ${busy || draft.clear_configuration ? "disabled" : ""}></label>
      <label class="perception-balance-field"><span>额度单位除数</span><input name="value_divisor" data-balance-field="value_divisor" type="text" inputmode="decimal" autocomplete="off" maxlength="64"
        value="${esc(draft.value_divisor)}" ${busy || draft.clear_configuration ? "disabled" : ""} required></label>
      <label class="perception-balance-field"><span>NewAPI 管理令牌（nap_）</span><input name="api_key" data-balance-field="api_key" type="password" autocomplete="new-password" maxlength="4096"
        value="${esc(draft.api_key)}" placeholder="${balanceConfig.has_api_key ? "已保存；留空保留当前令牌，输入新令牌可更换" : "输入带 profile:read 权限的个人访问令牌"}"
        ${busy || draft.clear_configuration ? "disabled" : ""}></label>
      <p class="perception-balance-note">查询使用个人访问令牌（需 profile:read），不是模型调用用的 sk- Key。额度按 NewAPI 的 QuotaPerUnit 换算；留空时使用 500000。</p>
      </fieldset>
      <label class="perception-balance-clear perception-balance-mode"><input type="checkbox" name="advanced_enabled" data-balance-field="advanced_enabled" ${draft.advanced_enabled ? "checked" : ""} ${busy || draft.clear_configuration ? "disabled" : ""}><span>启用高级设置</span><small>开启后禁用上方旧表单，只使用下面的独立配置；关闭后恢复旧配置。</small></label>
      ${draft.advanced_enabled ? `<fieldset class="perception-balance-advanced" aria-label="高级余额配置" ${busy || draft.clear_configuration ? "disabled" : ""}>
        <label class="perception-balance-clear"><input name="advanced_from_astrbot" data-balance-field="advanced_from_astrbot" type="checkbox" ${draft.advanced_from_astrbot ? "checked" : ""}><span>从 AstrBot 供应商读取余额（地址、API Key 和代理）</span></label>
        ${draft.advanced_from_astrbot ? `<label class="perception-balance-field"><span>AstrBot 供应商</span><select name="advanced_provider_id" data-balance-field="advanced_provider_id">
          <option value="" ${!draft.advanced_provider_id ? "selected" : ""}>跟随 AstrBot 默认模型所属供应商${balanceConfig.astrbot_default_provider_id ? `（${esc(balanceConfig.astrbot_default_provider_id)}）` : "（未配置）"}</option>
          ${(balanceConfig.astrbot_providers || []).map(item => `<option value="${esc(item.id)}" ${draft.advanced_provider_id === item.id ? "selected" : ""} ${item.enabled === false ? "disabled" : ""}>${esc(item.label || item.id)}${item.enabled === false ? "（已禁用）" : ""}</option>`).join("")}
          ${draft.advanced_provider_id && !(balanceConfig.astrbot_providers || []).some(item => item.id === draft.advanced_provider_id) ? `<option value="${esc(draft.advanced_provider_id)}" selected disabled>${esc(draft.advanced_provider_id)}（已失效，请重新选择）</option>` : ""}
        </select></label><p class="perception-balance-note">仅由后端读取供应商连接配置，不回显或复制 Key；每次查询读取最新配置。多 Key 使用首个非空且可解析的 Key。未设置代理则直连，代理错误不会回退直连。余额接口仍由下方脚本定义，NewAPI 管理令牌不能用模型 API Key 替代。</p>` : `
        <label class="perception-balance-field"><span>基础地址（可选，{{baseUrl}}）</span><input name="advanced_base_url" data-balance-field="advanced_base_url" type="url" maxlength="2048" placeholder="https://provider.example.com" value="${esc(draft.advanced_base_url)}"></label>
        <label class="perception-balance-field"><span>API Key（{{apiKey}}）</span><input name="advanced_api_key" data-balance-field="advanced_api_key" type="password" autocomplete="new-password" maxlength="4096" placeholder="${balanceConfig.has_advanced_api_key ? "已保存；留空保留，输入可更换" : "按接口需要填写"}" value="${esc(draft.advanced_api_key)}"></label>
        <label class="perception-balance-clear"><input name="clear_advanced_api_key" data-balance-field="clear_advanced_api_key" type="checkbox" ${draft.clear_advanced_api_key ? "checked" : ""}><span>清除已保存的高级 API Key</span></label>`}
        <label class="perception-balance-field"><span>NewAPI 访问令牌（{{accessToken}}）</span><input name="advanced_access_token" data-balance-field="advanced_access_token" type="password" autocomplete="new-password" maxlength="4096" placeholder="${balanceConfig.has_advanced_access_token ? "已保存；留空保留，输入可更换" : "使用 newapi 预设时填写"}" value="${esc(draft.advanced_access_token)}"></label>
        <label class="perception-balance-field"><span>NewAPI 用户 ID（{{userId}}）</span><input name="advanced_user_id" data-balance-field="advanced_user_id" type="text" inputmode="numeric" maxlength="19" placeholder="使用 newapi 预设时填写" value="${esc(draft.advanced_user_id)}"></label>
        <label class="perception-balance-clear"><input name="clear_advanced_access_token" data-balance-field="clear_advanced_access_token" type="checkbox" ${draft.clear_advanced_access_token ? "checked" : ""}><span>清除已保存的 NewAPI 访问令牌</span></label>
        <div class="perception-balance-presets"><label class="perception-balance-field"><span>内置预设模板</span><select name="advanced_preset" data-balance-preset>${[["generic","通用"],["newapi","newapi"],["sub2api","sub2api"]].map(([value,label]) => `<option value="${value}" ${balancePreset === value ? "selected" : ""}>${label}</option>`).join("")}</select></label><button type="button" data-balance-load-preset>载入</button><button type="button" data-balance-restore-default>恢复默认</button></div>
        <label class="perception-balance-field perception-balance-script"><span>请求与提取脚本</span><textarea name="advanced_script" data-balance-field="advanced_script" rows="16" maxlength="16384" spellcheck="false">${esc(draft.advanced_script)}</textarea></label>
        <p class="perception-balance-note">使用 ({ request: {...}, extractor: function(response) {...} })；支持 ES2020 语法，返回单个对象。request 支持 url、method、headers、字符串 body；{{baseUrl}}、{{apiKey}}、{{accessToken}}、{{userId}} 在脚本字符串中安全展开，支持随后调用 .replace() 等字符串方法。返回字段：isValid、invalidMessage、remaining、unit、planName、total、used、extra。金额为最终值，不再使用旧除数；缺少 remaining 时可由 total - used 计算。异步函数、多账户数组及脚本内联网不受支持。请用变量填写密钥，不要写入脚本。</p>
        ${!draft.advanced_from_astrbot ? `<label class="perception-balance-field"><span>专用代理（可选）</span><input name="advanced_proxy_url" data-balance-field="advanced_proxy_url" type="password" autocomplete="new-password" maxlength="2048" placeholder="${balanceConfig.has_advanced_proxy_url ? "已保存；留空保留，输入可更换" : "http://host:port 或 socks5://user:password@host:port"}" value="${esc(draft.advanced_proxy_url)}"></label>
        <label class="perception-balance-clear"><input name="clear_advanced_proxy_url" data-balance-field="clear_advanced_proxy_url" type="checkbox" ${draft.clear_advanced_proxy_url ? "checked" : ""}><span>清除已保存的专用代理，改为直连</span></label>` : ""}
      </fieldset>` : ""}
      ${balanceConfig.source === "plugin" ? `<label class="perception-balance-clear"><input type="checkbox" name="clear_configuration" data-balance-field="clear_configuration" ${draft.clear_configuration ? "checked" : ""} ${busy ? "disabled" : ""}><span>移除本页保存的数据源</span><small>移除后会继续使用插件环境配置（如果已设置）。</small></label>` : ""}
      <p class="perception-balance-message" role="status" aria-live="polite" ${balanceMessage ? "" : "hidden"}>${esc(balanceMessage)}</p>
      <p class="perception-balance-message is-error" role="alert" ${balanceError ? "" : "hidden"}>${esc(balanceError)}</p>
      <p class="perception-balance-message" data-balance-test-result role="status" aria-live="polite" ${displayMessage ? "" : "hidden"}>${esc(displayMessage)}</p>
      <p class="perception-balance-message is-error" role="alert" ${balanceTestError ? "" : "hidden"}>${esc(balanceTestError)}</p>
      <footer class="perception-balance-actions">
        <button type="button" class="perception-balance-contact" data-go="manage" data-manage="contact" data-contact-view="sources">${icon("settings-2")}<span>前往主动联系 · 外部触发源</span></button>
        <span class="perception-balance-buttons">
          <button type="button" class="perception-balance-test" data-balance-test ${busy || !testable || (balanceStatusLoading && balanceDraftIsCurrent()) ? "disabled" : ""}>${balanceTesting ? balanceDraftIsCurrent() ? "正在读取…" : "正在测试…" : balanceActionLabel}</button>
          <button type="submit" class="perception-balance-save" ${busy ? "disabled" : ""}>${balanceSaving ? "正在保存…" : draft.clear_configuration ? "移除数据源" : "保存数据源"}</button>
        </span>
      </footer>
    </form>`;
  }
  function balanceFocus(form, active = window.document?.activeElement) {
    if (!active || !form?.contains?.(active)) return null;
    return {toggle:false, focus:active.getAttribute?.("name")
      || (active.matches?.('button[type="submit"]') ? "__submit" : active.matches?.("[data-balance-test]") ? "__test" : ""),
      selectionStart:active.selectionStart, selectionEnd:active.selectionEnd};
  }
  function restoreBalanceFocus(saved) {
    const form = balanceWorkspace?.querySelector("[data-balance-form]");
    const focus = saved?.focus === "__submit" ? form?.querySelector('button[type="submit"]')
      : saved?.focus === "__test" ? form?.querySelector("[data-balance-test]")
        : saved?.focus ? form?.querySelector(`[name="${saved.focus}"]`) : null;
    focus?.focus({preventScroll:true});
    if (focus && Number.isInteger(saved.selectionStart) && Number.isInteger(saved.selectionEnd))
      focus.setSelectionRange?.(saved.selectionStart, saved.selectionEnd);
  }
  function renderBalanceSource() {
    if (!balanceWorkspace || !balanceExpanded) return;
    const markup = balanceConfigMarkup();
    if (markup === balanceMarkup) return;
    const savedFocus = balanceFocus(balanceWorkspace.querySelector("[data-balance-form]"));
    balanceWorkspace.innerHTML = markup;
    balanceMarkup = markup;
    if (savedFocus) restoreBalanceFocus(savedFocus);
  }
  function syncSelection() {
    for (const card of root?.querySelectorAll?.(".perception-board-card") || []) card.classList.remove("is-selected");
    for (const button of root?.querySelectorAll?.("[data-board-goto]") || []) {
      const active = selectedItemId
        ? button.dataset.perceptionItem === selectedItemId
        : button.dataset.boardGoto === selectedDestination
          && (!button.dataset.boardModelTab || button.dataset.boardModelTab === selectedModelTab);
      button.setAttribute("aria-expanded", String(active));
      button.classList.toggle("is-selected", active);
      if (active) button.closest(".perception-board-card")?.classList.add("is-selected");
    }
  }
  function select(destination, {modelTab="", itemId=""} = {}) {
    if (!root?.isConnected) return;
    selectedDestination = destination;
    selectedModelTab = modelTab;
    selectedItemId = itemId;
    syncSelection();
    balanceExpanded = ["config-news","config-balance","config-recall"].includes(destination.split(":")[0]);
    renderBalanceSource();
    if (balanceExpanded && !balanceConfig && !balanceConfigLoading) void readBalanceConfig(generation);
  }
  function preview(itemId) {
    activePreviewItemId = String(itemId || "").trim();
    const panel = typeof document !== "undefined" ? document.getElementById("perceptionSingleItemPreview") : null;
    if (!activePreviewItemId) {
      if (panel) { panel.hidden = true; panel.innerHTML = ""; }
      return "";
    }
    const markup = itemPreviewMarkup.get(activePreviewItemId)
      || `<p class="perception-preview-empty">这一项暂无可展示的内容。</p>`;
    if (panel) { panel.innerHTML = markup; panel.hidden = false; }
    return markup;
  }
  function render({balanceSource=true} = {}) {
    if (!root?.isConnected) return;
    const active = window.document?.activeElement;
    const focusedId = active && root.contains?.(active) ? active.id : "";
    const ready = snapshot?.ready === true;
    const configurationFailed = !ready && (!!snapshot?.error || snapshot?.loading === false);
    const configurationState = configurationFailed ? "读取失败" : "正在读取…";
    const environment = snapshot?.environment || {}, model = snapshot?.model || {};
    const calendar = runtimeSnapshot?.calendar || {};
    const weather = runtimeSnapshot?.weather || {}, screenResult = runtimeSnapshot?.screen || {};
    const realityResult = runtimeSnapshot?.reality || {}, mobile = realityResult.snapshot || {};
    const cameraResult = runtimeSnapshot?.camera || {state:"unavailable", reason:"camera_runtime_unavailable"};
    const modelInfo = modelRoute();
    const modelStateLabel = modelInfo ? "路由已读取" : modelState === "loading" ? "正在读取模型路由" : "模型路由暂不可读";
    const news = snapshot?.news || {};
    const locationName = environment.locationName || "";
    const balanceEnabled = snapshot?.contact?.balanceAwarenessEnabled;
    const balanceAllowed = snapshot?.contact?.automaticAllowed;
    const balanceSourceConfigured = balanceConfig?.source_state === "configured";
    const balanceSourceInvalid = balanceConfig?.source_state === "invalid";
    const balanceDraftObservation = balanceResultIsDraft && balanceTestResult?.available === true ? balanceTestResult : null;
    const balanceObservation = balanceDraftObservation || (balanceStatus
      ? balanceStatus.available ? balanceStatus : null
      : balanceTestResult?.available === true ? balanceTestResult : null);
    const balanceObservationIsDraft = balanceObservation === balanceDraftObservation && !!balanceDraftObservation;
    const balanceValue = !ready ? `${balanceSourceConfigured ? "数据源已配置；" : ""}余额联系设置${configurationState === "读取失败" ? "读取失败" : "正在读取"}` : balanceEnabled === true
      ? balanceAllowed === true ? "服务余额联系已启用" : "余额契机已开，但后台自动联系未开启"
      : balanceSourceConfigured ? "数据源已配置，余额联系未开启"
        : balanceSourceInvalid ? "余额数据源配置无效，联系未开启"
          : "服务余额联系契机未启用";
    const balanceTag = !ready ? configurationFailed ? "读取失败" : "读取中" : balanceEnabled === true
      ? balanceAllowed === true ? "主动联系" : "总开关受限"
      : balanceSourceConfigured ? "数据源已配置" : balanceSourceInvalid ? "待修复" : ready ? "未启用" : "待读取";
    const balanceDetail = !ready ? "余额联系条件尚未核对；可展开配置数据源，或重新读取状态。"
      : balanceSourceConfigured && balanceEnabled !== true
      ? "数据源已配置；还需在主动联系的外部触发源中开启余额感知。"
      : "仅在服务余额达到阈值时作为主动联系素材，不会持续注入日常对话；不表示用户欠款。";
    const searchSource = news.sourceKind === "search";
    const dailySource = /AI\s*(早报|日报)|橘鸦/i.test(news.label || "");
    const explorationEnabled = news.explorationEnabled === true;
    const sourceAssembled = news.assembled ?? news.ready;
    const networkSucceeded = news.networkStatus === "succeeded";
    const networkLabel = news.networkStatus === "failed" ? "上次网络检查失败"
      : news.networkStatus === "succeeded" ? "网络检查通过"
        : sourceAssembled ? "网络尚未检查" : "来源未装配";
    const externalValue = !ready ? configurationFailed ? "外部来源设置读取失败" : "正在读取外部来源" : searchSource
      ? explorationEnabled ? networkSucceeded ? news.label || "网络搜索检查通过" : sourceAssembled ? `网络搜索已配置 · ${networkLabel}` : "网络探索已启用，搜索源未装配" : "网络探索未启用"
      : dailySource ? `AI 日报已装配 · ${networkSucceeded ? "后台持续维护" : networkLabel}`
        : sourceAssembled ? `${news.label || "资讯来源"} · ${networkSucceeded ? "后台持续维护" : networkLabel}`
          : ready ? "尚未配置外部来源" : "正在读取外部来源";
    const externalState = !ready ? "pending" : searchSource && explorationEnabled
      ? networkSucceeded ? "ready" : "pending"
      : networkSucceeded ? "ready" : sourceAssembled ? "pending" : "off";
    const externalDetail = [
      searchSource ? "AstrBot 网页搜索或自定义搜索源" : dailySource ? "内置 AI 日报来源" : `来源类型 ${news.sourceKind || "未选择"}`,
      news.explorationInterests ? `兴趣：${news.explorationInterests}` : "未指定搜索兴趣",
      Number.isInteger(news.explorationIntervalHours) ? `最短间隔 ${news.explorationIntervalHours} 小时` : "",
      Number.isInteger(news.explorationResultCount) ? `每次最多 ${news.explorationResultCount} 条` : "",
      Number.isInteger(news.itemCount) ? `最近检查 ${news.itemCount} 条` : "",
      networkLabel,
      news.lastAttemptAt ? `最近尝试 ${news.lastAttemptAt}` : "",
      news.lastCheckedAt ? `最近同步 ${news.lastCheckedAt}` : "",
    ].filter(Boolean).join(" · ");
    const weatherValue = weather.state === "available"
      ? `${weather.location || environment.locationName || "当前位置"} · ${weather.condition || "天气已读取"}${weather.temperature_c == null ? "" : ` · ${weather.temperature_c}°C`}`
      : weather.state === "disabled" ? "天气感知未启用"
        : weather.state === "unconfigured" ? "尚未设置天气地点"
          : runtimeState === "loading" ? "正在读取天气实况" : "天气实况暂不可用";
    const weatherDetail = weather.state === "available"
      ? `${weather.provider || "天气来源"} · 观测 ${ageLabel(weather.observed_at)}${weather.humidity_pct == null ? "" : ` · 湿度 ${weather.humidity_pct}%`}${weather.wind_kph == null ? "" : ` · 风速 ${weather.wind_kph} km/h`}`
      : weather.state === "disabled" ? "启用后总览显示当前实况；私聊询问天气时按需读取，日常计划也可参考。"
        : weather.state === "unconfigured" ? "在下方环境配置设置角色地点。"
          : "实况读取失败时不会沿用旧数据冒充当前天气。";
    const routeLabel = (route, pending) => route?.model || (pending ? "正在读取模型路线" : "尚未分配");
    const routeSourceLabel = source => ({default:"插件默认", host_conversation:"AstrBot 会话",
      installation:"安装级", role:"角色默认", window:"当前窗口"}[source] || "上级配置");
    const detailForRoute = route => route
      ? `Provider ${route.provider}${route.fallback ? ` · 备用 ${route.fallback}` : " · 无备用"} · 来源 ${routeSourceLabel(route.source)}`
      : modelState === "error" ? "任务路由暂不可读" : "此任务当前没有可用路由";
    const voiceLanguage = voiceStatus?.languages?.find(item => /^(zh|zh[-_])|中文/i.test(item.k || item.n || ""))
      || voiceStatus?.languages?.[0];
    const voiceProvider = voiceStatus?.providers?.find(item => item.id === voiceLanguage?.bind);
    const voiceValue = voiceState === "loading" ? "正在读取语音绑定"
      : voiceState === "error" ? "TTS 绑定暂不可读"
        : voiceStatus?.voice_enabled === false ? "语音回复未启用"
          : voiceProvider?.model || voiceProvider?.label || voiceProvider?.name || voiceLanguage?.bind || "尚未绑定 TTS";
    const generated = imageStatus?.actions?.find(item => item.action === "image.generate");
    const imageRoute = modelRoute("image.generate");
    const preferredImage = imageStatus?.preferred_image && typeof imageStatus.preferred_image === "object"
      ? imageStatus.preferred_image : null;
    /* image.native is the native action owner, not a model.  The status API
       projects the first model in the configured generate queue so this card
       shows the same concrete model as the image configuration page. */
    const preferredImageModel = typeof preferredImage?.model === "string" ? preferredImage.model : "";
    const imageValue = imageState === "loading" ? "正在读取生图状态"
      : imageState === "error" ? "生图状态暂不可读"
        : imageRoute?.model || preferredImageModel || generated?.provider_id || generated?.owner || "尚未分配生图模型";
    const imageDetail = imageRoute
      ? `${generated?.owner ? `${generated.owner} · ` : ""}${imageRoute.model} · ${modelAvailabilityLabel(generated?.state)}`
      : preferredImageModel
        ? `${preferredImage.name || preferredImage.route || "在线生图模型"} · ${preferredImage.enabled ? "首选" : "已停用"}`
        : generated?.owner
          ? `${generated.owner} · ${modelAvailabilityLabel(generated.state)}`
          : "Native 生图动作与扩展由模型与连接页管理。";
    const visionRoute = modelRoute("vision.review") || modelRoute("screen.interpretation");
    const analysisProvider = semanticStatus?.settings?.analysis_provider;
    const events = upcomingDates(snapshot);
    const alertList = weather.state === "available" && Array.isArray(weather.alerts) ? weather.alerts.slice(0, 4) : [];
    const screenValue = screenResult.state === "available" ? screenResult.interpretation || "已有屏幕解读"
      : screenResult.state === "blocked" ? reasonLabel(screenResult.reason)
        : screenResult.state === "empty" ? "暂无已保存解读"
          : runtimeState === "loading" ? "正在核对屏幕权限" : "屏幕解读暂不可读";
    const mobileValue = mobile.location?.available
      ? mobile.location.label || "附近位置已读取"
      : runtimeState === "loading" ? "正在读取定位状态"
        : runtimeState === "error" ? "定位状态读取失败"
          : realityResult.reason ? reasonLabel(realityResult.reason)
        : realityResult.state === "ready" ? "授权已就绪，暂无位置快照"
          : "暂无已授权的位置快照";
    const device = mobile.device || {}, activity = mobile.activity || {};
    const health = mobile.health && typeof mobile.health === "object" ? mobile.health : {};
    const healthReadings = Array.isArray(health.measurements) ? health.measurements : [];
    const healthLabels = {heart_rate:"心率", steps:"步数", sleep_duration:"睡眠", active_calories:"活动消耗",
      body_temperature:"体温", oxygen_saturation:"血氧", weight:"体重", exercise_minutes:"运动时长"};
    const healthParts = healthReadings.slice(0, 4).map(item => {
      const label = typeof item.label === "string" && item.label ? item.label
        : healthLabels[item.type] || (typeof item.type === "string" ? item.type : "测量");
      const value = Number.isFinite(Number(item.value)) ? Number(item.value) : item.value;
      return `${label} ${value}${item.unit ? ` ${item.unit}` : ""}`;
    });
    if (typeof health.activity === "string" && health.activity) healthParts.push(`活动 ${health.activity}`);
    const healthAvailable = health.available === true && healthParts.length > 0;
    const healthReason = typeof health.reason === "string" ? health.reason : "";
    const healthSourceDisabled = healthReason === "telemetry_disabled" || healthReason === "mobile_gateway_disabled";
    const healthValue = healthAvailable ? healthParts.join(" · ")
      : runtimeState === "loading" ? "正在读取健康摘要"
        : runtimeState === "error" ? "健康摘要读取失败"
          : healthReason === "telemetry_disabled" ? "手机健康上报未开启"
            : healthReason === "no_recent_telemetry" ? "等待手机上报健康数据"
              : healthReason === "telemetry_expired" ? "最近健康数据已过期"
                : health.available === true ? "最近上报没有可用测量"
                  : realityResult.reason ? reasonLabel(realityResult.reason) : "暂无已授权的健康摘要";
    const phoneParts = [];
    if (device.available === true) {
      if (Number.isFinite(device.battery_percent)) phoneParts.push(`电量 ${device.battery_percent}%`);
      if (device.charging === true) phoneParts.push("充电中");
      const appState = ({foreground:"前台运行",background:"后台运行"})[device.app_state];
      if (appState) phoneParts.push(appState);
    }
    if (activity.available === true && activity.label) phoneParts.push(`${activity.stale ? "上次活动" : "活动"} · ${activity.label}`);
    const phoneAvailable = phoneParts.length > 0;
    const phoneValue = phoneAvailable ? phoneParts.join(" · ")
      : runtimeState === "loading" ? "正在读取手机状态"
        : runtimeState === "error" ? "手机状态读取失败"
          : realityResult.reason ? reasonLabel(realityResult.reason) : "暂无已授权的手机状态快照";
    const cameraObservation = cameraResult.observation || {};
    const cameraValue = cameraResult.state === "available"
      ? cameraResult.answer_available
        ? cameraObservation.summary || cameraObservation.scene_description || cameraObservation.purpose_answer || "最近单帧已识别"
        : cameraObservation.uncertainty || cameraResult.message || "最近单帧结果不确定"
      : runtimeState === "loading" ? "正在读取摄像头状态"
        : runtimeState === "error" ? "摄像头状态读取失败"
          : cameraResult.reason ? reasonLabel(cameraResult.reason)
        : cameraResult.state === "ready" ? "授权已就绪，暂无识别结果" : "暂无可用的摄像头结果";
    const recalls = Array.isArray(recallStatus?.entries) ? recallStatus.entries : [];
    const recallValue = recallState === "loading" ? "正在读取撤回记录"
      : recallState === "error" ? "撤回记录暂不可读"
        : recallStatus?.enabled === false ? "撤回转录未启用"
        : recalls.length ? `当前窗口 ${recalls.length} 条可读记录` : "近期没有可读记录";
    const recallDetail = recalls[0]
      ? `${recalls[0].local_time || ageLabel(recalls[0].withdrawn_at)} · ${String(recalls[0].text || "").slice(0, 72)}`
      : recallState === "error" ? (
        recallError === "core_access_required"
          ? "仅核心管理员可读这个角色窗口的撤回记录；当前 Dashboard 账号权限不足。"
          : recallError === "recall_scope_denied"
            ? "这个角色窗口还没解析出可读范围；确认它已绑定会话后再试。"
            : `读取失败（${recallError || "原因未知"}）· 检查当前角色窗口的授权与撤回转录开关。`)
        : recallStatus?.enabled === false ? "打开下方撤回转录设置后，才会显示授权范围内的记录。"
          : "仅显示当前会话授权范围内的记录，不会重新进入对话。";
    const configuredDevices = (mihomeStatus?.devices || []).filter(item => item?.configured === true || item?.aliases?.length);
    const onlineCount = configuredDevices.filter(item => item.online === true).length;
    const offlineCount = configuredDevices.filter(item => item.online === false).length;
    const unknownCount = configuredDevices.length - onlineCount - offlineCount;
    const mihomeEnabled = mihomeState === "ready" && mihomeStatus?.enabled === true;
    const mihomeSummary = mihomeEnabled
      ? configuredDevices.length
        ? `${onlineCount} 在线 · ${offlineCount} 离线 · ${unknownCount} 未核实 · ${configuredDevices.length} 台已配置`
        : "只读设备状态已开启，尚无已映射设备"
      : "";
    const injections = Array.isArray(runtimeSnapshot?.other_injections)
      ? runtimeSnapshot.other_injections.filter(item => typeof item === "string" || item?.label || item?.name).slice(0, 6) : [];
    const injectionLabels = injections.map(item => typeof item === "string" ? item : item.label || item.name);
    /* === 感知通道单一数据源 ===
       仪表盘只登记"当前真实存在的通道"，
       每条都带上它自己的读数与说明。不给通道编造数值、百分比或统一总数；
       读不到就说读不到（未启用 / 待授权），不拿旧数据冒充当前值。 */
    const percepts = [];
    const channel = (id, board, spec) => percepts.push({id, board, ...spec});
    const tabModel = "config-models:perception-detail-model-route";
    const tabPlace = "config-environment:perception-detail-place";
    const tabDevice = "config-screen:perception-detail-device-access";
    const tabCalendar = "config-models:perception-detail-context";
    const tabHealth = "config-health:perception-detail-health";

    const hostRoute = modelInfo?.host || {};
    const coreValue = hostRoute.model || routeLabel(modelInfo, modelState === "loading");
    const coreDetail = modelInfo
      ? `Provider ${modelInfo.provider}${modelInfo.fallback ? ` · 备用 ${modelInfo.fallback}` : " · 无备用"} · AstrBot 配置：${hostRoute.config?.name || "AstrBot 默认配置"}`
      : detailForRoute(modelInfo);
    channel("model-conversation", "models", {tab:tabModel, modelTab:"fn", icon:"cpu", title:"核心管理员对话",
      value:coreValue, detail:coreDetail,
      state:modelInfo ? "ready" : "pending", tag:modelInfo ? "实际路由" : modelStateLabel});
    channel("model-tts", "models", {tab:tabModel, modelTab:"voice", icon:"audio-lines", title:"语音模型",
      value:voiceValue,
      detail:voiceLanguage ? `${voiceLanguage.n || voiceLanguage.k} · ${modelAvailabilityLabel(voiceStatus?.catalog_state)}` : "按语种绑定，配置在模型与连接页。",
      state:voiceState === "error" || voiceState === "loading" ? "pending" : voiceStatus?.voice_enabled === false || !voiceLanguage?.bind ? "off" : "ready",
      tag:voiceState === "error" ? "读取失败" : voiceState === "loading" ? "读取中" : voiceStatus?.voice_enabled === false ? "未启用" : voiceLanguage?.bind ? "已绑定" : "未绑定"});
    channel("model-image", "models", {tab:tabModel, modelTab:"image", icon:"image", title:"生图模型",
      value:imageValue,
      detail:imageDetail,
      state:imageState !== "ready" ? "pending"
        : imageRoute ? "ready"
        : preferredImageModel ? preferredImage.enabled ? "ready" : "off"
        : generated?.state === "ready" ? "ready" : generated?.state === "disabled" ? "off" : "pending",
      tag:imageState === "error" ? "读取失败" : imageState === "loading" ? "读取中"
        : imageRoute ? "已分配"
        : preferredImageModel ? preferredImage.enabled ? "首选模型" : "已停用"
        : generated?.state === "ready" ? "可调用" : generated?.state === "disabled" ? "已关闭" : "待检查"});
    if (visionRoute || analysisProvider) channel("model-vision", "models", {tab:tabModel, modelTab:"fn", icon:"scan-eye", title:"视觉模型",
      value:visionRoute?.model || analysisProvider,
      detail:visionRoute ? detailForRoute(visionRoute) : "语义视觉分析 Provider",
      state:"ready", tag:"已配置"});

    /* 日期与历法：服务端 project_local_date 的产出必须有出口。
       每一项都只在服务端真的给出时才显示——历法项各有一个总开关，没开就是没开。 */
    const calendarView = (() => {
      const cal = calendarContext();
      if (!cal) return null;
      const labels = [];
      if (typeof cal.weekday === "string" && cal.weekday) labels.push(cal.weekday);
      if (typeof cal.period === "string" && cal.period) labels.push(cal.period);
      const detail = [];
      if (cal.lunar_status === "available" && typeof cal.lunar_date === "string" && cal.lunar_date)
        detail.push(cal.lunar_date.startsWith("农历") ? cal.lunar_date : `农历${cal.lunar_date}`);
      const terms = [];
      const today = cal.automatic_solar_terms?.today;
      if (Array.isArray(today)) for (const item of today) for (const name of item?.names || []) terms.push(name);
      const listed = cal.configured_annual_calendar?.listed_today?.solar_terms;
      if (Array.isArray(listed)) terms.push(...listed);
      if (terms.length) detail.push(`节气 ${terms.slice(0, 2).join("、")}`);
      if (cal.almanac?.status === "available" && cal.almanac.yi && cal.almanac.ji)
        detail.push(`宜 ${cal.almanac.yi} · 忌 ${cal.almanac.ji}`);
      return {
        labels: labels.length ? labels.join(" · ") : "历法已启用",
        detail: detail.length ? detail.join(" · ") : "农历、节气与黄历各有独立开关；没有明细时先检查这几项。",
      };
    })();
    if (calendarView) channel("env-calendar", "environment", {tab:tabCalendar, icon:"calendar-days", title:"日期与历法",
      value:calendarView.labels, detail:calendarView.detail, state:"ready", tag:"服务端日历"});
    channel("env-location", "environment", {tab:tabPlace, icon:"map-pin", title:"所在地",
      value:locationName || (!ready ? `角色地点${configurationState}` : "尚未设置角色地点"),
      /* 已配置时不再补一句「用角色配置的区域、不显示经纬度」：值本身已经写着区域，
         经纬度这件事属于「在哪里设置」的说明，点击后的预览卡里已经有用途与口径。 */
      detail:locationName ? "" : "在下方环境配置里填写角色地点。",
      state:locationName ? "ready" : ready ? "off" : "pending", tag:locationName ? "已配置" : !ready ? configurationFailed ? "读取失败" : "读取中" : "待设置"});
    channel("env-weather", "environment", {tab:tabPlace, icon:"cloud-sun", title:"天气实况",
      value:weather.state === "available"
        ? `${weather.condition || "天气已读取"}${weather.temperature_c == null ? "" : ` · ${weather.temperature_c}°C`}`
        : weatherValue,
      detail:weatherDetail,
      state:weather.state === "available" ? "ready" : weather.state === "disabled" ? "off" : "pending",
      tag:weather.state === "available" ? "实况" : weather.state === "disabled" ? "未启用" : weather.state === "unconfigured" ? "待配置" : runtimeState === "loading" ? "读取中" : "读取失败"});
    if (alertList.length) channel("env-alert", "environment", {tab:tabPlace, icon:"triangle-alert", title:"天气预警",
      value:alertList[0].title || alertList[0].event_name || "有官方预警",
      detail:`${({minor:"蓝色 · 一般",moderate:"黄色 · 较重",severe:"橙色 · 严重",extreme:"红色 · 特别严重"}[alertList[0].severity] || alertList[0].severity || "级别待核对")}${alertList[0].ends_at ? ` · 至 ${new Date(alertList[0].ends_at).toLocaleString("zh-CN")}` : ""}${alertList.length > 1 ? ` · 另有 ${alertList.length - 1} 条` : ""}`,
      state:"ready", tag:`${alertList.length} 条预警`});
    channel("ext-external", "external", {tab:"config-news:perception-detail-external", icon:"globe-2", title:"外部资讯",
      value:externalValue, detail:externalDetail + (news.failureCode ? ` · ${news.failureCode}` : ""),
      state:externalState,
      tag:!ready ? configurationFailed ? "读取失败" : "读取中" : externalState === "ready" ? "检查通过" : externalState === "off" ? "未启用" : news.networkStatus === "failed" ? "检查失败" : "待检查"});
    channel("ext-balance", "external", {tab:"config-balance:perception-detail-balance-source", icon:"wallet-cards", title:"余额感知",
      value:balanceValue, detail:[balanceDetail, balanceObservation ? `${balanceResultIsDraft ? "草稿测试" : "最近查询"} ${formatBalanceAmount(balanceObservation.amount)} ${balanceObservation.currency_label || "额度"}` : "尚无余额查询结果"].join(" · "),
      state:balanceObservation ? "ready" : balanceSourceConfigured || balanceSourceInvalid || balanceEnabled === true || !ready || balanceError || balanceTestError || balanceStatusError ? "pending" : "off",
      tag:balanceTag});

    const realityNeedsConsent = /capability_role_disabled|capability_consent_missing|consent/.test(realityResult.reason || "");
    channel("device-location", "devices", {tab:"config-screen:perception-detail-reality-access", icon:"smartphone", title:"当前定位",
      value:mobileValue,
      /* 授权边界写在点开后的「使用边界」里；状态行只留时间，不再重复一遍隐私说明。 */
      detail:mobile.location?.available ? `最近读取 ${ageLabel(mobile.location.captured_at)}`
        : realityNeedsConsent ? "手机读取一次授权后，定位和手机状态一起可用。" : "只回显对话已经读取的位置，不主动采集。",
      state:mobile.location?.available ? "ready" : realityResult.reason === "capability_role_disabled" ? "off" : "pending",
      tag:mobile.location?.available ? "已读取" : realityNeedsConsent ? "需手机读取" : runtimeState === "error" ? "读取失败" : runtimeState === "loading" ? "读取中" : "暂无快照"});
    channel("device-screen", "devices", {tab:tabDevice, icon:"scan-eye", title:"识屏",
      value:screenValue,
      detail:screenResult.state === "available" ? `${screenResult.surface || "屏幕画面"} · 采集 ${ageLabel(screenResult.captured_at)} · 解读 ${ageLabel(screenResult.created_at)}`
        : "只显示最近完成的授权解读；刷新不会触发采集。",
      state:screenResult.state === "available" ? "ready" : "pending",
      tag:screenResult.state === "available" ? "有结果" : runtimeState === "error" ? "读取失败" : runtimeState === "loading" ? "读取中" : screenResult.state === "blocked" ? "需授权" : "暂无结果"});
    channel("device-phone", "devices", {tab:"config-screen:perception-detail-reality-access", icon:"smartphone", title:"手机状态",
      value:phoneValue,
      /* 不再复述卡面里已经有的电量/状态字段，只回答「多久以前、刷新会不会重新采集」。 */
      detail:realityNeedsConsent ? "与当前定位共用一次手机读取授权；不会主动采集。" : "最近一次授权上报 · 刷新不会触发采集",
      state:phoneAvailable ? "ready" : realityResult.reason === "capability_role_disabled" ? "off" : "pending",
      tag:phoneAvailable ? "有快照" : realityNeedsConsent ? "需手机读取" : runtimeState === "error" ? "读取失败" : runtimeState === "loading" ? "读取中" : "暂无快照"});
    const healthBlocked = /consent|capability_role_disabled|reality_provider|reality_tool/.test(realityResult.reason || "");
    const huaweiReady = huaweiHealthState === "ready";
    const huaweiBound = huaweiReady && huaweiHealth?.bound === true;
    const huaweiLatest = huaweiBound && huaweiHealth?.latest && typeof huaweiHealth.latest === "object"
      ? huaweiHealth.latest : null;
    const huaweiSummary = huaweiLatest && typeof huaweiLatest.summary === "string"
      ? huaweiLatest.summary.split("\n").map(line => line.trim()).find(Boolean) || "" : "";
    const healthTitleValue = huaweiBound
      ? huaweiLatest ? huaweiSummary.slice(0, 120) : "已绑定 · 尚无健康摘要"
      : healthAvailable ? healthValue
        : huaweiReady ? "运动健康账号未绑定"
          : huaweiHealthState === "error" ? "运动健康状态暂不可读"
            : "正在读取运动健康";
    const healthTitleDetail = huaweiBound
      ? huaweiLatest ? `运动健康 · ${ageLabel(huaweiLatest.observed_at)} · 只作交流背景，不做医疗判断`
        : "账号已绑定；在「健康感知」页拉取最新摘要"
      : healthAvailable ? `手机端最近授权上报 ${ageLabel(health.captured_at)}${huaweiReady ? " · 运动健康账号未绑定" : ""} · 只作交流背景`
        : huaweiReady ? "在「感知 → 健康感知」里绑定运动健康账号"
          : huaweiHealthState === "error" ? huaweiHealthError || "运动健康状态暂不可读，请到健康感知页重新读取"
            : healthSourceDisabled ? "打开「感知 → 健康感知」里的健康摘要，并在「运动健康」卡绑定账号。"
              : healthReason === "no_recent_telemetry" ? "接收已开启，等待手机端下一次上报；刷新不会触发采集。"
                : healthReason === "telemetry_expired" ? "上次上报已超过有效期；等手机重新上报后再使用。"
                  : healthBlocked ? "打开健康感知后，在「运动健康」卡完成 Cookie 或账号密码登录。"
                    : "只显示最近一次已授权健康摘要；运动健康账号绑定和取数入口在健康感知页。";
    const healthState = huaweiBound ? huaweiLatest ? "ready" : "pending"
      : healthAvailable ? "ready" : huaweiReady ? "off" : huaweiHealthState === "error" ? "pending"
        : healthSourceDisabled || healthBlocked ? "off" : "pending";
    const healthTag = huaweiBound ? huaweiLatest ? "已同步" : "待拉取"
      : healthAvailable ? "手机快照" : huaweiReady ? "未绑定"
        : huaweiHealthState === "error" ? "读取失败" : "读取中";
    channel("device-health", "devices", {tab:tabHealth, icon:"heart-pulse", title:"运动健康",
      value:healthTitleValue, detail:healthTitleDetail, state:healthState, tag:healthTag});
    channel("device-camera", "devices", {tab:"config-screen:perception-detail-reality-access", icon:"camera", title:"摄像头",
      value:cameraValue,
      detail:cameraResult.state === "available" ? `最近读取 ${ageLabel(cameraResult.captured_at)} · ${cameraResult.purpose || "按本轮明确请求"}`
        : realityNeedsConsent ? "摄像头单独授权，只在明确请求时读取单帧。" : "只显示已经完成的单帧识别；不会因刷新而拍摄。",
      state:cameraResult.state === "available" && cameraResult.answer_available === true ? "ready" : "pending",
      tag:cameraResult.state === "available" ? "已识别" : realityNeedsConsent ? "需摄像头授权" : cameraResult.state === "blocked" ? "需授权" : cameraResult.state === "ready" ? "待请求" : "未就绪"});
    channel("device-recalls", "external", {tab:"config-recall:perception-detail-recalls", icon:"message-square-quote", title:"撤回记录",
      value:recallValue, detail:recallDetail,
      state:recallState === "error" || recallState === "loading" ? "pending" : recalls.length ? "ready" : "off",
      tag:recallState === "error" ? "读取失败" : recallState === "loading" ? "读取中" : recallStatus?.enabled === false ? "未启用" : recalls.length ? "只读记录" : "无记录"});
    if (mihomeEnabled) channel("device-mihome", "devices", {tab:"config-screen:perception-detail-mihome", icon:"plug-zap", title:"米家设备",
      value:mihomeSummary,
      detail:configuredDevices.slice(0, 3).map(item => `${item.name || item.aliases?.[0] || "设备"} ${item.online === true ? "在线" : item.online === false ? "离线" : "状态未知"}`).join(" · ")
        + (mihomeStatus?.snapshot_at ? ` · 同步 ${mihomeStatus.snapshot_at}` : ""),
      state:offlineCount ? "pending" : "ready", tag:offlineCount ? `${offlineCount} 台离线` : "设备正常"});
    if (injectionLabels.length) channel("ext-injection", "external", {tab:"config-injection:perception-detail-injection", icon:"layers-3", title:"其他注入",
      value:`${injectionLabels.length} 项进入本轮上下文`, detail:injectionLabels.join(" · "), state:"ready", tag:"本轮材料"});

    const activeCount = percepts.filter(item => item.state === "ready").length;
    /* === 顶部态势板 ===
       四张卡按能力簇分板，每块只用一个主视觉说话：环境卡给天气读数、模型卡给磁贴、
       设备与外部卡突出定位与余额，状态行显示实际读数、时间及缺失原因。
       卡头与通道直接选择仪表盘下方的真实配置页，不在卡内重复展开状态列表。 */
    const boardRows = id => percepts.filter(item => item.board === id);
    const boardSpecs = [
      {id:"environment", tone:"environment", icon:"cloud-sun", title:"时空 · 环境", tab:tabPlace},
      {id:"models", tone:"models", icon:"cpu", title:"模型能力", tab:tabModel, modelTab:"fn"},
      {id:"devices", tone:"devices", icon:"radar", title:"现实感知功能", tab:tabDevice},
      {id:"external", tone:"external", icon:"layers-2", title:"外部与记录", tab:"config-news:perception-detail-external"},
    ];
    const destinationAttrs = (destination, modelTab="") => `data-board-goto="${esc(destination)}" aria-controls="${esc(destination.split(":")[0])}" aria-expanded="false"${modelTab ? ` data-board-model-tab="${esc(modelTab)}"` : ""}`;
    const singleAttrs = (destination, title, itemId="", modelTab="") => `${destinationAttrs(destination, modelTab)} data-board-single="true" data-perception-item="${esc(itemId)}" data-item-title="${esc(title)}"`;
    const customAttrs = (destination, title, itemId, modelTab="") => `id="perception-channel-${esc(itemId)}" ${singleAttrs(destination, title, itemId, modelTab)} title="${esc(title)}" aria-label="${esc(`${title}。打开这一项的设置`)}"`;
    /* 标题与 aria 只拼接真正存在的读数：删掉一句说明后不该留下尾随的「 · 」。 */
    const channelAttrs = item => {
      const readout = [item.tag, item.value, item.detail].filter(Boolean).join(" · ");
      const spoken = [item.tag, item.value, item.detail].filter(Boolean).join("。");
      return `id="perception-channel-${item.id}" ${singleAttrs(item.tab, item.title, item.id, item.modelTab)} title="${esc(readout)}" aria-label="${esc(`${item.title}：${spoken}。打开这一项的设置`)}"`;
    };
    const channelById = id => percepts.find(item => item.id === id);
    const timeMarkup = (value, fallback="") => {
      const date = typeof value === "number" ? new Date(value * 1000) : new Date(value || "");
      return Number.isNaN(date.getTime()) ? fallback ? `<small class="perception-reading-age">${esc(fallback)}</small>` : ""
        : `<time class="perception-reading-age" datetime="${esc(date.toISOString())}" title="${esc(date.toLocaleString("zh-CN", {timeZone:clockZone()}))}">${esc(ageLabel(value))}</time>`;
    };
    const badgeMarkup = (state, label) => `<span class="perception-reading-badge is-${state}"><i class="perception-dot" aria-hidden="true"></i>${esc(label)}</span>`;
    const readingRow = (item, label, value, observedAt, fallback="") => `<button type="button" class="perception-reading-row is-${item.state}" ${channelAttrs(item)}>
      <span class="perception-reading-icon">${icon(item.icon)}</span><strong>${esc(label)}</strong>
      <span class="perception-reading-value">${esc(value)}</span>${timeMarkup(observedAt, fallback)}<i class="perception-dot" aria-hidden="true"></i>
    </button>`;
    const deviceCard = (() => {
      const location = channelById("device-location"), screen = channelById("device-screen"), phone = channelById("device-phone");
      const health = channelById("device-health"), camera = channelById("device-camera"), mihome = channelById("device-mihome");
      const cameraAuthorized = cameraResult.state === "available" || cameraResult.state === "ready";
      const cameraTag = cameraAuthorized ? "已授权" : runtimeState === "error" ? "读取失败" : runtimeState === "loading" ? "读取中" : /consent/.test(cameraResult.reason || "") ? "待授权" : "未就绪";
      const mihomeLabel = mihome ? mihomeSummary : mihomeState === "disabled" ? "未启用" : mihomeState === "loading" ? "读取中" : "暂不可读";
      return `<div class="perception-reading-card">
        <button type="button" class="perception-feature is-location is-${location.state}" ${channelAttrs(location)}>
          <span class="perception-feature-icon">${icon("map-pin")}</span>
          <span class="perception-feature-copy"><small>当前定位</small><strong>${esc(mobileValue)}</strong><span>${mobile.location?.available ? "最近授权快照" : "点击配置定位与手机授权"}</span></span>
          <span class="perception-feature-meta">${badgeMarkup(location.state, mobile.location?.available ? "最近一次定位" : location.tag)}${timeMarkup(mobile.location?.available ? mobile.location.captured_at : null)}</span>
        </button>
        ${readingRow(screen, "识屏", screenResult.state === "available" ? `上次识别 · ${screenResult.surface || "屏幕画面"}` : screenValue, screenResult.state === "available" ? screenResult.created_at || screenResult.captured_at : null)}
        ${readingRow(phone, "手机状态", phoneValue, phoneAvailable ? device.last_report_at || activity.last_report_at || realityResult.captured_at : null)}
        ${readingRow(health, "健康摘要", healthValue, healthAvailable ? health.captured_at : null)}
        <div class="perception-board-footer"><span>设备授权</span><div class="perception-authorization-chips">
          <button type="button" class="perception-authorization-chip is-${cameraAuthorized ? "ready" : camera.state}" ${channelAttrs(camera)}><i class="perception-dot" aria-hidden="true"></i>摄像头 · ${esc(cameraTag)}</button>
          <button type="button" class="perception-authorization-chip is-${mihome?.state || (mihomeState === "disabled" ? "off" : "pending")}" ${mihome ? channelAttrs(mihome) : destinationAttrs(tabDevice)} title="${esc(mihome?.detail || mihomeLabel)}"><i class="perception-dot" aria-hidden="true"></i>米家 · ${esc(mihomeLabel)}</button>
        </div></div>
      </div>`;
    })();
    const externalCard = (() => {
      const balance = channelById("ext-balance"), external = channelById("ext-external"), recall = channelById("device-recalls"), injection = channelById("ext-injection");
      const balanceReadLabel = balanceObservation ? balanceObservationIsDraft ? "草稿测试结果" : "最近一次查询"
        : balanceTesting || balanceStatusLoading ? "查询中" : balanceTestError || balanceError || balanceStatusError ? "读取失败"
          : balanceStatus?.state === "not_configured" || balanceConfig ? "未配置" : balanceConfigLoading ? "读取中" : balanceSourceInvalid ? "配置无效" : "等待读取";
      const balanceReadDetail = balanceTestError || balanceError || balanceStatusError || (balanceObservation ? balanceObservation.provider_label || "NewAPI"
        : balanceSourceInvalid ? "请检查数据源配置" : balanceSourceConfigured ? "读取服务余额中" : "配置数据源后读取服务余额");
      const newsSummary = !ready ? externalValue : networkSucceeded
        ? `${Number.isInteger(news.itemCount) && news.itemCount >= 0 ? `最近检查 ${news.itemCount} 条` : "最近检查通过"} · ${news.label || "资讯来源"}`
        : news.networkStatus === "failed" ? "上次检查失败 · 请检查来源" : externalValue;
      return `<div class="perception-reading-card">
        <button type="button" class="perception-feature is-balance is-${balance.state}" ${channelAttrs(balance)}>
          <span class="perception-feature-icon">${icon("circle-dollar-sign")}</span>
          <span class="perception-feature-copy"><small>余额感知</small><strong>${balanceObservation ? `<span class="perception-balance-unit">${esc(balanceObservation.currency_label || "额度")}</span> ${esc(formatBalanceAmount(balanceObservation.amount))}` : "尚无余额读数"}</strong><span>${esc(balanceReadDetail)}</span></span>
          <span class="perception-feature-meta">${badgeMarkup(balance.state, balanceReadLabel)}${timeMarkup(balanceObservation?.observed_at)}</span>
        </button>
        ${readingRow(external, "外部资讯", newsSummary, news.lastAttemptAt || news.lastCheckedAt)}
        ${readingRow(recall, "撤回记录", recallState === "ready" && recallStatus?.enabled !== false ? `当前窗口 ${recalls.length} 条可读记录` : recallValue, recalls[0]?.withdrawn_at, recalls[0]?.local_time)}
        <div class="perception-board-footer">${injection ? `<button type="button" class="perception-injection-summary" ${channelAttrs(injection)}>${icon("layers-3")}<span>其他注入 · 本轮 ${injectionLabels.length} 项 · ${esc(injectionLabels.join("、"))}</span></button>` : '<span>本轮暂无其他注入</span>'}</div>
      </div>`;
    })();
    const weatherIconName = condition => {
      const text = String(condition || "");
      if (/雨|雪|雷|雾|霾/.test(text)) return "cloud-rain";
      if (/阴/.test(text)) return "cloud";
      return "cloud-sun";
    };
    /* 环境卡：地点、时间、更新时刻、预警状态、温度读数全靠排版层级说话，不写标签行。 */
    const weatherCard = (() => {
      const item = percepts.find(entry => entry.id === "env-weather");
      const available = weather.state === "available";
      const place = weather.location || locationName || (!ready ? `角色地点${configurationState}` : "尚未设置角色地点");
      const alertState = alertList.length ? "实时预警" : runtimeState === "error" ? "读取失败"
        : runtimeState === "loading" ? "待读取" : weather.state === "disabled" || weather.state === "unconfigured" ? "未启用"
          : !available || configurationFailed ? "暂不可读" : environment.alertsReady ? "当前无预警" : "未配置";
      const metaBits = [weather.humidity_pct == null ? "" : `湿度 ${weather.humidity_pct}%`,
        weather.wind_kph == null ? "" : `风 ${weather.wind_kph} km/h`].filter(Boolean);
      return `<div class="perception-wx">
        <div class="perception-wx-top">
          <button type="button" class="perception-wx-place" ${destinationAttrs(tabPlace)} aria-label="所在地：${esc(place)}。打开所在地与天气配置">${icon("map-pin")}<b>${esc(place)}</b></button>
          <button type="button" class="perception-wx-time" ${destinationAttrs(tabCalendar)} aria-label="打开日期与历法上下文配置" data-context-clock-time>--:--</button>
        </div>
        <div class="perception-wx-sub">
          <button type="button" class="perception-wx-date" ${destinationAttrs(tabCalendar)} aria-label="日期与历法：${esc(calendarView?.detail || "服务端日历暂不可用")}。打开日期与历法上下文配置"><i data-context-clock-date>${runtimeState === "loading" ? "日期与历法待读取" : ""}</i><i data-context-clock-lunar></i><em data-context-clock-tag hidden></em></button>
          <button type="button" class="perception-wx-chip is-${alertState === "实时预警" ? "alert" : alertState === "当前无预警" ? "ready" : "off"}" ${destinationAttrs(tabPlace)} title="${esc(alertList.map(item => item.title || item.event_name || "官方预警").join(" · "))}">${icon("triangle-alert")}<span>天气预警 · ${alertState}</span></button>
        </div>
        <button type="button" class="perception-wx-main" ${channelAttrs(item)}>
          <span class="perception-wx-icon">${icon(available ? weatherIconName(weather.condition) : "cloud")}</span>
          <span class="perception-wx-temp">${esc(available && weather.temperature_c != null ? String(weather.temperature_c) : "—")}<i>°C</i></span>
          <span class="perception-wx-meta">
            <strong>${esc(available ? weather.condition || "实况已读取" : item.value)}</strong>
            <small>${esc(metaBits.length ? metaBits.join(" · ") : item.detail)}</small>
          </span>
        </button>
        ${calendarView?.detail ? `<button type="button" class="perception-wx-calendar" ${destinationAttrs(tabCalendar)}>${esc(calendarView.detail)}</button>` : ""}
        <button type="button" class="perception-wx-events" ${destinationAttrs("config-environment:perception-detail-important-days")}>${icon("calendar-heart")}<span>${events.length ? events.map(event => `${esc(event.label)} · ${event.days === 0 ? "今天" : event.days === 1 ? "明天" : `${event.days} 天后`}`).join("；") : "暂无近期生日与纪念日"}</span></button>
      </div>`;
    })();
    const boardBadgeMarkup = (state, label) => `<span class="perception-reading-badge is-${esc(state)}"><i class="perception-dot" aria-hidden="true"></i>${esc(label)}</span>`;
    const heroMarkup = ({state="off", iconName="circle", title, value="", valueHtml="", detail="", detailHtml="", tag="", attrs="", tone="generic"}) =>
      `<button type="button" class="perception-hero is-${esc(tone)} is-${esc(state)}" ${attrs}>
        <span class="perception-hero-icon">${icon(iconName)}</span>
        <span class="perception-hero-copy"><small>${esc(title)}</small><strong>${valueHtml || esc(value)}</strong>${detail || detailHtml ? `<span>${detailHtml || esc(detail)}</span>` : ""}</span>
        ${boardBadgeMarkup(state, tag)}
        <span class="perception-hero-chevron">${icon("chevron-right")}</span>
      </button>`;
    const miniMarkup = ({state="off", iconName="circle", title, value="", valueHtml="", detail="", detailHtml="", tag="", attrs=""}) =>
      `<button type="button" class="perception-mini is-${esc(state)}" ${attrs}>
        <span class="perception-mini-icon">${icon(iconName)}</span>
        <span class="perception-mini-copy"><small>${esc(title)}</small><strong>${valueHtml || esc(value)}</strong>${detail || detailHtml ? `<span>${detailHtml || esc(detail)}</span>` : ""}</span>
        ${boardBadgeMarkup(state, tag)}
        <span class="perception-mini-chevron" aria-hidden="true">${icon("chevron-right")}</span>
      </button>`;
    const itemHero = (item, overrides={}) => heroMarkup({
      state:item.state, iconName:item.icon, title:item.title, value:item.value,
      detail:item.detail, tag:item.tag, attrs:channelAttrs(item), ...overrides,
    });
    const itemMini = (item, overrides={}) => miniMarkup({
      state:item.state, iconName:item.icon, title:item.title, value:item.value,
      detail:item.detail, tag:item.tag, attrs:channelAttrs(item), ...overrides,
    });
    const environmentBodyMarkup = () => {
      const alert = channelById("env-alert");
      const alertFailed = runtimeState === "error" || (!ready && configurationFailed);
      const alertMini = alert ? itemMini(alert) : miniMarkup({
        state: alertFailed ? "pending" : environment.alertsReady ? "ready" : "off",
        iconName:"triangle-alert", title:"天气预警",
        value: alertFailed ? "预警读取失败" : environment.alertsReady ? "当前无预警" : "预警状态未配置",
        detail: weather.state === "available" ? `${weather.provider || "天气来源"} · 已检查` : "天气不可用时不会沿用旧预警",
        tag: alertFailed ? "读取失败" : environment.alertsReady ? "无预警" : "未配置",
        attrs:customAttrs(tabPlace, "天气预警", "env-alert"),
      });
      const eventSummary = events.length
        ? events.map(event => `${event.label} · ${event.days === 0 ? "今天" : event.days === 1 ? "明天" : `${event.days} 天后`}`).join("；")
        : "暂无近期生日与纪念日";
      const datesMini = miniMarkup({
        state: events.length ? "ready" : "off", iconName:"calendar-heart", title:"重要日期",
        value:eventSummary, detail:"角色档案与私聊资料", tag:events.length ? `${events.length} 项` : "暂无",
        attrs:customAttrs("config-environment:perception-detail-important-days", "重要日期", "env-dates"),
      });
      const available = weather.state === "available";
      const temp = available && weather.temperature_c != null ? `${weather.temperature_c}°C` : "—";
      const weatherBits = [weather.condition || "天气待读取",
        weather.humidity_pct == null ? "" : `湿度 ${weather.humidity_pct}%`,
        weather.wind_kph == null ? "" : `风 ${weather.wind_kph} km/h`].filter(Boolean).join(" · ");
      const weatherItem = channelById("env-weather");
      const timeHero = heroMarkup({
        state:"ready", iconName:"clock", title:"日期与历法", tone:"time", tag:calendarView ? "服务端日历" : "本地",
        valueHtml:`<b data-context-clock-time>--:--:--</b><small class="perception-clock-zone" data-context-clock-zone>${esc(clockZone())}</small>`,
        detailHtml:`<span data-context-clock-date>日期与历法待读取</span> <i data-context-clock-lunar></i> <em class="perception-clock-tag" data-context-clock-tag hidden></em>${calendarView?.detail ? `<span class="perception-hero-extra">${esc(calendarView.detail)}</span>` : ""}`,
        attrs:customAttrs(tabCalendar, "日期与历法", calendarView ? "env-calendar" : "env-time"),
      });
      const weatherHero = weatherItem ? itemHero(weatherItem, {
        iconName: available ? weatherIconName(weather.condition) : "cloud",
        title:"天气实况", value: available ? weatherItem.value : temp, detail: available ? `${weatherBits}${weatherItem.detail ? ` · ${weatherItem.detail}` : ""}` : weatherItem.detail || weatherBits,
        tone:"weather", tag: weatherItem.tag,
      }) : "";
      const location = channelById("env-location");
      return `<div class="perception-hero-grid is-two">${timeHero}${weatherHero}</div>
        <div class="perception-mini-grid is-three">${location ? itemMini(location) : ""}${alertMini}${datesMini}</div>`;
    };
    const modelBodyMarkup = () => {
      const rows = boardRows("models");
      const conversation = rows.find(item => item.id === "model-conversation");
      const rest = rows.filter(item => item !== conversation);
      const modelHero = conversation ? itemHero(conversation, {
        title:"对话模型", tone:"model", value:conversation.value,
        detail:conversation.detail, tag:conversation.tag,
      }) : `<div class="perception-board-empty">模型路由暂不可读</div>`;
      return `<div class="perception-hero-grid is-one">${modelHero}</div>
        <div class="perception-mini-grid is-three">${rest.map(item => itemMini(item)).join("")}</div>`;
    };
    const realityBodyMarkup = () => {
      const location = channelById("device-location");
      const health = channelById("device-health");
      const minis = ["device-screen", "device-phone", "device-camera", "device-mihome"]
        .map(id => channelById(id)).filter(Boolean).map(item => itemMini(item)).join("");
      return `<div class="perception-hero-grid is-two">
        ${location ? itemHero(location, {title:"当前定位", tone:"location"}) : ""}
        ${health ? heroMarkup({state:healthState, iconName:"heart-pulse", title:"运动健康", value:healthTitleValue,
          detail:healthTitleDetail, tag:healthTag, attrs:channelAttrs(health), tone:"health"}) : ""}
      </div><div class="perception-mini-grid is-two">${minis}</div>`;
    };
    const externalBodyMarkup = () => {
      const balance = channelById("ext-balance");
      const external = channelById("ext-external");
      let balanceValue = balance?.value || "", balanceDetail = balance?.detail || "", balanceTag = balance?.tag || "";
      if (balance && balanceObservation) {
        balanceValue = `${formatBalanceAmount(balanceObservation.amount)} ${balanceObservation.currency_label || "额度"}`;
        balanceDetail = `${balanceObservation.provider_label || "NewAPI"} · ${balanceObservationIsDraft ? "草稿测试" : "最近查询"}${balanceObservation.observed_at ? ` · ${ageLabel(balanceObservation.observed_at)}` : ""}`;
        balanceTag = balanceObservationIsDraft ? "草稿测试" : "最近查询";
      } else if (balance) {
        const error = balanceTestError || balanceError || balanceStatusError;
        if (error) { balanceDetail = error; balanceTag = "读取失败"; }
      }
      const minis = ["device-recalls", "ext-injection"]
        .map(id => channelById(id)).filter(Boolean).map(item => itemMini(item)).join("");
      return `<div class="perception-hero-grid is-two">
        ${balance ? heroMarkup({state:balance.state, iconName:"wallet-cards", title:"余额感知", value:balanceValue,
          detail:balanceDetail, tag:balanceTag, attrs:channelAttrs(balance), tone:"balance"}) : ""}
        ${external ? itemHero(external, {title:"外部资讯", tone:"news"}) : ""}
      </div><div class="perception-mini-grid is-two">${minis}</div>`;
    };
    const boardBodyMarkup = spec => {
      if (spec.id === "environment") return environmentBodyMarkup();
      if (spec.id === "models") return modelBodyMarkup();
      if (spec.id === "devices") return realityBodyMarkup();
      return externalBodyMarkup();
    };

    const previewField = (label, value) => `<div class="perception-preview-field"><span>${esc(label)}</span><strong>${esc(value || "—")}</strong></div>`;
    const previewFields = entries => `<div class="perception-preview-fields">${entries.map(([label, value]) => previewField(label, value)).join("")}</div>`;
    const previewCard = ({title, tag="", state="off", body="", note=""}) => `<section class="perception-item-preview">
      <header class="perception-preview-head"><div><small>当前内容</small><h3>${esc(title)}</h3></div>${boardBadgeMarkup(state, tag)}</header>
      <div class="perception-preview-body">${body}</div>
      <p class="perception-preview-note">${esc(note || "这里展示当前已读取或已保存的内容；是否进入下一轮对话由实际上下文决定。")}</p>
    </section>`;
    const healthSectionIcon = title => /睡眠|HRV/i.test(title) ? "moon"
      : /静息心率|心率/.test(title) ? "heart-pulse"
        : /压力/.test(title) ? "gauge"
          : /运动|纪录/.test(title) ? "activity" : "heart-pulse";
    const healthSummaryMarkup = text => {
      const source = String(text || "").slice(0, 5000);
      const sections = [];
      const pattern = /【([^】]+)】([\s\S]*?)(?=\n*【|$)/g;
      let match;
      while ((match = pattern.exec(source))) {
        const title = String(match[1] || "").trim();
        const content = String(match[2] || "").trim();
        if (!title || !content) continue;
        const rows = content.split(/\n+/).map(line => line.trim()).filter(Boolean).map(line => {
          const measured = /^(\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)\s+(.*)$/.exec(line);
          return measured ? {badge:measured[1], text:measured[2]} : {badge:"", text:line};
        });
        sections.push({title, rows});
      }
      if (!sections.length) return `<pre class="perception-preview-text">${esc(source)}</pre>`;
      const totalRows = sections.reduce((sum, section) => sum + section.rows.length, 0);
      return `<div class="health-preview-overview">
        <div><b>${sections.length}</b><span>健康分区</span></div>
        <div><b>${totalRows}</b><span>近期记录</span></div>
        <div><b>${esc(huaweiLatest?.observed_at ? ageLabel(huaweiLatest.observed_at) : "—")}</b><span>最近同步</span></div>
      </div><div class="health-preview-sections">${sections.map(section => `<section class="health-preview-section">
        <header><span class="health-preview-icon">${icon(healthSectionIcon(section.title))}</span><div><h4>${esc(section.title)}</h4><small>${section.rows.length} 条记录</small></div></header>
        <ul>${section.rows.map(row => `<li>${row.badge ? `<time>${esc(row.badge)}</time>` : ""}<span>${esc(row.text)}</span></li>`).join("")}</ul>
      </section>`).join("")}</div>`;
    };
    const buildItemPreviews = () => {
      itemPreviewMarkup = new Map();
      for (const item of percepts) {
        itemPreviewMarkup.set(item.id, previewCard({title:item.title, state:item.state, tag:item.tag,
          body:previewFields([["当前读数", item.value], ["来源与时效", item.detail]])}));
      }
      itemPreviewMarkup.set("env-time", previewCard({title:"当前时间", state:"ready", tag:"本地",
        body:previewFields([["本地时间", new Date().toLocaleTimeString("zh-CN", {hour12:false, timeZone:clockZone()})],
          ["日期与历法", serverDateLabel() || "日期与历法待读取"], ["时区", clockZone()]])}));
      itemPreviewMarkup.set("env-location", previewCard({title:"所在地", state:environment.locationName ? "ready" : ready ? "off" : "pending",
        tag:environment.locationName ? "已配置" : "待设置",
        body:previewFields([["地点", environment.locationName || "尚未设置"], ["用途", "用于天气、日程与生活规划；不显示经纬度"]])}));
      itemPreviewMarkup.set("env-weather", previewCard({title:"天气实况", state:weather.state === "available" ? "ready" : "off",
        tag:weather.state === "available" ? "实况" : "未就绪",
        body:previewFields([["地点", weather.location || locationName || "—"],
          ["天气", weather.state === "available" ? `${weather.condition || "已读取"}${weather.temperature_c == null ? "" : ` · ${weather.temperature_c}°C`}` : weatherValue],
          ["湿度与风", [weather.humidity_pct == null ? "" : `湿度 ${weather.humidity_pct}%`, weather.wind_kph == null ? "" : `风速 ${weather.wind_kph} km/h`].filter(Boolean).join(" · ") || "—"],
          ["来源与时效", weatherDetail]])
          + (alertList.length ? `<ul class="perception-preview-material-list">${alertList.map(alert => `<li><strong>${esc(alert.title || alert.event_name || "天气预警")}</strong><span>${esc(alert.severity || "级别待核对")}${alert.ends_at ? ` · 至 ${esc(new Date(alert.ends_at).toLocaleString("zh-CN"))}` : ""}</span></li>`).join("")}</ul>` : `<p class="perception-preview-empty">当前没有官方天气预警。</p>`)}));
      itemPreviewMarkup.set("env-alert", previewCard({title:"天气预警", state:alertList.length ? "pending" : "ready",
        tag:alertList.length ? `${alertList.length} 条` : "无预警",
        body:alertList.length ? `<ul class="perception-preview-material-list">${alertList.map(alert => `<li><strong>${esc(alert.title || alert.event_name || "天气预警")}</strong><span>${esc(alert.severity || "级别待核对")}${alert.ends_at ? ` · 至 ${esc(new Date(alert.ends_at).toLocaleString("zh-CN"))}` : ""}</span></li>`).join("")}</ul>` : `<p class="perception-preview-empty">当前无预警。</p>`}));
      itemPreviewMarkup.set("env-dates", previewCard({title:"重要日期", state:events.length ? "ready" : "off",
        tag:events.length ? `${events.length} 项` : "暂无",
        body:events.length ? `<ul class="perception-preview-material-list">${events.map(event => `<li><strong>${esc(event.label)}</strong><span>${event.days === 0 ? "今天" : event.days === 1 ? "明天" : `${event.days} 天后`}</span></li>`).join("")}</ul>` : `<p class="perception-preview-empty">近期没有生日或纪念日。</p>`}));
      if (calendarView) itemPreviewMarkup.set("env-calendar", previewCard({title:"日期与历法", state:"ready", tag:"服务端日历",
        body:previewFields([["今天", calendarView.labels], ["农历/节气/黄历", calendarView.detail],
          ["时区", clockZone()]])}));
      const modelIds = ["model-conversation", "model-tts", "model-image", "model-vision"];
      for (const id of modelIds) {
        const item = channelById(id);
        if (item) itemPreviewMarkup.set(id, previewCard({title:item.title, state:item.state, tag:item.tag,
          body:previewFields([["当前路由", item.value], ["Provider 与状态", item.detail]])}));
      }
      const location = channelById("device-location");
      if (location) itemPreviewMarkup.set("device-location", previewCard({title:"当前定位", state:location.state, tag:location.tag,
        body:previewFields([["位置", mobile.location?.available ? mobile.location.label || "附近位置已读取" : mobileValue],
          ["读取时间", mobile.location?.available ? ageLabel(mobile.location.captured_at) : "暂无已授权快照"],
          ["使用边界", "只回显对话已经读取的位置，不主动采集；不显示坐标"]])}));
      const screen = channelById("device-screen");
      if (screen) itemPreviewMarkup.set("device-screen", previewCard({title:"识屏", state:screen.state, tag:screen.tag,
        body:previewFields([["最近解读", screenResult.state === "available" ? screenResult.interpretation || "已有屏幕解读" : screenValue],
          ["屏幕来源", screenResult.state === "available" ? screenResult.surface || "屏幕画面" : "—"],
          ["采集与解读", screenResult.state === "available" ? `采集 ${ageLabel(screenResult.captured_at)} · 解读 ${ageLabel(screenResult.created_at)}` : "暂无已保存解读"]])}));
      const phone = channelById("device-phone");
      if (phone) itemPreviewMarkup.set("device-phone", previewCard({title:"手机状态", state:phone.state, tag:phone.tag,
        body:previewFields([["当前状态", phoneValue], ["最近上报", phoneAvailable ? ageLabel(device.last_report_at || activity.last_report_at || realityResult.captured_at) : "暂无已授权快照"], ["使用边界", "刷新页面不会触发手机采集"]])}));
      const health = channelById("device-health");
      if (health) itemPreviewMarkup.set("device-health", previewCard({title:"运动健康", state:healthState, tag:healthTag,
        body:(huaweiLatest?.summary ? healthSummaryMarkup(huaweiLatest.summary) : previewFields([["当前摘要", healthTitleValue], ["来源与时效", healthTitleDetail]])),
        note:"健康摘要只作为交流背景，不做医疗判断，也不逐项播报指标。"}));
      const camera = channelById("device-camera");
      if (camera) itemPreviewMarkup.set("device-camera", previewCard({title:"摄像头", state:camera.state, tag:camera.tag,
        body:previewFields([["最近观察", cameraValue], ["用途", cameraResult.state === "available" ? cameraResult.purpose || "按本轮明确请求" : "只在明确请求时读取单帧"],
          ["时间", cameraResult.state === "available" ? ageLabel(cameraResult.captured_at) : "暂无已保存结果"]])}));
      const mihome = channelById("device-mihome");
      if (mihome) itemPreviewMarkup.set("device-mihome", previewCard({title:"米家设备", state:mihome.state, tag:mihome.tag,
        body:configuredDevices.length ? `<ul class="perception-preview-material-list">${configuredDevices.map(item => `<li><strong>${esc(item.name || item.aliases?.[0] || "未命名设备")}</strong><span>${esc((item.aliases || []).join("、") || "未设置别名")} · ${item.online === true ? "在线" : item.online === false ? "离线" : "状态未知"}</span></li>`).join("")}</ul>` : `<p class="perception-preview-empty">只读设备状态已开启，暂无已映射设备。</p>`}));
      const external = channelById("ext-external");
      if (external) {
        const poolItems = Array.isArray(materialPool?.items) ? materialPool.items.filter(item => item && typeof item.title === "string") : [];
        const poolHtml = materialPoolState === "loading" ? `<p class="perception-preview-empty">正在读取资讯池…</p>`
          : materialPoolState === "error" ? `<p class="perception-preview-empty">资讯池读取失败：${esc(materialPoolError)}</p>`
            : poolItems.length ? `<ul class="perception-preview-material-list is-scroll">${poolItems.map(item => `<li><strong>${esc(item.title)}</strong>${item.detail ? `<span>${esc(String(item.detail).slice(0, 280))}</span>` : ""}<small>${esc(item.note || item.source || "资讯")}${item.captured_at ? ` · ${esc(ageLabel(item.captured_at))}` : ""}</small></li>`).join("")}</ul>`
              : `<p class="perception-preview-empty">资讯池暂无可展示条目。</p>`;
        itemPreviewMarkup.set("ext-external", previewCard({title:"外部资讯", state:external.state, tag:external.tag,
          body:previewFields([["来源", news.label || "尚未配置"], ["自选兴趣", news.explorationInterests || "未指定"],
            ["检查节奏", [Number.isInteger(news.explorationIntervalHours) ? `最短 ${news.explorationIntervalHours} 小时` : "", Number.isInteger(news.explorationResultCount) ? `每次最多 ${news.explorationResultCount} 条` : ""].filter(Boolean).join(" · ") || "—"],
            ["最近检查", [news.lastCheckedAt || "—", Number.isInteger(news.itemCount) ? `${news.itemCount} 条` : ""].filter(Boolean).join(" · ")]])
            + poolHtml, note:"这里展示当前资讯池中的自选内容；只有实际被选中的条目才会进入对话，不会整批注入。"}));
      }
      const balance = channelById("ext-balance");
      if (balance) itemPreviewMarkup.set("ext-balance", previewCard({title:"余额感知", state:balance.state, tag:balance.tag,
        body:previewFields([["当前额度", balanceObservation ? `${formatBalanceAmount(balanceObservation.amount)} ${balanceObservation.currency_label || "额度"}` : "尚无余额读数"],
          ["服务商", balanceObservation?.provider_label || "NewAPI"], ["最近查询", balanceObservation?.observed_at ? ageLabel(balanceObservation.observed_at) : "尚无查询结果"],
          ["用途", "仅在余额达到阈值时作为主动联系素材，不进入日常对话"]])}));
      const recallsItem = channelById("device-recalls");
      if (recallsItem) itemPreviewMarkup.set("device-recalls", previewCard({title:"撤回记录", state:recallsItem.state, tag:recallsItem.tag,
        body:recalls.length ? `<ul class="perception-preview-material-list is-scroll">${recalls.map(item => `<li><strong>${esc(item.local_time || ageLabel(item.withdrawn_at))} · ${esc(item.author || "当前会话")}</strong><span>${esc(String(item.text || "（无可展示文本）").slice(0, 1000))}</span></li>`).join("")}</ul>` : `<p class="perception-preview-empty">${esc(recallState === "error" ? `读取失败（${recallError || "原因未知"}）` : recallStatus?.enabled === false ? "撤回转录未启用。" : "当前保留时限内没有可读的撤回记录。")}</p>`}));
      if (injectionLabels.length) itemPreviewMarkup.set("ext-injection", previewCard({title:"其他注入", state:"ready", tag:"本轮材料",
        body:`<ul class="perception-preview-material-list">${injectionLabels.map(label => `<li><strong>${esc(label)}</strong><span>本轮上下文材料</span></li>`).join("")}</ul>`}));
    };
    buildItemPreviews();
    /* 页头保留真实的整体读数：通道数与"实况在线"都由当前 percepts 现算，
       不写死总数——模型视觉、天气预警、米家这类通道本来就时有时无。 */
    /* 同步状态必须说出来：日期与天气都绑在服务端快照上，读失败时页面会停在旧值，
       不给提示就等于拿过期读数冒充当前实况。 */
    const readStates = [runtimeState, modelState, voiceState, imageState, semanticState, recallState, huaweiHealthState];
    const syncState = readStates.includes("error") || configurationFailed ? {label:"读取未完成", tone:"warn", icon:"triangle-alert"}
      : readStates.includes("loading") || !ready ? {label:"正在读取实况", tone:"pending", icon:"loader-circle"}
        : ready ? {label:"真实状态已同步", tone:"ready", icon:"check"} : {label:"角色上下文待读取", tone:"pending", icon:"clock"};
    const pendingCount = percepts.filter(item => item.state === "pending").length;
    const offCount = percepts.filter(item => item.state !== "ready" && item.state !== "pending").length;
    const barMarkup = `<header class="perception-board-bar">
      <div>
        <span class="perception-kicker">PERCEPTION · 感知态势总览</span>
        <h1>感知仪表盘</h1>
        <p class="perception-board-sub">按四块能力卡片汇总当前角色的时空环境、模型路由、设备授权与外部来源；每张小卡片都是独立按钮，点击即可在下方查看实时内容与配置。</p>
      </div>
      <div class="perception-board-count">
        <span class="perception-board-sync is-${syncState.tone}">${icon(syncState.icon)}${esc(syncState.label)}</span>
        <b>${percepts.length} 个感知通道 · ${activeCount} 项实况在线</b>
        <span class="perception-board-summary-pills"><small>${pendingCount} 待处理</small><small>${offCount} 未启用</small></span>
        <span class="perception-v9-sr">${esc(runtimeState === "error" ? (snapshot?.error || "读取超时") : "")}</span>
        <button type="button" id="perception-status-refresh" class="button small perception-refresh-btn" data-context-refresh>${icon("refresh-cw")}<span>重新读取</span></button>
      </div>
    </header>`;
    const boardMarkup = `<section class="perception-board" aria-label="感知态势板">${boardSpecs.map((spec, index) => {
      const rows = boardRows(spec.id);
      const live = rows.filter(item => item.state === "ready").length;
      const count = spec.id === "environment" ? Math.max(rows.length, 4) : Math.max(rows.length, 1);
      return `<article class="perception-board-card is-${spec.tone}" data-perception-board="${esc(spec.id)}">
        <header id="perception-board-toggle-${spec.id}" class="perception-board-head"
          aria-label="${esc(spec.title)}：${count} 个条目，${live} 项实况在线">
          <span class="perception-board-icon">${icon(spec.icon)}</span>
          <span class="perception-board-title">
            <strong><span class="perception-board-index">0${index + 1}</span>${esc(spec.title)}</strong>
            <small>${live} 项实况在线 · 共 ${count} 项可配置</small>
          </span>
          <button type="button" class="perception-board-open" ${destinationAttrs(spec.tab, spec.modelTab)} aria-label="打开${esc(spec.title)}分区配置">
            <span>分区设置</span>${icon("chevron-right")}
          </button>
        </header>
        <div class="perception-board-body">${boardBodyMarkup(spec)}</div>
      </article>`;
    }).join("")}</section>
    <p class="perception-board-hint">点击任意感知条目卡片，可在下方单独查看该项的当前内容与设置；点击卡片右上角「分区设置」可切换整组配置。</p>`;
    root.innerHTML = `${barMarkup}${boardMarkup}`;
    syncSelection();
    if (activePreviewItemId) preview(activePreviewItemId);
    if (balanceSource) renderBalanceSource();

    // 保留工作区处理逻辑
    const page = root.parentElement;
    const mihomePanel = page?.querySelector("#perception-detail-mihome");
    if (mihomePanel) mihomePanel.hidden = !mihomeEnabled;
    const mihomeWorkspace = page?.querySelector("#mihomeStatusWorkspace");
    if (mihomeWorkspace) mihomeWorkspace.innerHTML = mihomeEnabled
      ? `<p class="perception-mihome-note">${mihomeStatus.snapshot_at ? `设备目录最近同步 ${esc(mihomeStatus.snapshot_at)}。` : "当前展示米家扩展缓存的设备目录状态。"} 状态未知时不会调用设备接口补采。</p>${configuredDevices.length
        ? `<ul>${configuredDevices.map(item => `<li class="perception-mihome-device"><span><strong>${esc(item.name || item.aliases?.[0] || "未命名设备")}</strong><small>${esc((item.aliases || []).join("、") || "未设置别名")}</small></span><b class="state-${item.online === true ? "online" : item.online === false ? "offline" : "unknown"}">${item.online === true ? "在线" : item.online === false ? "离线" : "未核实"}</b></li>`).join("")}</ul>`
        : `<p class="memory-notice" role="status">只读状态已启用，暂时没有已映射设备。</p>`}` : "";
    const recallWorkspace = page?.querySelector("#perceptionRecallWorkspace");
    if (recallWorkspace) recallWorkspace.innerHTML = recallState === "loading"
      ? `<p class="memory-notice" role="status">正在读取当前窗口的撤回记录…</p>`
      : recallState === "error" ? `<p class="memory-notice" role="status">这个角色窗口的撤回记录暂不可读（${esc(recallError || "原因未知")}）；检查角色窗口授权和撤回转录开关。</p>`
        : recallStatus?.enabled === false ? `<p class="memory-notice" role="status">撤回转录未启用。配置开启后，只读记录会在设定时限内显示。</p>`
          : recalls.length ? `<ul>${recalls.map(item => `<li class="perception-recall-item"><span><strong>${esc(item.local_time || ageLabel(item.withdrawn_at))}</strong><small>${item.author ? `发送账号 ${esc(item.author)}` : "当前会话记录"}</small></span><p>${esc(item.text || "（无可展示文本）")}</p></li>`).join("")}</ul>`
            : `<p class="memory-notice" role="status">当前保留时限内没有可读的撤回记录。</p>`;
    const injectionWorkspace = page?.querySelector("#perceptionInjectionWorkspace");
    if (injectionWorkspace) injectionWorkspace.innerHTML = injectionLabels.length
      ? `<ul>${injections.map(item => {
        const label = typeof item === "string" ? item : item.label || item.name;
        const detail = typeof item === "string" ? "本轮上下文材料" : item.detail || item.source || "本轮上下文材料";
        return `<li><strong>${esc(label)}</strong><span>${esc(detail)}</span></li>`;
      }).join("")}</ul>` : "";
    if (focusedId) root.querySelector(`#${focusedId}`)?.focus?.({preventScroll:true});
    window.lucide?.createIcons({attrs:{"aria-hidden":"true"}});
    lastDay = ""; updateClock();
  }
  async function refreshMaterialPool(ticket = generation) {
    if (!validRoot(ticket)) return;
    materialPoolState = "loading"; materialPoolError = ""; render();
    try {
      const base = product.role?.()?.perception_native_api;
      if (typeof base !== "string" || !base) throw new Error("material_pool_route_missing");
      const route = product.relative(base).replace(/\/$/, "") + "/materials";
      const value = product.unwrap(await product.request("GET", route, null, {timeoutMs:15000}));
      if (!validRoot(ticket)) return;
      materialPool = value && typeof value === "object" ? value : null;
      materialPoolState = "ready";
      rememberOverview("materialPool", materialPool, "materialPoolState", "ready");
    } catch (error) {
      if (!validRoot(ticket)) return;
      materialPool = null; materialPoolState = "error";
      materialPoolError = error?.message && error.message !== error.code ? String(error.message).slice(0,180) : "资讯池暂不可读";
      forgetOverview("materialPool", "materialPoolState");
    }
    render();
  }
  async function refreshHuaweiHealth(ticket = generation) {
    if (!validRoot(ticket)) return;
    huaweiHealthState = "loading"; huaweiHealthError = ""; render();
    try {
      const value = product.unwrap(await product.request("GET", "huawei-health/status", null, {timeoutMs:15000}));
      if (!validRoot(ticket)) return;
      huaweiHealth = value && typeof value === "object" ? value : null;
      huaweiHealthState = "ready";
      rememberOverview("huaweiHealth", huaweiHealth, "huaweiHealthState", "ready");
    } catch (error) {
      if (!validRoot(ticket)) return;
      huaweiHealth = null; huaweiHealthState = "error";
      huaweiHealthError = error?.message && error.message !== error.code ? String(error.message).slice(0, 180) : "运动健康状态暂不可读";
      forgetOverview("huaweiHealth", "huaweiHealthState");
    }
    render();
  }
  async function refreshModel(ticket = generation) {
    if (!validRoot(ticket)) return;
    modelState = "loading"; render();
    try {
      const endpoint = product.role()?.model_api;
      if (typeof endpoint !== "string" || !endpoint) throw new Error("missing model route");
      const response = await product.request("GET", product.relative(endpoint));
      const value = product.unwrap(response);
      if (!validRoot(ticket)) return;
      if (!Array.isArray(value.tasks) || !Array.isArray(value.providers)) throw new Error("invalid model route");
      modelStatus = value; modelState = "ready"; rememberOverview("modelStatus", value, "modelState", "ready");
    } catch (_) {
      if (!validRoot(ticket)) return;
      modelStatus = null; modelState = "error";
      forgetOverview("modelStatus", "modelState");
    }
    render();
  }
  async function refreshVoice(ticket = generation) {
    if (!validRoot(ticket)) return;
    voiceState = "loading"; render();
    try {
      const endpoint = product.role()?.model_api;
      if (typeof endpoint !== "string" || !endpoint) throw new Error("missing voice route");
      const value = product.unwrap(await product.request("GET", product.relative(endpoint.replace(/\/$/, "") + "/voice")));
      if (!validRoot(ticket)) return;
      if (!Array.isArray(value.languages) || !Array.isArray(value.providers)) throw new Error("invalid voice status");
      voiceStatus = value; voiceState = "ready"; rememberOverview("voiceStatus", value, "voiceState", "ready");
    } catch (_) {
      if (!validRoot(ticket)) return;
      voiceStatus = null; voiceState = "error";
      forgetOverview("voiceStatus", "voiceState");
    }
    render();
  }
  async function refreshImage(ticket = generation) {
    if (!validRoot(ticket)) return;
    imageState = "loading"; render();
    try {
      const endpoint = product.role()?.model_api;
      if (typeof endpoint !== "string" || !endpoint) throw new Error("missing image route");
      const value = product.unwrap(await product.request("GET", product.relative(endpoint.replace(/\/$/, "") + "/image")));
      if (!validRoot(ticket)) return;
      if (!Array.isArray(value.actions)) throw new Error("invalid image status");
      imageStatus = value; imageState = "ready"; rememberOverview("imageStatus", value, "imageState", "ready");
    } catch (_) {
      if (!validRoot(ticket)) return;
      imageStatus = null; imageState = "error";
      forgetOverview("imageStatus", "imageState");
    }
    render();
  }
  async function refreshSemanticStatus(ticket = generation) {
    if (!validRoot(ticket)) return;
    semanticState = "loading"; render();
    try {
      const bridge = product.bridgeNow?.() || await product.bridgeFor();
      const value = product.unwrap(await bridge.apiGet("semantic", {...product.selection(), kind:"reaction"}));
      if (!validRoot(ticket)) return;
      if (!value || typeof value !== "object") throw new Error("invalid semantic status");
      semanticStatus = value; semanticState = "ready"; rememberOverview("semanticStatus", value, "semanticState", "ready");
    } catch (_) {
      if (!validRoot(ticket)) return;
      semanticStatus = null; semanticState = "error";
      forgetOverview("semanticStatus", "semanticState");
    }
    render();
  }
  async function refreshRecalls(ticket = generation) {
    if (!validRoot(ticket)) return;
    recallState = "loading"; render();
    try {
      const replyApi = product.role?.()?.reply_api;
      if (typeof replyApi !== "string" || !replyApi) throw new Error("recall_scope_denied");
      const route = product.relative(replyApi).replace(/\/$/, "") + "/recalls";
      const response = product.request
        ? await product.request("GET", route, {limit:5})
        : await (product.bridgeNow?.() || await product.bridgeFor()).apiGet(route, {limit:5});
      const value = product.unwrap(response);
      if (!validRoot(ticket)) return;
      if (!Array.isArray(value.entries)) throw new Error("invalid recall status");
      recallStatus = value; recallState = "ready"; recallError = "";
      rememberOverview("recallStatus", value, "recallState", "ready");
    } catch (error) {
      if (!validRoot(ticket)) return;
      /* 「撤回原文转写」关闭是正常状态，不是读取失败：后端会以
         recall_transcribe_disabled 拒绝读取。把它画成故障会让人跑去查授权。 */
      const rawMessage = String(error?.message || "").trim();
      const code = /^[a-z][a-z0-9_]{2,80}$/.test(rawMessage) ? rawMessage
        : typeof error?.code === "string" && error.code ? error.code : "recall_request_failed";
      if (code === "recall_transcribe_disabled") {
        recallStatus = {enabled: false, entries: []}; recallState = "ready";
        rememberOverview("recallStatus", recallStatus, "recallState", "ready");
      } else {
        recallStatus = null; recallState = "error";
        recallError = code;
        forgetOverview("recallStatus", "recallState");
      }
    }
    render();
  }
  async function refreshMihome(ticket = generation) {
    if (!validRoot(ticket)) return;
    mihomeState = "loading"; render();
    try {
      const bridge = product.bridgeNow?.() || await product.bridgeFor();
      const value = product.unwrap(await bridge.apiGet("extensions", {}));
      if (!validRoot(ticket)) return;
      const reality = value?.extensions?.reality;
      const settings = reality?.settings;
      const mihome = reality?.management?.mihome;
      if (!settings || !mihome || mihome.available === false || !Array.isArray(mihome.devices)
          || typeof mihome.tool_settings?.enable_readonly_tool !== "boolean")
        throw new Error("invalid native MiHome status");
      if (settings.enabled === false || settings.mihome?.enabled === false
          || !mihome.tool_settings.enable_readonly_tool) {
        mihomeStatus = null; mihomeState = "disabled"; forgetOverview("mihomeStatus", "mihomeState");
        render();
        return;
      }
      mihomeStatus = {
        enabled:true,
        snapshot_at:typeof mihome.snapshot_at === "string" ? mihome.snapshot_at : "",
        devices:mihome.devices.filter(item => item?.configured === true || item?.aliases?.length)
          .map(item => ({
            configured:true,
            name:typeof item.name === "string" ? item.name.slice(0, 160) : "",
            aliases:Array.isArray(item.aliases)
              ? item.aliases.filter(alias => typeof alias === "string").slice(0, 20).map(alias => alias.slice(0, 120)) : [],
            online:item.online === true ? true : item.online === false ? false : null,
          })),
      };
      mihomeState = "ready";
      rememberOverview("mihomeStatus", mihomeStatus, "mihomeState", "ready");
    } catch (_) {
      if (!validRoot(ticket)) return;
      mihomeStatus = null; mihomeState = "unavailable";
      forgetOverview("mihomeStatus", "mihomeState");
    }
    render();
  }
  async function refreshRuntime(ticket = generation) {
    if (!validRoot(ticket)) return;
    runtimeState = "loading"; render();
    try {
      const endpoint = product.role()?.perception_native_api;
      if (typeof endpoint !== "string" || !endpoint) throw new Error("missing perception runtime route");
      const route = endpoint.replace(/\/$/, "") + "/runtime";
      const value = product.unwrap(await product.request("GET", product.relative(route)));
      if (!validRoot(ticket)) return;
      if (!value || typeof value !== "object" || !value.weather || !value.screen || !value.reality || !value.calendar)
        throw new Error("invalid perception runtime snapshot");
      runtimeSnapshot = value; runtimeState = "ready"; rememberOverview("runtimeSnapshot", value, "runtimeState", "ready");
    } catch (_) {
      if (!validRoot(ticket)) return;
      runtimeSnapshot = null; runtimeState = "error";
      forgetOverview("runtimeSnapshot", "runtimeState");
    }
    render();
  }
  async function requestCurrentBalance(ticket) {
    const endpoint = product.role()?.model_api;
    if (typeof endpoint !== "string" || !endpoint) throw new Error("当前角色没有可用的模型余额接口。");
    const route = product.relative(endpoint.replace(/\/$/, "") + "/balance");
    const value = product.unwrap(await product.request("GET", route));
    if (!validRoot(ticket)) return null;
    const status = safeBalanceStatus(value);
    if (!status) throw new Error("余额接口返回的数据无效。");
    return status;
  }
  function unavailableBalanceStatus() {
    const configured = balanceConfig?.source_state === "configured" || balanceConfig?.source_state === "invalid";
    return {configured, available:false, provider:balanceConfig?.provider || "newapi",
      provider_label:"NewAPI", currency_label:"额度", amount:null, observed_at:"",
      state:configured ? "unavailable" : "not_configured"};
  }
  async function refreshCurrentBalance(ticket = generation) {
    if (!validRoot(ticket)) return;
    if (balanceStatusLoading) {balanceStatusRefreshQueued = true; return;}
    balanceStatusLoading = true; balanceStatusError = ""; render();
    try {
      const status = await requestCurrentBalance(ticket);
      if (!validRoot(ticket) || !status) return;
      balanceStatus = status;
      balanceStatusError = balanceStatusMessage(status);
    } catch (error) {
      if (!validRoot(ticket)) return;
      balanceStatus = unavailableBalanceStatus();
      balanceStatusError = error.message || "余额读取失败，请检查数据源配置。";
    } finally {
      if (validRoot(ticket)) {
        balanceStatusLoading = false;
        rememberBalanceCache();
        render();
        if (balanceStatusRefreshQueued) {
          balanceStatusRefreshQueued = false;
          void refreshCurrentBalance(ticket);
        }
      }
    }
  }
  async function readBalanceConfig(ticket = generation) {
    if (!validRoot(ticket) || balanceConfigLoading || balanceSaving || balanceTesting) return;
    balanceConfigLoading = true; balanceError = ""; balanceMessage = ""; render();
    try {
      const bridge = product.bridgeNow?.() || (product.bridgeFor ? await product.bridgeFor() : window.AstrBotPluginPage);
      if (!bridge?.apiGet) throw new Error("页面连接尚未就绪，请稍后重试。");
      const value = product.unwrap(await bridge.apiGet("host-options/balance"));
      if (!validRoot(ticket)) return;
      if (!value?.service_balance || !value?.edit_tokens?.balance) throw new Error("余额数据源配置暂不可读。");
      balanceConfig = value.service_balance;
      balanceConfig.edit_token = value.edit_tokens.balance;
      if ((balanceResultConfigToken && balanceResultConfigToken !== balanceConfig.edit_token)
          || (balanceResultConfigKey && balanceResultConfigKey !== balanceConfigKey(balanceConfig))) clearBalanceResult();
      if (!balanceDraft) balanceDraft = emptyBalanceDraft(balanceConfig);
      rememberBalanceCache();
    } catch (error) {
      if (!validRoot(ticket)) return;
      balanceConfig = null; balanceDraft = null;
      clearBalanceResult();
      balanceError = error.message || "余额数据源配置暂不可读。";
    } finally {
      if (validRoot(ticket)) {balanceConfigLoading = false; render();}
    }
  }
  async function saveBalanceConfig(event) {
    const form = event.target?.closest?.("[data-balance-form]");
    if (!form) return;
    event.preventDefault();
    if (!balanceConfig?.edit_token || balanceSaving || balanceTesting) return;
    const pendingFocus = balanceFocus(form);
    const draft = rememberBalanceDraft(form);
    const clear = draft.clear_configuration;
    const validationError = clear ? "" : balanceDraftError(draft);
    if (validationError) { balanceError = validationError; render(); return; }
    balanceSaving = true; balanceError = ""; balanceMessage = "";
    balanceTestError = ""; clearBalanceResult();
    const ticket = generation;
    render();
    try {
      const bridge = product.bridgeNow?.() || (product.bridgeFor ? await product.bridgeFor() : window.AstrBotPluginPage);
      if (!bridge?.apiPost) throw new Error("页面连接尚未就绪，请稍后重试。");
      const changes = clear ? {clear_configuration:true} : balanceDraftChanges(draft);
      const value = product.unwrap(await bridge.apiPost("host-options/balance", {
        edit_token:balanceConfig.edit_token, changes,
      }));
      if (!validRoot(ticket)) return;
      balanceConfig = value.service_balance;
      balanceConfig.edit_token = value.edit_tokens.balance;
      balanceMessage = clear ? "已移除本页数据源；环境变量数据源仍可继续使用。" : "已保存并立即生效。";
      balanceDraft = emptyBalanceDraft(balanceConfig);
      clearBalanceResult(); balanceTestError = "";
      void refreshCurrentBalance(ticket);
    } catch (error) {
      if (!validRoot(ticket)) return;
      balanceError = error.message || "余额数据源保存失败。";
    } finally {
      if (validRoot(ticket)) {
        balanceSaving = false;
        const focusLost = window.document?.activeElement === window.document?.body;
        render();
        if (focusLost && pendingFocus) restoreBalanceFocus(pendingFocus);
      }
    }
  }
  const balanceTestErrors = {
    balance_test_timeout:"余额查询超时，请核对地址、服务状态或代理。",
    balance_test_failed:"连接失败，请检查令牌权限、用户 ID 和地址。",
    host_options_conflict:"设置已在别处更新，请重新读取后重试。",
    dashboard_identity_required:"请登录 AstrBot Dashboard 后再测试数据源。",
  };
  async function testBalanceConfig(form) {
    if (!balanceConfig?.edit_token || balanceSaving || balanceTesting
        || (balanceStatusLoading && balanceDraftIsCurrent())) return;
    const pendingFocus = balanceFocus(form);
    const draft = rememberBalanceDraft(form);
    let changes, usesCurrent = false;
    if (draft.clear_configuration) {
      balanceTestError = "先取消移除数据源，再测试当前连接。"; render(); return;
    }
    if (balanceDraftIsCurrent()) {
      usesCurrent = true;
    } else {
      const validationError = balanceDraftError(draft);
      if (validationError) { balanceTestError = validationError; render(); return; }
      changes = balanceDraftChanges(draft);
    }
    balanceTesting = true; balanceTestError = ""; clearBalanceResult();
    const ticket = generation;
    render();
    try {
      if (usesCurrent) {
        const status = await requestCurrentBalance(ticket);
        if (!validRoot(ticket) || !status) return;
        balanceStatus = status;
        balanceStatusError = balanceStatusMessage(status);
        balanceTestError = balanceStatusError;
        if (status.available) {
          balanceTestError = "";
          balanceMessage = "余额已重新读取。";
        }
      } else {
        const bridge = product.bridgeNow?.() || (product.bridgeFor ? await product.bridgeFor() : window.AstrBotPluginPage);
        if (!bridge?.apiPost) throw new Error("页面连接尚未就绪，请稍后重试。");
        const value = product.unwrap(await bridge.apiPost("host-options/balance/test", {
          edit_token:balanceConfig.edit_token, changes,
        }));
        if (!validRoot(ticket)) return;
        if (value?.tested !== true || value?.available !== true || !["number", "string"].includes(typeof value.amount)
            || String(value.amount).trim() === "" || !Number.isFinite(Number(value.amount)) || Number(value.amount) < 0)
          throw new Error(value?.message || "余额服务未返回可用金额，请检查数据源配置。");
        balanceTestResult = {...value, amount:formatBalanceAmount(value.amount)};
        balanceResultScope = scope;
        balanceResultConfigToken = "";
        balanceResultConfigKey = "";
        balanceResultIsDraft = true;
      }
      rememberBalanceCache();
    } catch (error) {
      if (!validRoot(ticket)) return;
      if (usesCurrent) {
        balanceStatus = unavailableBalanceStatus();
        balanceStatusError = error.message || "余额读取失败，请检查数据源配置。";
      }
      const code = error.code || "";
      balanceTestError = balanceTestErrors[code]
        || (error.message && error.message !== code ? error.message : "连接失败，请检查数据源配置后重试。");
    } finally {
      if (validRoot(ticket)) {
        balanceTesting = false;
        const focusLost = window.document?.activeElement === window.document?.body;
        render();
        if (focusLost && pendingFocus) restoreBalanceFocus(pendingFocus);
      }
    }
  }
  function input(event) {
    const field = event.target?.closest?.("[data-balance-field]");
    if (!field) return;
    const name = field.dataset?.balanceField || field.name;
    if (![...balanceTextFields, ...balanceCheckFields].includes(name)) return;
    if (!balanceDraft) balanceDraft = emptyBalanceDraft(balanceConfig);
    balanceDraft[name] = balanceCheckFields.includes(name) ? field.checked === true : field.value;
    const hadDraftObservation = !!balanceTestResult && balanceResultIsDraft;
    balanceError = ""; balanceTestError = "";
    if (hadDraftObservation) clearBalanceResult();
    else rememberBalanceCache();
    const form = field.closest?.("[data-balance-form]");
    const result = form?.querySelector("[data-balance-test-result]");
    if (result && hadDraftObservation) result.hidden = true;
    const test = form?.querySelector("[data-balance-test]");
    if (test) test.disabled = balanceSaving || balanceTesting || (balanceStatusLoading && balanceDraftIsCurrent()) || balanceDraft.clear_configuration
      || !balanceDraftTestable(balanceDraft);
    if (hadDraftObservation) render({balanceSource:false});
  }
  function click(event) {
    const restore = event.target.closest("[data-balance-restore-default]");
    const load = event.target.closest("[data-balance-load-preset]");
    if (restore || load) {
      event.preventDefault();
      if (balanceSaving || balanceTesting) return;
      const form = (restore || load).closest("[data-balance-form]");
      if (form) rememberBalanceDraft(form);
      const selected = restore ? "generic" : form?.querySelector('[name="advanced_preset"]')?.value || balancePreset;
      if (balanceDraft?.advanced_enabled && balanceScriptExamples[selected]) {
        balancePreset = selected;
        balanceDraft.advanced_script = balanceScriptExamples[selected];
        clearBalanceResult(); render();
      }
      return;
    }
    const testButton = event.target.closest("[data-balance-test]");
    if (testButton) {
      event.preventDefault();
      const form = testButton.closest("[data-balance-form]");
      if (form) void testBalanceConfig(form);
      return;
    }

    const gotoButton = event.target.closest("[data-board-goto]");
    if (gotoButton) {
      /* 配置本体由页面下方的分区面板维护。功能板条目只把对应的
         单项面板送到下方，不展开整个分类。 */
      window.CompanionPerceptionTabs?.activate?.(gotoButton.dataset.boardGoto, {
        focus:true, scroll:true, modelTab:gotoButton.dataset.boardModelTab || "",
        single:gotoButton.dataset.boardSingle === "true",
        itemId:gotoButton.dataset.perceptionItem || "",
        itemTitle:gotoButton.dataset.itemTitle || "",
      });
      return;
    }
    if (event.target.closest("[data-balance-reload]")) {readBalanceConfig(generation); return;}
    if (event.target.closest("[data-balance-refresh]")) {void refreshCurrentBalance(generation); return;}
    if (!event.target.closest("[data-context-refresh]")) return;
    window.CompanionNativePerceptionPage?.refresh?.();
    const ticket = generation;
    refreshModel(ticket); refreshRuntime(ticket); refreshVoice(ticket); refreshImage(ticket);
    refreshSemanticStatus(ticket); refreshRecalls(ticket); refreshMihome(ticket); refreshHuaweiHealth(ticket); refreshMaterialPool(ticket);
    void refreshCurrentBalance(ticket);
    if (!balanceConfig || balanceDraftIsCurrent()) void readBalanceConfig(ticket);
  }
  function change(event) {
    const preset = event.target.closest("[data-balance-preset]");
    if (preset) {balancePreset = preset.value; return;}
    input(event);
    const field = event.target.closest("[data-balance-field]");
    if (field && ["advanced_enabled", "advanced_from_astrbot", "advanced_provider_id", "clear_configuration"].includes(field.dataset?.balanceField || field.name)) {
      const form = field.closest("[data-balance-form]");
      if (form) rememberBalanceDraft(form);
      render();
    }
  }

  function mount(node) {
    unmount();
    const nextScope = activeScopeKey();
    root = node; scope = nextScope; abort = new AbortController();
    overviewRouteKey = routeKey();
    restoreBalanceCache(nextScope);
    balanceWorkspace = root.parentElement?.querySelector("#perceptionBalanceSourceWorkspace") || null;
    const ticket = ++generation;
    const cached = overviewCache.get(`${scope}:${overviewRouteKey}`) || {};
    snapshot = cached.snapshot || null; runtimeSnapshot = cached.runtimeSnapshot || null;
    runtimeState = cached.runtimeState || "loading";
    modelStatus = cached.modelStatus || null; modelState = cached.modelState || "loading";
    voiceStatus = cached.voiceStatus || null; voiceState = cached.voiceState || "loading";
    imageStatus = cached.imageStatus || null; imageState = cached.imageState || "loading";
    semanticStatus = cached.semanticStatus || null; semanticState = cached.semanticState || "loading";
    recallStatus = cached.recallStatus || null; recallState = cached.recallState || "loading";
    mihomeStatus = cached.mihomeStatus || null; mihomeState = cached.mihomeState || "loading";
    huaweiHealth = cached.huaweiHealth || null; huaweiHealthState = cached.huaweiHealthState || "loading";
    materialPool = cached.materialPool || null; materialPoolState = cached.materialPoolState || "loading";
    root.addEventListener("click", click, {signal:abort.signal});
    if (balanceWorkspace) {
      balanceWorkspace.addEventListener("click", click, {signal:abort.signal});
      balanceWorkspace.addEventListener("submit", saveBalanceConfig, {signal:abort.signal});
      balanceWorkspace.addEventListener("input", input, {signal:abort.signal});
      balanceWorkspace.addEventListener("change", change, {signal:abort.signal});
    }
    unsubscribe = window.CompanionNativePerceptionPage?.subscribe?.(next => {
      if (!validRoot(ticket)) return;
      /* native 页刚挂载时会先发 loading。已有成功快照时保留它，避免切页
         回来出现一整块空白；明确失败仍清掉旧快照并展示失败状态。 */
      if (next?.ready === true || !snapshot || (next?.loading === false && next?.error)) snapshot = next;
      if (snapshot?.ready === true) rememberOverview("snapshot", snapshot);
      else if (next?.loading === false && next?.error) forgetOverview("snapshot");
      render();
    }) || null;
    render();
    refreshModel(ticket); refreshRuntime(ticket); refreshVoice(ticket); refreshImage(ticket);
    refreshSemanticStatus(ticket); refreshRecalls(ticket); refreshMihome(ticket); refreshHuaweiHealth(ticket); refreshMaterialPool(ticket); readBalanceConfig(ticket);
    void refreshCurrentBalance(ticket);
    clockTimer = setInterval(updateClock, 1000);
  }
  function unmount() {
    rememberBalanceCache();
    generation++; abort?.abort(); abort = null; unsubscribe?.(); unsubscribe = null;
    if (clockTimer) clearInterval(clockTimer);
    clockTimer = null; root = null; scope = ""; overviewRouteKey = ""; snapshot = null; runtimeSnapshot = null; runtimeState = "loading";
    modelStatus = null; modelState = "loading"; voiceStatus = null; voiceState = "loading";
    imageStatus = null; imageState = "loading"; semanticStatus = null; semanticState = "loading";
    recallStatus = null; recallState = "loading"; mihomeStatus = null; mihomeState = "loading";
    lastDay = ""; selectedDestination = ""; selectedModelTab = "";
    balanceWorkspace = null; balanceMarkup = "";
    balanceConfig = null; balanceDraft = null; balanceExpanded = false; balanceConfigLoading = false;
    balanceStatus = null; balanceStatusLoading = false; balanceStatusRefreshQueued = false; balanceStatusError = "";
    balanceSaving = false; balanceTesting = false;
    balanceTestResult = null; balanceResultScope = ""; balanceResultConfigToken = ""; balanceResultConfigKey = ""; balanceResultIsDraft = false;
    balanceMessage = ""; balanceError = ""; balanceTestError = "";
  }
  return {mount, unmount, select, preview, refresh:refreshRuntime, upcomingDates};
})();
