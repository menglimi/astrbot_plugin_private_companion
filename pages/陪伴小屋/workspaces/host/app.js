"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g,
    character => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character]));
  const candidates = [
    { mode: "multi", prefix: "host" },
  ];
  const labels = {
    dashboard_identity_required: "请从已登录的 AstrBot 管理页面打开本页。",
    core_access_required: "当前管理账号无权编辑角色与会话。",
    host_disabled: "当前角色尚未启用，请刷新状态。",
    staging_settings_incomplete: "插件配置尚未填写完整，请补齐目标账号、会话与模型。",
    staging_settings_invalid: "插件配置无效，请检查角色、窗口和模型连接设置。",
    /* 已固定运行实例读不出来。真实原因藏在 reason 里（缺字段/未知字段/版本不符），
       后端把它编进码尾（staging_intent_invalid__missing_fields），见 labels 兜底。 */
    staging_intent_invalid: "已保存的账号与人格绑定配置无法读取，请核对账号与会话设置。",
    staging_intent_write_failed: "运行实例配置写入失败，请检查插件数据目录是否可写。",
    staging_profile_conflict: "已登记账号、私聊对象或 AstrBot 人格发生变化，请核对角色与窗口映射。",
    staging_not_initialized: "角色数据正在自动准备，请稍候刷新状态。",
    staging_session_unavailable: "当前没有可用的 V2 会话，请刷新状态后重试。",
    staging_host_terminated: "插件正在停止或已停止，请刷新状态后重试。",
    staging_batch_invalid: "指定的对话批次不存在或已不可读取。",
    staging_delivery_session_active: "角色映射正在应用，请稍候再保存。",
    authorization_denied: "当前关系设置不允许在这个会话中回复。",
    authorization_expired: "当前会话权限已失效，请检查关系与权限设置。",
    authorization_not_active: "当前会话权限尚未生效，请检查关系与权限设置。",
    authorization_revoked: "当前关系设置已收回回复权限。",
    scope_not_granted: "当前会话没有相应的回复权限。",
    staging_delivery_grant_changed: "当前会话权限发生变化，请重新读取状态。",
    staging_review_only: "当前角色运行实例尚未就绪，请刷新状态。",
    staging_session_mode_conflict: "当前会话状态已变化，请重新读取后重试。",
    staging_session_still_stopping: "上一会话仍在收尾，请稍后重新完成连接。",
    staging_account_unavailable: "所选平台账号当前未连接。",
    staging_platform_unavailable: "找不到配置的平台实例。",
    staging_conversation_missing: "目标会话在 AstrBot 中不可用，请先建立目标私聊会话。",
    staging_persona_mismatch: "目标会话当前选择的人格与固定运行实例不一致。",
    staging_provider_unsupported: "当前模型连接暂不支持此操作。",
    host_account_unavailable: "至少一个配置账号当前未连接。",
    host_persona_unavailable: "至少一个配置人格当前无法解析。",
    host_persona_invalid: "人格提示词或对话示例格式无法读取，请检查 AstrBot 人格设置。",
    host_binding_changed: "当前账号、会话或人格映射与已保存的运行配置不一致，请核对角色映射。",
    host_not_initialized: "角色数据尚未准备完成，请重新保存角色映射。",
    host_configuration_changed: "运行配置已变化，请重新读取并保存角色映射。",
    host_store_in_use: "当前运行库已绑定其他实例，请核对实例与数据目录。",
    host_identity_conflict: "现有账号或窗口已绑定其他角色，请核对角色映射。",
    host_identity_changed: "窗口所属账号或对象已变化，请重新读取角色映射。",
    host_persona_binding_changed: "窗口的人格绑定已变化，请刷新状态后重试。",
    host_installation_invalid: "运行实例标识无效，请检查插件的实例设置。",
    host_mappings_invalid: "账号与人格映射格式无效，请重新读取并保存角色映射。",
    host_mappings_conflict: "账号、窗口或人格存在重复绑定，请核对角色映射。",
    host_mappings_required: "请先绑定至少一个 Bot 账号与人格。",
    host_group_delivery_invalid: "群聊发送设置无效，请检查插件的群聊设置。",
    host_capabilities_invalid: "角色能力设置无效，请检查插件的能力配置。",
    host_provider_required: "AstrBot 暂无可用的文本模型；请先在 AstrBot 模型配置中启用 Provider。",
    host_provider_changed: "模型连接在准备期间发生变化，请刷新状态后重试。",
    host_target_unavailable: "目标窗口不在当前配置中，请刷新并核对角色映射。",
    host_terminated: "插件正在停止或已停止，请重新加载插件后重试。",
    account_config_persistence_unavailable: "当前环境无法安全保存插件设置。",
    account_config_running: "角色映射正在应用，请稍候再读取状态。",
    account_config_invalid: "绑定配置不完整或会话来源已变化，请重新核对。",
    account_config_save_failed: "保存失败，请检查插件数据目录是否可写后重试。",
    account_model_provider_invalid: "共享启动模型请求没有通过校验（与角色映射、会话来源无关），请刷新后重试；若仍失败，请到插件配置里设置模型。",
    account_model_provider_failed: "共享启动模型保存失败，请刷新状态并检查插件配置后重试。",
    account_session_unavailable: "这个会话已切换或不在当前目录中，请刷新。",
    account_session_persona_pinned: "AstrBot 已固定此会话人格，请在角色映射中调整。",
    account_session_persona_unmapped: "这个人格尚未绑定到当前 Bot，请先添加人格绑定。",
    account_core_window_required: "已确认的核心管理员窗口需要保留。",
    host_must_stop: "当前任务仍在收尾，请稍后保存映射。",
    setup_targets_unavailable: "会话目录暂时不可读取。",
    host_provider_unavailable: "所选共享启动模型当前不可用，请重新选择。",
    host_control_failed: "角色运行环境准备失败，请查看 AstrBot 日志中的 CompanionV2 异常详情。",
    host_module_unavailable: "插件模块加载失败，请重新加载 V2 插件；若仍失败，请检查安装包并查看 AstrBot 日志。",
    host_native_lookup_timeout: "AstrBot 读取原生状态超时，请刷新状态后重试。",
    host_target_not_ready: "角色或窗口尚未就绪，请核对映射和模型连接。",
    bridge_unavailable: "页面连接尚未就绪，请从插件管理页面重新打开。",
  };
  /* 后端把「运行实例读不出来」的真实原因编进码尾（staging_intent_invalid__schema）。
     具体原因比泛码更能指导修复，所以先查这张表；查不到再退回泛码文案。 */
  const intentReasons = {
    __bom: "文件带 UTF-8 BOM，请以无 BOM 的 UTF-8 重新保存。",
    __not_utf8: "文件不是合法的 UTF-8 文本。",
    __unreadable: "文件无法读取，请检查插件数据目录的权限。",
    __too_large: "文件超过 128 KB 上限，可能被别的程序改写。",
    __not_json: "文件不是合法 JSON，可能被手工编辑或写坏。",
    __duplicate: "JSON 里存在重复键。",
    __not_finite: "文件中存在非法数值（NaN / Infinity）。",
    __version: "配置文件的版本与当前插件不一致。",
    __schema: "字段与当前版本不符：缺少必需字段或存在未知字段。",
    __scope: "目标会话、账号或人格信息不完整或互不匹配。",
    __fingerprint: "人格或模型信息已变化，请重新读取后保存映射。",
    __invalid: "文件内容不合法。",
  };
  const lookupStages = {
    session_directory: "会话目录",
    conversation_id: "当前会话编号",
    conversation: "会话人格绑定",
    session_settings: "会话服务设置",
    persona: "会话生效人格",
    conversation_verify: "会话切换校验",
  };
  const failureOperations = {
    load_existing: "上次加载已保存配置失败",
    initialize_profile: "上次准备角色失败",
    start: "上次启动失败",
    probe: "上次检查运行连接失败",
  };
  /* 码可能带 `__后缀`；先按整码查，再按后缀查，最后回退。 */
  function labelFor(code) {
    if (!code) return "";
    const stage = code.startsWith("host_native_lookup_timeout__")
      ? lookupStages[code.slice("host_native_lookup_timeout__".length)] : "";
    if (stage) return `AstrBot 读取${stage}超时，请刷新状态后重试。`;
    if (labels[code]) return labels[code];
    const cut = code.indexOf("__");
    if (cut > 0) {
      const base = labels[code.slice(0, cut)];
      const detail = intentReasons[code.slice(cut)];
      if (base || detail) return [base, detail].filter(Boolean).join(" ");
    }
    return "";
  }
  function failureMessage(code) {
    const label = labelFor(code);
    if (label) return label;
    const reference = typeof code === "string" && /^[a-z][a-z0-9_]{0,127}$/.test(code)
      ? `（错误码：${code}）` : "";
    return `操作未完成${reference}，请查看 AstrBot 日志中的 CompanionV2 详情。`;
  }
  let bridge = null;
  let host = null;
  let status = null;
  let busy = false;
  let topologySelection = null;
  let topologyHover = null;
  let topologySnapshot = null;
  let topologyFrame = 0;
  let topologySessionPage = 0;
  let topologySessionFilter = "all";
  let bindingConfig = null;
  let modelConfig = null;
  let modelSaving = false;
  let bindingsDirty = false;
  let groupTargetsDirty = false;
  let groupTargetDraft = null;
  let directoryRead = null;
  let acquaintanceRead = null;
  let acquaintanceDirectory = null;
  const accountApi = "accounts";
  const TOPOLOGY_SESSION_PAGE_SIZE = 5;
  const TOPOLOGY_PIN_STORAGE_KEY = "astrbot_plugin_private_companion.topology.pinned_sessions";
  /* One stable colour per AstrBot account keeps two account chains legible. */
  const TOPOLOGY_ACCOUNT_COLORS = Object.freeze([
    "#5964d8", "#2f9488", "#c8792f", "#a45bb7", "#c05462", "#3d84c7", "#7b9438",
  ]);
  let topologyPinnedSessions = readPinnedTopologySessions();

  function readPinnedTopologySessions() {
    try {
      const saved = window.localStorage.getItem(TOPOLOGY_PIN_STORAGE_KEY);
      if (saved) {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed)) return new Set(parsed.filter(value => typeof value === "string" && value));
      }
      const legacy = window.localStorage.getItem("astrbot_plugin_private_companion.topology.pinned_session");
      const migrated = new Set(legacy ? [legacy] : []);
      if (migrated.size) writePinnedTopologySessions(migrated);
      return migrated;
    } catch { return new Set(); }
  }

  function writePinnedTopologySessions(values) {
    try {
      if (values.size) window.localStorage.setItem(TOPOLOGY_PIN_STORAGE_KEY, JSON.stringify([...values]));
      else window.localStorage.removeItem(TOPOLOGY_PIN_STORAGE_KEY);
      window.localStorage.removeItem("astrbot_plugin_private_companion.topology.pinned_session");
      return true;
    } catch { return false; }
  }

  const displayValue = (value, fallback = "未标记") => {
    const text = String(value ?? "").trim();
    return text || fallback;
  };
  const platformLabel = value => ({qq:"QQ · OneBot",default:"QQ · OneBot",aiocqhttp:"QQ · OneBot",onebot:"QQ · OneBot",qq_official:"QQ 官方机器人",qq_official_webhook:"QQ 官方机器人（Webhook）"})[value] || "当前平台";
  const nativeId = value => String(value ?? "").trim();
  const numericId = value => /^\d+$/.test(nativeId(value)) ? nativeId(value) : "";
  const topologyScope = (target) => {
    const kind = target?.scope_kind === "group" || target?.group_id ? "群聊" : "私聊";
    const id = nativeId(target.group_id || target.participant_id);
    const name = groupDisplayName(target) || (kind === "私聊" ? String(target.display_name || "").trim() : "");
    // Numeric OneBot ids are useful beside a nickname; opaque QQ Official
    // openids are shown only when no human-readable window name is available.
    const suffix = numericId(id) || (name ? "" : id);
    return `${kind} ${name ? name + (suffix ? `（${suffix}）` : "") : suffix || "当前对象"}`;
  };
  const accountChoiceLabel = item => {
    const id = nativeId(item.account_id || item.account);
    const name = item.account_name || item.name;
    return name ? name + (id ? `（${id}）` : "") : id ? `账号 ${id}` : "待确认账号";
  };
  const groupDisplayName = item => {
    const display = String(item?.display_name || item?.group_name || "").trim();
    return display && !/^\d+$/.test(display) ? display : "";
  };
  const personaName = name => name === "[%None]" ? "未指定人格" : !name ? "未确定人格" : name;
  const personaLabel = item => personaName(item.name || item.personaId);
  const stripRoleChannel = value => String(value || "").replace(/\s*[-_]\s*(?:私聊|群聊)\s*$/, "").trim();
  const roleDisplayName = (roleId, personaLabels = []) => {
    const key = String(roleId || "").trim();
    if (key && !/^(role-|staging-|dashboard:|onebot-user:)/.test(key))
      return stripRoleChannel(key) || "当前角色";
    const labels = [...new Set(personaLabels.map(label =>
      stripRoleChannel(personaName(label))).filter(Boolean))];
    return labels.join("、") || key || "当前角色";
  };
  const personaSourceLabels = Object.freeze({
    v2_window_override: "V2 窗口覆盖",
    astrbot_selection: "AstrBot 当前选择",
    role_mapping: "仅按角色映射",
    unresolved: "未确定",
  });
  function personaSourceLabel(row) {
    if (personaSourceLabels[row?.persona_source]) return personaSourceLabels[row.persona_source];
    if (row?.persona_override) return personaSourceLabels.v2_window_override;
    if (!row || row.details_available === false || !(row.persona_id || row.persona_override))
      return personaSourceLabels.unresolved;
    if (row.persona_pinned === true) return personaSourceLabels.role_mapping;
    return row.native === true ? personaSourceLabels.astrbot_selection : personaSourceLabels.unresolved;
  }
  const topologySessionPinKey = row => row?.session_key ? `session:${row.session_key}`
    : row?.managed && row.target_id ? `target:${row.target_id}` : "";
  function personaChoiceLabel(item) {
    const account = (bindingConfig?.discovered?.accounts || []).find(row => row.bot_id === item.bot_id);
    return `${account ? accountChoiceLabel(account) : "当前账号"} · ${personaName(item.label || item.persona_id)}`;
  }
  const nativePersonaKey = (botId, personaId) => JSON.stringify([botId, personaId]);
  function activeRoleAssignments() {
    const editor = $("edit-accounts");
    if (!bindingsDirty || !editor) return bindingConfig?.personas || [];
    const mappings = [...editor.querySelectorAll(".role-binding-card")].flatMap(card => {
      const roleId = card.querySelector("[data-role-id]").value.trim();
      return [...card.querySelectorAll("[data-persona-choice]:checked")].map(input => ({
        bot_id: input.dataset.botId, persona_id: input.dataset.personaId, role_id: roleId,
      }));
    });
    return [...new Map(mappings.map(row => [JSON.stringify([row.bot_id, row.persona_id, row.role_id]), row])).values()];
  }
  function mappedAccounts() {
    const accounts = bindingConfig?.discovered?.accounts || bindingConfig?.accounts || [];
    return [...new Map(accounts.filter(account => activeRoleAssignments().some(row => row.bot_id === account.bot_id))
      .map(account => [bindingAccountKey(account), {platform_id: account.platform_id,
        account_id: account.account_id, bot_id: account.bot_id}])).values()];
  }
  function mergeSharedRolePersonas(snapshot) {
    const personas = new Map(snapshot.personas.map(persona => [persona.key, persona]));
    const canonicalByKey = new Map();
    const groups = new Map();
    for (const persona of personas.values()) {
      if (!persona.roleId || !persona.personaId) continue;
      const key = JSON.stringify([persona.roleKey, persona.personaId]);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(persona);
    }
    for (const group of groups.values()) {
      if (group.length < 2) continue;
      group.sort((left, right) => left.key.localeCompare(right.key));
      const canonical = group[0];
      for (const duplicate of group.slice(1)) {
        canonical.accountKeys = new Set([...canonical.accountKeys, ...duplicate.accountKeys]);
        canonical.sessionKeys = new Set([...canonical.sessionKeys, ...duplicate.sessionKeys]);
        canonicalByKey.set(duplicate.key, canonical.key);
        personas.delete(duplicate.key);
      }
    }
    if (canonicalByKey.size) {
      const remap = keys => [...new Set([...keys].map(key => canonicalByKey.get(key) || key))];
      snapshot.roles.forEach(role => { role.personaKeys = new Set(remap(role.personaKeys)); });
      snapshot.accounts.forEach(account => { account.personaKeys = new Set(remap(account.personaKeys)); });
      snapshot.sessions.forEach(session => { session.personaKeys = remap(session.personaKeys); });
      personas.forEach(persona => {
        persona.accountKeys = new Set(remap(persona.accountKeys));
        persona.sessionKeys = new Set(remap(persona.sessionKeys));
      });
    }
    return {...snapshot, personas: [...personas.values()]};
  }

  function makeTopologySnapshot() {
    const discovered = bindingConfig?.discovered || {};
    const inventory = Array.isArray(discovered.targets)
      ? discovered.targets : Array.isArray(status?.targets) ? status.targets : [];
    const targets = inventory.filter(target => target.native === true
      && target.group_id !== "*" && target.participant_id !== "*");
    const accounts = new Map();
    const roles = new Map();
    const personas = new Map();
    const mappedPersonas = new Map();
    const sessions = [];
    const primaryLinks = (bindingConfig?.window_links || []).filter(link => link.mode === "primary");
    const hiddenWindowIds = new Set(primaryLinks.flatMap(link => link.alias_target_ids || []));
    const accountKey = target => `account:${JSON.stringify([target.platform_id || "", target.account_id || "", target.bot_id || ""])}`;
    const ensureAccount = target => {
      const key = accountKey(target);
      if (!accounts.has(key)) accounts.set(key, {key, platform_id: target.platform_id || "",
        platform_name: target.platform_name || "", account_id: target.account_id || "",
        bot_id: target.bot_id || "", account_name: target.account_name || "",
        color: TOPOLOGY_ACCOUNT_COLORS[accounts.size % TOPOLOGY_ACCOUNT_COLORS.length],
        roleKeys: new Set(), personaKeys: new Set(), sessionKeys: new Set()});
      return accounts.get(key);
    };
    const ensurePersona = (botId, nativePersonaId) => {
      const personaId = nativePersonaId == null ? "" : String(nativePersonaId);
      const key = "persona:" + JSON.stringify([botId, personaId]);
      if (!personas.has(key)) personas.set(key, {key, botId, personaId,
        name: personaName(personaId), roleId: "", roleKey: "", accountKeys: new Set(), sessionKeys: new Set()});
      return personas.get(key);
    };
    const ensureRole = (botId, roleId, name = roleId, mapped = true) => {
      const key = roleId ? `role:${roleId}` : `role:unassigned:${botId}`;
      if (!roles.has(key)) roles.set(key, {key, botId, roleId, name, mapped,
        personaKeys: new Set(), accountKeys: new Set(), sessionKeys: new Set()});
      return roles.get(key);
    };
    (discovered.accounts || []).forEach(ensureAccount);
    (discovered.personas || []).forEach((choice) => {
      const persona = ensurePersona(String(choice.bot_id || ""), String(choice.persona_id || "[%None]"));
      persona.label = personaName(choice.label || choice.persona_id);
      for (const account of accounts.values()) if (account.bot_id === choice.bot_id) persona.accountKeys.add(account.key);
    });
    for (const mapping of activeRoleAssignments()) {
      const botId = String(mapping.bot_id || "");
      const personaId = String(mapping.persona_id || "[%None]");
      const roleId = String(mapping.role_id || "");
      if (!botId || !roleId) continue;
      const role = ensureRole(botId, roleId);
      const roleKey = role.key;
      const persona = ensurePersona(botId, personaId);
      persona.roleId = roleId;
      persona.roleKey = roleKey;
      role.personaKeys.add(persona.key);
      mappedPersonas.set(JSON.stringify([botId, personaId]), {roleId, roleKey, personaKey: persona.key});
      for (const account of accounts.values()) if (account.bot_id === botId) {
        persona.accountKeys.add(account.key);
        account.roleKeys.add(roleKey);
        role.accountKeys.add(account.key);
      }
    }
    for (const role of roles.values()) {
      role.name = roleDisplayName(role.roleId, [...role.personaKeys]
        .map(key => personas.get(key)?.label || personas.get(key)?.personaId));
    }
    targets.forEach((target, targetIndex) => {
      if (hiddenWindowIds.has(String(target.target_id))) return;
      const account = ensureAccount(target);
      const personaId = String(target.persona_override || target.persona_id || "");
      const detailsAvailable = target.details_available !== false;
      const persona = detailsAvailable ? ensurePersona(account.bot_id, personaId) : null;
      const mapping = persona ? mappedPersonas.get(JSON.stringify([account.bot_id, personaId])) : null;
      if (persona) persona.accountKeys.add(account.key);
      const sessionKey = ["session", displayValue(target.session_key || target.target_id, `${target.scope_kind || "target"}-${targetIndex}`)].join(":");
      const session = {key: sessionKey, targetId: displayValue(target.target_id, `窗口 ${targetIndex + 1}`),
        label: topologyScope(target), scopeKind: target.scope_kind === "group" || target.group_id ? "group" : "private",
        accountKey: account.key, accountColor: account.color,
        accountLabel: accountChoiceLabel(account), platformName: account.platform_name || "",
        personaKeys: persona ? [persona.key] : [],
        roleKey: mapping?.roleKey || "",
        roleId: mapping?.roleId || "", registered: Boolean(mapping) && target.managed === true && !bindingsDirty,
        draft: Boolean(mapping) && bindingsDirty,
        mergedAliases: primaryLinks.find(link => link.primary_target_id === String(target.target_id))?.alias_target_ids?.length || 0,
        pinKey: topologySessionPinKey(target), platform: account.platform_id, raw: target};
      sessions.push(session);
      if (persona) persona.sessionKeys.add(sessionKey);
      account.sessionKeys.add(sessionKey);
      if (session.roleKey) roles.get(session.roleKey)?.sessionKeys.add(sessionKey);
    });
    for (const persona of personas.values()) {
      for (const key of persona.accountKeys) accounts.get(key)?.personaKeys.add(persona.key);
      if (persona.roleId) continue;
      const role = ensureRole(persona.botId, "", "未归入角色", false);
      role.personaKeys.add(persona.key);
      persona.roleKey = role.key;
      for (const key of persona.accountKeys) {
        role.accountKeys.add(key);
        accounts.get(key)?.roleKeys.add(role.key);
      }
      for (const key of persona.sessionKeys) {
        role.sessionKeys.add(key);
        const session = sessions.find(row => row.key === key);
        if (session) session.roleKey = role.key;
      }
    }
    const snapshot = mergeSharedRolePersonas({accounts: [...accounts.values()], roles: [...roles.values()],
      personas: [...personas.values()], sessions});
    snapshot.sessions.sort((left, right) => Number(topologyPinnedSessions.has(right.pinKey))
      - Number(topologyPinnedSessions.has(left.pinKey)));
    return snapshot;
  }

  function topologyItems(data) {
    return [...data.accounts.map((item) => ({...item, kind: "account"})),
      ...data.roles.map((item) => ({...item, kind: "role"})),
      ...data.personas.map((item) => ({...item, kind: "persona"})),
      ...data.sessions.map((item) => ({...item, kind: "session"}))];
  }

  function connectedTopologyKeys(data, selected) {
    if (!selected) return null;
    const all = topologyItems(data);
    const item = all.find((entry) => entry.key === selected);
    if (!item) return null;
    const keys = new Set([item.key]);
    if (item.kind === "account") {
      item.roleKeys.forEach((key) => keys.add(key));
      item.personaKeys.forEach((key) => keys.add(key));
      item.sessionKeys.forEach((key) => keys.add(key));
    } else if (item.kind === "role") {
      item.accountKeys.forEach((key) => keys.add(key));
      item.personaKeys.forEach((key) => keys.add(key));
      item.sessionKeys.forEach((key) => keys.add(key));
    } else if (item.kind === "persona") {
      item.accountKeys.forEach((key) => keys.add(key));
      item.sessionKeys.forEach((key) => keys.add(key));
      if (item.roleKey) keys.add(item.roleKey);
    } else {
      if (item.accountKey) keys.add(item.accountKey);
      if (item.roleKey) keys.add(item.roleKey);
      item.personaKeys.forEach((key) => keys.add(key));
    }
    return keys;
  }

  function topologyItemLabel(item) {
    if (!item) return "当前关系";
    if (item.kind === "account") return `AstrBot 账号 ${accountChoiceLabel(item)}`;
    if (item.kind === "role") return item.mapped ? `角色 ${item.name}` : `未归类人格 · ${item.name}`;
    if (item.kind === "persona") return `AstrBot 人格 ${personaLabel(item)}`;
    return `会话窗口 ${item.label}`;
  }

  function updateTopologyNote(connected) {
    const note = $("topology-note");
    if (!note || !topologySnapshot || !(topologySnapshot.roles.length
        || topologySnapshot.personas.length || topologySnapshot.sessions.length)) return;
    if (!topologySelection) {
      note.textContent = "";
      note.hidden = true;
      return;
    }
    const selected = topologyItems(topologySnapshot).find((item) => item.key === topologySelection);
    const relatedCount = Math.max(0, (connected?.size || 1) - 1);
    note.hidden = false;
    note.textContent = `${topologyItemLabel(selected)} · ${relatedCount} 个关联节点`;
  }

  function paintTopologySelection(manage = true) {
    if (!topologySnapshot) return;
    const highlighted = topologyHover || topologySelection;
    const connected = connectedTopologyKeys(topologySnapshot, highlighted);
    const selectedConnections = connectedTopologyKeys(topologySnapshot, topologySelection);
    document.querySelectorAll(".topology-node, .topology-role-heading").forEach((node) => {
      const active = Boolean(connected && connected.has(node.dataset.topologyKey));
      node.classList.toggle("is-active", active);
      node.classList.toggle("is-muted", Boolean(connected && !connected.has(node.dataset.topologyKey)));
      node.setAttribute("aria-pressed", String(node.dataset.topologyKey === topologySelection));
    });
    document.querySelectorAll(".topology-role-group").forEach(group => {
      group.classList.toggle("is-active", Boolean(connected && connected.has(group.dataset.roleKey)));
      group.classList.toggle("is-muted", Boolean(connected && !connected.has(group.dataset.roleKey)));
    });
    document.querySelectorAll(".topology-line").forEach((line) => {
      const active = Boolean(connected && connected.has(line.dataset.topologySource)
        && connected.has(line.dataset.topologyTarget));
      line.classList.toggle("is-active", active);
      line.classList.toggle("is-muted", Boolean(connected && !active));
    });
    const canvas = $("topology-canvas");
    if (canvas) canvas.dataset.selection = highlighted ? "active" : "none";
    updateTopologyNote(selectedConnections);
    if (manage) renderSessionManager();
  }

  function setTopologyHover(key) {
    if (topologyHover === key) return;
    topologyHover = key;
    paintTopologySelection(false);
  }

  function topologyNode(item, kind, index) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "topology-node";
    button.dataset.topologyKey = item.key;
    button.dataset.topologyKind = kind;
    const head = document.createElement("span");
    head.className = "topology-node-head";
    const title = document.createElement("span");
    title.className = "topology-node-title";
    title.textContent = kind === "account" ? accountChoiceLabel(item)
      : kind === "persona" ? personaLabel(item) : item.label;
    const badge = document.createElement("span");
    const isPinned = kind === "session" && topologyPinnedSessions.has(item.pinKey);
    badge.className = `topology-node-badge${item.scopeKind === "group" ? " group" : ""}${isPinned ? " pinned" : ""}`;
    badge.textContent = kind === "account" ? `${mappedRoleCount(item)} 个角色`
      : kind === "persona" ? `${item.sessionKeys.size} 个窗口`
        : `${isPinned ? "置顶 · " : ""}${item.scopeKind === "group" ? "群聊" : "私聊"}${item.mergedAliases ? ` · 已合并 ${item.mergedAliases}` : ""}`;
    head.append(title, badge);
    const meta = document.createElement("span");
    meta.className = "topology-node-meta";
    if (kind === "account") meta.textContent = `${platformLabel(item.platform_name || item.platform_id)} · Bot ${item.bot_id || "未标记"}`;
    else if (kind === "persona") meta.textContent = "AstrBot 人格";
    else meta.textContent = item.raw.unified_msg_origin?.split(":").pop().includes("_") ? "分成员会话" : item.scopeKind === "group" ? "群聊窗口" : "私聊窗口";
    const detail = document.createElement("span");
    detail.className = "topology-node-detail";
    if (kind === "account") detail.textContent = `${item.sessionKeys.size} 个原生会话窗口`;
    else if (kind === "persona") detail.textContent = item.roleId
      ? bindingsDirty ? "角色映射尚未保存" : "随角色自动接入窗口" : "归入角色后自动运行";
    else {
      const row = item.raw;
      const selectedPersona = item.personaKeys.map(key =>
        topologySnapshot?.personas.find(persona => persona.key === key)).find(Boolean);
      const persona = row.details_available === false ? "未确定人格"
        : personaLabel(selectedPersona || {personaId: row.persona_override || row.persona_id});
      const role = item.roleId
        ? topologySnapshot?.roles.find(entry => entry.key === item.roleKey)?.name || roleDisplayName(item.roleId)
        : "未归入角色";
      const mapping = item.draft ? "角色映射未保存" : item.registered ? "随角色运行"
        : row.details_available === false ? "详情未读到" : "等待归入角色";
      const runtime = row.native
        ? row.session_enabled === false ? "会话已停用" : row.llm_enabled === false ? "回复已关闭" : "回复已开启"
        : "尚未建立 AstrBot 会话";
      detail.textContent = `人格 ${persona} → 角色 ${role} · 来源：${personaSourceLabel(row)} · ${mapping} · ${runtime}`;
    }
    button.append(head, meta, detail);
    button.style.setProperty("--node-index", String(index));
    const accountColor = kind === "account" ? item.color : kind === "session" ? item.accountColor : "";
    if (accountColor) button.style.setProperty("--account-color", accountColor);
    wireTopologyControl(button, item, kind);
    return button;
  }

  function mappedRoleCount(account) {
    return (topologySnapshot?.roles || []).filter(role => role.mapped && account.roleKeys.has(role.key)).length;
  }

  function wireTopologyControl(button, item, kind) {
    button.dataset.topologyKey = item.key;
    button.dataset.topologyKind = kind;
    button.title = `${topologyItemLabel({...item, kind})} · 管理`;
    button.setAttribute("aria-label", topologyItemLabel({...item, kind}));
    button.setAttribute("aria-pressed", String(item.key === topologySelection));
    button.disabled = busy;
    button.addEventListener("pointerenter", () => setTopologyHover(item.key));
    button.addEventListener("pointerleave", () => {
      if (topologyHover === item.key) setTopologyHover(null);
    });
    button.addEventListener("focus", () => setTopologyHover(item.key));
    button.addEventListener("blur", () => {
      if (topologyHover === item.key && !button.matches(":hover")) setTopologyHover(null);
    });
    button.addEventListener("click", () => {
      topologySelection = topologySelection === item.key ? null : item.key;
      paintTopologySelection();
    });
  }

  function topologyRoleGroup(role, index) {
    const hideUnlinked = Boolean($("hide-unlinked-personas")?.checked);
    const personas = topologySnapshot.personas.filter(persona => role.personaKeys.has(persona.key)
      && (!hideUnlinked || persona.roleId || persona.sessionKeys.size));
    if (!personas.length) return null;
    const group = document.createElement("section");
    group.className = `topology-role-group${role.mapped ? "" : " is-unassigned"}`;
    group.dataset.roleKey = role.key;
    group.setAttribute("aria-label", role.mapped ? `角色 ${role.name}` : "未归入角色的人格");
    const heading = document.createElement("button");
    heading.type = "button";
    heading.className = "topology-role-heading";
    const title = document.createElement("strong");
    title.textContent = role.mapped ? `角色 · ${role.name}` : role.name;
    const count = document.createElement("span");
    count.textContent = `${personas.length} 个人格`;
    heading.append(title, count);
    wireTopologyControl(heading, role, "role");
    if (role.mapped && role.name !== role.roleId) heading.title += " · 内部标识 " + role.roleId;
    const list = document.createElement("div");
    list.className = "topology-personas";
    list.append(...personas.map((persona, personaIndex) => topologyNode(persona, "persona", index + personaIndex)));
    group.append(heading, list);
    return group;
  }

  function renderTopologySessions(data, {resetScroll = false} = {}) {
    const list = $("topology-sessions");
    const pagination = $("topology-session-pagination");
    const previous = $("topology-session-prev");
    const next = $("topology-session-next");
    const pageLabel = $("topology-session-page");
    if (!list || !pagination || !previous || !next || !pageLabel) return;
    const filtered = topologySessionFilter === "all" ? data.sessions
      : data.sessions.filter(item => item.scopeKind === topologySessionFilter);
    document.querySelectorAll("[data-session-filter]").forEach(button => {
      const kind = button.dataset.sessionFilter;
      const count = kind === "all" ? data.sessions.length
        : data.sessions.filter(item => item.scopeKind === kind).length;
      button.textContent = `${kind === "all" ? "全部" : kind === "private" ? "私聊" : "群聊"} ${count}`;
      button.setAttribute("aria-pressed", String(kind === topologySessionFilter));
    });
    const pageCount = Math.max(1, Math.ceil(filtered.length / TOPOLOGY_SESSION_PAGE_SIZE));
    topologySessionPage = Math.min(Math.max(0, topologySessionPage), pageCount - 1);
    const start = topologySessionPage * TOPOLOGY_SESSION_PAGE_SIZE;
    const visible = filtered.slice(start, start + TOPOLOGY_SESSION_PAGE_SIZE);
    if (visible.length) list.replaceChildren(...visible.map((item, index) => topologyNode(item, "session", index)));
    else {
      const empty = document.createElement("p");
      empty.className = "topology-session-filter-empty";
      empty.textContent = topologySessionFilter === "group" ? "没有群聊窗口" : "没有私聊窗口";
      list.replaceChildren(empty);
    }
    if (resetScroll) list.scrollTop = 0;
    const paged = pageCount > 1;
    pagination.hidden = !paged;
    pageLabel.textContent = paged
      ? `第 ${topologySessionPage + 1} / ${pageCount} 页 · ${start + 1}–${start + visible.length} / ${filtered.length}`
      : "";
    previous.dataset.disabled = String(topologySessionPage === 0);
    next.dataset.disabled = String(topologySessionPage >= pageCount - 1);
    previous.disabled = previous.dataset.disabled === "true";
    next.disabled = next.dataset.disabled === "true";
  }

  function changeTopologySessionPage(delta) {
    if (!topologySnapshot) return;
    const filtered = topologySessionFilter === "all" ? topologySnapshot.sessions
      : topologySnapshot.sessions.filter(item => item.scopeKind === topologySessionFilter);
    if (filtered.length <= TOPOLOGY_SESSION_PAGE_SIZE) return;
    const pageCount = Math.ceil(filtered.length / TOPOLOGY_SESSION_PAGE_SIZE);
    const nextPage = Math.min(Math.max(0, topologySessionPage + delta), pageCount - 1);
    if (nextPage === topologySessionPage) return;
    topologySessionPage = nextPage;
    topologyHover = null;
    if (topologySelection?.startsWith("session:")) topologySelection = null;
    renderTopologySessions(topologySnapshot, {resetScroll: true});
    scheduleTopologyLinks();
  }

  function drawTopologyLinks() {
    const data = topologySnapshot;
    const canvas = $("topology-canvas");
    const svg = $("topology-links");
    if (!data || !canvas || !svg || canvas.hidden) return;
    const canvasRect = canvas.getBoundingClientRect();
    const width = Math.max(1, canvasRect.width);
    const height = Math.max(1, canvasRect.height);
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const nodes = [...canvas.querySelectorAll(".topology-node")];
    const find = (key) => nodes.find((node) => node.dataset.topologyKey === key);
    const existing = new Map([...svg.querySelectorAll(".topology-line")].map(line =>
      [JSON.stringify([line.dataset.topologySource, line.dataset.topologyTarget]), line]));
    const retained = new Set();
    const visibleRect = node => {
      const rect = node.getBoundingClientRect();
      const list = node.closest(".topology-session-list");
      if (!list) return rect;
      const viewport = list.getBoundingClientRect();
      const left = Math.max(rect.left, viewport.left);
      const right = Math.min(rect.right, viewport.right);
      const top = Math.max(rect.top, viewport.top);
      const bottom = Math.min(rect.bottom, viewport.bottom);
      // A card can exist on this page while being clipped by the scroll list.
      if (right <= left || bottom <= top) return null;
      return {left, right, top, bottom, width: right - left, height: bottom - top};
    };
    const accountColors = new Map((data.accounts || []).map(account => [account.key, account.color]));
    const connections = [];
    data.personas.forEach((persona) => {
      persona.accountKeys.forEach((key) => connections.push({sourceKey: key, targetKey: persona.key,
        accountKey: key}));
    });
    data.sessions.forEach((session) => {
      session.personaKeys.forEach(key => connections.push({sourceKey: key, targetKey: session.key,
        accountKey: session.accountKey}));
    });
    const port = (from, to) => {
      const horizontalRight = to.left >= from.right - 4;
      const horizontalLeft = to.right <= from.left + 4;
      if (horizontalRight) return [[from.right - canvasRect.left, from.top - canvasRect.top + from.height / 2],
        [to.left - canvasRect.left, to.top - canvasRect.top + to.height / 2], false];
      if (horizontalLeft) return [[from.left - canvasRect.left, from.top - canvasRect.top + from.height / 2],
        [to.right - canvasRect.left, to.top - canvasRect.top + to.height / 2], false];
      if (to.top >= from.bottom - 4) return [[from.left - canvasRect.left + from.width / 2, from.bottom - canvasRect.top],
        [to.left - canvasRect.left + to.width / 2, to.top - canvasRect.top], true];
      return [[from.left - canvasRect.left + from.width / 2, from.top - canvasRect.top],
        [to.left - canvasRect.left + to.width / 2, to.bottom - canvasRect.top], true];
    };
    connections.forEach(({sourceKey, targetKey, accountKey}, lineIndex) => {
      const source = find(sourceKey);
      const target = find(targetKey);
      if (!source || !target) return;
      const sourceRect = visibleRect(source);
      const targetRect = visibleRect(target);
      if (!sourceRect || !targetRect) return;
      const [start, end, vertical] = port(sourceRect, targetRect);
      const distance = vertical ? Math.max(18, Math.abs(end[1] - start[1]) * .42)
        : Math.max(22, Math.abs(end[0] - start[0]) * .42);
      const key = JSON.stringify([sourceKey, targetKey]);
      let path = existing.get(key);
      if (!path) {
        path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.classList.add("topology-line");
        path.dataset.topologySource = sourceKey;
        path.dataset.topologyTarget = targetKey;
        path.setAttribute("pathLength", "1");
        path.style.setProperty("--line-delay", `${Math.min(lineIndex * 48, 240)}ms`);
        svg.append(path);
      }
      path.style.setProperty("--topology-account-color",
        accountColors.get(accountKey) || "var(--accent)");
      retained.add(path);
      path.setAttribute("d", vertical
        ? `M ${start[0]} ${start[1]} C ${start[0]} ${start[1] + distance}, ${end[0]} ${end[1] - distance}, ${end[0]} ${end[1]}`
        : `M ${start[0]} ${start[1]} C ${start[0] + distance} ${start[1]}, ${end[0] - distance} ${end[1]}, ${end[0]} ${end[1]}`);
    });
    for (const line of existing.values()) if (!retained.has(line)) line.remove();
    paintTopologySelection(false);
  }

  function scheduleTopologyLinks() {
    if (topologyFrame) cancelAnimationFrame(topologyFrame);
    topologyFrame = requestAnimationFrame(() => {
      topologyFrame = 0;
      drawTopologyLinks();
    });
  }

  function renderTopology() {
    const canvas = $("topology-canvas");
    const empty = $("topology-empty");
    const mode = $("topology-mode");
    const counts = $("topology-counts");
    const note = $("topology-note");
    if (!canvas || !empty || !mode || !counts || !note) return;
    const data = makeTopologySnapshot();
    topologySnapshot = data;
    topologyHover = null;
    const hasTopology = data.accounts.length > 0 || data.roles.length > 0 || data.sessions.length > 0;
    mode.textContent = "账号与会话链路";
    mode.title = hasTopology ? "连线与账号卡片的侧边色条对应不同 AstrBot 账号" : "";
    mode.dataset.state = hasTopology ? "ready" : "empty";
    counts.textContent = hasTopology
      ? `${data.accounts.length} 个账号 · ${data.roles.filter(role => role.mapped).length} 个角色 · ${data.sessions.length} 个窗口`
      : "尚未返回关系";
    note.textContent = hasTopology ? "" : "暂无 AstrBot 人格或会话窗口。";
    note.hidden = hasTopology;
    if (!hasTopology) {
      topologySelection = null;
      topologySessionPage = 0;
      canvas.hidden = true;
      empty.hidden = false;
      renderSessionManager();
      return;
    }
    empty.hidden = true;
    canvas.hidden = false;
    const lists = {accounts: $("topology-accounts"), roles: $("topology-roles"), sessions: $("topology-sessions")};
    lists.accounts.replaceChildren(...data.accounts.map((item, index) => topologyNode(item, "account", index)));
    lists.roles.replaceChildren(...data.roles.map(topologyRoleGroup).filter(Boolean));
    renderTopologySessions(data);
    if (topologySelection && !topologyItems(data).some((item) => item.key === topologySelection)) topologySelection = null;
    renderSessionManager();
    scheduleTopologyLinks();
  }

  function selectTopologySession(key) {
    topologySelection = key;
    const selected = topologySnapshot.sessions.find(row => row.key === key);
    if (selected && topologySessionFilter !== "all" && selected.scopeKind !== topologySessionFilter) {
      topologySessionFilter = selected.scopeKind;
    }
    const filtered = topologySessionFilter === "all" ? topologySnapshot.sessions
      : topologySnapshot.sessions.filter(item => item.scopeKind === topologySessionFilter);
    const index = filtered.findIndex((row) => row.key === key);
    topologySessionPage = Math.max(0, Math.floor(index / TOPOLOGY_SESSION_PAGE_SIZE));
    renderTopologySessions(topologySnapshot, {resetScroll: true});
    scheduleTopologyLinks();
    paintTopologySelection();
  }

  let windowLinkTargetId = "";
  function windowLinkFor(targetId) {
    return (bindingConfig?.window_links || []).find(link =>
      link.primary_target_id === targetId || (link.alias_target_ids || []).includes(targetId)) || null;
  }
  function renderWindowLink(item) {
    const panel = $("session-window-link");
    if (!panel) return;
    const isPrivate = item.kind === "session" && item.scopeKind === "private";
    panel.hidden = !isPrivate;
    if (!isPrivate) { windowLinkTargetId = ""; return; }
    const targetId = String(item.targetId || "");
    windowLinkTargetId = targetId;
    const link = windowLinkFor(targetId);
    const primaryMode = link?.mode === "primary";
    const select = $("window-link-aliases");
    const mode = $("window-link-mode");
    const save = $("window-link-save");
    const unlink = $("window-link-unlink");
    const state = $("window-link-state");
    const aliases = (topologySnapshot?.sessions || []).filter(row => row.scopeKind === "private"
      && String(row.targetId || "") !== targetId
      && !((bindingConfig?.window_links || []).some(other => other.primary_target_id !== targetId
        && (other.alias_target_ids || []).includes(row.targetId))));
    select.replaceChildren(...aliases.map(row => {
      const option = document.createElement("option");
      option.value = row.targetId;
      option.textContent = row.label;
      return option;
    }));
    save.disabled = !aliases.length || busy;
    if (link) {
      const primary = topologySnapshot?.sessions.find(row => row.targetId === link.primary_target_id);
      const names = (link.alias_target_ids || []).map(alias =>
        topologySnapshot?.sessions.find(row => row.targetId === alias)?.label || alias);
      if (link.primary_target_id === targetId) {
        state.textContent = `当前窗口是主窗口，已合并 ${names.length} 个窗口：${names.join("、") || "—"}。${primaryMode ? "回复统一到当前窗口。" : "只共享角色，回复仍回各自窗口。"}`;
        unlink.hidden = false;
      } else {
        state.textContent = `当前窗口已合并到「${primary?.label || link.primary_target_id}」， ${primaryMode ? "消息由主窗口统一回复。" : "角色和记忆共享，回复仍在当前窗口。"}`;
        unlink.hidden = false;
      }
    } else {
      state.textContent = aliases.length ? "选择要合并的窗口和合并方式。" : "没有其他可合并的私聊窗口。";
      unlink.hidden = true;
    }
    mode.disabled = Boolean(link && link.primary_target_id !== targetId);
    select.disabled = Boolean(link && link.primary_target_id !== targetId);
    save.hidden = Boolean(link && link.primary_target_id !== targetId);
    unlink.dataset.primaryTargetId = link?.primary_target_id || "";
  }
  async function saveWindowLink() {
    if (busy || !windowLinkTargetId) return;
    const aliases = [...($("window-link-aliases")?.selectedOptions || [])].map(option => option.value);
    const mode = $("window-link-mode")?.value || "role";
    if (!aliases.length) { notice("请选择至少一个要合并的窗口。", "error"); return; }
    setBusy(true);
    try {
      await api("POST", `${accountApi}/window-link`, {primary_target_id: windowLinkTargetId,
        alias_target_ids: aliases, mode}, 60000);
      await readBindingConfig();
      try { await readStatus(); } catch { /* configuration is already saved */ }
      notice(mode === "primary" ? "窗口已合并，回复和主动联系将由主窗口接管。" : "窗口已合并，只共享角色与记忆。", "success");
    } catch (error) {
      notice(`窗口合并未完成：${publicError(error)}`, "error");
    } finally { setBusy(false); renderTopology(); }
  }
  async function unlinkWindow() {
    const primary = $("window-link-unlink")?.dataset.primaryTargetId;
    if (busy || !primary) return;
    setBusy(true);
    try {
      await api("POST", `${accountApi}/window-unlink`, {primary_target_id: primary}, 60000);
      await readBindingConfig();
      try { await readStatus(); } catch { /* configuration is already saved */ }
      notice("窗口合并已解除。", "success");
    } catch (error) {
      notice(`解除合并未完成：${publicError(error)}`, "error");
    } finally { setBusy(false); renderTopology(); }
  }

  function renderSessionManager() {
    const panel = $("session-manager");
    const item = topologyItems(topologySnapshot || {accounts: [], roles: [], personas: [], sessions: []})
      .find((row) => row.key === topologySelection);
    panel.hidden = !item;
    if (!item) return;
    $("session-manager-heading").textContent = topologyItemLabel(item);
    const pinButton = $("topology-pin-session");
    const isSession = item.kind === "session";
    const isPinned = isSession && topologyPinnedSessions.has(item.pinKey);
    pinButton.hidden = !isSession;
    pinButton.textContent = isPinned ? "取消置顶" : "置顶窗口";
    pinButton.setAttribute("aria-pressed", String(isPinned));
    pinButton.disabled = busy;
    renderWindowLink(item);
    const related = $("session-manager-related");
    related.replaceChildren();
    renderGroupNetwork(item.kind === "session" ? item.raw : null);
    $("session-manager-state").textContent = "";
    if (item.kind !== "session") {
      $("session-manager-meta").textContent = item.kind === "account"
        ? `${platformLabel(item.platform_name || item.platform_id)} · ${mappedRoleCount(item)} 个角色 · ${item.sessionKeys.size} 个原生会话`
        : item.kind === "persona" ? `${item.roleId ? `角色 ${topologySnapshot.roles.find(role => role.key === item.roleKey)?.name || item.roleId}` : "未归入角色"} · ${item.sessionKeys.size} 个会话窗口`
          : `${item.personaKeys.size} 个 AstrBot 人格 · ${item.accountKeys.size} 个账号 · ${item.sessionKeys.size} 个会话窗口`;
      const appendRelated = (label, key) => {
        const button = document.createElement("button");
        button.type = "button"; button.className = "button button-quiet"; button.textContent = label;
        button.addEventListener("click", () => {
          topologySelection = key;
          paintTopologySelection();
        });
        related.append(button);
      };
      if (item.kind === "account") {
        item.personaKeys.forEach((key) => {
          const persona = topologySnapshot.personas.find((row) => row.key === key);
          if (persona) appendRelated(`人格 ${personaLabel(persona)}`, persona.key);
        });
      } else if (item.kind === "role") {
        item.personaKeys.forEach((key) => {
          const persona = topologySnapshot.personas.find((row) => row.key === key);
          if (persona) appendRelated(`人格 ${personaLabel(persona)}`, persona.key);
        });
      }
      topologySnapshot.sessions.filter((row) => item.sessionKeys.has(row.key)).forEach((row) =>
        appendRelated(row.label, row.key));
      $("session-manager-state").textContent = item.kind === "account"
        ? "账号下归入角色的人格会自动处理已发现的私聊和群聊。"
        : item.kind === "persona" ? item.roleId ? "这个人格的会话随所属角色自动运行。" : "这个人格尚未归入角色。"
          : item.mapped ? "这个角色会自动接入所归属人格对应的私聊和群聊。" : "这些人格尚未归入角色。";
      return;
    }
    const row = item.raw;
    const account = topologySnapshot.accounts.find(entry => entry.key === item.accountKey);
    const selectedPersona = item.personaKeys.map(key =>
      topologySnapshot.personas.find(persona => persona.key === key)).find(Boolean);
    const persona = row.details_available === false ? "未确定人格"
      : personaLabel(selectedPersona || {personaId: row.persona_override || row.persona_id});
    const mappedRole = item.roleId
      ? topologySnapshot.roles.find(entry => entry.key === item.roleKey)?.name || roleDisplayName(item.roleId)
      : "未归入角色";
    $("session-manager-meta").textContent = `${platformLabel(item.platformName || item.raw?.platform_name || item.platform)} · ${accountChoiceLabel(account || {})} · ${item.label}`;
    const mappingState = !row.native ? "尚未建立 AstrBot 会话"
      : item.draft ? "角色映射尚未保存" : item.registered ? "已归入角色，随角色自动运行"
        : "AstrBot 已发现，等待账号下的人格归入角色";
    $("session-manager-state").textContent = `人格 ${persona} → 角色 ${mappedRole} · 来源：${personaSourceLabel(row)} · ${mappingState}`;
  }

  function renderGroupNetwork(row) {
    const panel = $("session-group-network");
    const content = $("session-group-network-content");
    const state = $("session-group-network-state");
    const counts = $("session-group-network-counts");
    if (!panel || !content || !state || !counts) return;
    const route = row?.scope_kind === "group" && row.group_id
      ? {platform_id: row.platform_id, account_id: row.account_id, bot_id: row.bot_id,
        group_id: row.group_id, roles: Array.isArray(row.roles) ? row.roles : []} : null;
    panel.hidden = !route;
    content.replaceChildren();
    counts.textContent = "";
    state.textContent = "";
    panel.dataset.sessionKey = route ? row.session_key : "";
    if (!route) return;
    if (!acquaintanceDirectory) {
      state.textContent = "正在读取成员关系与熟人卡…";
      loadAcquaintanceDirectory(row.session_key);
      return;
    }
    if (acquaintanceDirectory.error) {
      state.textContent = "成员目录暂不可读取。";
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "button button-quiet";
      retry.textContent = "重新读取";
      retry.addEventListener("click", () => {
        acquaintanceDirectory = null;
        renderGroupNetwork(row);
      });
      content.append(retry);
      return;
    }
    const roles = new Set(route.roles.map(role => String(role?.role_id || "")).filter(Boolean));
    const networks = (acquaintanceDirectory.group_networks || []).filter(network =>
      String(network.platform_id || "") === String(route.platform_id || "")
      && String(network.account_id || "") === String(route.account_id || "")
      && String(network.bot_id || "") === String(route.bot_id || "")
      && String(network.group_id || "") === String(route.group_id)
      && (!roles.size || roles.has(String(network.role_id || ""))));
    if (!networks.length) {
      state.textContent = "目录中还没有这个群的人际观察或熟人卡记录。";
      return;
    }
    state.textContent = "连线仅表示短时相邻接话；成员线索与熟人卡按各自来源展示。";
    const memberCount = new Set(networks.flatMap(network => (network.members || [])
      .map(member => String(member.native_account_id || "")).filter(Boolean))).size;
    const edgeCount = networks.reduce((total, network) => total + (network.edges || []).length, 0);
    const interactionCount = networks.reduce((total, network) => total + numberOf(network.interaction_count), 0);
    counts.textContent = `${memberCount} 人 · ${edgeCount} 条互动边 · ${interactionCount} 次接话`;
    networks.forEach((network) => {
      const section = document.createElement("section");
      section.className = "session-group-network-route";
      const members = Array.isArray(network.members) ? network.members : [];
      const edges = Array.isArray(network.edges) ? network.edges : [];
      const heading = document.createElement("h5");
      heading.textContent = network.role_name || "群关系网";
      const summary = document.createElement("div");
      summary.className = "group-network-metrics";
      for (const [label, value] of [["成员", `${numberOf(members.length)} 人`],
        ["接话边", `${numberOf(edges.length)} 条`], ["接话次数", `${numberOf(network.interaction_count)} 次`]]) {
        const metric = document.createElement("span");
        metric.textContent = `${label} ${value}`;
        summary.append(metric);
      }
      section.append(heading, summary);
      if (members.length) {
        const viewport = document.createElement("div");
        viewport.className = "session-group-network-viewport";
        viewport.append(groupMemberNetworkSvg(network, members));
        section.append(viewport);
      }
      if (members.length) section.append(groupMemberRows(members));
      if (edges.length) section.append(groupInteractionRows(network, members));
      if (network.unmatched_clues?.length) section.append(unmatchedGroupClues(network.unmatched_clues));
      content.append(section);
    });
  }

  function numberOf(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? Math.max(0, numeric) : 0;
  }

  function appendText(parent, tag, text, className = "") {
    const element = document.createElement(tag);
    element.textContent = String(text ?? "");
    if (className) element.className = className;
    parent.append(element);
    return element;
  }

  function groupMemberNetworkSvg(network, sourceMembers) {
    const members = sourceMembers.slice(0, 20);
    const columns = [members.filter((_, index) => index % 2 === 0), members.filter((_, index) => index % 2 === 1)];
    const positions = new Map();
    columns.forEach((items, column) => items.forEach((member, index) => positions.set(
      String(member.native_account_id), {x: column ? 452 : 28, y: 42 + index * 66, member})));
    const height = Math.max(144, 78 + Math.max(...columns.map(items => items.length), 0) * 66);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 684 ${height}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "群内成员互动图");
    const desc = document.createElementNS("http://www.w3.org/2000/svg", "desc");
    desc.textContent = "连线表示 180 秒内相邻成员发言的次数，不推断现实关系。";
    svg.append(desc);
    (network.edges || []).filter(edge => positions.has(String(edge.a)) && positions.has(String(edge.b)))
      .slice(0, 4).forEach(edge => {
        const a = positions.get(String(edge.a)), b = positions.get(String(edge.b));
        const first = a.x < b.x ? a : b, second = a.x < b.x ? b : a;
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("class", `session-group-edge tone-${edge.tone || "ordinary"}`);
        path.setAttribute("d", `M ${first.x + 204} ${first.y} C 296 ${first.y}, 384 ${second.y}, ${second.x} ${second.y}`);
        path.setAttribute("stroke-width", String(Math.min(5, 1 + Math.log2(Math.max(1, numberOf(edge.count))))));
        const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
        title.textContent = `${edge.summary || "近期连续接话"} · ${numberOf(edge.count)} 次 · ${shortGroupTime(edge.last_seen)}`;
        path.append(title);
        svg.append(path);
      });
    positions.forEach(({x, y, member}, account) => {
      const name = member.display_name || `成员 ${account}`;
      const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
      group.setAttribute("class", "session-group-node");
      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", String(x)); rect.setAttribute("y", String(y - 22));
      rect.setAttribute("width", "204"); rect.setAttribute("height", "44"); rect.setAttribute("rx", "4");
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("cx", String(x + 17)); circle.setAttribute("cy", String(y)); circle.setAttribute("r", "11");
      circle.setAttribute("fill", `hsl(${hueOf(account)} 42% 43%)`);
      const initial = document.createElementNS("http://www.w3.org/2000/svg", "text");
      initial.setAttribute("class", "session-group-initial"); initial.setAttribute("x", String(x + 17));
      initial.setAttribute("y", String(y + 4)); initial.textContent = Array.from(name)[0] || "？";
      const title = document.createElementNS("http://www.w3.org/2000/svg", "text");
      title.setAttribute("class", "session-group-name"); title.setAttribute("x", String(x + 34));
      title.setAttribute("y", String(y - 2)); title.textContent = graphLabel(name, 15);
      const meta = document.createElementNS("http://www.w3.org/2000/svg", "text");
      meta.setAttribute("class", "session-group-member-meta"); meta.setAttribute("x", String(x + 34));
      meta.setAttribute("y", String(y + 14));
      meta.textContent = `${numberOf(member.group_observation?.message_count)} 条 · 最近 ${shortGroupTime(member.group_observation?.last_seen)}`;
      group.append(rect, circle, initial, title, meta);
      svg.append(group);
    });
    return svg;
  }

  function hueOf(seed) {
    let value = 0;
    for (const char of String(seed)) value = (value * 31 + char.codePointAt(0)) % 360;
    return value;
  }

  function graphLabel(value, limit) {
    const chars = Array.from(String(value || ""));
    return chars.length > limit ? chars.slice(0, limit - 1).join("") + "…" : String(value || "");
  }

  function shortGroupTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isFinite(date.getTime())
      ? new Intl.DateTimeFormat("zh-CN", {month: "short", day: "numeric"}).format(date) : "—";
  }

  function groupMemberRows(members) {
    const section = document.createElement("section");
    section.className = "session-group-members";
    const heading = document.createElement("h6");
    heading.textContent = `成员与熟人卡 · ${members.length} 人`;
    const list = document.createElement("div");
    list.className = "session-group-member-list";
    members.forEach(member => {
      const row = document.createElement("article");
      row.className = "session-group-member";
      const identity = document.createElement("div");
      identity.className = "session-group-member-identity";
      appendText(identity, "strong", member.display_name || `成员 ${member.native_account_id}`);
      appendText(identity, "small", `账号 ${member.native_account_id} · ${member.group_observation?.basis === "acquaintance_card" ? "熟人卡群内记录" : "关系网观察"} ${numberOf(member.group_observation?.message_count)} 条 · ${numberOf(member.group_observation?.active_days)} 天`);
      const card = member.acquaintance || {};
      const groupCard = card.group || {};
      const cardLabel = `熟人卡 ${numberOf(card.message_count)} 条${card.private_chat ? " · 有私聊" : ""} · 本群 ${numberOf(groupCard.message_count)} 条`;
      appendText(identity, "small", cardLabel, "session-group-card-meta");
      if (member.clues?.length) {
        const clues = member.clues.map(clue => `${clue.name || "未命名线索"} · ${clue.basis === "declared_account" ? "账号声明" : "名称唯一匹配"}`);
        appendText(identity, "small", `成员线索：${clues.join("；")}`, "session-group-clue-meta");
      }
      row.append(identity);
      list.append(row);
    });
    section.append(heading, list);
    return section;
  }

  function groupInteractionRows(network, members) {
    const names = new Map(members.map(member => [String(member.native_account_id), member.display_name || `成员 ${member.native_account_id}`]));
    const section = document.createElement("section");
    section.className = "session-group-interactions";
    appendText(section, "h6", `近期互动 · ${(network.edges || []).length} 条边`);
    const list = document.createElement("div");
    list.className = "session-group-interaction-list";
    (network.edges || []).slice(0, 80).forEach(edge => {
      const row = document.createElement("div");
      row.className = `session-group-interaction tone-${edge.tone || "ordinary"}`;
      appendText(row, "strong", `${names.get(String(edge.a)) || edge.a} ↔ ${names.get(String(edge.b)) || edge.b}`);
      const tone = {ordinary: "接话", banter: "玩笑", tension: "紧绷"}[edge.tone] || "接话";
      appendText(row, "small", `${numberOf(edge.count)} 次 · ${tone} · 最近 ${shortGroupTime(edge.last_seen)}`);
      list.append(row);
    });
    section.append(list);
    return section;
  }

  function unmatchedGroupClues(clues) {
    const section = document.createElement("section");
    section.className = "session-group-unmatched";
    appendText(section, "h6", `待确认成员线索 · ${clues.length} 条`);
    clues.slice(0, 32).forEach(clue => {
      const row = document.createElement("article");
      row.className = "session-group-unmatched-row";
      appendText(row, "strong", clue.name || "未命名线索");
      const explanation = clue.basis === "ambiguous_display_label"
        ? `名称对应多位成员：${(clue.candidate_account_ids || []).join("、")}` : "尚未对应到群内账号";
      const declared = (clue.declared_account_ids || []).join("、");
      appendText(row, "small", explanation + (clue.basis === "unmatched" && declared ? ` · 线索声明账号 ${declared}` : ""));
      if (clue.content) appendText(row, "p", clue.content);
      section.append(row);
    });
    return section;
  }

  async function loadAcquaintanceDirectory(sessionKey) {
    if (!acquaintanceRead) {
      acquaintanceRead = api("GET", "acquaintance").then(value => {
        acquaintanceDirectory = value && typeof value === "object" ? value : {group_networks: []};
      }).catch(() => {
        acquaintanceDirectory = {error: true};
      }).finally(() => { acquaintanceRead = null; });
    }
    await acquaintanceRead;
    if (topologySelection !== `session:${sessionKey}`) return;
    renderSessionManager();
  }

  function codeOf(value) {
    return value?.code || value?.data?.code || value?.response?.data?.code || "";
  }
  function messageOf(value) {
    return [value?.message, value?.reason, value?.response?.message, value?.detail]
      .find((item) => typeof item === "string" && item) || "";
  }
  function unwrap(value) {
    let current = value;
    for (let depth = 0; depth < 3; depth += 1) {
      if (current?.status === "error") {
        const error = new Error(labelFor(current?.data?.code) || messageOf(current) || "请求未完成。请刷新状态后重试。");
        error.code = codeOf(current) || current.message || "";
        error.reason = current.reason || "";
        throw error;
      }
      if (current?.status === "ok" && Object.hasOwn(current, "data")) {
        current = current.data;
        continue;
      }
      return current;
    }
    return current;
  }
  function isMissingRoute(error) {
    if (["dashboard_identity_required", "core_access_required"].includes(error?.code)) return false;
    const message = messageOf(error) || error?.message || "";
    return /未找到该路由|route (?:was )?not found|no matching route/i.test(message);
  }
  async function api(method, endpoint, body = {}, timeoutMs = 30000) {
    if (!bridge) throw new Error("AstrBot 页面桥尚未就绪，请从已启用插件的管理页面打开本页。");
    let timer;
    const pending = method === "GET" ? bridge.apiGet(endpoint, {}) : bridge.apiPost(endpoint, body);
    const timeout = new Promise((_, reject) => {
      timer = window.setTimeout(() => reject(Object.assign(
        new Error("请求未及时返回，操作结果暂时无法确认。"), {code: "host_operation_result_unknown"}
      )), timeoutMs);
    });
    let response;
    try { response = await Promise.race([pending, timeout]); }
    finally { window.clearTimeout(timer); }
    return unwrap(response);
  }
  function setBusy(value) {
    busy = value;
    document.querySelectorAll("button").forEach((button) => {
      button.disabled = value || button.dataset.disabled === "true";
    });
    document.querySelector(".status-panel").setAttribute("aria-busy", String(value));
    updateControls();
    updateBindingCheck();
    updateGroupTargetControls();
    renderSessionManager();
  }

  function setLoading(button, value) {
    if (!button) return;
    button.classList.toggle("is-loading", value);
    button.setAttribute("aria-busy", String(value));
  }

  function paintStatusValue(id, value) {
    const element = $(id);
    if (!element) return;
    const next = String(value ?? "");
    if (element.textContent === next) return;
    element.textContent = next;
    element.classList.remove("is-updated");
    void element.offsetWidth;
    element.classList.add("is-updated");
  }
  function notice(text, kind = "info") {
    const element = $("notice");
    element.textContent = text;
    element.dataset.kind = kind;
    element.hidden = !text;
  }
  function stateLabel(value) {
    if (value === "uninitialized" && isEnabled()) {
      return hasSavedRoleMappings() ? "角色已映射，等待准备" : "等待角色映射";
    }
    return ({ running: "运行中", new: "正在准备", stopped: "已停止", uninitialized: "等待角色映射", unloaded: "正在准备" })[value] || "状态待核对";
  }
  function hasSavedRoleMappings() {
    return (status?.targets || []).some(target => Array.isArray(target?.roles) && target.roles.length > 0);
  }
  function modelProviderName(providerId) {
    const config = modelConfig || {};
    return config.providers?.find(item => item.id === providerId)?.name || providerId || "未选择";
  }
  function renderSharedModel() {
    const select = $("shared-model-provider"), save = $("save-shared-model"), note = $("shared-model-note");
    if (!select || !save || !note) return;
    if (!modelConfig) {
      select.innerHTML = `<option value="">正在读取模型连接…</option>`;
      select.disabled = true; save.disabled = true;
      note.textContent = "正在读取已加载的模型连接…";
      note.dataset.kind = "info";
      return;
    }
    const providers = Array.isArray(modelConfig?.providers) ? modelConfig.providers : [];
    const current = String(modelConfig?.provider_id || "");
    const configured = String(modelConfig?.configured_provider_id || "");
    const source = String(modelConfig?.provider_source || "");
    const previous = select.value;
    select.innerHTML = `<option value="">请选择已加载的文本模型</option>${providers.map(provider => {
      const name = String(provider.name || "");
      const label = name && name !== provider.id
        ? `${escapeHTML(name)}（${escapeHTML(provider.id)}）` : escapeHTML(provider.id);
      return `<option value="${escapeHTML(provider.id)}">${label}</option>`;
    }).join("")}`;
    const desired = providers.some(item => item.id === previous) ? previous
      : providers.some(item => item.id === current) ? current : "";
    select.value = desired;
    select.disabled = busy || modelSaving || !providers.length;
    save.disabled = busy || modelSaving || !providers.length || !select.value;
    save.textContent = source === "auto" ? "固定启动模型" : "保存启动模型";
    if (!providers.length) {
      note.textContent = "AstrBot 没有读取到已加载的文本模型。请先在 AstrBot 模型配置中添加并启用一个 Provider。";
      note.dataset.kind = "error";
    } else if (!current) {
      note.textContent = "尚未选择插件启动模型。先在这里保存，角色映射才能准备启动；启动后再到「模型与连接」细调任务路由。";
      note.dataset.kind = "info";
    } else if (source === "fallback") {
      note.textContent = "已保存的启动模型不可用，已自动改用 AstrBot 当前模型。";
      note.dataset.kind = "info";
    } else if (source === "auto") {
      note.textContent = "已自动跟随 AstrBot 当前模型；无需手动保存，可直接准备并启动。";
      note.dataset.kind = "info";
    } else if (!providers.some(item => item.id === current)) {
      note.textContent = `已保存的启动模型 ${current} 当前不可用，请重新选择。`;
      note.dataset.kind = "error";
    } else {
      const running = isAccepting() || status?.state === "running";
      note.textContent = running
        ? `当前启动模型：${modelProviderName(current)}。宿主已在运行，保存后将在下次启动或重载时生效。`
        : `当前启动模型：${modelProviderName(current)}。保存角色映射后即可点「准备并启动」。`;
      note.dataset.kind = "info";
    }
  }
  async function saveSharedModel() {
    if (busy || modelSaving) return;
    const select = $("shared-model-provider"), button = $("save-shared-model");
    const providerId = String(select?.value || "");
    if (!providerId) { notice("请先选择一个已加载的文本模型。", "error"); return; }
    modelSaving = true;
    setLoading(button, true);
    setBusy(true);
    notice("正在保存共享模型连接…", "info");
    try {
      const result = await api("POST", `${accountApi}/model-provider`, {provider_id: providerId}, 60000);
      if (result?.model) modelConfig = result.model;
      let refreshed = true;
      try { await readStatus(); } catch { refreshed = false; }
      if (!refreshed) {
        notice("共享模型已保存；运行状态暂未读到，请点「刷新状态」核对后再准备启动。", "info");
      } else if (result?.prepared) {
        notice("共享模型已保存，角色运行环境已准备。现在可以点「准备并启动」。", "success");
      } else if (result?.initialization_error) {
        notice(`共享模型已保存；角色运行环境尚未就绪：${failureMessage(result.initialization_error)}。请先完成角色映射，再点「准备并启动」。`, "info");
      } else {
        notice("共享模型已保存。请点「准备并启动」完成角色准备。", "success");
      }
    } catch (error) {
      notice(`共享模型保存未完成：${publicError(error)}`, "error");
    } finally {
      modelSaving = false;
      setBusy(false);
      setLoading(button, false);
      renderSharedModel();
    }
  }
  function isAccepting() { return Boolean(status?.accepting); }
  function isEnabled() {
    if (!status || !host) return false;
    return Boolean(status.enabled && !status.settings_error);
  }
  function editorRow(fields, values, remove) {
    const row = document.createElement("div"); row.className = "binding-row";
    fields.forEach(([key, label, placeholder]) => { const input = document.createElement("input"); input.dataset.field = key; input.placeholder = placeholder || label; input.value = values?.[key] || ""; input.setAttribute("aria-label", label); row.append(input); });
    const button = document.createElement("button"); button.type = "button"; button.className = "button button-quiet binding-remove"; button.textContent = "移除"; button.addEventListener("click", () => { row.remove(); markBindingsDirty(); }); row.append(button); return row;
  }
  const bindingAccountKey = (row) => `${row.platform_id}|${row.account_id}|${row.bot_id}`;
  function personaHasNativeWindow(persona, account) {
    const targets = bindingConfig?.discovered?.targets || [];
    const personaId = String(persona?.persona_id ?? "");
    if (!personaId) return false;
    return targets.some(target => target?.native && String(target.bot_id || "") === String(persona.bot_id || "")
      && (!account || (target.platform_id === account.platform_id && target.account_id === account.account_id))
      && String(target.persona_override || target.persona_id || "") === personaId);
  }

  function roleBindingCard(account, roleId = "", mappings = []) {
    const botId = String(account.bot_id || "");
    const card = document.createElement("section");
    card.className = "role-binding-card";
    card.dataset.botId = botId;
    const heading = document.createElement("div");
    heading.className = "role-binding-heading";
    const input = document.createElement("input");
    input.type = "text";
    input.maxLength = 128;
    input.dataset.roleId = "";
    input.value = roleId;
    input.placeholder = "角色名";
    input.setAttribute("aria-label", "角色名");
    input.title = roleId;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "button button-quiet binding-remove";
    remove.textContent = "移除角色";
    remove.addEventListener("click", () => {
      const owner = card.closest(".account-binding-card");
      card.remove(); markBindingsDirty(owner);
    });
    const personaLabels = mappings.map(row => {
      const native = (bindingConfig?.discovered?.personas || []).find(choice =>
        choice.bot_id === row.bot_id && choice.persona_id === row.persona_id);
      return native?.label || row.persona_id;
    });
    const displayName = roleDisplayName(roleId, personaLabels);
    if (roleId && displayName !== roleId) {
      const identity = document.createElement("div");
      identity.className = "role-binding-identity";
      const name = document.createElement("strong");
      name.textContent = displayName;
      const hint = document.createElement("small");
      hint.textContent = "内部标识 · 保留以继续关联已有资料";
      input.setAttribute("aria-label", "内部角色标识");
      identity.append(name, input, hint);
      heading.append(identity, remove);
    } else heading.append(input, remove);
    const choices = document.createElement("div");
    choices.className = "role-persona-choices";
    const selected = new Set(mappings.map(row => nativePersonaKey(row.bot_id, row.persona_id)));
    const personas = [...new Map([...(bindingConfig?.discovered?.personas || []), ...mappings]
      .filter(persona => String(persona.bot_id || "") === botId)
      .map(persona => [nativePersonaKey(persona.bot_id, persona.persona_id), persona])).values()];
    personas.forEach(persona => {
      const label = document.createElement("label");
      label.className = "role-persona-choice";
      label.dataset.linked = String(personaHasNativeWindow(persona, account));
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.dataset.personaChoice = "";
      checkbox.dataset.botId = persona.bot_id;
      checkbox.dataset.personaId = persona.persona_id;
      checkbox.checked = selected.has(nativePersonaKey(persona.bot_id, persona.persona_id));
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          card.closest(".account-binding-card").querySelectorAll("[data-persona-choice]:checked").forEach(other => {
            if (other !== checkbox && other.dataset.botId === checkbox.dataset.botId
                && other.dataset.personaId === checkbox.dataset.personaId) other.checked = false;
          });
        }
      });
      const text = document.createElement("span");
      text.textContent = personaName(persona.label || persona.persona_id);
      label.append(checkbox, text);
      choices.append(label);
    });
    const empty = document.createElement("p");
    empty.className = "role-persona-empty";
    empty.textContent = "没有已加载的 AstrBot 人格";
    empty.hidden = personas.length > 0;
    choices.append(empty);
    card.append(heading, choices);
    return card;
  }
  function appendRoleToAccount(account, roleId = "") {
    if (!account) return;
    account.querySelector(".account-role-list").append(roleBindingCard(account._account, roleId, []));
    markBindingsDirty(account);
  }

  function accountBindingCard(account, mappings) {
    const card = document.createElement("section");
    card.className = "account-binding-card";
    card.dataset.accountBot = String(account.bot_id || "");
    card.dataset.accountKey = bindingAccountKey(account);
    card._account = account;
    const heading = document.createElement("div");
    heading.className = "account-binding-heading";
    const title = document.createElement("strong");
    title.textContent = accountChoiceLabel(account);
    const meta = document.createElement("small");
    meta.textContent = `${platformLabel(account.platform_name || account.platform_id)} · Bot ${account.bot_id || "未标记"}`;
    const add = document.createElement("button");
    add.type = "button";
    add.className = "button button-quiet";
    add.textContent = "添加角色";
    add.addEventListener("click", () => appendRoleToAccount(card));
    heading.append(title, meta, add);
    const roles = document.createElement("div");
    roles.className = "account-role-list";
    const roleGroups = new Map();
    mappings.forEach(row => {
      if (!roleGroups.has(row.role_id)) roleGroups.set(row.role_id, []);
      roleGroups.get(row.role_id).push(row);
    });
    if (!roleGroups.size) roles.append(roleBindingCard(account, "", []));
    else [...roleGroups].forEach(([roleId, rows]) => roles.append(roleBindingCard(account, roleId, rows)));
    card.append(heading, roles);
    return card;
  }

  function renderAccountEditor(data) {
    const root = $("edit-accounts");
    if (!root) return;
    const accounts = data.discovered?.accounts || data.accounts || [];
    const mappings = data.personas || [];
    const byAccount = new Map();
    accounts.forEach(account => {
      const key = bindingAccountKey(account);
      if (!byAccount.has(key)) byAccount.set(key, account);
    });
    const mappedBots = new Set(mappings.map(row => String(row.bot_id || "")));
    root.replaceChildren(...[...byAccount.values()].map(account => accountBindingCard(account,
      mappings.filter(row => String(row.bot_id || "") === String(account.bot_id || "")))));
    if (!root.children.length) {
      const empty = document.createElement("p");
      empty.className = "role-persona-empty";
      empty.textContent = "当前没有连接的 Bot 账号";
      root.append(empty);
    }
    if (mappedBots.size && ![...byAccount.values()].some(account => mappedBots.has(String(account.bot_id || "")))) {
      const empty = document.createElement("p");
      empty.className = "role-persona-empty";
      empty.textContent = "已保存的人格所属账号当前未连接";
      root.append(empty);
    }
    refreshPersonaVisibility();
  }
  function refreshPersonaVisibility() {
    const hide = Boolean($("hide-unlinked-personas")?.checked);
    const assigned = new Set([...$("edit-accounts").querySelectorAll("[data-persona-choice]:checked")]
      .map(input => nativePersonaKey(input.dataset.botId, input.dataset.personaId)));
    $("edit-accounts").querySelectorAll(".role-persona-choices").forEach(choices => {
      const account = choices.closest(".account-binding-card")._account;
      const labels = [...choices.querySelectorAll(".role-persona-choice")];
      labels.forEach(label => {
        const input = label.querySelector("[data-persona-choice]");
        const linked = personaHasNativeWindow({bot_id: input.dataset.botId, persona_id: input.dataset.personaId}, account);
        label.hidden = hide && !linked && !assigned.has(nativePersonaKey(input.dataset.botId, input.dataset.personaId));
      });
      const empty = choices.querySelector(".role-persona-empty");
      empty.hidden = labels.some(label => !label.hidden);
      empty.textContent = labels.length ? "没有关联此账号窗口的人格" : "没有已加载的 AstrBot 人格";
    });
  }
  function syncSharedAccountDraft(source) {
    if (!source) return;
    const drafts = [...source.querySelectorAll(".role-binding-card")].map(card => ({
      name: card.querySelector("[data-role-id]").value,
      mappings: [...card.querySelectorAll("[data-persona-choice]:checked")].map(input => ({
        bot_id: input.dataset.botId, persona_id: input.dataset.personaId,
      })),
    }));
    $("edit-accounts").querySelectorAll(".account-binding-card").forEach(account => {
      if (account === source || account.dataset.accountBot !== source.dataset.accountBot) return;
      account.querySelector(".account-role-list").replaceChildren(...drafts.map(draft =>
        roleBindingCard(account._account, draft.name, draft.mappings)));
    });
  }
  function renderBindingEditor(data) {
    if (!data) return;
    bindingConfig = data;
    if (data.model) modelConfig = data.model;
    const discovered = data.discovered || {}, accounts = discovered.accounts || data.accounts || [];
    const accountByKey = new Map(accounts.map(x => [`${x.platform_id}|${x.account_id}|${x.bot_id}`, x]));
    bindingConfig._accountByKey = accountByKey;
    if (!bindingsDirty) {
      renderAccountEditor(data);
    }
    const nativeCount = discovered.targets?.filter(row => row.native).length || 0;
    const directory = discovered.directory || {};
    const issues = Array.isArray(directory.issues) ? directory.issues : [];
    const stageLabels = {session_directory: "会话目录", conversation_id: "会话编号",
      conversation: "会话人格绑定", session_settings: "会话设置", persona: "会话人格",
      conversation_verify: "会话校验", details: "会话详情"};
    const failedStages = [...new Set(issues.map(item => stageLabels[item.stage] || item.stage || "详情"))];
    const directoryState = $("directory-state");
    directoryState.textContent = `${nativeCount} 个 AstrBot 会话${issues.length
      ? ` · 部分读取：${failedStages.join("、")}` : ""}`;
    directoryState.title = issues.map(item => `${item.code || "session_details_unavailable"}：${item.reason || "读取未完成"}`)
      .join("\n");
    updateBindingCheck();
    renderGroupTargets();
    renderTopology();
    renderSharedModel();
  }

  const groupRouteKey = row => JSON.stringify([row.platform_id || "", row.account_id || "",
    row.bot_id || "", row.group_id || ""]);
  function groupTargetRows() {
    const discovered = bindingConfig?.discovered || {};
    const accounts = discovered.accounts || bindingConfig?.accounts || [];
    const rows = new Map();
    const add = (target, account, native) => {
      const name = groupDisplayName(target);
      const row = {platform_id: account?.platform_id || target.platform_id || "",
        account_id: account?.account_id || target.account_id || "", bot_id: account?.bot_id || target.bot_id || "",
        group_id: String(target.group_id || ""), display_name: name, group_name: name, account, native};
      if (!row.group_id || row.group_id === "*") return;
      const key = groupRouteKey(row);
      const previous = rows.get(key);
      const merged = {...previous, ...row, native: Boolean(previous?.native || native)};
      const previousName = groupDisplayName(previous);
      const resolvedName = name || previousName;
      if (resolvedName) merged.display_name = merged.group_name = resolvedName;
      rows.set(key, merged);
    };
    for (const target of discovered.targets || []) {
      if (!(target.scope_kind === "group" || target.group_id) || target.native !== true || !target.group_id
          || target.group_id === "*") continue;
      const candidates = accounts.filter(account => account.platform_id === target.platform_id
        && (!target.account_id || account.account_id === target.account_id)
        && (!target.bot_id || account.bot_id === target.bot_id));
      if (candidates.length) candidates.forEach(account => add(target, account, true));
      else add(target, null, true);
    }
    for (const target of bindingConfig?.groups || []) {
      if (target.group_id === "*") continue;
      const account = accounts.find(row => row.platform_id === target.platform_id
        && row.account_id === target.account_id && row.bot_id === target.bot_id);
      add(target, account, false);
    }
    return [...rows.values()].sort((left, right) =>
      left.platform_id.localeCompare(right.platform_id) || left.account_id.localeCompare(right.account_id)
      || left.group_id.localeCompare(right.group_id, undefined, {numeric: true}));
  }
  function selectedGroupTargets() {
    const explicit = [...$("group-target-list").querySelectorAll("[data-group-target-choice]:checked")]
      .map(input => ({platform_id: input.dataset.platformId, account_id: input.dataset.accountId,
        bot_id: input.dataset.botId, group_id: input.dataset.groupId}));
    const wildcard = [...$("group-target-list").querySelectorAll("[data-group-wildcard-choice]:checked")]
      .map(input => ({platform_id: input.dataset.platformId, account_id: input.dataset.accountId,
        bot_id: input.dataset.botId, group_id: "*"}));
    return [...wildcard, ...explicit];
  }
  const sameGroupTargets = (left, right) => JSON.stringify(left.map(groupRouteKey).sort())
    === JSON.stringify(right.map(groupRouteKey).sort());
  function markGroupTargetsDirty() {
    const selected = selectedGroupTargets();
    groupTargetsDirty = !sameGroupTargets(selected, bindingConfig?.groups || []);
    groupTargetDraft = groupTargetsDirty ? selected : null;
    updateGroupTargetControls();
  }
  function updateGroupTargetControls() {
    const inputs = [...$("group-target-list").querySelectorAll(
      "[data-group-target-choice],[data-group-wildcard-choice]")];
    const selected = selectedGroupTargets();
    const current = (bindingConfig?.groups || []).map(row => ({platform_id: row.platform_id,
      account_id: row.account_id, bot_id: row.bot_id, group_id: row.group_id}));
    const changed = !sameGroupTargets(selected, current);
    const wildcard = selected.some(row => row.group_id === "*");
    const unresolvedSelection = inputs.some(input => input.checked && input.dataset.routeReady !== "true");
    $("group-target-count").textContent = `${selected.length} / ${inputs.length} 个已选`;
    $("group-target-state").textContent = bindingsDirty
      ? "先保存角色映射，再修改群目标。"
      : groupTargetsDirty ? "群目标选择尚未保存。"
        : wildcard ? "当前包含通配群目标：该账号下所有群都会接入 V2。"
        : selected.length ? `${selected.length} 个群目标已启用。` : "当前没有启用群目标。";
    $("save-group-targets").disabled = busy || bindingsDirty || !changed || unresolvedSelection;
    $("save-group-targets").textContent = `保存群目标${selected.length ? `（${selected.length}）` : ""}`;
    for (const input of inputs) input.disabled = busy || bindingsDirty || input.dataset.routeReady !== "true";
  }
  function renderGroupTargets() {
    const panel = $("group-target-panel");
    const list = $("group-target-list");
    if (!panel || !list) return;
    const configured = bindingConfig?.groups || [];
    const rows = groupTargetRows();
    const wildcardRows = (bindingConfig?.accounts || []).map(account => ({
      platform_id: account.platform_id, account_id: account.account_id, bot_id: account.bot_id,
      group_id: "*", display_name: "该账号下所有群", group_name: "该账号下所有群",
      account, native: false, wildcard: true,
    })).filter(row => row.platform_id && row.account_id && row.bot_id);
    if (groupTargetsDirty) {
      const keys = new Set(rows.map(groupRouteKey));
      for (const row of groupTargetDraft || []) {
        if (row.group_id !== "*" && !keys.has(groupRouteKey(row))) rows.push({...row, native: false});
      }
    }
    panel.hidden = !rows.length && !wildcardRows.length && !configured.length;
    if (panel.hidden) return;
    const selectedRows = groupTargetsDirty ? groupTargetDraft || [] : configured;
    const selected = new Set(selectedRows.filter(row => row.group_id !== "*").map(groupRouteKey));
    const wildcardSelected = new Set(selectedRows.filter(row => row.group_id === "*").map(groupRouteKey));
    $("group-target-note").textContent = wildcardRows.length
      ? "勾选「该账号下所有群」后，这个账号下新出现的群也会自动接入；勾选明确群目标会取消同一账号的通配。"
      : rows.length ? "未勾选的群不会接入 V2 群聊。" : "没有已发现的群；保存空列表可移除现有群目标。";
    const children = [];
    for (const row of wildcardRows) {
      const label = document.createElement("label");
      label.className = "group-target-row group-target-wildcard";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.dataset.groupWildcardChoice = "";
      input.dataset.platformId = row.platform_id;
      input.dataset.accountId = row.account_id;
      input.dataset.botId = row.bot_id;
      input.dataset.groupId = "*";
      input.dataset.routeReady = "true";
      input.checked = wildcardSelected.has(groupRouteKey(row));
      const copy = document.createElement("span");
      copy.className = "group-target-copy";
      appendText(copy, "strong", "该账号下所有群（通配）");
      appendText(copy, "small", `${row.account ? accountChoiceLabel(row.account) : "账号待确认"} · 含后续新建或改名的群`);
      input.addEventListener("change", () => {
        if (input.checked) {
          for (const peer of list.querySelectorAll("[data-group-target-choice]")) {
            if (peer.dataset.platformId === input.dataset.platformId
                && peer.dataset.accountId === input.dataset.accountId
                && peer.dataset.botId === input.dataset.botId) peer.checked = false;
          }
        }
        markGroupTargetsDirty();
      });
      label.append(input, copy);
      children.push(label);
    }
    if (!rows.length && !wildcardRows.length) {
      const empty = document.createElement("p");
      empty.className = "group-target-empty";
      empty.textContent = "当前没有可选群会话。";
      children.push(empty);
    } else {
      for (const row of rows) {
        const label = document.createElement("label");
        label.className = "group-target-row";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.dataset.groupTargetChoice = "";
        input.dataset.platformId = row.platform_id;
        input.dataset.accountId = row.account_id;
        input.dataset.botId = row.bot_id;
        input.dataset.groupId = row.group_id;
        input.dataset.routeReady = String(Boolean(row.platform_id && row.account_id && row.bot_id));
        input.checked = selected.has(groupRouteKey(row));
        const copy = document.createElement("span");
        copy.className = "group-target-copy";
        const name = groupDisplayName(row);
        appendText(copy, "strong", name ? `群 ${name}` : `群 ${row.group_id}`);
        appendText(copy, "small", `${name ? `群号 ${row.group_id} · ` : ""}${row.account ? accountChoiceLabel(row.account) : "账号待确认"} · ${row.native ? "已发现" : "当前已启用，暂未发现"}`);
        input.addEventListener("change", () => {
          if (input.checked) {
            for (const wildcard of list.querySelectorAll("[data-group-wildcard-choice]")) {
              if (wildcard.dataset.platformId === input.dataset.platformId
                  && wildcard.dataset.accountId === input.dataset.accountId
                  && wildcard.dataset.botId === input.dataset.botId) wildcard.checked = false;
            }
          }
          markGroupTargetsDirty();
        });
        label.append(input, copy);
        children.push(label);
      }
    }
    list.replaceChildren(...children);
    updateGroupTargetControls();
  }
  async function saveGroupTargets() {
    if (!bindingConfig || bindingsDirty) {
      notice("先保存角色映射，再修改群目标。", "info");
      return;
    }
    const groups = selectedGroupTargets();
    const accounts = (bindingConfig.accounts || []).map(row => ({platform_id: row.platform_id,
      account_id: row.account_id, bot_id: row.bot_id}));
    const personas = activeRoleAssignments();
    setBusy(true);
    try {
      const result = await api("POST", accountApi, {enabled: true, accounts, personas, groups});
      if (result.configuration) {
        if (result.configuration.enabled === true && sameGroupTargets(result.configuration.groups || [], groups)) {
          groupTargetsDirty = false;
          groupTargetDraft = null;
        }
        renderBindingEditor(result.configuration);
      }
      try { await readStatus(); }
      catch { notice("群目标已保存；运行状态暂未读到，可重新读取。", "info"); return; }
      const saved = (bindingConfig?.groups || []).map(groupRouteKey).sort();
      const expected = groups.map(groupRouteKey).sort();
      if (bindingConfig?.enabled !== true || JSON.stringify(saved) !== JSON.stringify(expected)) {
        notice("群目标保存后与所选清单不一致，请刷新并核对。", "error");
        return;
      }
      groupTargetsDirty = false;
      groupTargetDraft = null;
      renderGroupTargets();
      notice(groups.some(row => row.group_id === "*")
        ? "群目标已保存：该账号下所有群都会接入 V2。"
        : `群目标已保存并启用 ${groups.length} 个群。`, "success");
    } catch (error) {
      notice(`群目标保存未完成：${publicError(error)}`, "error");
    } finally {
      setBusy(false);
      updateGroupTargetControls();
    }
  }
  function updateBindingCheck() {
    const accountsByBot = new Map([...$("edit-accounts").querySelectorAll(".account-binding-card")]
      .map(card => [card.dataset.accountBot, card]));
    const roleCards = [...accountsByBot.values()].flatMap(account => [...account.querySelectorAll(".role-binding-card")])
      .filter(card => card.querySelector("[data-role-id]").value.trim()
        || card.querySelectorAll("[data-persona-choice]:checked").length);
    const mappings = activeRoleAssignments();
    const accounts = mappedAccounts();
    const accountBots = new Set(accounts.map(row => row.bot_id));
    const personaBots = new Set(mappings.map(row => row.bot_id));
    const personaKeys = mappings.map(row => nativePersonaKey(row.bot_id, row.persona_id));
    const roleIds = roleCards.map(card => card.querySelector("[data-role-id]").value.trim()).filter(Boolean);
    const cardAssignmentsValid = roleCards.every(card => {
      const roleId = card.querySelector("[data-role-id]").value.trim();
      const checked = card.querySelectorAll("[data-persona-choice]:checked").length;
      return checked > 0 && roleId.length > 0 && roleId.length <= 128;
    });
    const accountsValid = accountBots.size > 0;
    const mappingsValid = mappings.length > 0 && cardAssignmentsValid
      && new Set(personaKeys).size === personaKeys.length
      && new Set(roleCards.map(card => `${card.dataset.botId}\u0000${card.querySelector("[data-role-id]").value.trim()}`)).size === roleCards.length
      && accountBots.size === personaBots.size && [...accountBots].every(botId => personaBots.has(botId));
    const valid = accountsValid && mappingsValid;
    $("binding-check").textContent = valid ? `${roleIds.length} 个角色 · ${mappings.length} 个人格${bindingsDirty ? " · 尚未保存" : ""}`
      : !mappingsValid ? "请将 AstrBot 人格归入唯一角色" : "请先读取已连接的 Bot 账号";
    $("save-bindings").disabled = !valid || busy;
  }
  async function readBindingConfig() {
    try {
      directoryRead ||= api("GET", accountApi).finally(() => { directoryRead = null; });
      renderBindingEditor(await directoryRead);
    }
    catch (error) { $("directory-state").textContent = publicError(error); renderTopology(); }
  }
  function markBindingsDirty(source) {
    bindingsDirty = true;
    syncSharedAccountDraft(source);
    refreshPersonaVisibility();
    updateBindingCheck(); updateGroupTargetControls(); renderTopology();
  }
  async function saveBindingConfig() {
    const accounts = mappedAccounts();
    const personas = activeRoleAssignments();
    setBusy(true);
    try {
      const result = await api("POST", accountApi, {enabled: true, accounts,
        personas, groups: bindingConfig?.groups || []});
      bindingsDirty = false;
      if (result.configuration) renderBindingEditor(result.configuration);
      acquaintanceDirectory = null;
      window.parent?.postMessage?.({channel: "companion-workspace", kind: "host-mappings-updated"}, "*");
      try { await readStatus(); }
      catch { notice("角色映射已保存；运行状态暂未读到，可重新读取。", "info"); return; }
      if (result.startup_error) {
        const modelPending = ["host_provider_required", "host_provider_unavailable"].includes(result.startup_error);
        const message = modelPending
          ? "角色映射已经保存。共享启动模型会自动跟随 AstrBot 当前模型；如无可用模型，请先在 AstrBot 模型配置中启用 Provider，再点「准备并启动」。"
          : `运行未启动：${failureMessage(result.startup_error)}`;
        notice(`角色映射已保存。${message}`, modelPending ? "info" : "error");
        return;
      }
      if (result.requires_initialize) {
        notice("角色映射已保存；点「准备并启动」完成角色准备。", "info");
        return;
      }
      notice(result.started ? "角色映射已保存并自动加载。" : "角色映射已保存，运行状态已更新。", "success");
    } catch (error) { notice(`保存未完成：${publicError(error)}`, "error"); }
    finally { setBusy(false); updateBindingCheck(); }
  }

  function renderStatus() {
    if (!status || !host) return;
    $("connection").textContent = "插件连接正常";
    $("connection").dataset.state = "ready";
    $("status-heading").textContent = isEnabled() ? "配置已加载" : "插件未启用或配置无效";
    paintStatusValue("profile", "陪伴运行范围");
    paintStatusValue("runtime-state", stateLabel(status.state || status.session_state || (status.configured ? "new" : "unloaded")));
    paintStatusValue("accepting", isAccepting() ? "正在运行" : "未运行");
    $("safety-note").textContent = isAccepting()
      ? "已归入角色的人格自动处理其会话；主动消息按各窗口权限发送。"
      : hasSavedRoleMappings() ? "角色映射已保存；准备并启动后开始处理对应会话。"
        : "保存角色映射后，插件会自动准备并运行对应窗口。";
    $("safety-note").hidden = !$("safety-note").textContent;
    if (status.last_failure?.code || status.settings_error) {
      const code = status.last_failure?.code || status.settings_error;
      const operation = status.last_failure?.code ? failureOperations[status.last_failure.operation] : "";
      notice((operation ? operation + "：" : "") + failureMessage(code), "error");
    } else {
      notice("");
    }
    updateControls();
  }
  function updateControls() {
    $("refresh").disabled = busy;
    const hasProvider = typeof modelConfig !== "undefined" && Boolean(modelConfig?.provider_id);
    const start = $("start-host");
    if (start) {
      start.hidden = !isEnabled() || isAccepting();
      start.disabled = busy || start.hidden || !hasProvider;
      start.title = hasProvider ? "" : "AstrBot 暂无可用的文本模型";
    }
    if (typeof renderSharedModel === "function") renderSharedModel();
  }
  async function startHost() {
    if (busy || !host || !isEnabled() || isAccepting()) return;
    const button = $("start-host");
    setLoading(button, true);
    setBusy(true);
    try {
      const result = await api("POST", `${host.prefix}/start`, {}, 120000);
      await readStatus();
      if (isAccepting() || result?.state === "running") {
        notice("角色已准备并开始接收消息。", "success");
      } else if (status?.last_failure?.code || status?.settings_error) {
        notice(failureMessage(status.last_failure?.code || status.settings_error), "error");
      } else {
        notice("启动结果暂未确认，请查看运行状态。", "info");
      }
    } catch (error) {
      try { await readStatus(); } catch { /* Keep the operation error visible below. */ }
      if (isAccepting()) notice("角色已准备并开始接收消息。", "success");
      else notice(publicError(error), "error");
    } finally {
      setBusy(false);
      setLoading(button, false);
    }
  }
  async function readStatus() {
    let lastError = null;
    for (const candidate of candidates) {
      try {
        const value = await api("GET", `${candidate.prefix}/status`);
        host = candidate;
        status = value;
        renderStatus();
        await readBindingConfig();
        return;
      } catch (error) {
        lastError = error;
        if (!isMissingRoute(error)) throw error;
      }
    }
    throw lastError || new Error("没有发现可用的 Companion V2 管理接口。");
  }
  function publicError(error) {
    if (labelFor(error?.code)) return labelFor(error.code);
    const message = messageOf(error) || error?.message || "";
    if (labelFor(message)) return labelFor(message);
    if (/dashboard|sign in|unauthorized|401/i.test(message)) return "请先登录 AstrBot Dashboard，再从插件页面打开本页。";
    if (isMissingRoute(error)) return "当前插件版本没有提供角色与会话管理接口，请重新加载 Companion V2 插件。";
    if (error?.code === "host_operation_result_unknown") return "请求暂未返回，当前无法确认最终状态。";
    return failureMessage(error?.code || message);
  }
  function toggleTopologyPin() {
    const item = topologySnapshot?.sessions.find((row) => row.key === topologySelection);
    if (!item?.pinKey) return;
    if (topologyPinnedSessions.has(item.pinKey)) topologyPinnedSessions.delete(item.pinKey);
    else topologyPinnedSessions.add(item.pinKey);
    const saved = writePinnedTopologySessions(topologyPinnedSessions);
    const ordered = makeTopologySnapshot().sessions;
    const index = ordered.findIndex((row) => row.key === item.key);
    topologySessionPage = index < 0 ? 0 : Math.floor(index / TOPOLOGY_SESSION_PAGE_SIZE);
    renderTopology();
    notice(topologyPinnedSessions.has(item.pinKey)
      ? saved ? "会话窗口已置顶，并保存到当前浏览器。" : "会话窗口已置顶；当前浏览器不允许保存偏好。"
      : saved ? "会话窗口已取消置顶。" : "会话窗口已取消置顶；当前浏览器不允许保存偏好。",
    saved ? "success" : "info");
  }
  async function boot() {
    try {
      bridge = window.AstrBotPluginPage;
      if (!bridge) throw new Error("AstrBot 页面桥未加载，请从已登录的插件管理页面打开本页。");
      await bridge.ready();
      setBusy(true);
      await readStatus();
    } catch (error) {
      $("connection").textContent = "未连接";
      $("connection").dataset.state = "error";
      $("status-heading").textContent = "无法读取插件状态";
      status = null;
      host = null;
      topologySnapshot = null;
      renderTopology();
      notice(publicError(error), "error");
      updateControls();
    } finally {
      setBusy(false);
      document.querySelector(".status-panel").setAttribute("aria-busy", "false");
    }
  }
  $("refresh").addEventListener("click", async () => {
    setLoading($("refresh"), true);
    setBusy(true);
    try { await readStatus(); }
    catch (error) { notice(publicError(error), "error"); }
    finally { setBusy(false); setLoading($("refresh"), false); }
  });
  $("start-host")?.addEventListener("click", startHost);
  $("shared-model-provider")?.addEventListener("change", renderSharedModel);
  $("save-shared-model")?.addEventListener("click", saveSharedModel);
  $("topology-session-prev").addEventListener("click", () => changeTopologySessionPage(-1));
  $("topology-session-next").addEventListener("click", () => changeTopologySessionPage(1));
  $("topology-session-filters").addEventListener("click", event => {
    const button = event.target.closest("[data-session-filter]");
    if (!button || button.dataset.sessionFilter === topologySessionFilter) return;
    topologySessionFilter = button.dataset.sessionFilter;
    topologySessionPage = 0;
    const selected = topologySnapshot?.sessions.find(row => row.key === topologySelection);
    if (selected && topologySessionFilter !== "all" && selected.scopeKind !== topologySessionFilter) {
      topologySelection = null;
      topologyHover = null;
      renderSessionManager();
      paintTopologySelection(false);
    }
    renderTopologySessions(topologySnapshot, {resetScroll: true});
    scheduleTopologyLinks();
  });
  $("topology-pin-session").addEventListener("click", toggleTopologyPin);
  $("window-link-save")?.addEventListener("click", saveWindowLink);
  $("window-link-unlink")?.addEventListener("click", unlinkWindow);
  $("close-session-manager").addEventListener("click", () => { topologySelection = null; paintTopologySelection(); });
  $("hide-unlinked-personas")?.addEventListener("change", () => {
    refreshPersonaVisibility();
    renderTopology();
  });
  $("save-bindings")?.addEventListener("click", saveBindingConfig);
  $("save-group-targets")?.addEventListener("click", saveGroupTargets);
  const updateDraft = event => {
    const account = event.target.closest(".account-binding-card");
    if (account) markBindingsDirty(account);
  };
  document.addEventListener("input", updateDraft);
  document.addEventListener("change", updateDraft);
  document.addEventListener("companion-mappings-refresh-failed", () => {
    notice("角色映射已保存；小屋数据暂未更新，可重新读取状态。", "info");
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !topologySelection) return;
    topologySelection = null;
    paintTopologySelection();
  });
  window.addEventListener("resize", scheduleTopologyLinks, {passive: true});
  $("topology-sessions").addEventListener("scroll", scheduleTopologyLinks, {passive: true});
  $("topology-canvas").addEventListener("animationend", event => {
    if (event.target.matches(".topology-column, .topology-node")) scheduleTopologyLinks();
  });
  if (typeof ResizeObserver === "function") {
    const observer = new ResizeObserver(scheduleTopologyLinks);
    for (const id of ["topology-canvas", "topology-accounts", "topology-roles", "topology-sessions"]) {
      observer.observe($(id));
    }
  }
  boot();
  setInterval(() => {
    if (host && !busy && !bindingsDirty && !groupTargetsDirty
        && document.visibilityState !== "hidden" && !topologySelection) void readBindingConfig();
  }, 15000);
})();
