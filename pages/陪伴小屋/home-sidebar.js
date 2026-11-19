/* 小屋右栏：只投影当前授权对象的状态、日历与生活确认。 */
"use strict";
window.CompanionHomeSidebar = (() => {
  const e = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const text = value => typeof value === "string" ? value.trim() : "";
  const stamp = value => typeof value === "string" && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;
  const kinds = {sleep:"睡眠休息", reading:"阅读", diary:"整理日记", dream:"梦境"};
  let schedulePastExpanded = false;
  if (typeof document !== "undefined") document.addEventListener("click", event => {
    const past = event.target?.closest?.("details[data-home-agenda-past] > summary");
    if (past) schedulePastExpanded = !past.parentElement.open;
  });
  function zoneOf({state, life}) {
    const zone = state?.settings?.planning_timezone || life?.config?.timezone || "Asia/Shanghai";
    try { new Intl.DateTimeFormat("zh-CN", {timeZone:zone}); return zone; }
    catch { return "Asia/Shanghai"; }
  }
  function format(value, zone, options) {
    if (value == null) return "";
    return new Intl.DateTimeFormat("zh-CN", {timeZone:zone, ...options}).format(new Date(value));
  }
  const day = (value, zone) => format(value, zone, {year:"numeric", month:"numeric", day:"numeric"});
  const clock = (value, zone) => format(value, zone, {hour:"2-digit", minute:"2-digit", hourCycle:"h23"});
  const dayMinutes = 24 * 60;
  function minuteOfDay(value, zone) {
    const at = typeof value === "number" ? value : stamp(value);
    if (at == null) return null;
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", {timeZone:zone, hour:"2-digit", minute:"2-digit", hourCycle:"h23"})
      .formatToParts(new Date(at)).filter(part => part.type !== "literal").map(part => [part.type, part.value]));
    return Number(parts.hour) * 60 + Number(parts.minute);
  }
  function dayMinute(value, now, zone) {
    const at = typeof value === "number" ? value : stamp(value);
    if (at == null) return null;
    const target = dayKey(at, zone), today = dayKey(now, zone);
    return target < today ? 0 : target > today ? dayMinutes : minuteOfDay(at, zone);
  }
  function minuteLabel(value) {
    if (value >= dayMinutes) return "24:00";
    const minutes = Math.max(0, Math.floor(value));
    return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
  }
  function durationLabel(value) {
    const minutes = Math.max(0, Math.floor(value));
    const hours = Math.floor(minutes / 60), remainder = minutes % 60;
    return hours ? `${hours}小时${remainder ? `${remainder}分` : ""}` : `${remainder}分钟`;
  }
  function overviewSegments(items, now, zone, complete) {
    const ranges = items.map(item => {
      const start = dayMinute(item.start, now, zone), end = dayMinute(item.end, now, zone);
      if (start == null || end == null || end <= start) return null;
      const tone = item.order === 0 || item.tone === "current" ? "current"
        : item.tone === "proposed" ? "proposed"
          : item.tone === "elapsed" || end <= minuteOfDay(now, zone) ? "elapsed" : "planned";
      return {start, end, tone};
    }).filter(Boolean);
    const boundaries = [...new Set([0, dayMinutes, ...ranges.flatMap(item => [item.start, item.end])])].sort((a,b)=>a-b);
    const segments = [];
    for (let index = 0; index < boundaries.length - 1; index += 1) {
      const start = boundaries[index], end = boundaries[index + 1];
      const covering = ranges.filter(item => item.start < end && item.end > start)
        .sort((a,b) => (a.tone === "current" ? 0 : 1) - (b.tone === "current" ? 0 : 1));
      const segment = covering.length ? {start, end, kind:"planned", tone:covering[0].tone}
        : {start, end, kind:complete ? "gap" : "unknown", tone:""};
      const previous = segments[segments.length - 1];
      if (previous && previous.end === start && previous.kind === segment.kind && previous.tone === segment.tone) previous.end = end;
      else segments.push(segment);
    }
    return segments;
  }
  function timelineRows(items, now, zone, complete) {
    // Keep the visible timeline chronological even when an API returns the
    // day's occurrences newest-first.  The current/pending event stays pinned
    // by its own timestamp instead of relying on caller order.
    items = items.slice().sort((a, b) => (a.start ?? Infinity) - (b.start ?? Infinity)
      || a.order - b.order || a.key.localeCompare(b.key));
    let cursor = minuteOfDay(now, zone), cursorKnown = true;
    const rows = [];
    for (const item of items) {
      const start = dayMinute(item.start, now, zone);
      if (complete && cursorKnown && start != null && start > cursor) {
        rows.push({type:"gap", start:cursor, end:start, current:rows.length === 0, next:item});
      }
      rows.push({type:"event", item});
      const end = dayMinute(item.end, now, zone);
      if (end != null) cursor = Math.max(cursor, end);
      else if (item.order === 0 && (item.start == null || stamp(item.start) <= now)) cursorKnown = false;
      else if (start != null) cursor = Math.max(cursor, start);
    }
    if (complete && cursorKnown && cursor < dayMinutes) {
      rows.push({type:"gap", start:cursor, end:dayMinutes, current:rows.length === 0});
    }
    return rows;
  }
  function renderOverview(items, now, zone, complete) {
    if (complete) return `<div class="home-agenda-overview" hidden></div>`;
    const elapsed = items.filter(item => item.tone === "elapsed" || item.end != null && item.end <= now
      && item.order !== 0).length;
    const current = items.filter(item => item.order === 0 || item.tone === "current").length;
    return `<div class="home-agenda-overview" role="status">
      <span class="home-agenda-overview-unknown" data-kind="unknown">未读范围</span>
      <p class="home-agenda-overview-note">日程数据不完整，空白暂不能判断为未安排。</p>
    </div>`;
  }
  function dayKey(value, zone) {
    const at = typeof value === "number" ? value : stamp(value);
    if (at == null) return "";
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {timeZone:zone, year:"numeric", month:"2-digit", day:"2-digit"})
      .formatToParts(new Date(at)).filter(part => part.type !== "literal").map(part => [part.type, part.value]));
    return `${parts.year}-${parts.month}-${parts.day}`;
  }
  const sameDay = (value, now, zone) => value != null && dayKey(value, zone) === dayKey(now, zone);
  function overlapsToday(start, end, now, zone) {
    const today = dayKey(now, zone);
    return start != null && end != null && end > start
      && dayKey(start, zone) <= today && dayKey(end - 1, zone) >= today;
  }
  function moment(value, now, zone) {
    return (day(value, zone) === day(now, zone) ? "" : format(value, zone, {month:"long", day:"numeric"}) + " ") + clock(value, zone);
  }
  function windowText(item, now, zone) {
    if (item.start == null) return "时间待定";
    const start = moment(item.start, now, zone);
    return item.end != null ? start + " – " + (day(item.start, zone) === day(item.end, zone) ? clock(item.end, zone) : moment(item.end, now, zone)) : start;
  }
  const activities = life => Array.isArray(life?.activities) ? life.activities : [];
  const sleepOf = ({state, life}) => state?.body_sleep || life?.body_sleep || state?.state?.sleep_execution;
  function vital(kind, raw, {sleeping=false, disabled=false, loading=false, unreadable=false}={}) {
    const icons = {energy:"battery",health:"heart-pulse",hunger:"utensils",sleep:"moon",mood:"heart",
      cycle:"calendar-heart",busy:"briefcase-business",location:"map-pin"};
    const item = (visual,icon,label,tone="quiet") => ({visual,icon,label,tone});
    if (disabled) return item("disabled",icons[kind],"未启用");
    if (kind === "energy" && typeof raw === "number") {
      if (raw >= 75) return item("energy-full","battery-full","充足","ok");
      if (raw >= 40) return item("energy-medium","battery-medium","尚可","ok");
      if (raw >= 20) return item("energy-low","battery-low","偏低","warn");
      return item("energy-depleted","battery-warning","较低","risk");
    }
    if (kind === "sleep" && sleeping) return item("sleeping","moon-star","睡眠中","rest");
    if (!raw) return item("unknown",icons[kind],loading ? "读取中" : unreadable ? "待读取" : "未记录");
    if (/^(未知|不清楚|未记录|暂无|尚未记录|未设置|待确认)$/.test(raw)) return item("unknown",icons[kind],"未知");
    if (kind === "health") {
      if (/^(稳定|正常|健康|良好|状态良好|无不适|没有不适|身体无不适)$/.test(raw)) return item("well","heart-pulse","良好","ok");
      if (/恢复中|康复中|正在恢复|逐渐恢复|正在好转|有所好转/.test(raw)) return item("recovering","heart-handshake","恢复中","ok");
      const symptoms = raw.replace(/(?:没有|暂无|并无|未见|没有出现|未出现|不再|无|没|不)(?:明显|任何)?(?:不适|疼痛|头痛|发热|发烧|感冒|虚弱|生病)/g, "");
      if (/不舒服|不适|生病|发烧|发热|疼痛|头痛|感冒|虚弱|不稳定|欠佳/.test(symptoms)) return item("strained","heart-crack","不适","warn");
      if (/稳定|正常|健康|良好|(?:没有|暂无|无)(?:明显|任何)?不适/.test(raw)) return item("well","heart-pulse","良好","ok");
    } else if (kind === "hunger") {
      if (/吃撑|太饱|过饱|饱胀|有点撑/.test(raw)) return item("full","utensils-crossed","偏饱","warn");
      if (/^(不饿|没有饥饿感|已吃饱|吃饱了|饱腹|饱足|已饱|适中)$/.test(raw)) return item("sated","utensils","不饿","ok");
      if (/没吃饱|未吃饱|有点饿|饥饿|很饿|饿了|^饿$|饥肠辘辘/.test(raw)) return item("hungry","soup","饿了","warn");
    } else if (kind === "sleep") {
      if (/^(清醒|精神|精神饱满|不困|没有困意|已醒|醒着)$/.test(raw)) return item("awake","sun","清醒","ok");
      if (/难以入睡|无法入睡|失眠|睡不着|辗转|睡眠不足/.test(raw)) return item("restless","cloud-moon","睡不好","warn");
      if (/困倦|犯困|想睡|有些困|很困|疲惫|^困$/.test(raw)) return item("sleepy","moon","困倦","warn");
      if (/休息|入睡|睡着|睡眠中/.test(raw)) return item("rest","moon","休息感受","rest");
    }
    // 自由文字不能可靠归类时保留原文，不推断成正常或异常。
    return item("recorded",icons[kind],raw);
  }
  function specialState(kind, raw, {timeline, sleeping=false}={}) {
    if(kind==="cycle")return timeline?.applicability==="applicable"&&timeline.phase==="menstrual";
    if(kind==="sleep"&&sleeping)return false;
    if(!raw||/(?:可能|或许|也许|不确定|担心|会不会)/.test(raw))return false;
    const negative={health:"不适|疼痛|头痛|发热|发烧|感冒|虚弱|生病",hunger:"饥饿感|饥饿|饿",sleep:"失眠|睡不着|睡眠不足|难以入睡",busy:"忙碌|忙"}[kind];
    const affirmative=negative?raw.replace(new RegExp(`(?:没有|暂无|并无|未见|不再|并不|并未|未|无|没|不)(?:觉得|感到|明显|任何|出现|什么)?(?:${negative})`,"g"),""):raw;
    if(kind==="busy")return /忙碌|忙着|忙于|在忙|正忙|很忙|有点忙|忙不过来|^忙$/.test(affirmative);
    const visual=vital(kind,affirmative).visual;
    return kind==="health"?visual==="strained":kind==="hunger"?visual==="hungry":kind==="sleep"?visual==="restless":false;
  }
  function renderVital(kind, label, raw, options) {
    const item = vital(kind, raw, options), detail = item.visual === "disabled" ? "该项维护未启用"
      : kind === "energy" && typeof raw === "number" ? `${raw} / 100` : raw || item.label;
    return `<span class="home-state-point" data-state-kind="${kind}" data-visual="${item.visual}" data-tone="${item.tone}"
      ${kind === "energy" && typeof raw === "number" ? `style="--home-state-level:${raw}%"` : ""}
      title="${e(label+" · "+detail)}"><span class="home-state-glyph" aria-hidden="true"><i data-lucide="${item.icon}"></i></span>
      <span class="home-state-label">${label}</span><strong>${e(item.label)}</strong>
      <span class="home-state-tooltip" role="tooltip"><b>${label} · ${e(item.label)}</b><span>${e(detail)}</span></span></span>`;
  }
  function renderState(input) {
    const {state, life, loading=false, unavailable=()=>"尚未读取"} = input;
    const now = input.now ?? Date.now(), zone = zoneOf(input);
    const runtimeMissing = input.unavailableCode?.("state") === "staging_not_initialized";
    const values = state?.state?.values || {}, settings = state?.settings || {};
    const sleeping = sleepOf(input)?.state === "sleeping" || activities(life).some(item=>item.kind === "sleep" && item.state === "active");
    const sleepPlan = state?.sleep_schedule || life?.sleep_schedule;
    const rhythmLabels = {awake:"清醒", sleepy:"逐渐困倦", winding_down:"准备休息", recovering:"醒来恢复"};
    const rhythm = sleepPlan?.rhythm;
    const energy = typeof values.energy === "number" && Number.isFinite(values.energy) && values.energy >= 0 && values.energy <= 100 ? values.energy : null;
    const unreadable = !state || state.state?.state === "unavailable";
    const cycleNames={menstrual:"经期",follicular:"卵泡期",pre_ovulation:"排卵前",ovulation:"排卵期",luteal:"黄体期",pms:"经前期",recovery:"恢复期"};
    const timeline=state?.state?.cycle_timeline;
    const cycle=timeline?.applicability==="not_applicable"?"不适用":timeline?.applicability==="applicable"
      ?`${cycleNames[timeline.phase]||"周期中"}${Number.isInteger(timeline.phase_day)?` · 第 ${timeline.phase_day} 天`:""}`:text(values.cycle);
    const mood=text(typeof values.mood==="string"?values.mood:values.mood?.description);
    const candidates=[["energy","能量",energy,settings.energy_enabled===false],["health","健康",text(values.health),settings.health_enabled===false],
      ["hunger","饥饿",text(values.hunger),settings.hunger_enabled===false],
      ["mood","心情",mood,settings.mood_enabled===false],
      ["sleep","睡眠",sleeping?"睡眠中 · 活动确认":text(values.sleep)||rhythmLabels[rhythm?.phase],settings.sleep_enabled===false],
      ["cycle","生理期",cycle,settings.cycle_enabled===false],["busy","忙碌",text(values.busy),settings.busy_enabled===false],
      ["location","位置",text(values.location),settings.location_enabled===false]];
    const visible=candidates.filter(([kind,,raw,disabled])=>settings["home_show_"+kind]!==false&&settings.enabled!==false&&!disabled
      &&(!settings["home_only_special_"+kind]||!unreadable&&specialState(kind,raw,{timeline,sleeping})));
    const updated = stamp(state?.state?.updated_at);
    const caption = [settings.enabled === false || state?.state?.state === "disabled" ? "自动维护未启用" : "",
      updated != null ? `更新于 ${moment(updated, now, zone)}` : ""].filter(Boolean).join(" · ");
    const states=visible.map(([kind,label,raw,disabled])=>renderVital(kind,label,raw,{sleeping,disabled,loading,unreadable})).join("");
    const fallback=loading?"正在读取角色状态…":settings.enabled===false?"状态维护未启用":visible.length?"":"暂无已启用的首页状态";
    const accessible=visible.map(([kind,label,raw,disabled])=>`${label} ${disabled?"未启用":kind==="energy"&&typeof raw==="number"?`${raw} / 100`:raw||"尚未记录"}`).join("；");
    return `<button type="button" class="home-state-board" data-go="life" data-life-target="state" aria-label="查看并更新角色状态。${e(accessible||fallback)}" title="查看并更新角色状态">
      ${states||`<span class="home-state-empty">${e(fallback)}</span>`}<span class="home-state-open" aria-hidden="true"><i data-lucide="arrow-up-right"></i></span></button>
      ${unreadable && !loading ? `<p class="home-side-warning" role="status">${e(runtimeMissing ? "角色数据尚未就绪，请核对账号与人格的角色映射；首次设置记录仍会保留。" : state ? "暂时无法读取角色状态，请在详情中核对。" : unavailable("state"))}</p>${runtimeMissing ? `<button class="text-button home-side-action" data-go="manage" data-manage="host">前往账号与会话</button>` : ""}` : ""}
      ${caption ? `<p class="home-side-caption">${e(caption)}</p>` : ""}`;
  }
  function agenda(input) {
    const {state, life} = input, now = input.now ?? Date.now(), zone = zoneOf(input), items = [], seen = new Set();
    function add(item) { if (!seen.has(item.key)) {seen.add(item.key); items.push(item);} }
    for (const item of state?.occurrences?.items || []) {
      const start = stamp(item.start), end = stamp(item.end);
      if (item.state === "cancelled" || !overlapsToday(start, end, now, zone)) continue;
      const kind = item.kind === "user_report" ? "你的安排" : item.kind === "commitment" ? "约定" : "角色计划";
      const phase = end <= now ? item.state === "proposed" ? "待确认 · 时段已过，完成情况未记录" : "时段已过 · 完成情况未记录"
        : item.state === "proposed" ? "待确认" : start <= now ? "当前计划时段" : "计划安排";
      const tone = end <= now ? "elapsed" : item.state === "proposed" ? "proposed" : start <= now ? "current" : "planned";
      add({key:`calendar:${item.id}:${item.sequence}`, title:text(item.title) || "未命名安排", start, end,
        detail:text(item.detail), label:`${kind} · ${phase}`, tone, order:1});
    }
    for (const item of activities(life)) {
      const started = stamp(item.started_at), updated = stamp(item.updated_at), due = stamp(item.due_at);
      const active = item.state === "active", completed = item.state === "completed", paused = item.state === "paused";
      if (item.state === "planned" && !sameDay(due ?? started, now, zone)) continue;
      if (paused && !sameDay(updated, now, zone)) continue;
      if (completed && !sameDay(updated, now, zone)) continue;
      if (!active && !completed && !paused && item.state !== "planned") continue;
      const planned = item.state === "planned";
      const late = planned && due != null && due <= now;
      add({key:`activity:${item.activity_id}`, title:text(item.title) || kinds[item.kind] || "个人活动",
        start:planned ? due ?? started : started ?? updated,
        end:completed || paused ? updated : active && due != null && due > now ? due : null,
        detail:text(item.checkpoint) || text(item.reflection),
        label:active ? "当前活动 · 进行中" : completed ? "已完成" : paused ? "已暂停"
          : late ? "计划时间已过 · 状态待更新" : due ? "待开始 · 计划时间" : "待开始",
        tone:active ? "active" : completed ? "completed" : paused ? "paused" : late ? "elapsed" : "planned", order:active ? 0 : 2});
    }
    const sleep = sleepOf(input), plan = state?.sleep_schedule || life?.sleep_schedule;
    if (sleep?.state === "sleeping" && !activities(life).some(item=>item.kind === "sleep" && item.state === "active")) {
      add({key:"body:sleep", title:"睡眠休息", start:stamp(sleep.started_at), end:stamp(sleep.planned_wake_at),
        label:"当前活动 · 睡眠中 · 活动确认", tone:"active", order:0});
    }
    const nextSleep = stamp(plan?.next_sleep_at);
    if (plan?.enabled && nextSleep != null && nextSleep >= now && sameDay(nextSleep, now, zone)) {
      add({key:"routine:sleep", title:"睡眠休息", start:nextSleep, end:stamp(plan.next_wake_at),
        label:plan.executor_state === "available" ? "自动睡眠计划" : "睡眠计划 · 等待授权窗口", tone:"planned", order:2});
    }
    const nextDiary = stamp(life?.next_diary_at);
    if (life?.config?.diary_enabled && nextDiary != null && nextDiary >= now && sameDay(nextDiary, now, zone)) {
      add({key:"routine:diary", title:"整理今日日记", start:nextDiary, end:null, label:"自动日记计划", tone:"planned", order:2});
    }
    return items.sort((a,b)=>(a.start ?? Infinity)-(b.start ?? Infinity) || a.order-b.order || a.key.localeCompare(b.key));
  }
  function renderSchedule(input) {
    const {state, life, loading=false, unavailable=()=>"尚未读取"} = input;
    const now = input.now ?? Date.now(), zone = zoneOf(input), items = agenda(input), messages = [];
    const calendarRead = Array.isArray(state?.occurrences?.items), lifeRead = Array.isArray(life?.activities);
    const stateRuntimeMissing = !calendarRead
      && input.unavailableCode?.("state") === "staging_not_initialized";
    const lifeRuntimeMissing = !lifeRead
      && input.unavailableCode?.("life") === "staging_not_initialized";
    const runtimeMissing = stateRuntimeMissing || lifeRuntimeMissing;
    const planning = state?.planning, nextCheck = stamp(planning?.next_check);
    const dailyResult = planning?.daily_result;
    const planningIncomplete = planning?.enabled && planning.calendar_enabled
      && (planning.running || ["failed","expired","dead_letter","cancelled","uncertain","blocked","deferred"].includes(dailyResult?.state));
    const planningFailure = planningIncomplete && !planning.running;
    const planningFailureLabel = ({provider_wait_timed_out:"模型等待超时",timeout:"执行超时",
      model_usage_unknown:"模型输出不完整（可能达到长度上限）",response_contract_rejected:"模型返回格式不符合日程要求",
      native_response_parse_failed:"模型返回无法解析",planning_output_invalid:"模型返回的日程无效",worker_error:"生成失败"})[dailyResult?.code]
      || (dailyResult?.code ? `生成未完成（${dailyResult.code}）` : "生成未完成");
    const nextReview = stamp(planning?.next_review_at);
    const planningNote = planning?.enabled
      ? !planning.calendar_enabled ? "日历写入尚未开启，自动规划暂停"
        : planning.running ? "自动规划正在检查日程"
          : planningFailure ? `当日规划未完成：${planningFailureLabel} · 发送「重新生成日程」可立即重试${nextReview != null ? ` · ${moment(nextReview, now, zone)} 重新评估剩余时段` : ""}`
          : nextCheck != null ? `自动规划已开启 · 调度巡检 ${moment(nextCheck, now, zone)}`
            : "自动规划已开启 · 暂无近期计划"
      : "";
    if (!loading) {
      const missingRuntimeSections = [stateRuntimeMissing ? "日历" : "", lifeRuntimeMissing ? "活动" : ""].filter(Boolean);
      if (missingRuntimeSections.length) messages.push(`${missingRuntimeSections.join("与")}暂不可读取：角色数据尚未就绪，请核对账号与人格的角色映射；首次设置状态不会因此改变。`);
      if (!calendarRead && !stateRuntimeMissing) messages.push(`日历：${state ? "安排尚未读取" : unavailable("state")}`);
      if (!lifeRead && !lifeRuntimeMissing) messages.push(`活动：${unavailable("life")}`);
      const coverage = state?.occurrences;
      if (coverage?.skipped) {
        const reasons = coverage.skipped_reasons || {};
        const labels = [];
        if (reasons.role_mismatch) labels.push(`${reasons.role_mismatch} 条来源角色不匹配`);
        if (reasons.source_unavailable) labels.push(`${reasons.source_unavailable} 条来源失效或不可验证`);
        messages.push(`${coverage.skipped} 条日历记录已跳过${labels.length ? `（${labels.join("，")}）` : "（来源不可用）"}`);
      }
      if (coverage?.scan_truncated) messages.push("日历记录达到读取上限，仍有记录未检查");
      if (coverage?.occurrences_truncated) messages.push(`${coverage.occurrences_truncated} 个日历期次超过读取上限，未显示`);
      if (coverage?.partial && !coverage?.skipped && !coverage?.scan_truncated && !coverage?.occurrences_truncated) {
        messages.push("部分日历安排未完整读取");
      }
    }
    const emptyTitle = loading ? "正在读取日程…" : calendarRead && lifeRead
      ? state?.occurrences?.partial ? "日程未完整读取" : planningIncomplete ? planning.running ? "今日规划正在生成" : "今日规划尚未生成" : "今日暂无安排"
      : "日程暂不可读取";
    const row = (item, current=false) => `<li class="home-agenda-item" data-tone="${item.tone}"${current ? ` data-current="true" aria-current="time"` : ""}><span class="home-agenda-dot" aria-hidden="true"></span><div class="home-agenda-body">
      <time${item.start == null ? "" : ` datetime="${new Date(item.start).toISOString()}"`}>${e(windowText(item, now, zone))}</time><strong>${e(item.title)}</strong><small>${e(item.label)}</small>
      ${item.detail ? `<small class="home-agenda-detail">${e(item.detail)}</small>` : ""}</div></li>`;
    const active = items.filter(item=>item.order === 0);
    const current = items.filter(item=>item.tone === "current");
    const elapsed = items.filter(item=>item.tone === "elapsed" || item.end != null && item.end <= now && item.order !== 0)
      .sort((a,b)=>(b.end ?? b.start ?? -Infinity)-(a.end ?? a.start ?? -Infinity));
    const upcoming = items.filter(item=>!active.includes(item) && !current.includes(item) && !elapsed.includes(item));
    const live = [...active, ...current, ...upcoming]
      .sort((a,b)=>(a.start ?? Infinity)-(b.start ?? Infinity) || a.order-b.order || a.key.localeCompare(b.key));
    const coverageComplete = calendarRead && lifeRead && !state?.occurrences?.partial
      && !state?.occurrences?.skipped && !state?.occurrences?.scan_truncated
      && !state?.occurrences?.occurrences_truncated && !planningIncomplete;
    const rows = timelineRows(live, now, zone, coverageComplete);
    const currentGap = rows.find(item => item.type === "gap" && item.current);
    const clockLabel = loading ? "正在读取当前活动…" : lifeRead
      ? active.length ? "有活动正在进行" : "当前没有进行中的活动"
      : "当前活动尚未读取";
    const nowLine = !live.length && elapsed.length
      ? `<p class="home-agenda-now is-empty">今日安排已结束</p>`
      : currentGap
      ? `<div class="home-agenda-now"><span>现在空档</span><time>${currentGap.next?.start != null ? `下一项 ${e(clock(currentGap.next.start, zone))}` : "当天暂无后续安排"}</time></div>`
      : `<p class="home-agenda-now${live.length ? "" : " is-empty"}">${clockLabel}</p>`;
    // The current gap is already the compact "now" strip; do not repeat it as a
    // large dashed card inside the timeline.
    const visibleRows = rows.filter(item => item.type !== "gap" || (!item.current && item.end - item.start >= 45));
    const timeline = visibleRows.length ? visibleRows.map(item => item.type === "event"
      ? row(item.item, item.item.tone === "current" || item.item.order === 0)
      : `<li class="home-agenda-gap"${item.current ? ` data-current="true" aria-current="time"` : ""}><span class="home-agenda-gap-mark" aria-hidden="true"></span><div class="home-agenda-gap-body"><time>${minuteLabel(item.start)} – ${minuteLabel(item.end)} · ${e(durationLabel(item.end-item.start))}</time><strong>${item.current ? (items.length ? "当前未安排" : "今日暂无安排") : "未安排时段"}</strong><small>${item.next ? `这段时间没有已记录安排 · 下一项 ${e(clock(item.next.start, zone))}` : "当天暂无后续安排"}</small></div></li>`).join("")
      : `<li class="home-agenda-item"><span class="home-agenda-dot" aria-hidden="true"></span><div class="home-agenda-body"><strong>${emptyTitle}</strong></div></li>`;
    // One summary, one "up next" heading, one collapsed history.  The previous
    // header repeated the finished count three times and the act counter started
    // at 02 because it included hidden elapsed items.
    const summary = `<div class="home-agenda-summary"><span><b>${items.length}</b> 项安排</span>${elapsed.length ? `<span>已结束 ${elapsed.length}</span>` : ""}<span class="is-accent">接下来 ${live.length}</span></div>`;
    const programHead = `<div class="home-agenda-program-head"><h3>接下来</h3><span>${live.length} 项</span></div>`;
    return `${summary}
      ${nowLine}
      ${calendarRead && lifeRead ? renderOverview(items, now, zone, coverageComplete) : ""}
      ${programHead}
      <div class="home-agenda-scroll" tabindex="0" aria-label="当天日程时间线"><ol class="home-agenda" style="counter-reset:agenda-act 0" aria-label="今日安排">${timeline}</ol></div>
      ${elapsed.length ? `<details class="home-agenda-more" data-home-agenda-past ${schedulePastExpanded ? "open" : ""}><summary>${schedulePastExpanded ? "收起" : "查看"}已结束 ${elapsed.length} 项</summary><ol class="home-agenda">${elapsed.map(row).join("")}</ol></details>` : ""}
      ${messages.length ? `<p class="home-side-warning" role="status">${e(messages.join("；"))}</p>` : ""}
      ${runtimeMissing ? `<button class="text-button home-side-action" data-go="manage" data-manage="host">前往账号与会话</button>` : ""}
      <p class="home-side-caption">${planningNote ? `${e(planningNote)} · ` : ""}时间按 ${e(zone)} 显示</p>`;
  }
  return {renderState, renderSchedule};
})();
