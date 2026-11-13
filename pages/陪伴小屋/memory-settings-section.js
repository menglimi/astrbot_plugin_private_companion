/* 整理策略配置：事实与经历页面的独立视图。

   档案操作与策略编辑分开呈现，但共用同一领域入口，避免把策略散落在决策页。

   接口：mount(element) / unmount()，与表达页、config-editor 的适配层一致。
   字段名照 companion_v2/memory/configuration.py 的注册表抄——写错的字段名
   不会报错，只会掉进末尾的兜底分组并显示成原始 id。 */
"use strict";
window.CompanionMemorySettingsSection = (() => {
  const PREFIX = "/astrbot_plugin_private_companion/";

  const labels = {
    enabled: "启用整理", automatic_context: "自动注入相关事实",
    context_items: "自动上下文条数", context_chars: "自动上下文字符数",
    query_items: "查询结果条数", query_chars: "查询结果字符数",
    source_chars: "单个来源读取字符数", turn_chars: "单轮事实字符预算",
    turn_calls: "单轮事实查询次数", core_items: "核心事实条数", core_chars: "核心事实字符数",
    inclusion_chars: "整理材料字符上限",
    reflection_enabled: "定时整理", reflection_timezone: "整理时区",
    reflection_time: "每日整理时间", reflection_response_format: "模型返回格式",
    reflection_auto_repair: "允许修复未完成整理项",
    group_episode_memory_enabled: "群聊经历",
    group_episode_refresh_minutes: "群聊经历刷新间隔（分钟）",
    group_episode_min_messages: "群聊经历最少消息数", max_group_episodes: "群聊经历上限",
    dialogue_episode_memory_enabled: "私聊对话经历",
    dialogue_episode_refresh_messages: "对话经历刷新消息数",
    dialogue_episode_refresh_minutes: "对话经历刷新间隔（分钟）",
    max_dialogue_episodes: "对话经历上限",
    habit_learning_enabled: "用户习惯学习", habit_min_count: "习惯最少观察次数",
    habit_max_items: "习惯条目上限", habit_refresh_minutes: "习惯刷新间隔（分钟）",
    food_menu_recommendation_enabled: "推荐菜谱",
  };
  const helps = {
    automatic_context: "关闭后不会主动把事实塞进这一轮对话，仍可在「来源与经历」里手动查询。",
    reflection_timezone: "IANA 时区名；定时整理按这个时区计算每日时刻。",
    reflection_time: "每天在这个时刻整理一次；时区由上一条决定。",
    reflection_response_format: "取决于整理模型支持的模式，选错会被后端拒绝。",
    inclusion_chars: "单次整理最多读取多少字符的材料。",
    turn_chars: "一轮对话里事实内容的总长度上限。",
    turn_calls: "一轮对话最多查几次事实，防止反复检索拖慢回复。",
    habit_min_count: "同一习惯被观察到这么多次才会沉淀，避免一次就下结论。",
    food_menu_recommendation_enabled: "允许角色在合适的时候推荐菜谱；关闭后不再产生这类建议。",
    group_episode_refresh_minutes: "每隔这么久把「本群最近发生」整理成一段经历。",
    dialogue_episode_refresh_minutes: "私聊对话经历按消息数或间隔先到者触发整理。",
  };
  /* 整理时区的常见取值。IANA 名写错要到后端校验才报错，先给一份可选建议。 */
  const TIMEZONES = ["Asia/Shanghai", "Asia/Tokyo", "Asia/Singapore", "UTC",
    "Europe/London", "Europe/Berlin", "America/New_York", "America/Los_Angeles"];
  /* 分组按语义切，字段名照注册表抄（见文件头）。 */
  const sections = [
    {id: "core", title: "事实与注入", help: "决定事实怎么进入对话，以及单轮最多读多少。",
     fields: ["enabled", "automatic_context", "context_items", "context_chars", "query_items",
              "query_chars", "source_chars", "turn_chars", "turn_calls", "core_items", "core_chars"]},
    {id: "reflection", title: "定时整理", help: "把来源提炼成事实与经历；时区与时刻决定什么时候跑。",
     fields: ["reflection_enabled", "reflection_timezone", "reflection_time",
              "reflection_response_format", "reflection_auto_repair", "inclusion_chars"]},
    {id: "episode", title: "经历整理", help: "周期性把最近的对话压成一段经历，群聊与私聊各有一套阈值。",
     fields: ["group_episode_memory_enabled", "group_episode_refresh_minutes",
              "group_episode_min_messages", "max_group_episodes", "dialogue_episode_memory_enabled",
              "dialogue_episode_refresh_messages", "dialogue_episode_refresh_minutes",
              "max_dialogue_episodes"]},
    {id: "habit", title: "习惯与推荐", help: "从重复出现的模式里沉淀偏好；观察次数不够就不下结论。",
     fields: ["habit_learning_enabled", "habit_min_count", "habit_max_items",
              "habit_refresh_minutes", "food_menu_recommendation_enabled"]},
  ];

  let host = null, instance = null, boundPath = null;

  /* memory_api 的角色根路径（相对，PREFIX 已剥掉）。 */
  function relativePath() {
    const raw = window.CompanionProduct?.role()?.memory_api;
    if (typeof raw !== "string" || !raw.startsWith(PREFIX)) return null;
    const value = raw.slice(PREFIX.length);
    if (!value || /[?#%\\:]/.test(value)
        || value.split("/").some(part => !part || part === "." || part === "..")) return null;
    return value;
  }

  function unmount() {
    if (instance) instance.unmount();
    if (host) host.textContent = "";
    instance = null; host = null; boundPath = null;
  }

  function mount(element) {
    const root = element;
    unmount();
    if (!root) return;
    host = root;
    if (!window.CompanionProduct?.hosted?.()) {
      root.innerHTML = '<div class="wb-notice">请从已登录的 AstrBot 管理界面打开陪伴小屋，'
        + '连接运行实例后调整整理策略。</div>';
      return;
    }
    const path = relativePath();
    if (!path) {
      root.innerHTML = '<div class="wb-notice is-error">当前对象没有可用的整理设置。'
        + '请先选择已初始化的私聊对象和角色。</div>';
      return;
    }
    if (!window.CompanionWorkbench?.create) {
      root.innerHTML = '<div class="wb-notice is-error">配置工作台未加载，无法编辑整理策略。</div>';
      return;
    }
    boundPath = path;
    instance = window.CompanionWorkbench.create({
      group: "memory",
      title: "整理策略",
      note: "",
      labels, helps,
      /* 注册表里这两个是 TEXT，但整理时刻与 IANA 时区名各有更合适的控件。 */
      inputs: {reflection_time: "time", reflection_timezone: "line"},
      suggestions: {reflection_timezone: TIMEZONES},
      sections,
      /* 读取在 {memory_api}/settings（工作台的 GET 直接打 endpoint），
         写入在 {memory_api}/settings/command。别据 memory_api 反推前缀，
         照 memory_api.py 的 PREFIX 拼。 */
      endpoint: PREFIX + path + "/settings",
      savePath: "/command",
      /* 不能带 query：整理设置路由显式拒绝任何查询串。载荷是扁平的。 */
      valuesKey: "values",
      sendTokenKey: "expected_edit_token",
      resetKey: "reset_to_inherit",
      outbound: name => "memory." + name,
      mount: () => host,
    });
    instance.mount();
  }

  /* 宿主整块重写 innerHTML 后调用：重新绑定并重绘，不重新读取。 */
  function attach() {
    if (instance) instance.attach();
  }

  /* 全局搜索用：把这一页的字段摊成条目，与 reply-page.searchEntries() 同形。
     记忆页共有 30 个可编辑字段，此前整组不在搜索索引里，只能靠在「事实与经历」
     里逐段找。 */
  function searchEntries() {
    return sections.flatMap(section => (section.fields || []).filter(name => labels[name]).map(field => ({
      group: "memory", groupTitle: "整理策略", field,
      label: labels[field] || field, help: helps[field] || "",
      section: section.id, sectionTitle: section.title, sectionCount: sections.length,
    })));
  }

  return {mount, unmount, attach, labels, helps, sections, searchEntries,
    get state() { return {instance, path: boundPath}; }};
})();
