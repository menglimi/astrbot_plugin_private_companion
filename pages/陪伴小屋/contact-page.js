/* 陪伴小屋 · 主动与联系（节律盘）
 *
 * 这一页管的是「她此刻会不会开口、按什么节律开口」。
 *
 * 与记忆页的区别——也是不能照搬记忆页版式的原因：
 *   记忆是**生命周期**（写入→更正→撤回→遗忘），所以记忆页是档案库、master-detail。
 *   联系是**时间上的分布**（一天里哪些小时允许开口、两次之间隔多久、连发几轮），
 *   所以这一页的主体是一个钟面：24 小时环形，四圈同心。
 *
 * 四圈对应四个真实字段，且只有这四圈是「一天」这个量纲上的东西：
 *   外圈 · 免打扰弧      quiet_enabled / quiet_start / quiet_end（可跨午夜）
 *   次圈 · 活动曲线      proactive_hour_activity_enabled / proactive_hour_activity_curve（24 权重）
 *   三圈 · 强度档位       proactive_intensity_preset（缩放间隔与门槛，不放宽额度）
 *   内圈 · 问候窗口       greeting_morning/noon/evening_window + 失眠窗口
 * 环心预览安装级默认时段，不表示任何私聊用户的授权结果。
 *
 * 全局设置通过 /config 读取，写入 installation 层的 proactive.contact 段。
 * 页面不读取宿主当前打开的会话；单个私聊用户的覆盖与授权在私聊用户页管理。
 *
 * 关于额度：配置页展示滚动上限，管理员诊断结果展示模型调用、实际发送和待确认预占。
 *
 * 动效分三层，都在 prefers-reduced-motion 下压到瞬时终态，信息一条不丢：
 *   ① 开页一次——24 根柱子从 0:00（正上方）向两侧荡开，在 12:00 会合，
 *      约一秒跑完就不再重复（涟漪，见 ct-bar-ripple 与 --i）；
 *   ② 改动确认——改了哪一圈就只闪哪一圈（PULSE_PART → is-pulse-*），
 *      整盘一起动等于什么都没说清；类由渲染时给出，不是渲染后往节点上加；
 *   ③ 状态常驻——时间游标、「此刻可以开口」的呼吸点。
 */
