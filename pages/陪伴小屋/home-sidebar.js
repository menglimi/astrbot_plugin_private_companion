/* 小屋右栏：只投影当前授权对象的状态、日历与生活确认。 */
"use strict";
window.CompanionHomeSidebar = (() => {
  const e = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const text = value => typeof value === "string" ? value.trim() : "";
  const stamp = value => typeof value === "string" && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;
  const kinds = {sleep:"睡眠休息", reading:"阅读", diary:"整理日记", dream:"梦境"};
  let schedulePastExpanded = false;
  let scheduleListOpen = true;
  const expandedDetailKeys = new Set();
  const DETAIL_CLAMP_CHARS = 44;

  function updateScrollFade(scroller) {
    if (!scroller || !scroller.dataset) return;
    const scrollable = (scroller.scrollHeight - scroller.clientHeight) > 4;
    scroller.dataset.fadeTop = scrollable && scroller.scrollTop > 2 ? "true" : "false";
    scroller.dataset.fadeBottom = scrollable
      && (scroller.scrollTop + scroller.clientHeight < scroller.scrollHeight - 2) ? "true" : "false";
  }

  function syncScrollFade(root) {
    const scope = root && typeof root.querySelectorAll === "function" ? root
      : (typeof document !== "undefined" && typeof document.querySelectorAll === "function" ? document : null);
    if (!scope) return;
    const run = () => {
      for (const scroller of scope.querySelectorAll(".home-agenda-scroll")) updateScrollFade(scroller);
    };
    run();
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(run);
  }

  if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
    document.addEventListener("click", event => {
      const past = event.target?.closest?.("details[data-home-agenda-past] > summary");
      if (past) {
        schedulePastExpanded = !past.parentElement.open;
        return;
      }
      const toggle = event.target?.closest?.("button[data-agenda-detail-key]");
      if (!toggle) return;
      const key = toggle.dataset?.agendaDetailKey;
      if (!key) return;
      const expanded = !expandedDetailKeys.has(key);
      if (expanded) expandedDetailKeys.add(key);
      else expandedDetailKeys.delete(key);
      const detail = toggle.previousElementSibling;
      if (detail?.classList?.contains?.("home-agenda-row-detail")) {
        detail.classList.toggle("is-clamped", !expanded);
      }
      toggle.setAttribute?.("aria-expanded", String(expanded));
      toggle.textContent = expanded ? "收起详情" : "展开全文";
      const scroller = toggle.closest?.(".home-agenda-scroll");
      if (scroller) updateScrollFade(scroller);
    });
    document.addEventListener("toggle", event => {
      const details = event.target;
      if (!details?.classList?.contains?.("home-agenda-list")) return;
      scheduleListOpen = Boolean(details.open);
      syncScrollFade(details);
    }, true);
    document.addEventListener("scroll", event => {
      const scroller = event.target;
      if (scroller?.classList?.contains?.("home-agenda-scroll")) updateScrollFade(scroller);
    }, {capture: true, passive: true});
  }
  if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
    window.addEventListener("resize", () => syncScrollFade());
  }
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
  const CLAUSE_SPLIT = /[，。；,;!！?？\n]+|(?=(?:但是|但|不过|可是|只是|就是|却|然而|反而|反倒|倒是))/;
  // 固定表达与“不+正向词”习语中的“不/没/无”不构成外层否定作用域（模糊表达单独由 UNCERTAIN_NEGATOR 处理）。
  const NON_NEGATOR_PHRASE = /不得不|忍不住|禁不住|按捺不住|免不了|少不了|难免|不由得|不由自主|不知不觉|不自觉|不仅|不但|不只|不光|不止|不停|不断|不绝|时不时|不时|不小心|不慎|不知怎么|不知道为什么|不知道为何|不管|不论|无论|没想到|没料到|没完没了|无精打采|无力|无奈|无端|无故|说不上来|说不上|说不出来|说不出|讲不清|分不清|不是很舒服|不怎么舒服|不太舒服|不大舒服|不甚舒服|不舒服|不舒适|不太好受|不好受|不太舒坦|不舒坦|不太稳定|不够稳定|不稳定/g;
  // 强存在/感知/判断/终止否定词：既可否定普通症状，也可对“不舒服/不太舒服/没吃饱”构成双重否定。
  const STRONG_NEGATOR = /(?:完全没有|一点也没|一点都没|丝毫没有|半点没有|压根没有|根本没有|没有出现|没有觉得|没有感到|并没有|并未曾|并未觉得|并未感到|未曾|并无|暂无|全无|皆无|未见|尚未|没觉得|没感到|没有|没什么|没啥|没到|还没|不觉得|不感到|觉察不到|感受不到|感觉不到|挑不出|找不到|谈不上|算不上|不至于|不算|不再|并非|不是|并没|无|没)/g;
  // 广义前置否定词：用于非“不/没”前缀的普通症状词（如“已经不痛了”“完全不困”“不怎么忙”）。
  const GENERAL_NEGATOR = /(?:完全没有|一点也没|一点都没|丝毫没有|半点没有|压根没有|根本没有|没有出现|没有觉得|没有感到|并没有|并未曾|并未觉得|并未感到|未曾|完全不|一点也不|一点都不|丝毫不|半点不|压根不|根本不|并不|并未|并无|暂无|全无|皆无|未见|尚未|没觉得|没感到|没有|没什么|没啥|没到|还没|不觉得|不感到|觉察不到|感受不到|感觉不到|挑不出|找不到|谈不上|算不上|不至于|不算|不再|不怎么|不太|不大|不甚|并非|不是|并没|无|没|不)/g;
  // 模糊/难以指认句式（如“说不上哪里不适”）：抑制误判为正向不适，但不强行断言为“良好”。
  const UNCERTAIN_NEGATOR = /(?:说不上来|说不上|说不出来|说不出|讲不清|分不清)/g;
  // 同一分句内终止否定作用域的边界：因果转折、持续强调或已消耗否定词的独立动作谓语。
  const NEG_SCOPE_BREAK = /(?:导致|引起|弄得|搞得|害得|结果|所以|因此|于是|反而|反倒|偏偏|还是|依然|依旧|仍旧|仍然|却|但|不过|可是|只是|就是|然而|倒是|还(?:挺|很|有点|有些|略|偏)|睡好|睡足|睡够|吃饭|吃早饭|吃午饭|吃晚饭|吃东西|吃饱|休息好|喝水|盖好|穿暖|穿够)/;
  const NON_CESSATION_SUFFIX = /^(?:感|意|症状|之感|的感觉|状态)?(?:一点|半点|丝毫)?(?:也|都)?(?:还|仍|仍然|依然|依旧|始终|一直|迟迟|并)?(?:没有|没|未|并未|尚未|不曾|不太)(?:消退|缓解|减轻|好转|散去|退去|退|痊愈|消失|停|止|好)/;
  const POST_NEG_SUFFIX = /^(?:感|意|症状|之感|的地方|的感觉|的迹象|的苗头)?(?:也|都|均|统统|全都|全|一概)?(?:没有|并无|全无|皆无|无)(?=$|[了啦呢嘛啊呀的])/;
  const CESSATION_SUFFIX = /^(?:感|意|症状|之感|的感觉)?(?:已经|早已|早就|基本|彻底|完全|终于|都|全都)?(?:消退|散去|退去|退了|痊愈|消失|好了|止住了|没了|没有了|不再有)(?:了|啦|呢|嘛)?(?=$|[，。；,;!！?？\s了啦呢嘛啊呀的])/;
  const COMPARATIVE_MITIGATION = /(?:之前|刚才|先前|昨天|昨晚|原先|原本|平时|以前).{0,4}(?:那么|那样|这么|这样)/;
  const NEG_IDIOM_HEAD = /^(?:不是很舒服|不怎么舒服|不太舒服|不大舒服|不甚舒服|不舒服|不舒适|不太好受|不好受|不太舒坦|不舒坦|不太稳定|不够稳定|不稳定|没有吃饱|没怎么吃饱|没太吃饱|没吃饱|未吃饱|没有睡好|没怎么睡好|没睡好|难以入睡|无法入睡)/;
  const NEG_SCOPE_MAX_CHARS = 24;
  const UNCERTAIN_SCOPE_MAX_CHARS = 16;
  const HEALTH_STRAINED_POS = "不是很舒服|不怎么舒服|不太舒服|不大舒服|不甚舒服|不舒服|不舒适|不太好受|不好受|不太舒坦|不舒坦|不太稳定|不够稳定|不稳定|不适|生病|发烧|低烧|高烧|发热|感冒|着凉|受凉|剧烈疼痛|疼痛|偏头痛|头痛|头疼|病痛|酸痛|胃痛|胃疼|腹痛|肚子痛|肚子疼|痛经|咽痛|喉咙痛|喉咙疼|嗓子痛|嗓子疼|牙痛|牙疼|腰痛|腰疼|背痛|背疼|胸痛|关节痛|隐痛|胀痛|刺痛|闷痛|钝痛|剧痛|作痛|阵痛|发痛|发疼|生疼|疼得|痛得|很痛|很疼|好痛|好疼|有点痛|有点疼|有些痛|有些疼|挺痛|挺疼|太痛|太疼|略痛|略疼|微痛|微疼|难受|头晕|眩晕|恶心|反胃|咳嗽|发冷|畏寒|腹泻|拉肚子|抽筋|虚弱|乏力|欠佳";
  const HUNGER_POS_CORE = "没有吃饱|没怎么吃饱|没太吃饱|没吃饱|未吃饱|吃不饱|有点饿|有些饿|微饿|很饿|好饿|挺饿|太饿|发饿|肚子饿|饿得|饿了|饥饿感|饥饿|饥肠辘辘";
  const SLEEPY_POS_CORE = "困倦感|困倦|困意|犯困|想睡|好困|有些困|有点困|很困|太困|挺困|困乏|疲惫";
  const BUSY_POS_CORE = "忙碌|忙着|忙于|在忙|正忙|很忙|有点忙|有些忙|挺忙|太忙|忙不过来|忙得|忙活";
  const STATUS_PATTERNS = {
    healthRecovering: "恢复中|康复中|正在恢复|逐渐恢复|慢慢恢复|正在好转|有所好转|逐渐好转|慢慢好转|正在缓解|逐渐缓解|慢慢缓解",
    healthStrainedPos: HEALTH_STRAINED_POS,
    healthStrainedNeg: `${HEALTH_STRAINED_POS}|痛(?!(?:快|恨|惜|哭|饮|骂|斥|击|打|改|定))|疼(?!(?:爱|惜))`,
    healthWell: "稳定|正常|健康|良好|舒展|舒适|舒服|松快|轻快|安稳|稳当|神清气爽|无恙|健朗|退烧|痊愈",
    hungerFull: "吃撑|太饱|过饱|饱胀|有点撑|撑得",
    hungerPos: `${HUNGER_POS_CORE}|^饿$`,
    hungerNeg: `${HUNGER_POS_CORE}|饿意|饿`,
    hungerSated: "已吃饱|吃饱了|饱腹|饱足|已饱",
    sleepRestless: "难以入睡|无法入睡|失眠|睡不着|辗转|睡眠不足|没有睡好|没怎么睡好|没睡好|睡不好",
    sleepyPos: `${SLEEPY_POS_CORE}|^困$`,
    sleepyNeg: `${SLEEPY_POS_CORE}|困(?![难惑扰境局顿])`,
    sleepRest: "休息|入睡|睡着|睡眠中",
    sleepAwake: "清醒|精神饱满|已醒|醒着",
    busyPos: `${BUSY_POS_CORE}|^忙$`,
    busyNeg: `${BUSY_POS_CORE}|(?<!帮|急|匆|慌|连)忙`,
  };
  function collectMatches(regex, source) {
    regex.lastIndex = 0;
    const list = [];
    let m;
    while ((m = regex.exec(source)) !== null) {
      list.push({start: m.index, end: m.index + m[0].length, text: m[0]});
      if (m[0].length === 0) regex.lastIndex += 1;
    }
    return list;
  }
  function findGoverningNegator(negators, clause, targetStart, maxChars) {
    for (let i = negators.length - 1; i >= 0; i -= 1) {
      const neg = negators[i];
      if (neg.end > targetStart) continue;
      const gap = clause.slice(neg.end, targetStart);
      if (gap.length > maxChars) break;
      if (NEG_SCOPE_BREAK.test(gap)) return null;
      return {neg, gap};
    }
    return null;
  }
  function analyzeNegation(raw, posSource, negSource = posSource) {
    const value = text(raw);
    if (!value) return {hasPositive:false, hasNegated:false, hasUncertain:false};
    const posPattern = new RegExp(posSource);
    const tokenPattern = new RegExp(negSource, "g");
    let hasPositive = false, hasNegated = false, hasUncertain = false;
    for (const part of value.split(CLAUSE_SPLIT)) {
      const clause = part.trim();
      if (!clause) continue;
      const masked = clause.replace(NON_NEGATOR_PHRASE, match => " ".repeat(match.length));
      const strongNegs = collectMatches(STRONG_NEGATOR, masked);
      const generalNegs = collectMatches(GENERAL_NEGATOR, masked);
      const uncertainNegs = collectMatches(UNCERTAIN_NEGATOR, clause);
      const tokens = collectMatches(tokenPattern, clause);
      for (const tok of tokens) {
        const suffix = clause.slice(tok.end);
        const isExactClause = tok.start === 0 && tok.end === clause.length;
        const matchesPos = posPattern.test(isExactClause ? tok.text : ` ${tok.text} `);
        if (NON_CESSATION_SUFFIX.test(suffix)) {
          if (matchesPos) hasPositive = true;
          continue;
        }
        if (POST_NEG_SUFFIX.test(suffix) || CESSATION_SUFFIX.test(suffix)) {
          hasNegated = true;
          continue;
        }
        const negPool = NEG_IDIOM_HEAD.test(tok.text) ? strongNegs : generalNegs;
        const governed = findGoverningNegator(negPool, clause, tok.start, NEG_SCOPE_MAX_CHARS);
        if (governed) {
          if (COMPARATIVE_MITIGATION.test(governed.gap)) hasUncertain = true;
          else hasNegated = true;
          continue;
        }
        if (findGoverningNegator(uncertainNegs, clause, tok.start, UNCERTAIN_SCOPE_MAX_CHARS)) {
          hasUncertain = true;
          continue;
        }
        if (matchesPos) hasPositive = true;
      }
    }
    return {hasPositive, hasNegated, hasUncertain};
  }
  const STRUCTURED_VITALS = {
    health: {
      well: ["well","heart-pulse","良好","ok"],
      recovering: ["recovering","heart-handshake","恢复中","ok"],
      strained: ["strained","heart-crack","不适","warn"],
    },
    hunger: {
      sated: ["sated","utensils","不饿","ok"],
      hungry: ["hungry","soup","饿了","warn"],
      full: ["full","utensils-crossed","偏饱","warn"],
    },
    sleep: {
      awake: ["awake","sun","清醒","ok"],
      sleepy: ["sleepy","moon","困倦","warn"],
      restless: ["restless","cloud-moon","睡不好","warn"],
      rest: ["rest","moon","休息感受","rest"],
    },
  };
  function vital(kind, raw, {sleeping=false, disabled=false, loading=false, unreadable=false, level=null}={}) {
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
    if (loading && (raw == null || raw === "")) return item("loading",icons[kind],"读取中","loading");
    if (!raw) return item("unknown",icons[kind],unreadable ? "待读取" : "未记录");
    if (/^(未知|不清楚|未记录|暂无|尚未记录|未设置|待确认)$/.test(raw)) return item("unknown",icons[kind],"未知");
    // 第一层：优先采用后端状态维护 LLM 同步产出的结构化定性枚举，避免对文学化措辞二次猜测。
    const structuredKey = text(level).toLowerCase();
    const structured = structuredKey && STRUCTURED_VITALS[kind]?.[structuredKey];
    if (structured) return item(...structured);
    // 第二层：旧记录或缺省枚举时，回退至分句级中文否定作用域分析器精准兜底。
    if (kind === "health") {
      if (/^(稳定|正常|健康|良好|状态良好|无不适|没有不适|身体无不适)$/.test(raw)) return item("well","heart-pulse","良好","ok");
      if (analyzeNegation(raw, STATUS_PATTERNS.healthRecovering).hasPositive) return item("recovering","heart-handshake","恢复中","ok");
      const strained = analyzeNegation(raw, STATUS_PATTERNS.healthStrainedPos, STATUS_PATTERNS.healthStrainedNeg);
      if (strained.hasPositive) return item("strained","heart-crack","不适","warn");
      if (strained.hasNegated || analyzeNegation(raw, STATUS_PATTERNS.healthWell).hasPositive) return item("well","heart-pulse","良好","ok");
    } else if (kind === "hunger") {
      if (analyzeNegation(raw, STATUS_PATTERNS.hungerFull).hasPositive) return item("full","utensils-crossed","偏饱","warn");
      if (/^(不饿|没有饥饿感|已吃饱|吃饱了|饱腹|饱足|已饱|适中)$/.test(raw)) return item("sated","utensils","不饿","ok");
      const hungry = analyzeNegation(raw, STATUS_PATTERNS.hungerPos, STATUS_PATTERNS.hungerNeg);
      if (hungry.hasPositive) return item("hungry","soup","饿了","warn");
      if (hungry.hasNegated || analyzeNegation(raw, STATUS_PATTERNS.hungerSated).hasPositive) return item("sated","utensils","不饿","ok");
    } else if (kind === "sleep") {
      if (/^(清醒|精神|精神饱满|不困|没有困意|已醒|醒着)$/.test(raw)) return item("awake","sun","清醒","ok");
      if (analyzeNegation(raw, STATUS_PATTERNS.sleepRestless).hasPositive) return item("restless","cloud-moon","睡不好","warn");
      const sleepy = analyzeNegation(raw, STATUS_PATTERNS.sleepyPos, STATUS_PATTERNS.sleepyNeg);
      if (sleepy.hasPositive) return item("sleepy","moon","困倦","warn");
      if (analyzeNegation(raw, STATUS_PATTERNS.sleepRest).hasPositive) return item("rest","moon","休息感受","rest");
      if (sleepy.hasNegated || analyzeNegation(raw, STATUS_PATTERNS.sleepAwake).hasPositive) return item("awake","sun","清醒","ok");
    }
    // 自由文字不能可靠归类时保留原文，不推断成正常或异常。
    return item("recorded",icons[kind],raw);
  }
  function specialState(kind, raw, {timeline, sleeping=false, level=null}={}) {
    if (kind === "cycle") return timeline?.applicability === "applicable" && timeline.phase === "menstrual";
    if (kind === "sleep" && sleeping) return false;
    if (!raw) return false;
    const structuredKey = text(level).toLowerCase();
    if (kind === "busy" && (structuredKey === "busy" || structuredKey === "free")) return structuredKey === "busy";
    if (structuredKey && STRUCTURED_VITALS[kind]?.[structuredKey]) {
      const visual = STRUCTURED_VITALS[kind][structuredKey][0];
      return kind === "health" ? visual === "strained" : kind === "hunger" ? visual === "hungry" : kind === "sleep" ? visual === "restless" : false;
    }
    if (/(?:可能|或许|也许|不确定|担心|会不会)/.test(raw)) return false;
    if (kind === "busy") return analyzeNegation(raw, STATUS_PATTERNS.busyPos, STATUS_PATTERNS.busyNeg).hasPositive;
    const visual = vital(kind, raw).visual;
    return kind === "health" ? visual === "strained" : kind === "hunger" ? visual === "hungry" : kind === "sleep" ? visual === "restless" : false;
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
  function renderStateRow(kind, label, raw, options, wide = false) {
    const item = vital(kind, raw, options);
    const detail = item.visual === "disabled" ? "该项维护未启用"
      : kind === "energy" && typeof raw === "number" ? `${raw} / 100` : raw || item.label;
    return `<span class="home-state-row${wide ? " is-wide" : ""}" data-state-kind="${kind}" data-visual="${item.visual}" data-tone="${item.tone}"${kind === "energy" && typeof raw === "number" ? ` style="--home-state-level:${raw}%"` : ""} title="${e(label + " · " + detail)}">
      <span class="home-state-row-icon" aria-hidden="true"><i data-lucide="${item.icon}"></i></span>
      <span class="home-state-row-copy"><span class="home-state-row-label">${label}</span><strong>${e(item.label)}</strong></span>
    </span>`;
  }
  /* 状态时间移到标题旁后，用相对时间读起来比“18:42”更直接；
     15 秒一次的首页时钟会顺手刷新它，不必整块重绘。 */
  function stateAge(input) {
    const updated = stamp(input?.state?.state?.updated_at);
    if (updated == null) return "";
    const now = input.now ?? Date.now();
    const minutes = Math.max(0, Math.floor((now - updated) / 60000));
    if (minutes < 1) return "刚刚";
    if (minutes < 60) return `${minutes} 分钟前`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} 小时前`;
    return `${Math.floor(hours / 24)} 天前`;
  }
  function renderState(input) {
    const {state, life, loading=false, unavailable=()=>"尚未读取"} = input;
    const now = input.now ?? Date.now(), zone = zoneOf(input);
    const runtimeMissing = input.unavailableCode?.("state") === "staging_not_initialized";
    const values = state?.state?.values || {}, settings = state?.settings || {};
    const sleeping = sleepOf(input)?.state === "sleeping" || activities(life).some(item=>item.kind === "sleep" && item.state === "active");
    const sleepPlan = state?.sleep_schedule || life?.sleep_schedule;
    const rhythmLabels = {awake:"清醒",sleepy:"逐渐困倦",winding_down:"准备休息",recovering:"醒来恢复"};
    const rhythm = sleepPlan?.rhythm;
    const energy = typeof values.energy === "number" && Number.isFinite(values.energy) && values.energy >= 0 && values.energy <= 100 ? values.energy : null;
    const unreadable = !state || state.state?.state === "unavailable";
    const cycleNames = {menstrual:"经期",follicular:"卵泡期",pre_ovulation:"排卵前",ovulation:"排卵期",luteal:"黄体期",pms:"经前期",recovery:"恢复期"};
    const timeline = state?.state?.cycle_timeline;
    const cycle = timeline?.applicability === "not_applicable" ? "不适用" : timeline?.applicability === "applicable"
      ? `${cycleNames[timeline.phase] || "周期中"}${Number.isInteger(timeline.phase_day) ? ` · 第 ${timeline.phase_day} 天` : ""}` : text(values.cycle);
    const mood = text(typeof values.mood === "string" ? values.mood : values.mood?.description);
    const candidates = [["energy","精力",energy,settings.energy_enabled===false],["health","健康",text(values.health),settings.health_enabled===false],
      ["hunger","饥饿",text(values.hunger),settings.hunger_enabled===false],
      ["mood","心情",mood,settings.mood_enabled===false],
      ["sleep","睡眠",sleeping ? "睡眠中 · 活动确认" : text(values.sleep) || rhythmLabels[rhythm?.phase],settings.sleep_enabled===false],
      ["cycle","生理期",cycle,settings.cycle_enabled===false],["busy","忙碌",text(values.busy),settings.busy_enabled===false],
      ["location","位置",text(values.location),settings.location_enabled===false]];
    const levels = {
      health: text(values.health_level),
      hunger: text(values.hunger_level),
      sleep: text(values.sleep_level),
      busy: text(values.busy_level),
    };
    const visible = candidates.filter(([kind,,raw,disabled]) => settings["home_show_"+kind] !== false && settings.enabled !== false && !disabled
      && (!settings["home_only_special_"+kind] || !unreadable && specialState(kind,raw,{timeline,sleeping,level:levels[kind]})));
    const sleepEntry = visible.find(([kind]) => kind === "sleep");
    const rows = visible.filter(([kind]) => kind !== "sleep").map(row => {
      const options = {sleeping, disabled:row[3], loading, unreadable, level:levels[row[0]]};
      const item = vital(row[0], row[2], options);
      const compactLimit = row[0] === "cycle" ? 13 : row[0] === "location" ? 10 : 6;
      return {row, options, wide: ["mood","busy"].includes(row[0]) || item.label.length > compactLimit};
    });
    const shortRows = rows.filter(item => !item.wide);
    if (shortRows.length % 2) shortRows[shortRows.length - 1].wide = true;
    const laidOut = [...rows.filter(item => !item.wide), ...rows.filter(item => item.wide)];
    const states = laidOut.map(({row, options, wide}) => renderStateRow(row[0],row[1],row[2],options,wide)).join("");
    const sleepRaw = sleepEntry?.[2] || "";
    const sleepStart = stamp(sleepOf(input)?.started_at);
    const sleepMinutes = sleeping && sleepStart != null ? Math.max(0, (now - sleepStart) / 60000) : null;
    const sleepDuration = sleepMinutes == null ? "" : durationLabel(sleepMinutes).replace(/(\d)(小时|分)/g, "$1 $2");
    const heroLoading = loading && unreadable && !sleeping && !sleepRaw;
    const heroTitle = heroLoading ? "正在读取" : sleeping ? `睡眠中${sleepDuration ? ` · 已睡 ${sleepDuration}` : ""}` : sleepRaw && !/^清醒$/.test(sleepRaw) ? sleepRaw : "清醒";
    const fallback = loading && unreadable ? "正在读取角色状态…" : settings.enabled === false ? "状态维护未启用" : visible.length ? "" : "暂无已启用的首页状态";
    const accessible = visible.map(([kind,label,raw,disabled]) => `${label} ${disabled ? "未启用" : kind === "energy" && typeof raw === "number" ? `${raw} / 100` : raw || "尚未记录"}`).join("；");
    const hero = sleepEntry ? `<span class="home-state-hero" data-tone="${heroLoading ? "loading" : sleeping ? "rest" : "quiet"}"${heroLoading ? ' data-visual="loading"' : ""} title="${e("睡眠 · " + heroTitle)}">
      <span class="home-state-hero-icon" aria-hidden="true"><i data-lucide="${heroLoading ? "loader-circle" : sleeping ? "moon-star" : "sun"}"></i></span>
      <span class="home-state-hero-copy"><small>当前</small><strong>${e(heroTitle)}</strong></span></span>` : "";
    return `<button type="button" class="home-state-board is-b2"${loading && unreadable ? ' data-loading="true"' : ""} data-go="life" data-life-target="state" aria-label="查看并更新角色状态。${e(accessible || fallback)}" title="查看并更新角色状态">
      ${hero}<span class="home-state-grid">${states}</span>${!hero && !states ? `<span class="home-state-empty">${e(fallback)}</span>` : ""}<span class="home-state-open" aria-hidden="true"><i data-lucide="arrow-up-right"></i></span></button>
      ${unreadable && !loading ? `<p class="home-side-warning" role="status">${e(runtimeMissing ? "角色数据尚未就绪，请核对账号与人格的角色映射；首次设置记录仍会保留。" : state ? "暂时无法读取角色状态，请在详情中核对。" : unavailable("state"))}</p>${runtimeMissing ? `<button class="text-button home-side-action" data-go="manage" data-manage="host">前往账号与会话</button>` : ""}` : ""}`;
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
        detail:text(item.detail), label:`${kind} · ${phase}`, tone, order:1,
        temporary: item.kind === "user_report" || item.kind === "commitment"});
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
    const complete = calendarRead && lifeRead;
    const currentItem = items.find(item => item.order === 0 || item.tone === "current");
    const endedCount = items.filter(item => item.end != null && item.end <= now).length;
    const upcomingItems = items.filter(item => item.start != null && item.start > now)
      .sort((a,b) => a.start - b.start || a.order - b.order);
    const nextItem = upcomingItems[0];
    const beforeStart = !currentItem && endedCount === 0 && upcomingItems.length > 0;
    const status = loading
      ? {tone:"loading",kicker:"正在读取",title:"正在读取今日安排…"}
      : !complete
        ? {tone:"unknown",kicker:"暂时无法确认",title:state?.occurrences?.partial ? "日程未完整读取" : "日程暂不可读取"}
        : currentItem
          ? {tone:"current",kicker:currentItem.key?.includes("sleep") ? "" : "进行中",title:currentItem.title}
          : !items.length
            ? {tone:"empty",kicker:"",title:"今天没有安排"}
            : endedCount === items.length
              ? {tone:"ended",kicker:"",title:"今天的安排都结束啦"}
              : beforeStart
                ? {tone:"before",kicker:"开始前",title:"今天还没开始"}
                : {tone:"gap",kicker:"",title:"当前空闲"};
    const nowMinute = minuteOfDay(now, zone) ?? 0;
    const nowPercent = Math.max(0, Math.min(100, nowMinute / dayMinutes * 100));
    const toneOf = item => item.order === 0 || item.tone === "current" ? "current"
      : item.tone === "elapsed" || item.tone === "completed" ? "done"
        : item.tone === "proposed" ? "proposed" : "planned";
    const progressNodeCount = items.filter(item => item.start != null).length;
    const progressNodes = items.flatMap(item => {
      const at = item.start != null ? dayMinute(item.start, now, zone) : null;
      if (at == null) return [];
      const percent = Math.max(0, Math.min(100, at / dayMinutes * 100));
      const tone = toneOf(item), temporary = item.temporary === true;
      const label = `${clock(item.start, zone)} ${item.title}`;
      return [`<span class="home-agenda-progress-node" data-tone="${tone}"${temporary ? ' data-temporary="true"' : ""}${item === nextItem ? ' data-next="true"' : ""} style="left:${percent.toFixed(2)}%" title="${e(label)}" aria-hidden="true"></span>`];
    }).join("");
    const progress = `<div class="home-agenda-progress" role="img" aria-label="今天的进度，当前 ${clock(now, zone)}${progressNodeCount ? `；${progressNodeCount} 个日程节点` : ""}">
      <span class="home-agenda-progress-fill" style="width:${nowPercent.toFixed(2)}%"></span>
      ${progressNodes}
      <span class="home-agenda-progress-now" style="left:${nowPercent.toFixed(2)}%"></span>
    </div>`;
    const scheduleRow = (item, index) => {
      const start = item.start != null ? clock(item.start, zone) : "—";
      const end = item.end != null ? clock(item.end, zone) : "";
      const temporary = item.temporary === true;
      const tone = toneOf(item);
      const isCurrent = tone === "current";
      const detailText = text(item.detail);
      const isLongDetail = Boolean(detailText)
        && (detailText.length > DETAIL_CLAMP_CHARS || detailText.includes("\n"));
      const detailExpanded = !isLongDetail || expandedDetailKeys.has(item.key);
      const detailMarkup = detailText
        ? `<p class="home-agenda-row-detail${detailExpanded ? "" : " is-clamped"}">${e(detailText)}</p>`
          + (isLongDetail
            ? `<button type="button" class="home-agenda-detail-more" data-agenda-detail-key="${e(item.key)}" aria-expanded="${detailExpanded}">${detailExpanded ? "收起详情" : "展开全文"}</button>`
            : "")
        : "";
      return `<li class="home-agenda-row" data-tone="${tone}"${isCurrent ? ' data-current="true" aria-current="time"' : ""}${temporary ? ' data-temporary="true"' : ""} style="--agenda-index:${index}">
        <div class="home-agenda-row-time"><time${item.start == null ? "" : ` datetime="${new Date(item.start).toISOString()}"`}>${e(start)}</time>${end ? `<small>${e(end)}</small>` : ""}</div>
        <span class="home-agenda-row-marker" aria-hidden="true"></span>
        <div class="home-agenda-row-copy">
          <div class="home-agenda-row-heading"><strong>${e(item.title)}</strong>${isCurrent ? `<span class="home-agenda-now-badge">进行中</span>` : ""}${temporary ? `<span class="home-agenda-temp">临时</span>` : ""}</div>
          ${temporary ? `<small class="home-agenda-row-note">临时插入 · 不在原计划内</small>` : ""}
          ${detailMarkup}
        </div>
      </li>`;
    };
    const sorted = [...items].sort((a, b) => (b.start ?? 0) - (a.start ?? 0)
      || b.order - a.order || a.key.localeCompare(b.key));
    const signature = `${status.tone}|${items.map(item=>`${item.key}:${item.tone}:${item.start ?? ""}`).join("|")}`;
    const list = items.length ? `<details class="home-agenda-list"${scheduleListOpen ? " open" : ""}>
      <summary class="home-agenda-list-head"><span>已结束 <b>${endedCount}</b> / 共 <b>${items.length}</b></span><span class="home-agenda-list-toggle"><span class="is-open">收起</span><span class="is-closed">展开</span></span></summary>
      <div class="home-agenda-scroll" tabindex="0" aria-label="今日安排时间轴">
        <ol class="home-agenda-rows" aria-label="今日安排">${sorted.map((item,index)=>scheduleRow(item,index)).join("")}</ol>
      </div>
    </details>` : "";
    return `<span class="home-agenda-signature" hidden>${e(signature)}</span>${progress}
      <div class="home-agenda-status" data-tone="${status.tone}"${loading ? ' data-loading="true"' : ""} role="status">
        <span class="home-agenda-status-kicker">${e(status.kicker)}</span>
        <strong>${e(status.title)}</strong>
      </div>
      ${list}
      ${messages.length ? `<p class="home-side-warning" role="status">${e(messages.join("；"))}</p>` : ""}
      ${runtimeMissing ? `<button class="text-button home-side-action" data-go="manage" data-manage="host">前往账号与会话</button>` : ""}
      <p class="home-side-caption">${planningNote ? `${e(planningNote)} · ` : ""}时间按 ${e(zone)} 显示</p>`;
  }
  return {renderState, renderSchedule, stateAge, vital, specialState, syncScrollFade};
})();
