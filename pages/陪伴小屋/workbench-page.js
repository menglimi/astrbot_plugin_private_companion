/* 统一配置工作台 · 内核
   取代此前 reply / expression / semantic / config-editor 各自实现的保存生命周期。
   各页只声明「字段 + 作用范围 + 接口」，草稿、脏状态、幂等重试、版本冲突、
   层轨与幽灵值由本文件统一负责。

   设计要点：
   - 声明式字段：kind/minimum/maximum 全部来自后端下发的 `fields` 契约，
     页面不再硬编码一份可能与校验器漂移的范围。
   - 层轨：后端下发 `layers`（继承链，外→内）与 `provenance`（每字段实际来源）。
     本文件把它画成可交互的层轨，并让悬停某层时其余层退后。
   - 幂等：保存沿用 operation_key + edit_token，结果未确认时锁编辑并只重试原请求。
   - 草稿：sessionStorage 按 installation + operator + 当前对象/角色 + group 隔离，切页不丢。 */
"use strict";
window.CompanionWorkbench = (() => {
  const PREFIX = "/astrbot_plugin_private_companion/";
  const STORE = "companion-workbench.v1:";
  /* 跳转到出错字段时跟随系统的减少动态偏好：平滑滚动对前庭敏感的用户不友好。 */
  const REDUCED = () => window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true
    || ["low", "off"].includes(document.documentElement.dataset.motion || "");

  /* 生效范围的四档用**自解释的长句**：四个词单独摆出来（安装/角色/关系/窗口）
     是"按实现分的类"，读的人还得先学会这套分类；写成一句话就不用解释了。
     「继承自…」那句仍用短名词，否则会读成「继承自只对这个角色生效」。 */
  const LAYER_LABELS = {
    installation: "所有角色共用", role: "只对这个角色生效",
    relationship: "只对这个人生效", window: "只在这个对话生效", scope: "生效范围",
  };
  const LAYER_SHORT_LABELS = {
    installation: "安装", role: "角色", relationship: "关系", window: "窗口",
  };
  const LAYER_ORIGINS = {
    installation: "所有角色", role: "这个角色", relationship: "这个人", window: "这个对话",
  };
  const KIND = {
    boolean: "bool", integer: "int", text: "text", probability: "float", weight: "float",
  };

  /* 活着的工作台实例。宿主要判断「离开这一页会不会丢掉没保存的改动」，
     而实例是由各页面各自 create 出来的，内核是唯一知道它们的地方。 */
  const LIVE = new Set();
  const registerInstance = api => LIVE.add(api);
  const forgetInstance = api => LIVE.delete(api);

  /* 取值为枚举的文本字段：注册表里它们仍是 TEXT（约束在各自领域的
     ``review_settings`` 一类校验器里），所以选项由页面声明、后端校验。
     声明缺失时退回普通文本输入，不会渲染一个空的下拉。 */
  const CHOICES = new Map();

  const errors = {
    configuration_revision_conflict: "配置已经更新。草稿已保留，读取新版本后再核对。",
    configuration_request_invalid: "请求格式有误，请刷新后重试。",
    invalid_configuration_value: "请核对数值类型与范围。",
    invalid_precision: "数值精度超出允许范围。",
    dashboard_identity_required: "请从已登录的 AstrBot 管理界面打开陪伴小屋。",
    core_access_required: "当前账号没有管理权限。",
    staging_not_initialized: "角色数据尚未加载，请重新读取。",
    staging_host_terminated: "插件已停止，请重新进入插件。",
    host_context_unavailable: "这个角色还没有可读取的私聊上下文；请先在该窗口收到一条消息，或在「账号与会话」中启用它。",
    host_target_mismatch: "当前窗口与角色映射不一致，请刷新「账号与会话」后重试。",
    host_binding_changed: "当前角色绑定已变化，请重新读取角色状态。",
    host_persona_changed: "角色人格已变化，请重新读取角色状态。",
    bridge_timeout: "连接超时，请核对连接后重试。",
    invalid_response: "返回数据不完整，请刷新核对。",
    route_not_found: "当前页面请求的功能路由未注册；请把插件升级到最新版并重启 AstrBot 后重新打开。",
    "未找到该路由": "当前页面请求的功能路由未注册；请把插件升级到最新版并重启 AstrBot 后重新打开。",
    /* 表达学习分区（expression-page.js）走这个内核渲染，它的域内码放在这里，
       避免分区再自带一张会与内核漂移的第二份表。 */
    expression_settings_invalid: "组合不合法：单批取样条数不能小于最低样本数，候选池不能小于每轮注入规则数；也请核对各项数值范围。",
    expression_configuration_invalid: "请提供明确的配置变更。",
    expression_private_audience_required: "表达学习只适用于私聊对象。",
    expression_group_audience_required: "这一项只适用于群聊对象。",
    expression_owner_unavailable: "表达学习的工作区当前不可用，请重新选择对象。",
    expression_binding_changed: "当前角色绑定已变化，请重新选择后再操作。",
    /* 撤回 / 屏幕 / 熟人卡 / 成长四个分区也走这个内核渲染，域内码同样放在这里。 */
    recall_scope_invalid: "撤回原文转写按会话生效，请选择一个已初始化的私聊对象。",
    recall_target_changed: "当前对象或角色绑定已变化，请重新读取。",
    recall_configuration_invalid: "请核对转写条数、字数与时间窗的取值范围。",
    screen_scope_invalid: "屏幕解读按会话生效，请选择一个已初始化的私聊对象。",
    screen_target_changed: "当前对象或角色绑定已变化，请重新读取。",
    screen_configuration_invalid: "请核对开关与素材字数上限。",
    person_scope_invalid: "熟人卡按对象生效，请选择一个已初始化的私聊对象。",
    person_target_changed: "当前对象或角色绑定已变化，请重新读取。",
    role_growth_scope_invalid: "成长与技能按会话生效，请选择一个已初始化的私聊对象。",
    role_growth_target_changed: "当前对象或角色绑定已变化，请重新读取。",
    growth_target_changed: "当前对象或角色绑定已变化，请重新读取后再保存。",
    /* 知识库与日记/梦境两个分区同样走这个内核渲染。 */
    knowledge_scope_invalid: "知识库按会话生效，请选择一个已初始化的私聊对象。",
    knowledge_target_changed: "当前对象或角色绑定已变化，请重新读取。",
    knowledge_settings_invalid: "知识库设置不合法：单轮的引用字符上限不能小于单条素材的字数上限。",
    role_life_scope_invalid: "日记与梦境按会话生效，请选择一个已初始化的私聊对象。",
    role_life_target_changed: "当前对象或角色绑定已变化，请重新读取。",
    rest_gate_settings_invalid: "休息判定设置不合法：判定方式只能选概率或模型，时段格式形如 23:00-08:30。",
    life_timezone_invalid: "请填写有效的 IANA 时区，例如 Asia/Shanghai。",
    life_clock_invalid: "请填写二十四小时制的时刻，形如 23:50 或 00:30。",
    /* 书架与群聊参与同样经内核渲染。书架的三个码此前只写在 config-editor.js
       的导出表里，而那张表无人引用，页面因此会漏出裸机器码。 */
    library_scope_invalid: "书架按角色生效，请选择一个已初始化的私聊对象。",
    library_target_changed: "当前对象或角色绑定已变化，请重新读取。",
    library_configuration_invalid: "请核对开关与两项数量上限的取值范围。",
    group_participation_scope_invalid: "群聊参与按会话生效，请选择一个已初始化的私聊对象。",
    group_participation_target_changed: "当前对象或角色绑定已变化，请重新读取。",
    group_participation_configuration_invalid: "请核对群聊参与设置：概率需在 0 到 1 之间，数字项需在各自范围内；打断语只能是一行、不超过 80 字。",
    /* 参考图与衣柜（生图分区 C1）同样经内核渲染。参考图的字段校验码来自
       分组验证器（photo_reference_*），与编辑器的 reference_* 前缀不同名。 */
    reference_scope_invalid: "参考图按角色生效，请选择一个已初始化的私聊对象。",
    reference_target_changed: "当前对象或角色绑定已变化，请重新读取。",
    reference_configuration_invalid: "请提供明确的配置变更。",
    photo_reference_configuration_invalid: "请核对三个开关与轮换天数（1 到 365）的取值范围。",
    photo_reference_group_disabled: "这个角色尚未允许在群聊使用已登记参考图。",
    wardrobe_scope_invalid: "衣柜按角色生效，请选择一个已初始化的私聊对象。",
    wardrobe_target_changed: "当前对象或角色绑定已变化，请重新读取。",
    wardrobe_configuration_invalid: "请核对开关、两处文字（各不超过 800 字）与两项数量上限的取值范围。",
    /* 主动联系偏好（proactive.contact）经内核渲染。它有两层域内码：
       编辑器自己的 proactive_contact_*，以及所有者与分组校验器的 contact_* /
       invalid_contact_preference——后者是后端唯一的取值校验码，而页面能先拦的形状
       由 contactAudit 兜住，所以这里要把两张表的码都写上。 */
    proactive_contact_scope_invalid: "当前对象的联系状态不可用，请选择已建立私聊的用户。",
    proactive_contact_target_changed: "当前对象或角色绑定已变化，请重新读取。",
    proactive_contact_configuration_invalid: "请核对主动联系设置：数值需在各自范围内，枚举项需在下拉选项内。",
    configuration_relationship_required: "主动联系偏好需要一个已绑定的角色和这个会话的真人关系；请先完成对象与角色的绑定。",
    invalid_contact_preference: "主动联系设置有字段不合法：时刻用 HH:MM，问候窗口用 HH:MM-HH:MM，时区用 IANA 名称，暂停时间点用带时区的 ISO 时间，活跃曲线写满 24 个数字，清单只能填允许的名字，重要日期要是 JSON 数组。",
    proactive_contact_clock_invalid: "请填写二十四小时制的时刻，形如 23:00 或 07:45。",
    proactive_contact_window_invalid: "问候窗口形如 07:45-10:20，且必须当天内结束，不能跨午夜。",
    proactive_contact_timezone_invalid: "请填写有效的 IANA 时区，例如 Asia/Shanghai。",
    proactive_contact_pause_invalid: "暂停时间点要带时区，形如 2026-09-28T12:00:00+00:00；留空表示本层不设暂停。",
    proactive_contact_curve_invalid: "活跃曲线必须写满 24 个 0.05 到 2.00 之间的数字，用逗号分隔。",
    proactive_contact_list_invalid: "清单里只能填允许的名字，用逗号分隔且不能重复；热榜来源只接受 weibo 与 hackernews。",
    proactive_contact_dates_invalid: "重要日期要写成 JSON 数组，最多 32 项。",
    proactive_contact_source_invalid: "资讯来源类型与 UID / 地址不匹配：B 站动态要十进制 UID，RSS 与 HTTPS JSON 要 https 地址。",
  };

  const e = value => String(value ?? "").replace(/[&<>"']/g, c =>
    ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const error = (code, confirmed = false) => Object.assign(new Error(code), {code, confirmed});
  function relative(path) {
    if (typeof path !== "string" || !path.startsWith(PREFIX)) throw error("invalid_response");
    const route = path.slice(PREFIX.length);
    if (!route || /[?#%\\:]/.test(route) || route.split("/").some(part => !part || part === "." || part === "..")) {
      throw error("invalid_response");
    }
    return route;
  }
  /* 后端把机器码放在信封的 message 上（页面桥只转发 message、丢掉其余字段），
     所以取码要同时看 data.code 与 message；reason 里可能是可直接展示的原因句，
     那种情况要原样保留，不能替换成泛化文案。 */
  const isCode = value => typeof value === "string" && /^[a-z][a-z0-9_]{2,60}$/.test(value);
  const codeOf = wrapped => wrapped?.data?.code
    || (isCode(wrapped?.message) ? wrapped.message : "") || "";
  const ENVELOPE_KEYS = new Set(["status", "message", "reason", "data", "error", "code"]);
  const hasOwn = (value, name) => !!value && typeof value === "object"
    && Object.prototype.hasOwnProperty.call(value, name);
  const isEnvelope = value => !!value && typeof value === "object" && !Array.isArray(value)
    && ["ok", "error"].includes(value.status)
    && Object.keys(value).every(name => ENVELOPE_KEYS.has(name))
    && (value.status === "error" || hasOwn(value, "data"));
  const localized = (problem, fallback) => errors[problem?.code]
    || (isCode(problem?.message) ? errors[problem.message] : problem?.message)
    || fallback;
  const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  let instanceSequence = 0;
  /* Short-lived stale-while-revalidate cache for workbench reads.  Page
     navigation often remounts the same configuration section; painting the
     last successful snapshot immediately makes tab switches feel instant while
     the background refresh still reconciles the edit token. */
  const READ_CACHE = new Map();
  const READ_CACHE_MS = 15000;
  const readCache = key => {
    const item = READ_CACHE.get(key);
    if (!item || Date.now() - item.at > READ_CACHE_MS) { READ_CACHE.delete(key); return null; }
    return JSON.parse(JSON.stringify(item.payload));
  };
  const writeCache = (key, payload) => {
    if (READ_CACHE.size > 256) READ_CACHE.clear();
    try { READ_CACHE.set(key, {at: Date.now(), payload: JSON.parse(JSON.stringify(payload))}); } catch {}
  };
  const clearReadCache = () => READ_CACHE.clear();

  /* === 字段契约：把后端 FieldDefinition 收敛成渲染所需的最小形状 === */
  function contract(name, definition, choices = null) {
    const kind = KIND[definition?.kind] || "text";
    return {
      name,
      kind,
      /* 枚举对文本与数字字段都生效：注册表把「有固定档位」的值注册成 INTEGER
         （如成长对日程的影响强度 0–3），只认文本会让这类字段渲染成数字框、
         丢掉档位文案。布尔字段不接受枚举——它的控件就是开关。
         缺省时保持普通输入。 */
      choices: kind !== "bool" && choices ? {...choices} : null,
      minimum: definition?.minimum ?? null,
      maximum: definition?.maximum ?? null,
      maximumChars: definition?.maximum_chars ?? null,
      decimals: definition?.decimal_places ?? (kind === "float" ? 2 : 0),
      editable: definition?.editable_layers || [],
      hasBounds: definition?.minimum != null && definition?.maximum != null,
    };
  }

  const choiceText = (item, value) => (item.choices && item.choices[value]) || value;
  /* 词条/顺序列表在网线上有两种历史写法：编辑器写出的 JSON 数组串，以及后端
     注册默认值那种用换行、中英文逗号或分号分隔的纯文本。只认 JSON 会把默认值
     读成 0 个词条、让整张卡判为非法；非 JSON 文本按同一组分隔符回退。 */
  const WORD_LIST_SPLIT = /[\r\n,，;；]+/;
  const parseStringList = value => {
    const text = String(value ?? "").trim();
    if (!text) return [];
    if (text.startsWith("[")) {
      try {
        const parsed = JSON.parse(text);
        return Array.isArray(parsed) && parsed.every(item => typeof item === "string") ? parsed : null;
      } catch {}
    }
    return text.split(WORD_LIST_SPLIT).map(item => item.trim()).filter(Boolean);
  };
  const parseLineList = value => String(value ?? "").split(/\r\n|\n|\r/)
    .map(line => line.trim()).filter(Boolean).map(line => {
      const token = line.toLocaleLowerCase();
      if (["<newline>", "{newline}", "[newline]", "\\n"].includes(token)) return "\n";
      if (["<space>", "{space}", "[space]"].includes(token)) return " ";
      if (["<tab>", "{tab}", "[tab]"].includes(token)) return "\t";
      return line;
    });
  const lineListText = words => words.map(word => word === "\n" ? "<newline>"
    : word === "\t" ? "<tab>" : word === " " ? "<space>" : word).join("\n");
  const parseReplacementList = value => {
    try {
      const parsed = JSON.parse(String(value ?? ""));
      if (!Array.isArray(parsed)) return null;
      const pairs = parsed.map(item => {
        if (item && typeof item === "object" && !Array.isArray(item)
            && typeof item.from === "string" && typeof item.to === "string") return {from: item.from, to: item.to};
        if (typeof item === "string" && item.includes("=>")) {
          const [from, ...rest] = item.split("=>");
          return {from: from.trim(), to: rest.join("=>").trim()};
        }
        return null;
      });
      return pairs.every(Boolean) ? pairs : null;
    } catch { return null; }
  };
  const COMPONENT_ORDER = ["voice", "at", "text", "face", "image"];
  const COMPONENT_LABELS = {voice: "语音", at: "@ 提及", text: "文字", face: "平台表情", image: "图片"};
  const visibleToken = value => value === "\n" ? "换行" : value === "\t" ? "制表符"
    : value === " " ? "空格" : value;
  const wordLabel = value => {
    const folded = value.toLocaleLowerCase();
    if (value === "\n" || folded === "<newline>") return "换行";
    if (value === "\t" || folded === "<tab>") return "制表符";
    if (value === " " || folded === "<space>") return "空格";
    return value;
  };
  /* 自我页的三种「轻结构」文本：生日（年份可省、可标农历）、口吻示例（一句一行）
     与词条（顿号分隔的自由文本）。它们在注册表里仍是 TEXT，值按原样保存；
     控件只负责把结构化编辑翻译回字符串，真源始终是 data-wb-field 那一个控件。 */
  const TAG_SPLIT = /[、,，;；\n]+/;
  const parseTagList = value => String(value ?? "").split(TAG_SPLIT)
    .map(part => part.trim()).filter(Boolean);
  const parseQuoteLines = value => String(value ?? "").split(/\r\n|\n|\r/);
  const CHINESE_DIGITS = {零: 0, 〇: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5,
    六: 6, 七: 7, 八: 8, 九: 9};
  const LUNAR_MONTHS = ["", "正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊"];
  const LUNAR_DAYS = ["", "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九",
    "初十", "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
    "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十"];
  const lunarFormatter = (() => {
    try { return new Intl.DateTimeFormat("en-u-ca-chinese", {
      year: "numeric", month: "long", day: "numeric", timeZone: "UTC",
    }); } catch { return null; }
  })();
  const lunarYearCache = new Map();
  const utcTime = (year, month, day) => {
    const date = new Date(0);
    date.setUTCHours(0, 0, 0, 0);
    date.setUTCFullYear(year, month, day);
    return date.getTime();
  };
  function chineseNumber(text) {
    if (text.startsWith("初")) text = text.slice(1);
    if (/^\d+$/.test(text)) return Number(text);
    if (/^[零〇一二两三四五六七八九]+$/.test(text)) {
      return Number([...text].map(digit => CHINESE_DIGITS[digit]).join(""));
    }
    if (text === "廿") return 20;
    if (text === "卅") return 30;
    if (text.startsWith("廿")) return 20 + (CHINESE_DIGITS[text.slice(1)] || 0);
    if (text.startsWith("卅")) return 30 + (CHINESE_DIGITS[text.slice(1)] || 0);
    if (text === "十") return 10;
    if (text.startsWith("十")) return 10 + (CHINESE_DIGITS[text.slice(1)] || 0);
    if (text.endsWith("十")) return (CHINESE_DIGITS[text[0]] || 0) * 10;
    if (text.includes("十")) {
      const [tens, ones] = text.split("十");
      return (CHINESE_DIGITS[tens] || 1) * 10 + (CHINESE_DIGITS[ones] || 0);
    }
    return CHINESE_DIGITS[text] || 0;
  }
  function parseBirthday(value) {
    const text = String(value ?? "").normalize("NFKC").trim();
    const lunarMarked = /农历|阴历|舊曆|旧历|農曆|陰曆/.test(text);
    const solarMarked = /公历|阳历|新历|陽曆|公曆/.test(text);
    const normalized = text.replace(/[\s·，,]/g, "")
      .replace(/农历|阴历|舊曆|旧历|農曆|陰曆|公历|阳历|新历|陽曆|公曆/g, "")
      .replace(/[()\[\]【】（）]/g, "")
      .replace(/^[:：]/, "")
      .replace(/^(?:每年)?(?:出生年月日|出生日期|生日|出生于|出生|日期)(?:是|为)?[:：]?/, "")
      .replace(/(?:生日|纪念日)$/, "");
    const iso = /^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/.exec(normalized);
    if (iso) return {year: iso[1], month: iso[2], day: iso[3], lunar: lunarMarked, leap: false};
    const monthFirst = /^(\d{1,2})[-/.](\d{1,2})(?:[-/.](\d{4}))?$/.exec(normalized);
    if (monthFirst) return {year: monthFirst[3] || "", month: monthFirst[1], day: monthFirst[2],
      lunar: lunarMarked, leap: false};
    const chinese = /^(?:(\d{4}|[零〇一二两三四五六七八九]{4})年?)?(闰|閏)?(正|冬|腊|[零〇一二两三四五六七八九十\d]{1,3})月((?:初|廿|卅)?[零〇一二两三四五六七八九十\d]{1,3})[日号號]?$/.exec(normalized);
    if (chinese) {
      const month = chinese[3] === "正" ? 1 : chinese[3] === "冬" ? 11
        : chinese[3] === "腊" ? 12 : chineseNumber(chinese[3]);
      const day = chineseNumber(chinese[4]);
      const year = chinese[1] ? (/^\d+$/.test(chinese[1]) ? chinese[1]
        : String(chineseNumber(chinese[1]))) : "";
      const inferredLunar = Boolean(chinese[2] || /^(?:正|冬|腊)/.test(chinese[3])
        || /^(?:初|廿|卅)/.test(chinese[4]));
      const lunar = lunarMarked || (!solarMarked && inferredLunar);
      return {year, month: String(month), day: String(day), lunar, leap: Boolean(chinese[2])};
    }
    return null;
  }
  function birthdayPartsError(parts) {
    const year = parts.year ? Number(parts.year) : null;
    const month = Number(parts.month);
    const day = Number(parts.day);
    if (year != null && (!Number.isInteger(year) || year < 1 || year > 9999)) return "年份需在 1 至 9999 年之间。";
    if (!Number.isInteger(month) || month < 1 || month > 12 || !Number.isInteger(day) || day < 1 || day > 31) {
      return "请核对生日的月份和日期。";
    }
    if (parts.lunar) {
      if (day > 30) return "农历日期最多为三十。";
      if (year != null) {
        const index = lunarDateIndex(year);
        if (!index) return "当前浏览器无法校验这个农历年份，请核对年份或更换浏览器。";
        if (!index.has(month + ":" + (parts.leap ? "1" : "0") + ":" + day)) {
          return "这个年份没有对应的农历日期，请核对月份、日期和闰月。";
        }
      }
    } else {
      const checkYear = year == null ? 2000 : year;
      const daysInMonth = new Date(utcTime(checkYear, month, 0)).getUTCDate();
      if (day > daysInMonth) return "这个公历日期不存在，请核对月份和日期。";
    }
    return "";
  }
  const birthdayText = (parts, lunar, noYear) => {
    const year = !noYear && parts.year ? String(Number(parts.year)) + "年" : "";
    if (lunar) return year + "农历" + (parts.leap ? "闰" : "")
      + LUNAR_MONTHS[Number(parts.month)] + "月" + LUNAR_DAYS[Number(parts.day)];
    return year + Number(parts.month) + "月" + Number(parts.day) + "日";
  };
  function lunarDateIndex(year) {
    if (!lunarFormatter || !Number.isInteger(year) || year < 1 || year > 9999) return null;
    if (lunarYearCache.has(year)) return lunarYearCache.get(year);
    const months = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh",
      "Eighth", "Ninth", "Tenth", "Eleventh", "Twelfth"];
    const found = new Set();
    const start = utcTime(year, 0, 1);
    const stop = utcTime(year + 1, 3, 1);
    try {
      for (let time = start; time < stop; time += 86400000) {
        const parts = Object.fromEntries(lunarFormatter.formatToParts(new Date(time))
          .map(part => [part.type, part.value]));
        if (Number(parts.relatedYear) !== year) continue;
        const match = /^(First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth|Eleventh|Twelfth) Month(bis)?$/i
          .exec(parts.month || "");
        if (!match) continue;
        const month = months.findIndex(name => name.toLowerCase() === match[1].toLowerCase()) + 1;
        found.add(String(month) + ":" + (match[2] ? "1" : "0") + ":" + Number(parts.day));
      }
    } catch { return null; }
    lunarYearCache.set(year, found.size ? found : null);
    return found.size ? found : null;
  }
  const fireInput = el => el.dispatchEvent(new Event("input", {bubbles: true}));
  const displayValue = (item, value) => item.input === "line-list"
    ? (parseStringList(value) || []).map(word => word === "\n" ? "<newline>" : word).join(" · ") || "（空）"
    : item.input === "word-list"
    ? (parseStringList(value) || []).map(wordLabel).join("、") || "（空）"
    : item.input === "replacement-list"
      ? (parseReplacementList(value) || []).map(pair => `${visibleToken(pair.from)} → ${visibleToken(pair.to) || "删除"}`).join("、") || "（空）"
      : item.input === "order-list"
        ? (parseStringList(value) || []).map(kind => COMPONENT_LABELS[kind] || kind).join(" → ") || "自然顺序"
        : item.choices ? choiceText(item, value) : value;

  /* 下拉的选项键是字符串，而字段本身可能是整数；比较与写回都要先归一。 */
  const choiceKey = (item, value) => (item.choices ? String(value) : value);
  const coerceChoice = (item, value) => (item.kind === "int" ? Number(value)
    : item.kind === "float" ? Number(value) : value);

  function valid(item, value) {
    /* 快照可能带出本页面不编辑的字段（如群唤醒、风控开关）。
       那些字段没有控件、也没有契约，就不在这里判——
       它们既不会被改动，也不会进入 changes。 */
    if (!item) return true;
    if (item.kind === "bool") return typeof value === "boolean";
    if (item.input === "word-list" || item.input === "line-list") {
      const words = parseStringList(value);
      const policy = item.listPolicy || {};
      if (!words || words.length < (policy.minItems || 0) || words.length > (policy.maxItems || 80)
          || words.some(word => !word.length || word.length > (policy.maxLength || 200))) return false;
      if (policy.unique === "exact" && new Set(words).size !== words.length) return false;
      if (policy.unique === "casefold"
          && new Set(words.map(word => word.toLocaleLowerCase())).size !== words.length) return false;
      if (policy.numericIds && words.some(word => !/^[0-9]+$/.test(word) || word.startsWith("0"))) return false;
      return true;
    }
    if (item.input === "chips" && item.chipOptions?.mode === "multi") {
      const options = item.chipOptions.options || [];
      const parts = parseTagList(value);
      return parts.length > 0 && parts.every(part => options.includes(part))
        && new Set(parts).size === parts.length;
    }
    if (item.input === "replacement-list") {
      const pairs = parseReplacementList(value);
      const policy = item.listPolicy || {};
      return Boolean(pairs && pairs.length <= (policy.maxItems || 80)
        && pairs.every(pair => pair.from.trim().length > 0
          && pair.from.length <= (policy.fromMaxLength || 200)
          && pair.to.length <= (policy.toMaxLength || 500)));
    }
    if (item.input === "order-list") {
      const order = parseStringList(value);
      return Boolean(order && order.length <= COMPONENT_ORDER.length
        && order.every(kind => COMPONENT_ORDER.includes(kind))
        && new Set(order).size === order.length);
    }
    /* 有档位的字段只接受档位内的值；数字字段的草稿已是数字，比较前先归一成字符串。 */
    if (item.choices) return Object.hasOwn(item.choices, String(value));
    if (item.kind === "int") {
      return Number.isInteger(value)
        && (item.minimum == null || value >= item.minimum)
        && (item.maximum == null || value <= item.maximum);
    }
    if (item.kind === "float") {
      return typeof value === "number" && Number.isFinite(value)
        && (item.minimum == null || value >= item.minimum)
        && (item.maximum == null || value <= item.maximum);
    }
    if (item.maximumChars != null && Array.from(String(value ?? "")).length > item.maximumChars) return false;
    return typeof value === "string";
  }

  /* 概率字段在界面按百分比编辑，契约里仍是 0–1 的小数。 */
  const toDisplay = (item, value) => item.kind === "float" && item.maximum === 1 && item.minimum === 0
    ? Number((value * 100).toFixed(Math.max(0, item.decimals + 2))) : value;
  const fromDisplay = (item, value) => item.kind === "float" && item.maximum === 1 && item.minimum === 0
    ? Number((value / 100).toFixed(Math.max(0, item.decimals + 2))) : value;

  /* === 渲染 === */
  function railMarkup(state, name, item, provenance, container) {
    const source = provenance?.[name]?.layer ?? null;
    const writeLayer = item.writeLayer || container.writeLayer;
    const nodes = container.layers.map(layer => {
      const sourceClass = layer === source ? " is-source" : "";
      const editable = item.editable.length && !item.editable.includes(layer) ? " is-locked" : " is-editable";
      const label = LAYER_LABELS[layer] || layer;
      const shortLabel = LAYER_SHORT_LABELS[layer] || layer;
      const inherits = (!item.editable.length || item.editable.includes(layer))
        ? (layer === writeLayer ? "本次保存写入这一层" : `${label}层可写`)
        : `${shortLabel}层不适用于这一字段`;
      return `<span class="wb-node${sourceClass}${editable}" data-layer="${e(layer)}"`
        + ` title="${e(label)}层 · ${e(layer === source ? "当前生效值来自这里" : inherits)}"></span>`;
    }).join("");
    return `<div class="wb-rail${container.drawRails ? "" : " is-drawn"}" data-rail="${e(name)}"`
      + ` aria-hidden="true">${nodes}</div>`;
  }

  function controlMarkup(item, value, locked, state) {
    const dis = locked ? " disabled" : "";
    if (item.kind === "bool") {
      return `<label class="wb-switch"><input type="checkbox" data-wb-field="${e(item.name)}" ${value ? "checked" : ""}${dis}>`
        + `<span class="wb-switch-track" aria-hidden="true"></span>`
        + `<span class="wb-switch-state">${value ? "已开启" : "已关闭"}</span></label>`;
    }
    if (item.segmented) {
      const selected = choiceKey(item, value);
      const choices = Object.entries(item.choices || {}).map(([key, text]) =>
        "<label class=\"wb-segmented-option\"><input type=\"radio\" data-wb-field=\"" + e(item.name) + "\""
        + " name=\"wb-" + e(item.name) + "\" value=\"" + e(key) + "\""
        + (selected === key ? " checked" : "") + dis + "><span>" + e(text)
        + "</span></label>").join("");
      return "<div class=\"wb-segmented\" role=\"radiogroup\" aria-label=\""
        + e(item.label || item.name) + "\">" + choices + "</div>";
    }
    if (item.choices && item.input !== "chips") {
      /* 枚举：空值是合法的「跟随上级」，所以不自动补一个选中项。
         草稿可能是数字（整数档位字段），选项键一定是字符串，比较前先归一。 */
      const selected = choiceKey(item, value);
      const options = Object.entries(item.choices).map(([key, text]) =>
        `<option value="${e(key)}" ${selected === key ? "selected" : ""}>${e(text)}</option>`).join("");
      return `<select data-wb-field="${e(item.name)}"${dis}>${options}</select>`;
    }
    if (item.kind === "text" && item.input === "source-checklist") {
      const selected = new Set(String(value ?? "").split(/[\r\n,;，；]+/)
        .map(identifier => identifier.trim()).filter(Boolean));
      const sources = item.catalog?.(state.status) || [];
      const selectedLimit = Number(state.draft?.source_limit || 16);
      const option = (id, label, detail, attributes = "") =>
        `<label class="wb-source-option"><input type="checkbox" data-wb-field="${e(item.name)}"`
        + ` data-wb-source-id="${e(id)}"${attributes} ${selected.has(id) ? "checked" : ""}${dis}>`
        + `<span><b>${e(label)}</b>${detail ? `<small>${e(detail)}</small>` : ""}</span></label>`;
      const rows = sources.map(source => {
        if (source.type === "missing") {
          return `<div class="wb-source-missing">${option(source.id, source.name || source.id,
            "AstrBot 中已不可用", " data-wb-source-missing")}</div>`;
        }
        const kbId = String(source.kb_id || "");
        const docs = Array.isArray(source.documents) ? source.documents : [];
        const base = option(source.id, `${source.emoji ? source.emoji + " " : ""}${source.name || kbId}`,
          `${Number(source.doc_count || docs.length)} 篇文档 · ${Number(source.chunk_count || 0)} 段`,
          ` data-wb-source-base="${e(kbId)}"`);
        const docRows = docs.map(doc => option(doc.id, doc.name || doc.doc_id,
          `${doc.file_type || "文档"} · ${Number(doc.chunk_count || 0)} 段`,
          ` data-wb-source-document="${e(kbId)}"`)).join("");
        return `<section class="wb-source-group">${base}${source.description
          ? `<p>${e(source.description)}</p>` : ""}${docRows ? `<div class="wb-source-documents">${docRows}</div>` : ""}</section>`;
      }).join("");
      const empty = state.status?.knowledge_sources_available === false
        ? "AstrBot 知识库暂不可用" : "暂无 AstrBot 知识库";
      const count = selected.size;
      return `<div class="wb-source-picker" data-wb-source-limit="${e(selectedLimit)}">`
        + (rows || `<div class="wb-empty-list">${e(empty)}</div>`)
        + `<span class="wb-source-count">已选 ${count} / ${selectedLimit} 项</span></div>`;
    }
    if (item.kind === "text" && item.input === "identity-checklist") {
      let selected = [];
      try {
        const parsed = JSON.parse(String(value || "[]"));
        if (Array.isArray(parsed)) selected = parsed.filter(id => typeof id === "string");
      } catch {}
      const selectedSet = new Set(selected);
      const entries = item.catalog?.(state.status) || {};
      const option = (id, label, missing = false) => `<label class="wb-source-option">`
        + `<input type="checkbox" data-wb-field="${e(item.name)}" data-wb-identity-id="${e(id)}"`
        + `${selectedSet.has(id) ? " checked" : ""}${dis}>`
        + `<span><b>${e(label)}</b>${missing ? "<small>当前角色目录中暂不可用</small>" : ""}</span></label>`;
      const rows = Object.entries(entries).filter(([id]) => id).map(([id, label]) => option(id, label));
      selected.filter(id => !Object.hasOwn(entries, id)).forEach((id, index) =>
        rows.push(option(id, `已选身份 ${index + 1}`, true)));
      return `<div class="wb-source-picker wb-identity-picker">`
        + (rows.join("") || `<div class="wb-empty-list">暂无当前角色已绑定的私聊对象</div>`)
        + `<span class="wb-source-count">已选 ${selectedSet.size} 项</span></div>`;
    }
    if (item.catalog) {
      /* 选项在读取之后才知道（如 TTS 扩展目录），所以由分区声明一个
         取选项的函数；当前值不在目录里时补一条标注，避免选择被静默改写。 */
      const entries = item.catalog(state.status) || {};
      if (value !== undefined && value !== null && !Object.hasOwn(entries, value)) {
        entries[value] = `${value}（暂不可用）`;
      }
      const options = Object.entries(entries).map(([key, text]) =>
        `<option value="${e(key)}" ${value === key ? "selected" : ""}>${e(text)}</option>`).join("");
      return `<select data-wb-field="${e(item.name)}"${dis}>${options}</select>`;
    }
    if (item.kind === "text" && item.input === "line-list") {
      const words = parseStringList(value) || [];
      const policy = item.listPolicy || {};
      const limit = policy.maxItems || 80;
      const rows = Math.max(4, Math.min(8, words.length + 1));
      return `<div class="wb-line-list-editor"><textarea class="wb-line-list" data-wb-field="${e(item.name)}"`
        + ` data-wb-line-list="${e(item.name)}" rows="${rows}" aria-label="${e(item.label || item.name)}"${dis}>`
        + `${e(lineListText(words))}</textarea><div class="wb-word-tools"><span class="wb-line-list-count">${words.length} / ${limit} 项 · 每行一个，<newline> 表示换行</span></div></div>`;
    }
    if (item.kind === "text" && item.input === "word-list") {
      const words = parseStringList(value) || [];
      const policy = item.listPolicy || {};
      const limit = policy.maxItems || 80;
      const chips = words.map((word, index) =>
        "<span class=\"wb-keyword\"><span>" + e(wordLabel(word)) + "</span>"
        + "<button type=\"button\" data-wb-word-remove=\"" + e(item.name) + "\""
        + " data-wb-word-index=\"" + index + "\" aria-label=\"删除 "
        + e(wordLabel(word)) + "\" title=\"删除\""
        + (words.length <= (policy.minItems || 0) ? " disabled" : "")
        + ">" + icon("x") + "</button></span>").join("");
      const specialLabels = {newline: "换行", space: "空格", tab: "制表符"};
      const specialWords = {newline: "\n", space: " ", tab: "\t"};
      const specials = (policy.specials || []).map(special => {
        const present = words.includes(specialWords[special]);
        const glyph = special === "newline" ? icon("corner-down-left") : "";
        return "<button type=\"button\" class=\"wb-word-special\" data-wb-word-special=\""
          + e(special) + "\" data-wb-word-field=\"" + e(item.name) + "\""
          + (locked || present || words.length >= limit ? " disabled" : "") + ">"
          + glyph + "<span>" + e(specialLabels[special] || special) + "</span></button>";
      }).join("");
      const quickWords = (policy.quickAdd || []).map(option => {
        const entry = typeof option === "string" ? {value: option, label: option}
          : option && typeof option === "object" ? option : null;
        if (!entry) return "";
        const special = Object.hasOwn(specialWords, entry.special) ? entry.special : "";
        const word = special ? specialWords[special] : String(entry.value ?? "");
        if (!word) return "";
        const label = String(entry.label ?? (special ? specialLabels[special] : word));
        const present = words.includes(word);
        const valueAttribute = special
          ? `data-wb-word-special=\"${e(special)}\"` : `data-wb-word-value=\"${e(word)}\"`;
        const glyph = special === "newline" ? icon("corner-down-left") : "";
        return `<button type=\"button\" class=\"wb-word-special\" ${valueAttribute}`
          + ` data-wb-word-field=\"${e(item.name)}\"`
          + (locked || present || words.length >= limit ? " disabled" : "") + ">"
          + glyph + `<span>${e(label)}</span></button>`;
      }).join("");
      return "<div class=\"wb-word-editor\" data-wb-word-list=\"" + e(item.name) + "\">"
        + "<div class=\"wb-keywords\" role=\"group\" aria-label=\"" + e(item.label || item.name) + "\">"
        + chips + "<input class=\"wb-keyword-input\" type=\"text\" data-wb-word-input=\""
        + e(item.name) + "\" maxlength=\"" + e(policy.maxLength || 200)
        + "\" aria-label=\"添加" + e(item.label || "词条") + "\" placeholder=\"添加词条\""
        + " title=\"按 Enter 添加\"" + (locked || words.length >= limit ? " disabled" : "") + ">"
        + "<button type=\"button\" class=\"wb-keyword-add\" data-wb-word-add=\"" + e(item.name) + "\""
        + (locked || words.length >= limit ? " disabled" : "") + ">"
        + icon("plus") + "<span>添加</span></button></div>"
        + "<div class=\"wb-word-tools\">" + specials
        + (quickWords ? "<span class=\"wb-word-tools-label\">" + e(policy.quickAddLabel || "常用分段词")
          + "</span>" + quickWords : "")
        + "<span>" + words.length + " / " + limit
        + "</span></div></div>";
    }
    if (item.kind === "text" && item.input === "replacement-list") {
      const pairs = parseReplacementList(value) || [];
      const policy = item.listPolicy || {};
      const limit = policy.maxItems || 80;
      const entries = pairs.map((pair, index) =>
        "<div class=\"wb-replacement-chip\"><span>" + e(visibleToken(pair.from)) + "</span>"
        + "<span class=\"wb-replacement-arrow\" aria-hidden=\"true\">→</span><span>"
        + e(visibleToken(pair.to) || "删除") + "</span><button type=\"button\" data-wb-replacement-remove=\""
        + e(item.name) + "\" data-wb-replacement-index=\"" + index
        + "\" aria-label=\"删除替换规则\" title=\"删除\"" + dis + ">" + icon("x") + "</button></div>").join("");
      return "<div class=\"wb-replacement-editor\" data-wb-replacement-list=\"" + e(item.name) + "\">"
        + "<div class=\"wb-replacement-list\" role=\"list\" aria-label=\"" + e(item.label || item.name) + "\">"
        + entries + (pairs.length ? "" : "<span class=\"wb-empty-list\">尚未添加规则</span>") + "</div>"
        + "<div class=\"wb-replacement-form\" data-wb-replacement-form=\"" + e(item.name) + "\">"
        + "<input type=\"text\" data-wb-replacement-from=\"" + e(item.name) + "\" maxlength=\""
        + e(policy.fromMaxLength || 200) + "\" aria-label=\"匹配原词\" placeholder=\"匹配原词\"" + (locked || pairs.length >= limit ? " disabled" : "") + ">"
        + "<span class=\"wb-replacement-arrow\" aria-hidden=\"true\">→</span>"
        + "<input type=\"text\" data-wb-replacement-to=\"" + e(item.name) + "\" maxlength=\""
        + e(policy.toMaxLength || 500) + "\" aria-label=\"替换文本\" placeholder=\"替换文本（留空表示删除）\"" + (locked || pairs.length >= limit ? " disabled" : "") + ">"
        + "<button type=\"button\" class=\"wb-keyword-add\" data-wb-replacement-add=\"" + e(item.name) + "\""
        + (locked || pairs.length >= limit ? " disabled" : "") + ">" + icon("plus") + "<span>添加</span></button></div>"
        + "<div class=\"wb-word-tools\"><span>" + pairs.length + " / " + limit + " 条</span></div></div>";
    }
    if (item.kind === "text" && item.input === "order-list") {
      const order = parseStringList(value) || [];
      if (!order.length) {
        return "<div class=\"wb-order-editor\"><div class=\"wb-order-natural\">"
          + icon("shuffle") + "<span>跟随自然顺序</span></div><button type=\"button\" class=\"wb-keyword-add\""
          + " data-wb-order-start=\"" + e(item.name) + "\"" + dis + ">" + icon("list-ordered")
          + "<span>自定义顺序</span></button></div>";
      }
      const rows = order.map((kind, index) =>
        "<li class=\"wb-order-item\"><span class=\"wb-order-index\">" + (index + 1) + "</span><strong>"
        + e(COMPONENT_LABELS[kind] || kind) + "</strong><button type=\"button\" data-wb-order-move=\"up\""
        + " data-wb-order-index=\"" + index + "\" aria-label=\"上移" + e(COMPONENT_LABELS[kind] || kind)
        + "\" title=\"上移\"" + (locked || index === 0 ? " disabled" : "") + ">" + icon("chevron-up")
        + "</button><button type=\"button\" data-wb-order-move=\"down\" data-wb-order-index=\"" + index
        + "\" aria-label=\"下移" + e(COMPONENT_LABELS[kind] || kind) + "\" title=\"下移\""
        + (locked || index === order.length - 1 ? " disabled" : "") + ">" + icon("chevron-down")
        + "</button><button type=\"button\" data-wb-order-remove=\"" + e(item.name) + "\" data-wb-order-index=\""
        + index + "\" aria-label=\"移除" + e(COMPONENT_LABELS[kind] || kind) + "\" title=\"移除\""
        + dis + ">" + icon("x") + "</button></li>").join("");
      const available = COMPONENT_ORDER.filter(kind => !order.includes(kind));
      const options = available.map(kind => "<option value=\"" + e(kind) + "\">"
        + e(COMPONENT_LABELS[kind]) + "</option>").join("");
      return "<div class=\"wb-order-editor\"><ol class=\"wb-order-list\" aria-label=\"自定义发送顺序\">"
        + rows + "</ol><div class=\"wb-order-tools\"><select data-wb-order-add-select=\"" + e(item.name)
        + "\" aria-label=\"添加组件\"" + (locked || !available.length ? " disabled" : "") + ">"
        + "<option value=\"\">添加组件…</option>" + options + "</select><button type=\"button\""
        + " class=\"wb-word-special\" data-wb-order-add=\"" + e(item.name) + "\"" + (locked || !available.length ? " disabled" : "")
        + ">" + icon("plus") + "<span>添加</span></button><button type=\"button\" class=\"wb-word-special\""
        + " data-wb-order-natural=\"" + e(item.name) + "\"" + dis + ">" + icon("rotate-ccw") + "<span>恢复自然顺序</span></button></div></div>";
    }
    /* 文字字段是保存真源；识别到日期后，历法选择器可继续细调。 */
    if (item.kind === "text" && item.input === "date") {
      const parts = parseBirthday(value);
      const dateError = parts ? birthdayPartsError(parts) : "";
      const lunar = parts?.lunar || false;
      const noYear = parts ? !parts.year : false;
      const picker = parts && !parts.lunar
        ? `${noYear ? "2000" : parts.year.padStart(4, "0")}-${parts.month.padStart(2, "0")}-${parts.day.padStart(2, "0")}` : "";
      const selectedMonth = parts ? Number(parts.month) : "";
      const selectedDay = parts ? Number(parts.day) : "";
      const year = noYear ? "" : parts?.year || "";
      const monthOptions = Array.from({length: 12}, (_, index) => index + 1).map(month =>
        `<option value="${month}"${selectedMonth === month ? " selected" : ""}>${LUNAR_MONTHS[month]}月</option>`).join("");
      const dayOptions = Array.from({length: 30}, (_, index) => index + 1).map(day =>
        `<option value="${day}"${selectedDay === day ? " selected" : ""}>${LUNAR_DAYS[day]}</option>`).join("");
      return `<div class="wb-date" data-wb-date-group="${e(item.name)}" data-wb-date-invalid="${String(Boolean(dateError))}">`
        + `<input type="text" class="wb-date-text" data-wb-field="${e(item.name)}"`
        + ` data-wb-date-text="${e(item.name)}" value="${e(value)}"`
        + ` aria-label="${e(item.label || item.name)}文字输入"`
        + ` placeholder="如：农历五月初八 / 2000-05-08 / 每年冬至"${dis}>`
        + `<div class="wb-date-solar"${lunar ? " hidden" : ""}>`
        + `<input type="date" data-wb-date="${e(item.name)}" data-wb-date-role="picker" value="${e(picker)}"`
        + ` aria-label="${e(item.label || item.name)}公历日期"${dis}></div>`
        + `<div class="wb-date-lunar"${lunar ? "" : " hidden"}>`
        + `<label class="wb-date-year"><span>农历年份</span><input type="number" min="1" max="9999" step="1"`
        + ` data-wb-date="${e(item.name)}" data-wb-date-role="year" value="${e(year)}"`
        + ` aria-label="${e(item.label || item.name)}农历年份"${noYear ? " disabled" : ""}${dis}></label>`
        + `<label class="wb-date-select"><span>月份</span><select data-wb-date="${e(item.name)}"`
        + ` data-wb-date-role="month" aria-label="农历月份"${dis}><option value="">选择月</option>${monthOptions}</select></label>`
        + `<label class="wb-date-select"><span>日期</span><select data-wb-date="${e(item.name)}"`
        + ` data-wb-date-role="day" aria-label="农历日期"${dis}><option value="">选择日</option>${dayOptions}</select></label>`
        + `<label class="wb-date-flag"><input type="checkbox" data-wb-date="${e(item.name)}"`
        + ` data-wb-date-role="leap"${parts?.leap ? " checked" : ""}${dis}><span>闰月</span></label></div>`
        + `<label class="wb-date-flag"><input type="checkbox" data-wb-date="${e(item.name)}"`
        + ` data-wb-date-role="lunar"${lunar ? " checked" : ""}${dis}><span>农历</span></label>`
        + `<label class="wb-date-flag"><input type="checkbox" data-wb-date="${e(item.name)}"`
        + ` data-wb-date-role="noyear"${noYear ? " checked" : ""}${dis}><span>不确定年份</span></label>`
        + `<button type="button" class="wb-date-clear" data-wb-date-clear="${e(item.name)}" aria-label="清除${e(item.label || "生日")}日期"`
        + ` title="清除日期"${dis}>${icon("x")}</button>`
        + `<span class="wb-date-error" data-wb-date-error role="status" aria-live="polite">${e(dateError)}</span></div>`;
    }
    /* 词条快选：固定候选 + 自定义输入。可见的文本框就是 data-wb-field，
       候选只是往里填写；单击选中、再击清空（multi 模式为顿号清单的加入与移除）。 */
    if (item.kind === "text" && item.input === "chips") {
      const options = item.chipOptions?.options || [];
      const multi = item.chipOptions?.mode === "multi";
      const labels = item.chipOptions?.labels || {};
      const current = String(value ?? "").trim();
      const parts = parseTagList(current);
      const chips = options.map(option => {
        const on = multi ? parts.includes(option) : current === option;
        return `<button type="button" class="wb-chip-opt${on ? " is-on" : ""}"`
          + ` data-wb-chip="${e(item.name)}" data-wb-chip-value="${e(option)}"${dis}>${e(labels[option] || option)}</button>`;
      }).join("");
      const field = item.chipOptions?.hideInput
        ? `<input type="hidden" data-wb-field="${e(item.name)}" value="${e(value)}">`
        : `<input type="text" data-wb-field="${e(item.name)}" value="${e(value)}"`
          + ` aria-label="${e(item.label || item.name)}" placeholder="${e(item.chipOptions?.placeholder || "自定义…")}"${dis}>`;
      return `<div class="wb-chipset" data-wb-chipset="${e(item.name)}">`
        + (chips ? `<div class="wb-chip-opts">${chips}</div>` : "") + field + `</div>`;
    }
    /* 词条小片：从顿号清单里逐项增删（爱好、标志性表达这类自由清单）。 */
    if (item.kind === "text" && item.input === "tags") {
      const tags = parseTagList(value);
      const max = item.chipOptions?.maxTags || 24;
      const slips = tags.map((tag, index) =>
        `<span class="wb-tag"><span>${e(tag)}</span>`
        + `<button type="button" data-wb-tag-remove="${e(item.name)}" data-wb-tag-index="${index}"`
        + ` aria-label="删除 ${e(tag)}" title="删除"${dis}>${icon("x")}</button></span>`).join("");
      const count = Array.from(String(value ?? "")).length;
      return `<div class="wb-tagset" data-wb-tagset="${e(item.name)}">`
        + `<input type="hidden" data-wb-field="${e(item.name)}" value="${e(value)}">`
        + `<div class="wb-tag-slips" role="list" aria-label="${e(item.label || item.name)}">`
        + (slips || `<span class="wb-empty-list">尚未添加</span>`) + `</div>`
        + `<div class="wb-tag-form"><input type="text" data-wb-tag-input="${e(item.name)}"`
        + ` maxlength="${e(Math.min(item.maximumChars || 40, 40))}" aria-label="添加${e(item.label || "词条")}"`
        + ` placeholder="输入后按 Enter"${dis || tags.length >= max ? " disabled" : ""}>`
        + `<button type="button" class="wb-keyword-add" data-wb-tag-add="${e(item.name)}"`
        + (dis || tags.length >= max ? " disabled" : "") + ">" + icon("plus") + "<span>添加</span></button></div>"
        + (item.maximumChars != null
          ? `<span class="wb-text-length${count > item.maximumChars ? " is-over-limit" : ""}" data-wb-counter="${e(item.name)}">${count} / ${e(item.maximumChars)} 字</span>` : "")
        + `</div>`;
    }
    /* 台词气泡：口吻示例一句一行；每一句都是发给模型的样本。 */
    if (item.kind === "text" && item.input === "quotes") {
      const lines = parseQuoteLines(value).map(line => line.trim()).filter(Boolean);
      const count = Array.from(String(value ?? "")).length;
      const bubbles = lines.map((line, index) =>
        `<div class="wb-quote" data-wb-quote><span class="wb-quote-no" aria-hidden="true">${index + 1}</span>`
        + `<textarea data-wb-quote-line="${e(item.name)}" rows="1" aria-label="${e(item.label || item.name)} 第 ${index + 1} 句"${dis}>${e(line)}</textarea>`
        + `<button type="button" class="wb-quote-remove" data-wb-quote-remove="${e(item.name)}"`
        + ` data-wb-quote-index="${index}" aria-label="删除第 ${index + 1} 句" title="删除这句"${dis}>${icon("x")}</button></div>`).join("");
      return `<div class="wb-quotes" data-wb-quotes="${e(item.name)}">`
        + `<input type="hidden" data-wb-field="${e(item.name)}" value="${e(value)}">`
        + `<div class="wb-quote-list">${bubbles || `<span class="wb-empty-list">还没有示例；新增一句试试。</span>`}</div>`
        + `<div class="wb-quote-tools"><button type="button" class="wb-keyword-add" data-wb-quote-add="${e(item.name)}"${dis}>`
        + icon("plus") + "<span>新增一句</span></button>"
        + (item.maximumChars != null
          ? `<span class="wb-text-length${count > item.maximumChars ? " is-over-limit" : ""}" data-wb-counter="${e(item.name)}">${count} / ${e(item.maximumChars)} 字</span>` : "")
        + `</div></div>`;
    }
    if (item.kind === "text") {
      /* 记忆分区的整理时刻与时区是两个「文本」字段，但 textarea 既难读也难填：
         它们其实是 time 输入与 IANA 时区名。`inputs` 声明控件类型，
         `suggestions` 给 datalist 候选项（时区名写错要到后端校验才报错）。 */
      if (item.input === "time") {
        return `<input type="time" data-wb-field="${e(item.name)}" value="${e(value)}"${dis}>`;
      }
      if (item.input === "line") {
        const list = item.suggestions ? ` list="wb-list-${e(item.name)}"` : "";
        const options = item.suggestions
          ? `<datalist id="wb-list-${e(item.name)}">${item.suggestions.map(option =>
              `<option value="${e(option)}"></option>`).join("")}</datalist>` : "";
        return `<input type="text" data-wb-field="${e(item.name)}" value="${e(value)}"${list}${dis}>${options}`;
      }
      const rows = item.rows || 3;
      const textarea = `<textarea data-wb-field="${e(item.name)}" rows="${rows}"${dis}>${e(value)}</textarea>`;
      if (item.maximumChars == null) return textarea;
      const count = Array.from(String(value ?? "")).length;
      return `<div class="wb-textarea-wrap">${textarea}<span class="wb-text-length${count > item.maximumChars ? " is-over-limit" : ""}" data-wb-counter="${e(item.name)}">${count} / ${e(item.maximumChars)} 字</span></div>`;
    }
    const shown = e(toDisplay(item, value));
    const step = item.kind === "int" ? "1" : String(1 / Math.pow(10, item.decimals));
    const bounds = item.hasBounds
      ? `<span class="wb-range">${e(item.minimum)} – ${e(item.maximum)}</span>` : "";
    const unit = item.kind === "float" && item.maximum === 1 && item.minimum === 0 ? " %" : "";
    return `<span class="wb-number"><input type="number" data-wb-field="${e(item.name)}"`
      + ` min="${e(toDisplay(item, item.minimum))}" max="${e(toDisplay(item, item.maximum))}"`
      + ` step="${e(step)}" value="${shown}"${dis}>${unit ? `<span class="wb-unit">${unit.trim()}</span>` : ""}${bounds}</span>`;
  }

  function fieldMarkup(state, container, name) {
    const item = container.fields[name];
    if (!item) return "";
    const value = state.draft[name];
    const provenance = state.status?.provenance?.[name] || {};
    const inherited = provenance.explicit === false && provenance.layer !== null;
    const layerEditable = !item.editable.length || item.editable.includes(item.writeLayer || container.writeLayer);
    const locked = state.locked() || !layerEditable;
    /* 控件已显示当前值；只有草稿覆盖继承值时，额外标出被替换的来源值。 */
    let label = item.label || name;
    if (inherited && container.presentation !== "journal") {
      const ghost = state.base?.[name];
      const shown = displayValue(item, ghost);
      label = e(item.label || name);
      if (value !== ghost) {
        label += ` <span class="wb-ghost" title="当前草稿覆盖前的继承值">继承值 ${e(shown)}</span>`;
      }
    } else {
      label = e(item.label || name);
    }
    const chip = inherited
      ? `<span class="wb-chip">继承自${e(LAYER_ORIGINS[provenance.layer] || LAYER_LABELS[provenance.layer] || provenance.layer)}</span>` : "";
    const reset = inherited && !locked
      ? `<button type="button" class="wb-reset" data-wb-reset="${e(name)}">${icon("undo-2")}恢复继承</button>` : "";
    const dirty = state.base && value !== state.base[name] ? " is-dirty" : "";
    const inheritClass = inherited ? " is-inherited" : "";
    const visible = !item.visibleWhen || Object.entries(item.visibleWhen)
      .every(([field, expected]) => state.draft?.[field] === expected);
    /* 说明与错误各自有个 id：控件的 aria-describedby 在 paint() 之后统一挂
       （见 wireFieldDescriptions）。只写 aria-invalid 不告诉读屏「为什么无效」。 */
    const helpId = `${state.instanceId}-help-${name}`;
    const errorId = `${state.instanceId}-error-${name}`;
    return `<div class="wb-field${dirty}${inheritClass}${layerEditable ? "" : " is-layer-locked"}" data-wb-row="${e(name)}"${visible ? "" : " hidden"}>`
      + (container.presentation === "journal" ? "" : railMarkup(state, name, item, state.status?.provenance, container))
      + `<div class="wb-field-main"><span class="wb-label">${label}</span>`
      + (item.help && container.presentation !== "journal" ? `<p class="wb-help" id="${e(helpId)}">${e(item.help)}</p>` : "")
      + `<div class="wb-control">${controlMarkup(item, value, locked, state)}</div>`
      + `<p class="wb-field-error" id="${e(errorId)}" data-wb-field-error></p></div>`
      + `<div class="wb-field-side">${chip}${reset}</div></div>`;
  }

  function cardMarkup(state, container, titleAbove = "") {
    const s = state;
    /* 分区可以把自己的字段分成若干逻辑段落（如「分段」「拟人」「语音」）。
       分组只是阅读顺序，不改变作用范围：整张卡仍然共用一个编辑令牌与一次保存。 */
    const groups = state.visibleGroupIds
      ? container.groups.filter(group => state.visibleGroupIds.has(group.id))
      : container.groups;
    const grouped = groups.length > 1;
    const journal = container.presentation === "journal";
    const groupMarkup = (group, index) => {
      const names = group.fields.filter(name => container.fields[name]);
      if (!names.length) return "";
      let rows = names.map(name => fieldMarkup(s, container, name)).join("");
      if (journal && container.group === "role_profile" && group.id === "identity") {
        const identityFields = ["name", "gender", "birthday", "age", "species", "occupation"];
        const emblem = Array.from(String(s.draft?.name || "").trim())[0] || "设";
        rows = `<div class="wb-identity-card"><div class="wb-identity-photo" aria-hidden="true">${e(emblem)}</div>`
          + `<div class="wb-identity-rows">${identityFields.filter(name => names.includes(name))
            .map(name => fieldMarkup(s, container, name)).join("")}</div></div>`
          + names.filter(name => !identityFields.includes(name)).map(name => fieldMarkup(s, container, name)).join("");
      }
      /* 只有一段时不必重复标题。 */
      const heading = grouped && group.title
        ? `<h4 class="wb-group-head">${e(group.title)}</h4>` : "";
      /* 段落级的依赖说明必须渲染出来。它说的是「总闸关着时下面几项为什么没反应」——
         不显示的话，用户看到一排不起作用的开关却找不到原因。数据一直传到了这里
         （spec.sections 的 help 经 group.help 进来），只是渲染时被漏掉了。
         用与字段说明同一个 .wb-help 类，保持同一套排版。 */
      const groupHelp = !journal && grouped && group.help
        ? `<p class="wb-help">${e(group.help)}</p>` : "";
      return `<section class="wb-group" data-wb-group="${e(group.id || index)}">${heading}`
        + `${groupHelp}<div class="wb-fields">${rows}</div></section>`;
    };
    const useSectionNavigation = grouped && groups.length <= container.navigationLimit;
    const activeGroup = groups.find(group => group.id === state.activeGroupId)
      || groups[0];
    const blocks = useSectionNavigation
      ? journal
        ? `<div class="wb-section-panels">${groups.map((group, index) => {
            const id = group.id || String(index);
            return `<div class="wb-section-panel" id="${e(container.sectionTabPrefix)}panel-${e(id)}"
                role="tabpanel" aria-labelledby="${e(container.sectionTabPrefix)}tab-${e(id)}"
                data-wb-section-panel="${e(id)}"${id === activeGroup?.id ? "" : " hidden"}>
              ${groupMarkup(group, index)}</div>`;
          }).join("")}</div>`
        : `<div class="wb-section-layout">
          <aside class="wb-section-sidebar">
            <div class="wb-section-nav-head"><span class="wb-section-nav-title">配置模块</span>
              <p class="wb-section-nav-note">保存会提交全部模块的改动</p></div>
            <nav class="wb-section-nav" role="tablist" aria-orientation="vertical" aria-label="${e(container.title)} 配置模块">
            ${groups.map((group, index) => {
              const id = group.id || String(index);
              const active = id === activeGroup?.id;
              const changed = group.fields.filter(name => s.draft?.[name] !== s.base?.[name]
                || state.resetFields.includes(name)).length;
              const count = group.fields.filter(name => container.fields[name]).length;
              return `<button type="button" class="wb-section-tab${changed ? " has-changes" : ""}"
                  id="${state.instanceId}-tab-${e(id)}" role="tab" aria-controls="${state.instanceId}-panel-${e(id)}"
                  aria-selected="${active}" tabindex="${active ? "0" : "-1"}" data-wb-section="${e(id)}">
                <span class="wb-section-index">${String(index + 1).padStart(2, "0")}</span>
                <span class="wb-section-tab-copy"><strong>${e(group.title || "配置模块")}</strong>
                  <small data-wb-section-count="${e(id)}">${changed ? `${changed} 项已改` : `${count} 项`}</small></span>
                ${icon("chevron-right")}
              </button>`;
            }).join("")}
            </nav>
          </aside>
          <div class="wb-section-panels">
            ${groups.map((group, index) => {
              const id = group.id || String(index);
              const active = id === activeGroup?.id;
              return `<div class="wb-section-panel" id="${state.instanceId}-panel-${e(id)}"
                  role="tabpanel" aria-labelledby="${state.instanceId}-tab-${e(id)}" tabindex="0"
                  data-wb-section-panel="${e(id)}"${active ? "" : " hidden"}>
                ${groupMarkup(group, index)}
              </div>`;
            }).join("")}
          </div>
        </div>`
      : groups.map(groupMarkup).join("");
    const body = s.status ? blocks
      : `<div class="wb-skeleton" role="status" aria-label="正在读取设置">`
        + `<div class="wb-skeleton-row"></div><div class="wb-skeleton-row"></div><div class="wb-skeleton-row"></div></div>`;
    const legend = !journal && s.status && container.layers.length > 1
      ? `<div class="wb-rail-legend">${container.layers.map(layer =>
          `<b>${e(LAYER_SHORT_LABELS[layer] || layer)}</b>`).join("<span>·</span>")}`
        + `</div>` : "";
     const pendingText = s.pending
       ? (s.pendingReason
         ? `保存结果尚未确认（${s.pendingReason}）。可能已经写入；核对会沿用同一个操作标识，不会重复保存。`
         : "保存结果尚未确认。可能已经写入；核对会沿用同一个操作标识，不会重复保存。")
       : "";
     const scopeLabel = s.status
      ? (container.scopeNote || (s.status.scope === "installation" ? "插件安装全局"
        : s.status.scope === "role" ? "安装与角色"
        /* 默认写窗口、但角色层同样可写的组（技能领域、日记与作息）：值是角色共享的，
           只有本会话覆盖才落在窗口层，所以不能只标「当前会话」。
           角色层之上还有关系层的组（主动联系偏好）由分区自己声明 scopeNote：
           默认这句会说成「角色共享」，漏掉真正管着这段关系的那一层。 */
        : (s.status.layers || []).includes("role") ? "角色共享 · 本会话可覆盖" : "当前会话"))
      : "";
    const apply = s.status?.apply_mode === "next_task" ? "下一个任务生效" : "立即生效";
    const heading = journal ? "" : `<header class="wb-card-head"><div class="wb-card-copy">`
      + `${titleAbove === container.title ? "" : `<h3>${e(container.title)}</h3>`}${container.note ? `<p>${e(container.note)}</p>` : ""}</div>`
      + (scopeLabel ? `<span class="wb-scope">${e(scopeLabel)} · ${e(apply)}</span>` : "")
      + `</header>`;
    return `<section class="wb-card${journal ? " wb-journal" : ""}" data-wb-card="${e(container.group)}">`
      + heading + `<div class="wb-card-body">`
       + (s.pending ? `<div class="wb-pending"><p>${e(pendingText)}</p>`
           + `<button type="button" class="button small" data-wb-action="retry"${s.busy ? " disabled" : ""}>核对原保存结果</button></div>` : "")
      + legend + body
      + `<div class="wb-actions">`
      + `<button type="button" class="wb-save${s.busy ? " is-busy" : ""}${s.savedState || ""}"`
      + ` data-wb-action="save"${s.locked() || Object.keys(s.conflicts).length ? " disabled" : ""}>`
      + `<span class="wb-save-label">${e(s.busy ? "保存中…" : s.stale ? "核对最新配置" : "保存")}</span>`
      + `<svg class="wb-ring" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7"></circle></svg>`
      + `<svg class="wb-check" viewBox="0 0 19 19" aria-hidden="true"><path d="M4 10l4 4 7-9"></path></svg>`
      + `</button>`
      + `<button type="button" class="button" data-wb-action="reset"${s.locked() || !s.dirty() ? " disabled" : ""}>使用已保存配置</button>`
      + (container.resettable
        ? `<button type="button" class="button" data-wb-action="inherit"${s.locked() ? " disabled" : ""}>全部恢复继承</button>`
        : "")
      + `<span class="wb-actions-note">${e(Object.keys(s.conflicts).length ? `${Object.keys(s.conflicts).length} 项修改待核对` : s.stale ? "改动已保留，待核对当前配置" : s.dirty() ? "有未保存的改动" : "与已保存一致")}</span>`
      + (journal && scopeLabel ? `<span class="wb-scope">${e(scopeLabel)}</span>` : "")
      + `</div></div></section>`;
  }

   function noticeMarkup(state) {
     /* 未确认保存已经有一个带操作按钮的待核对块；顶部再画同一句失败提示，
        用户会误以为发生了两次保存问题。真正的原因放进待核对块，保留一个入口。 */
     if (!state.notice || state.pending) return "";
    const tone = state.bad ? " is-error" : state.ok ? " is-ok" : "";
    return `<div class="wb-notice${tone}" role="status" aria-live="polite">`
      + `${icon(state.bad ? "alert-circle" : state.ok ? "check-circle-2" : "info")}`
      + `<span>${e(state.notice)}</span>`
      + (state.stale ? `<button type="button" class="button small" data-wb-action="reconcile"${state.busy || state.loading || state.pending ? " disabled" : ""}>核对最新配置</button>` : "")
      + `</div>`;
  }

  /* === 实例 === */
  function create(spec) {
    const {
      group, title, note = "", labels = {}, helps = {}, choices = {}, sections = null,
      catalogs = {}, rows = {}, inputs = {}, suggestions = {}, segmentedChoices = [],
      visibility = {}, listFields = {}, chipOptions = {}, navigationLimit = 8,
      presentation = "", sectionTabPrefix = "", initialSection = null,
      mount: mountPoint, endpoint, order = null, pollMs = 0, query = null,
      prefetch = null,
      audit = null, onStatus = null, scopeKey: scopeKeyOverride = null,
      /* 分组用一层继承链，但「值是谁共享的」不一定等于链上有没有角色层：
         主动联系偏好还有关系层，卡片右上角要说清这一点，所以允许分区声明自己的
         生效范围文案；不声明时仍按链自动判断。 */
      scopeNote = "",
      /* 有分区的读取键不是 `settings`、保存端点不是 `/configure`、
         令牌字段与重置字段也各有叫法。这些在 spec 里声明，不一处分叉一份内核。
         `resetKey` 默认不发送：只有真的支持恢复继承的分区才声明它，
         否则多带一个字段会被严格校验的分区直接拒掉。 */
      valuesKey = "settings", savePath = "/configure", requestSection = null,
      tokenKey = "edit_token", sendTokenKey = null, resetKey = null,
      /* 字段名在读取侧与写入侧可以不同：记忆分区读短名 `enabled`、
         写长名 `memory.enabled`（后端 `_normalize_patch` 按 field_id 查表，
         而它自己的测试就断言长名）。声明的函数把草稿名翻成网线上的名字。 */
      outbound = null,
      /* 旧枚举值在进入表单前归一到当前选项，避免已保存的数据被误判为非法。 */
      normalizeDraft = null,
    } = spec;
    const wireName = outbound || (name => name);
    /* 读取快照时先把词条字段收敛成编辑器写出的 JSON 数组串，draft 与 base 都从
       这份值派生。只归一草稿会让默认的换行串永远显示成「有改动」，保存时还会
       把默认值当成新值重发；只归一默认值又会让快捷添加拿到空列表（5 类变 1 类）。 */
    const normalizeWordLists = (values, fields = state.container?.fields || {}) => {
      for (const [name, item] of Object.entries(fields)) {
        if (item?.input === "word-list" && typeof values?.[name] === "string") {
          values[name] = JSON.stringify(parseStringList(values[name]));
        }
      }
      return values;
    };
    const draftFrom = (settings, status) => {
      const draft = {...settings};
      if (normalizeDraft) normalizeDraft(draft, status);
      return draft;
    };
    /* 读取侧与发送侧的令牌字段名可以不同：配置分区是 `edit_token` 收发同名，
       记忆分区读 `edit_token`、发 `expected_edit_token`。只声明一个的话，
       总有一侧拼错——读取静默落空（invalid_response），或保存被后端拒掉。 */
    const writeTokenKey = sendTokenKey || tokenKey;
    const state = {
       status: null, draft: null, base: null, pending: null, pendingReason: "",
      busy: false, loading: false, denied: false, stale: false, conflicts: {},
      notice: "", bad: false, ok: false, savedState: "", ticket: 0,
      drawRails: true, container: null, timer: null,
      instanceId: `wb-${++instanceSequence}`, activeGroupId: initialSection, resetFields: [],
      visibleGroupIds: null,
    };
    /* 被显式「恢复继承」的字段：保存时要作为 reset_to_inherit 发出去，
       而不是留一份与继承值相同的显式副本。 */
    let resets_ = [];
    let autoRecoveryKey = "";
    let autoRecoveryTimer = null;

    const locked = () => state.busy || state.loading || !!state.pending || !state.status || state.denied;
    const dirty = () => !!state.draft && !!state.base
      && (resets_.length > 0 || Object.keys(state.conflicts).length > 0
        || Object.keys(state.draft).some(name => state.draft[name] !== state.base[name]));
    state.locked = locked;
    state.dirty = dirty;

     const storageKey = () => {
     const identity = window.CompanionProduct?.identity?.() || {};
      let scope = "";
      try {
        scope = state.status?.scope === "installation" ? "installation"
          : typeof scopeKeyOverride === "string" && scopeKeyOverride
            ? scopeKeyOverride : typeof window.CompanionProduct?.scopeKey === "function"
              ? String(window.CompanionProduct.scopeKey() || "") : "";
       } catch (_) {}
       return STORE + JSON.stringify([identity.installation_id, identity.operator_id, scope, group]);
     };
    const savePending = () => {
      try {
        if (state.pending) sessionStorage.setItem(storageKey(), JSON.stringify(state.pending));
        else sessionStorage.removeItem(storageKey());
      } catch {}
    };
    const restorePending = () => {
      try {
        const value = JSON.parse(sessionStorage.getItem(storageKey()));
        if (value && typeof value.operation_key === "string" && value.operation_key
            && typeof value.edit_token === "string" && value.edit_token.length === 64
            && value.changes && Object.keys(value.changes).length) {
          const restored = requestSection ? {...value, section: group} : {...value};
          if (typeof restored.pending_reason === "string") {
            restored.pending_reason = restored.pending_reason.slice(0, 512);
          } else {
            delete restored.pending_reason;
          }
          if (typeof restored.pending_reason_code !== "string"
              || !/^[a-z][a-z0-9_]{2,79}$/.test(restored.pending_reason_code)) {
            delete restored.pending_reason_code;
          }
          return restored;
        }
      } catch {}
      return null;
    };

    function recoverConfirmedConflict(pending) {
      const confirmedConflict = pending?.pending_reason_code === "configuration_revision_conflict"
        || pending?.pending_reason === errors.configuration_revision_conflict;
      if (!confirmedConflict) return false;
      const changes = pending.changes || {};
      const resets = Array.isArray(pending.reset_to_inherit) ? pending.reset_to_inherit : [];
      const names = [...Object.keys(changes), ...resets];
      if (!names.every(name => Object.hasOwn(state.draft || {}, name))) return false;
      for (const [name, value] of Object.entries(changes)) state.draft[name] = value;
      for (const name of resets) {
        state.draft[name] = state.base[name];
        if (!resets_.includes(name)) resets_.push(name);
      }
      state.pending = null;
      state.pendingReason = "";
      savePending();
      clearAutoRecovery(pending.operation_key);
      state.stale = false;
      state.ok = false;
      return true;
    }

    function scheduleAutoRecovery(operationKey, ticket = state.ticket) {
      if (!operationKey || state.pending?.operation_key !== operationKey || autoRecoveryKey === operationKey) return;
      if (autoRecoveryTimer !== null) clearTimeout(autoRecoveryTimer);
      autoRecoveryKey = operationKey;
      autoRecoveryTimer = setTimeout(() => {
        autoRecoveryTimer = null;
        if (ticket !== state.ticket || state.pending?.operation_key !== operationKey
            || state.busy || state.loading || state.denied) return;
        void save(true);
      }, 400);
    }

    function clearAutoRecovery(operationKey) {
      if (autoRecoveryKey !== operationKey) return;
      if (autoRecoveryTimer !== null) clearTimeout(autoRecoveryTimer);
      autoRecoveryTimer = null;
      autoRecoveryKey = "";
    }

    /* 容器形状：字段顺序、契约、继承链、本次写入层 */
    function buildContainer(status) {
      const definitions = status.fields || {};
      const names = order && order.length ? order.filter(name => definitions[name])
        : Object.keys(definitions);
      const fields = {};
      for (const name of names) {
        fields[name] = contract(name, definitions[name], choices[name] || null);
        fields[name].label = labels[name] || name;
        fields[name].help = helps[name] || "";
        fields[name].writeLayer = definitions[name].write_layer || "";
        /* 目录型控件（选项来自读取结果，如 TTS 扩展）与多行文本框行数。 */
        if (typeof catalogs[name] === "function") fields[name].catalog = catalogs[name];
        if (rows[name]) fields[name].rows = rows[name];
        /* 控件类型：默认按 kind 决定，`inputs` 覆盖成 time / line。
           time 值仍是字符串（'23:50'），所以 kind 保持 text，只换控件。 */
       if (inputs[name]) fields[name].input = inputs[name];
       if (Array.isArray(suggestions[name])) fields[name].suggestions = suggestions[name];
        if (segmentedChoices.includes(name)) fields[name].segmented = true;
        if (visibility[name]) fields[name].visibleWhen = {...visibility[name]};
        if (listFields[name]) fields[name].listPolicy = {...listFields[name]};
        /* 词条快选的候选与模式（single/multi）：候选只是往 data-wb-field 里填值。 */
        if (chipOptions[name]) fields[name].chipOptions = {...chipOptions[name]};
      }
      /* 段落：spec.sections 声明 [{id, title, help, fields}]。
         没声明时退化成一段，标题留空——单段不强加小标题。
         段落之外的字段（若 order 里有而 sections 没提）附到最后一段，
         免得声明漏一个字段就把它从界面上弄丢。 */
      const declared = new Set();
      const groups = [];
      for (const [index, group] of (Array.isArray(sections) ? sections : []).entries()) {
        const members = (group.fields || []).filter(name => fields[name]);
        members.forEach(name => declared.add(name));
        if (members.length) {
          groups.push({id: group.id || `g${index}`, title: group.title || "", help: group.help || "",
                       fields: members});
        }
      }
      const rest = names.filter(name => !declared.has(name));
      if (rest.length) {
        if (groups.length) groups[groups.length - 1].fields.push(...rest);
        else groups.push({id: "all", title: "", help: "", fields: rest});
      }
      const layers = Array.isArray(status.layers) && status.layers.length
        ? status.layers : ["installation", "role", "window"];
      return {
        group, title, note, order: names, fields, layers, groups, scopeNote, navigationLimit,
        presentation, sectionTabPrefix,
        writeLayer: layers[layers.length - 1],
        /* 只有声明了 resetKey 的分区才提供「全部恢复继承」：
           没有这条协议的分区点了会写出错误请求。 */
        resettable: Boolean(resetKey),
        drawRails: state.drawRails,
      };
    }

    function refreshCatalogs() {
      if (!state.status || !state.container) return;
      state.container = buildContainer(state.status);
      paint();
    }

    async function api(method, path, body, query) {
      let timer;
      try {
        /* 等桥注进来，别一眼看空就报「未连接」：桥的注入晚于脚本执行是正常时序。
           写法必须是 `bridgeNow() || await bridgeFor()`，不能是对 `whenBridgeReady`
           用 `??`——`??` 的右半边会立刻求值，product 没提供等待器时捕到的是
           undefined，接着 bridge?.apiGet 判空就报「没接上」，而真正该发生的
           「去等」根本没发生。 */
        const product = window.CompanionProduct;
        const bridge = product?.bridgeNow?.() || (product?.bridgeFor
          ? await product.bridgeFor() : window.AstrBotPluginPage);
        if (!bridge?.apiGet) throw error("bridge_unavailable");
        const route = relative(path);
        /* 发给桥的必须是**插件相对**路径：桥的 endpoint 不带前导斜杠、也不带插件
           目录名，宿主自己拿插件名与它拼成真实路由。这里原来发的是拼回全路径的
           `path`（`route` 算完就被丢掉），于是宿主再拼一次插件名 →
           「未找到该路由」+「Plugin bridge endpoint is invalid.」。
           同一约定见 product-page.js 的 relative() 注释。 */
        const response = await Promise.race([
          method === "GET" ? bridge.apiGet(route, query || body) : bridge.apiPost(route, body),
          new Promise((_, reject) => {timer = setTimeout(() => reject(error("bridge_timeout")), 15000);}),
        ]);
        const wrapped = isEnvelope(response) ? response : isEnvelope(response?.data) ? response.data : null;
        if (wrapped?.status === "error") throw error(codeOf(wrapped) || "invalid_response", true);
        let value = wrapped ? wrapped.data : response;
        /* Some Dashboard bridge versions leave one anonymous {data: ...} wrapper. */
        if (!wrapped && response && typeof response === "object" && !Array.isArray(response)
            && !hasOwn(response, "status") && hasOwn(response, "data")
            && response.data && typeof response.data === "object" && !Array.isArray(response.data)) {
          value = response.data;
        }
        if (!value || typeof value !== "object" || Array.isArray(value)) throw error("invalid_response");
        if (method !== "GET") clearReadCache();
        return value;
      } finally { clearTimeout(timer); }
    }

    /* 说明与错误接到控件上。屏幕阅读器停在某个输入框时，
       现在能一起读出「这一项是什么、为什么无效」。
       放在 paint 之后统一做，是因为控件由七八个分支生成（数字 / 文本 / 多行 /
       枚举 / 开关 / 日期 / 词条），逐分支插属性迟早漏一个。 */    function wireFieldDescriptions(root) {
      for (const row of root.querySelectorAll(".wb-field")) {
        const control = row.querySelector("[data-wb-field]")
          || row.querySelector(".wb-control :is(input,textarea,select,button)");
        if (!control) continue;
        const ids = [row.querySelector(".wb-help[id]"), row.querySelector("[data-wb-field-error][id]")]
          .filter(Boolean).map(node => node.id);
        if (ids.length) control.setAttribute("aria-describedby", ids.join(" "));
        /* 重绘会抹掉输入时写上的 aria-invalid，这里按当前草稿重新判定。 */
        const name = row.dataset.wbRow;
        const item = state.container?.fields[name];
        if (item && row.classList.contains("is-invalid")) control.setAttribute("aria-invalid", "true");
      }
    }

    function fieldRow(name) {
      const rows = mountPoint()?.querySelectorAll?.("[data-wb-row]") || [];
      return [...rows].find(row => row.dataset.wbRow === name) || null;
    }

    /* 把「不合法」翻译成人话。原来所有情况共用一句「请核对数值类型与范围。」：
       超字数的字段是文本、跟数值范围无关，开关填错类型也跟范围无关，
       用户照那句话核对不出来。按字段类型分情形说，并带上标签与具体边界。 */
    function explainInvalid(item, value) {
      const label = item?.label || item?.name || "这一项";
      const count = Array.from(String(value ?? "")).length;
      if (item?.maximumChars != null && count > item.maximumChars) {
        return `${label}已超过 ${item.maximumChars} 字上限（当前 ${count} 字）。`;
      }
      if (item?.kind === "bool") {
        return `${label}只能是开或关。`;
      }
      if (item?.kind === "int" || item?.kind === "float") {
        if (typeof value !== "number" || !Number.isFinite(value)) {
          return `${label}要填数字（当前填的不是数字）。`;
        }
        if (item?.hasBounds) {
          return `${label}要填 ${toDisplay(item, item.minimum)} 到 ${toDisplay(item, item.maximum)} 之间的数。`;
        }
        return `${label}要填数字${item?.decimals ? `，最多 ${item.decimals} 位小数` : "，不能带小数"}。`;
      }
      if (item?.hasBounds) {
        return `${label}的取值要在 ${toDisplay(item, item.minimum)} 到 ${toDisplay(item, item.maximum)} 之间。`;
      }
      if (typeof value !== "string") {
        return `${label}要填文本（当前填的不是文本）。`;
      }
      return `${label}的取值不合法，请核对。`;
    }

    function markInvalid(name, message, {jump = false} = {}) {
      const row = fieldRow(name);
      if (!row) return;
      row.classList.add("is-invalid");
      const slot = row.querySelector("[data-wb-field-error]");
      if (slot) slot.textContent = message;
      const control = row.querySelector("[data-wb-field]");
      control?.setAttribute?.("aria-invalid", "true");
      if (!jump) return;
      /* 保存按钮在表单底部，出错的字段可能在顶部：不跳过去的话，
         用户按完保存看到的只是「什么都没发生」。 */
      row.scrollIntoView?.({block: "center", behavior: REDUCED() ? "auto" : "smooth"});
      control?.focus?.({preventScroll: true});
    }

    function clearInvalid(name) {
      const row = fieldRow(name);
      if (!row || !row.classList.contains("is-invalid")) return;
      row.classList.remove("is-invalid");
      const slot = row.querySelector("[data-wb-field-error]");
      if (slot) slot.textContent = "";
      row.querySelector("[data-wb-field]")?.setAttribute?.("aria-invalid", "false");
    }

    function paint() {
      const root = mountPoint();
      if (!root) return;
      if (!state.status && !state.loading) {
        root.innerHTML = noticeMarkup(state);
        return;
      }
      if (!state.container) {
        root.innerHTML = `<div class="wb">${noticeMarkup(state)}`
          + `<p class="wb-notice">${icon("info")}<span>正在读取设置…</span></p></div>`;
        return;
      }
      const wasDrawing = state.drawRails;
      state.resetFields = resets_;
      root.innerHTML = `<div class="wb">${noticeMarkup(state)}${conflictMarkup()}${cardMarkup(state, state.container, root.closest?.(".console-section, .decision-stage")?.querySelector?.(":scope > header h2")?.textContent?.trim())}</div>`;
      /* 层轨绘制只做一次：重绘之后不再重播。 */
      if (wasDrawing && state.status) {
        state.drawRails = false;
        const rails = root.querySelectorAll(".wb-rail");
        rails.forEach((node, index) => {
          node.classList.add("is-drawing");
          node.style.animationDelay = `${Math.min(index, 8) * 22}ms`;
        });
        setTimeout(() => rails.forEach(node => node.classList.remove("is-drawing")), 900);
      }
      window.lucide?.createIcons({nodes: root.querySelectorAll("[data-lucide]")});
      wireFieldDescriptions(root);
      /* 台词气泡的 textarea 按内容自动长高；重绘后统一量一次。 */
      growQuotes(root);
    }

    function conflictMarkup() {
      const names = Object.keys(state.conflicts);
      if (!names.length) return "";
      const text = (name, value) => {
        const item = state.container?.fields[name];
        if (value === undefined) return "此项已移除";
        if (typeof value === "boolean") return value ? "已开启" : "已关闭";
        if (value === "") return "未填写";
        return String(item?.choices?.[value] ?? value);
      };
      return `<section class="wb-conflicts" aria-label="核对同时修改的配置">`
        + `<p>这些设置也在其他页面改过，请逐项选择要保留的值。</p>`
        + names.map(name => `<div class="wb-conflict"><strong>${e(labels[name] || name)}</strong>`
          + `<div class="wb-conflict-values"><span>当前已保存：${e(text(name, state.base[name]))}</span>`
          + `<span>我的修改：${e(resets_.includes(name) ? "恢复继承" : text(name, state.draft[name]))}</span></div>`
          + `<div class="wb-conflict-actions"><button type="button" class="button small" data-wb-conflict="${e(name)}" data-wb-choice="current"${locked() ? " disabled" : ""}>使用当前值</button>`
          + (state.container?.fields[name] ? `<button type="button" class="button small" data-wb-conflict="${e(name)}" data-wb-choice="draft"${locked() ? " disabled" : ""}>保留我的修改</button>` : "")
          + `</div></div>`).join("") + `</section>`;
    }

    function resolveConflict(name, choice) {
      if (locked() || !Object.hasOwn(state.conflicts, name)) return;
      if (choice === "current") {
        if (Object.hasOwn(state.base, name)) state.draft[name] = state.base[name];
        else delete state.draft[name];
        resets_ = resets_.filter(field => field !== name);
      } else if (choice !== "draft" || !state.container?.fields[name]) return;
      delete state.conflicts[name];
      state.bad = Object.keys(state.conflicts).length > 0;
      state.notice = state.bad ? "还有同时修改的设置需要核对。"
        : dirty() ? "核对完成，保存后应用你的修改。" : "核对完成，已使用当前保存的配置。";
      paint();
    }

    function focusSection(id) {
      const tabs = mountPoint()?.querySelectorAll?.("[data-wb-section]") || [];
      [...tabs].find(tab => tab.dataset.wbSection === id)?.focus?.({preventScroll: true});
    }

    function selectSection(id, focus = false) {
      const groups = state.container?.groups || [];
      if (presentation === "journal" && !state.container) {
        state.activeGroupId = id;
        return;
      }
      if (!groups.some(group => group.id === id)
          || (state.visibleGroupIds && !state.visibleGroupIds.has(id))
          || state.activeGroupId === id) return;
      state.activeGroupId = id;
      if (presentation === "journal") {
        const panels = mountPoint()?.querySelectorAll?.("[data-wb-section-panel]") || [];
        for (const panel of panels) panel.hidden = panel.dataset.wbSectionPanel !== id;
        window.CompanionPageMotion?.reveal([...panels].find(panel=>!panel.hidden));
        return;
      }
      paint();
      window.CompanionPageMotion?.reveal(mountPoint()?.querySelector?.('[data-wb-section-panel]:not([hidden])'));
      if (focus) focusSection(id);
    }

    function setVisibleGroups(ids, preferredId = "") {
      const requested = Array.isArray(ids) ? new Set(ids.map(String)) : null;
      state.visibleGroupIds = requested?.size ? requested : null;
      const groups = state.container?.groups || [];
      const visible = state.visibleGroupIds
        ? groups.filter(group => state.visibleGroupIds.has(group.id)) : groups;
      if (visible.length) {
        if (!visible.some(group => group.id === state.activeGroupId)) {
          state.activeGroupId = visible.some(group => group.id === preferredId)
            ? preferredId : visible[0].id;
        }
      } else if (!groups.length && preferredId) {
        state.activeGroupId = preferredId;
      }
      if (state.container) paint();
      return true;
    }

    function onSectionKeydown(event) {
      /* Ctrl/Command+S 保存当前分区。
       * 表单有二十多个字段时，键盘用户改完一项要一路 Tab 到按钮；
       * 而浏览器自己的「保存网页」对话框会在这个习惯动作里弹出来。
       * 并进同一个 keydown 监听器，而不是再挂一个：同一个根元素上挂两个
       * keydown，宿主与测试里的监听器表都只能存住后一个（真实的浏览器不会，
       * 但两种环境行为不一致本身就是隐患）。 */
      if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey
          && String(event.key).toLowerCase() === "s") {
        event.preventDefault();
        if (!locked()) void save();
        return;
      }
      const wordInput = event.target.closest?.("[data-wb-word-input]");
      if (wordInput && event.key === "Enter" && !event.isComposing) {
        event.preventDefault();
        addWord(wordInput.dataset.wbWordInput, wordInput.value);
        return;
      }
      const tagInput = event.target.closest?.("[data-wb-tag-input]");
      if (tagInput && event.key === "Enter" && !event.isComposing) {
        event.preventDefault();
        addTag(tagInput.dataset.wbTagInput, tagInput.value);
        return;
      }
      const replacementFrom = event.target.closest?.("[data-wb-replacement-from]");
      const replacementTo = event.target.closest?.("[data-wb-replacement-to]");
      if (event.key === "Enter" && !event.isComposing && (replacementFrom || replacementTo)) {
        event.preventDefault();
        if (replacementFrom) {
          const to = replacementFrom.closest(".wb-replacement-form")?.querySelector("[data-wb-replacement-to]");
          to?.focus?.();
        } else {
          addReplacement(replacementTo.dataset.wbReplacementTo);
        }
        return;
      }
      const tab = event.target.closest?.("[data-wb-section]");
      if (!tab) return;
      const groups = state.container?.groups || [];
      const index = groups.findIndex(group => group.id === tab.dataset.wbSection);
      if (index < 0) return;
      let next = index;
      if (event.key === "ArrowDown" || event.key === "ArrowRight") next = (index + 1) % groups.length;
      else if (event.key === "ArrowUp" || event.key === "ArrowLeft") next = (index + groups.length - 1) % groups.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = groups.length - 1;
      else return;
      event.preventDefault();
      selectSection(groups[next].id, true);
    }

    function updateSectionCounts() {
      const root = mountPoint();
      if (!root || !state.container) return;
      root.querySelectorAll("[data-wb-section-count]").forEach(node => {
        const group = state.container.groups.find(item => item.id === node.dataset.wbSectionCount);
        if (!group) return;
        const changed = group.fields.filter(name => state.draft?.[name] !== state.base?.[name]
          || resets_.includes(name)).length;
        const count = group.fields.filter(name => state.container.fields[name]).length;
        node.textContent = changed ? `${changed} 项已改` : `${count} 项`;
        node.closest("[data-wb-section]")?.classList.toggle("has-changes", changed > 0);
      });
    }

    /* 悬停：亮起该层，其余退后。用 data 属性驱动，交给 CSS。
       具名函数，这样 unmount 能真的把它们摘掉——匿名闭包摘不掉，
       宿主换绑到同一个元素上时就会越叠越多份。 */
    function onHover(event) {
      const node = event.target.closest?.(".wb-node");
      if (!node) return;
      const row = node.closest(".wb-field");
      if (!row) return;
      row.dataset.focusLayer = node.dataset.layer || "";
      row.querySelectorAll(".wb-node").forEach(item =>
        item.classList.toggle("is-focus", item === node));
    }

    function onUnhover(event) {
      const node = event.target.closest?.(".wb-node");
      if (!node) return;
      const row = node.closest(".wb-field");
      if (!row) return;
      delete row.dataset.focusLayer;
      row.querySelectorAll(".wb-node").forEach(item => item.classList.remove("is-focus"));
    }

    /* 已绑定的宿主元素。用实例级的 WeakSet 而不是元素上的 data 属性：
       data 标记在「同一个元素、内容被重写」时会把重新绑定误判成重复绑定，
       而 WeakSet 既不泄漏，也跟元素一起消失。 */
    const bound = new WeakSet();

    function bind() {
      const root = mountPoint();
      if (!root || bound.has(root)) return;
      bound.add(root);
      root.addEventListener("input", onInput);
      root.addEventListener("change", onInput);
      root.addEventListener("click", onClick);
      root.addEventListener("keydown", onSectionKeydown);
      root.addEventListener("mouseover", onHover);
      root.addEventListener("mouseout", onUnhover);
    }

    function unbind() {
      const root = mountPoint();
      if (!root || !bound.has(root)) return;
      bound.delete(root);
      root.removeEventListener("input", onInput);
      root.removeEventListener("change", onInput);
      root.removeEventListener("click", onClick);
      root.removeEventListener("keydown", onSectionKeydown);
      root.removeEventListener("mouseover", onHover);
      root.removeEventListener("mouseout", onUnhover);
    }



    function onInput(event) {
      const el = event.target;
      /* Native choice controls emit `input` before `change`. Repainting here
         removes the active radio before its change event reaches this owner. */
      if (event.type === "input" && (el?.type === "radio" || el?.type === "checkbox")) return;
      /* 生日与台词气泡的辅助控件：先把它们的改动翻译回 data-wb-field 的字符串，
         再走下面同一条通用路径（草稿、脏标记、计数全部复用）。 */
      if (el.dataset?.wbDate) { composeDateField(el.dataset.wbDate); return; }
      if (el.dataset?.wbDateText) {
        const group = el.closest("[data-wb-date-group]");
        const parsed = parseBirthday(el.value);
        if (parsed) {
          syncDateControlsFromText(group, parsed);
          setDateGroupError(group, birthdayPartsError(parsed));
        } else {
          clearDateControls(group);
          setDateGroupError(group, "");
        }
      }
      if (el.dataset?.wbQuoteLine !== undefined) {
        el.style.height = "auto";
        el.style.height = `${el.scrollHeight}px`;
        composeQuotes(el.dataset.wbQuoteLine);
        return;
      }
      const name = el.dataset?.wbField;
      if (!name || !state.draft || !state.container?.fields[name]) return;
      const item = state.container.fields[name];
      if (item.input === "source-checklist" && el.type === "checkbox") {
        const control = el.closest(".wb-source-picker");
        const baseId = el.dataset.wbSourceBase;
        const docKbId = el.dataset.wbSourceDocument;
        if (el.checked && baseId) {
          [...(control?.querySelectorAll("[data-wb-source-document]") || [])]
            .filter(input => input.dataset.wbSourceDocument === baseId)
            .forEach(input => { input.checked = false; });
        } else if (el.checked && docKbId) {
          const base = [...(control?.querySelectorAll("[data-wb-source-base]") || [])]
            .find(input => input.dataset.wbSourceBase === docKbId);
          if (base) base.checked = false;
        }
        const options = [...(control?.querySelectorAll("[data-wb-source-id]:checked") || [])];
        const maximum = Number(state.draft.source_limit || 16);
        if (options.length > maximum) {
          el.checked = false;
          state.notice = `最多读取 ${maximum} 个来源。`;
          state.bad = true; state.ok = false;
          paint();
          return;
        }
        const raw = options.map(input => input.dataset.wbSourceId).join("\n");
        state.draft[name] = raw;
        state.savedState = ""; state.ok = false;
        const row = el.closest(".wb-field");
        if (row) row.classList.toggle("is-dirty", state.base && raw !== state.base[name]);
        const count = control?.querySelector(".wb-source-count");
        if (count) count.textContent = `已选 ${options.length} / ${maximum} 项`;
        return;
      }
      if (item.input === "identity-checklist" && el.type === "checkbox") {
        const control = el.closest(".wb-identity-picker");
        const selected = [...(control?.querySelectorAll("[data-wb-identity-id]:checked") || [])]
          .map(input => input.dataset.wbIdentityId);
        const raw = JSON.stringify(selected);
        state.draft[name] = raw;
        state.savedState = ""; state.ok = false;
        const row = el.closest(".wb-field");
        if (row) row.classList.toggle("is-dirty", state.base && raw !== state.base[name]);
        const count = control?.querySelector(".wb-source-count");
        if (count) count.textContent = `已选 ${selected.length} 项`;
        return;
      }
      const raw = item.kind === "bool" ? el.checked
        : el.tagName === "SELECT" ? coerceChoice(item, el.value)
          : item.kind === "text" && item.input === "line-list" ? JSON.stringify(parseLineList(el.value))
            : item.kind === "text" ? el.value
            : fromDisplay(item, el.valueAsNumber);
      state.draft[name] = raw;
      state.savedState = "";
      state.ok = false;
      const affectsVisibility = Object.values(state.container.fields)
        .some(field => field.visibleWhen && Object.hasOwn(field.visibleWhen, name));
      if (affectsVisibility) {
        const restoreChoiceFocus = el.type === "radio" || el.type === "checkbox";
        paint();
        if (restoreChoiceFocus) {
          const next = [...(mountPoint()?.querySelectorAll?.("[data-wb-field]") || [])]
            .find(control => control.dataset.wbField === name
              && (el.type !== "radio" || String(control.value) === String(raw)));
          next?.focus?.({preventScroll: true});
        }
        return;
      }
      /* 局部更新：不重绘整卡，避免输入框失去焦点。 */
      const row = el.closest(".wb-field");
      if (row) row.classList.toggle("is-dirty", state.base && raw !== state.base[name]);
      if (item.input === "chips") refreshChipset(name);
      if (row && item.input === "line-list") {
        const words = parseStringList(raw) || [];
        const policy = item.listPolicy || {};
        const count = row.querySelector(".wb-line-list-count");
        if (count) count.textContent = `${words.length} / ${policy.maxItems || 80} 项 · 每行一个，<newline> 表示换行`;
        el.setAttribute("aria-invalid", String(!valid(item, raw)));
      }
      if (row && item.maximumChars != null) {
        const count = Array.from(String(raw ?? "")).length;
        const counter = row.querySelector("[data-wb-counter]");
        if (counter) {
          counter.textContent = `${count} / ${item.maximumChars} 字`;
          counter.classList.toggle("is-over-limit", count > item.maximumChars);
        }
        el.setAttribute("aria-invalid", String(count > item.maximumChars));
      }
      const toggle = el.closest(".wb-switch")?.querySelector(".wb-switch-state");
      if (toggle) toggle.textContent = raw ? "已开启" : "已关闭";
      /* 已经被标红的字段：改对了就立刻撤掉标记，改得还不合法就把提示换成
         当前这句（例如字数从 340 降到 330，提示要跟着变），
         没标红的字段不在输入过程中突然变红——边打字边报错很吵。 */
      if (row?.classList.contains("is-invalid")) {
        if (valid(item, raw)) clearInvalid(name);
        else {
          const message = explainInvalid(item, raw);
          const slot = row.querySelector("[data-wb-field-error]");
          if (slot) slot.textContent = message;
          state.notice = `${message}改动还在，改好这一项就能保存。`;
          const noticeNode = mountPoint()?.querySelector?.(".wb-notice span");
          if (noticeNode) noticeNode.textContent = state.notice;
        }
      }
      const actions = mountPoint()?.querySelector(".wb-actions");
      if (actions) {
        const note = actions.querySelector(".wb-actions-note");
        if (note) note.textContent = dirty() ? "有未保存的改动" : "与已保存一致";
        const reset = actions.querySelector('[data-wb-action="reset"]');
        if (reset) reset.disabled = !dirty();
      }
      updateSectionCounts();
    }

    function onClick(event) {
      const conflict = event.target.closest("[data-wb-conflict]");
      if (conflict) { resolveConflict(conflict.dataset.wbConflict, conflict.dataset.wbChoice); return; }
      const clearDate = event.target.closest("[data-wb-date-clear]");
      if (clearDate) { composeDateField(clearDate.dataset.wbDateClear, true); return; }
      const section = event.target.closest("[data-wb-section]");
      if (section) { selectSection(section.dataset.wbSection, true); return; }
      const removeWord = event.target.closest("[data-wb-word-remove]");
      if (removeWord) {
        removeWordAt(removeWord.dataset.wbWordRemove, Number(removeWord.dataset.wbWordIndex));
        return;
      }
      const addWordButton = event.target.closest("[data-wb-word-add]");
      if (addWordButton) {
        const input = addWordButton.closest(".wb-field")?.querySelector("[data-wb-word-input]");
        if (input) addWord(addWordButton.dataset.wbWordAdd, input.value);
        return;
      }
      const specialWord = event.target.closest("[data-wb-word-special]");
      if (specialWord) {
        const value = {newline: "\n", space: " ", tab: "\t"}[specialWord.dataset.wbWordSpecial];
        if (value !== undefined) addWord(specialWord.dataset.wbWordField, value, true);
        return;
      }
      const quickWord = event.target.closest("[data-wb-word-value]");
      if (quickWord) {
        addWord(quickWord.dataset.wbWordField, quickWord.dataset.wbWordValue);
        return;
      }
      const addReplacementButton = event.target.closest("[data-wb-replacement-add]");
      if (addReplacementButton) { addReplacement(addReplacementButton.dataset.wbReplacementAdd); return; }
      const removeReplacementButton = event.target.closest("[data-wb-replacement-remove]");
      if (removeReplacementButton) {
        removeReplacementAt(removeReplacementButton.dataset.wbReplacementRemove,
          Number(removeReplacementButton.dataset.wbReplacementIndex));
        return;
      }
      const chipOption = event.target.closest("[data-wb-chip]");
      if (chipOption) { applyChipOption(chipOption.dataset.wbChip, chipOption.dataset.wbChipValue); return; }
      const tagAdd = event.target.closest("[data-wb-tag-add]");
      if (tagAdd) {
        const input = tagAdd.closest(".wb-tag-form")?.querySelector("[data-wb-tag-input]");
        if (input) addTag(tagAdd.dataset.wbTagAdd, input.value);
        return;
      }
      const tagRemove = event.target.closest("[data-wb-tag-remove]");
      if (tagRemove) {
        removeTagAt(tagRemove.dataset.wbTagRemove, Number(tagRemove.dataset.wbTagIndex));
        return;
      }
      const quoteAdd = event.target.closest("[data-wb-quote-add]");
      if (quoteAdd) { addQuote(quoteAdd.dataset.wbQuoteAdd); return; }
      const quoteRemove = event.target.closest("[data-wb-quote-remove]");
      if (quoteRemove) {
        removeQuote(quoteRemove.dataset.wbQuoteRemove, Number(quoteRemove.dataset.wbQuoteIndex));
        return;
      }
      const orderStart = event.target.closest("[data-wb-order-start]");
      if (orderStart) {
        updateOrder(orderStart.dataset.wbOrderStart, COMPONENT_ORDER, "已开启自定义组件顺序。", 0);
        return;
      }
      const orderMove = event.target.closest("[data-wb-order-move]");
      if (orderMove) {
        moveOrder(Number(orderMove.dataset.wbOrderIndex), orderMove.dataset.wbOrderMove);
        return;
      }
      const orderRemove = event.target.closest("[data-wb-order-remove]");
      if (orderRemove) {
        removeOrder(orderRemove.dataset.wbOrderRemove, Number(orderRemove.dataset.wbOrderIndex));
        return;
      }
      const orderAdd = event.target.closest("[data-wb-order-add]");
      if (orderAdd) {
        const select = orderAdd.closest(".wb-order-tools")?.querySelector("[data-wb-order-add-select]");
        addOrder(orderAdd.dataset.wbOrderAdd, select?.value);
        return;
      }
      const orderNatural = event.target.closest("[data-wb-order-natural]");
      if (orderNatural) {
        updateOrder(orderNatural.dataset.wbOrderNatural, [], "已恢复自然顺序。", null);
        return;
      }
      const reset = event.target.closest("[data-wb-reset]");
      if (reset) { revertField(reset.dataset.wbReset); return; }
      const action = event.target.closest("[data-wb-action]")?.dataset.wbAction;
      if (!action) return;
      if (action === "save") { void save(false); return; }
      if (action === "retry") { void save(true); return; }
      if (action === "reconcile") { void refresh(); return; }
      if (action === "reset") {
        if (state.stale) { void refresh({discardDraft: true}); return; }
        state.base = {...state.status.settings};
        state.draft = draftFrom(state.status.settings, state.status);
        state.conflicts = {};
        resets_ = [];
        state.notice = "已恢复为当前已保存配置。";
        state.bad = false; state.ok = false;
        paint();
      }
      if (action === "inherit") {
        /* 全部字段恢复继承：draft 回到已保存值，并整体标记为待重置。 */
        resets_ = Object.keys(state.draft);
        state.notice = "已标记为全部恢复继承；保存后这些字段由低层配置决定。";
        state.bad = false; state.ok = false;
        paint();
      }
    }

    function addWord(name, rawWord, preserveWhitespace = false) {
      if (!state.draft || locked()) return;
      const item = state.container?.fields[name];
      if (!item || item.input !== "word-list") return;
      const policy = item.listPolicy || {};
      const word = preserveWhitespace ? String(rawWord ?? "")
        : policy.trim ? String(rawWord ?? "").trim() : String(rawWord ?? "");
      if (!word.length) return;
      if (word.length > (policy.maxLength || 200)) {
        state.notice = `每个词条最多 ${policy.maxLength || 200} 个字符。`;
        state.bad = true; state.ok = false; paint();
        return;
      }
      const words = parseStringList(state.draft[name]) || [];
      const duplicate = policy.unique === "casefold"
        ? words.some(value => value.toLocaleLowerCase() === word.toLocaleLowerCase())
        : words.includes(word);
      if (duplicate) {
        state.notice = "这个词条已经在列表中。";
        state.bad = false; state.ok = false; paint();
        return;
      }
      if (words.length >= (policy.maxItems || 80)) {
        state.notice = `这个列表最多添加 ${policy.maxItems || 80} 项。`;
        state.bad = true; state.ok = false; paint();
        return;
      }
      words.push(word);
      updateWordList(name, words, `已添加“${wordLabel(word)}”。`, true);
    }

    function removeWordAt(name, index) {
      if (!state.draft || locked()) return;
      const item = state.container?.fields[name];
      if (!item || item.input !== "word-list") return;
      const words = parseStringList(state.draft[name]) || [];
      if (!Number.isInteger(index) || index < 0 || index >= words.length) return;
      if (words.length <= ((item.listPolicy || {}).minItems || 0)) return;
      const [removed] = words.splice(index, 1);
      updateWordList(name, words, `已移除“${wordLabel(removed)}”。`, false);
    }

    function updateWordList(name, words, notice, focus) {
      state.draft[name] = JSON.stringify(words);
      resets_ = resets_.filter(field => field !== name);
      state.notice = notice;
      state.bad = false; state.ok = false; state.savedState = "";
      paint();
      if (focus) mountPoint()?.querySelector(`[data-wb-word-input="${name}"]`)?.focus?.();
    }

    function addReplacement(name) {
      if (!state.draft || locked()) return;
      const item = state.container?.fields[name];
      if (!item || item.input !== "replacement-list") return;
      const form = mountPoint()?.querySelector(`[data-wb-replacement-form="${name}"]`);
      const from = String(form?.querySelector("[data-wb-replacement-from]")?.value ?? "");
      const to = String(form?.querySelector("[data-wb-replacement-to]")?.value ?? "");
      const policy = item.listPolicy || {};
      if (!from.trim() || from.length > (policy.fromMaxLength || 200)
          || to.length > (policy.toMaxLength || 500)) {
        state.notice = "请填写原词，并确认替换内容没有超过长度限制。";
        state.bad = true; state.ok = false; paint();
        return;
      }
      const pairs = parseReplacementList(state.draft[name]) || [];
      if (pairs.some(pair => pair.from === from)) {
        state.notice = "这个原词已经有替换规则。";
        state.bad = false; state.ok = false; paint();
        return;
      }
      if (pairs.length >= (policy.maxItems || 80)) {
        state.notice = `这个列表最多添加 ${policy.maxItems || 80} 条规则。`;
        state.bad = true; state.ok = false; paint();
        return;
      }
      pairs.push({from, to});
      updateStructuredList(name, pairs, `已添加“${visibleToken(from)}”的替换规则。`,
        `[data-wb-replacement-from="${name}"]`);
    }

    function removeReplacementAt(name, index) {
      if (!state.draft || locked()) return;
      const item = state.container?.fields[name];
      if (!item || item.input !== "replacement-list") return;
      const pairs = parseReplacementList(state.draft[name]) || [];
      if (!Number.isInteger(index) || index < 0 || index >= pairs.length) return;
      const [removed] = pairs.splice(index, 1);
      updateStructuredList(name, pairs, `已移除“${visibleToken(removed.from)}”的替换规则。`,
        `[data-wb-replacement-from="${name}"]`);
    }

    function addOrder(name, kind) {
      const item = state.container?.fields[name];
      if (!state.draft || locked() || !item || item.input !== "order-list" || !COMPONENT_ORDER.includes(kind)) return;
      const order = parseStringList(state.draft[name]) || [];
      if (order.includes(kind)) return;
      order.push(kind);
      updateOrder(name, order, `已添加${COMPONENT_LABELS[kind]}。`, order.length - 1);
    }

    function moveOrder(index, direction) {
      const name = "component_order";
      if (!state.draft || locked()) return;
      const order = parseStringList(state.draft[name]) || [];
      const target = direction === "up" ? index - 1 : direction === "down" ? index + 1 : -1;
      if (index < 0 || target < 0 || target >= order.length) return;
      [order[index], order[target]] = [order[target], order[index]];
      updateOrder(name, order, "已调整组件顺序。", target);
    }

    function removeOrder(name, index) {
      if (!state.draft || locked()) return;
      const order = parseStringList(state.draft[name]) || [];
      if (!Number.isInteger(index) || index < 0 || index >= order.length) return;
      const [removed] = order.splice(index, 1);
      updateOrder(name, order, `已移除${COMPONENT_LABELS[removed]}。`, Math.max(0, index - 1));
    }

    function updateOrder(name, order, notice, focusIndex) {
      updateStructuredList(name, order, notice,
        focusIndex == null ? null : `[data-wb-order-move="up"][data-wb-order-index="${focusIndex}"]`);
    }

    function updateStructuredList(name, value, notice, focusSelector) {
      if (!state.draft || locked()) return;
      state.draft[name] = JSON.stringify(value);
      resets_ = resets_.filter(field => field !== name);
      state.notice = notice;
      state.bad = false; state.ok = false; state.savedState = "";
      paint();
      if (focusSelector) mountPoint()?.querySelector(focusSelector)?.focus?.();
    }

    /* === 轻结构文本的编辑翻译 === */
    /* 生日选择器编辑后写回可见文字字段；无法解析的纪念日文字保持原样。 */
    function composeDateField(name, clear = false) {
      if (!state.draft || locked()) return;
      const group = mountPoint()?.querySelector('[data-wb-date-group="' + name + '"]');
      if (!group) return;
      const field = group.querySelector("[data-wb-field]");
      if (!field) return;
      const item = state.container?.fields[name];
      if (!item || item.input !== "date") return;
      const lunar = Boolean(group.querySelector('[data-wb-date-role="lunar"]')?.checked);
      const noYear = Boolean(group.querySelector('[data-wb-date-role="noyear"]')?.checked);
      const yearInput = group.querySelector('[data-wb-date-role="year"]');
      const solar = group.querySelector(".wb-date-solar");
      const lunarInputs = group.querySelector(".wb-date-lunar");
      if (solar) solar.hidden = lunar;
      if (lunarInputs) lunarInputs.hidden = !lunar;
      if (yearInput) yearInput.disabled = noYear || locked();
      const error = group.querySelector("[data-wb-date-error]");
      const setError = message => {
        setDateGroupError(group, message, error);
      };
      if (clear) {
        group.querySelectorAll("input,select").forEach(input => {
          if (input.type === "checkbox") input.checked = false;
          else input.value = "";
        });
        if (yearInput) yearInput.disabled = false;
        field.value = "";
        setError("");
        fireInput(field);
        return;
      }
      if (lunar) {
        const month = group.querySelector('[data-wb-date-role="month"]')?.value || "";
        const day = group.querySelector('[data-wb-date-role="day"]')?.value || "";
        const year = noYear ? "" : String(yearInput?.value || "").trim();
        const leap = Boolean(group.querySelector('[data-wb-date-role="leap"]')?.checked);
        if (!month && !day) {
          if (field.value) { setDateGroupError(group, "选择农历日期后会替换现有生日文字；清除请使用清除按钮。", error, false); return; }
          setError("");
          return;
        }
        if (!month || !day) { setDateGroupError(group, "请选择完整的农历月份和日期。", error, false); return; }
        if (year) {
          const numericYear = Number(year);
          if (!Number.isInteger(numericYear) || numericYear < 1 || numericYear > 9999) {
            setError("农历年份需在 1 至 9999 年之间；不需要年份时可勾选「不确定年份」。");
            return;
          }
          const index = lunarDateIndex(numericYear);
          if (!index) { setError("当前浏览器无法校验这个农历年份，请清空年份或更换浏览器。"); return; }
          if (!index.has(month + ":" + (leap ? "1" : "0") + ":" + Number(day))) {
            setError("这个年份没有对应的农历日期，请核对月份、日期和闰月。");
            return;
          }
        }
        field.value = birthdayText({year, month, day, leap}, true, noYear || !year);
      } else {
        const picker = /^(\d{4})-(\d{2})-(\d{2})$/.exec(
          group.querySelector('[data-wb-date-role="picker"]')?.value || "");
        if (!picker) {
          if (field.value) { setDateGroupError(group, "选择完整日期后才会替换现有生日文字；清除请使用清除按钮。", error, false); return; }
          setError("");
          return;
        }
        field.value = birthdayText({year: picker[1], month: picker[2], day: picker[3]}, false, noYear);
      }
      setError("");
      fireInput(field);
    }
    function setDateGroupError(group, message, error = group?.querySelector("[data-wb-date-error]"), invalid = Boolean(message)) {
      if (!group) return;
      group.dataset.wbDateInvalid = String(invalid);
      if (error) error.textContent = message || "";
    }
    function clearDateControls(group) {
      if (!group) return;
      group.querySelectorAll('[data-wb-date-role="year"], [data-wb-date-role="month"], '
        + '[data-wb-date-role="day"], [data-wb-date-role="picker"]').forEach(input => { input.value = ""; });
      const leap = group.querySelector('[data-wb-date-role="leap"]');
      if (leap) leap.checked = false;
      const lunar = group.querySelector('[data-wb-date-role="lunar"]');
      if (lunar) lunar.checked = false;
      const noyear = group.querySelector('[data-wb-date-role="noyear"]');
      if (noyear) { noyear.checked = false; noyear.disabled = locked(); }
      const year = group.querySelector('[data-wb-date-role="year"]');
      if (year) year.disabled = locked();
      const solar = group.querySelector(".wb-date-solar");
      const lunarPanel = group.querySelector(".wb-date-lunar");
      if (solar) solar.hidden = false;
      if (lunarPanel) lunarPanel.hidden = true;
    }
    function syncDateControlsFromText(group, parts) {
      if (!group) return;
      const lunar = Boolean(parts.lunar);
      const noYear = !parts.year;
      const lunarToggle = group.querySelector('[data-wb-date-role="lunar"]');
      const noyearToggle = group.querySelector('[data-wb-date-role="noyear"]');
      if (lunarToggle) lunarToggle.checked = lunar;
      if (noyearToggle) noyearToggle.checked = noYear;
      const year = group.querySelector('[data-wb-date-role="year"]');
      if (year) { year.value = parts.year || ""; year.disabled = noYear || locked(); }
      const month = group.querySelector('[data-wb-date-role="month"]');
      const day = group.querySelector('[data-wb-date-role="day"]');
      if (month) month.value = String(Number(parts.month));
      if (day) day.value = String(Number(parts.day));
      const leap = group.querySelector('[data-wb-date-role="leap"]');
      if (leap) leap.checked = Boolean(parts.leap);
      const picker = group.querySelector('[data-wb-date-role="picker"]');
      if (picker) {
        const pickerYear = parts.year || "2000";
        picker.value = `${pickerYear.padStart(4, "0")}-${String(parts.month).padStart(2, "0")}-${String(parts.day).padStart(2, "0")}`;
      }
      const solar = group.querySelector(".wb-date-solar");
      const lunarPanel = group.querySelector(".wb-date-lunar");
      if (solar) solar.hidden = lunar;
      if (lunarPanel) lunarPanel.hidden = !lunar;
    }
    /* 词条快选：single 模式单击选中、再击清空；multi 模式维护顿号清单。 */
    function applyChipOption(name, option) {
      if (!state.draft || locked()) return;
      const item = state.container?.fields[name];
      if (!item || item.input !== "chips") return;
      const line = mountPoint()?.querySelector(`[data-wb-chipset="${name}"] [data-wb-field]`);
      if (!line) return;
      const current = String(line.value ?? "").trim();
      let next;
      if (item.chipOptions?.mode === "multi") {
        const parts = parseTagList(current);
        const index = parts.indexOf(option);
        if (index >= 0) parts.splice(index, 1); else parts.push(option);
        next = parts.join("、");
      } else {
        next = current === option ? "" : option;
      }
      line.value = next;
      refreshChipset(name);
      fireInput(line);
    }
    function refreshChipset(name) {
      const set = mountPoint()?.querySelector(`[data-wb-chipset="${name}"]`);
      if (!set) return;
      const item = state.container?.fields[name];
      const value = String(set.querySelector("[data-wb-field]")?.value ?? "").trim();
      const parts = parseTagList(value);
      set.querySelectorAll("[data-wb-chip]").forEach(button => {
        const on = item?.chipOptions?.mode === "multi"
          ? parts.includes(button.dataset.wbChipValue)
          : value === button.dataset.wbChipValue;
        button.classList.toggle("is-on", on);
      });
    }
    function setTagList(name, tags, notice, focusInput) {
      if (!state.draft || locked()) return;
      const field = mountPoint()?.querySelector(`[data-wb-tagset="${name}"] [data-wb-field]`);
      const raw = tags.join("、");
      if (field) field.value = raw;
      state.draft[name] = raw;
      resets_ = resets_.filter(item => item !== name);
      state.notice = notice;
      state.bad = false; state.ok = false; state.savedState = "";
      paint();
      if (focusInput) mountPoint()?.querySelector(`[data-wb-tag-input="${name}"]`)?.focus?.();
    }
    function addTag(name, rawTag) {
      if (!state.draft || locked()) return;
      const item = state.container?.fields[name];
      if (!item || item.input !== "tags") return;
      const tag = String(rawTag ?? "").trim();
      if (!tag.length) return;
      const tags = parseTagList(state.draft[name]);
      const max = item.chipOptions?.maxTags || 24;
      if (tags.includes(tag)) {
        state.notice = "这一条已经在列表里。";
        state.bad = false; state.ok = false; paint();
        return;
      }
      if (tags.length >= max) {
        state.notice = `最多添加 ${max} 项。`;
        state.bad = true; state.ok = false; paint();
        return;
      }
      tags.push(tag);
      setTagList(name, tags, `已添加“${tag}”。`, true);
    }
    function removeTagAt(name, index) {
      if (!state.draft || locked()) return;
      const item = state.container?.fields[name];
      if (!item || item.input !== "tags") return;
      const tags = parseTagList(state.draft[name]);
      if (!Number.isInteger(index) || index < 0 || index >= tags.length) return;
      const [removed] = tags.splice(index, 1);
      setTagList(name, tags, `已移除“${removed}”。`, false);
    }
    /* 台词气泡：直接在原位增删节点并重新编号，不整卡重绘；删除与改写都会
       重新组成“一句一行”写回隐藏的 data-wb-field。 */
    function renumberQuotes(set, label) {
      set.querySelectorAll("[data-wb-quote]").forEach((node, index) => {
        const no = node.querySelector(".wb-quote-no");
        if (no) no.textContent = String(index + 1);
        const remove = node.querySelector("[data-wb-quote-remove]");
        if (remove) {
          remove.dataset.wbQuoteIndex = String(index);
          remove.setAttribute("aria-label", `删除第 ${index + 1} 句`);
        }
        const line = node.querySelector("[data-wb-quote-line]");
        if (line) line.setAttribute("aria-label", `${label} 第 ${index + 1} 句`);
      });
    }
    function growQuotes(scope) {
      scope?.querySelectorAll?.("[data-wb-quote-line]")?.forEach(node => {
        node.style.height = "auto";
        node.style.height = `${node.scrollHeight}px`;
      });
    }
    function composeQuotes(name) {
      if (!state.draft || locked()) return;
      const set = mountPoint()?.querySelector(`[data-wb-quotes="${name}"]`);
      if (!set) return;
      const field = set.querySelector("[data-wb-field]");
      if (!field) return;
      const lines = [...set.querySelectorAll("[data-wb-quote-line]")]
        .map(node => node.value.replace(/\s*\n+\s*/g, " ").trim()).filter(Boolean);
      field.value = lines.join("\n");
      fireInput(field);
    }
    function addQuote(name) {
      if (!state.draft || locked()) return;
      const set = mountPoint()?.querySelector(`[data-wb-quotes="${name}"]`);
      if (!set) return;
      const item = state.container?.fields[name];
      if (!item || item.input !== "quotes") return;
      set.querySelector(".wb-empty-list")?.remove();
      const list = set.querySelector(".wb-quote-list");
      const node = document.createElement("div");
      node.className = "wb-quote";
      node.dataset.wbQuote = "";
      const index = list.querySelectorAll("[data-wb-quote-line]").length;
      node.innerHTML = `<span class="wb-quote-no" aria-hidden="true">${index + 1}</span>`
        + `<textarea data-wb-quote-line="${e(name)}" rows="1" aria-label="${e(item.label || item.name)} 第 ${index + 1} 句"></textarea>`
        + `<button type="button" class="wb-quote-remove" data-wb-quote-remove="${e(name)}"`
        + ` data-wb-quote-index="${index}" aria-label="删除第 ${index + 1} 句" title="删除这句">${icon("x")}</button>`;
      list.appendChild(node);
      window.lucide?.createIcons({nodes: node.querySelectorAll("[data-lucide]")});
      const line = node.querySelector("textarea");
      line.focus();
    }
    function removeQuote(name, index) {
      if (!state.draft || locked()) return;
      const set = mountPoint()?.querySelector(`[data-wb-quotes="${name}"]`);
      if (!set) return;
      const nodes = [...set.querySelectorAll("[data-wb-quote]")];
      if (!Number.isInteger(index) || index < 0 || index >= nodes.length) return;
      nodes[index].remove();
      if (!set.querySelector("[data-wb-quote-line]")) {
        set.querySelector(".wb-quote-list").innerHTML = `<span class="wb-empty-list">还没有示例；新增一句试试。</span>`;
      }
      renumberQuotes(set, state.container?.fields[name]?.label || name);
      composeQuotes(name);
    }

    /* 恢复继承：清掉本层的显式值，让低层重新生效。
       记进 resets_ 以便保存时发 reset_to_inherit，而不是写一份重复值。 */
    function revertField(name) {
      if (!state.status || locked()) return;
      if (!resets_.includes(name)) resets_.push(name);
      state.draft[name] = state.base[name];
      state.notice = `${labels[name] || name} 已改为恢复继承；保存后低层配置重新生效。`;
      state.bad = false; state.ok = false;
      paint();
    }

    async function refresh(options = {}) {
      /* 已经有一次读取在途时不再叠加：挂载与显式刷新都会走到这里，
         而重复请求既浪费往返，也会让后到的旧快照覆盖新快照。 */
      if (state.loading || state.busy || (state.pending && state.status)) return;
      const previousBase = state.base;
      const previousDraft = state.draft;
      const previousConflicts = state.conflicts;
      const keepDraft = !options?.discardDraft && dirty();
      const cacheKey = JSON.stringify([endpoint, group, query || null,
        window.CompanionProduct?.scopeKey?.() || "",
        window.CompanionProduct?.selection?.() || null]);
      const prefetched = options?.prefetched && typeof options.prefetched === "object" ? options.prefetched : null;
      const cached = prefetched || (options?.allowCache && !options?.force && !keepDraft && !state.pending
        ? readCache(cacheKey) : null);
      const usedCache = Boolean(cached);
      const ticket = ++state.ticket;
      state.loading = true;
      state.notice = state.status ? state.notice : "";
      paint();
      try {
        let payload, retried = false;
        if (cached) {
          payload = cached;
          writeCache(cacheKey, cached);
        }
        for (let retries = 0; !cached; retries += 1) {
          try {
            payload = await api("GET", endpoint, null, query || undefined);
            writeCache(cacheKey, payload);
            break;
          } catch (problem) {
            if (ticket !== state.ticket) return;
            const code = problem?.code || (isCode(problem?.message) ? problem.message : "");
            if (code !== "bridge_timeout" || retries >= 2) throw problem;
            retried = true;
            state.notice = `宿主响应超时，自动重试读取（${retries + 1}/2）…`;
            state.bad = false;
            state.denied = false;
            paint();
            await new Promise(resolve => setTimeout(resolve, retries === 0 ? 400 : 1200));
            if (ticket !== state.ticket) return;
          }
        }
        if (ticket !== state.ticket) return;
        /* 配置接口按分区返回 {sections: {name: {...}}}；取本分区那一片。 */
        const value = payload.sections ? payload.sections[group] : payload;
        const settings = value?.[valuesKey];
        const token = value?.[tokenKey];
        if (!settings || typeof settings !== "object"
            || typeof token !== "string" || token.length !== 64) {
          throw error("invalid_response");
        }
        const nextContainer = buildContainer(value);
        /* 读取侧的令牌名可能与发送侧不同（见 writeTokenKey）。存进 status 时
           统一成 edit_token，后续只读这一个名字；发送时再翻回 writeTokenKey。 */
        if (tokenKey !== "edit_token") value.edit_token = token;
        if (valuesKey !== "settings") value.settings = settings;
        normalizeWordLists(value.settings, nextContainer.fields);
        const changed = state.status && state.status.edit_token !== value.edit_token;
        state.status = value;
        state.container = nextContainer;
        state.base = {...value.settings};
        state.draft = draftFrom(value.settings, value);
        state.conflicts = {};
        if (keepDraft) {
          for (const [name, draftValue] of Object.entries(previousDraft)) {
            if (draftValue === previousBase[name] && !resets_.includes(name)
                && !Object.hasOwn(previousConflicts, name)) continue;
            const prior = Object.hasOwn(previousConflicts, name)
              ? previousConflicts[name].base : previousBase[name];
            const reset = resets_.includes(name);
            state.draft[name] = reset ? value.settings[name] : draftValue;
            if (!Object.hasOwn(value.settings, name)
                || (value.settings[name] !== prior && (reset || value.settings[name] !== draftValue))) {
              state.conflicts[name] = {base: prior};
            }
          }
        } else {
          resets_ = [];
        }
        state.stale = false;
        let recoveredConflict = false;
        if (!state.pending) {
          const restored = restorePending();
          if (restored) {
            if (recoverConfirmedConflict(restored)) recoveredConflict = true;
            else {
              state.pending = restored;
              state.pendingReason = restored.pending_reason || "";
            }
          }
        }
        state.denied = false; state.bad = false;
        if (recoveredConflict) {
          state.bad = true;
          state.notice = "上次保存遇到配置版本冲突，服务端没有写入；草稿已恢复，请核对后重新保存。";
        } else if (Object.keys(state.conflicts).length) {
          state.bad = true;
          state.notice = "当前配置已读取，请核对同时修改的设置。";
        } else if (keepDraft) state.notice = dirty()
          ? "已读取最新配置，你的改动已保留，保存后生效。" : "改动已与当前配置一致，无需再次保存。";
        else if (changed) state.notice = "已读取最新配置。";
        else if (retried) state.notice = "";
        state.loading = false;
        paint();
        if (onStatus) onStatus(value);
        if (state.pending) scheduleAutoRecovery(state.pending.operation_key, ticket);
        schedule();
        if (usedCache && !prefetched) {
          setTimeout(() => {
            if (!state.busy && !state.loading && !state.dirty() && !state.pending) {
              void refresh({...options, force: true});
            }
          }, 0);
        }
      } catch (problem) {
        if (ticket !== state.ticket) return;
        /* 读取失败后不能继续把上一份配置当成当前真实状态展示。保留用户尚未
           提交的草稿，但把它标成需要核对；没有未保存改动时连草稿基线一起丢掉，
           下一次成功读取会从服务端重新建立完整合同。 */
        const preserveDraft = dirty();
        state.status = null;
        state.container = null;
        if (!preserveDraft) {
          state.draft = null;
          state.base = null;
          resets_ = [];
        }
        state.stale = preserveDraft;
        state.loading = false;
        state.denied = true; state.bad = true;
        state.notice = localized(problem, "暂时无法读取。请重新读取角色状态后重试。");
        paint();
      }
    }

    async function save(retry) {
      if (state.busy || state.loading || state.denied) return;
       if (!retry) {
         if (state.pending) { state.notice = ""; state.bad = false; paint(); return; }
        if (state.stale) { await refresh(); return; }
        if (Object.keys(state.conflicts).length) {
          state.notice = "请先选择同时修改的设置要保留哪个值。"; state.bad = true; paint(); return;
        }
        const invalidDate = mountPoint()?.querySelector('[data-wb-date-invalid="true"]');
        if (invalidDate) {
          invalidDate.querySelector("[data-wb-date-error]")?.focus?.();
          return;
        }
        const bad = Object.entries(state.draft).find(([name, value]) =>
          !valid(state.container.fields[name], value));
        if (bad) {
          const item = state.container.fields[bad[0]];
          const message = explainInvalid(item, bad[1]);
          state.bad = true;
          /* 提示条画在卡片顶部，而按保存的人通常正停在底部：光写提示等于
             「点了没反应」。所以重绘之后把第一条不合法的字段标出来、跳过去、
             把光标放上去——用户下一步就在那一项上，不用自己找。 */
          state.notice = `${message}改动还在，改好这一项就能保存。`;
          paint();
          markInvalid(bad[0], message, {jump: true});
          return;
        }
        /* 组合约束（如「单批取样条数不能小于最低样本数」）不是单字段范围能表达的，
           由各分区自己声明。返回错误码即拦下，不发请求。 */
        const combined = audit ? audit({...state.draft}) : null;
        if (combined) {
          state.bad = true;
          state.notice = errors[combined] || "这几项的组合不合法，请调整后再保存。";
          paint();
          return;
        }
        const changes = {};
        for (const [name, value] of Object.entries(state.draft)) {
          if (value !== state.base[name]) changes[wireName(name)] = value;
        }
        /* 恢复继承：这些字段要显式告诉后端「清掉本层的值」，
           而不是写一份和继承值相同的副本。 */
        const resets = Object.keys(state.draft).filter(name =>
          resets_.includes(name) && state.draft[name] === state.base[name]).map(wireName);
        if (!Object.keys(changes).length && !resets.length) {
          state.notice = "设置没有变化。"; state.bad = false; paint(); return;
        }
        /* 存进 pending 的是**网线名**：重试要逐字节复用原请求，
           而草稿名与网线名可能不同（见 outbound）。 */
        state.pending = {
          ...(requestSection ? {section: group} : {}),
          operation_key: crypto.randomUUID(),
          edit_token: state.status.edit_token,
          changes,
          reset_to_inherit: resets,
        };
        savePending();
      }
      if (!state.pending) return;
       state.busy = true; state.bad = false; state.ok = false;
       if (!retry) state.pendingReason = "";
       state.notice = retry ? "正在核对原保存结果…" : "正在保存…";
      paint();
      const request = {...state.pending};
      try {
        const body = {
          operation_key: request.operation_key,
          [writeTokenKey]: request.edit_token,
          changes: request.changes,
          ...(resetKey ? {[resetKey]: request.reset_to_inherit || []} : {}),
        };
        /* /config/configure multiplexes groups and requires `section`; the
           reply/expression/memory routes identify their group in the path and
           reject unknown body keys. Keep the discriminator opt-in. */
        if (requestSection) body.section = request.section || requestSection;
        const result = await api("POST", endpoint + savePath, body);
        const receipt = result.result ?? result;
        if (typeof receipt?.operation_id !== "string" || typeof receipt?.replayed !== "boolean") {
          throw error("invalid_response");
        }
         state.pending = null; state.pendingReason = ""; savePending();
        resets_ = [];
        clearAutoRecovery(request.operation_key);
        /* 保存确认同样按分区包裹，取本分区那一片；缺失时保留原状态。
           读取侧与发送侧的值字段名可以不同（见 valuesKey），这里也要按同一个名字回填，
           否则记忆分区保存成功后快照永远不刷新，下一次保存又会撞版本冲突。 */
        const fresh = result.status?.sections ? result.status.sections[group] : result.status;
        const freshValues = fresh && fresh[valuesKey];
        if (freshValues && typeof freshValues === "object") {
          if (valuesKey !== "settings") fresh.settings = freshValues;
          if (tokenKey !== "edit_token" && typeof fresh[tokenKey] === "string") {
            fresh.edit_token = fresh[tokenKey];
          }
          state.status = fresh;
          state.container = buildContainer(fresh);
          normalizeWordLists(fresh.settings, state.container.fields);
          state.draft = draftFrom(fresh.settings, fresh);
          state.base = {...fresh.settings};
          state.stale = false;
          state.conflicts = {};
          resets_ = [];
          if (onStatus) onStatus(fresh);
        }
        state.busy = false;
        /* 幂等重放：勾画两遍，视觉上区分「新写入」与「复用旧请求确认」。 */
        state.savedState = receipt.replayed ? " is-saved is-replayed" : " is-saved";
        state.ok = true;
        state.notice = receipt.replayed
          ? "原保存已确认：这次沿用同一个操作标识，没有重复写入。"
          : "已保存。按该分区的作用范围生效。";
        paint();
        /* 有的分区不回带新快照（记忆分区的保存确认只有操作结果），
           但写入后 edit_token 已经轮换：不重新读一次，下一次保存会拿着旧令牌
           去撞版本冲突。这里补一次读取，让令牌与基线跟上。
           注意要放在 paint() 之后：refresh() 会自己重绘并给出「已读取」提示，
           先读的话保存成功的动效就看不见了。 */
        if (!freshValues) void refresh();
        setTimeout(() => {
          if (state.savedState) { state.savedState = ""; paint(); }
        }, receipt.replayed ? 1800 : 1300);
      } catch (problem) {
        state.busy = false;
        state.bad = true;
        const problemCode = problem?.code || (isCode(problem?.message) ? problem.message : "");
        if (problem.confirmed || problemCode === "configuration_revision_conflict") {
           state.pending = null; state.pendingReason = ""; savePending();
           clearAutoRecovery(request.operation_key);
           state.stale = problemCode === "configuration_revision_conflict";
           state.notice = localized(problem, "保存未通过。请读取当前设置后核对。");
        } else {
           state.pendingReason = localized(problem, "连接没有返回保存确认。");
           state.pending.pending_reason = state.pendingReason.slice(0, 512);
           if (problemCode) state.pending.pending_reason_code = problemCode.slice(0, 80);
           savePending();
           scheduleAutoRecovery(request.operation_key, state.ticket);
           /* 待核对块会展示原因和唯一操作入口；顶部不要再重复一条红色提示。 */
           state.notice = "";
           state.bad = false;
         }
        paint();
      }
    }

    async function saveField(name, value) {
      if (state.pending) return {state: "unknown", message: state.pendingReason || state.pending.pending_reason || ""};
      if (state.busy || state.loading || state.denied || !state.status) return {state: "unavailable"};
      if (!state.draft || !state.container?.fields[name]) return {state: "unavailable"};
      if (state.stale || Object.keys(state.conflicts).length) return {state: "conflict", message: state.notice || "配置已更新，请先核对当前版本。"};
      const otherChanges = Object.keys(state.draft).some(field => field !== name && state.draft[field] !== state.base?.[field]);
      if (otherChanges || resets_.length) return {state: "dirty"};
      state.draft[name] = value;
      state.savedState = "";
      state.ok = false;
      state.notice = "";
      state.bad = false;
      if (state.base[name] === value) { paint(); return {state: "unchanged"}; }
      await save();
      if (state.ok) return {state: "saved"};
      if (state.pending) return {state: "unknown", message: state.pendingReason || state.pending.pending_reason || ""};
      if (state.stale || Object.keys(state.conflicts).length) return {state: "conflict", message: state.notice || "配置已更新，请先核对当前版本。"};
      if (state.bad) return {state: "rejected", message: state.notice || "设置未通过校验。"};
      return {state: "unchanged", message: state.notice || "设置没有变化。"};
    }

    function schedule() {
      stop();
      if (!pollMs || !state.status || document.hidden) return;
      state.timer = setTimeout(() => {
        if (!state.busy && !state.loading && !state.dirty()) void refresh({force:true});
        else schedule();
      }, pollMs);
    }
    function stop() { clearTimeout(state.timer); state.timer = null; }

    function mount() {
      /* 登记在上面的两个提前返回之前：宿主判断「有没有未保存改动」时，
         一个正在显示「未连接」提示的实例不该被漏掉（它的草稿可能还在）。 */
      registerInstance(handle);
      bind();
      window.addEventListener("companion:catalogs-updated", refreshCatalogs);
      if (!window.CompanionProduct?.hosted?.()) {
        const root = mountPoint();
        if (root) root.innerHTML = `<p class="wb-notice">${icon("info")}`
          + `<span>请从已登录的 AstrBot 管理界面打开陪伴小屋，连接 V2 运行实例后调整这些设置。</span></p>`;
        return;
      }
      if (typeof endpoint !== "string" || !endpoint.startsWith(PREFIX)) {
        const root = mountPoint();
        if (root) root.innerHTML = `<p class="wb-notice is-error">${icon("alert-circle")}`
          + `<span>当前对象没有可用的配置工作区。请先选择已初始化的私聊对象和角色。</span></p>`;
        return;
      }
      if (typeof prefetch === "function") {
        Promise.resolve().then(prefetch).then(payload => {
          const mounted = mountPoint();
          if (mounted && mounted.isConnected !== false) void refresh({prefetched: payload});
        }).catch(() => { void refresh({allowCache:true}); });
      } else {
        void refresh({allowCache:true});
      }
    }

    /* 宿主页面每次 render() 会整块重写 innerHTML，把本工作台的 DOM 一起冲掉。
       实例仍活着（status/draft 都在），所以只需要重新绑定并重绘——
       不要再读一次：那会让「切个区域再切回来」变成一次多余往返，
       也会把页面其他地方正在用的层轨动画重播一遍。
       endpoint 变了（换了对象或角色）才需要真正重挂。 */
    function attach() {
      bind();
      paint();
    }

    function unmount() {
      forgetInstance(handle);
      state.ticket++;
      stop();
      if (autoRecoveryTimer !== null) clearTimeout(autoRecoveryTimer);
      autoRecoveryTimer = null;
      autoRecoveryKey = "";
      unbind();
      window.removeEventListener("companion:catalogs-updated", refreshCatalogs);
       state.status = null; state.container = null; state.draft = null; state.base = null;
       state.pendingReason = "";
      state.busy = false; state.loading = false; state.notice = ""; state.bad = false;
      state.ok = false; state.savedState = ""; state.drawRails = true;
      state.stale = false; state.conflicts = {};
      state.visibleGroupIds = null;
      resets_ = [];
    }

    function changedFields() {
      return [...new Set([...Object.keys(state.draft || {}), ...Object.keys(state.conflicts)])]
        .filter(name => state.draft[name] !== state.base?.[name] || Object.hasOwn(state.conflicts, name)
        || resets_.includes(name)).map(name => (state.container?.fields[name]?.label || labels[name] || name)
          + (resets_.includes(name) ? '（恢复继承）' : ''));
    }

    async function saveBeforeLeave() {
      if (state.pending) return {error: '上次保存结果待核对，请回到这项配置核对原请求。'};
      if (state.busy || state.loading) return {error: '这项配置正在处理，请稍后再保存。'};
      await save(false);
      return !dirty() && !state.pending ? true
        : {error: state.notice || state.pendingReason || '保存尚未确认，改动已保留，请回到配置中检查。'};
    }

    function discard() {
      if (state.busy) return {error: '正在保存，请等待保存结果后再切换。'};
      state.draft = state.base ? draftFrom(state.base, state.status) : null;
      resets_ = [];
      state.conflicts = {}; state.stale = false;
      // An unanswered request stays in sessionStorage for later reconciliation.
      state.pending = null;
      state.notice = ''; state.bad = false;
      paint();
      return true;
    }

    /* 句柄不能叫 api：同一作用域里已有一个 async function api（桥调用）。 */
    const handle = {group, title, mount, attach, unmount, refresh, state, save, saveField, stop,
      start: schedule, selectSection, setVisibleGroups, dirty, changedFields, saveBeforeLeave, discard,
      connected: () => { const root = mountPoint(); return !!root && root.isConnected !== false; }};
    return handle;
  }

  return {create, errors, LAYER_LABELS, contract, valid, toDisplay, fromDisplay, choiceText,
    /* 词条解析也对外：配置覆盖自检要拿它验证注册默认值读得懂，避免审计脚本
       再写一份解析规则、和页面一起写错。 */
    parseStringList,
    /* 有草稿没保存的实例清单（含分区标题，用于提示语）。 */
    dirtyEditors: () => [...LIVE].flatMap(entry => {
      try {
        if (!entry.connected()) { entry.unmount(); return []; }
        return entry.dirty() ? [{group: entry.group, title: entry.title || entry.group,
          fields: entry.changedFields(), save: entry.saveBeforeLeave, discard: entry.discard}] : [];
      } catch { return []; }
    })};
})();