"use strict";
window.CompanionContactPage = (() => {
  const HOST = "astrbot_plugin_private_companion";
  const PREFIX = "/" + HOST + "/";
  const STORE = "companion-contact-page-v1:";
  const HISTORY_PENDING = "__companion_contact_pending_v1";
  const HISTORY_PREVIOUS = "__companion_contact_previous_state_v1";
  const pendingWrites = new Map();
  const proactiveTestGuards = new Map();
  const TEST_GUARD_RESET = "__reset_proactive_test_guard__";
  const AUTO_RECOVERY_INITIAL_DELAY = 400;
  const AUTO_RECOVERY_MAX_DELAY = 30000;
  const AUTO_RECOVERY_MAX_EXPONENT = 8;
  let autoRecoveryKey = "";
  let autoRecoveryTimer = null;
  let autoRecoveryAttempts = 0;
  const GROUP = "proactive.contact";
  /* 15 类契机，归属三个语义桶——这个分法来自它们「凭什么开口」：
     关系本身、日程时刻、还是外部分享。矩阵按桶分行，不按字母序。 */
  const PURPOSE_BUCKETS = [
    {id:"bond",  label:"关系",  hint:"由关系状态驱动", purposes:[
      ["affection","想念与亲近"],["care","情绪关怀"],["play","轻松互动"],
      ["repair","关系修复"],["chat","日常话题"]]},
    {id:"daily", label:"日程",  hint:"由时刻或约定驱动", purposes:[
      ["greeting","问候与纪念"],["followup","接回旧话题"],["invitation","共同活动邀请"],
      ["reminder","约定与提醒"],["meal","饭点话题"]]},
    {id:"share", label:"分享",  hint:"由外部素材驱动", purposes:[
      ["life","分享自己的生活"],["creation","作品与阅读"],["news","外部资讯"],
      ["environment","环境与状态变化"],["screen","已授权屏幕观察"]]},
  ];
  const ALL_PURPOSES = PURPOSE_BUCKETS.flatMap(b => b.purposes);
  const PURPOSE_META = Object.fromEntries(PURPOSE_BUCKETS.flatMap(bucket =>
    bucket.purposes.map(([name, label]) => [name, {label, bucket}])));
  const TEST_INSTRUCTIONS = [
    {id:"affection", label:"想念与亲近", bucket:"关系", coverage:"ready",
      source:"已确认私聊窗口与当前关系联系节律；不要求近期关键词",
      setup:"普通日常语境即可验证主动表达；另测明确边界和重复消息，不自然时允许跳过。",
      instruction:"允许角色主动表达自己的亲近与想念，不推断用户也有同样感受、不虚构共同经历或索取回应。",
      expect:"表达来自角色自身；不合适或显得突兀时选择 skip。"},
    {id:"care", label:"情绪关怀", bucket:"关系", coverage:"ready",
      source:"已确认私聊窗口与当前关系联系节律；旧情绪不作当前事实",
      setup:"普通日常语境也可验证低压力问候；另测旧情绪和明确边界。",
      instruction:"可以主动轻轻问候近况或表达愿意倾听；不把旧情绪当成当前状态，不诊断、不盘问、不催回复。",
      expect:"关心不预设对方正在难过或需要帮助；不自然时选择 skip。"},
    {id:"play", label:"轻松互动", bucket:"关系", coverage:"ready",
      source:"已确认私聊窗口与当前关系联系节律；不要求已有共同梗",
      setup:"普通日常语境即可验证角色发起的轻互动；另测严肃话题和明确边界。",
      instruction:"由角色先带来轻松的问题、俏皮念头或可立即参与的小游戏切口；不只表示可以陪聊，不假定用户兴趣或编造共同经历。",
      expect:"互动可自然结束；不合适时选择 skip。"},
    {id:"repair", label:"关系修复", bucket:"关系", coverage:"ready",
      source:"最近一轮真实私聊原文，由模型判断是否有可核验的失误线索",
      setup:"分别用有明确失误线索和普通日常语境验收；责任不清时允许跳过。",
      instruction:"只有来源能说明角色具体做错什么时才简短承认并表达理解；不辩解、不要求原谅、不把责任推给对方。",
      expect:"不凭用途标签认错；责任或语境不清时选择 skip。"},
    {id:"chat", label:"日常话题", bucket:"关系", coverage:"ready",
      source:"已确认私聊窗口与主动联系节律；最近对话只作可选语境",
      setup:"用普通日常私聊验证角色主动开启话题，不限定关键词或具体事件；另测刚有新输入、明确边界和重复话题。",
      instruction:"可以从角色自己的想法、兴趣或真实对话里自然开口；事实才需要来源支持，不要求复述最近消息。",
      expect:"授权、空闲、额度、活动曲线和去重按真实配置生效；不合适或显得突兀时选择 skip。"},
    {id:"greeting", label:"问候与纪念", bucket:"日程", coverage:"ready",
      source:"本地问候窗口、夜间窗口、固定节日，以及角色档案和当前私聊资料中的生日与纪念日",
      setup:"分别构造早/午/晚窗口、夜间窗口和重要日期样例，并验证窗口边界。",
      instruction:"把日期或时段只当作开口机会；优先承接当前语境，不假设对方刚醒、吃饭、休息或正在等待。",
      expect:"只在有效窗口、静默和冷却规则允许时进入链路；不得机械套用固定问候。"},
    {id:"followup", label:"接回旧话题", bucket:"日程", coverage:"ready",
      source:"已确认投递记录、等待时间及之后是否有新输入",
      setup:"构造已确认投递、等待期已过且无新输入的记录；另测新输入、屏幕观察和冷却中的对照组。",
      instruction:"只承接原记录中仍有意义且未被新输入取代的话题，不暗示已读或催回复。",
      expect:"只在原话仍有效、等待期已过且无新输入时起草；跟进生产器会再次校验原始投递与冷却。"},
    {id:"invitation", label:"共同活动邀请", bucket:"日程", coverage:"ready",
      source:"已确认私聊窗口与当前关系联系节律；活动由角色主动提出",
      setup:"验证一起玩游戏、打语音电话、一起看或听等具体邀请；另测明确拒绝和重复邀请，不假设共同兴趣或空闲。",
      instruction:"由角色挑一项具体活动主动邀请，例如玩一局五子棋、海龟汤或打个语音电话；说清想一起做什么，不只说陪聊。邀请不代表已经约好、开局或拨通，执行另走已有能力与授权。",
      expect:"邀请具体、口吻自然，给对方留出拒绝或改期空间；边界明确时选择 skip。"},
    {id:"reminder", label:"约定与提醒", bucket:"日程", coverage:"ready",
      source:"当前有效、已接受且即将开始的日历约定",
      setup:"构造已接受且即将开始的约定；对照待确认、取消、过期和修订过的记录。",
      instruction:"只根据当前有效的已接受约定简短提醒，不说成已经发生，也不声称对方已收到或记得。",
      expect:"来源回执、版本、发生时间和配置窗口全部匹配才进入模型。"},
    {id:"meal", label:"饭点话题", bucket:"日程", coverage:"ready",
      source:"关系时区中的午餐或晚餐时间窗口，以及角色自己的生活节奏",
      setup:"按关系时区构造午餐、晚餐窗口；另测窗口边界、暂停、重复候选和当天没有合适话头的对照组。",
      instruction:"饭点只提供角色自己的生活时间背景，不推断对方没吃饭、正在吃饭或需要提醒；优先分享角色自己的念头或接回原话题，没有自然话头就选择 skip。",
      expect:"使用配置时区和原始窗口；不默认盘问饮食、不把饭点变成每日打卡或要求报平安。"},
    {id:"life", label:"分享自己的生活", bucket:"分享", coverage:"ready",
      source:"仍有效且允许分享的角色活动回执或日记记录",
      setup:"构造真实活动完成回执或角色日记；另测素材撤回和修订版本变化。",
      instruction:"只分享已完成且当前仍有效的具体片段；日记和梦境保持主观语境，不扩写成客观事实或对方经历。",
      expect:"原操作、修订、来源授权和受众复核通过才生成；旧候选在来源变化后失效。"},
    {id:"creation", label:"作品与阅读", bucket:"分享", coverage:"ready",
      source:"带有阅读范围的完成回执",
      setup:"构造完成阅读回执和可引用范围；对照只打开、未完成和范围已变更的记录。",
      instruction:"只说回执覆盖的阅读片段；个人感想明确是角色感受，不声称读完整本或把书中内容说成现实。",
      expect:"只有完成且回执有效时触发；不补写未提供的书名、章节或情节。"},
    {id:"news", label:"外部资讯", bucket:"分享", coverage:"ready",
      source:"带标题、原链接和发布时间的当前资讯条目",
      setup:"即时抓取当前配置的真实资讯源（默认 AI 早报），使用有效条目生成消息。",
      instruction:"只根据给定条目判断是否与关系语境相关；保留原发布时间，不声称亲历、看完全文或独立核实。",
      expect:"实际抓取、条目校验、模型生成和 QQ 回执全部完成；出处留存供追问。"},
    {id:"environment", label:"环境与状态变化", bucket:"分享", coverage:"ready",
      source:"当前天气、天气变化、官方预警或 Bot 服务余额来源",
      setup:"分测当前观测、两次观测的变化、官方预警覆盖范围和服务余额；再测过期或范围不明素材。",
      instruction:"天气观测不等于预报，变化不等于灾害；预警只按官方原文与覆盖范围，余额不是用户欠款，不催充值。",
      expect:"严格使用来源地点、时效、覆盖范围和金额；依据不足时选择 skip。"},
    {id:"screen", label:"已授权屏幕观察", bucket:"分享", coverage:"ready",
      source:"本次已授权设备的新截图及对应视觉模型分析回执",
      setup:"实际调用窥屏设备，保存截图原件并调用视觉模型，再用这次分析生成主动消息。",
      instruction:"只提及分析明确支持且适合分享的一点观察；不暴露无关屏幕内容，不推断用户意图或身份。",
      expect:"新截图、该截图的视觉分析、消息生成与 QQ 回执全部完成；撤权或来源变化后失效。"},
    {id:"mobile_location", label:"手机地点主动消息", bucket:"拓展", coverage:"ready",
      source:"手机陪伴终端本次上报的授权地点快照与当前私聊角色",
      setup:"读取终端当前未过期地点，经过现实触及授权和变化校验，再进入环境主动联系与 QQ 投递链。测试不伪造坐标，也不改生产去重标记。",
      instruction:"只把手机上报的地点标签作为可选聊天素材；不能据此推断用户正在做什么、心情如何或是否安全。",
      expect:"终端地点读取、来源发布、主动消息生成与 QQ 回执全部完成；没有有效地点或授权时明确失败。"},
    {id:"reaction_image", label:"带图主动消息", bucket:"拓展", coverage:"ready",
      source:"当前私聊已授权的表情图库、原图校验与图片投递回执",
      setup:"按一次性测试意图从授权图库检索素材，执行原图校验、封存和真实 QQ 图片投递；不修改生产图片频率。",
      instruction:"正文保持自然完整；只附一张适合日常友好互动的原图，不适合时或本人停用表情时不得发送。",
      expect:"授权素材检索、图片原件、文本与图片同一投递载荷及平台回执全部匹配；不会以纯文本冒充通过。"},
  ];
  const TEST_COVERAGE = {ready:"生产器已接通", partial:"只有资格检查", missing:"尚无独立生产器"};
  const TEST_BUCKET_IDS = {关系:"bond", 日程:"daily", 分享:"share", 拓展:"extension"};
  /* 强度档位。effects 只是**给界面看的**说明，真值在 configuration.py 的
     INTENSITY_PRESETS 里；这里写死的是「这个档位会怎么变」的文案，
     不参与任何计算。 */
  const TIERS = [
    {key:"off",         label:"关闭",   interval:1.0,  idle:1.0,  burst:0, chars:0},
    {key:"balanced",    label:"均衡",   interval:0.8,  idle:0.7,  burst:2, chars:300},
    {key:"high_private",label:"高频私聊", interval:0.5, idle:0.35, burst:3, chars:300},
    {key:"high_group",  label:"高频群聊", interval:0.9, idle:0.8,  burst:2, chars:240},
    {key:"live",        label:"实时",   interval:0.2,  idle:0.1,  burst:3, chars:400},
  ];
  const TIER_BY_KEY = Object.fromEntries(TIERS.map(t => [t.key, t]));
  /* 强度别名。后端 INTENSITY_ALIASES 会把 直播/高频 这类写法归一，
     页面显示时必须显示归一后的档位，否则「设了实时却显示直播」会让人以为没保存上。 */
  const TIER_ALIAS = {manual:"off",none:"off",default:"off","":"off",standard:"balanced",
    active:"high_private",private:"high_private",group:"high_group",online:"live",直播:"live",高频:"live"};
  const DEFAULT_CURVE = "0.22,0.16,0.12,0.10,0.10,0.14,0.28,0.50,0.66,0.72,0.78,0.92,1.0,0.94,"
    + "0.82,0.74,0.78,0.92,1.0,0.98,0.88,0.70,0.48,0.32";
  /* 三个问候锚点的默认窗口。只用于在环上画参考弧，真值读 settings。 */
  const ANCHORS = [["morning","morning_greeting","07:45","10:20"],["noon","noon_greeting","12:05","13:35"],
                   ["evening","evening_greeting","20:10","21:20"]];
  const errors = {
    dashboard_identity_required:"请从已登录的 AstrBot 管理界面打开陪伴小屋。",
    staging_not_initialized:"角色数据尚未就绪，请在「账号与会话」保存角色映射；已保存可重新读取状态。",
    staging_host_terminated:"插件已停止，请重新打开拓展页。",
    core_access_required:"当前账号没有联系设置权限。",
    authorization_denied:"当前账号没有安装级联系配置权限。",
    binding_revoked:"管理账号的授权已撤销。", binding_rebound:"管理账号的绑定已变化，请重新进入。",
    proactive_contact_scope_invalid:"全局联系配置接口不可用，请重新读取安装配置。",
    proactive_contact_target_changed:"全局联系配置目标已变化，请重新读取。",
    proactive_contact_configuration_invalid:"请核对设置：数值需在各自范围内，枚举项需在下拉选项内。",
    proactive_contact_clock_invalid:"请填写二十四小时制的时刻，形如 23:00 或 07:45。",
    proactive_contact_window_invalid:"问候窗口形如 07:45-10:20，且必须当天内结束，不能跨午夜。",
    proactive_contact_timezone_invalid:"时区名无效，形如 Asia/Shanghai。",
    proactive_contact_pause_invalid:"暂停时刻需带时区偏移，或留空表示不暂停。",
    proactive_contact_curve_invalid:"活动曲线需要 24 个数值；写错的那一小时会回退，其余照常。",
    proactive_contact_list_invalid:"请填写逗号分隔的取值，且不能重复。",
    proactive_contact_dates_invalid:"重要日期需要一个 JSON 数组，最多 32 条。",
    proactive_contact_source_invalid:"资讯来源填写有误，请核对类型、标识或地址。",
    proactive_contact_revision_conflict:"设置已有其他更新，已读取最新值。",
    configuration_revision_conflict:"设置已有其他更新。正在读取最新值，未保存的改动会保留。",
    contact_configuration_invalid:"请核对联系设置：提交的字段或操作不完整。",
    invalid_contact_preference:"请核对联系设置的格式或范围。",
    configuration_relationship_required:"这个对象还没有建立可写入的关系。",
    contact_recipient_changed:"联系对象已变化，请重新读取。",
    configuration_request_invalid:"请求与当前接口不匹配，请更新页面后重试。",
    configuration_group_unknown:"这个设置分组已不存在，请更新页面。",
    bridge_unavailable:"页面还没有接上 AstrBot。若这是从管理界面打开的插件页，刷新一次通常就好；若反复出现，请确认插件已启用。",
    bridge_timeout:"未收到服务确认，请刷新核对。",
    invalid_response:"服务返回的数据不完整，请刷新核对。",
    material_pool_unavailable:"角色共享素材暂时无法读取，请稍后重试。",
    proactive_quiet_hours:"当前处于免打扰时段。", proactive_paused:"当前已暂停主动联系。",
    proactive_disabled:"主动联系总开关已关闭。", contact_automatic_disabled:"后台自动联系未开启。",
    model_task_unregistered:"请先登记主动联系模型。", host_not_running:"当前运行实例尚未启动。",
    contact_model_profile_exceeded:"主动联系的上下文超过当前模型输入或输出上限。请减少历史长度，或在「模型与连接」换用上下文更大的模型。",
    model_estimation_failed:"当前模型无法接受这次主动联系的上下文长度；请减少历史长度或换用上下文更大的模型。",
    host_model_limit:"当前模型输入或输出上限不足；请减少历史长度或换用上下文更大的模型。",
  };
  const CODE = /^[a-z][a-z0-9_]{2,60}$/;
  const denied = new Set(["dashboard_identity_required","core_access_required","binding_revoked",
    "binding_rebound","staging_host_terminated","proactive_contact_scope_invalid"]);
  const e = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const reason = code => errors[code] || "联系条件暂时无法核对，请重新读取状态。";
  const problem = code => Object.assign(new Error(reason(code)), {code});
  function recoveryFailure(error) {
    const code = typeof error?.code === "string" ? error.code : "";
    if (code === "bridge_timeout" || code === "bridge_unavailable") {
      return "自动核对暂时没有收到服务确认；原请求已保留，系统会继续核对。";
    }
    if (errors[code]) return "核对未完成：" + errors[code];
    const message = typeof error?.message === "string" ? error.message.replace(/[\u0000-\u001f\u007f]+/g, " ")
      .replace(/\s+/g, " ").trim().slice(0, 120) : "";
    let detail = CODE.test(code) ? `错误码：${code}`
      : CODE.test(message) ? `错误码：${message}`
        : /^plugin bridge request failed\.?$/i.test(message) ? "宿主页面桥接请求未返回结果"
          : /failed to fetch|networkerror|network request failed/i.test(message) ? "网络连接中断"
            : message ? `错误信息：${message}` : "";
    const suffix = detail ? `（${detail}）` : "";
    return `核对请求未收到确认${suffix}；原保存请求已保留。`;
  }
  function automaticNotice(effective) {
    if (!effective || effective.automatic_allowed !== false) return null;
    const code = typeof effective.automatic_reason === "string" ? effective.automatic_reason : "";
    /* 授权、宿主、暂停等原因已经由环心的主原因说明；只有自动联系
       自己还有一条额外阻断时才另列一行，避免「开关已开」却显示成
       「后台自动联系未开启」的假象。 */
    if (!code || code === effective.reason) return null;
    const text = reason(code);
    return {code, text: code === "contact_automatic_disabled"
      ? "后台自动联系未开启" : (text === "联系条件暂时无法核对，请重新读取状态。"
        ? "后台自动联系当前受限。" : "后台自动联系：" + text)};
  }
  /* 时刻 HH:MM → 一天中的分钟数。跨午夜的区间靠 from > to 判定，不做取模，
     因为后端明确要求「窗口必须当天内结束」，只有免打扰允许跨午夜。 */
  const minutes = value => {
    const m = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(typeof value === "string" ? value.trim() : "");
    return m ? Number(m[1]) * 60 + Number(m[2]) : null;
  };
  const hm = value => {
    const total = Math.round(value) % 1440;
    return String(Math.floor(total / 60)).padStart(2, "0") + ":" + String(total % 60).padStart(2, "0");
  };
  const curveOf = value => {
    const parts = String(value ?? "").replace(/，/g, ",").split(",");
    if (parts.length !== 24) return DEFAULT_CURVE.split(",").map(Number);
    return parts.map(p => { const n = Number(p); return Number.isFinite(n) ? Math.max(0.05, Math.min(2, n)) : 1; });
  };
  const curveText = list => list.map(w => w.toFixed(2)).join(",");
  const tierOf = value => {
    const raw = String(value ?? "").trim().toLowerCase();
    return TIER_BY_KEY[raw] ? raw : (TIER_ALIAS[raw] || "off");
  };
  /* 一天里的第几分钟（带小数）→ 环上的角度。0:00 在正上方，顺时针。 */
  const angle = minute => (minute / 1440) * 360 - 90;
  const point = (cx, cy, r, minute) => {
    const a = angle(minute) * Math.PI / 180;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };
  /* 一段圆弧的 path。跨午夜（from > to）时拆成两段——SVG 的弧不会自动绕圈。 */
  function arc(cx, cy, r, from, to, width) {
    const spans = from > to ? [[from, 1440], [0, to]] : [[from, to]];
    return spans.map(([a, b]) => {
      if (b - a <= 0.5) return "";
      const [x1, y1] = point(cx, cy, r, a), [x2, y2] = point(cx, cy, r, b);
      const large = b - a > 720 ? 1 : 0;
      return `<path d="M${x1.toFixed(2)} ${y1.toFixed(2)} A${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}"`
        + ` fill="none" stroke-width="${width}"/>`;
    }).join("");
  }
  const ICON = {
    dial:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="12" cy="12" r="8.4"/><path d="M12 7.4V12l3.2 2"/></svg>',
    gate:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h16"/><path d="M7 8v8M17 8v8"/></svg>',
    seed:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v7"/><path d="M8.5 6.5 12 10l3.5-3.5"/><path d="M5 14h14v6H5z"/></svg>',
    quota:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M5 19V9M10 19V5M15 19v-6M20 19v-9"/></svg>',
    source:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16M4 12h16M4 18h10"/><circle cx="18.5" cy="18" r="2"/></svg>',
    test:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4.5h6M8.5 10h7M8.5 14h7M8.5 18h4"/><path d="m8.5 8.5 1 1 1.7-2"/></svg>',
    copy:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h2"/></svg>',
    mute:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9v6h3l4 3V6L9 9z"/><path d="m16 9 4 6M20 9l-4 6"/></svg>',
    refresh:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12a7 7 0 1 1-2.1-5"/><path d="M19 4v4h-4"/></svg>',
    alert:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v5M12 16h.01"/></svg>',
    check:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5L19 7"/></svg>',
    save:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 4h11l3 3v13H5z"/><path d="M9 4v5h6"/></svg>',
    arrow:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h13"/><path d="m13 6 6 6-6 6"/></svg>',
    undo:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10h11a4 4 0 0 1 0 8H8"/><path d="m8 6-4 4 4 4"/></svg>',
    /* 外部触发源那一屏的分组图标。几何都用圆、矩形、直线拼，
       不用一条长贝塞尔画云或碗：16px 下曲线走形比抽象更难看。 */
    weather:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="9.2" cy="12.4" r="3.2"/><circle cx="14.4" cy="12.9" r="2.5"/><path d="M6.2 15.6h11.6"/></svg>',
    bell:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6.6 10.2a5.4 5.4 0 0 1 10.8 0c0 3.1.8 4.5 1.4 5.3H5.2c.6-.8 1.4-2.2 1.4-5.3z"/><path d="M10.2 18.4a2 2 0 0 0 3.6 0"/></svg>',
    meal:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4.2 11.4h15.6a7.8 7.8 0 0 1-15.6 0z"/><path d="M9.4 8.2c0-1.1.8-1.5.8-2.4M13.6 8.2c0-1.1.8-1.5.8-2.4"/></svg>',
    compass:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.2"/><path d="m14.9 9.1-1.8 3.9-3.9 1.8 1.8-3.9z"/></svg>',
    screen:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3.4" y="5" width="17.2" height="11.4" rx="1.8"/><path d="M9.4 19.8h5.2"/></svg>',
    context:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 6.4h14M5 11.2h9M5 16h7"/><circle cx="18" cy="16.4" r="2.4"/></svg>',
    purse:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3.6" y="7.4" width="16.8" height="11.2" rx="2"/><path d="M3.6 11.6h16.8"/><path d="M15.4 15.4h2.2"/></svg>',
  };
  /* 哪个字段属于盘面的哪一部分。改动后只闪那一片：
     整盘一起动等于什么都没说清，用户看不出自己改的是哪一圈。
     盘面之外没有的字段一律不进这张表（找不到就说明「不需要动画」）。 */
  const PULSE_PART = {
    quiet_enabled:"quiet", quiet_start:"quiet", quiet_end:"quiet",
    proactive_hour_activity_enabled:"curve", proactive_hour_activity_curve:"curve",
    enable_daily_greetings:"greet", allow_insomnia_night_message:"greet",
    insomnia_night_start:"greet", insomnia_night_end:"greet",
    greeting_morning_window:"greet", greeting_noon_window:"greet", greeting_evening_window:"greet",
    quiet_timezone:"cursor",
    enabled:"center", automatic_enabled:"center",
    proactive_intensity_preset:"tier",
  };
  let root = null, bridge = null, version = 0, data = null;
  /* 联系设置有不少页面内筛选与未保存草稿。路由切换会卸载本页，按安装
     和管理账号在内存保留这些普通字段；令牌不进入缓存，回来后由真实接口
     重新读取。 */
  const contactCache = new Map();
  let contactScope = "";
  function currentContactScope() {
    try {
      const identity = window.CompanionProduct?.identity?.() || {};
      return JSON.stringify([identity.installation_id || "", identity.operator_id || ""]);
    } catch (_) { return "default"; }
  }
  function cacheContact() {
    if (!data || !contactScope) return;
    const hasGlobalSettings = data.configurationLoaded && data.configScope === "installation"
      && data.settings && typeof data.settings === "object";
    contactCache.set(contactScope, {
      ...data, settings:hasGlobalSettings ? {...data.settings} : null, editToken:"", configToken:"",
      configurationLoaded:!!hasGlobalSettings, busy:false, loading:false, contractLoading:false,
      ready:false, effective:null, effectiveSettings:null, purposes:{},
      confirmTest:"", testBusy:"", testUncertain:data.testUncertain || !!data.testBusy,
      testGuard:data.testGuard || (data.testBusy ? {purpose:data.testBusy, state:"pending"} : null),
      contractError:"", contractErrorCode:"", materialsLoading:false, touched:[...data.touched],
      draft:{...data.draft}, pulse:new Set(), pulseNow:[], flip:null, flipNow:null, viewShift:null, viewShiftNow:null,
    });
  }
  function pendingWriteKey() {
    const identity = window.CompanionProduct?.identity?.() || {};
    return STORE + "pending:" + JSON.stringify([
      identity.installation_id || "", identity.operator_id || "", contactScope, data?.configPath || "",
    ]);
  }
  function proactiveTestGuardKey() {
    const identity = window.CompanionProduct?.identity?.() || {};
    return STORE + "test-guard:" + JSON.stringify([
      identity.installation_id || "", identity.operator_id || "", contactScope,
    ]);
  }
  function writeHistoryPending(key, value) {
    try {
      const current = window.history.state;
      const state = current && typeof current === "object" && !Array.isArray(current)
        ? {...current} : current == null ? {} : {[HISTORY_PREVIOUS]: current};
      const pending = state[HISTORY_PENDING] && typeof state[HISTORY_PENDING] === "object"
        ? {...state[HISTORY_PENDING]} : {};
      if (value) pending[key] = JSON.stringify(value);
      else delete pending[key];
      if (Object.keys(pending).length) state[HISTORY_PENDING] = pending;
      else delete state[HISTORY_PENDING];
      const next = !Object.keys(state).length ? null
        : Object.keys(state).length === 1 && HISTORY_PREVIOUS in state ? state[HISTORY_PREVIOUS] : state;
      window.history.replaceState(next, "");
      return true;
    } catch { return false; }
  }
  function readHistoryPending(key) {
    try {
      const stored = window.history.state?.[HISTORY_PENDING]?.[key];
      return typeof stored === "string" ? JSON.parse(stored) : null;
    } catch { return null; }
  }
  function storeProactiveTestGuard(value) {
    const key = proactiveTestGuardKey();
    if (value) proactiveTestGuards.set(key, JSON.stringify(value));
    else proactiveTestGuards.delete(key);
    if (value) {
      for (const storage of [() => localStorage, () => sessionStorage]) {
        try { storage().setItem(key, JSON.stringify(value)); return true; } catch {}
      }
      return writeHistoryPending(key, value);
    }
    let removed = false;
    for (const storage of [() => localStorage, () => sessionStorage]) {
      try { storage().removeItem(key); removed = true; } catch {}
    }
    return writeHistoryPending(key, null) || removed;
  }
  function restoreProactiveTestGuard() {
    const key = proactiveTestGuardKey();
    for (const read of [
      () => proactiveTestGuards.get(key),
      () => localStorage.getItem(key),
      () => sessionStorage.getItem(key),
      () => readHistoryPending(key),
    ]) try {
      const stored = read();
      const value = typeof stored === "string" ? JSON.parse(stored) : stored;
      if (value && PURPOSE_META[value.purpose]
          && ["pending", "unknown"].includes(value.state)) return value;
    } catch {}
    return null;
  }
  function storePendingWrite() {
    const key = pendingWriteKey();
    // Sandboxed Dashboard pages may block both storage and history. Keep the
    // same operation in this page session while its original response is unknown.
    if (data?.pendingWrite) pendingWrites.set(key, JSON.stringify(data.pendingWrite));
    else pendingWrites.delete(key);
    try {
      if (data?.pendingWrite) {
        sessionStorage.setItem(key, JSON.stringify(data.pendingWrite));
        return true;
      }
    } catch {}
    if (data?.pendingWrite) {
      try {
        localStorage.setItem(key, JSON.stringify(data.pendingWrite));
        return true;
      } catch { return writeHistoryPending(key, data.pendingWrite) || pendingWrites.has(key); }
    }
    let removed = false;
    for (const storage of [() => sessionStorage, () => localStorage]) {
      try { storage().removeItem(key); removed = true; } catch {}
    }
    return writeHistoryPending(key, null) || removed;
  }
  function restorePendingWrite() {
    if (data?.pendingWrite) return data.pendingWrite;
    const key = pendingWriteKey();
    for (const read of [
      () => pendingWrites.get(key),
      () => sessionStorage.getItem(key),
      () => localStorage.getItem(key),
      () => readHistoryPending(key),
    ]) try {
      const stored = read();
      const value = typeof stored === "string" ? JSON.parse(stored) : stored;
      const body = value?.body;
      if (value?.route === data.configPath + "/configure" && body?.section === GROUP
          && typeof body.operation_key === "string" && body.operation_key.length > 0 && body.operation_key.length <= 128
          && typeof body.edit_token === "string" && body.edit_token.length === 64
          && body.changes && typeof body.changes === "object" && !Array.isArray(body.changes)
          && Object.keys(body.changes).length) return value;
    } catch {}
    return null;
  }

  function schedulePendingRecovery(ticket = version) {
    const operationKey = data?.pendingWrite?.body?.operation_key;
    if (!operationKey) return;
    if (autoRecoveryKey !== operationKey) {
      if (autoRecoveryTimer !== null) clearTimeout(autoRecoveryTimer);
      autoRecoveryKey = operationKey;
      autoRecoveryAttempts = 0;
    }
    if (autoRecoveryTimer !== null) return;
    const delay = Math.min(AUTO_RECOVERY_INITIAL_DELAY * (2 ** Math.min(autoRecoveryAttempts,
      AUTO_RECOVERY_MAX_EXPONENT)), AUTO_RECOVERY_MAX_DELAY);
    autoRecoveryTimer = setTimeout(() => {
      autoRecoveryTimer = null;
      if (!live(ticket) || data.pendingWrite?.body?.operation_key !== operationKey) return;
      if (!settingsAvailable() || data.busy || data.loading || data.contractLoading) {
        schedulePendingRecovery(ticket);
        return;
      }
      autoRecoveryAttempts = Math.min(autoRecoveryAttempts + 1, AUTO_RECOVERY_MAX_EXPONENT);
      void save();
    }, delay);
  }

  function clearPendingRecovery(operationKey) {
    if (autoRecoveryKey !== operationKey) return;
    if (autoRecoveryTimer !== null) clearTimeout(autoRecoveryTimer);
    autoRecoveryTimer = null;
    autoRecoveryKey = "";
    autoRecoveryAttempts = 0;
  }

  function localDayKey() {
    const now = new Date();
    return [now.getFullYear(), String(now.getMonth() + 1).padStart(2, "0"),
      String(now.getDate()).padStart(2, "0")].join("-");
  }
  function localTimezone() {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; }
    catch { return "UTC"; }
  }
  function blank() {
    return {view:"dial", purposeQuery:"", purposeBucket:"all", purposeFilter:"all",
      testQuery:"", testBucket:"all", testCoverage:"all", materialQuery:"", materialCategory:"all",
      materialKind:"window", materialWindow:"all", materialDay:localDayKey(), materialTimezone:localTimezone(),
      nativePath:"", configPath:"",
      settings:null, effective:null, effectiveSettings:null, purposes:{}, hostState:"",
      materialItems:[], materialsLoaded:false, materialsLoading:false, materialsError:"",
      materialsState:"not_checked", materialsCheckedAt:"", materialsPatrolPolls:0,
      materialsSharedCount:0, materialsTraceCount:0, materialsTraceTotal:0,
      materialsTraceState:"not_checked", materialsTraceContexts:0, materialsTraceUnavailable:0,
       editToken:"", configToken:"", configScope:"", configurationLoaded:false,
       contractLoading:false, contractError:"", contractErrorCode:"",
      /* 草稿：只放用户真的改过的字段。提交时只发这些，未动的字段一个都不发——
         避免把「没看过的字段」当成「要保持的值」写回去。 */
      draft:{}, touched:new Set(), tierDraft:null, pendingWrite:null,
      confirmTest:"", testBusy:"", testUncertain:false, testGuard:null, testResults:{}, testModes:{}, testVariants:{},
      /* pulse 是「下一次渲染要闪哪几片盘面」，pulseNow 是这一帧真正在闪的。
         两者分开是因为 render() 每次都会重写 DOM，类必须由渲染时的状态决定，
         当场往节点上加类会被紧接着的重写丢掉（切档位那次动画就是这么消失的）。 */
      pulse:new Set(), pulseNow:[],
      /* flip 是「这一次谁被扳动了、扳向哪一边」，flipNow 是这一帧要放动画的那一个。
         开关本体没有 transition 的落脚点：点一下就整块重写 DOM，新节点带着终态出生，
         再漂亮的 transition 也一帧都放不出来——于是「扳」这个动作在界面上是凭空跳变。
         所以两个方向都用一次性 keyframes 播：toggle() / srcToggle() 按 flipNow 写类。 */
      flip:null, flipNow:null,
      /* 换页方向：1 往后、-1 往回、null 没换页。轨道上被点中的那一项也要跟着
         响一下——轨道按钮同样是每次渲染新建的，transition 照样不响，
         所以选中态也得走一次性动画，不然点导航就是「啪」地贴上去。 */
      viewShift:null, viewShiftNow:null,
      busy:false, loading:false, ready:false, scanning:true,
      notice:"", error:false, savedAt:null};
  }
  const value = name => name in (data?.draft || {}) ? data.draft[name] : data?.settings?.[name];
  const observedValue = name => data?.settings?.[name];
  const settingsAvailable = () => !!data?.settings && data.configurationLoaded === true;
  function rel(absolute) {
    if (typeof absolute !== "string" || !absolute.startsWith(PREFIX)) throw problem("invalid_response");
    const route = absolute.slice(PREFIX.length);
    if (!route || /[?#%\\:]/.test(route) || route.split("/").some(p => !p || p === "." || p === "..")) {
      throw problem("invalid_response");
    }
    return route;
  }
  async function bounded(promise, timeoutMs = 15000) {
    let timeout;
    try {
      return await Promise.race([promise, new Promise((_, reject) => {
        timeout = setTimeout(() => reject(problem("bridge_timeout")), timeoutMs);
      })]);
    } finally { clearTimeout(timeout); }
  }
  async function api(method, path, body, timeoutMs = 15000) {
    let response;
    try {
      response = await bounded(method === "GET" ? bridge.apiGet(path, body) : bridge.apiPost(path, body), timeoutMs);
    } catch (error) {
      const message = typeof error?.message === "string" ? error.message.trim() : "";
      if (!error?.code && CODE.test(message) && Object.prototype.hasOwnProperty.call(errors, message)
          && message !== "bridge_timeout" && message !== "bridge_unavailable") {
        throw Object.assign(problem(message), {confirmed:true});
      }
      throw error;
    }
    const wrapped = ["ok","error"].includes(response?.status) && response?.data !== undefined ? response
      : ["ok","error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    if (wrapped?.status === "error") throw Object.assign(problem(CODE.test(wrapped.data?.code || wrapped.message || "")
      ? (wrapped.data?.code || wrapped.message) : "invalid_response"), {confirmed:true});
    const value = wrapped ? wrapped.data : response;
    if (!value || typeof value !== "object") throw problem("invalid_response");
    return value;
  }
  function live(ticket) { return ticket === version && root?.isConnected; }
  function notice(message, error = false) { data.notice = message; data.error = error; }

  /* ---- 读 ------------------------------------------------------------- */
  /* 全局页只读取安装级配置，不依赖宿主当前打开的会话。 */
  async function loadContract() {
    if (!data.configPath || data.contractLoading) return;
    const ticket = version;
    data.configToken = "";
    data.contractLoading = true;
    render();
    try {
      const payload = await api("GET", data.configPath, {sections: GROUP});
      if (!live(ticket)) return;
      const section = payload?.sections?.[GROUP];
      if (!section || typeof section !== "object" || section.scope !== "installation"
          || typeof section.edit_token !== "string" || !section.edit_token
          || !section.settings || typeof section.settings !== "object" || Array.isArray(section.settings))
        throw problem("invalid_response");
      data.configToken = section.edit_token;
      data.settings = {...section.settings};
      data.configurationLoaded = true;
      data.configScope = typeof section.scope === "string" ? section.scope : "";
      data.ready = true; data.scanning = true;
      data.contractError = "";
      data.contractErrorCode = "";
    } catch (error) {
      if (live(ticket)) {
        data.contractErrorCode = typeof error.code === "string" ? error.code : "";
        data.contractError = "全局设置未能读取，当前不会使用会话设置代替；请重新读取配置。";
      }
    } finally {
      if (live(ticket)) {
        data.contractLoading = false; render();
        if (settingsAvailable() && data.pendingWrite) schedulePendingRecovery(ticket);
      }
    }
  }
  async function loadMaterials({patrolPoll = false} = {}) {
    if (!data.nativePath || data.materialsLoading) return;
    if (!patrolPoll) data.materialsPatrolPolls = 0;
    const ticket = version;
    data.materialsLoading = true;
    data.materialsError = "";
    let receivedStatus = false;
    render();
    try {
      const payload = await api("GET", data.nativePath + "/materials",
        {day:data.materialDay, timezone:data.materialTimezone});
      if (!live(ticket)) return;
      if (!Array.isArray(payload?.items)) throw problem("invalid_response");
      data.materialItems = payload.items.filter(item => item && typeof item === "object"
        && typeof item.id === "string" && typeof item.title === "string"
        && (item.scope === "role" && !item.usage
          || item.scope === "window" && item.usage && typeof item.usage.status === "string"));
      if (data.materialWindow !== "all"
          && !data.materialItems.some(item => item.scope === "window" && materialWindowKey(item) === data.materialWindow)) {
        data.materialWindow = "all";
      }
      data.materialsState = typeof payload.state === "string" ? payload.state : "ready";
      data.materialsCheckedAt = typeof payload.checked_at === "string" ? payload.checked_at : "";
      data.materialsSharedCount = Number.isInteger(payload.shared_count)
        ? payload.shared_count : data.materialItems.filter(item => item.scope === "role").length;
      data.materialsTraceCount = Number.isInteger(payload.trace_count)
        ? payload.trace_count : data.materialItems.filter(item => item.scope === "window").length;
      data.materialsTraceTotal = Number.isInteger(payload.history_count)
        ? payload.history_count : data.materialsTraceCount;
      data.materialsTraceState = typeof payload.trace_state === "string" ? payload.trace_state : "unavailable";
      data.materialsTraceContexts = Number.isInteger(payload.trace_context_count)
        ? payload.trace_context_count : 0;
      data.materialsTraceUnavailable = Number.isInteger(payload.trace_context_unavailable)
        ? payload.trace_context_unavailable : 0;
      data.materialsLoaded = true;
      receivedStatus = true;
    } catch (error) {
      if (!live(ticket)) return;
      data.materialsError = error.code ? reason(error.code) : "素材暂时无法读取，请重试。";
    } finally {
      if (live(ticket)) {
        data.materialsLoading = false; render();
        if (receivedStatus && ["refreshing", "not_checked"].includes(data.materialsState)
            && data.materialsPatrolPolls < 3) {
          data.materialsPatrolPolls += 1;
          const delay = [1500, 3500, 5000][data.materialsPatrolPolls - 1];
          setTimeout(() => {
            if (live(ticket) && data.view === "materials" && !data.materialsLoading)
              void loadMaterials({patrolPoll:true});
          }, delay);
        } else if (!["refreshing", "not_checked"].includes(data.materialsState)) {
          data.materialsPatrolPolls = 0;
        }
      }
    }
  }
  /* ---- 写 -------------------------------------------------------------
     只提交 touched 里的字段。edit_token 必须用**刚刚读到的那一份**：
     配置页的令牌与 native 的令牌是两个生效范围，不能互相顶替
     （native 那条路由只写 news 六个字段，其余交给 config 段）。 */
  async function save() {
    if (!data.configPath || data.busy || (!data.pendingWrite && !data.touched.size)) return;
    if (!settingsAvailable()) { notice("还没有读到当前设置，不能保存。", true); render(); return; }
    if (!data.configToken && !data.pendingWrite) { notice("缺少配置令牌，请重新读取后再保存。", true); render(); return; }
    const ticket = version;
    data.busy = true;
    let pending = data.pendingWrite;
    let changes = pending?.body?.changes || {};
    if (!pending) {
      changes = {};
      for (const name of data.touched) if (name in data.draft) changes[name] = data.draft[name];
      pending = {route:data.configPath + "/configure", body:{
        section:GROUP, operation_key:operationKey(), edit_token:data.configToken, changes,
      }};
      data.pendingWrite = pending;
      if (!storePendingWrite()) {
        data.pendingWrite = null;
        data.busy = false;
        notice("当前浏览器无法安全记录这次保存请求，尚未提交。请检查页面存储后重试。", true);
        render();
        return;
      }
    }
    try {
      const payload = await api("POST", pending.route, pending.body);
      if (!live(ticket)) return;
      const receipt = payload?.result ?? payload;
      if (typeof receipt?.operation_id !== "string" || typeof receipt?.replayed !== "boolean") {
        throw problem("invalid_response");
      }
      const status = payload?.status?.sections?.[GROUP];
      const hasFreshConfigToken = typeof status?.edit_token === "string" && status.edit_token.length === 64;
      if (hasFreshConfigToken) {
        data.configToken = status.edit_token;
      }
      data.pendingWrite = null; storePendingWrite();
      clearPendingRecovery(pending.body.operation_key);
      if (data.configurationLoaded) data.settings = {...data.settings, ...changes};
      data.touched.clear(); data.draft = {}; data.tierDraft = null;
      data.savedAt = Date.now(); data.busy = false;
      const savedNotice = "已保存 " + Object.keys(changes).length + " 项。改动从下一轮主动评估开始生效。";
      notice(savedNotice);
      if (live(ticket)) await loadContract();
      if (live(ticket)) {
        const refreshed = settingsAvailable() && !!data.configToken;
        notice(refreshed ? savedNotice : "已保存 " + Object.keys(changes).length + " 项，但当前状态未能刷新；请稍后重新读取。", !refreshed);
        render();
      }
    } catch (error) {
      if (!live(ticket)) return;
      data.busy = false;
      const code = error.code || "";
      if (error.confirmed) {
        data.pendingWrite = null; storePendingWrite();
        clearPendingRecovery(pending?.body?.operation_key);
      }
      const unresolved = !!data.pendingWrite && !error.confirmed;
      notice(unresolved ? recoveryFailure(error) : reason(code), true);
      if (["configuration_revision_conflict", "proactive_contact_revision_conflict"].includes(code)) {
        /* A revision conflict rejects only the stale write.  Keep the fields
           the user edited while refreshing the authoritative settings and
           token, so a concurrent update cannot turn into lost UI work.  The
           next save submits touched fields only, which preserves unrelated
           changes made by the other writer. */
        const conflictedDraft = {...data.draft};
        const conflictedTouched = new Set(data.touched);
        const conflictedTier = data.tierDraft;
        await loadContract();
        if (live(ticket)) {
          data.draft = conflictedDraft;
          data.touched = conflictedTouched;
          data.tierDraft = conflictedTier;
          notice(settingsAvailable() && data.configToken
            ? "设置已有其他更新，已读取最新值；未保存的改动仍保留，请核对后再次保存。"
            : "设置已有其他更新，但最新值暂时无法读取；未保存的改动仍保留，请重新读取后再保存。",
          !(settingsAvailable() && data.configToken));
          render();
        }
      } else if (code === "proactive_contact_target_changed") {
        data.touched.clear(); data.draft = {}; data.tierDraft = null;
        await loadContract();
        if (live(ticket)) {
          notice(data.configToken
            ? "联系配置目标已变化，已读取新目标设置；请核对后重新编辑。"
            : "联系配置目标已变化，但新目标设置暂时无法读取；请重新读取。", !data.configToken);
          render();
        }
      } else {
        /* notice() 只改状态；失败路径必须主动重画，否则用户点保存后
           看不到任何反馈，直到下一次切页或别的控件触发 render。 */
        render();
        if (data.pendingWrite) schedulePendingRecovery(ticket);
      }
    }
  }
  /* The backend combines the operation key with the authorized target, so the
     key stays short even when a target's route contains long opaque identifiers. */
  let opCounter = 0;
  function operationKey() {
    return STORE + Date.now().toString(36) + ":" + Math.random().toString(36).slice(2, 10) + ":" + opCounter++;
  }
  function set(name, value) {
    if (data.pendingWrite) return;
    data.draft[name] = value; data.touched.add(name);
    if (name === "proactive_intensity_preset") data.tierDraft = tierOf(value);
    const part = PULSE_PART[name];
    if (part) data.pulse.add(part);
  }
  function resetDraft() { if (data.pendingWrite) return; data.touched.clear(); data.draft = {}; data.tierDraft = null; render(); }
  /* 这一帧要不要给这个开关播翻转动画、播哪个方向。
     方向由 flipNow 记述，这里只翻译成类，不改动词义。 */
  function flipClass(name) {
    const flip = data.flipNow;
    if (!flip || flip.name !== name) return "";
    return flip.on ? " is-flip-on" : " is-flip-off";
  }
  function dirty(name) { return data.touched.has(name); }

  /* This page shows installation defaults only. Per-person authorization and
     delivery readiness belong to the private contact view. */
  function currentMinute(settings) {
    const zone = settings ? settings.quiet_timezone : value("quiet_timezone");
    if (typeof zone !== "string" || !zone.trim()) return null;
    try {
      const parts = new Intl.DateTimeFormat("en-GB", {timeZone:zone, hour:"2-digit", minute:"2-digit",
        hour12:false}).formatToParts(new Date());
      const h = Number(parts.find(p => p.type === "hour")?.value);
      const m = Number(parts.find(p => p.type === "minute")?.value);
      return Number.isFinite(h) && Number.isFinite(m) ? h * 60 + m : null;
    } catch { return null; }
  }
  function quietSpan(settings) {
    const from = minutes(settings ? settings.quiet_start : value("quiet_start"));
    const to = minutes(settings ? settings.quiet_end : value("quiet_end"));
    if (from === null || to === null) return null;
    return {from, to};
  }
  function inQuiet(minute, settings) {
    const span = quietSpan(settings);
    if (span === null || !(settings ? settings.quiet_enabled : value("quiet_enabled")) || minute === null) return false;
    const {from, to} = span;
    return from === to || (from > to ? (minute >= from || minute < to) : (minute >= from && minute < to));
  }
  function currentWeight(minute, settings) {
    if (!(settings ? settings.proactive_hour_activity_enabled : value("proactive_hour_activity_enabled")) || minute === null) return null;
    return curveOf(settings ? settings.proactive_hour_activity_curve : value("proactive_hour_activity_curve"))[Math.floor(minute / 60) % 24];
  }
  /* Project the saved installation settings onto the configured quiet hours
     and activity curve; never present this as a per-person authorization check. */
  function verdict() {
    if (!settingsAvailable()) {
      return {tone:"blocked", title:"全局设置尚未读取", detail:"当前页面只显示安装级默认配置。", code:"", automatic:false};
    }
    const settings = data.settings;
    const minute = currentMinute(settings);
    const pauseUntil = observedValue("pause_until");
    if (observedValue("enabled") === false) {
      return {tone:"blocked", title:"全局默认已关闭", detail:"具体私聊用户可在私聊用户页单独设置。", code:"", automatic:false};
    }
    if (typeof pauseUntil === "string" && Date.parse(pauseUntil) > Date.now()) {
      return {tone:"blocked", title:"全局暂停中", detail:"暂缓至 " + pauseUntil.slice(0, 16).replace("T", " "), code:"", automatic:false};
    }
    if (inQuiet(minute, settings)) {
      return {tone:"quiet", title:"全局免打扰时段", detail:quietSpanText(settings),
        code:"", automatic:false};
    }
    const weight = currentWeight(minute, settings);
    if (weight !== null && weight < 0.3) {
      return {tone:"low", title:"全局节律低谷", detail:"这一小时的活动权重 " + weight.toFixed(2)
        + "，低于 0.30；这条曲线只删格子，不放宽额度。", code:"", automatic:false};
    }
    const automatic = observedValue("automatic_enabled") === true ? null
      : {text:"后台自动联系默认关闭"};
    return {tone:"open", title:"全局默认已开启", detail:minute === null
      ? "具体用户的授权与私聊设置在私聊用户页管理。"
      : "按 " + hm(minute) + " 的全局时段规则；具体用户的授权与私聊设置在私聊用户页管理。",
      code:"", automatic};
  }
  function quietSpanText(settings) {
    const span = quietSpan(settings);
    if (span === null) return "免打扰时段未设完整。";
    return span.from > span.to
      ? hm(span.from) + " 至次日 " + hm(span.to) + " 不打扰"
      : hm(span.from) + " 至 " + hm(span.to) + " 不打扰";
  }

  /* ---- 环的绘制 ---------------------------------------------------------
     四圈半径固定，只有「哪一圈在编辑」会改变描边权重。
     这不是装饰：一天只有 24 格，四圈都画粗了就分不清哪圈是免打扰。 */
  const R = {quiet:132, curve:112, anchor:94, tier:76};
  function dialSVG() {
    const cx = 150, cy = 150, minute = currentMinute();
    const span = quietSpan();
    const live = value("quiet_enabled") && span;
    const weights = curveOf(value("proactive_hour_activity_curve"));
    const curveOn = !!value("proactive_hour_activity_enabled");
    const peak = curveOn ? Math.max(...weights, 0.05) : 1;
    /* 曲线画成 24 个柱子，围成一圈。柱高按峰值归一，但**保留 0.05 的下限高度**，
       否则权重 0.10 的凌晨会看起来是「没有这一格」。
       --i 是「到 0:00 的环形距离」，涟漪与改动波都按它排队：
       波前从正上方出发、两侧同时走，在 12:00 会合——按下标排就只有单向推进。 */
    const bars = weights.map((w, hour) => {
      const ratio = curveOn ? Math.max(0.05, w / peak) : 1;
      const inner = R.curve - 9, outer = R.curve + 9;
      const r = inner + (outer - inner) * ratio;
      const [x1, y1] = point(cx, cy, inner, hour * 60 + 1);
      const [x2, y2] = point(cx, cy, r, hour * 60 + 1);
      const hot = curveOn && w >= peak * 0.75;
      return `<line x1="${x1.toFixed(2)}" y1="${y1.toFixed(2)}" x2="${x2.toFixed(2)}" y2="${y2.toFixed(2)}"`
        + ` class="ct-bar${curveOn ? " is-on" : ""}${hot ? " is-peak" : ""}"`
        + ` style="--i:${Math.min(hour, 24 - hour)}" data-hour="${hour}"/>`;
    }).join("");
    /* 三圈十二个刻度：0/6/12/18 写数字，其余只点一下。 */
    const ticks = Array.from({length:24}, (_, hour) => {
      const [x1, y1] = point(cx, cy, R.quiet + 6, hour * 60);
      const [x2, y2] = point(cx, cy, R.quiet + (hour % 6 === 0 ? 14 : 10), hour * 60);
      const major = hour % 6 === 0;
      const label = major ? (() => {
        const [lx, ly] = point(cx, cy, R.quiet + 27, hour * 60);
        return `<text x="${lx.toFixed(1)}" y="${(ly + 4).toFixed(1)}" class="ct-tick-label">${hour}</text>`;
      })() : "";
      return `<line x1="${x1.toFixed(2)}" y1="${y1.toFixed(2)}" x2="${x2.toFixed(2)}" y2="${y2.toFixed(2)}"`
        + ` class="ct-tick${major ? " is-major" : ""}"/>${label}`;
    }).join("");
    /* 问候窗口：只有**启用每日问候**时才画。
       原先的判据是 `enable_daily_greetings || value("greeting_x_window")`，
       而后端对这九个字段都有默认值，所以右边恒为真——三个窗口在未启用时
       照样画在环上。那是在暗示一个并不存在的时刻：用户会以为早上七点四十五
       会收到问候，而实际上总开关是关的。画不出的东西就不画。 */
    const greetingsOn = value("enable_daily_greetings") === true;
    const anchors = greetingsOn ? ANCHORS.map(([name, , fallbackFrom, fallbackTo], index) => {
      const raw = value("greeting_" + name + "_window");
      const parts = typeof raw === "string" ? raw.split("-") : [];
      const from = minutes(parts[0]) ?? minutes(fallbackFrom);
      const to = minutes(parts[1]) ?? minutes(fallbackTo);
      /* 后端要求窗口当天内结束（不能跨午夜），所以 from > to 是坏值，不画。 */
      if (from === null || to === null || from >= to) return "";
      /* --a 是三条弧的序号：改动波按早/午/晚依次亮起，顺序就是一天的顺序。 */
      return `<g class="ct-anchor" data-anchor="${name}" style="--a:${index}">`
        + `${arc(cx, cy, R.anchor, from, to, 7)}</g>`;
    }).join("") : "";
    const insomnia = value("allow_insomnia_night_message")
      ? `<g class="ct-insomnia" style="--a:3">${arc(cx, cy, R.anchor - 11,
          minutes(value("insomnia_night_start")) ?? 1380, minutes(value("insomnia_night_end")) ?? 360, 3)}</g>` : "";
    /* 游标：一条从环心指出的细线，末端一个小圆。它随真实时间走，
       reduced-motion 下不做扫描动画，只停在当前角度。 */
    const cursor = minute === null ? "" : (() => {
      const [x, y] = point(cx, cy, R.quiet + 16, minute);
      const [ix, iy] = point(cx, cy, 30, minute);
      return `<line class="ct-cursor-line" x1="${ix.toFixed(2)}" y1="${iy.toFixed(2)}"`
        + ` x2="${x.toFixed(2)}" y2="${y.toFixed(2)}"/>`
        + `<circle class="ct-cursor-dot" cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="3.6"/>`;
    })();
    return `<svg viewBox="0 0 300 300" class="ct-dial${data.scanning ? " is-scanning" : ""}"
      role="img" aria-label="${e(dialLabel(minute))}" data-role="dial">
      <circle cx="${cx}" cy="${cy}" r="${R.quiet}" class="ct-ring-base"/>
      <g class="ct-quiet${live ? " is-on" : ""}">${live ? arc(cx, cy, R.quiet, span.from, span.to, 10) : ""}</g>
      <g class="ct-bars">${bars}</g>
      ${anchors}${insomnia}${ticks}${cursor}
    </svg>`;
  }
  function dialLabel(minute) {
    return "一天的节律：免打扰" + (value("quiet_enabled") ? quietSpanText() : "未启用")
      + "；活动曲线" + (value("proactive_hour_activity_enabled") ? "已启用" : "未启用")
      + "；当前" + (minute === null ? "未设时区" : hm(minute) + " 时");
  }

  /* ---- 渲染 ------------------------------------------------------------ */
  const VIEWS = [["dial","节律盘",ICON.dial],["morning","晨间唤醒",ICON.bell],["tests","消息测试",ICON.test],
                 ["purposes","契机矩阵",ICON.seed],
                 ["gates","开口闸门",ICON.gate],["budgets","额度与去重",ICON.quota],
                 ["sources","外部触发源",ICON.source],["materials","主动检视",ICON.seed]];
  function headMarkup() {
    const v = verdict();
    const label = VIEWS.find(([id]) => id === data.view)?.[1] || "节律盘";
    const dirtyCount = data.touched.size;
    return `<header class="ct-head">
      <div class="ct-head-main">
        <h2>${e(label)}</h2>
        <span class="ct-badge">全局设置</span>
        <span class="ct-verdict is-${v.tone}"><i class="ct-dot"></i>${e(v.title)}</span>
      </div>
      <div class="ct-head-actions">
        <button type="button" class="ct-btn ct-head-test" data-view="tests" aria-pressed="${data.view === "tests"}"
          title="打开消息测试">${ICON.test}<span>消息测试</span></button>
        ${dirtyCount && !data.pendingWrite ? `<button type="button" class="ct-btn" data-action="reset">${ICON.undo}<span>放弃 ${dirtyCount} 项改动</span></button>` : ""}
        ${data.pendingWrite ? "" : dirtyCount
          ? `<button type="button" class="ct-btn is-primary" data-action="save"${data.busy || data.contractLoading || !data.configToken ? " disabled" : ""}>${ICON.save}<span>保存</span></button>`
          : `<button type="button" class="ct-btn" data-action="refresh"${data.loading || data.busy || data.contractLoading ? " disabled" : ""}>${ICON.refresh}<span>重新读取</span></button>`}
      </div>
    </header>`;
  }
  function railMarkup() {
    return `<nav class="ct-rail" aria-label="联系工作台">${VIEWS.map(([id, text, icon]) =>
      `<button type="button" data-view="${id}" aria-pressed="${data.view === id}"`
      + `${data.view === id && data.viewShiftNow ? ' class="is-picked"' : ""}>
        <span class="ct-rail-icon">${icon}</span><span class="ct-rail-label">${e(text)}</span></button>`).join("")}</nav>`;
  }
  function noticeMarkup() {
    const notices = [];
    if (data.pendingWrite) {
      const pendingText = data.busy
        ? "保存请求处理中；系统会核对原操作结果。"
        : "保存结果尚未确认；系统会持续沿用同一操作标识核对，不会重复写入。";
      notices.push(`<div class="ct-notice is-error" role="status">${ICON.alert}`
      + `<span>${pendingText}`
      + `${data.error && data.notice ? `<small>${e(data.notice)}</small>` : ""}</span>`
      + `<button type="button" class="ct-notice-retry" data-action="retry-pending"`
      + `${data.busy || data.loading || data.contractLoading || !settingsAvailable() ? " disabled" : ""}>${ICON.refresh}`
      + `<span>${data.busy ? "处理中" : "立即核对"}</span></button></div>`);
    } else if (data.notice) notices.push(`<p class="ct-notice${data.error ? " is-error" : ""}" role="status">${data.error ? ICON.alert : ICON.check}`
      + `<span>${e(data.notice)}</span><button type="button" data-action="dismiss" aria-label="关闭">×</button></p>`);
    if (data.contractError) {
      const message = data.contractLoading
        ? "正在重新读取配置编辑信息；当前输入会保留。" : data.contractError;
      const detail = data.contractErrorCode ? ` title="接口错误码：${e(data.contractErrorCode)}"` : "";
      notices.push(`<p class="ct-notice is-error" role="status">${ICON.alert}`
        + `<span${detail}>${e(message)}</span><button type="button" class="ct-notice-retry" data-action="retry-contract"${data.contractLoading ? " disabled" : ""}>${ICON.refresh}<span>${data.contractLoading ? "读取中" : "重试"}</span></button></p>`);
    }
    return notices.join("");
  }
  function emptyMarkup() {
    if (data.loading || data.contractLoading) return `<div class="ct-empty"><span class="ct-spinner"></span>正在读取全局设置…</div>`;
    if (!data.configPath) return `<div class="ct-empty">${ICON.alert}<strong>全局设置接口未就绪</strong>
      <p>请确认 CompanionV2 已启动后重新读取。</p></div>`;
    return `<div class="ct-empty">${ICON.alert}<strong>还没有读到设置</strong>
      <p>${e(data.notice || "请重新读取；若反复失败，先在账号与会话中确认 V2 已启动。")}</p>
      <button type="button" class="ct-btn" data-action="refresh">${ICON.refresh}<span>重新读取</span></button></div>`;
  }

  /* 节律盘视图。版式分两层：
     第一行是「盘面 + 三个总开关 + 免打扰」——这几项一改就立刻改变盘面几何，
     放在同一行才能做到「改完不用滚动就看见环变了」。
     下面一层（活动曲线 / 强度档位 / 问候窗口）铺满整页宽：
     它们本来就是「按一天铺开的数据」，24 根柱子要的是横向空间，
     挤进右栏只会把每根柱子压到十来像素。
     控件顺序按「改了会立刻改变环的哪一圈」排，不按字段在注册表里的顺序。 */
  function dialView() {
    const span = quietSpan();
    const tier = TIER_BY_KEY[data.tierDraft || tierOf(value("proactive_intensity_preset"))];
    const v = verdict();
    const interval = Number(value("interval_seconds"));
    const idle = Number(value("idle_seconds"));
    const effectiveInterval = Number.isFinite(interval) && interval > 0
      ? Math.max(1, Math.round(interval * tier.interval)) : null;
    const effectiveIdle = Number.isFinite(idle)
      ? Math.max(0, Math.round(idle * tier.idle)) : null;
    /* 这一帧要闪哪几片盘面。取出来就清空：动效只放一次，
       下一次渲染（哪怕什么都没改）不该重放。 */
    const pulse = data.pulseNow;
    return `<div class="ct-dial-wrap">
      <div class="ct-dial-stage${pulse.map(part => " is-pulse-" + part).join("")}">${dialSVG()}
        <div class="ct-center is-${v.tone}">
          <span class="ct-center-title">${e(v.title)}</span>
          <span class="ct-center-detail">${e(v.detail)}</span>
          ${v.automatic ? `<span class="ct-center-flag">${ICON.alert}${e(v.automatic.text)}</span>` : ""}
        </div>
        ${data.scanning ? `<div class="ct-scan-hint">时间游标按 ${e(value("quiet_timezone") || "未设时区")} 走</div>` : ""}
      </div>
      <div class="ct-controls">
        ${fieldBlock("全局开关", [
          toggle("enabled", "允许主动联系", "单个用户的开关在私聊用户页调整。"),
          toggle("automatic_enabled", "后台自动联系", "未单独设置的用户跟随此值。"),
        ])}
        ${fieldBlock("免打扰 · 外圈", [
          toggle("quiet_enabled", "启用免打扰时段", "免打扰只决定「现在不开口」，不改动已登记的日程。"),
          timeRow("quiet_start", "开始", "quiet_end", "结束", span && span.from > span.to ? "跨午夜：" + hm(span.from) + " 至次日 " + hm(span.to) : ""),
          textRow("quiet_timezone", "时区", "Asia/Shanghai", "环上游标与免打扰判定都用这个时区。"),
        ], "is-ring-quiet")}
      </div></div>
    <div class="ct-rest">
      ${fieldBlock("活动曲线 · 次圈", [
        toggle("proactive_hour_activity_enabled", "按小时权重开口", "启用后这条曲线只会删掉落在低谷的格子，不放宽任何额度。"),
        curveEditor(),
      ], "is-wide is-ring-curve")}
      ${fieldBlock("强度档位 · 三圈", [
        tierPicker(tier),
        `<div class="ct-tier-effective" role="status" aria-live="polite">
          <span>当前生效</span>
          <strong>${effectiveInterval === null ? "—" : e(fmtDuration(effectiveInterval))}</strong>
          <small>两次联系至少间隔</small>
          <strong>${effectiveIdle === null ? "—" : e(fmtDuration(effectiveIdle))}</strong>
          <small>对话结束后至少等候</small>
        </div>`,
        numberRow("interval_seconds", "两次联系至少间隔（基础值）", "秒", 60, 604800,
          interval ? "当前档位实际约 " + fmtDuration(effectiveInterval) : ""),
        numberRow("idle_seconds", "对话结束后至少等候（基础值）", "秒", 0, 86400,
          idle !== undefined && Number.isFinite(idle) ? "当前档位实际约 " + fmtDuration(effectiveIdle) : ""),
      ], "is-ring-tier")}
      ${fieldBlock("问候窗口 · 内圈", [
        toggle("enable_daily_greetings", "启用每日问候", "三个窗口必须当天内结束，不能跨午夜。"),
        ...ANCHORS.map(([name, , from, to]) => textRow("greeting_" + name + "_window",
          {morning:"早间", noon:"午间", evening:"晚间"}[name], from + "-" + to, "形如 " + from + "-" + to)),
        toggle("allow_insomnia_night_message", "允许深夜一条", "深夜窗口显式有界，并按日限额。"),
      ], "is-ring-greet")}
    </div>`;
  }
  function morningView() {
    return `<div class="ct-rest">
      ${fieldBlock("晨间流程", [
        toggle("morning_chain_enabled", "启用晨间流程", "关闭后不会生成角色醒来问候或清醒确认提醒。"),
        timeRow("wake_morning_start", "开始", "wake_morning_end", "结束", "使用全局免打扰时区；窗口外不补发过期消息。"),
      ], "is-wide")}
      ${fieldBlock("角色作息", [
        toggle("morning_role_greeting_enabled", "角色醒来后先问候", "只判断角色自身醒来，不推断用户已经醒。"),
        toggle("routine_learning_enabled", "学习角色的起床习惯", "根据近期有效样本调整角色的预测起床窗口。"),
        numberRow("routine_window_days", "学习窗口", "天", 14, 180, "只使用最近这段时间的作息记录。"),
        decimalRow("morning_wake_confidence_floor", "预测可信度下限", 0, 1, 0.05,
          "预测把握低于此值时不生成晨间问候。"),
      ])}
      ${fieldBlock("清醒确认", [
        toggle("morning_nudge_enabled", "允许轻声确认", "仅在清醒证据充分、窗口有效且没有新输入时提醒。"),
        numberRow("morning_nudge_delay_minutes", "确认前等待", "分钟", 10, 90, "给对方自然开口的时间。"),
        numberRow("morning_nudge_max_attempts", "每次起床最多提醒", "次", 0, 2, "0 表示不提醒；默认最多一次。"),
        numberRow("wake_contact_window_minutes", "清醒后有效窗口", "分钟", 15, 240, "超时后不再翻旧账。"),
        numberRow("wake_contact_min_confidence", "清醒迹象可信度", "%", 40, 100, "低于此值只保留诊断，不触发联系。"),
        numberRow("wake_contact_max_daily", "每日确认上限", "次", 0, 3, "按免打扰时区的本地日期计数。"),
        toggle("wake_require_device_evidence", "要求设备侧证据", "开启后只接受授权设备提供的清醒迹象。"),
      ], "is-wide")}
    </div>`;
  }
  function fmtDuration(seconds) {
    if (!Number.isFinite(seconds)) return "—";
    if (seconds < 60) return seconds + " 秒";
    if (seconds < 3600) return (seconds / 60).toFixed(seconds % 60 ? 1 : 0) + " 分钟";
    return (seconds / 3600).toFixed(seconds % 3600 ? 1 : 0) + " 小时";
  }
  /* extra 只用来挂「这一块管哪一圈」的语义类（is-ring-*）与跨栏（is-wide），
     不改变块的内部结构——块里放什么仍由调用方逐个写出，看得见字段名。 */
  function fieldBlock(title, body, extra) {
    return `<section class="ct-block${extra ? " " + extra : ""}"><h3>${e(title)}</h3>${body.join("")}</section>`;
  }
  function toggle(name, label, help) {
    const on = value(name) === true;
    return `<label class="ct-switch${dirty(name) ? " is-dirty" : ""}${flipClass(name)}">
      <input type="checkbox" data-field="${e(name)}"${on ? " checked" : ""}>
      <span class="ct-switch-track"><i></i></span>
      <span class="ct-switch-text"><strong>${e(label)}</strong><small>${e(help)}</small></span></label>`;
  }
  function textRow(name, label, placeholder, help) {
    const current = value(name);
    return `<label class="ct-field${dirty(name) ? " is-dirty" : ""}">
      <span class="ct-field-label">${e(label)}</span>
      <input type="text" data-field="${e(name)}" value="${e(typeof current === "string" ? current : "")}"
        placeholder="${e(placeholder)}" autocomplete="off" spellcheck="false">
      ${help ? `<small class="ct-field-help">${e(help)}</small>` : ""}</label>`;
  }
  function timeRow(nameA, labelA, nameB, labelB, help) {
    const a = value(nameA), b = value(nameB);
    return `<div class="ct-field-pair${dirty(nameA) || dirty(nameB) ? " is-dirty" : ""}">
      <label class="ct-field"><span class="ct-field-label">${e(labelA)}</span>
        <input type="time" data-field="${e(nameA)}" value="${e(typeof a === "string" ? a : "")}"></label>
      <label class="ct-field"><span class="ct-field-label">${e(labelB)}</span>
        <input type="time" data-field="${e(nameB)}" value="${e(typeof b === "string" ? b : "")}"></label>
      ${help ? `<small class="ct-field-help">${e(help)}</small>` : ""}</div>`;
  }
  function numberRow(name, label, unit, min, max, help) {
    const current = value(name);
    return `<label class="ct-field ct-field-number${dirty(name) ? " is-dirty" : ""}">
      <span class="ct-field-label">${e(label)}<small>${e(min)}–${e(max)}</small></span>
      <span class="ct-input-unit"><input type="number" data-field="${e(name)}" value="${e(current ?? "")}"
        min="${min}" max="${max}" step="1" inputmode="numeric"><i>${e(unit)}</i></span>
      ${help ? `<small class="ct-field-help">${e(help)}</small>` : ""}</label>`;
  }
  function decimalRow(name, label, min, max, step, help) {
    const current = value(name);
    return `<label class="ct-field ct-field-number${dirty(name) ? " is-dirty" : ""}">
      <span class="ct-field-label">${e(label)}<small>${e(min)}–${e(max)}</small></span>
      <input type="number" data-field="${e(name)}" value="${e(current ?? "")}"
        min="${min}" max="${max}" step="${step}" inputmode="decimal">
      ${help ? `<small class="ct-field-help">${e(help)}</small>` : ""}</label>`;
  }
  /* 强度档位是一个**控件**，不是下拉：五档的差别是「更快 / 更多」，而这句话
     只有并排画出来才成立。选中的档位给一条随档位变长的底条。 */
  function tierPicker(tier) {
    const interval = Number(value("interval_seconds"));
    const idle = Number(value("idle_seconds"));
    return `<div class="ct-tiers" role="radiogroup" aria-label="强度档位">
      ${TIERS.map(t => {
        const on = t.key === tier.key;
        const effectiveInterval = Number.isFinite(interval) && interval > 0
          ? fmtDuration(Math.max(1, Math.round(interval * t.interval))) : "—";
        const effectiveIdle = Number.isFinite(idle)
          ? fmtDuration(Math.max(0, Math.round(idle * t.idle))) : "—";
        return `<button type="button" role="radio" aria-checked="${on}" data-tier="${t.key}"
          class="ct-tier${on ? " is-on" : ""}${dirty("proactive_intensity_preset") ? " is-dirty" : ""}"
          title="间隔 ×${t.interval}　等候 ×${t.idle}">
          <span class="ct-tier-bar" style="--fill:${(1 - t.interval) * 100}%"></span>
          <span class="ct-tier-label">${e(t.label)}</span>
          <span class="ct-tier-meta">间隔 ×${t.interval}</span>
          <span class="ct-tier-effective">约 ${e(effectiveInterval)} · 等候 ${e(effectiveIdle)}</span>
        </button>`;
      }).join("")}
    </div>`;
  }
  /* 24 小时曲线编辑器。画法和环一样，但这里是**可直接涂的**：
     按住横向拖过若干小时就改成那个高度。这比让用户改 24 个数字现实。
     下面再给一个文本口——拖涂只有指针设备能用，键盘用户否则完全改不了这一项，
     而它是这一页唯一一个「一天 24 格」的原始数据。 */
  function curveEditor() {
    const weights = curveOf(value("proactive_hour_activity_curve"));
    const on = !!value("proactive_hour_activity_enabled") || dirty("proactive_hour_activity_curve");
    const rows = weights.map((w, hour) => {
      const percent = Math.round((w / 2) * 100);
      return `<span class="ct-curve-col" data-hour="${hour}" title="${String(hour).padStart(2,"0")}:00 · 权重 ${w.toFixed(2)}">
        <i style="height:${percent}%"></i></span>`;
    }).join("");
    return `<div class="ct-curve${on ? " is-on" : ""}" data-role="curve">
      <div class="ct-curve-plot" data-role="curvePlot" role="group"
        aria-label="24 小时活动权重，0 时到 23 时">${rows}</div>
      <div class="ct-curve-axis">${[0,6,12,18].map(h => `<span style="--at:${(h / 24) * 100}%">${h}:00</span>`).join("")}</div>
      <div class="ct-curve-actions">
        <button type="button" class="ct-btn is-quiet" data-action="curve-default">${ICON.undo}<span>恢复默认曲线</span></button>
        <button type="button" class="ct-btn is-quiet" data-action="curve-flat">拉平</button>
      </div>
      <label class="ct-field ct-curve-text${dirty("proactive_hour_activity_curve") ? " is-dirty" : ""}">
        <span class="ct-field-label">按小时填写<small>0 时 → 23 时，逗号分隔</small></span>
        <textarea data-field="proactive_hour_activity_curve" data-role="curveText" rows="2"
          spellcheck="false" placeholder="${e(DEFAULT_CURVE)}">${e(curveText(weights))}</textarea>
      </label></div>`;
  }

  /* 契机矩阵视图：15 类契机 × 三列（开关 / 权重 / 暂缓）。
     权重不是数字输入而是**条形**——权重是相对值，绝对值没有意义，
     一列里并排画出来才知道谁是「主要的开口理由」。 */
  function purposesView() {
    const rows = PURPOSE_BUCKETS.map(bucket => {
      const items = bucket.purposes.map(([name, label]) => purposeRow(name, label));
      return `<section class="ct-bucket${bucket.id === "share" ? " is-wide" : ""}" data-purpose-bucket="${bucket.id}"><header>
          <h3>${e(bucket.label)}</h3><span>${e(bucket.hint)}</span>
          <em data-purpose-bucket-count="${bucket.id}">${bucket.purposes.length}/${bucket.purposes.length} 类</em></header>
        <div class="ct-purpose-list">${items.join("")}</div></section>`;
    }).join("");
    const enabled = ALL_PURPOSES.filter(([name]) => value(name + ".enabled") !== false).length;
    const allowed = ALL_PURPOSES.filter(([name]) => purposeStatus(name) === "available").length;
    const paused = ALL_PURPOSES.filter(([name]) => purposeStatus(name) === "paused").length;
    return `<div class="ct-purposes">
      <div class="ct-purposes-summary" aria-label="契机概况">
        <div class="ct-purpose-stat"><strong>${ALL_PURPOSES.length}</strong><span>类契机</span></div>
        <div class="ct-purpose-stat"><strong>${enabled}</strong><span>单项已开启</span></div>
        <div class="ct-purpose-stat"><strong>${allowed}</strong><span>全局默认开启</span></div>
        <div class="ct-purpose-stat"><strong>${paused}</strong><span>暂缓中</span></div>
      </div>
      <div class="ct-purpose-tools">
        <label class="ct-purpose-search"><span>查找</span>
          <input type="search" data-purpose-search aria-label="搜索契机名称或类别"
            value="${e(data.purposeQuery)}" placeholder="名称或类别">
        </label>
        <label class="ct-purpose-filter"><span>类别</span>
          <select data-purpose-bucket-filter aria-label="按契机类别筛选">
            <option value="all"${data.purposeBucket === "all" ? " selected" : ""}>全部类别</option>
            ${PURPOSE_BUCKETS.map(bucket => `<option value="${bucket.id}"${data.purposeBucket === bucket.id ? " selected" : ""}>${e(bucket.label)}</option>`).join("")}
          </select>
        </label>
        <label class="ct-purpose-filter"><span>全局状态</span>
          <select data-purpose-filter aria-label="按全局默认状态筛选">
            ${[["all","全部状态"],["enabled","单项已开启"],["disabled","单项已关闭"],
              ["available","全局默认开启"],["paused","全局暂缓中"],["unavailable","总开关关闭"]]
              .map(([key, label]) => `<option value="${key}"${data.purposeFilter === key ? " selected" : ""}>${label}</option>`).join("")}
          </select>
        </label>
        <span class="ct-purpose-results" data-purpose-result-count>${ALL_PURPOSES.length} / ${ALL_PURPOSES.length} 类</span>
      </div>
      <div class="ct-purpose-groups">${rows}</div>
      <p class="ct-purpose-empty" hidden>没有匹配的契机</p>
    </div>`;
  }
  function testInstructionText(item) {
    const synthetic = testUsesSynthetic(item.id);
    return [
      `这是一条「${item.label}」主动消息类型的${synthetic ? "合成测试" : "真实来源功能测试"}。`,
      synthetic ? "使用管理员已知的一次性合成情境，只在本轮演练中将给定场景作为表达前提；不扩写经历、承诺或声称取得真实采集来源。"
        : "即时获取当前配置的真实来源；屏幕须实际截图并分析。只使用本次取得的来源，失败时报告具体阶段。",
      `生产器来源：${item.source}`,
      `生产器验收场景：${item.setup}`,
      synthetic ? "按角色身处该情境时会主动发出的消息生成正文；除边界或安全要求不适合外，选择 send。"
        : "根据本次真实来源生成自然、符合角色的消息；内容受实际来源支持，报告 QQ 真实投递回执。",
      `类型要求：${item.instruction}`,
      ...(item.id === "reaction_image" ? ["本次确认的是一次性带图测试，按生产图库检索与 QQ 图片投递链验证；不改变日常图片概率或频率配置。"] : []),
      "测试状态与合成标识只在管理端呈现；QQ 正文按正式效果表达，不加入测试、模拟、演练或链路说明。",
      "保持当前角色口吻和称呼，由角色先带来内容；可以自然提问或发出具体邀请，不催促回复。严格遵守通用 JSON 合同，并给出 decision_confidence。",
      `通过条件：${item.expect}`,
    ].join("\n");
  }
  const SYNTHETIC_SOURCE_TESTS = new Set(["news", "environment", "screen"]);
  const NON_SYNTHETIC_TESTS = new Set(["mobile_location", "reaction_image"]);
  const TEST_ENVIRONMENT_SOURCES = {weather:"当前真实天气", weather_change:"真实天气变化",
    official_alert:"当前官方预警", service_balance:"真实服务余额", external_news:"当前真实资讯"};
  function testUsesSynthetic(id) {
    return !NON_SYNTHETIC_TESTS.has(id)
      && (!SYNTHETIC_SOURCE_TESTS.has(id) || data.testModes?.[id] === "synthetic");
  }
  function testModeMarkup(id) {
    if (!SYNTHETIC_SOURCE_TESTS.has(id)) return "";
    const synthetic = testUsesSynthetic(id);
    return `<label class="ct-test-material"><span>测试素材</span><select data-test-mode="${e(id)}" aria-label="测试素材"
      ${data.testBusy || data.testUncertain ? " disabled" : ""}>
      <option value="real"${!synthetic ? " selected" : ""}>真实来源 · 完整功能测试</option>
      <option value="synthetic"${synthetic ? " selected" : ""}>合成事件 · 生成与投递演练</option>
    </select></label>${id === "environment" ? `<label class="ct-test-material"><span>环境来源</span>
      <select data-test-variant="environment" aria-label="环境来源"${data.testBusy || data.testUncertain ? " disabled" : ""}>
      ${Object.entries(TEST_ENVIRONMENT_SOURCES).map(([key, label]) => `<option value="${key}"${(data.testVariants?.environment || "weather") === key ? " selected" : ""}>${e(label)}</option>`).join("")}
      </select></label>` : ""}`;
  }
  function testsView() {
    const counts = Object.fromEntries(["ready", "partial", "missing"].map(coverage => [
      coverage, TEST_INSTRUCTIONS.filter(item => item.coverage === coverage).length,
    ]));
    const cards = TEST_INSTRUCTIONS.map(item => `<article class="ct-test-card"
      data-test-card data-test-id="${e(item.id)}" data-test-bucket="${TEST_BUCKET_IDS[item.bucket]}"
      data-test-coverage="${e(item.coverage)}">
      <header class="ct-test-card-head">
        <div class="ct-test-title"><code>${e(item.id)}</code><h3>${e(item.label)}</h3></div>
        <span class="ct-test-coverage is-${e(item.coverage)}">${e(TEST_COVERAGE[item.coverage])}</span>
      </header>
      <dl class="ct-test-details">
        <div><dt>生产器来源</dt><dd>${e(item.source)}</dd></div>
        <div><dt>生产器验收场景</dt><dd>${e(item.setup)}</dd></div>
      </dl>
      <section class="ct-test-prompt"><h4>类型指令</h4><p>${e(item.instruction)}</p></section>
      <p class="ct-test-expect"><strong>通过条件</strong>${e(item.expect)}</p>
      ${testModeMarkup(item.id)}
      ${testResultMarkup(item.id)}
      ${item.id === "screen" ? '<div data-screen-observation></div>' : ""}
      <footer class="ct-test-actions">
        <button type="button" class="ct-btn is-primary ct-test-run" data-test-run="${e(item.id)}"
          ${data.testBusy || data.testUncertain || item.coverage !== "ready" ? " disabled" : ""}>
          ${data.testBusy === item.id ? '<span class="ct-spinner" aria-hidden="true"></span><span>测试中…</span>'
            : data.testUncertain ? `${ICON.alert}<span>先核对投递结果</span>`
            : `${ICON.test}<span>运行测试</span>`}
        </button>
        <button type="button" class="ct-btn ct-test-copy" data-test-copy="${e(item.id)}">${ICON.copy}<span>复制提示词</span></button>
      </footer>
    </article>`).join("");
    return `<div class="ct-tests">
      <div class="ct-test-overview">
        <p>主动联系目的与拓展发送链路分别验收，来源、素材和投递回执各自可核对。</p>
        <div class="ct-test-coverage-summary" aria-label="生产器覆盖情况">
          <span><i class="is-ready"></i>生产器已接通 <strong>${counts.ready}</strong></span>
          <span><i class="is-partial"></i>只有资格检查 <strong>${counts.partial}</strong></span>
          <span><i class="is-missing"></i>尚无独立生产器 <strong>${counts.missing}</strong></span>
        </div>
        <p class="ct-test-boundary">资讯、天气、屏幕和手机地点读取真实来源；屏幕会实际截图并调用视觉模型，手机地点读取终端授权快照。带图测试使用授权图库素材。测试调用角色模型并真实投递到核心管理员私聊。</p>
      </div>
      ${data.testUncertain ? `<div class="ct-test-lock" role="alert">
        <span>${data.testGuard?.state === "pending" ? "上次测试没有收到最终回执。" : "上次测试结果未确认。"}请先核对核心管理员私聊；确认前不能再次启动测试，以免重复投递。</span>
        <button type="button" class="ct-btn" data-test-action="unlock">核实后解除测试锁</button>
      </div>` : ""}
      ${data.testBusy ? `<p class="ct-test-running" role="status"><span class="ct-spinner" aria-hidden="true"></span>正在运行 ${e(PURPOSE_META[data.testBusy]?.label || "主动消息")} 测试；可能需要等待模型与平台回执。</p>` : ""}
      <div class="ct-test-tools">
        <label class="ct-test-search"><span>查找类型</span>
          <input type="search" data-test-search aria-label="搜索主动消息类型和测试指令"
            value="${e(data.testQuery)}" placeholder="名称、素材、指令或验收条件">
        </label>
        <label><span>消息类别</span><select data-test-bucket-filter aria-label="按消息类别筛选">
          <option value="all"${data.testBucket === "all" ? " selected" : ""}>全部类别</option>
          <option value="bond"${data.testBucket === "bond" ? " selected" : ""}>关系</option>
          <option value="daily"${data.testBucket === "daily" ? " selected" : ""}>日程</option>
          <option value="share"${data.testBucket === "share" ? " selected" : ""}>分享</option>
          <option value="extension"${data.testBucket === "extension" ? " selected" : ""}>拓展</option>
        </select></label>
        <label><span>生产器状态</span><select data-test-coverage-filter aria-label="按生产器状态筛选">
          <option value="all"${data.testCoverage === "all" ? " selected" : ""}>全部状态</option>
          <option value="ready"${data.testCoverage === "ready" ? " selected" : ""}>生产器已接通</option>
          <option value="partial"${data.testCoverage === "partial" ? " selected" : ""}>只有资格检查</option>
          <option value="missing"${data.testCoverage === "missing" ? " selected" : ""}>尚无独立生产器</option>
        </select></label>
        <span class="ct-test-result-count" data-test-result-count>${TEST_INSTRUCTIONS.length} / ${TEST_INSTRUCTIONS.length} 类</span>
      </div>
      <div class="ct-tests-grid">${cards}</div>
      <p class="ct-test-empty" hidden>没有匹配的消息类型</p>
      ${testConfirmationMarkup()}
    </div>`;
  }
  const TEST_STAGE_LABELS = {
    request:"诊断请求", authorization:"管理员身份", binding:"管理员私聊绑定", runtime:"运行环境",
    settings:"主动联系设置", purpose:"联系目的", trusted_candidate:"联系机会/来源", contact_admission:"主动联系准入",
    news_fetch:"真实资讯抓取", weather_fetch:"真实天气获取", screen_capture:"本次真实截图", screen_interpretation:"本次视觉分析",
    mobile_location_fetch:"手机地点读取", image_attachment:"主动图片素材与投递",
    attempt_budget:"主动模型调用次数上限",
    delivery_budget:"本窗口今日实际联系额度",
    model_route:"主动联系模型路由", model_generation:"模型生成", persona_review:"人格复核",
    delivery:"平台投递", receipt:"投递回执", execution:"端到端执行",
  };
  const TEST_STAGE_STATES = {
    passed:"通过", failed:"失败", pending:"待检查", skipped:"未执行", not_required:"无需复核",
    not_started:"未开始", checking:"检查中", running:"运行中", unknown:"结果未知",
  };
  const TEST_SKIP_REASONS = {
    duplicate:"与近期已发送内容重复", low_confidence:"模型把握不足，按沉默阈值暂缓发送",
    continuation_after_silence:"刚回复过且用户未再发言，这次主动只是在接自己的话，已跳过",
    persona_review:"人格复核未放行", framework_guard:"正文含框架标记，发送前检查拦截",
    presentation_empty:"文字清理后正文为空", forbidden_word:"命中当前禁用词规则",
    source_coverage:"来源材料不足以支持当前表述", no_sendable_content:"处理后没有可发送正文",
    model_decision:"模型判断当前不适合发送", unknown:"系统没有记录具体跳过原因",
  };
  const TEST_MODEL_SKIP_REASONS = {
    insufficient_context:"可用材料不足", no_natural_opening:"当前没有自然的开口点",
    purpose_mismatch:"材料与测试目的不匹配", repetitive:"内容可能重复",
    respect_user_boundary:"为尊重对方边界而保持沉默", other:"模型选择暂不发送",
  };
  const TEST_DELIVERY_STATES = {
    delivered:"平台已确认投递", failed:"平台明确返回失败", unknown:"平台投递结果未知",
    accepted:"平台已接收请求，但尚无最终投递回执", pending:"等待平台投递回执",
    dispatched:"已提交平台，等待投递回执",
  };
  const TEST_MEDIA_STATES = {
    reaction: {attached:"已附表情图", pending_review:"待人格复核后附表情图", not_selected:"未选表情图"},
    voice: {sealed:"已封装语音", not_selected:"未选语音"},
    poke: {sealed:"已封装戳一戳", not_selected:"未选戳一戳"},
  };
  function testStageMarkup(result) {
    const stages = Array.isArray(result.stages) ? result.stages.filter(item =>
      item && typeof item.stage === "string" && typeof item.state === "string") : [];
    const rows = stages.length ? stages : result.stage ? [{stage:result.stage,
      state:result.state === "unknown" ? "unknown" : result.state === "pending" ? "pending" : "failed"}] : [];
    if (!rows.length) return "";
    return `<section class="ct-test-diagnosis" aria-label="诊断步骤"><h4>诊断步骤</h4>
      <ol>${rows.map(item => `<li data-state="${e(item.state)}"><span>${e(TEST_STAGE_LABELS[item.stage] || item.stage)}</span>
        <strong>${e(TEST_STAGE_STATES[item.state] || item.state)}</strong>${item.detail ? `<small>${e(item.detail)}</small>` : ""}</li>`).join("")}</ol>
    </section>`;
  }
  function testMetricsMarkup(result) {
    const metrics = result.stage_metrics;
    const model = metrics?.model;
    const delivery = metrics?.delivery;
    const fmtCost = (value) => value == null ? "费用未返回" : `成本 $${(Number(value) / 1000000).toFixed(6)}`;
    const stageLines = [];
    if (model && Number.isFinite(Number(model.provider_calls))) {
      const latency = Number.isFinite(Number(model.avg_latency_ms)) ? ` · 平均 ${Number(model.avg_latency_ms)} ms` : "";
      const unknown = Number(model.unknown_calls) > 0 ? ` · 未知 ${Number(model.unknown_calls)} 次` : "";
      stageLines.push(`本轮 Provider 模型：${Number(model.provider_calls)} 次调用 · ${Number(model.tokens || 0)} Tokens · ${fmtCost(model.cost_microusd)}${latency}${unknown}`);
    }
    if (delivery && Number.isFinite(Number(delivery.expected_parts))) {
      stageLines.push(`本轮投递：${Number(delivery.delivered_parts || 0)} / ${Number(delivery.expected_parts)} 段已确认 · ${Number(delivery.unresolved_parts || 0)} 段待确认 · ${Number(delivery.failed_parts || 0)} 段失败`);
    }
    const media = metrics?.media;
    if (media && typeof media === "object") {
      const mediaText = Object.entries(TEST_MEDIA_STATES).map(([key, labels]) =>
        labels[media[key]] ? `${labels[media[key]]}` : "").filter(Boolean);
      if (mediaText.length) stageLines.push(`本轮表达：${mediaText.join(" · ")}`);
    }
    const summary = result.usage_summary;
    const sessionDecision = summary?.decisions?.session;
    const sessionDelivery = summary?.deliveries?.session;
    const sessionModel = summary?.model?.session;
    const quotaLine = (label, decision, contact, usage) => {
      if (!decision || !contact || !usage) return "";
      const cost = usage.cost_microusd == null ? "费用未返回" : `$${(Number(usage.cost_microusd) / 1000000).toFixed(6)}`;
      return `${label}：主动模型决策已调用 ${Number(decision.calls ?? decision.used ?? 0)} 次（待处理 ${Number(decision.pending || 0)}，仅统计）；实际联系已确认发送 ${Number(contact.delivered)} 条（每日上限占用 ${Number(contact.occupied ?? contact.used)}/${Number(contact.limit)}，待确认 ${Number(contact.unresolved)}）；主动生成/复核 Provider 调用 ${Number(usage.calls)} 次、${Number(usage.tokens)} Tokens、${cost}`;
    };
    const quotaDay = sessionDecision?.date || sessionDelivery?.date || sessionModel?.date || summary?.date;
    const quotaTimezone = sessionDecision?.timezone || sessionDelivery?.timezone || sessionModel?.timezone || summary?.timezone;
    const quotaLabel = quotaDay ? `当前窗口 ${quotaDay}${quotaTimezone ? `（${quotaTimezone}）` : ""} 每日额度` : "当前窗口每日额度";
    const usageLines = [quotaLine(quotaLabel, sessionDecision, sessionDelivery, sessionModel)].filter(Boolean);
    if (!stageLines.length && !usageLines.length) return "";
    return `<section class="ct-test-diagnosis" aria-label="运行统计"><h4>运行统计</h4>
      ${stageLines.concat(usageLines).map(line => `<p>${e(line)}</p>`).join("")}
    </section>`;
  }
  function testResultMarkup(id) {
    const result = data.testResults[id];
    if (!result) return "";
    const state = ["passed", "skipped", "failed", "unknown", "pending"].includes(result.state)
      ? result.state : "unknown";
    const title = state === "passed" ? "测试通过 · 已投递"
      : state === "skipped" ? "测试完成 · 本次未发送"
      : state === "failed" ? "测试未通过"
      : state === "pending" ? "测试仍在处理中 · 请勿重试" : "结果未知 · 请勿重试";
    const stage = result.stage ? TEST_STAGE_LABELS[result.stage] || result.stage : "";
    const skipReason = TEST_SKIP_REASONS[result.skip_reason] || result.skip_reason || "";
    const modelSkipReason = TEST_MODEL_SKIP_REASONS[result.model_skip_reason] || result.model_skip_reason || "";
    const technical = result.code || result.error ? `<details class="ct-test-technical"><summary>展开技术详情</summary>
      ${result.code ? `<code>${e(result.code)}</code>` : ""}${result.error ? `<pre>${e(result.error)}</pre>` : ""}</details>` : "";
    const delivery = TEST_DELIVERY_STATES[result.delivery_state] || "";
    return `<section class="ct-test-result is-${state}" role="status" aria-live="polite">
      <strong>${e(title)}${state === "failed" && stage ? ` · 卡在${e(stage)}` : ""}</strong>
      ${result.failure_title ? `<p class="ct-test-cause"><b>失败原因</b>${e(result.failure_title)}</p>` : ""}
      ${result.explanation ? `<p class="ct-test-explanation">${e(result.explanation)}</p>` : ""}
      ${result.route?.label || result.variant?.label ? `<p>来源：${e(result.route?.label || result.variant.label)}</p>` : ""}
      ${result.purpose_label ? `<p>测试用途：${e(result.purpose_label)} · 收件人：核心管理员</p>` : ""}
      ${result.event_mode === "synthetic_event" ? `<p>输入：一次性合成事件（未写入普通聊天记录）</p>` : ""}
      ${result.event_mode === "source_backed" ? `<p>输入：本次取得的真实来源</p>` : ""}
      ${delivery ? `<p>投递状态：${e(delivery)}</p>` : ""}
      ${skipReason ? `<p class="ct-test-cause"><b>未发送原因</b>${e(skipReason)}${modelSkipReason ? `：${e(modelSkipReason)}` : ""}</p>` : ""}
      ${testMetricsMarkup(result)}
      ${testStageMarkup(result)}
      ${result.suggestion ? `<p class="ct-test-next"><b>下一步</b>${e(result.suggestion)}</p>` : ""}
      ${technical}
    </section>`;
  }
  function testConfirmationMarkup() {
    if (!data.confirmTest) return "";
    if (data.confirmTest === TEST_GUARD_RESET) return `<dialog class="ct-test-dialog" data-test-confirm-dialog aria-labelledby="ct-test-confirm-title">
      <div class="ct-test-dialog-body">
        <p class="ct-test-dialog-kicker">上一轮结果未确认</p>
        <h2 id="ct-test-confirm-title">解除主动测试锁？</h2>
        <p>请先核对核心管理员私聊，并确认上一轮测试已经结束。解除后可以重新发起测试，仍可能产生另一条真实消息。</p>
        <div class="ct-test-dialog-actions">
          <button type="button" class="ct-btn" data-test-action="cancel">继续等待</button>
          <button type="button" class="ct-btn is-primary" data-test-action="confirm-unlock">我已核实，解除锁定</button>
        </div>
      </div>
    </dialog>`;
    const item = TEST_INSTRUCTIONS.find(candidate => candidate.id === data.confirmTest);
    if (!item) return "";
    const synthetic = testUsesSynthetic(item.id);
    const realAction = item.id === "mobile_location" ? "读取手机陪伴终端当前经授权且未过期的真实地点快照；不伪造坐标，也不改变生产去重标记"
      : item.id === "reaction_image" ? "从当前私聊授权图库检索一张表情原图，执行素材校验并真实投递图片；不修改生产图片频率"
      : item.id === "screen" ? "调用当前已授权的窥屏设备取得一张新截图，并调用视觉模型分析该截图"
      : item.id === "news" || data.testVariants?.environment === "external_news" ? "即时抓取当前配置的真实资讯源，使用带日期和原链接的有效条目"
      : item.id === "environment" && (data.testVariants?.environment || "weather") === "weather" ? "即时获取已配置角色所在地的真实天气观测"
      : "读取所选环境来源的当前真实事件";
    return `<dialog class="ct-test-dialog" data-test-confirm-dialog aria-labelledby="ct-test-confirm-title">
      <div class="ct-test-dialog-body">
        <p class="ct-test-dialog-kicker">${synthetic ? "合成事件" : "真实来源"} · 真实投递</p>
        <h2 id="ct-test-confirm-title">运行「${e(item.label)}」测试？</h2>
        <p>${synthetic ? `将为「${e(item.label)}」构造一次性合成事件；合成事件不会写入聊天记录。` : `将${realAction}。来源获取失败会显示失败阶段。`}之后调用角色配置的消息模型，按配置执行人格复核，并真实投递到核心管理员 QQ 私聊。QQ 正文按正式效果呈现，测试标识只保留在本页。</p>
        <p class="ct-test-dialog-warning">测试不占日常联系额度。带图测试可能记录素材实际已投递；若结果未知，请先核对管理员私聊，不要重新运行。</p>
        <div class="ct-test-dialog-actions">
          <button type="button" class="ct-btn" data-test-action="cancel">取消</button>
          <button type="button" class="ct-btn is-primary" data-test-action="confirm">确认并运行</button>
        </div>
      </div>
    </dialog>`;
  }
  function testMatches(id) {
    const item = TEST_INSTRUCTIONS.find(candidate => candidate.id === id);
    if (!item) return false;
    if (data.testBucket !== "all" && TEST_BUCKET_IDS[item.bucket] !== data.testBucket) return false;
    if (data.testCoverage !== "all" && item.coverage !== data.testCoverage) return false;
    const query = data.testQuery.trim().toLocaleLowerCase();
    if (!query) return true;
    return [item.id, item.label, item.bucket, item.source, item.setup, item.instruction,
      item.expect, TEST_COVERAGE[item.coverage]].join(" ").toLocaleLowerCase().includes(query);
  }
  function updateTestFilters() {
    if (!root?.querySelectorAll) return;
    const cards = Array.from(root.querySelectorAll("[data-test-card]"));
    if (!cards.length) return;
    let visible = 0;
    for (const card of cards) {
      const show = testMatches(card.dataset.testId);
      card.hidden = !show;
      if (show) visible++;
    }
    const result = root.querySelector("[data-test-result-count]");
    if (result) result.textContent = visible + " / " + TEST_INSTRUCTIONS.length + " 类";
    const empty = root.querySelector(".ct-test-empty");
    if (empty) empty.hidden = visible > 0;
  }
  function purposeStatus(name) {
    if (value(name + ".enabled") === false) return "disabled";
    if (value("enabled") === false) return "unavailable";
    if ([value(name + ".pause_until"), value("pause_until")]
      .some(until => typeof until === "string" && Date.parse(until) > Date.now())) return "paused";
    return "available";
  }
  function purposeRow(name, label) {
    const enabled = value(name + ".enabled") !== false;
    const weight = Number(value(name + ".weight"));
    const status = purposeStatus(name);
    const pauseUntil = [value(name + ".pause_until"), value("pause_until")]
      .filter(until => typeof until === "string" && Date.parse(until) > Date.now())
      .sort((a, b) => Date.parse(b) - Date.parse(a))[0];
    const scale = Math.max(0, Math.min(5, Number.isFinite(weight) ? weight : 1));
    return `<article class="ct-purpose${enabled ? " is-on" : ""}"
      data-purpose="${e(name)}">
      <label class="ct-check">
        <input type="checkbox" data-field="${e(name)}.enabled"${enabled ? " checked" : ""}>
        <span class="ct-check-box">${ICON.check}</span>
        <span class="ct-purpose-name">${e(label)}</span></label>
      <div class="ct-weight" title="相对权重 ${Number.isFinite(weight) ? weight.toFixed(2) : "—"}">
        <input type="range" min="0.1" max="5" step="0.1" data-field="${e(name)}.weight"
          value="${Number.isFinite(weight) ? weight : 1}" aria-label="${e(label)} 相对权重">
        <span class="ct-weight-fill" style="--w:${(scale / 5) * 100}%"></span>
        <em>${Number.isFinite(weight) ? weight.toFixed(1) : "—"}</em>
      </div>
      <div class="ct-purpose-state">
        ${status === "disabled" ? `<span class="ct-chip">已关闭</span>`
          : status === "paused" ? `<span class="ct-chip is-paused">${pauseUntil ? "暂缓至 " + e(pauseUntil.slice(0, 16).replace("T", " ")) : "暂缓中"}</span>`
          : status === "unavailable" ? `<span class="ct-chip is-risk">全局总开关关闭</span>`
          : status === "available" ? `<span class="ct-chip is-ok">全局默认开启</span>`
          : `<span class="ct-chip">待配置</span>`}
      </div>
    </article>`;
  }
  function purposeMatches(name) {
    const meta = PURPOSE_META[name];
    const query = data.purposeQuery.trim().toLocaleLowerCase();
    if (data.purposeBucket !== "all" && meta.bucket.id !== data.purposeBucket) return false;
    if (query) {
      const searchable = [name, meta.label, meta.bucket.label].join(" ").toLocaleLowerCase();
      if (!searchable.includes(query)) return false;
    }
    switch (data.purposeFilter) {
      case "enabled": return value(name + ".enabled") !== false;
      case "disabled": return value(name + ".enabled") === false;
      case "available": return purposeStatus(name) === "available";
      case "paused": return purposeStatus(name) === "paused";
      case "unavailable": return purposeStatus(name) === "unavailable";
      default: return true;
    }
  }
  function updatePurposeFilters() {
    if (!root?.querySelectorAll) return;
    const rows = Array.from(root.querySelectorAll(".ct-purpose"));
    if (!rows.length) return;
    const visibleByBucket = Object.fromEntries(PURPOSE_BUCKETS.map(bucket => [bucket.id, 0]));
    let visible = 0;
    for (const row of rows) {
      const show = purposeMatches(row.dataset.purpose);
      row.hidden = !show;
      if (show) { visible++; visibleByBucket[PURPOSE_META[row.dataset.purpose].bucket.id]++; }
    }
    for (const bucket of PURPOSE_BUCKETS) {
      const section = root.querySelector('[data-purpose-bucket="' + bucket.id + '"]');
      if (section) section.hidden = visibleByBucket[bucket.id] === 0;
      const count = root.querySelector('[data-purpose-bucket-count="' + bucket.id + '"]');
      if (count) count.textContent = visibleByBucket[bucket.id] + "/" + bucket.purposes.length + " 类";
    }
    const result = root.querySelector("[data-purpose-result-count]");
    if (result) result.textContent = visible + " / " + ALL_PURPOSES.length + " 类";
    const empty = root.querySelector(".ct-purpose-empty");
    if (empty) empty.hidden = visible > 0;
  }
  function shortReason(code) {
    if (!code) return "受阻";
    const text = reason(code);
    return text.length > 14 ? text.slice(0, 13) + "…" : text;
  }

  /* These gates describe available global settings, not a live delivery check. */
  function gatesView() {
    const settings = data.settings;
    const minute = currentMinute(settings);
    const pauseUntil = value("pause_until");
    const gates = [
      {id:"enabled", title:"全局主动联系默认", ok:value("enabled") !== false,
       note:"逐用户授权与单独设置在私聊用户页管理。"},
      {id:"automatic", title:"后台自动联系默认", ok:value("automatic_enabled") === true,
       note:"关闭时仍可由你主动触发联系。"},
      {id:"quiet", title:"免打扰时段", ok:!inQuiet(minute, settings), code:"proactive_quiet_hours",
       note:quietSpanText(settings)},
      {id:"curve", title:"小时活动曲线", ok:(() => { const w = currentWeight(minute, settings); return w === null || w >= 0.3; })(),
       code:"", note:value("proactive_hour_activity_enabled") ? "当前小时权重决定这一格是否保留。" : "未启用，不做小时过滤。"},
      {id:"pause", title:"临时暂停", ok:!(Date.parse(pauseUntil) > Date.now()), code:"proactive_paused",
       note:pauseUntil ? "已暂缓至 " + String(pauseUntil).slice(0, 16).replace("T", " ") : "未设暂停。"},
    ];
    const blocked = gates.filter(g => !g.ok).length;
    const paused = Date.parse(pauseUntil) > Date.now();
    return `<div class="ct-gates">
      <p class="ct-gates-sum${paused || value("enabled") === false ? " is-shut" : blocked ? " is-shut" : " is-open"}">${paused ? "全局暂停生效中" : value("enabled") === false ? "全局主动联系默认关闭" : "全局默认配置已读取"}</p>
      ${gates.map((g, i) => `<article class="ct-gate${g.ok ? " is-ok" : " is-shut"}" style="--i:${i}">
        <span class="ct-gate-mark">${g.ok ? ICON.check : ICON.alert}</span>
        <div class="ct-gate-body"><strong>${e(g.title)}</strong>
          ${g.ok ? `<small>${e(g.note)}</small>` : `<small>${e(g.note)}</small>`}</div>
        <span class="ct-gate-state">${g.ok ? "开" : "关"}</span></article>`).join("")}
      <section class="ct-gate-settings-brief" aria-label="回复与补看设置">
        <div class="ct-gate-settings-copy">
          <span class="ct-gate-settings-eyebrow">回复与补看</span>
          <h3>忙碌延迟、休息判定与醒后补看</h3>
          <p>此页编辑全局默认，逐用户设置与授权在私聊用户页管理。</p>
        </div>
        <button type="button" class="ct-btn is-primary ct-gate-settings-button" data-action="open-gate-settings">
          ${ICON.gate}<span>调整开口闸门</span>
        </button>
      </section>
      <section class="ct-block ct-gate-pause"><h3>临时暂停</h3>
        <label class="ct-field${dirty("pause_until") ? " is-dirty" : ""}">
          <span class="ct-field-label">暂缓全部联系至<small>留空表示不暂停</small></span>
          <input type="datetime-local" data-field="pause_until"
            value="${e(datetimeLocal(value("pause_until")))}">
          <small class="ct-field-help">留空取消全局暂停。</small></label>
        <label class="ct-field${dirty("note") ? " is-dirty" : ""}">
          <span class="ct-field-label">补充联系偏好</span>
          <textarea data-field="note" rows="3" placeholder="只是写进提示词的材料，不改变权限、频率或去重">${e(value("note") || "")}</textarea></label>
      </section></div>`;
  }
  /* datetime-local 只吃本地无时区的串；后端要的是带偏移的 UTC。
     这里的转换只为了display正确，提交前统一走 toIso。 */
  function datetimeLocal(value) {
    if (typeof value !== "string" || !value) return "";
    const at = new Date(value);
    if (!Number.isFinite(at.getTime())) return "";
    const pad = n => String(n).padStart(2, "0");
    return at.getFullYear() + "-" + pad(at.getMonth() + 1) + "-" + pad(at.getDate())
      + "T" + pad(at.getHours()) + ":" + pad(at.getMinutes());
  }
  function toIso(local) {
    if (typeof local !== "string" || !local) return "";
    const at = new Date(local);
    return Number.isFinite(at.getTime()) ? at.toISOString() : "";
  }

  /* 额度与去重：额度**没有读出口**，所以画成上限刻度而不是用量表。 */
  function budgetsView() {
    const num = name => { const n = Number(value(name)); return Number.isFinite(n) ? n : null; };
    const quota = [["max_contacts_24h", "本窗口每日最多实际联系", "条", 100]];
    const dedup = [
      ["dedup_enabled", "启用重复抑制"],
      ["dedup_last_message_enabled", "与上一条原文比对"],
      ["dedup_sent_window_minutes", "已发内容窗口（分钟）", 1440],
      ["dedup_last_message_window_minutes", "上一条窗口（分钟）", 1440],
      ["dedup_min_shared_tokens", "最少共有词数", 4],
      ["dedup_min_overlap_ratio", "最低重合比例", 1],
    ];
    return `<div class="ct-budgets">
      <section class="ct-block"><h3>每日额度</h3>
        <p class="ct-note">这几个数只有上限，没有实时读出口——所以画成刻度，不画成进度条。</p>

        ${quota.map(([name, label, unit, max]) => {
          const current = num(name);
          return `<div class="ct-quota${dirty(name) ? " is-dirty" : ""}">
            <div class="ct-quota-head"><strong>${e(label)}</strong>
              <span class="ct-quota-value"><b>${current === null ? "—" : current}</b><i>${e(unit)}</i></span></div>
            <input type="range" min="0" max="${max}" step="1" data-field="${e(name)}"
              value="${current ?? 0}" aria-label="${e(label)}">
            <div class="ct-quota-scale"><span>0</span><span>${max}</span></div>
          </div>`;
        }).join("")}
      </section>
      <section class="ct-block"><h3>重复抑制</h3>
        ${dedup.map(([name, label, max]) => {
          if (typeof max === "undefined") return toggle(name, label, "关闭后同一内容可能在窗口内重复发出。");
          const current = num(name);
          return `<div class="ct-quota${dirty(name) ? " is-dirty" : ""}">
            <div class="ct-quota-head"><strong>${e(label)}</strong>
              <span class="ct-quota-value"><b>${current === null ? "—" : current}</b></span></div>
            <input type="range" min="0" max="${max}" step="${max <= 4 ? 1 : max <= 100 ? 5 : 30}"
              data-field="${e(name)}" value="${current ?? 0}" aria-label="${e(label)}">
            <div class="ct-quota-scale"><span>0</span><span>${max}</span></div></div>`;
        }).join("")}
        ${textRow("dedup_policies", "启用的去重策略", "semantic,content_fingerprint,life_event,event_revision",
          "逗号分隔，不能重复。可选：semantic / event_anchor / event_revision / conversation_anchor / daily_slot / content_fingerprint / life_event。")}
      </section>
      <section class="ct-block is-wide"><h3>连发与收尾</h3>
        ${toggle("enable_proactive_burst", "允许连发", "连发只接着一条普通话题继续；提醒、预警、问候和分享不会拖着连发走。")}
        ${numberRow("proactive_burst_max_messages", "一轮最多几条", "条", 2, 3, "")}
        ${numberRow("proactive_burst_gap_min_seconds", "两条之间最少", "秒", 10, 600, "")}
        ${numberRow("proactive_burst_gap_max_seconds", "两条之间最多", "秒", 10, 900, "实际间隔由这一轮的联系标识决定，同一轮不会重掷。")}
        ${numberRow("proactive_closing_grace_minutes", "收尾静默时长", "分钟", 0, 240, "连续开口到这个数后会主动停一段，等你回应或静默结束。")}
        ${numberRow("proactive_closing_contacts_threshold", "触发收尾的连续条数", "条", 1, 10, "")}
        ${numberRow("proactive_share_probability", "分享类开口概率", "%", 0, 100, "只作用于「分享自己的生活」与「作品与阅读」；提醒、预警和天气各有自己的授权，不受这个数影响。")}
      </section></div>`;
  }

  /* 外部触发源：这些不是「一天」上的东西，是「素材从哪来」。
     所以单独一屏，按获取方式分组，每组一张卡片：图标 + 组名 + 「n/m 开启」，
     组内一行一件事（标签在左、控件在右）。
     分组没动过——分法是按「谁在决定素材」，改了分组名等于重写语义；
     改的只是画法：原来每个字段竖排三行（标签 / 控件 / 说明），
     一屏扫下去全是左对齐的灰标签，读不出哪一行是哪一项。
     wide 的三组条目最多（14 / 9 / 9 条），占满整行、组内分两栏，
     否则它们会把同一行的短卡片拉出一大块空白。 */
  function sourcesView() {
    const known = [
      {id:"weather", icon:ICON.weather, title:"天气", hint:"定时采样；只有突变超过阈值才开口。", body:[
        srcToggle("weather_checkin_enabled", "定时天气问候", "要等定时点。"),
        srcText("weather_checkin_time", "问候时刻", "08:30", "本地时间，二十四小时制。"),
        srcNumber("weather_sample_minutes", "采样间隔", "分钟", 10, 1440, ""),
        srcToggle("weather_change_enabled", "天气突变时联系", "变化幅度超过阈值时才开口。"),
        srcNumber("dedup_weather_window_minutes", "同内容不重复时长", "分钟", 0, 2880, "突变与预警都走 event_revision 去重。"),
      ]},
      {id:"weather-alert", icon:ICON.bell, title:"官方预警", hint:"预警由官方发布，这里只管转不转、多久看一次。", body:[
        srcToggle("official_weather_alert_enabled", "转发官方气象预警", "预警由官方发布，这里只管转不转、多久看一次。"),
        srcNumber("official_alert_poll_minutes", "轮询间隔", "分钟", 1, 1440, ""),
        srcSelect("official_alert_min_severity", "最低预警级别",
          [["","不限制（全部转发）"],["minor","蓝色 · 一般"],["moderate","黄色 · 较重"],
           ["severe","橙色 · 严重"],["extreme","红色 · 特别严重"]]),
      ]},
      {id:"news", icon:ICON.source, title:"资讯", hint:"资讯来源、每日热点与去重时长。", body:[
        srcToggle("news_share_enabled", "分享资讯", "自然分享亮点和感想；追问时再给来源与原文。分段沿用表达设置。"),
        srcNumber("news_check_interval_minutes", "检查间隔", "分钟", 15, 1440, ""),
        srcSelect("news_source_kind", "来源类型", [["host","默认数据源"],["url","自定地址"],["uid","平台标识"]]),
        srcText("news_source_uid", "来源标识", "", "与类型匹配填写。"),
        srcText("news_source_url", "来源地址", "", "与类型匹配填写。"),
        srcToggle("news_daily_hot_read_enabled", "每日热点", ""),
        srcText("news_hot_sources", "热点来源", "weibo,hackernews", "逗号分隔，可选 weibo / hackernews。"),
      ]},
      {id:"meal", icon:ICON.meal, title:"饭点与生日纪念日", hint:"饭点只是可选话题；生日和纪念日自动读取资料。", body:[
        srcToggle("meal_checkin_enabled", "饭点话题", "系统会按当天语境在跳过、单时段或双时段中选择，不保证每餐都联系。"),
        srcText("meal_lunch_time", "午餐大致时刻", "12:00", "实际机会会在参考时刻前后小幅浮动。"),
        srcText("meal_dinner_time", "晚餐大致时刻", "18:00", "实际机会会在参考时刻前后小幅浮动。"),
        srcNumber("meal_care_followup_minutes", "饭点后可接话窗口", "分钟", 1, 240, "超出窗口就不再跟进；语境不合适时可以直接跳过。"),
        srcToggle("date_checkin_enabled", "生日与纪念日问候", ""),
        srcText("date_checkin_time", "问候时刻", "09:00", ""),
        srcNumber("important_date_lookahead_days", "提前几天开始留意", "天", 0, 30, "0 表示只在当天问候；角色生日、对方资料日期和每日反思记下的重要互动纪念日会自动读取，无需再填日期清单；可在聊天中用「陪伴 日期列表」查看或删除。"),
      ]},
      {id:"explore", icon:ICON.compass, title:"网络探索与自关联", hint:"她自己去网上找素材，并把外部事件关联回来。", body:[
        srcToggle("web_exploration_enabled", "网络探索", ""),
        srcText("web_exploration_interests", "关注方向", "", "留空则不做定向探索。"),
        srcNumber("web_exploration_min_interval_hours", "最短探索间隔", "小时", 1, 168, ""),
        srcNumber("web_exploration_result_count", "每次取几条", "条", 1, 30, ""),
        srcToggle("external_event_self_link_enabled", "把外部事件关联到自己", ""),
      ]},
      {id:"screen", icon:ICON.screen, title:"屏幕与动作", hint:"屏幕观察有授权与每日上限；语音与戳一戳各自有冷却。", wide:true, body:[
        srcToggle("screen_glance_enabled", "已授权屏幕观察", "采集本身是设备侧的原生调用，可能被拒绝。"),
        srcNumber("screen_glance_daily_limit", "每日上限", "次", 0, 20, ""),
        srcNumber("screen_glance_cooldown_minutes", "冷却", "分钟", 0, 10080, ""),
        srcToggle("screen_glance_goodnight_enabled", "睡前看一眼", ""),
        srcNumber("screen_glance_goodnight_delay_minutes", "睡前延迟", "分钟", 1, 180, ""),
        srcToggle("screen_glance_followup_enabled", "未回应后跟进", ""),
        srcNumber("screen_glance_followup_after_minutes", "多久后跟进", "分钟", 10, 240, ""),
        srcNumber("screen_glance_followup_cooldown_minutes", "跟进冷却", "分钟", 30, 1440, ""),
        srcToggle("voice_action_enabled", "允许发语音", "沿用回复设置中的语音语言与对应 TTS，正文完整保留，语音另条发送。"),
        srcNumber("voice_action_max_chars", "语音参考长度", "字", 6, 80, "供模型组织完整短句参考，优先保证语意完整，不按字数截断。"),
        srcNumber("voice_action_cooldown_minutes", "语音冷却", "分钟", 0, 1440, ""),
        srcToggle("poke_action_enabled", "允许戳一戳", ""),
        srcNumber("poke_action_daily_limit", "每日上限", "次", 0, 20, ""),
        srcNumber("poke_action_cooldown_minutes", "冷却", "分钟", 0, 1440, ""),
      ]},
      {id:"context", icon:ICON.context, title:"约定与生成上下文", hint:"约定前后的静默，以及开口前那一次自评。", wide:true, body:[
        srcNumber("appointment_lead_minutes", "约定提前量", "分钟", 0, 240, ""),
        srcNumber("appointment_lag_minutes", "约定延后量", "分钟", 0, 180, ""),
        srcNumber("appointment_pre_silence_minutes", "约定前静默", "分钟", 0, 240, "约定前先安静一段，避免连着说两遍。"),
        srcToggle("enable_llm_proactive_persona_judge", "开口前先自评", "多一次模型调用，按阈值决定这条要不要发。"),
        srcNumber("proactive_persona_judge_threshold", "自评阈值", "", 0, 100, ""),
        srcNumber("proactive_persona_judge_cache_minutes", "自评缓存", "分钟", 0, 720, ""),
        srcNumber("generation_history_limit", "近期对话轮数", "轮", 0, 64, "同一轮中的分段消息会合并计数，并受上下文字数预算约束。"),
        srcNumber("history_max_chars", "历史字数上限", "字", 0, 8192, ""),
      ]},
      {id:"balance", icon:ICON.purse, title:"余额与其它", hint:"余额只影响说不说、说不说金额；两个阈值是绝对值与百分比双保险。", wide:true, body:[
        srcToggle("balance_awareness_enabled", "余额感知", ""),
        srcToggle("balance_include_amount_in_message", "在消息里说出金额", "关闭时只提「快用完了」，不报具体数字。"),
        srcText("balance_currency_label", "货币单位", "元", "不超过 20 字符。"),
        srcNumber("balance_check_interval_minutes", "检查间隔", "分钟", 5, 1440, ""),
        srcNumber("balance_low_threshold", "低余额阈值", "", 0, 1000000000, ""),
        srcNumber("balance_critical_threshold", "极低余额阈值", "", 0, 1000000000, ""),
        srcNumber("balance_low_percent_threshold", "低余额百分比", "%", 0, 100, ""),
        srcNumber("balance_critical_percent_threshold", "极低余额百分比", "%", 0, 100, ""),
        srcNumber("balance_message_cooldown_hours", "余额提醒冷却", "小时", 1, 168, ""),
      ]},
    ];
    const groups = known.map(sourceGroup).join("");
    const fields = (groups.match(/data-field="/g) || []).length;
    const toggles = (groups.match(/data-kind="toggle"/g) || []).length;
    const on = (groups.match(/data-on="1"/g) || []).length;
    const entries = [
      ["天气与官方预警", "地点、时效、突变采样和官方预警状态", "config-environment:perception-detail-place", ICON.weather],
      ["日期、饭点与约定", "重要日子、日历上下文和提醒素材", "config-environment:perception-detail-important-days", ICON.meal],
      ["资讯与网络探索", "来源、兴趣和外部检索状态", "config-news:perception-detail-external", ICON.source],
      ["设备观察与动作", "授权屏幕、手机、摄像头和动作回执", "config-screen:perception-detail-device-access", ICON.screen],
      ["余额感知", "服务余额状态与数据源配置", "config-balance:perception-detail-balance", ICON.purse],
    ];
    return `<div class="ct-sources">
      <section class="ct-source-guide"><div><strong>触发条件可在本页直接调整</strong><p>下面每张卡片的开关、时间、来源和阈值会直接写入主动联系设置。感知页提供按职责查看的状态汇总与更细的授权配置，可以从这里跳过去核对，两边不是单一入口。</p></div><div class="ct-source-guide-links">${entries.map(([title, detail, target, icon]) => `<button type="button" class="ct-source-guide-link" data-go="manage" data-manage="perception" data-perception-tab-target="${e(target)}"><span class="ct-src-icon">${icon}</span><span><b>${e(title)}</b><small>${e(detail)}</small></span><span class="ct-source-guide-arrow">查看感知状态 ${ICON.arrow}</span></button>`).join("")}</div></section>
      <div class="ct-source-summary" role="status">${fields} 项触发配置 · ${on}/${toggles} 个开关当前开启</div>
      ${groups}<div data-screen-observation></div></div>`;
  }
  function sourceGroup(group) {
    const body = group.body;
    const html = body.join("");
    const half = group.wide ? Math.ceil(body.length / 2) : body.length;
    const rows = [body.slice(0, half), body.slice(half)].filter(list => list.length)
      .map(list => `<div class="ct-src-col">${list.join("")}</div>`).join("");
    const toggles = (html.match(/data-kind="toggle"/g) || []).length;
    const on = (html.match(/data-on="1"/g) || []).length;
    return `<section class="ct-src-group${group.wide ? " is-wide" : ""}" data-source="${e(group.id)}"
      data-toggles="${toggles}" data-enabled="${on}">
      <header class="ct-src-head"><span class="ct-src-icon">${group.icon}</span><h3>${e(group.title)}</h3>
        <em class="ct-src-badge${on ? " is-on" : ""}">${on}/${toggles} 开启</em></header>
      <p class="ct-src-hint">${e(group.hint)}</p><div class="ct-src-rows">${rows}</div></section>`;
  }
  function srcRow(name, label, help, kind, control) {
    return `<label class="ct-src-row${dirty(name) ? " is-dirty" : ""}" data-kind="${kind}">
      <span class="ct-src-label">${e(label)}${help ? `<small>${e(help)}</small>` : ""}</span>
      <span class="ct-src-control">${control}</span></label>`;
  }
  function srcToggle(name, label, help) {
    const on = value(name) === true;
    return `<label class="ct-src-row is-toggle${on ? " is-on" : ""}${dirty(name) ? " is-dirty" : ""}${flipClass(name)}"
      data-kind="toggle"${on ? ' data-on="1"' : ""}>
      <span class="ct-src-label">${e(label)}${help ? `<small>${e(help)}</small>` : ""}</span>
      <span class="ct-src-control"><span class="ct-switch"><input type="checkbox" data-field="${e(name)}"${on ? " checked" : ""}>
        <span class="ct-switch-track"><i></i></span></span></span></label>`;
  }
  function srcText(name, label, placeholder, help) {
    const current = value(name);
    return srcRow(name, label, help, "text", `<input type="text" data-field="${e(name)}" value="${e(typeof current === "string" ? current : "")}"
      placeholder="${e(placeholder)}" autocomplete="off" spellcheck="false" aria-label="${e(label)}">`);
  }
  function srcNumber(name, label, unit, min, max, help) {
    const current = value(name);
    const range = "可填 " + min + "–" + max + (unit ? "，单位 " + unit : "");
    return srcRow(name, label, help, "number", `<input type="number" data-field="${e(name)}" value="${e(current ?? "")}" min="${min}" max="${max}"
      step="1" inputmode="numeric" aria-label="${e(label)}" title="${e(range)}"><i>${e(unit)}</i>`);
  }
  function srcSelect(name, label, options) {
    const current = value(name);
    return srcRow(name, label, "", "select", `<select data-field="${e(name)}" aria-label="${e(label)}">${options.map(([v, text]) =>
      `<option value="${e(v)}"${String(current ?? "") === v ? " selected" : ""}>${e(text)}</option>`).join("")}</select>`);
  }
  function materialTime(value) {
    if (typeof value !== "string") return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN", {
      month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit",
    });
  }
  function materialLink(value) {
    try {
      const url = new URL(value);
      return url.protocol === "https:" && !url.username && !url.password ? url.href : "";
    } catch { return ""; }
  }
  function materialWindowKey(item) {
    return typeof item.trace_window_id === "string" && item.trace_window_id
      ? item.trace_window_id : "legacy:" + JSON.stringify([item.trace_target || "", item.trace_role || ""]);
  }
  function materialCard(item) {
    const records = Array.isArray(item.usage?.records) ? item.usage.records : [];
    const search = [item.category, item.purpose, item.title, item.detail, item.note, item.source, item.url,
      item.usage?.status, item.usage?.thought_origin, item.usage?.followup_observation?.status,
      item.trace_target, item.trace_role, item.skip_reason, item.model_skip_reason, ...records.map(record => record.text)]
      .filter(Boolean).join(" ").toLocaleLowerCase();
    const source = item.source || "已核验来源";
    const href = materialLink(item.url);
    const usage = item.usage && typeof item.usage === "object" ? item.usage : null;
    const usageLabels = {delivered:"已送达", partial:"部分送达", unconfirmed:"结果待确认", not_sent:"未发送",
      pending:"等待投递", failed:"投递失败", cancelled:"已取消", reviewing:"等待检视"};
    const skipLabels = {duplicate:"内容重复", low_confidence:"判断把握不足", persona_review:"人格复核未通过",
      continuation_after_silence:"刚回复过且用户未再发言，避开自接话头",
      presentation_empty:"正文整理后为空", forbidden_word:"内容规则限制", model_decision:"模型选择暂不发送",
      source_coverage:"来源支撑不足", no_sendable_content:"没有可发送内容"};
    const modelSkipLabels = {insufficient_context:"上下文不足", no_natural_opening:"当前没有自然的开口",
      purpose_mismatch:"内容与联系目的不匹配", repetitive:"内容重复", respect_user_boundary:"遵守用户边界", other:"其他原因"};
    const usageHistory = records;
    const thoughtOrigin = typeof usage?.thought_origin === "string" ? usage.thought_origin : "";
    const followup = usage?.status === "delivered" ? usage.followup_observation : null;
    const followupLabels = {input_after_delivery:"送达后出现新输入",
      no_input_after_delivery:"暂无后续输入", unknown:"后续输入状态未知"};
    const followupTime = followup?.input_at ? " · " + materialTime(followup.input_at)
      : followup?.status === "no_input_after_delivery" && followup.observed_through
        ? " · 截至 " + materialTime(followup.observed_through) : "";
    const followupMarkup = followup && typeof followup === "object" ? `<span title="仅表示时间上出现后续输入，不代表这条消息已读或得到回应。">
      ${e(followupLabels[followup.status] || "后续输入状态未知")}${e(followupTime)}</span>` : "";
    const skipReason = modelSkipLabels[item.model_skip_reason] || skipLabels[item.skip_reason] || "未生成可投递正文";
    const note = item.note || (usage?.status === "not_sent" ? "未发送原因 · " + skipReason : "");
    const contextMarkup = item.scope === "window" ? `<p class="ct-material-trace-context">
      <span>接收目标</span><b>${e(item.trace_target || "私聊目标")}</b>
      <span>角色</span><b>${e(item.trace_role || "角色")}</b>
    </p>` : "";
    const usageMarkup = usage ? `<div class="ct-material-usage">
      <strong>发送前检视 · ${e(usageLabels[usage.status] || "状态未知")}</strong>
      ${thoughtOrigin ? `<span>念头来由 · ${e(thoughtOrigin)}</span>` : ""}
      <span>${usage.status === "not_sent" && Number(usage.total_parts) === 0
        ? "本次没有可投递的正文"
        : `${e(usage.delivered_parts ?? 0)} / ${e(usage.total_parts ?? 0)} 段已确认送达`}</span>
      ${followupMarkup}
      ${usageHistory.length ? `<ol>${usageHistory.map(record => `<li><time datetime="${e(record.delivered_at || "")}">
        第 ${e(Number.isInteger(record.sequence) ? record.sequence + 1 : "-")} 段 · ${e(materialTime(record.delivered_at))}</time><span>${e(record.text || "")}</span></li>`).join("")}</ol>` : ""}
    </div>` : "";
    return `<article class="ct-material-card" data-material-card data-scope="${e(item.scope || "role")}"
      data-window-id="${e(item.scope === "window" ? materialWindowKey(item) : "")}"
      data-category="${e(item.category || "")}" data-search="${e(search)}">
      <header class="ct-material-card-head"><span class="ct-material-category">${e(item.category || "素材")}</span>
        <span class="ct-material-purpose">${e(item.purpose || "")}</span></header>
      <h3>${e(item.title || "主动念头")}</h3>
      ${item.detail ? `<p class="ct-material-detail">${e(item.detail)}</p>` : ""}
      ${contextMarkup}
      ${usageMarkup}
      ${note ? `<p class="ct-material-note">${e(note)}</p>` : ""}
      <footer><span>${e(source)}</span><span>${usage ? "生成于 " : "采集于 "}${e(materialTime(item.captured_at))}</span>
        <span>${usage ? "发送轨迹" : item.expires_at ? "有效至 " + e(materialTime(item.expires_at)) : "有效期未设"}</span>
        ${href ? `<a class="ct-material-source-link" href="${e(href)}" target="_blank" rel="noopener noreferrer">查看来源</a>` : ""}</footer>
    </article>`;
  }
  function materialsView() {
    const items = Array.isArray(data.materialItems) ? data.materialItems : [];
    const count = items.length;
    const sharedCount = data.materialsSharedCount;
    const traceCount = data.materialsTraceCount;
    const traceTotal = data.materialsTraceTotal;
    const traceCountLabel = traceTotal > traceCount ? `${traceCount} / 共 ${traceTotal}` : String(traceCount);
    const categories = [...new Set(items.map(item => item.category).filter(value => typeof value === "string" && value))];
    const windows = new Map();
    for (const item of items) {
      if (item.scope !== "window") continue;
      const id = materialWindowKey(item);
      if (!windows.has(id)) windows.set(id, `${item.trace_target || "私聊目标"} · ${item.trace_role || "角色"}`);
    }
    let content = "";
    if (data.materialsLoading && !data.materialsLoaded) {
      content = `<div class="ct-material-empty"><span class="ct-spinner"></span><strong>正在读取外部素材与主动记录</strong></div>`;
    } else if (data.materialsError && !data.materialsLoaded) {
      content = `<div class="ct-material-empty is-error">${ICON.alert}<strong>检视记录读取失败</strong><p>${e(data.materialsError)}</p>
        <button type="button" class="ct-btn" data-action="refresh-materials"${data.materialsLoading ? " disabled" : ""}>${ICON.refresh}<span>重试</span></button></div>`;
    } else if (!count) {
      const messages = {refreshing:"正在获取角色共享资讯", not_checked:"正在等待首次巡视",
        not_configured:"尚未启用角色共享资讯来源", failed:"最近一次巡视失败，暂无有效素材",
        unavailable:"角色共享素材当前不可用",
        ready:"当前没有外部素材或主动消息记录"};
      const message = messages[data.materialsState] || messages.ready;
      content = `<div class="ct-material-empty${data.materialsState === "failed" ? " is-error" : ""}">${ICON.source}<strong>${e(message)}</strong>
        ${data.materialsError ? `<p>${e(data.materialsError)}</p>` : ""}</div>`;
    } else {
      content = `<div class="ct-material-grid">${items.map(materialCard).join("")}</div>
        <p class="ct-material-filter-empty" hidden>当前筛选没有匹配记录</p>`;
    }
    const refreshed = data.materialsCheckedAt ? `最近巡视 ${e(materialTime(data.materialsCheckedAt))}` : "";
    const stale = data.materialsState === "stale" ? "最近一次资讯巡视未成功，仍显示已采集的有效外部素材"
      : data.materialsState === "failed" && sharedCount ? "资讯巡视失败，仍显示已采集的有效外部素材" : "";
    const traceWarning = data.materialsTraceState === "failed"
      ? "主动消息检视记录读取失败；外部素材仍可查看"
      : data.materialsTraceState === "partial"
        ? `${data.materialsTraceUnavailable} 个私聊目标无法读取主动记录，其余记录仍可查看` : "";
    return `<div class="ct-materials">
      <div class="ct-material-heading"><strong>素材与主动检视</strong>
        <span>${sharedCount} 条外部素材 · ${traceCountLabel} 条主动记录</span></div>
      <div class="ct-material-kinds" role="group" aria-label="按记录类型筛选">
        ${[["all","全部",count],["role","外部素材",sharedCount],["window","主动记录",traceCount]].map(([kind,label,total]) =>
          `<button type="button" data-material-kind-filter="${kind}" aria-pressed="${data.materialKind === kind}">
            <span>${label}</span><b>${total}</b></button>`).join("")}
      </div>
      <div class="ct-material-tools">
        <label><span>主动记录日期</span><input type="date" data-material-day aria-label="主动记录日期"
          value="${e(data.materialDay)}"${data.materialsLoading ? " disabled" : ""}></label>
        <label><span>私聊窗口</span><select data-material-window aria-label="按私聊窗口筛选">
          <option value="all"${data.materialWindow === "all" ? " selected" : ""}>全部窗口</option>
          ${[...windows].map(([id, label]) => `<option value="${e(id)}"${data.materialWindow === id ? " selected" : ""}>${e(label)}</option>`).join("")}
        </select></label>
        <label class="ct-material-search"><span>查找记录</span>
          <input type="search" data-material-search aria-label="搜索外部素材与主动记录" value="${e(data.materialQuery)}" placeholder="内容、目标、角色或状态">
        </label>
        <label><span>类别</span><select data-material-category aria-label="按记录类别筛选">
          <option value="all"${data.materialCategory === "all" ? " selected" : ""}>全部类别</option>
          ${categories.map(category => `<option value="${e(category)}"${data.materialCategory === category ? " selected" : ""}>${e(category)}</option>`).join("")}
        </select></label>
        <span class="ct-material-count" data-material-result-count>${count} 条记录</span>
        <button type="button" class="ct-btn ct-material-refresh" data-action="refresh-materials" title="刷新外部素材与主动记录" aria-label="刷新检视记录"${data.materialsLoading ? " disabled" : ""}>${ICON.refresh}</button>
      </div>
      ${refreshed ? `<p class="ct-material-updating">${refreshed}</p>` : ""}
      ${stale ? `<p class="ct-material-updating is-error" role="status">${e(stale)}</p>` : ""}
      ${traceWarning ? `<p class="ct-material-updating${data.materialsTraceState === "failed" ? " is-error" : ""}" role="status">${e(traceWarning)}</p>` : ""}
      ${data.materialsLoading && data.materialsLoaded ? `<p class="ct-material-updating" role="status">正在读取更新</p>` : ""}
      ${data.materialsError && data.materialsLoaded ? `<p class="ct-material-updating is-error" role="status">${e(data.materialsError)}</p>` : ""}
      ${content}
    </div>`;
  }
  function updateMaterialFilters() {
    if (!root) return;
    const query = data.materialQuery.trim().toLocaleLowerCase();
    const category = data.materialCategory;
    const cards = [...root.querySelectorAll("[data-material-card]")];
    const candidates = cards.filter(card => (data.materialKind === "all" || card.dataset.scope === data.materialKind)
      && (card.dataset.scope !== "window" || data.materialWindow === "all"
        || card.dataset.windowId === data.materialWindow));
    const candidateSet = new Set(candidates);
    let visible = 0;
    for (const card of cards) {
      const matches = candidateSet.has(card) && (category === "all" || card.dataset.category === category)
        && (!query || String(card.dataset.search || "").includes(query));
      card.hidden = !matches;
      if (matches) visible++;
    }
    const count = root.querySelector("[data-material-result-count]");
    if (count) count.textContent = visible + " / " + candidates.length + " 条记录";
    for (const button of root.querySelectorAll("[data-material-kind-filter]")) {
      button.setAttribute("aria-pressed", String(button.dataset.materialKindFilter === data.materialKind));
    }
    const empty = root.querySelector(".ct-material-filter-empty");
    if (empty) empty.hidden = visible > 0 || cards.length === 0;
  }
  function body() {
    if (data.view === "tests") return testsView();
    if (data.view === "materials") return materialsView();
    if (!settingsAvailable()) return emptyMarkup();
    return {"dial":dialView, "morning":morningView, "purposes":purposesView, "gates":gatesView,
      "budgets":budgetsView, "sources":sourcesView}[data.view]();
  }
  /* 换页。方向按轨道上的先后：往后（往下）点，新的一屏从右边进来；
     往回点从左边进来。整块 DOM 会被重写，旧的一屏没有「离场」这一步——
     所以只有入场动画可写，方向就是唯一能说清「我是往哪边走的」的信息，
     没有方向的话两屏切换看起来只是闪了一下。 */
  function switchView(next) {
    const from = VIEWS.findIndex(([id]) => id === data.view);
    const to = VIEWS.findIndex(([id]) => id === next);
    data.viewShift = to > from ? 1 : to < from ? -1 : null;
    data.view = next;
    data.notice = "";
  }
  function render() {
    if (!root) return;
    /* 取这一帧要闪的盘面部分并立刻清空：动效是「刚刚改了什么」的一次确认，
       不是常驻状态。类由 dialView 写进 HTML，而不是渲染后往节点上加——
       这里是整块 innerHTML 重写，渲染后加类等于加在一个马上被丢弃的节点上。 */
    data.pulseNow = [...data.pulse]; data.pulse.clear();
    data.flipNow = data.flip; data.flip = null;
    /* 换页方向同样在这一帧消费掉：下一次渲染（比如改一个开关）不该重放
       入场动画，否则每动一下整屏都要再滑一次。 */
    data.viewShiftNow = data.viewShift; data.viewShift = null;
    const shift = data.viewShiftNow;
    const enter = shift ? (shift > 0 ? " is-from-next" : " is-from-prev") : "";
    root.innerHTML = `<div class="ct-app">${railMarkup()}<div class="ct-main">`
      + `<div class="ct-view${enter}">${headMarkup()}${noticeMarkup()}${body()}</div></div></div>`;
    if (data.pendingWrite) for (const field of root.querySelectorAll("[data-field]")) field.disabled = true;
    const gateSettingsArea = document.getElementById("contact-gate-settings-area");
    if (gateSettingsArea) gateSettingsArea.hidden = data.view !== "gates";
    if (data.view === "purposes") updatePurposeFilters();
    if (data.view === "tests") updateTestFilters();
    if (data.view === "materials") updateMaterialFilters();
    const screenObservationHost = root.querySelector?.("[data-screen-observation]");
    if (screenObservationHost) window.CompanionScreenObservation?.mount(screenObservationHost);
    else window.CompanionScreenObservation?.unmount();
    const testDialog = root.querySelector?.("[data-test-confirm-dialog]");
    if (testDialog && typeof testDialog.showModal === "function" && !testDialog.open) testDialog.showModal();
    if (data.scanning && data.ready) {
      data.scanning = false;
      const dial = root.querySelector(".ct-dial");
      if (dial) { dial.classList.add("is-entering"); setTimeout(() => dial.classList.remove("is-entering"), 900); }
    }
  }

  /* ---- 交互 ------------------------------------------------------------ */
  let painting = null;
  function focusGateSettings() {
    const target = document.getElementById("contact-gate-settings");
    if (!target) return false;
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches
      || ["low", "off"].includes(document.documentElement.dataset.motion);
    target.scrollIntoView({behavior: reduced ? "instant" : "smooth", block: "start"});
    if (!target.hasAttribute("tabindex")) target.tabIndex = -1;
    target.focus({preventScroll: true});
    return true;
  }
  function openGateSettingsView() {
    if (!root || !data) return false;
    if (data.view !== "gates") { switchView("gates"); render(); }
    return focusGateSettings();
  }
  function clicked(event) {
    const view = event.target.closest("[data-view]");
    if (view) {
      switchView(view.dataset.view); render();
      if (view.dataset.view === "materials") void loadMaterials();
      return;
    }
    const materialKind = event.target.closest("[data-material-kind-filter]")?.dataset.materialKindFilter;
    if (["all", "role", "window"].includes(materialKind)) {
      data.materialKind = materialKind;
      render(); return;
    }
    const testAction = event.target.closest("[data-test-action]")?.dataset.testAction;
    if (testAction === "cancel") { data.confirmTest = ""; render(); return; }
    if (testAction === "confirm") { void runProactiveTest(data.confirmTest); return; }
    if (testAction === "unlock") {
      if (data.testUncertain) { data.confirmTest = TEST_GUARD_RESET; render(); }
      return;
    }
    if (testAction === "confirm-unlock") {
      storeProactiveTestGuard(null);
      data.confirmTest = ""; data.testGuard = null; data.testUncertain = false;
      data.testResults = {};
      notice("已解除测试锁。下一轮测试仍可能真实投递到核心管理员私聊。");
      render(); return;
    }
    const testRun = event.target.closest("[data-test-run]")?.dataset.testRun;
    if (testRun) {
      if (data.testBusy || data.testUncertain) return;
      data.confirmTest = testRun;
      render(); return;
    }
    const testCopy = event.target.closest("[data-test-copy]")?.dataset.testCopy;
    if (testCopy) { copyTestInstruction(testCopy); return; }
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action === "refresh") {
      data.notice = ""; data.error = false; loadContract();
      if (data.view === "materials") void loadMaterials();
      return;
    }
    if (action === "refresh-materials") { void loadMaterials(); return; }
    if (action === "retry-contract") { void loadContract(); return; }
    if (action === "open-gate-settings") { focusGateSettings(); return; }
    if (action === "save") { save(); return; }
    if (action === "retry-pending") { save(); return; }
    if (action === "reset") { resetDraft(); notice("已放弃未保存的改动。"); render(); return; }
    if (action === "dismiss") { data.notice = ""; render(); return; }
    if (action === "curve-default") {
      set("proactive_hour_activity_curve", DEFAULT_CURVE); notice("已恢复默认曲线，保存后生效。"); render(); return;
    }
    if (action === "curve-flat") {
      set("proactive_hour_activity_curve", curveText(new Array(24).fill(1)));
      notice("已拉平到 1.00，保存后生效：所有小时同等保留。"); render(); return;
    }
    const tier = event.target.closest("[data-tier]")?.dataset.tier;
    if (tier && TIER_BY_KEY[tier]) {
      /* 环的形变不在这里当场加类：render() 会立刻重写整块 DOM，
         加在旧节点上的动画一帧都放不出来（改版前这里就是这么写的，
         于是 ct-shift 是一条从没跑过的规则）。
         现在由 set() 记进 data.pulse，渲染时变成 .is-pulse-tier。 */
      set("proactive_intensity_preset", tier);
      render(); return;
    }
    const anchor = event.target.closest("[data-anchor]")?.dataset.anchor;
    if (anchor) {
      data.notice = "问候窗口 " + String(value("greeting_" + anchor + "_window") || "") + "；改下面那三个框。";
      render(); return;
    }
  }
  function changed(event) {
    const testMode = event.target.closest("[data-test-mode]");
    if (testMode) { data.testModes = {...data.testModes, [testMode.dataset.testMode]:testMode.value}; render(); return; }
    const testVariant = event.target.closest("[data-test-variant]");
    if (testVariant) { data.testVariants = {...data.testVariants, [testVariant.dataset.testVariant]:testVariant.value}; render(); return; }
    const materialDay = event.target.closest("[data-material-day]");
    if (materialDay) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(materialDay.value)) return;
      data.materialDay = materialDay.value;
      data.materialWindow = "all";
      void loadMaterials();
      return;
    }
    const materialWindow = event.target.closest("[data-material-window]");
    if (materialWindow) {
      data.materialWindow = materialWindow.value || "all";
      updateMaterialFilters();
      return;
    }
    const materialCategory = event.target.closest("[data-material-category]");
    if (materialCategory) {
      data.materialCategory = materialCategory.value;
      updateMaterialFilters();
      return;
    }
    const testBucket = event.target.closest("[data-test-bucket-filter]");
    if (testBucket) { data.testBucket = testBucket.value; updateTestFilters(); return; }
    const testCoverage = event.target.closest("[data-test-coverage-filter]");
    if (testCoverage) { data.testCoverage = testCoverage.value; updateTestFilters(); return; }
    const bucketFilter = event.target.closest("[data-purpose-bucket-filter]");
    if (bucketFilter) {
      data.purposeBucket = bucketFilter.value;
      updatePurposeFilters();
      return;
    }
    const purposeFilter = event.target.closest("[data-purpose-filter]");
    if (purposeFilter) {
      data.purposeFilter = purposeFilter.value;
      updatePurposeFilters();
      return;
    }
    const field = event.target.closest("[data-field]");
    if (!field) return;
    const name = field.dataset.field;
    if (field.type === "checkbox") {
      const on = !!field.checked;
      readField(name, on);
      /* 开与关都要录：两个方向的动画同样是一次 switch 动作，只播一半等于没播。 */
      data.flip = {name, on};
    }
    else if (field.type === "range") readField(name, Number(field.value));
    else if (field.type === "number") {
      const n = Number(field.value);
      readField(name, Number.isFinite(n) ? n : 0);
    } else if (field.type === "datetime-local") readField(name, toIso(field.value));
    else readField(name, field.value);
    /* 权重与额度的滑块：只重画那一条的填充，不整页重画——
       整页重画会把手上的滑块弄丢，拖到一半就断了。 */
    const range = field.type === "range" ? field : null;
    if (range) {
      const fill = range.parentElement?.querySelector(".ct-weight-fill");
      if (fill) fill.style.setProperty("--w", (Number(range.value) / 5) * 100 + "%");
      const em = range.parentElement?.querySelector("em");
      /* 权重是小数（0.1 步进），额度是整数（整步进）——判据是**步长**，不是 max。
         曾经写成 `range.max === "5"`：max 是字符串，恒不等于数字 5，
         于是权重永远被显示成整数，3.5 变成 4（草稿里存的是 3.5，界面却写 4）。 */
      if (em) em.textContent = Number(range.step) < 1 ? Number(range.value).toFixed(1) : String(range.value);
      const quota = range.closest(".ct-quota");
      const shown = quota?.querySelector(".ct-quota-value b");
      if (shown) shown.textContent = String(range.value);
      return;
    }
    render();
  }
  function readField(name, value_) { set(name, value_); }
  function input(event) {
    const materialSearch = event.target.closest("[data-material-search]");
    if (materialSearch) {
      data.materialQuery = materialSearch.value;
      updateMaterialFilters();
      return;
    }
    const testSearch = event.target.closest("[data-test-search]");
    if (testSearch) { data.testQuery = testSearch.value; updateTestFilters(); return; }
    const search = event.target.closest("[data-purpose-search]");
    if (search) {
      data.purposeQuery = search.value;
      updatePurposeFilters();
      return;
    }
    const field = event.target.closest("[data-field]");
    if (field && field.type === "range") changed(event);
  }
  async function copyTestInstruction(id) {
    const item = TEST_INSTRUCTIONS.find(candidate => candidate.id === id);
    if (!item) return;
    try {
      const clipboard = window.navigator?.clipboard;
      if (typeof clipboard?.writeText !== "function") throw new Error("clipboard unavailable");
      await clipboard.writeText(testInstructionText(item));
      notice(item.id + " 测试指令已复制；当前只复制文本，没有调用模型或真实投递。");
    } catch {
      notice("剪贴板权限不可用，请选中当前类型卡片中的指令手动复制。", true);
    }
    render();
  }
  async function runProactiveTest(id) {
    const item = TEST_INSTRUCTIONS.find(candidate => candidate.id === id);
    if (!item || data.testBusy || data.testUncertain) return;
    const ticket = version;
    const guard = {purpose:id, state:"pending", started_at:Date.now()};
    if (!storeProactiveTestGuard(guard)) {
      data.confirmTest = "";
      data.testResults[id] = {state:"failed", failure_title:"测试没有启动",
        error:"浏览器未能保存防重复测试状态。", suggestion:"请允许此页面使用浏览器存储后再运行。"};
      notice("未能保存防重复测试状态，因此没有启动测试。", true);
      render(); return;
    }
    data.confirmTest = "";
    data.testBusy = id;
    data.testGuard = guard;
    data.testResults[id] = null;
    const synthetic = testUsesSynthetic(id);
    notice(synthetic ? "正在生成合成事件并等待模型与 QQ 投递回执…" : "正在获取真实来源、运行分析与消息模型，并等待 QQ 投递回执…");
    render();
    try {
      const purpose = id === "mobile_location" ? "environment" : id === "reaction_image" ? "chat" : id;
      const result = await api("POST", "setup/proactive-diagnostic",
        {purpose, confirm_delivery: true, synthetic_event: synthetic,
          ...(id === "environment" ? {variant:data.testVariants?.environment || "weather"} : {}),
          ...(id === "mobile_location" ? {variant:"mobile_location"} : {}),
          ...(id === "reaction_image" ? {diagnostic_image:true} : {})}, id === "screen" && !synthetic ? 300000 : 180000);
      if (!live(ticket)) return;
      if (!["passed", "skipped", "failed", "unknown", "pending"].includes(result.state)) {
        throw problem("invalid_response");
      }
      data.testResults[id] = result;
      const uncertain = ["unknown", "pending"].includes(result.state);
      data.testUncertain = uncertain;
      data.testGuard = uncertain ? {...guard, state:result.state} : null;
      storeProactiveTestGuard(data.testGuard);
      notice(uncertain ? "测试结果未确认。请先核对核心管理员私聊；不要重试。"
        : result.state === "passed" ? "测试通过，消息已投递到核心管理员私聊。"
        : result.state === "skipped" ? "测试完成，模型按当前素材判断本次不发送。"
        : "测试未通过，查看卡片中的阶段和建议。", result.state === "failed");
    } catch (error) {
      if (!live(ticket)) return;
      const uncertain = error?.confirmed !== true;
      data.testUncertain = uncertain;
      data.testGuard = uncertain ? {...guard, state:"unknown"} : null;
      storeProactiveTestGuard(data.testGuard);
      data.testResults[id] = {
        state: uncertain ? "unknown" : "failed",
        error: uncertain ? "请求未收到可核验的最终结果。" : reason(error.code),
        suggestion: uncertain ? "请核对核心管理员私聊；确认是否收到前，不要再次启动测试。" : "修正提示的问题后可重新运行测试。",
      };
      notice(uncertain ? "测试请求结果未知。请先核对核心管理员私聊，不要重试。"
        : "测试请求被拒绝，未启动测试。", true);
    } finally {
      if (live(ticket)) { data.testBusy = ""; render(); }
    }
  }
  /* 曲线拖涂。按下开始，横向移动换算成小时，纵向换算成权重——
     这是这一页唯一一个「直接画数据」的地方，因为 24 个数字确实没人愿意输。 */
  function plotRect() { return root?.querySelector('[data-role="curvePlot"]')?.getBoundingClientRect() || null; }
  function paintHour(clientX, clientY, rect) {
    if (data.pendingWrite) return;
    const x = Math.max(0, Math.min(rect.width - 0.01, clientX - rect.left));
    const hour = Math.min(23, Math.floor((x / rect.width) * 24));
    const y = Math.max(0, Math.min(rect.height, clientY - rect.top));
    const weight = Math.max(0.05, Math.min(2, (1 - y / rect.height) * 2));
    const list = curveOf(value("proactive_hour_activity_curve")).slice();
    if (Math.abs(list[hour] - weight) < 0.03) return;
    list[hour] = weight;
    data.draft["proactive_hour_activity_curve"] = curveText(list);
    data.touched.add("proactive_hour_activity_curve");
    const col = root.querySelector('.ct-curve-col[data-hour="' + hour + '"]');
    if (col) {
      const bar = col.querySelector("i");
      if (bar) bar.style.height = Math.round((weight / 2) * 100) + "%";
      col.title = String(hour).padStart(2, "0") + ":00 · 权重 " + weight.toFixed(2);
    }
    const read = root.querySelector('[data-role="curveText"]');
    if (read) read.value = curveText(list);
  }
  function pointerDown(event) {
    if (!event.target.closest('[data-role="curvePlot"]')) return;
    const rect = plotRect();
    if (!rect) return;
    painting = true;
    event.target.setPointerCapture?.(event.pointerId);
    paintHour(event.clientX, event.clientY, rect);
  }
  function pointerMove(event) {
    if (!painting) return;
    const rect = plotRect();
    if (rect) paintHour(event.clientX, event.clientY, rect);
  }
  function pointerUp() { painting = null; }
  function keydown(event) {
    if (event.key === "Escape" && data.notice) { data.notice = ""; render(); return; }
    /* ⌘/Ctrl + S 保存。这一页改动多，来回找按钮很烦。 */
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
      event.preventDefault(); save(); return;
    }
    const rail = event.target.closest("[data-view]");
    if (rail && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      event.preventDefault();
      const index = VIEWS.findIndex(([id]) => id === data.view);
      const next = (index + (event.key === "ArrowDown" ? 1 : VIEWS.length - 1)) % VIEWS.length;
      switchView(VIEWS[next][0]); render();
      root.querySelector('[data-view="' + data.view + '"]')?.focus();
    }
  }

  /* ---- 挂载 ------------------------------------------------------------ */
  async function whenBridgeReady(ticket, timeout = 10000) {
    if (window.AstrBotPluginPage?.apiGet) return window.AstrBotPluginPage;
    const product = window.CompanionProduct;
    if (product?.bridgeNow || product?.bridgeFor) {
      try {
        const found = product.bridgeNow?.() || await product.bridgeFor();
        if (found?.apiGet) return found;
      } catch { /* 交给下面的轮询 */ }
      if (window.AstrBotPluginPage?.apiGet) return window.AstrBotPluginPage;
    }
    return new Promise((resolve, reject) => {
      const started = Date.now();
      const tick = () => {
        if (!live(ticket)) return;
        const found = window.AstrBotPluginPage;
        if (found?.apiGet) return resolve(found);
        if (Date.now() - started >= timeout) return reject(problem("bridge_unavailable"));
        setTimeout(tick, 80);
      };
      tick();
    });
  }
  function addresses() {
    /* 配置和共享素材读取都走固定安装级路由。 */
    data.configPath = rel(PREFIX + "config");
    data.nativePath = rel(PREFIX + "perception-native");
  }
  async function mount(host) {
    /* 顺序有讲究：unmount() 会 ++version 并清掉 data，所以
       ① 先 unmount，② 再建新 data，③ 才取票号。
       在 unmount() 之前取票会让下面第一处 live(ticket) 立即为假，
       页面看着挂上了但其实一个请求都没发；
       在建 data 之前 render() 会读到 null —— 那是一条只在首次挂载时出现的崩溃。 */
    unmount();
    root = host; contactScope = currentContactScope();
    const cached = contactCache.get(contactScope);
    data = cached ? {...blank(), ...cached, confirmTest:"", testBusy:"", touched:new Set(cached.touched || []), draft:{...(cached.draft || {})}, pulse:new Set(), pulseNow:[], flip:null, flipNow:null, viewShift:null, viewShiftNow:null} : blank();
    const testGuard = restoreProactiveTestGuard();
    if (testGuard) {
      data.testGuard = testGuard;
      data.testUncertain = true;
      data.testResults[testGuard.purpose] = {state:"unknown",
        error:"页面没有收到上一轮测试的最终回执。",
        suggestion:"请核对核心管理员私聊；确认上一轮结束后再解除测试锁。"};
    }
    /* 页面切换只缓存草稿和筛选，不把旧的网络错误带回新的读取周期。 */
    if (data.error) { data.notice = ""; data.error = false; }
    const ticket = ++version;
    root.addEventListener("click", clicked);
    root.addEventListener("change", changed);
    root.addEventListener("input", input);
    root.addEventListener("keydown", keydown);
    root.addEventListener("pointerdown", pointerDown);
    root.addEventListener("pointermove", pointerMove);
    root.addEventListener("pointerup", pointerUp);
    root.addEventListener("pointercancel", pointerUp);
    render();
    try {
      bridge = await whenBridgeReady(ticket);
      if (!live(ticket)) return;
      const context = await bounded(bridge.ready());
      if (!live(ticket)) return;
      if (context?.pluginName !== HOST) throw problem("bridge_unavailable");
      addresses();
      data.pendingWrite = restorePendingWrite();
      await loadContract();
      if (live(ticket)) render();
    } catch (error) {
      if (!live(ticket)) return;
      notice(reason(error.code), true);
      render();
    }
  }
  function unmount() {
    window.CompanionScreenObservation?.unmount();
    ++version;
    if (autoRecoveryTimer !== null) clearTimeout(autoRecoveryTimer);
    autoRecoveryTimer = null;
    autoRecoveryKey = "";
    autoRecoveryAttempts = 0;
    cacheContact();
    if (!root) { data = null; bridge = null; contactScope = ""; return; }
    root.removeEventListener("click", clicked);
    root.removeEventListener("change", changed);
    root.removeEventListener("input", input);
    root.removeEventListener("keydown", keydown);
    root.removeEventListener("pointerdown", pointerDown);
    root.removeEventListener("pointermove", pointerMove);
    root.removeEventListener("pointerup", pointerUp);
    root.removeEventListener("pointercancel", pointerUp);
    root = null; bridge = null; data = null; contactScope = ""; painting = null;
  }
  /* `state` 只为测试驱动而暴露：这一页的 data 是模块私有的，
     测试要断言环的几何与阈值时没有别的入口。 */
  return {mount, unmount, openGateSettingsView,
    get state() { return {data}; },
    _internals:{minutes, hm, angle, point, curveOf, curveText, tierOf, arc, quietSpan}};
})();
