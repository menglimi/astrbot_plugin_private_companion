/* 小屋顶部的角色地点、实况天气与当地时钟。 */
"use strict";
window.CompanionHomeEnvironment = (() => {
  const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, char => ({
    "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;",
  }[char]));
  let host = null, input = {}, context = "", sequence = 0, loading = false, weather = null, clockTimer = null;

  function timezoneOf(state, life) {
    const value = state?.settings?.planning_timezone || life?.config?.timezone || "Asia/Shanghai";
    try { new Intl.DateTimeFormat("zh-CN", {timeZone:value}); return value; }
    catch { return "Asia/Shanghai"; }
  }
  function locationOf(settings) {
    const raw = settings?.planning_weather_location;
    if (raw && typeof raw === "object" && typeof raw.name === "string") return raw.name.trim();
    if (typeof raw !== "string" || !raw.trim()) return "";
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed.name === "string" ? parsed.name.trim() : "";
    } catch { return ""; }
  }
  function phase(hour) {
    return hour < 6 || hour >= 19 ? "夜间" : hour < 8 || hour >= 17 ? "晨昏" : "日间";
  }
  function clock(now, zone) {
    const date = new Date(now);
    const parts = new Intl.DateTimeFormat("en-GB", {timeZone:zone, hour:"2-digit", minute:"2-digit", hourCycle:"h23"}).formatToParts(date);
    const hour = Number(parts.find(part => part.type === "hour")?.value || 0);
    /* 星期用短名（周二），给 320px 手机留出同排显示时区的空间。 */
    const dateLabel = new Intl.DateTimeFormat("zh-CN", {timeZone:zone, month:"long", day:"numeric", weekday:"short"}).format(date);
    return {date, hour, label:parts.map(part => part.value).join(""), dateLabel, phase:phase(hour)};
  }
  function sceneForTime(now, state, life) {
    const hour = clock(now, timezoneOf(state, life)).hour;
    return hour < 6 || hour >= 19 ? "night" : hour < 8 ? "dawn" : hour < 17 ? "day" : "evening";
  }
  function iconFor(condition) {
    if (/雷|暴/.test(condition)) return "cloud-lightning";
    if (/雨|雪/.test(condition)) return /雪/.test(condition) ? "cloud-snow" : "cloud-rain";
    if (/雾|霾/.test(condition)) return "cloud-fog";
    if (/云|阴/.test(condition)) return /晴/.test(condition) ? "cloud-sun" : "cloud";
    if (/晴/.test(condition)) return "sun";
    return "cloud-sun";
  }
  function freshness(observedAt) {
    const stamp = typeof observedAt === "string" ? Date.parse(observedAt) : NaN;
    if (!Number.isFinite(stamp)) return "更新时间未知";
    const minutes = Math.max(0, Math.floor((Date.now() + serverOffset - stamp) / 60000));
    return minutes < 1 ? "刚刚更新" : minutes < 60 ? `${minutes} 分钟前更新` : `${Math.floor(minutes / 60)} 小时前更新`;
  }
  function weatherText(settings, location) {
    if (!input.state?.settings) return input.loading ? "正在读取天气设置…" : "天气设置暂不可读";
    if (settings.planning_weather_enabled !== true) return "天气未启用";
    if (!location) return "未设置天气地点";
    if (loading && !weather) return "正在读取天气…";
    if (weather?.state === "live" || weather?.state === "stale") {
      const temperature = typeof weather.temperature_c === "number" && Number.isFinite(weather.temperature_c)
        ? ` · ${weather.temperature_c}°C` : "";
      const validUntil = typeof weather.valid_until === "string" ? Date.parse(weather.valid_until) : NaN;
      const expired = Number.isFinite(validUntil) && validUntil <= Date.now() + serverOffset;
      return `${expired ? "上次实况" : weather.condition || "当前天气"}${temperature}`;
    }
    if (weather?.state === "disabled") return "天气未启用";
    if (weather?.state === "unconfigured") return "未设置天气地点";
    if (input.loading) return "正在读取天气…";
    return "天气暂不可用";
  }
  function weatherDetail(settings, location) {
    if (weather?.refresh_throttled) return `刚刚更新过 · ${freshness(weather.observed_at)} · 请稍后再试`;
    if (weather?.state === "live" || weather?.state === "stale") {
      const validUntil = typeof weather.valid_until === "string" ? Date.parse(weather.valid_until) : NaN;
      const expired = Number.isFinite(validUntil) && validUntil <= Date.now() + serverOffset;
      const age = Number.isFinite(validUntil)
        ? expired ? "已超过有效期" : `有效至 ${clock(validUntil, timezoneOf(input.state, input.life)).label}`
        : freshness(weather.observed_at);
      if (weather.state === "stale" && weather.fetched_at) return `更新未成功 · 显示上次实况 · ${age}`;
      if (loading) return `正在更新 · 显示上次实况 · ${age}`;
      return `${expired ? "上次实况" : "实况天气"} · ${age}`;
    }
    if (!input.state?.settings) return input.loading ? "角色设置读取中" : "角色设置暂不可读";
    if (settings.planning_weather_enabled !== true) return "角色时区仍按下方显示";
    if (!location) return "请先设置角色天气地点";
    if (weather?.state === "unavailable") return "暂时无法读取天气实况";
    return "正在读取实况天气";
  }
  function paint() {
    if (!host) return;
    const now = input.now ?? Date.now(), settings = input.state?.settings || {};
    const zone = timezoneOf(input.state, input.life), location = locationOf(settings), time = clock(now, zone);
    if (input.autoScene) {
      const scene = sceneForTime(now, input.state, input.life);
      if (scene !== input.currentScene && typeof input.onSceneChange === "function"
        && input.onSceneChange(scene) !== false) input.currentScene = scene;
    }
    const alert = Array.isArray(weather?.alerts) ? weather.alerts[0] : null;
    const summary = weatherText(settings, location) + (alert ? ` · ${alert.event_name || alert.title}` : "");
    const condition = weather?.condition || "";
    const icon = alert ? "triangle-alert" : iconFor(condition);
    const canRefresh = settings.planning_weather_enabled === true && Boolean(location);
    const locationLabel = weather?.location || location || "角色地点未设置";
    const severity = ({minor:"蓝色 · 一般",moderate:"黄色 · 较重",severe:"橙色 · 严重",extreme:"红色 · 特别严重"})[alert?.severity] || alert?.severity || "级别待核对";
    const detail = alert
      ? `官方预警 · ${severity}${alert.ends_at ? ` · 至 ${new Date(alert.ends_at).toLocaleString("zh-CN")}` : ""}`
      : weatherDetail(settings, location);
    const timeISO = time.date.toISOString();
    host.innerHTML = `<div class="home-environment-icon" aria-hidden="true"><i data-lucide="${icon}"></i></div>
      <div class="home-environment-copy"><strong>${escapeHTML(locationLabel)} · ${escapeHTML(summary)}</strong>
        <small>${escapeHTML(detail)}</small></div>
      <div class="home-environment-clock"><time datetime="${timeISO}">${time.label}</time><small class="home-environment-date">${escapeHTML(time.dateLabel)}</small><small>${escapeHTML(zone)} · ${time.phase}</small></div>
      <button type="button" class="home-environment-refresh" data-home-weather-refresh aria-label="刷新天气" title="刷新天气"
        aria-busy="${loading}" ${!canRefresh || loading ? "disabled" : ""}><i data-lucide="refresh-cw"></i></button>`;
    window.lucide?.createIcons({nodes:host.querySelectorAll("[data-lucide]"),attrs:{"aria-hidden":"true"}});
  }
  async function fetchWeather(ticket, force = false) {
    loading = true;
    paint();
    try {
      if (typeof input.requestWeather !== "function") throw new Error("weather_unavailable");
      const result = await input.requestWeather(force);
      if (ticket !== sequence) return;
      weather = result && typeof result === "object" ? result : {state:"unavailable"};
      const serverTime = typeof weather.server_time === "string" ? Date.parse(weather.server_time) : NaN;
      if (Number.isFinite(serverTime)) serverOffset = serverTime - Date.now();
    } catch {
      if (ticket !== sequence) return;
      weather = weather?.state === "live" ? {...weather, state:"stale"} : {state:"unavailable"};
    } finally {
      if (ticket === sequence) { loading = false; paint(); }
    }
  }
  let serverOffset = 0;
  function scheduleClock() {
    clearTimeout(clockTimer);
    clockTimer = setTimeout(() => { paint(); scheduleClock(); }, 60000 - (Date.now() % 60000) + 30);
  }
  function render(root, next = {}) {
    if (!root) return;
    host = root;
    input = next;
    if (!host.dataset.weatherBound) {
      host.dataset.weatherBound = "true";
      host.addEventListener("click", event => {
        if (!event.target.closest("[data-home-weather-refresh]") || loading) return;
        const settings = input.state?.settings || {};
        if (settings.planning_weather_enabled !== true || !locationOf(settings)) return;
        sequence += 1;
        fetchWeather(sequence,true);
      });
    }
    const settings = input.state?.settings || {}, location = locationOf(settings);
    const zone = timezoneOf(input.state, input.life);
    const nextContext = JSON.stringify([input.scopeKey || "", zone, settings.planning_weather_enabled === true, location]);
    if (nextContext !== context) {
      context = nextContext;
      sequence += 1;
      loading = false;
      weather = null;
      if (input.state && settings.planning_weather_enabled === true && location) fetchWeather(sequence);
    }
    paint();
    if (clockTimer == null) scheduleClock();
  }
  return {render, sceneForTime};
})();
