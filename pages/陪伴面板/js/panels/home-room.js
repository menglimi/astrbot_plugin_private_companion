window.PrivateCompanionHomeRoom = (() => {
  const LIGHT_STORAGE_KEY = "pc_home_room_light_v1";
  const LAYOUT_STORAGE_KEY = "pc_home_room_layout_v1:";
  const ATMOSPHERE_STORAGE_KEY = "pc_home_room_atmosphere_v1";
  const runtime = {
    context: null,
    root: null,
    active: false,
    light: "day",
    lightMode: "auto",
    atmosphere: { motion: true, events: true },
    environment: null,
    environmentError: false,
    environmentRequest: null,
    environmentSequence: 0,
    environmentTimer: null,
    environmentDue: 0,
    clockOffset: 0,
    moments: [],
    outfitRequestId: 0,
    outfitKey: "",
    three: null,
    threeLoading: null,
    station: "overview",
    diaryIndex: 0,
    epoch: 0,
    memoBusy: false,
    memoLoading: false,
    memoError: "",
    memoDraft: "",
    memoRevision: 0,
    outfitData: "",
    inspectorKey: "",
    layoutPersona: null,
    layoutLoading: false,
    layoutLoadError: false,
    layoutSaving: null,
    layoutSaveError: false,
    editing: false,
    editSnapshot: null,
    editorState: null,
  };

  function text(value, limit = 240) {
    return String(value ?? "").replace(/\s+/g, " ").trim().slice(0, limit);
  }

  function escapeHtml(value) {
    return String(value ?? "").slice(0, 30000)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function readStorage(key, fallback) {
    try {
      return window.localStorage.getItem(key) || fallback;
    } catch (_error) {
      return fallback;
    }
  }

  function writeStorage(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_error) {}
  }

  function byId(id) {
    return runtime.context?.document?.getElementById(id) || null;
  }

  function setText(id, value) {
    const target = byId(id);
    if (target) target.textContent = text(value, 600);
  }

  function currentOverview() {
    return runtime.context?.state?.overview || {};
  }

  function personaKey() {
    const context = runtime.context || {};
    return text(context.personaId || context.state?.multiPersona?.current || "primary", 120);
  }

  function roomClock() {
    const date = new Date(Date.now() + runtime.clockOffset);
    let timezone = runtime.environment?.timezone || currentOverview().settings?.environment_perception_timezone_effective || "Asia/Shanghai";
    try { new Intl.DateTimeFormat("zh-CN", { timeZone: timezone }).format(date); }
    catch (_) { timezone = "Asia/Shanghai"; }
    const parts = new Intl.DateTimeFormat("en-GB", { timeZone: timezone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(date);
    const hour = Number(parts.find(part => part.type === "hour")?.value || 0);
    return { date, timezone, hour, label: parts.map(part => part.value).join(""), phase: hour < 6 || hour >= 19 ? "night" : hour < 8 || hour >= 17 ? "dusk" : "day" };
  }

  function applyAtmosphere() {
    const clock = roomClock();
    const effective = runtime.lightMode === "auto" ? clock.phase : runtime.lightMode;
    setLight(effective, { persist: false });
    const weather = runtime.environment?.weather;
    runtime.three?.setAtmosphere?.({
      condition: ["live", "stale"].includes(weather?.status) ? weather.condition : "unknown",
      motion: runtime.atmosphere.motion,
      events: runtime.atmosphere.events,
    });
    const motion = byId("homeRoomMotion"), events = byId("homeRoomEvents"), mode = byId("homeRoomLightMode");
    if (motion) motion.checked = runtime.atmosphere.motion;
    if (events) events.checked = runtime.atmosphere.events;
    if (mode) mode.value = runtime.lightMode;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    setText("homeRoomMotionHint", reduced ? "已跟随系统减少动态效果，小景会静静出现。" : "小景安静出现，不播放声音。");
    updateEncounterButton();
  }

  function updateEncounterButton() {
    const button = runtime.root?.querySelector("[data-room-encounter]");
    if (!button) return;
    const scene = runtime.three?.getAmbientState?.();
    button.disabled = !runtime.active || !scene || runtime.editing || scene.paused || Boolean(scene.event);
    button.title = runtime.editing ? "布置完成后再遇见" : scene?.event ? "先享受眼前的小片刻" : scene?.paused ? "停止巡游后再遇见" : "在小屋里遇见一个小景";
  }

  function renderEnvironment() {
    const clock = roomClock(), weather = runtime.environment?.weather;
    setText("homeRoomClock", clock.label);
    byId("homeRoomClock")?.setAttribute("datetime", clock.date.toISOString());
    setText("homeRoomClockLabel", `${clock.timezone} · ${runtime.lightMode === "auto" ? ({ day: "日间", dusk: "晨昏", night: "夜间" })[clock.phase] : "手动光线"}`);
    const sources = { qweather: "和风天气", amap: "高德天气", openmeteo: "Open-Meteo", private_companion: "OpenWeatherMap", screen_companion: "屏幕伴侣" };
    const available = ["live", "stale"].includes(weather?.status) && weather.summary;
    const age = weather?.updated_at ? Math.max(0, Math.floor((clock.date.getTime() / 1000 - weather.updated_at) / 60)) : null;
    const stale = weather?.status === "stale" || (age !== null && age * 60 >= Number(weather.stale_after_seconds || 5400));
    let summary = "暂时没有天气数据", detail = "可在环境感知设置中配置天气来源和地点";
    if (available) {
      const labels = { clear: "晴", cloudy: "多云 / 阴", rain: "雨", snow: "雪", storm: "雷雨", mist: "雾", unknown: "当前天气" };
      const temperature = Number.isFinite(weather.temperature_c) ? ` · ${weather.temperature_c}°C` : "";
      summary = `${weather.location || "窗外"} · ${labels[weather.condition] || labels.unknown}${temperature}`;
      const freshness = runtime.environmentError ? "更新未成功，显示上次天气" : stale ? "上次天气 · 待更新" : "实况天气";
      const ageText = age === null ? "更新时间未知" : age < 1 ? "刚刚更新" : age < 60 ? `${age} 分钟前更新` : `${Math.floor(age / 60)} 小时前更新`;
      detail = `${freshness} · ${sources[weather.source] || "天气来源"} · ${ageText}${weather.location ? "" : " · 来源未提供地点"}`;
    } else if (weather?.status === "disabled") {
      summary = "天气同步未开启"; detail = "时钟和小景照常，天气可在环境感知设置中开启";
    } else if (runtime.environmentRequest) {
      summary = "正在看看窗外…"; detail = "读取已配置地点的天气";
    } else if (runtime.environmentError) {
      detail = "暂时未能连接天气服务，稍后自动重试";
    }
    setText("homeRoomWeather", summary); setText("homeRoomWeatherDetail", detail);
    const icon = available ? ({ clear: "☀", cloudy: "☁", rain: "☂", storm: "☂", snow: "❄", mist: "≋" })[weather.condition] || "◌" : "◌";
    setText("homeRoomWeatherIcon", icon);
    if (byId("homeRoomWeather")) byId("homeRoomWeather").title = available ? text(weather.summary, 240) : summary;
    const button = runtime.root?.querySelector("[data-room-weather-refresh]");
    if (button) { button.disabled = Boolean(runtime.environmentRequest); button.setAttribute("aria-busy", String(Boolean(runtime.environmentRequest))); }
    applyAtmosphere();
  }

  async function refreshEnvironment() {
    const context = runtime.context;
    if (!runtime.active || context?.document?.hidden || runtime.environmentRequest || !context?.fetchJson) return;
    const controller = new AbortController(), sequence = ++runtime.environmentSequence, epoch = runtime.epoch, persona = personaKey();
    runtime.environmentRequest = controller;
    runtime.environmentDue = Date.now() + 300000;
    renderEnvironment();
    let timeout;
    try {
      const payload = await Promise.race([
        context.fetchJson("/home-room/environment", { signal: controller.signal, dedupe: false }),
        new Promise((_, reject) => { timeout = setTimeout(() => { controller.abort(); reject(new Error("weather timeout")); }, 15000); }),
      ]);
      if (sequence !== runtime.environmentSequence || epoch !== runtime.epoch || persona !== personaKey()) return;
      if (!payload?.weather || !Number.isFinite(payload.server_ts)) throw new Error("weather unavailable");
      runtime.environment = payload;
      runtime.clockOffset = payload.server_ts * 1000 - Date.now();
      runtime.environmentError = false;
    } catch (_) {
      if (sequence === runtime.environmentSequence && epoch === runtime.epoch && persona === personaKey()) runtime.environmentError = true;
    } finally {
      clearTimeout(timeout);
      if (sequence === runtime.environmentSequence) { runtime.environmentRequest = null; renderEnvironment(); }
    }
  }

  function syncEnvironmentActivity() {
    const active = runtime.active && !runtime.context?.document?.hidden;
    if (!active) {
      clearInterval(runtime.environmentTimer); runtime.environmentTimer = null;
      if (runtime.environmentRequest) { runtime.environmentRequest.abort(); runtime.environmentRequest = null; runtime.environmentSequence++; runtime.environmentDue = 0; }
      updateEncounterButton(); return;
    }
    if (!runtime.environmentTimer) runtime.environmentTimer = setInterval(() => {
      renderEnvironment();
      if (Date.now() >= runtime.environmentDue) void refreshEnvironment();
    }, 15000);
    renderEnvironment();
    if (Date.now() >= runtime.environmentDue) void refreshEnvironment();
  }

  function onMoment(moment) {
    if (moment) {
      runtime.moments.unshift({ ...moment, time: roomClock().label });
      runtime.moments = runtime.moments.slice(0, 4);
      setText("homeRoomMoment", moment.caption);
      const history = byId("homeRoomMomentHistory");
      if (history) history.innerHTML = runtime.moments.map(item => `<li><time>${escapeHtml(item.time)}</time> · ${escapeHtml(item.caption)}</li>`).join("");
    }
    updateEncounterButton();
  }

  function formatDate(value) {
    const normalized = text(value, 24);
    const parsed = normalized ? new Date(`${normalized}T12:00:00`) : new Date();
    const date = Number.isNaN(parsed.getTime()) ? new Date() : parsed;
    try {
      return new Intl.DateTimeFormat("zh-CN", {
        month: "long",
        day: "numeric",
        weekday: "short",
      }).format(date);
    } catch (_error) {
      return date.toLocaleDateString("zh-CN");
    }
  }

  function displayValue(value, fallback = "未记录") {
    const normalized = text(value, 80);
    return normalized || fallback;
  }

  function displayEnergy(value) {
    if (typeof value === "number" && Number.isFinite(value)) {
      return `${Math.max(0, Math.min(100, Math.round(value)))}%`;
    }
    const normalized = text(value, 40);
    return normalized || "未记录";
  }

  function planWindow(item) {
    const explicit = text(item?.window, 40);
    if (explicit) return explicit;
    const start = text(item?.time || item?.start_time, 12);
    const end = text(item?.end || item?.end_time, 12);
    if (start && end) return `${start}-${end}`;
    return start || "未定时间";
  }

  function normalizedScheduleStatus(item) {
    const clock = text(item?.clock_status || item?.lifecycle || item?.status, 32).toLowerCase();
    if (["active", "current", "executing", "in_progress", "running"].includes(clock)) return "active";
    if (["completed", "done", "finished", "past"].includes(clock)) return "completed";
    if (["cancelled", "canceled", "skipped"].includes(clock)) return "cancelled";
    if (["tentative", "candidate", "pending_confirmation"].includes(clock)) return "tentative";
    return "planned";
  }

  function statusLabel(status, source) {
    if (status === "active") return source === "calendar" ? "正在日程时段" : "当前时间段";
    if (status === "completed") return "时间已过";
    if (status === "cancelled") return "已取消";
    if (status === "tentative") return "暂定安排";
    return "计划安排";
  }

  function scheduleItems() {
    const overview = currentOverview();
    const segments = Array.isArray(overview.daily_timeline?.segments)
      ? overview.daily_timeline.segments
      : [];
    const timelineItems = segments.map((item, index) => ({
      key: text(item?.key || `timeline-${index}`, 180),
      source: "timeline",
      time: planWindow(item),
      title: text(item?.activity || item?.summary, 160) || "未命名日程",
      status: normalizedScheduleStatus(item),
      evidence: text(item?.evidence_lifecycle, 40),
    }));
    const activeIndex = timelineItems.findIndex((item) => item.status === "active");
    let visibleTimeline = timelineItems;
    if (timelineItems.length > 4) {
      const start = activeIndex >= 0
        ? Math.max(0, Math.min(activeIndex - 1, timelineItems.length - 4))
        : 0;
      visibleTimeline = timelineItems.slice(start, start + 4);
    }

    const calendarEvents = Array.isArray(runtime.context?.state?.calendar?.today?.events)
      ? runtime.context.state.calendar.today.events
      : [];
    const seen = new Set(visibleTimeline.map((item) => item.title.toLocaleLowerCase()));
    const calendarItems = calendarEvents.map((item, index) => ({
      key: text(item?.calendar_id || `calendar-${index}`, 180),
      source: "calendar",
      time: planWindow(item),
      title: text(item?.title, 160) || "未命名日历安排",
      status: normalizedScheduleStatus(item),
      evidence: "",
    })).filter((item) => {
      const key = item.title.toLocaleLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    return [...visibleTimeline, ...calendarItems].slice(0, 4);
  }

  function renderSchedule() {
    const list = byId("homeRoomSchedule");
    if (!list) return;
    const overview = currentOverview();
    const date = text(overview.daily_state?.date || overview.daily_timeline?.date, 24);
    setText("homeRoomDate", formatDate(date));
    const items = scheduleItems();
    if (!items.length) {
      list.innerHTML = `
        <li class="is-empty">
          <span></span>
          <div><b>今天还没有可展示的日程</b><small>这里只读取陪伴插件已有安排</small></div>
        </li>
      `;
      return;
    }
    list.innerHTML = items.map((item) => `
      <li class="is-${escapeHtml(item.status)}">
        <span aria-hidden="true"></span>
        <div>
          <b><time>${escapeHtml(item.time)}</time>${escapeHtml(item.title)}</b>
          <small>${escapeHtml(scheduleLabel(item))}</small>
        </div>
      </li>
    `).join("");
  }

  function renderHeaderAndState() {
    const overview = currentOverview();
    const botName = text(overview.plugin?.bot_name, 80) || "她";
    const dailyState = overview.daily_state && typeof overview.daily_state === "object"
      ? overview.daily_state
      : {};
    const current = overview.life_observation?.current_plan && typeof overview.life_observation.current_plan === "object"
      ? overview.life_observation.current_plan
      : {};
    const activity = text(current.activity, 180);
    const windowText = planWindow(current);
    const currentLine = activity
      ? `${windowText === "未定时间" ? "当前日程" : windowText} · ${activity}`
      : dailyState.location
        ? `当前记录位置：${text(dailyState.location, 80)}`
        : "陪伴状态已经同步，暂无当前日程记录";
    const mood = displayValue(current.mood || dailyState.mood_bias);
    const stateTitle = activity ? "当前日程" : "生活状态";
    const badge = current.clock_status === "active" ? "当前时段" : "已同步";

    setText("homeRoomBotName", botName);
    setText("homeRoomCurrentLine", currentLine);
    setText("homeRoomStateTitle", stateTitle);
    setText("homeRoomStateBadge", badge);
    setText("homeRoomNote", text(dailyState.note, 260)
      || "生活的碎片，会慢慢留在这里。");

    const facts = byId("homeRoomStateFacts");
    if (facts) {
      const rows = [
        ["心情", mood],
        ["精力", displayEnergy(dailyState.energy)],
        ["位置", displayValue(dailyState.location)],
        ["天气", displayValue(dailyState.weather)],
      ];
      facts.innerHTML = rows.map(([label, value]) => `
        <div><dt>${escapeHtml(label)}</dt><dd title="${escapeHtml(value)}">${escapeHtml(value)}</dd></div>
      `).join("");
    }
  }

  function resetOutfitThumb() {
    runtime.outfitData = "";
    const thumb = runtime.root?.querySelector("[data-home-room-outfit-thumb]");
    if (!thumb) return;
    thumb.innerHTML = '<i aria-hidden="true">♧</i>';
  }

  async function hydrateOutfit() {
    const context = runtime.context;
    const outfit = currentOverview().daily_outfit || {};
    const endpoint = text(outfit.image_data_url, 500);
    const nextKey = `${personaKey()}::${endpoint}`;
    if (!outfit.available || !/^\/daily_outfit\/image_data(?:\?|$)/.test(endpoint)) {
      runtime.outfitKey = "";
      runtime.outfitRequestId += 1;
      resetOutfitThumb();
      setText("homeRoomOutfitLabel", outfit.enabled ? "今日暂无穿搭照片" : "每日穿搭未启用");
      if (runtime.station === "wardrobe") renderInspector();
      return;
    }
    if (runtime.outfitKey === nextKey && runtime.root?.querySelector("[data-home-room-outfit-thumb] img")) {
      return;
    }
    const requestId = ++runtime.outfitRequestId;
    runtime.outfitKey = nextKey;
    resetOutfitThumb();
    setText("homeRoomOutfitLabel", "正在读取今日穿搭…");
    if (runtime.station === "wardrobe") renderInspector();
    try {
      const result = await context.fetchJson(endpoint);
      if (requestId !== runtime.outfitRequestId || runtime.outfitKey !== nextKey) return;
      const dataUrl = String(result?.data_url || "").trim();
      if (!dataUrl.startsWith("data:image/")) throw new Error("穿搭图片为空");
      const thumb = runtime.root?.querySelector("[data-home-room-outfit-thumb]");
      if (!thumb) return;
      const image = context.document.createElement("img");
      runtime.outfitData = dataUrl;
      image.src = dataUrl;
      image.alt = "今日穿搭缩略图";
      thumb.replaceChildren(image);
      setText("homeRoomOutfitLabel", outfit.date ? `${outfit.date} · 今日穿搭照片` : "今日穿搭照片");
      if (runtime.station === "wardrobe") renderInspector();
    } catch (_error) {
      if (requestId !== runtime.outfitRequestId) return;
      runtime.outfitKey = "";
      resetOutfitThumb();
      setText("homeRoomOutfitLabel", "穿搭照片暂时无法读取");
      if (runtime.station === "wardrobe") renderInspector();
    }
  }


  function creativeAvailable() {
    const status = currentOverview().companion_plugins?.content || {};
    const tab = runtime.context?.document?.querySelector('.annotations .tab[data-tab="creative"]');
    return Boolean(status.installed && status.enabled && status.available !== false && tab && !tab.hidden);
  }

  function openDestination(destination) {
    const context = runtime.context;
    if (!context?.switchTab) return;
    const targets = {
      calendar: ["memory", "#calendarWorkspace"],
      diary: ["memory", "#diaryCards"],
      dream: ["memory", "#dreamContent"],
      goals: ["memory", "#personalGoalPanel"],
      news: ["dashboard", "#dashboardNewsCard"],
      search: ["dashboard", "#dashboardWebExplorationCard"],
      wardrobe: ["roleplay", '[data-world-section="wardrobe"]', true],
      skills: ["learning", '[data-learning-section="skills"]', true],
      creative: ["creative", "#bookshelfPublicBooks"],
      drawer: ["creative", "#bookshelfUnlockForm"],
    };
    const [tab, selector, click] = targets[destination] || [destination || "memory", ""];
    if (tab === "creative" && !creativeAvailable()) {
      context.showToast("创作扩展当前不可用", "error");
      return;
    }
    if (tab === "image" && !extensionState("image").ready) {
      context.showToast("生图扩展当前不可用，请在相机详情中查看状态。", "error"); return;
    }
    context.switchTab(tab);
    const epoch = runtime.epoch;
    // Wait for the existing view transition, and respect a cancelled navigation.
    const deadline = performance.now() + 2200;
    function reveal() {
      if (epoch !== runtime.epoch) return;
      const panel = context.document.getElementById(`panel-${tab}`);
      if (!panel?.classList.contains("is-active")) {
        if (performance.now() < deadline) requestAnimationFrame(reveal);
        return;
      }
      const target = selector ? panel.querySelector(selector) : null;
      if (click) target?.click();
      target?.scrollIntoView({ behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ? "instant" : "smooth", block: "center" });
    }
    requestAnimationFrame(reveal);
  }

  const STATIONS = {
    calendar: ["01", "生活日历", "日程 · 约定 · 待确认安排"],
    desk: ["02", "手账与便签", "翻一页日记，记下想做的事"],
    shelf: ["03", "故事书架", "正在写的故事，安静收藏"],
    wardrobe: ["04", "今日衣柜", "穿搭照片 · 角色衣柜"],
    bed: ["05", "睡眠与梦境", "昨夜的梦，醒来的余韵"],
    radio: ["06", "见闻电台", "读到的新闻，好奇的发现"],
    garden: ["07", "慢慢成长", "个人目标 · 技能成长"],
    game: ["08", "游戏伴侣", "一局棋，或一段一起玩的时光"],
    image: ["09", "生图工作室", "相机里的灵感与画面"],
    together: ["10", "在一起", "把想念放在电话旁"],
  };
  const EXTENSIONS = {
    game: { intro: "棋桌、街机或游戏挂屏，都可以作为游戏伴侣的入口。", pending: "游戏空间尚未接通，暂时不能在小屋查看对局或开始游戏。" },
    image: { intro: "用相机、拍立得或照片挂架装点小屋，从这里进入生图工作室。", pending: "生图扩展就绪后，可以从这里打开现有的生图页面。" },
    together: { intro: "电话柜与壁挂电话，是联系和共处的同一个入口。", pending: "联系与共处页面尚未接通，暂时不能在小屋拨号、邀请或加入会话。" },
  };
  function extensionState(id) {
    const status = currentOverview().companion_plugins?.[id];
    if (!status) return { ready: false, label: id === "image" ? "状态尚未读取" : "入口待接通" };
    if (!status.installed) return { ready: false, label: "未安装" };
    if (!status.enabled) return { ready: false, label: "未启用" };
    if (status.available === false) return { ready: false, label: "暂不可用" };
    const tab = runtime.context.document.querySelector(`.tab[data-tab="${id}"]`);
    const ready = id === "image" && Boolean(tab && !tab.hidden);
    return { ready, label: ready ? "可打开工作室" : "入口待接通" };
  }
  const list = (value) => Array.isArray(value) ? value.filter(item => item && typeof item === "object") : [];
  function scheduleLabel(item) {
    if (item.evidence === "completed") return "已确认完成";
    if (["active", "executing", "running"].includes(item.evidence)) return "已确认执行中";
    return statusLabel(item.status, item.source);
  }
  function memoPayload() { return runtime.context?.state?.memoNotes || currentOverview().bookshelf?.memo_notes || {}; }
  function action(label, destination) { return `<button type="button" class="room-action" data-home-room-destination="${escapeHtml(destination)}">${escapeHtml(label)} <span aria-hidden="true">↗</span></button>`; }
  function empty(message) { return `<p class="room-empty">${escapeHtml(message)}</p>`; }
  function paragraph(value) { return `<p class="room-prose">${escapeHtml(value)}</p>`; }
  function safeLink(value) { try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) ? url.href : ""; } catch (_) { return ""; } }
  function renderStations() {
    const o = currentOverview();
    const diaries = list(o.life_observation?.diaries);
    const statuses = {
      calendar: `${scheduleItems().length} 项近期安排`,
      desk: `${diaries.length} 篇日记 · ${Number(memoPayload().active || 0)} 张待办`,
      shelf: creativeAvailable() ? `${Number(o.bookshelf?.public_count || 0)} 部公开作品` : "创作扩展未就绪",
      wardrobe: o.daily_outfit?.available ? "有穿搭照片" : "今日暂无照片",
      bed: o.life_observation?.dream?.content ? "有梦境记录" : "暂无梦境记录",
      radio: o.news?.last_digest?.headline || o.web_exploration?.last_digest?.note ? "有新的见闻" : "暂无见闻记录",
      garden: `${Number(o.personal_goals?.active_count || 0)} 个进行中目标`,
      ...Object.fromEntries(Object.keys(EXTENSIONS).map(id => [id, extensionState(id).label])),
    };
    const dock = byId("homeRoomStations");
    if (!dock) return;
    dock.innerHTML = Object.entries(STATIONS).map(([id, [number, label]]) => `<button type="button" data-home-room-station="${id}" aria-pressed="${runtime.station === id}" class="${runtime.station === id ? "is-selected" : ""}"><span>${number}</span><div><b>${label}</b><small>${escapeHtml(statuses[id])}</small></div></button>`).join("");
  }
  function selectStation(id, { camera = true, keyboard = false } = {}) {
    runtime.station = STATIONS[id] ? id : "overview";
    runtime.root.dataset.roomStation = runtime.station;
    renderStations(); renderInspector();
    if (camera) runtime.three?.focus?.(runtime.station);
    if (runtime.station === "desk") void loadMemos();
    if (runtime.station === "wardrobe") void hydrateOutfit();
    if (keyboard && runtime.station !== "overview") byId("homeRoomInspectorTitle")?.focus({ preventScroll: true });
    if (runtime.station !== "overview" && window.matchMedia?.("(max-width: 980px)")?.matches) {
      byId("homeRoomInspector")?.scrollIntoView({ behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ? "instant" : "smooth", block: "start" });
    }
  }
  function renderInspector() {
    const panel = byId("homeRoomInspector");
    if (!panel) return;
    const selected = STATIONS[runtime.station];
    panel.hidden = !selected;
    runtime.root.querySelectorAll("[data-room-default-card]").forEach(el => { el.hidden = Boolean(selected); });
    if (!selected) return;
    const body = byId("homeRoomInspectorBody");
    const focused = runtime.context.document.activeElement;
    const draftFocused = focused?.id === "homeRoomMemoDraft";
    const caret = draftFocused ? [focused.selectionStart, focused.selectionEnd] : null;
    const scroll = body.scrollTop;
    const inspectorKey = `${runtime.station}:${runtime.diaryIndex}`;
    const samePage = runtime.inspectorKey === inspectorKey;
    const readingOpen = samePage && body.querySelector(".room-reading")?.open;
    runtime.inspectorKey = inspectorKey;
    setText("homeRoomInspectorNumber", selected[0]);
    setText("homeRoomInspectorTitle", selected[1]);
    setText("homeRoomInspectorHint", selected[2]);
    const o = currentOverview(), life = o.life_observation || {}, daily = o.daily_state || {};
    let content = "";
    if (runtime.station === "calendar") {
      content = `<small class="room-eyebrow">${escapeHtml(formatDate(daily.date))}</small>`;
      const items = scheduleItems();
      content += items.length ? `<ol class="room-agenda">${items.map(item => `<li><time>${escapeHtml(item.time)}</time><b>${escapeHtml(item.title)}</b><small>${escapeHtml(scheduleLabel(item))}</small></li>`).join("")}</ol>` : empty("今天的日程还没有写好。");
      content += `<p class="room-footnote">时间走过不代表已经完成；确认发生的安排会单独标记。</p>${action("查看月历 / 添加约定", "calendar")}`;
    }
    if (runtime.station === "desk") {
      const diaries = list(life.diaries).slice().reverse();
      runtime.diaryIndex = Math.max(0, Math.min(runtime.diaryIndex, diaries.length - 1));
      const diary = diaries[runtime.diaryIndex];
      content = `<div class="room-section-heading"><h4>日记本</h4><div class="room-page-controls"><button type="button" data-room-page="-1" aria-label="较新的日记" ${runtime.diaryIndex <= 0 ? "disabled" : ""}>←</button><span>${diaries.length ? runtime.diaryIndex + 1 : 0} / ${diaries.length}</span><button type="button" data-room-page="1" aria-label="较早的日记" ${runtime.diaryIndex >= diaries.length - 1 ? "disabled" : ""}>→</button></div></div>`;
      content += diary ? `<time class="room-eyebrow">${escapeHtml(diary.date)}</time>${paragraph(diary.summary || diary.body)}${diary.body && diary.summary ? `<details class="room-reading"><summary>展开这一页</summary>${paragraph(diary.body)}</details>` : ""}` : empty("还没有日记。生成后会放在这里。");
      content += `<div class="room-section-heading"><h4>留一张便签</h4><small>与陪伴便签同步</small></div><form id="homeRoomMemoForm" class="room-memo-form"><label class="sr-only" for="homeRoomMemoDraft">便签内容</label><textarea id="homeRoomMemoDraft" maxlength="800" rows="2" placeholder="想记住的事，写在这里…" ${runtime.memoBusy ? "disabled" : ""}>${escapeHtml(runtime.memoDraft)}</textarea><button type="submit" class="room-action" ${runtime.memoBusy || !runtime.memoDraft.trim() ? "disabled" : ""}>${runtime.memoBusy ? "保存中…" : "保存便签"}</button></form>`;
      if (runtime.memoError) content += `<p role="alert" class="room-error">${escapeHtml(runtime.memoError)}</p><button type="button" class="room-action" data-room-memo-retry>重新读取</button>`;
      if (runtime.memoLoading) content += `<p role="status" class="room-footnote">正在读取便签…</p>`;
      const notes = list(memoPayload().items).slice(0, 8);
      content += notes.length ? `<ul class="room-memos">${notes.map(note => `<li><button type="button" data-room-memo-id="${escapeHtml(note.id)}" data-room-memo-action="${note.status === "completed" ? "reopen" : "complete"}" aria-label="${note.status === "completed" ? "恢复" : "完成"}便签：${escapeHtml(note.title || note.content)}" ${runtime.memoBusy ? "disabled" : ""}>${note.status === "completed" ? "✓" : "○"}</button><div class="${note.status === "completed" ? "is-done" : ""}"><b>${escapeHtml(note.title || note.content)}</b>${note.title && note.content ? `<p>${escapeHtml(note.content)}</p>` : ""}<small>${escapeHtml(note.due_text || (note.status === "completed" ? "已完成" : "无到期时间"))}${note.repeat && note.repeat !== "none" ? " · 重复便签" : ""}</small></div></li>`).join("")}</ul>` : empty("还没有便签，先留下一件小事。");
      content += action("查看日记与生活记录", "diary");
    }
    if (runtime.station === "bed") {
      const dream = life.dream || {};
      content = `<div class="room-sleep-state"><span>睡眠记录</span><b>${escapeHtml(daily.sleep_phase || daily.sleep || "未记录")}</b></div>`;
      content += dream.content ? `<small class="room-eyebrow">${escapeHtml(dream.date)} · ${escapeHtml(dream.label || "梦境")}</small>${paragraph(dream.content)}${dream.afterglow ? `<blockquote>${escapeHtml(dream.afterglow)}</blockquote>` : ""}` : empty("昨夜还没有留下梦境。");
      content += action("查看梦境与状态", "dream");
    }
    if (runtime.station === "wardrobe") {
      const outfit = o.daily_outfit || {};
      content = runtime.outfitData ? `<figure class="room-outfit-preview"><figcaption>${escapeHtml(outfit.date || "今日")} · 穿搭记录</figcaption></figure>` : empty(byId("homeRoomOutfitLabel")?.textContent || "今日暂无穿搭照片");
      content += paragraph("在角色衣柜中管理服装、参考图和穿搭偏好。") + action("打开角色衣柜", "wardrobe");
    }
    if (runtime.station === "shelf") {
      const available = creativeAvailable(), books = available ? list(o.bookshelf?.public_books) : [];
      content = available ? (books.length ? books.slice(0, 6).map(book => `<article class="room-book"><span aria-hidden="true">▤</span><div><h4>${escapeHtml(book.title || "未命名作品")}</h4><p>${escapeHtml(book.summary || book.description || "公开收藏")}</p></div></article>`).join("") : empty("书架还空着，故事会从这里开始。")) : empty("创作扩展未就绪。启用「我会替你留住故事」后，这里会展示公开作品。");
      if (available) content += action("阅读 / 管理作品", "creative") + `<div class="room-locked"><b>抽屉夹层</b><p>私密资料保留在原来的密码抽屉里。</p>${action("前往夹层", "drawer")}</div>`;
    }
    if (runtime.station === "radio") {
      const news = o.news?.last_digest || {}, web = o.web_exploration?.last_digest || {};
      content = `<h4>最近读到</h4>`;
      content += news.headline ? `<small class="room-eyebrow">${escapeHtml(news.source || "新闻阅读")}</small><h4>${escapeHtml(news.headline)}</h4>${paragraph(news.impression || "暂无阅读感想")}` : empty(o.news?.enabled ? "还没有新闻阅读记录。" : "新闻阅读未启用。");
      const link = safeLink(news.link);
      if (link) content += `<a class="room-source" href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">阅读原文 ↗</a>`;
      content += `<div class="room-section-heading"><h4>好奇的发现</h4></div>`;
      content += web.note ? `<h4>${escapeHtml(web.topic || web.source_title || "探索笔记")}</h4>${paragraph(web.note)}` : empty(o.web_exploration?.enabled ? "还没有探索笔记。" : "主动搜索未启用。");
      content += action("浏览新闻记录", "news") + action("浏览探索记录", "search");
    }
    if (runtime.station === "garden") {
      const goals = list(o.personal_goals?.items);
      content = goals.length ? goals.slice(0, 5).map(goal => {
        const progress = Math.min(100, Math.max(0, Number(goal.progress) || 0));
        return `<article class="room-goal"><div><h4>${escapeHtml(goal.title)}</h4><span>${progress}%</span></div><progress max="100" value="${progress}" aria-label="${escapeHtml(goal.title)}的进度"></progress><small>${escapeHtml(goal.status_label || "未记录状态")}</small>${paragraph(goal.next_step || goal.note || "暂无下一步记录")}</article>`;
      }).join("") : empty("还没有长期目标。每一个小进展，都可以从这里开始。");
      content += action("查看 / 管理目标", "goals") + action("打开技能成长", "skills");
    }
    if (EXTENSIONS[runtime.station]) {
      const id = runtime.station, extension = EXTENSIONS[id], state = extensionState(id);
      const catalog = runtime.three?.getCatalog(), item = runtime.three?.getLayout().items[id];
      const appearance = catalog?.objectModels?.[id]?.[item?.model];
      content = `<div class="room-extension-state" data-extension-state="${state.ready ? "ready" : "pending"}"><span aria-hidden="true">${id === "game" ? "♟" : id === "image" ? "▣" : "☎"}</span><b>${escapeHtml(state.label)}</b></div>${paragraph(extension.intro)}`;
      if (appearance) content += `<dl class="room-extension-details"><div><dt>当前物件</dt><dd>${escapeHtml(appearance.label)}</dd></div><div><dt>摆放位置</dt><dd>${escapeHtml(catalog.surfaces[item.surface])}</dd></div></dl>`;
      content += state.ready ? action("打开生图工作室", "image") : `<p class="room-empty" role="status">${escapeHtml(extension.pending)}</p><button type="button" class="room-action" disabled>${id === "image" ? "工作室暂不可用" : "功能入口待接通"}</button>`;
      content += `<button type="button" class="room-action" data-room-customize="${id}" ${runtime.three ? "" : "disabled"}>更换物件 / 布置位置 <span aria-hidden="true">↗</span></button>`;
      if (!runtime.three) content += `<p class="room-footnote">体素场景加载成功后可以布置物件。</p>`;
      content += `<p class="room-footnote">外观只改变小屋布置。功能状态会随扩展更新。</p>`;
    }
    body.innerHTML = content;
    if (runtime.station === "wardrobe" && runtime.outfitData) {
      const image = runtime.context.document.createElement("img");
      image.src = runtime.outfitData;
      image.alt = `${text(o.daily_outfit?.date) || "今日"}的穿搭照片`;
      body.querySelector(".room-outfit-preview")?.prepend(image);
    }
    if (readingOpen && body.querySelector(".room-reading")) body.querySelector(".room-reading").open = true;
    body.scrollTop = samePage ? scroll : 0;
    if (draftFocused && !runtime.memoBusy) {
      const input = byId("homeRoomMemoDraft");
      input?.focus({ preventScroll: true });
      if (caret) input?.setSelectionRange(...caret);
    }
  }
  async function loadMemos(force = false) {
    if (runtime.memoLoading || (!force && runtime.context?.state?.memoNotes)) return;
    const epoch = runtime.epoch, revision = runtime.memoRevision, context = runtime.context;
    runtime.memoLoading = true; runtime.memoError = ""; renderInspector();
    try {
      const result = await context.fetchJson("/memo/list");
      if (epoch !== runtime.epoch || revision !== runtime.memoRevision) return;
      if (result?.memo_notes) context.applyMemoPayload?.(result.memo_notes);
    } catch (error) { if (epoch === runtime.epoch && revision === runtime.memoRevision) runtime.memoError = `便签读取失败：${text(error.message)}`; }
    finally { if (epoch === runtime.epoch) { runtime.memoLoading = false; if (runtime.station === "desk") renderInspector(); renderStations(); } }
  }
  async function mutateMemo(payload) {
    if (runtime.memoBusy) return;
    const epoch = runtime.epoch, context = runtime.context;
    runtime.memoRevision += 1;
    runtime.memoBusy = true; runtime.memoError = ""; renderInspector();
    try {
      const result = await context.postJson("/memo/update", payload);
      if (epoch !== runtime.epoch) return;
      if (!result?.memo_notes) throw new Error("未收到便签保存结果");
      context.applyMemoPayload?.(result.memo_notes);
      if (payload.action === "save") runtime.memoDraft = "";
      context.showToast(payload.action === "save" ? "便签已保存" : payload.action === "reopen" ? "便签已恢复" : "已记录本次完成");
    } catch (error) { if (epoch === runtime.epoch) runtime.memoError = `未能保存，请重试：${text(error.message)}`; }
    finally { if (epoch === runtime.epoch) { runtime.memoBusy = false; if (runtime.station === "desk") renderInspector(); renderStations(); } }
  }

  function updateTourButton(running) {
    const button = runtime.root?.querySelector("[data-home-room-camera-tour]");
    if (!button) return;
    button.setAttribute("aria-pressed", running ? "true" : "false");
    button.textContent = running ? "暂停运镜" : "电影巡游";
  }

  function savedLayout() {
    try {
      const raw = readStorage(LAYOUT_STORAGE_KEY + encodeURIComponent(personaKey()), "");
      return raw ? JSON.parse(raw) : null;
    } catch (_) { return null; }
  }
  async function layoutRequest(promise) {
    let timer;
    try {
      return await Promise.race([promise, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error("房间存储请求超时")), 15000);
      })]);
    } finally { clearTimeout(timer); }
  }
  function validLayout(value) {
    return value?.version === 1 && value.items && typeof value.items === "object" && !Array.isArray(value.items);
  }
  function renderEditor(value = runtime.editorState) {
    if (value) runtime.editorState = value;
    if (!runtime.root) return;
    runtime.root.dataset.roomEditing = String(runtime.editing);
    const panel = byId("homeRoomEditor"); if (panel) panel.hidden = !runtime.editing;
    const badge = byId("homeRoomEditBadge"); if (badge) badge.hidden = !runtime.editing;
    const edit = runtime.root.querySelector("[data-room-edit]");
    if (edit) {
      edit.setAttribute("aria-pressed", String(runtime.editing));
      edit.disabled = runtime.layoutLoading || !runtime.three;
      edit.setAttribute("aria-busy", String(runtime.layoutLoading));
      edit.textContent = runtime.layoutLoading ? "读取布置…" : "布置房间";
    }
    const save = runtime.root.querySelector("[data-room-save]"), cancel = runtime.root.querySelector("[data-room-cancel]");
    if (save) { save.disabled = Boolean(runtime.layoutSaving); save.textContent = runtime.layoutSaving ? "保存中…" : "保存布置"; }
    if (cancel) cancel.disabled = Boolean(runtime.layoutSaving);
    panel?.setAttribute("aria-busy", String(Boolean(runtime.layoutSaving)));
    setText("homeRoomStorageNote", runtime.layoutLoadError
      ? "暂未读取到已保存的布置，当前使用浏览器旧布局或默认布局。可刷新页面重试读取；保存将更新当前人格的布置。"
      : "保存到当前 AstrBot，按人格独立记住。默认布局也可以撤销。");
    for (const selector of ["[data-home-room-camera-tour]", "[data-room-next-shot]"]) {
      const button = runtime.root.querySelector(selector); if (button) button.disabled = runtime.editing || !runtime.three;
    }
    if (!runtime.editing || !value?.item) return;
    const catalog = runtime.three.getCatalog();
    const houseOptions = byId("homeRoomHouseOptions");
    if (houseOptions && !houseOptions.children.length) {
      houseOptions.innerHTML = Object.entries(catalog.houseStyles).map(([id, style]) => `<button type="button" data-room-house-style="${escapeHtml(id)}" aria-pressed="false"><i aria-hidden="true"></i><span><b>${escapeHtml(style.label)}</b><small>${escapeHtml(style.materials)}</small></span></button>`).join("");
    }
    const houseStyle = catalog.houseStyles[value.houseStyle] || catalog.houseStyles.classic;
    setText("homeRoomHouseLabel", houseStyle.label); setText("homeRoomHouseNote", houseStyle.note);
    runtime.root.querySelectorAll("[data-room-house-style]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.roomHouseStyle === value.houseStyle)));
    const select = byId("homeRoomFurniture");
    if (select && !select.options.length) {
      select.innerHTML = [["生活家具", id => !EXTENSIONS[id]], ["拓展物件", id => Boolean(EXTENSIONS[id])]].map(([label, filter]) => `<optgroup label="${label}">${Object.entries(catalog.furniture).filter(([id]) => filter(id)).map(([id, name]) => `<option value="${escapeHtml(id)}">${escapeHtml(name)}</option>`).join("")}</optgroup>`).join("");
    }
    if (select) select.value = value.id;
    const models = catalog.objectModels[value.id] || {}, modelPicker = byId("homeRoomObjectModel"), surfacePicker = byId("homeRoomSurface");
    byId("homeRoomModelField").hidden = Object.keys(models).length < 2;
    if (modelPicker.dataset.furniture !== value.id) {
      modelPicker.innerHTML = Object.entries(models).map(([id, model]) => `<option value="${id}">${escapeHtml(model.label)}</option>`).join("");
      modelPicker.dataset.furniture = value.id;
    }
    modelPicker.value = value.item.model;
    const surfaces = models[value.item.model]?.surfaces || ["floor"], surfaceKey = surfaces.join(",");
    if (surfacePicker.dataset.options !== surfaceKey) {
      surfacePicker.innerHTML = surfaces.map(id => `<option value="${id}">${escapeHtml(catalog.surfaces[id])}</option>`).join("");
      surfacePicker.dataset.options = surfaceKey;
    }
    surfacePicker.value = value.item.surface; surfacePicker.disabled = surfaces.length === 1;
    const wall = value.item.surface !== "floor";
    for (const field of runtime.root.querySelectorAll("[data-room-coordinate]")) {
      field.value = String(value.item[field.dataset.roomCoordinate]);
      const key = field.dataset.roomCoordinate;
      field.parentElement.hidden = key === "y" ? !wall : key === "rotation" ? wall : key === "x" ? value.item.surface === "left-wall" : value.item.surface === "back-wall";
    }
    runtime.root.querySelector(".room-rotate").hidden = wall;
    runtime.root.querySelectorAll("[data-room-nudge]").forEach(button => {
      const [x, z] = button.dataset.roomNudge.split(",").map(Number);
      button.setAttribute("aria-label", z ? wall ? z < 0 ? "挂件升高" : "挂件降低" : z < 0 ? "家具向后移动" : "家具向前移动" : x < 0 ? "家具向左移动" : "家具向右移动");
    });
    const styleOptions = byId("homeRoomStyleOptions");
    if (styleOptions && !styleOptions.children.length) {
      styleOptions.innerHTML = Object.entries(catalog.styles).map(([id, label]) => `<button type="button" data-room-style="${escapeHtml(id)}" aria-pressed="false"><i aria-hidden="true"></i><span><b>${escapeHtml(label)}</b><small></small></span></button>`).join("");
      setText("homeRoomStyleCount", `${Object.keys(catalog.styles).length} 款可选 · 可混搭`);
    }
    runtime.root.querySelectorAll("[data-room-style]").forEach(button => {
      const id = button.dataset.roomStyle, detail = catalog.styleDetails?.[id], model = models[value.item.model]?.label || detail?.models?.[value.id] || catalog.styles[id];
      button.setAttribute("aria-pressed", String(id === value.item.style));
      button.setAttribute("aria-label", `${catalog.styles[id]} · ${model}`);
      button.querySelector("small").textContent = model;
      button.title = detail?.note || "";
    });
    setText("homeRoomStyleNote", catalog.styleDetails?.[value.item.style]?.note || "");
    const undo = runtime.root.querySelector("[data-room-undo]"), redo = runtime.root.querySelector("[data-room-redo]");
    if (undo) undo.disabled = !value.canUndo; if (redo) redo.disabled = !value.canRedo;
    setText("homeRoomPlacementHint", value.hint);
    byId("homeRoomPlacementHint").dataset.blocked = String(Boolean(value.blocked));
    const dirty = runtime.three && JSON.stringify(runtime.editSnapshot) !== JSON.stringify(runtime.three.getLayout());
    setText("homeRoomLayoutStatus", runtime.layoutSaving ? "保存中…" : runtime.layoutSaveError ? "保存失败" : dirty ? "未保存" : "尚未改动");
  }
  function syncRoomLayout() {
    if (!runtime.three || runtime.layoutPersona === personaKey()) return;
    runtime.editing = false; runtime.editSnapshot = null; runtime.three.setEditing(false);
    runtime.three.setLayout(savedLayout()); runtime.layoutPersona = personaKey();
    runtime.three.toggleDoor(false, false); updateDoorButton(false);
    runtime.layoutLoading = true; runtime.layoutLoadError = false; runtime.layoutSaveError = false;
    renderEditor();
    const scene = runtime.three, persona = runtime.layoutPersona, epoch = runtime.epoch;
    const current = () => epoch === runtime.epoch && persona === runtime.layoutPersona && scene === runtime.three;
    void (async () => {
      try {
        const result = await layoutRequest(runtime.context.fetchJson(`/home-room/layout?_persona_id=${encodeURIComponent(persona)}`, { dedupe: false }));
        if (!current()) return;
        if (result?.layout !== null && !validLayout(result?.layout)) throw new Error("房间存储暂不可用");
        // A missing server preference keeps legacy browser layouts until Save.
        if (result.layout) {
          scene.setLayout(result.layout);
          writeStorage(LAYOUT_STORAGE_KEY + encodeURIComponent(persona), JSON.stringify(result.layout));
        }
      } catch (_) { if (current()) runtime.layoutLoadError = true; }
      finally { if (current()) { runtime.layoutLoading = false; renderEditor(); } }
    })();
  }
  function beginEditing() {
    if (!runtime.three || runtime.editing || runtime.layoutLoading) return;
    selectStation("overview", { camera: false });
    runtime.editSnapshot = runtime.three.getLayout(); runtime.editing = true; runtime.layoutSaveError = false;
    runtime.three.setEditing(true); renderEditor();
    byId("homeRoomEditorTitle")?.focus({ preventScroll: true });
  }
  async function finishEditing(save) {
    if (!runtime.editing || !runtime.three || runtime.layoutSaving) return;
    if (save) {
      const context = runtime.context, persona = runtime.layoutPersona, epoch = runtime.epoch;
      const layout = runtime.three.getLayout(), operation = {};
      runtime.layoutSaving = operation; runtime.layoutSaveError = false; renderEditor();
      try {
        const result = await layoutRequest(context.postJson("/home-room/layout/update", { layout, _persona_id: persona }));
        if (epoch !== runtime.epoch || runtime.layoutSaving !== operation || persona !== personaKey()) return;
        if (result?.saved !== true || !validLayout(result.layout)) throw new Error("未收到保存确认，请确认陪伴插件已加载最新版本");
        writeStorage(LAYOUT_STORAGE_KEY + encodeURIComponent(persona), JSON.stringify(layout));
        runtime.layoutLoadError = false;
        // Editing may continue during the request. Those newer changes stay a
        // draft, and Cancel now restores the snapshot that really was saved.
        runtime.editSnapshot = layout;
        if (JSON.stringify(layout) !== JSON.stringify(runtime.three.getLayout())) {
          context.showToast("此前的布置已保存，后续调整还未保存。"); return;
        }
      } catch (error) {
        if (epoch === runtime.epoch && runtime.layoutSaving === operation) {
          runtime.layoutSaveError = true;
          context.showToast(`布置未能保存：${text(error.message, 160)}。本次调整仍保留，可以重试。`, "error");
        }
        return;
      } finally {
        if (runtime.layoutSaving === operation) { runtime.layoutSaving = null; renderEditor(); }
      }
    } else runtime.three.setLayout(runtime.editSnapshot);
    runtime.editing = false; runtime.editSnapshot = null; runtime.three.setEditing(false); renderEditor();
    runtime.root.querySelector("[data-room-edit]")?.focus({ preventScroll: true });
    runtime.context.showToast(save ? "房间布置已保存到 AstrBot，下次打开会恢复。" : "已取消本次布置。");
  }
  function nudgeFurniture(x, z) {
    const item = runtime.editorState?.item; if (!item) return;
    const patch = item.surface === "back-wall" ? { x: item.x + x, y: item.y - z } : item.surface === "left-wall" ? { z: item.z - x, y: item.y - z } : { x: item.x + x, z: item.z + z };
    runtime.three.updateFurniture(patch);
  }
  function updateDoorButton(open) {
    const button = runtime.root?.querySelector("[data-room-door]");
    if (!button) return;
    button.textContent = open ? "关门" : "开门"; button.setAttribute("aria-pressed", String(open));
    button.setAttribute("aria-label", open ? "关上房门" : "打开房门");
  }
  function onDoorChange(open) {
    updateDoorButton(open);
    // A local, persona-scoped extension seam. Opening a door makes no request
    // and never invents an outdoor destination or changes companion state.
    runtime.root?.dispatchEvent(new CustomEvent("companion:room-door", {
      bubbles: true, detail: { version: 1, doorId: "entrance", open, personaId: personaKey() },
    }));
  }

  function mountThree() {
    const mount = runtime.root?.querySelector("[data-home-room-3d-mount]");
    const api = window.PrivateCompanionHomeRoom3D;
    if (!mount || !api?.mount) throw new Error("3D 小屋模块未注册");
    if (!runtime.three) {
      runtime.three = api.mount(mount, {
        light: runtime.light,
        layout: savedLayout(),
        reducedMotion: window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true,
        onTourChange: updateTourButton,
        onSelect: (id) => selectStation(id, { camera: false }),
        onShotChange: (label) => setText("homeRoomShot", label),
        onEditorChange: renderEditor,
        onDoorChange,
        onMoment,
        onAtmosphereChange: updateEncounterButton,
        onError: () => {
          runtime.three?.dispose?.(); runtime.three = null;
          const error = runtime.root?.querySelector("[data-home-room-3d-error]");
          if (error) error.hidden = false;
        },
        onFocusChange: (focus) => {
          runtime.root?.querySelectorAll("[data-home-room-camera-focus]").forEach((button) => {
            button.classList.toggle("is-active", button.dataset.homeRoomCameraFocus === focus);
          });
        },
      });
    }
    syncRoomLayout();
    runtime.three.setLight?.(runtime.light);
    runtime.three.setActive?.(runtime.active && !runtime.context?.document?.hidden);
    runtime.three.resize?.();
    applyAtmosphere();
  }

  function ensureThree() {
    if (runtime.three) {
      mountThree();
      return Promise.resolve(runtime.three);
    }
    if (runtime.threeLoading) return runtime.threeLoading;
    const loading = runtime.root?.querySelector("[data-home-room-3d-loading]");
    const error = runtime.root?.querySelector("[data-home-room-3d-error]");
    if (loading) loading.hidden = false;
    if (error) error.hidden = true;
    runtime.threeLoading = runtime.context.loadOptionalModule("homeRoom3d")
      .then(() => {
        mountThree();
        if (loading) loading.hidden = true;
        return runtime.three;
      })
      .catch((loadError) => {
        if (loading) loading.hidden = true;
        if (error) error.hidden = false;
        renderEditor();
        throw loadError;
      })
      .finally(() => {
        runtime.threeLoading = null;
      });
    return runtime.threeLoading;
  }

  function loadRoomScene() {
    ensureThree().catch((error) => {
      runtime.context.showToast(`体素小屋加载失败：${text(error?.message, 120)}`, "error");
    });
    updateEncounterButton();
  }

  function setLight(light, options = {}) {
    runtime.light = ["night", "dusk"].includes(light) ? light : "day";
    if (runtime.root) {
      runtime.root.dataset.homeRoomLight = runtime.light;
      const panel = runtime.root.closest(".home-room-panel");
      if (panel) panel.dataset.homeRoomLight = runtime.light;
    }
    const button = runtime.root?.querySelector("[data-home-room-theme]");
    if (button) {
      button.textContent = runtime.light === "night" ? "☾" : "☼";
      button.setAttribute("aria-label", runtime.light === "night" ? "切换为日间光线" : "切换为夜间光线");
    }
    runtime.three?.setLight?.(runtime.light);
    if (options.persist !== false) {
      runtime.lightMode = runtime.light;
      writeStorage(LIGHT_STORAGE_KEY, runtime.lightMode);
      renderEnvironment();
    }
  }

  function bind() {
    const root = runtime.root;
    if (!root || root.dataset.homeRoomBound === "1") return;
    root.dataset.homeRoomBound = "1";
    root.addEventListener("click", (event) => {
      if (event.target.closest("[data-room-weather-refresh]")) { void refreshEnvironment(); return; }
      if (event.target.closest("[data-room-encounter]")) { runtime.three?.encounter?.(); return; }
      const customize = event.target.closest("[data-room-customize]");
      if (customize) { beginEditing(); runtime.three?.selectFurniture(customize.dataset.roomCustomize); byId("homeRoomObjectModel")?.focus({ preventScroll: true }); return; }
      if (event.target.closest("[data-room-edit]")) { if (!runtime.editing) beginEditing(); else byId("homeRoomEditorTitle")?.focus({ preventScroll: true }); return; }
      if (event.target.closest("[data-room-save]")) { finishEditing(true); return; }
      if (event.target.closest("[data-room-cancel]")) { finishEditing(false); return; }
      if (event.target.closest("[data-room-door]")) { runtime.three?.toggleDoor(); return; }
      if (event.target.closest("[data-room-next-shot]")) { selectStation("overview", { camera: false }); runtime.three?.nextShot(); return; }
      if (event.target.closest("[data-room-undo]")) { runtime.three?.undoEdit(); return; }
      if (event.target.closest("[data-room-redo]")) { runtime.three?.undoEdit(true); return; }
      if (event.target.closest("[data-room-defaults]")) { runtime.three?.restoreDefaults(); return; }
      const houseStyle = event.target.closest("[data-room-house-style]");
      if (houseStyle) { runtime.three?.updateHouseStyle(houseStyle.dataset.roomHouseStyle); return; }
      const style = event.target.closest("[data-room-style]");
      if (style) { runtime.three?.updateFurniture({ style: style.dataset.roomStyle }); return; }
      const nudge = event.target.closest("[data-room-nudge]"), rotate = event.target.closest("[data-room-rotate]");
      if ((nudge || rotate) && runtime.editorState?.item) {
        const item = runtime.editorState.item;
        if (rotate) runtime.three.updateFurniture({ rotation: item.rotation + Number(rotate.dataset.roomRotate) });
        else { const [x, z] = nudge.dataset.roomNudge.split(",").map(Number); nudgeFurniture(x, z); }
        return;
      }
      const station = event.target.closest("[data-home-room-station]");
      if (station) { selectStation(station.dataset.homeRoomStation, { keyboard: event.detail === 0 }); return; }
      if (event.target.closest("[data-room-close]")) { selectStation("overview"); return; }
      const page = event.target.closest("[data-room-page]");
      if (page) { runtime.diaryIndex += Number(page.dataset.roomPage); renderInspector(); return; }
      const memo = event.target.closest("[data-room-memo-id]");
      if (memo) { void mutateMemo({ action: memo.dataset.roomMemoAction, id: memo.dataset.roomMemoId }); return; }
      if (event.target.closest("[data-room-memo-retry]")) { void loadMemos(true); return; }
      const destination = event.target.closest("[data-home-room-destination]");
      if (destination) {
        openDestination(destination.dataset.homeRoomDestination);
        return;
      }
      if (event.target.closest("[data-home-room-theme]")) {
        setLight(runtime.light === "night" ? "day" : "night");
        return;
      }
      const cameraFocus = event.target.closest("[data-home-room-camera-focus]");
      if (cameraFocus) {
        selectStation(cameraFocus.dataset.homeRoomCameraFocus);
        return;
      }
      if (event.target.closest("[data-home-room-camera-tour]")) {
        if (runtime.station !== "overview") selectStation("overview");
        const running = runtime.three?.toggleTour?.();
        if (typeof running === "boolean") updateTourButton(running);
        return;
      }
      if (event.target.closest("[data-home-room-camera-reset]")) {
        selectStation("overview", { camera: false }); runtime.three?.reset();
        return;
      }
      if (event.target.closest("[data-home-room-retry-3d]")) {
        loadRoomScene();
      }
    });
    root.addEventListener("change", (event) => {
      if (event.target.id === "homeRoomLightMode") { runtime.lightMode = event.target.value; writeStorage(LIGHT_STORAGE_KEY, runtime.lightMode); renderEnvironment(); return; }
      if (["homeRoomMotion", "homeRoomEvents"].includes(event.target.id)) {
        runtime.atmosphere[event.target.id === "homeRoomMotion" ? "motion" : "events"] = event.target.checked;
        writeStorage(ATMOSPHERE_STORAGE_KEY, JSON.stringify(runtime.atmosphere)); applyAtmosphere(); return;
      }
      if (event.target.id === "homeRoomFurniture") { runtime.three?.selectFurniture(event.target.value); return; }
      if (event.target.id === "homeRoomObjectModel") { runtime.three?.updateFurniture({ model: event.target.value }); return; }
      if (event.target.id === "homeRoomSurface") { runtime.three?.updateFurniture({ surface: event.target.value }); return; }
      const key = event.target.dataset.roomCoordinate;
      if (key && runtime.editing) {
        const value = event.target.valueAsNumber;
        if (Number.isFinite(value)) runtime.three.updateFurniture({ [key]: value });
        else renderEditor();
      }
    });
    root.addEventListener("input", (event) => {
      if (event.target.id !== "homeRoomMemoDraft") return;
      runtime.memoDraft = event.target.value;
      const button = byId("homeRoomMemoForm")?.querySelector('[type="submit"]');
      if (button) button.disabled = runtime.memoBusy || !runtime.memoDraft.trim();
    });
    root.addEventListener("submit", (event) => {
      if (event.target.id !== "homeRoomMemoForm") return;
      event.preventDefault();
      if (runtime.memoDraft.trim()) void mutateMemo({ action: "save", content: runtime.memoDraft.trim(), color: "yellow", remind_enabled: false });
    });
    root.addEventListener("keydown", (event) => {
      if (runtime.editing) {
        if (event.key === "Escape") { event.preventDefault(); finishEditing(false); return; }
        if (event.target.closest("input, textarea, select, [contenteditable]")) return;
        const key = event.key.toLowerCase(), item = runtime.editorState?.item;
        if ((event.ctrlKey || event.metaKey) && ["z", "y"].includes(key)) { event.preventDefault(); runtime.three.undoEdit(key === "y" || event.shiftKey); return; }
        const offsets = { ArrowLeft: [-.25, 0], ArrowRight: [.25, 0], ArrowUp: [0, -.25], ArrowDown: [0, .25] };
        if (item && offsets[event.key]) { event.preventDefault(); const [x, z] = offsets[event.key]; nudgeFurniture(x, z); }
        if (item && key === "r" && !event.ctrlKey && !event.metaKey) { event.preventDefault(); runtime.three.updateFurniture({ rotation: item.rotation + (event.shiftKey ? -45 : 45) }); }
        return;
      }
      if (event.key === "Escape" && runtime.station !== "overview") {
        const previous = runtime.station; selectStation("overview");
        root.querySelector(`[data-home-room-station="${previous}"]`)?.focus({ preventScroll: true });
      }
    });
    runtime.context.document.addEventListener("visibilitychange", () => {
      runtime.three?.setActive?.(runtime.active && !runtime.context.document.hidden);
      syncEnvironmentActivity();
    });
    window.matchMedia?.("(prefers-reduced-motion: reduce)")?.addEventListener?.("change", () => applyAtmosphere());
  }

  function render(context) {
    runtime.context = context;
    runtime.root = context?.document?.getElementById("homeRoomRoot") || null;
    if (!runtime.root) return;
    bind();
    renderEditor();
    renderHeaderAndState();
    renderSchedule();
    renderStations();
    renderInspector();
    void hydrateOutfit();
    const savedLight = readStorage(LIGHT_STORAGE_KEY, "auto");
    runtime.lightMode = ["auto", "day", "night"].includes(savedLight) ? savedLight : "auto";
    try { const saved = JSON.parse(readStorage(ATMOSPHERE_STORAGE_KEY, "{}")); runtime.atmosphere = { motion: saved?.motion !== false, events: saved?.events !== false }; } catch (_) {}
    renderEnvironment();
    loadRoomScene();
    setActive(true);
  }

  function setActive(active) {
    runtime.active = Boolean(active);
    runtime.three?.setActive?.(runtime.active && !runtime.context?.document?.hidden);
    syncEnvironmentActivity();
  }

  function resetPersona() {
    runtime.epoch += 1;
    setActive(false);
    runtime.environment = null; runtime.environmentError = false; runtime.environmentDue = 0; runtime.clockOffset = 0; runtime.moments = [];
    runtime.three?.resetAtmosphere?.();
    setText("homeRoomMoment", "坐一会儿，等一个小小的偶遇。");
    const momentHistory = byId("homeRoomMomentHistory");
    if (momentHistory) momentHistory.innerHTML = "<li>还没有遇见，慢慢来。</li>";
    renderEnvironment();
    runtime.memoBusy = false; runtime.memoLoading = false; runtime.memoDraft = ""; runtime.memoError = "";
    runtime.station = "overview"; runtime.diaryIndex = 0;
    runtime.inspectorKey = "";
    runtime.editing = false; runtime.editSnapshot = null; runtime.layoutPersona = null;
    runtime.layoutLoading = false; runtime.layoutLoadError = false; runtime.layoutSaving = null; runtime.layoutSaveError = false;
    runtime.three?.setEditing(false); runtime.three?.setLayout(null); runtime.three?.toggleDoor(false, false);
    renderEditor(); updateDoorButton(false);
    runtime.three?.reset?.();
    const inspector = byId("homeRoomInspector");
    if (inspector) inspector.hidden = true;
    const body = byId("homeRoomInspectorBody");
    if (body) body.replaceChildren();
    for (const id of ["homeRoomSchedule", "homeRoomStateFacts", "homeRoomStations"]) byId(id)?.replaceChildren();
    setText("homeRoomCurrentLine", "正在读取此刻的生活状态…");
    setText("homeRoomNote", "");
    setText("homeRoomOutfitLabel", "正在读取穿搭记录…");
    runtime.outfitRequestId += 1;
    runtime.outfitKey = "";
    resetOutfitThumb();
  }

  return {
    render,
    setActive,
    resetPersona,
  };
})();
