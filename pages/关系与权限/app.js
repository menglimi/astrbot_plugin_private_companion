"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const endpoint = "acquaintance";
  const TIER_LABELS = {standard: "普通用户", important: "重要用户", core: "核心用户"};
  const errors = {
    dashboard_identity_required: "请从已登录的 AstrBot 管理界面打开本页面。",
    staging_not_initialized: "角色数据尚未就绪，请在「账号与会话」保存角色映射；已保存可重新读取状态。",
    staging_host_terminated: "插件已经停止，请重新打开管理页面。",
    core_access_required: "当前管理账号没有相识目录查看权限。",
    acquaintance_request_invalid: "目录请求不合法，请刷新页面重试。",
    account_config_running: "请先停止 V2，再修改群聊接入状态。",
    account_group_target_account_required: "请先在账号与会话中绑定这个 Bot 账号和人格。",
    account_group_target_route_unresolved: "AstrBot 还无法确定该群对应的 Bot 账号。先让目标 Bot 在该群产生一次会话记录，再重新读取群聊目录。",
    account_group_target_unavailable: "这个群当前不在 AstrBot 会话目录中，请刷新后重试。",
    account_group_target_apply_failed: "群聊接入写入遇到未预期错误；页面会重读群聊目录核对当前状态。请根据后端原因检查账号与会话配置后重试。",
    acquaintance_native_directory_unavailable: "AstrBot 原生群聊目录读取失败。请检查会话目录与插件日志后重新读取。",
    acquaintance_directory_read_failed: "群聊目录读取遇到未预期错误。请检查插件日志后重新读取。",
    setup_targets_unavailable: "AstrBot 会话目录暂不可读。请检查账号连接与插件日志，再重新读取群聊目录。",
    host_mappings_conflict: "当前 Bot 账号与人格映射有冲突，请先在账号与会话中校正映射后重试。",
    host_mappings_required: "共享运行配置至少需要一个会话目标和人格，请先在账号与会话中补齐。",
    account_config_save_failed: "群聊接入状态没有保存，请检查配置目录权限后重试。",
    host_mappings_required: "至少保留一个 V2 会话目标，无法移除最后一个群聊目标。",
    acquaintance_subject_required: "请先选择一位成员。",
    acquaintance_subject_unknown: "这个人不在当前运行实例的相识目录里，请刷新后重试。",
    authorization_command_invalid: "这次授权变更缺少必要信息，请刷新页面重试。",
    authorization_capability_configured: "这项能力由运行实例配置签发，请到对应功能页调整。",
    authorization_capability_not_managed: "这项能力当前没有本页可以撤销的授权，请刷新后重试。",
    authorization_revision_conflict: "授权刚刚被改过，页面已读回最新状态，请再确认一次。",
    authorization_operation_unresolved: "上一次授权变更没有走完，请刷新核对后再试。",
    authorization_change_not_applied: "这次授权变更没有落到记录上，请刷新核对。",
    person_nickname_command_invalid: "称呼请求不完整，请刷新成员资料后重试。",
    person_nickname_invalid: "管理员称呼最多 64 个字符。",
    acquaintance_person_unknown: "这位成员已不在当前 Bot 与角色目录中，请刷新后重试。",
    person_nickname_revision_conflict: "成员资料刚刚被更新，正在读回最新版本。",
    person_nickname_operation_conflict: "这次称呼操作键已用于另一项修改，请重新保存。",
    person_nickname_operation_unresolved: "上一次称呼修改尚未完成，请刷新核对后重试。",
  };
  let bridge, data = null, filter = "stable", selected = null, selectedGroupId = "", activeView = "network", noticeView = "";
  let graphScope = "all", searchTerm = "", graphExpandedCluster = "", requestSequence = 0, hideIdleUsers = true, groupStatusFilter = "all";
  let memberChangeSequence = 0;

  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const hue = (seed) => {
    let value = 0;
    for (const char of String(seed)) value = (value * 31 + char.codePointAt(0)) % 360;
    return value;
  };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
  const number = (value) => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
  const shortTime = (value) => {
    if (!value) return "—";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "—";
    return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric" }).format(date);
  };
  const dateTime = (value) => {
    if (!value) return "—";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "—";
    return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  };
  const nameOf = (person) => person.display_name || `未命名成员 ${person.native_account_id ?? ""}`.trim();
  const isPrivateUser = person => Boolean(person?.private_chat || person?.private_contact
    || person?.private_target && (person?.admin_nickname || ["core", "important"].includes(person?.identity_tier)));
  const knownPerson = person => Boolean(hasInteraction(person) || person?.private_contact
    || person?.admin_nickname || ["core", "important"].includes(person?.identity_tier));
  const personFilter = person => isPrivateUser(person) ? "stable"
    : person?.group_member ? "temporary" : person ? "discovered" : null;
  const accountLabel = (item) => item.account_name || (/^\d+$/.test(String(item.account_id || ""))
    ? `QQ ${item.account_id}` : "当前 Bot 账号");
  const groupsOf = (person) => person.groups && typeof person.groups === "object" ? person.groups : {};
  const groupIdsOf = (person) => Object.keys(groupsOf(person));
  const personaIdsOf = (person) => {
    const ids = Array.isArray(person?.persona_ids) ? person.persona_ids
      .map(value => value === "[%None]" ? "" : String(value ?? "").trim()) : [];
    return [...new Set(ids)].sort((a, b) => a.localeCompare(b, "zh-CN"));
  };
  const personaGroupKey = (person) => Array.isArray(person?.persona_ids) && person.persona_ids.length
    ? JSON.stringify([person.platform, person.bot_id, person.account_id,
      [...new Set(personaIdsOf(person))].sort((a, b) => a.localeCompare(b, "zh-CN"))])
    : JSON.stringify([person.platform, person.bot_id, person.account_id, ["role", person?.role_id || ""]]);
  const personaDisplayLabel = (person) => personaIdsOf(person).map(value => value || "默认人格").join("、")
    || person?.role_name || "当前人格";
  const routeSnapshot = (person) => Object.fromEntries(["platform", "bot_id", "account_id", "role_id", "persona_ids",
    "revision", "settings_available", "private_target", "private_chat", "private_contact",
    "legacy_private_contact", "legacy_maintained", "admin_nickname", "identity_tier",
    "display_name", "native_account_id", "role_name", "private", "groups", "total"]
    .filter(key => person?.[key] !== undefined).map(key => [key, Array.isArray(person[key]) ? [...person[key]] : person[key]]));
  const displayPersonKey = (person) => JSON.stringify([person.subject_id, personaGroupKey(person)]);
  const activeDisplayRoute = (person) => person.private_target === true && person.settings_available === true;
  const mergeTime = (left, right, pickMax) => {
    const values = [left, right].filter(Boolean).sort((a, b) => String(a).localeCompare(String(b)));
    return values.length ? values[pickMax ? values.length - 1 : 0] : null;
  };
  const mergeContext = (left, right) => {
    if (!left && !right) return null;
    return {msg_count: Math.max(number(left?.msg_count), number(right?.msg_count)),
      active_days: Math.max(number(left?.active_days), number(right?.active_days)),
      first_seen: mergeTime(left?.first_seen, right?.first_seen, false),
      last_seen: mergeTime(left?.last_seen, right?.last_seen, true)};
  };
  function mergeDirectoryPeople(rows) {
    const merged = new Map();
    for (const source of rows || []) {
      const key = displayPersonKey(source);
      const snapshot = routeSnapshot(source);
      if (!merged.has(key)) {
        const activeRoutes = activeDisplayRoute(source) ? [snapshot] : [];
        merged.set(key, {...source, persona_ids: personaIdsOf(source), persona_role_ids: [source.role_id].filter(Boolean),
          private_target: Boolean(source.private_target), settings_available: activeRoutes.length > 0,
          display_routes: activeRoutes, display_route_count: activeRoutes.length});
        continue;
      }
      const target = merged.get(key);
      const routeKey = JSON.stringify([snapshot.platform, snapshot.bot_id, snapshot.account_id, snapshot.role_id]);
      if (activeDisplayRoute(source) && !target.display_routes.some(route => JSON.stringify([route.platform, route.bot_id, route.account_id, route.role_id]) === routeKey)) {
        target.display_routes.push(snapshot);
      }
      target.persona_role_ids = [...new Set([...(target.persona_role_ids || []), source.role_id].filter(Boolean))];
      target.display_route_count = target.display_routes.length;
      target.persona_ids = [...new Set([...(target.persona_ids || []), ...personaIdsOf(source)])];
      target.private_chat ||= Boolean(source.private_chat);
      target.private_contact ||= Boolean(source.private_contact);
      target.legacy_private_contact ||= Boolean(source.legacy_private_contact);
      target.legacy_maintained ||= Boolean(source.legacy_maintained);
      target.private_target ||= Boolean(source.private_target);
      target.group_member ||= Boolean(source.group_member);
      target.settings_available = target.display_route_count > 0;
      if (source.admin_nickname && !target.admin_nickname) target.admin_nickname = source.admin_nickname;
      if (source.revision > number(target.revision)) target.revision = source.revision;
      target.private = mergeContext(target.private, source.private);
      const groups = {...groupsOf(target)};
      for (const [id, value] of Object.entries(groupsOf(source))) groups[id] = mergeContext(groups[id], value);
      target.groups = groups;
      target.total = mergeContext(target.total, source.total);
      target.role_name = personaDisplayLabel(target);
    }
    return [...merged.values()];
  }
  const groupKey = (group) => String(group.group_id ?? "");
  const groupBaseKey = (group) => JSON.stringify([group.platform_id || group.platform || "",
    group.bot_id || "", group.account_id || "", groupKey(group)]);
  const groupTargetRouteReady = (group) => [group.platform_id, group.account_id, group.bot_id, groupKey(group)]
    .every(value => typeof value === "string" && value.trim());
  function groupTargetBlock(group) {
    const access = data?.group_access;
    if (!access) return {message: "暂时无法确认自动接入状态，请先重新读取目录。", tab: "host", label: "账号与会话"};
    if (!groupTargetRouteReady(group)) return {message: "AstrBot 还没有为这个群确定唯一的 Bot 账号。"};
    const accounts = Array.isArray(access.accounts) ? access.accounts : [];
    if (!accounts.some(item => item.platform_id === group.platform_id
        && item.account_id === group.account_id && item.bot_id === group.bot_id)) {
      return {message: "这个 Bot 账号尚未归入角色，请在账号与会话中完成角色映射。",
        tab: "host", label: "账号与会话"};
    }
    if (!Array.isArray(access.persona_bots) || !access.persona_bots.includes(group.bot_id)) {
      return {message: "这个 Bot 账号还没有归入角色，请在账号与会话中完成映射。",
        tab: "host", label: "账号与会话"};
    }
    return null;
  }
  function managementButton(tab, label) {
    const button = node("button", label, "secondary group-access-button");
    button.type = "button";
    button.addEventListener("click", () => window.parent?.postMessage?.({
      channel: "companion-workspace", kind: "navigate", target: {tab},
    }, "*"));
    return button;
  }
  const groupRouteKey = (group) => JSON.stringify([groupBaseKey(group), personaIdsOf(group).length
    ? ["persona", personaIdsOf(group)] : ["role", group.role_id || ""]]);
  const groupName = (group) => group.display_name || group.group_name || `群 ${groupKey(group)}`;
  const groupLabel = (group) => group.display_name || group.group_name
    ? `${groupName(group)}（${groupKey(group)}）` : groupName(group);
  const groupRoleLabel = (group) => {
    const values = String(group?.role_name || "").split(/[、,，]/).map(value =>
      value.replace(/\s*[-_·－]\s*(?:私聊|群聊)\s*$/u, "").trim()).filter(Boolean);
    return [...new Set(values)].join("、") || (group?.role_id ? "人格分线" : "角色待映射");
  };
  const groupListStatus = (group) => group.configured ? "已自动接入"
    : group.discovered ? groupTargetRouteReady(group) ? "AstrBot 已发现，待角色映射" : "AstrBot 已发现，账号待确认"
      : "历史往来";
  const groupStatusKey = (group) => group.configured ? "configured" : group.discovered ? "pending" : "history";
  const groupStatusLabel = (group) => group.configured ? "已接入"
    : group.discovered ? "待处理" : "历史往来";
  const groupDetailStatus = (group) => group.configured ? "已按角色映射自动接入"
    : group.discovered ? groupTargetRouteReady(group) ? "AstrBot 已发现该群会话；等待角色映射。"
      : "AstrBot 已发现该群会话；对应 Bot 账号尚未确认。"
      : "历史群内往来记录";
  const groupGraphStatus = (group) => group.configured ? "已自动接入"
    : group.discovered ? groupTargetRouteReady(group) ? "已发现 · 待角色映射" : "已发现 · 账号待确认" : "历史群内记录";
  const groupById = (id) => data?.groups.find((group) => groupKey(group) === id) || { group_id: id };
  const groupSearchText = (group) => [groupName(group), groupKey(group)].join(" ").toLocaleLowerCase();
  const graphLabel = (value, limit) => Array.from(value).length > limit
    ? Array.from(value).slice(0, limit - 1).join("") + "…" : value;

  function readDirectoryPreferences() {
    const toggle = $("hide-idle-users");
    if (!toggle) return;
    let stored = null;
    try { stored = window.localStorage?.getItem("companion-v2.hide-idle-users"); } catch (_) { stored = null; }
    hideIdleUsers = stored === null ? true : stored !== "false";
    toggle.checked = hideIdleUsers;
  }
  function saveDirectoryPreference() {
    try { window.localStorage?.setItem("companion-v2.hide-idle-users", String(hideIdleUsers)); } catch (_) { /* 页面桥可能禁用存储 */ }
  }
  function hasInteraction(person) {
    return Boolean(person?.private_chat || person?.group_member
      || number(person?.total?.msg_count) > 0
      || number(person?.private?.msg_count) > 0
      || Object.values(groupsOf(person)).some((item) => number(item?.msg_count) > 0));
  }
  function isIdleOrdinaryUser(person) {
    return !knownPerson(person);
  }
  function peopleForCurrentFilter({includeHidden = false} = {}) {
    if (!data) return [];
    return data.persons.filter((person) => {
      if (!matchesSearch(person)) return false;
      if (filter === "stable" && !isPrivateUser(person)) return false;
      if (filter === "temporary" && !person.group_member) return false;
      if (filter === "discovered" && knownPerson(person)) return false;
      if (filter !== "discovered" && !includeHidden && hideIdleUsers && isIdleOrdinaryUser(person)) return false;
      return true;
    });
  }

  function notice(message, error = false) {
    const target = $("notice");
    target.textContent = message;
    target.className = "notice" + (error ? " error" : "");
    noticeView = message ? activeView : "";
    target.hidden = !message;
  }
  function setSyncState(state, message) {
    const target = $("sync-state");
    target.className = "sync-state" + (state ? " is-" + state : "");
    target.textContent = message;
  }
  function unwrap(response) {
    const wrapped = ["ok", "error"].includes(response?.status) && response?.data !== undefined ? response
      : ["ok", "error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    if (wrapped?.status === "error") throw Object.assign(
      new Error(wrapped.reason || wrapped.error?.reason || wrapped.message || "读取失败"),
      { code: wrapped.data?.code || wrapped.error?.code || wrapped.message });
    const content = wrapped ? wrapped.data : response;
    if (!content || typeof content !== "object") throw new Error("未收到服务结果。");
    return content;
  }
  function localized(error, fallback = "操作未完成，请重新读取当前页面数据后重试。") {
    if (errors[error?.code]) return new Error(errors[error.code]);
    const message = [error?.message, error?.reason].find((item) => typeof item === "string" && item) || "";
    if (/initialized v2 profile|staging profile|staging profile first/i.test(message)) return new Error(errors.staging_not_initialized);
    if (/dashboard|sign in/i.test(message)) return new Error(errors.dashboard_identity_required);
    return new Error(fallback);
  }
  function groupTargetFailure(error) {
    const code = typeof error?.code === "string" ? error.code : "";
    const reason = [error?.reason, error?.message].find((item) => typeof item === "string" && item) || "";
    const localizedReason = code ? errors[code] : "";
    const plainReason = reason.replace(/[。.!?]+$/, "");
    if (!code && /^(请求未完成，请刷新核对当前状态后重试|未收到执行结果，请核对后重试本次操作)[。.!?]*$/.test(reason)) {
      return new Error("没有收到 AstrBot 对群聊接入操作的明确回执，当前状态可能已变化；正在重读群聊目录核对结果。");
    }
    if (!code && !reason) {
      return new Error("没有收到 AstrBot 对群聊接入操作的明确回执，当前状态可能已变化；正在重读群聊目录核对结果。");
    }
    if (code || reason) return new Error(`${(localizedReason || "群聊接入失败").replace(/[。.!?]+$/, "")}`
      + `${code ? `（${code}）` : ""}`
      + `${reason && reason !== localizedReason ? `：${plainReason}` : ""}`
      + "。正在重读群聊目录核对当前状态。");
    return localized(error, "群聊接入状态未完成，请重新读取群聊目录后重试。");
  }
  function searchText(person) {
    return [nameOf(person), person.native_account_id, person.subject_id,
      personaDisplayLabel(person),
      ...groupIdsOf(person).map((id) => groupLabel(groupById(id)))].join(" ").toLocaleLowerCase();
  }
  function matchesSearch(person) { return !searchTerm || searchText(person).includes(searchTerm); }
  function visiblePeople() {
    return peopleForCurrentFilter();
  }
  function graphPeople() {
    if (!data) return [];
    return data.persons.filter((person) => {
      if (!matchesSearch(person)) return false;
      if (hideIdleUsers && isIdleOrdinaryUser(person)) return false;
      if (graphScope === "private") return isPrivateUser(person);
      if (graphScope === "group") return person.group_member;
      return true;
    });
  }
  function allGroups() {
    if (!data) return [];
    const groups = new Map(), peopleByBase = new Map();
    const add = (item, {supplied = false} = {}) => {
      const id = groupKey(item);
      if (!id) return null;
      const key = groupBaseKey(item);
      const previous = groups.get(key);
      if (!previous) {
        groups.set(key, { ...item, group_id: id, configured: supplied ? item.configured !== false : Boolean(item.configured),
          member_count: number(item.member_count), msg_count: number(item.msg_count), supplied,
          people: [] });
      } else {
        previous.configured ||= Boolean(item.configured);
        previous.discovered ||= Boolean(item.discovered);
        previous.display_name ||= item.display_name || item.group_name;
        if (supplied) {
          previous.supplied = true;
          previous.member_count = Math.max(previous.member_count, number(item.member_count));
          previous.msg_count = Math.max(previous.msg_count, number(item.msg_count));
        }
      }
      return groups.get(key);
    };
    for (const group of data.groups || []) {
      let source = group;
      if (!group.platform_id && !group.bot_id && !group.account_id) {
        const candidates = (data.persons || []).filter((person) => groupsOf(person)[groupKey(group)]);
        const routes = new Map(candidates.map((person) => [groupRouteKey({
          group_id: groupKey(group), platform_id: person.platform, bot_id: person.bot_id,
          account_id: person.account_id, role_id: person.role_id, persona_ids: person.persona_ids,
        }), person]));
        if (routes.size === 1) {
          const person = routes.values().next().value;
          source = {...group, platform_id: person.platform, bot_id: person.bot_id,
            account_id: person.account_id, role_id: person.role_id};
        }
      }
      add(source, {supplied: true});
    }
    for (const person of data.persons || []) {
      for (const [id, item] of Object.entries(groupsOf(person))) {
        if (!id) continue;
        const group = add({group_id: id, configured: false, platform_id: person.platform,
          bot_id: person.bot_id, account_id: person.account_id, role_id: person.role_id});
        const people = peopleByBase.get(groupBaseKey(group)) || [];
        if (!people.some((value) => personRouteKey(value) === personRouteKey(person))) people.push(person);
        peopleByBase.set(groupBaseKey(group), people);
        if (!group.supplied && !group.configured) {
          group.member_count = people.length;
          group.msg_count += number(item?.msg_count);
        }
      }
    }
    const networks = new Map();
    for (const network of data.group_networks || []) {
      const base = groupBaseKey({ ...network, group_id: network.group_id });
      const entries = networks.get(base) || [];
      entries.push(network);
      networks.set(base, entries);
      if (!groups.has(base)) add({...network, configured: Boolean(network.configured)});
    }
    const expanded = [];
    for (const group of groups.values()) {
      const groupNetworks = networks.get(groupBaseKey(group)) || [];
      const people = peopleByBase.get(groupBaseKey(group)) || [];
      if (groupNetworks.length) {
        /* A current route wins over stale historical routes for the same physical group.
           Otherwise an old role key from a previous profile produces a second card for a
           group that is already configured, and its reply/archive buttons cannot resolve. */
        const activeNetworks = groupNetworks.filter(network => network.configured === true);
        const visibleNetworks = activeNetworks.length ? activeNetworks : groupNetworks;
        const byPersona = new Map();
        for (const network of visibleNetworks) {
          const key = personaIdsOf(network).length ? JSON.stringify(["persona", personaIdsOf(network)])
            : JSON.stringify(["role", network.role_id || ""]);
          const entries = byPersona.get(key) || [];
          entries.push(network);
          byPersona.set(key, entries);
        }
        for (const entries of byPersona.values()) {
          const network = mergeGroupNetworks(entries);
          expanded.push({...group, role_id: network.role_id, role_ids: network.role_ids,
            persona_ids: network.persona_ids, role_name: network.role_name, network,
            configured: network.configured === true,
            discovered: Boolean(network.discovered || group.discovered),
            member_count: number(network.member_count), people});
        }
      } else {
        const byPersona = new Map();
        for (const person of people) {
          const key = personaGroupKey(person);
          const entries = byPersona.get(key) || [];
          entries.push(person);
          byPersona.set(key, entries);
        }
        if (byPersona.size > 1) for (const entries of byPersona.values()) {
          const roleIds = [...new Set(entries.flatMap(person => person.persona_role_ids || [person.role_id]).filter(Boolean))];
          expanded.push({...group, role_id: roleIds[0] || "", role_ids: roleIds,
            persona_ids: personaIdsOf(entries[0]), role_name: personaDisplayLabel(entries[0]), people: entries});
        } else {
          const entries = [...byPersona.values()][0] || people;
          const roleIds = [...new Set(entries.flatMap(person => person.persona_role_ids || [person.role_id]).filter(Boolean))];
          expanded.push({...group, role_id: roleIds[0] || group.role_id || "", role_ids: roleIds,
            persona_ids: personaIdsOf(entries[0] || group),
            role_name: entries.length ? personaDisplayLabel(entries[0]) : group.role_name || "", people: entries});
        }
      }
    }
    return expanded.sort((a, b) => Number(b.configured) - Number(a.configured)
      || number(b.msg_count) - number(a.msg_count) || groupKey(a).localeCompare(groupKey(b), "zh-CN")
      || groupRouteKey(a).localeCompare(groupRouteKey(b), "zh-CN"));
  }
  function searchedGroups() {
    return allGroups().filter((group) => {
      if (graphScope === "private") return false;
      if (!searchTerm) return true;
      const idMatch = groupSearchText(group).includes(searchTerm);
      return idMatch || peopleInGroup(group, {honorSearch: false}).some(matchesSearch);
    });
  }
  function peopleInGroup(group, { honorSearch = true } = {}) {
    if (!data) return [];
    if (group.discovered && (!group.bot_id || !group.account_id)) return [];
    const id = groupKey(group);
    const candidates = data.persons.filter((person) => groupsOf(person)[id]);
    const matchesRoute = (person) => (!group.platform_id || person.platform === group.platform_id)
      && (!group.bot_id || person.bot_id === group.bot_id)
      && (!group.account_id || person.account_id === group.account_id)
      && (group.persona_ids?.length
        ? JSON.stringify(personaIdsOf(person)) === JSON.stringify(personaIdsOf(group))
        : (group.role_ids?.length ? group.role_ids.some(role => (person.persona_role_ids || [person.role_id]).includes(role))
          : !group.role_id || person.role_id === group.role_id));
    let members = candidates.filter(matchesRoute);
    // Older directory payloads did not carry a complete route on group rows.
    // A single unambiguous candidate remains usable during that transition.
    if (!members.length && !group.platform_id && !group.bot_id && !group.account_id && candidates.length === 1) {
      members = candidates;
    }
    return members.filter((person) => !honorSearch || !searchTerm
      || groupSearchText(group).includes(searchTerm) || matchesSearch(person));
  }
  function mergeGroupNetworks(items) {
    const first = items[0];
    const roleIds = [...new Set(items.map(item => item.role_id).filter(Boolean))].sort();
    const personaIds = [...new Set(items.flatMap(personaIdsOf))].sort((a, b) => a.localeCompare(b, "zh-CN"));
    const members = new Map();
    for (const network of items) for (const member of network.members || []) {
      const account = String(member.native_account_id || "");
      if (!account) continue;
      const previous = members.get(account);
      if (!previous) {members.set(account, {...member}); continue;}
      const left = previous.group_observation || {}, right = member.group_observation || {};
      previous.group_observation = {...left,
        message_count: Math.max(number(left.message_count), number(right.message_count)),
        active_days: Math.max(number(left.active_days), number(right.active_days)),
        last_seen: mergeTime(left.last_seen, right.last_seen, true),
        recent: [...(left.recent || []), ...(right.recent || [])].filter((item, index, all) =>
          all.findIndex(other => JSON.stringify(other) === JSON.stringify(item)) === index).slice(-8)};
      const a = previous.acquaintance || {}, b = member.acquaintance || {};
      previous.acquaintance = {...a, message_count: Math.max(number(a.message_count), number(b.message_count)),
        private_chat: Boolean(a.private_chat || b.private_chat)};
      const clues = new Map([...(previous.clues || []), ...(member.clues || [])].map(item => [item.entry_id || JSON.stringify(item), item]));
      if (clues.size) previous.clues = [...clues.values()];
    }
    const edges = new Map();
    for (const network of items) for (const edge of network.edges || []) {
      const pair = [String(edge.a), String(edge.b)].sort();
      const key = JSON.stringify(pair), old = edges.get(key);
      if (!old || number(edge.count) > number(old.count)) edges.set(key, {...edge, a: pair[0], b: pair[1]});
      else if (edge.last_seen && (!old.last_seen || edge.last_seen > old.last_seen)) {
        edges.set(key, {...old, ...edge, a: pair[0], b: pair[1], count: old.count});
      }
    }
    const clues = new Map();
    for (const network of items) for (const clue of network.unmatched_clues || []) {
      clues.set(clue.entry_id || JSON.stringify(clue), clue);
    }
    return {...first, role_id: roleIds[0] || "", role_ids: roleIds, persona_ids: personaIds,
      role_name: personaIds.map(value => value || "默认人格").join("、") || first.role_name,
      configured: items.some(item => item.configured), member_count: members.size, members: [...members.values()],
      edges: [...edges.values()], interaction_count: [...edges.values()].reduce((sum, edge) => sum + number(edge.count), 0),
      unmatched_clues: [...clues.values()]};
  }
  function avatar(person, className = "avatar") {
    const wrapper = node("span", undefined, className);
    wrapper.style.setProperty("--hue", String(hue(person.subject_id)));
    wrapper.append(node("span", (nameOf(person)[0] ?? "？").toUpperCase()));
    if (person.private_chat) wrapper.append(node("i", undefined, "private-dot"));
    return wrapper;
  }
  function badge(text, className = "") { return node("small", text, "badge" + (className ? " " + className : "")); }
  function contextBadge(text, className = "") { return node("span", text, "ctx" + (className ? " " + className : "")); }

  function personCard(person, index) {
    const card = node("button", undefined, "person-card");
    card.type = "button";
    card.style.setProperty("--i", String(index));
    card.dataset.userId = String(person.subject_id || person.native_account_id || "");
    card.setAttribute("aria-label", `打开 ${nameOf(person)} 的用户资料`);
    card.setAttribute("aria-pressed", String(selected && personRouteKey(selected) === personRouteKey(person)));
    const head = node("span", undefined, "card-head");
    head.append(avatar(person));
    const title = node("span", undefined, "card-title");
    title.append(node("strong", nameOf(person)));
    title.title = `${nameOf(person)} · ${person.native_account_id ?? ""}`;
    const badges = node("span", undefined, "card-badges");
    if (person.private_chat) badges.append(badge("私聊", "private"));
     if (person.group_member) badges.append(badge(`群聊 ${groupIdsOf(person).length}`, "group"));
     if (!person.private_chat && !person.group_member) badges.append(badge(person.private_contact ? "已维护" : "未有往来"));
     badges.append(badge(TIER_LABELS[person.identity_tier] || "普通用户", person.identity_tier === "core" ? "companion" : ""));
     if (person.identity_tier === "core") badges.append(badge("管理员", "administrator"));
     title.append(badges);
    head.append(title);
    const meta = node("span", undefined, "card-meta");
    meta.append(node("span", `QQ ${person.native_account_id || "未知"}`),
      node("span", personaDisplayLabel(person)),
      node("span", accountLabel(person)));
    if (person.display_route_count > 1) meta.append(node("span", `${person.display_route_count} 个内部归属`, "route-count"));
    const contexts = node("span", undefined, "card-contexts");
    const totalMessages = number(person.total?.msg_count);
    contexts.append(contextBadge(`${totalMessages} 条消息 · 最近 ${shortTime(person.total?.last_seen)}`,
      totalMessages ? "private" : "idle"));
    card.append(head, meta, contexts);
    card.addEventListener("click", () => select(person));
    return card;
  }
  function renderPeople() {
    const grid = $("people-grid");
    grid.replaceChildren();
    const people = visiblePeople();
    $("people-result-count").textContent = `${people.length} 人`;
    const visibilitySummary = $("people-visibility-summary");
    if (visibilitySummary) {
      const hidden = data ? peopleForCurrentFilter({includeHidden: true}).length - people.length : 0;
      visibilitySummary.textContent = filter === "discovered" ? "仅在 AstrBot 中发现的会话，可在此单独配置" : hideIdleUsers
        ? hidden ? `已隐藏 ${hidden} 位无交往普通用户` : "默认隐藏无交往普通用户"
        : "已显示全部用户";
    }
    const toggle = $("hide-idle-users");
    if (toggle) { toggle.checked = hideIdleUsers; toggle.disabled = filter === "discovered"; }
    if (!people.length) {
      const empty = node("div", undefined, "empty-state");
      const hidden = data ? peopleForCurrentFilter({includeHidden: true}).length : 0;
      const title = data ? searchTerm ? "没有符合的往来用户"
        : hideIdleUsers && hidden ? "无交往普通用户已隐藏" : "这里还没有符合的用户" : "正在读取用户目录…";
      empty.append(node("strong", title), node("p", data && hideIdleUsers && hidden
        ? "关闭“隐藏无交往的普通用户”后可查看完整目录。" : "收到私聊或群聊记录后，用户会出现在这里。"));
      grid.append(empty);
      return;
    }
    people.forEach((person, index) => grid.append(personCard(person, index)));
  }

  const GRAPH_PERSON_LIMIT = 48;
  const GRAPH_FEATURE_LIMIT = 34;
  const graphRoleName = (people) => {
    const names = [...new Set(people.map(personaDisplayLabel).filter(Boolean))];
    const contextName = bridge?.getContext?.()?.display?.role_name;
    return names.length === 1 ? names[0] : contextName || "当前角色";
  };
  const graphChannels = (person) => {
    const channels = [];
    if (person.private_chat) channels.push("私聊");
    if (person.group_member) channels.push(String(groupIdsOf(person).length || 1) + " 个群");
    return channels.length ? channels.join(" · ") : "连接已记录";
  };
  const graphMemoryHint = (person) => {
    const profile = person.relationship_profile || person.profile || {};
    const nickname = person.admin_nickname || profile.default_nickname;
    const relation = profile.role_relation || person.relationship || "";
    if (nickname && relation) return String(nickname) + " · " + String(relation);
    if (nickname) return "称呼：" + String(nickname);
    if (relation) return String(relation);
    const groups = groupIdsOf(person).map(id => groupName(groupById(id))).filter(Boolean);
    return groups.length ? "出现于 " + graphLabel(groups[0], 16) : "等待更多共同片段";
  };
  const graphPersonMeta = (person) => graphChannels(person) + " · 最近 " + shortTime(person.total?.last_seen);
  const graphPriority = (person) => String(person.total?.last_seen || "") + "|" + String(number(person.total?.msg_count)).padStart(9, "0");

  // 雷达距离只使用已经记录的互动信号，不把身份等级或权限当作亲密度。
  const radarDaysSince = (value) => {
    const timestamp = Date.parse(value || "");
    return Number.isFinite(timestamp) ? Math.max(0, (Date.now() - timestamp) / 86400000) : Infinity;
  };
  const radarLogScore = (value, ceiling) => Math.min(1, Math.log1p(number(value)) / Math.log1p(ceiling));
  const radarCloseness = (person) => {
    const messages = number(person.total?.msg_count)
      + number(person.private?.msg_count) * .65
      + Object.values(groupsOf(person)).reduce((sum, item) => sum + number(item?.msg_count) * .35, 0);
    const activeDays = number(person.total?.active_days)
      + number(person.private?.active_days) * .5;
    const days = radarDaysSince(person.total?.last_seen);
    const recent = Number.isFinite(days) ? Math.exp(-days / 45) : 0;
    return Math.max(.06, Math.min(1, radarLogScore(messages, 160) * .5
      + Math.min(1, activeDays / 45) * .25 + recent * .25));
  };
  function graphClusters(people) {
    const buckets = new Map([
      ["private", {id: "private", label: "私聊关系", detail: "内圈候选", people: []}],
      ["both", {id: "both", label: "私聊 + 群聊", detail: "双重连接", people: []}],
      ["group", {id: "group", label: "群聊相识", detail: "外圈用户", people: []}],
      ["other", {id: "other", label: "待补充连接", detail: "尚无稳定窗口", people: []}],
    ]);
    for (const person of people) {
      const id = person.private_chat && person.group_member ? "both"
        : person.private_chat ? "private" : person.group_member ? "group" : "other";
      buckets.get(id).people.push(person);
    }
    return [...buckets.values()].filter(item => item.people.length);
  }
  function graphVisiblePeople(people) {
    if (people.length <= GRAPH_PERSON_LIMIT) return {visible: people, hidden: [], expanded: false};
    const ranked = [...people].sort((a, b) => graphPriority(b).localeCompare(graphPriority(a)));
    const clusters = graphClusters(people);
    const expanded = clusters.find(item => item.id === graphExpandedCluster);
    if (expanded) {
      const selectedKey = selected && personRouteKey(selected);
      const keep = ranked.filter(person => personRouteKey(person) !== selectedKey).slice(0, 6);
      const expandedPeople = expanded.people.slice().sort((a, b) => graphPriority(b).localeCompare(graphPriority(a)))
        .slice(0, GRAPH_FEATURE_LIMIT - keep.length);
      const merged = [...keep, ...expandedPeople];
      if (selected && people.includes(selected) && !merged.includes(selected)) merged.unshift(selected);
      return {visible: [...new Map(merged.map(person => [personRouteKey(person), person])).values()], hidden: [], expanded: true};
    }
    const visible = ranked.slice(0, GRAPH_FEATURE_LIMIT);
    if (selected && people.includes(selected) && !visible.includes(selected)) visible[visible.length - 1] = selected;
    return {visible: [...new Map(visible.map(person => [personRouteKey(person), person])).values()], hidden: people.filter(person => !visible.includes(person)), expanded: false};
  }
  function radarLayout(people, centerX, centerY) {
    const place = (list, minRadius, maxRadius, zone) => {
      const ordered = list.slice().sort((a, b) => hue(personRouteKey(a)) - hue(personRouteKey(b))
        || personRouteKey(a).localeCompare(personRouteKey(b)));
      return ordered.map((person, index) => {
        const score = radarCloseness(person);
        const angle = -Math.PI / 2 + (index + .5) / Math.max(1, ordered.length) * Math.PI * 2
          + (zone === "private" ? .08 : -.04);
        const radius = maxRadius - score * (maxRadius - minRadius);
        return {person, score, angle, zone, x: centerX + Math.cos(angle) * radius, y: centerY + Math.sin(angle) * radius};
      });
    };
    const privatePeople = people.filter(person => person.private_chat);
    const groupPeople = people.filter(person => !person.private_chat && person.group_member);
    const unlinkedPeople = people.filter(person => !person.private_chat && !person.group_member);
    return [
      // 中心角色卡占据核心区，私聊光点停在它外侧；越活跃越靠近中心。
      ...place(privatePeople, 106, 148, "private"),
      ...place(groupPeople, 202, 278, "group"),
      ...place(unlinkedPeople, 286, 300, "other"),
    ];
  }
  function svgRadarPerson(item, visibleCount) {
    const {person, score, angle, zone, x, y} = item;
    const id = esc(personRouteKey(person));
    const name = esc(nameOf(person));
    const selectedClass = selected && personRouteKey(selected) === personRouteKey(person) ? " selected" : "";
    const direction = Math.cos(angle) >= 0 ? 1 : -1;
    const labelY = Math.sin(angle) > .58 ? 25 : Math.sin(angle) < -.58 ? -18 : 4;
    const labelAnchor = direction > 0 ? "start" : "end";
    const labelX = direction * 14;
    const showLabel = visibleCount <= 18 || selectedClass || score >= .72;
    const channelDots = [
      person.private_chat ? '<circle class="radar-channel-dot private" cx="8" cy="-8" r="3"><title>私聊连接</title></circle>' : "",
      person.group_member ? '<circle class="radar-channel-dot group" cx="15" cy="-4" r="3"><title>'
        + (groupIdsOf(person).length || 1) + ' 个群聊连接</title></circle>' : "",
    ].join("");
    return [
      '<g class="graph-person radar-person ', zone, selectedClass, '" data-person-id="', id,
      '" data-radar-zone="', zone, '" transform="translate(', x, " ", y,
      ')" tabindex="0" role="button" aria-label="打开 ', name, ' 的用户资料">',
      '<title>', name, " · ", esc(graphPersonMeta(person)), " · 靠近度 ", Math.round(score * 100), "</title>",
      '<circle class="radar-dot-halo" r="', zone === "private" ? 13 : 11,
      '"></circle><circle class="radar-dot" r="', zone === "private" ? 6 : 5, '"></circle>',
      channelDots,
      showLabel ? '<text class="radar-person-label" x="' + labelX + '" y="' + labelY
        + '" text-anchor="' + labelAnchor + '">' + esc(graphLabel(nameOf(person), 11)) + "</text>" : "",
      "</g>",
    ].join("");
  }
  function svgCluster(cluster, x, y) {
    const expanded = graphExpandedCluster === cluster.id;
    const count = cluster.people.length;
    return [
      '<g class="graph-cluster radar-cluster', expanded ? " selected" : "",
      '" data-graph-cluster="', esc(cluster.id), '" transform="translate(', x, " ", y,
      ')" tabindex="0" role="button" aria-expanded="', expanded,
      '" aria-label="', esc(cluster.label), "，", count, " 人，点击", expanded ? "收起" : "展开", '">',
      '<circle class="radar-cluster-orb" r="24"></circle><text class="radar-cluster-count" y="5">',
      count, '</text><text class="radar-cluster-label" y="40">', esc(cluster.label), "</text></g>",
    ].join("");
  }
  function svgGroupSource(group, x, y) {
    const id = groupKey(group), route = groupRouteKey(group);
    return [
      '<g class="graph-group-source', selectedGroupId === route ? " selected" : "",
      '" data-group-id="', esc(id), '" data-group-route="', esc(route),
      '" transform="translate(', x, " ", y, ')" tabindex="0" role="button" aria-label="打开',
      esc(groupLabel(group)), '的详情"><title>', esc(groupLabel(group)), " · ", esc(groupGraphStatus(group)),
      '</title><rect width="236" height="46" rx="13"></rect><circle class="graph-source-orb" cx="17" cy="23" r="6"></circle>',
      '<text class="graph-source-title" x="32" y="21">', esc(graphLabel(groupName(group), 14)),
      '</text><text class="graph-source-meta" x="32" y="36">', esc(groupGraphStatus(group)),
      " · ", number(group.member_count), " 人</text></g>",
    ].join("");
  }
  function graphMarkup() {
    if (!data) return "";
    const people = graphPeople();
    const groups = searchedGroups();
    const {visible, hidden, expanded} = graphVisiblePeople(people);
    const roleName = graphRoleName(people);
    const centerX = 560, centerY = 320, innerRadius = 154, outerRadius = 276;
    const sourceGroups = groups.slice(0, 8);
    const sourceRows = sourceGroups.length ? Math.ceil(sourceGroups.length / 4) : 0;
    const sourceTop = 650;
    const height = sourceGroups.length ? sourceTop + sourceRows * 56 + 30 : 650;
    const layout = radarLayout(visible, centerX, centerY);
    const edges = [];
    for (const item of layout) {
      const d = "M " + centerX + " " + centerY + " L " + item.x.toFixed(1) + " " + item.y.toFixed(1);
      if (item.person.private_chat) edges.push('<path class="graph-edge private" d="' + d + '"></path>');
      if (item.person.group_member) edges.push('<path class="graph-edge group" d="' + d + '"></path>');
    }
    const personNodes = layout.map(item => svgRadarPerson(item, layout.length)).join("");
    const clusters = expanded
      ? graphClusters(people).filter(cluster => cluster.id === graphExpandedCluster)
      : graphClusters(hidden);
    const clusterNodes = clusters.map((cluster, index) => {
      const ring = cluster.id === "private" || cluster.id === "both" ? innerRadius + 20 : outerRadius - 8;
      const angle = -Math.PI / 2 + (index + 1) / (clusters.length + 1) * Math.PI * 2;
      return svgCluster(cluster, centerX + Math.cos(angle) * ring, centerY + Math.sin(angle) * ring);
    }).join("");
    const sourceNodes = sourceGroups.map((group, index) => svgGroupSource(group,
      18 + (index % 4) * 270, sourceTop + Math.floor(index / 4) * 56)).join("");
    if (hidden.length > 0 && !expanded) {
      edges.push('<text class="graph-fold-note" x="' + centerX + '" y="' + (outerRadius + centerY + 42)
        + '">' + hidden.length + " 位用户已收进关系簇，点击光团展开</text>");
    }
    const summary = searchTerm
      ? people.length + " 位符合搜索的用户 · "
        + (hidden.length ? "显示 " + visible.length + " 人 + " + clusters.length + " 个关系簇" : "已显示靠近度与连接")
      : people.length + " 位用户 · "
        + (hidden.length && !expanded ? "显示 " + visible.length + " 人 + " + clusters.length + " 个关系簇" : "内圈私聊 · 外圈群聊");
    $("graph-summary").textContent = summary;
    $("graph-empty").hidden = people.length > 0 || groups.length > 0;
    $("graph-empty").replaceChildren(node("strong", searchTerm ? "没有符合的关系" : "还没有认识的人"),
      node("p", searchTerm ? "试试用户名称、账号或群名。" : "发生私聊或群聊互动后，用户会出现在关系雷达中。"));
    if (!people.length && !groups.length) return "";
    const sourceHeight = sourceGroups.length ? sourceTop - 16 : height;
    return [
      '<svg class="relationship-graph-svg radar-graph" viewBox="0 0 1120 ', height,
      '" role="group" aria-label="', esc(summary), '"><desc>关系雷达以当前角色为中心；内圈表示私聊用户，外圈表示群聊用户，光点越靠近中心代表已有互动信号越集中；群内相邻接话仅在群聊详情中查看，不推断成员彼此关系。</desc>',
      '<defs><radialGradient id="radar-backdrop" cx="50%" cy="48%"><stop offset="0" stop-color="var(--teal)" stop-opacity=".13"></stop><stop offset=".62" stop-color="var(--blue)" stop-opacity=".04"></stop><stop offset="1" stop-color="var(--surface-soft)" stop-opacity="0"></stop></radialGradient></defs>',
      '<rect class="radar-backdrop" x="0" y="0" width="1120" height="', sourceHeight, '" rx="18"></rect>',
      '<circle class="radar-ring radar-ring-outer" cx="', centerX, '" cy="', centerY, '" r="', outerRadius, '"></circle>',
      '<circle class="radar-ring radar-ring-inner" cx="', centerX, '" cy="', centerY, '" r="', innerRadius, '"></circle>',
      '<circle class="radar-ring radar-ring-core" cx="', centerX, '" cy="', centerY, '" r="62"></circle>',
      '<path class="radar-crosshair horizontal" d="M ', centerX - outerRadius, " ", centerY,
      " H ", centerX + outerRadius, '"></path><path class="radar-crosshair vertical" d="M ', centerX,
      " ", centerY - outerRadius, " V ", centerY + outerRadius, '"></path>',
      '<g class="radar-sweep"><path d="M ', centerX, " ", centerY, " L ", centerX, " ",
      centerY - outerRadius, '"></path></g>',
      '<text class="radar-zone-label radar-zone-private" x="', centerX - innerRadius + 4,
      '" y="', centerY - innerRadius - 13, '">INNER · 私聊</text>',
      '<text class="radar-zone-label radar-zone-group" x="', centerX + outerRadius - 82,
      '" y="', centerY - 8, '">OUTER · 群聊</text>',
      '<text class="radar-scale-label" x="', centerX, '" y="', centerY + outerRadius + 25,
      '">靠近度 · 近期与高频互动信号</text>',
      edges.join(""),
      '<g class="graph-hub graph-role-hub" transform="translate(', centerX - 96, " ", centerY - 43,
      ')"><rect width="192" height="86" rx="28"></rect><circle class="graph-hub-star" cx="31" cy="43" r="16"></circle>',
      '<text class="hub-label" x="57" y="38">', esc(graphLabel(roleName, 13)),
      '</text><text class="hub-sub" x="57" y="58">关系雷达中心</text></g>',
      personNodes, clusterNodes,
      sourceNodes ? '<g class="graph-source-strip"><text class="graph-strip-title" x="18" y="'
        + (sourceTop - 12) + '">GROUP SOURCES · 群聊入口</text>' + sourceNodes + "</g>" : "",
      "</svg>",
    ].join("");
  }
  function renderNetworkOverview() {
    const people = data ? data.persons.filter(matchesSearch) : [];
    const visibleGroups = allGroups().filter(group => !searchTerm
      || groupSearchText(group).includes(searchTerm)
      || peopleInGroup(group, {honorSearch: false}).some(matchesSearch));
    const kinds = [
      {key: "both", match: person => person.private_chat && person.group_member},
      {key: "private", match: person => person.private_chat && !person.group_member},
      {key: "group", match: person => !person.private_chat && person.group_member},
      {key: "idle", match: person => !person.private_chat && !person.group_member},
    ];
    const maxMessages = Math.max(1, ...people.map(person => number(person.total?.msg_count)));
    const counts = {};
    for (const {key, match} of kinds) {
      const list = $("network-" + key + "-list");
      const tier = list.closest(".network-tier");
      const members = people.filter(match).sort((left, right) =>
        number(right.total?.msg_count) - number(left.total?.msg_count)
        || nameOf(left).localeCompare(nameOf(right), "zh-CN"));
      counts[key] = members.length;
      tier.hidden = key === "idle" && members.length === 0;
      const legendItem = document.querySelector(`.network-legend [data-kind="${key}"]`);
      if (legendItem) legendItem.hidden = key === "idle" && members.length === 0;
      $("network-" + key + "-count").textContent = `${members.length} 人`;
      list.replaceChildren();
      if (!members.length) {
        list.append(node("p", data ? "暂无成员" : "正在读取…", "network-empty"));
        continue;
      }
      members.forEach(person => {
        const card = node("button", undefined, "network-person-card");
        card.type = "button";
        card.setAttribute("aria-label", `查看 ${nameOf(person)} 的用户资料`);
        const stats = person.total || {};
        const details = node("span", undefined, "network-person-meta");
        details.append(node("span", `${number(stats.msg_count)} 条`),
          node("span", `${number(stats.active_days)} 天`),
          node("span", `最近 ${shortTime(stats.last_seen)}`));
        const groupCount = groupIdsOf(person).length;
        if (groupCount) details.append(node("span", `共 ${groupCount} 群`));
        const meter = node("span", undefined, "network-person-meter");
        const fill = node("span");
        fill.style.width = `${Math.max(0, Math.min(100, number(stats.msg_count) / maxMessages * 100))}%`;
        meter.append(fill);
        card.append(node("strong", nameOf(person), "network-person-name"), details, meter);
        card.addEventListener("click", () => navigateToPerson(personRouteKey(person)));
        list.append(card);
      });
    }
    const total = people.length;
    $("nav-network-count").textContent = counts.idle ? "4 层" : "3 层";
    $("network-layer-summary").textContent = data ? `${total} 位用户` : "正在读取";
    $("network-summary-label").textContent = data
      ? `${total} 位用户 · ${counts.both} / ${counts.private} / ${counts.group}${counts.idle ? ` / ${counts.idle}` : ""}`
      : "正在读取用户目录";
    const groupTarget = $("network-groups");
    groupTarget.replaceChildren();
    $("network-groups-count").textContent = `${visibleGroups.length} 个群`;
    if (!visibleGroups.length) {
      groupTarget.append(node("p", data ? "暂无群聊记录" : "正在读取…", "network-empty"));
      return;
    }
    visibleGroups.forEach(group => {
      const card = node("button", undefined, "network-group-card");
      card.type = "button";
      card.setAttribute("aria-label", `查看 ${groupName(group)} 的群聊详情`);
      const head = node("span", undefined, "network-group-head");
      head.append(node("strong", groupName(group)), badge(groupStatusLabel(group),
        group.configured ? "group" : group.discovered ? "pending" : "history"));
      const details = node("span", undefined, "network-group-meta");
      const memberCount = group.network?.member_count ?? group.member_count;
      details.append(node("span", `群号 ${groupKey(group)}`),
        node("span", `${number(memberCount)} 人`),
        node("span", `${number(group.network?.interaction_count) || number(group.msg_count)} 条`));
      card.append(head, details);
      card.addEventListener("click", () => navigateToGroup(groupKey(group), groupRouteKey(group)));
      groupTarget.append(card);
    });
  }
  function renderGraph() {
    $("relationship-graph").innerHTML = graphMarkup();
  }
  function updateGraphFilters() {
    document.querySelectorAll(".graph-filter").forEach((button) => {
      const active = button.dataset.graphScope === graphScope;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }
  function updateGroupFilters() {
    document.querySelectorAll(".group-status-filter").forEach((button) => {
      const active = button.dataset.groupFilter === groupStatusFilter;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }
  function updateGroupOverview(groups) {
    const list = Array.isArray(groups) ? groups : [];
    const count = (key) => list.filter((group) => groupStatusKey(group) === key).length;
    const messages = list.reduce((sum, group) => sum + number(group.network?.interaction_count)
      + number(group.msg_count), 0);
    const values = {
      "group-summary-total": list.length,
      "group-summary-configured": count("configured"),
      "group-summary-pending": count("pending"),
      "group-summary-history": count("history"),
      "group-summary-messages": messages,
    };
    Object.entries(values).forEach(([id, value]) => { const target = $(id); if (target) target.textContent = String(value); });
  }

  function renderGroups({preserveDetail = false} = {}) {
    const list = $("groups-list");
    list.replaceChildren();
    const groups = allGroups().filter((group) => {
      if (groupStatusFilter !== "all" && groupStatusKey(group) !== groupStatusFilter) return false;
      if (!searchTerm) return true;
      return groupSearchText(group).includes(searchTerm)
        || peopleInGroup(group, { honorSearch: false }).some(matchesSearch);
    });
    $("groups-result-count").textContent = `${groups.length} 个群`;
    updateGroupOverview(allGroups());
    updateGroupFilters();
    if (!groups.length) {
      const empty = node("div", undefined, "empty-state");
      const title = data ? searchTerm ? "没有符合的群聊记录"
        : groupStatusFilter === "configured" ? "当前没有已接入群聊"
        : groupStatusFilter === "pending" ? "当前没有待处理群聊"
        : groupStatusFilter === "history" ? "当前没有历史群聊" : "当前没有可显示的群聊" : "正在读取群聊目录…";
      empty.append(node("strong", title),
        node("p", data ? "目录显示当前目标、AstrBot 已发现会话和 V2 历史往来。" : ""));
      list.append(empty);
      if (!preserveDetail || !selectedGroupId) renderGroupDetail(null);
      return;
    }
    groups.forEach((group) => {
      const id = groupKey(group);
      const route = groupRouteKey(group);
      const card = node("button", undefined, "group-card");
      card.type = "button";
      card.dataset.groupId = id;
      card.dataset.groupStatus = groupStatusKey(group);
      card.setAttribute("aria-pressed", String(selectedGroupId === route));
      const info = node("span", undefined, "group-info");
      card.title = groupLabel(group);
      const nameLine = node("span", undefined, "group-name-line");
      nameLine.append(node("strong", groupName(group)), badge(groupStatusLabel(group), group.configured ? "group" : group.discovered ? "pending" : "history"));
      info.append(nameLine,
        node("small", `群号 ${id} · ${groupListStatus(group)}${group.account_name || group.account_id ? ` · ${accountLabel(group)}` : ""}${group.role_id ? ` · ${groupRoleLabel(group)}` : ""}`));
      const stats = node("span", undefined, "group-stats");
      const rosterCount = group.network?.member_count ?? group.member_count;
      const unconfiguredSession = group.discovered && !group.configured;
      stats.append(node("span", `${number(rosterCount)} 位${unconfiguredSession ? "已记录成员" : "群成员"}`),
        node("span", `${number(group.msg_count)} 条${unconfiguredSession ? " V2 记录" : "消息"}`),
        node("span", group.network?.updated_at ? `最近 ${shortTime(group.network.updated_at)}` : "尚无最近观察", "group-last-seen"));
      card.append(info, stats);
      card.addEventListener("click", () => selectGroup(group));
      list.append(card);
    });
    const selectedGroup = groups.find((group) => groupRouteKey(group) === selectedGroupId) || null;
    if (!selectedGroup) selectedGroupId = "";
    if (!preserveDetail || !selectedGroup) renderGroupDetail(selectedGroup);
  }

  function renderGroupDetail(group) {
    const panel = $("group-detail");
    panel.replaceChildren();
    if (!group) {
      const empty = node("div", undefined, "placeholder");
      empty.append(node("span", "02", "placeholder-mark"), node("h3", data ? "选择一个群聊" : "群聊信息暂不可用"),
        node("p", "查看群内已记录的相识成员及往来数量。"));
      panel.append(empty);
      return;
    }
    const id = groupKey(group);
    const members = groupRoster(group);
    const network = group.network || null;
    const edges = Array.isArray(network?.edges) ? network.edges : [];
    const unmatchedClues = Array.isArray(network?.unmatched_clues) ? network.unmatched_clues : [];
    const observedMessages = (network?.members || []).reduce((sum, member) =>
      sum + number(member.group_observation?.message_count), 0);
    const activeMembers = members.filter(({member}) => number(member.group_observation?.message_count) > 0);
    const detail = node("div", undefined, "group-detail-inner");
    const head = node("header", undefined, "group-detail-head group-detail-hero");
    const title = node("div");
    title.append(node("span", "GROUP SPACE", "eyebrow"), node("h3", groupName(group)), node("small", `群号 ${id}`),
      node("small", `${groupDetailStatus(group)}${group.role_id ? ` · ${groupRoleLabel(group)}` : ""}`, "detail-subtitle"));
    const routeFacts = node("div", undefined, "group-route-facts");
    routeFacts.append(node("span", group.platform_id || group.platform || "平台待确认"),
      node("span", group.account_name || group.account_id || "账号待确认"),
      node("span", groupRoleLabel(group)));
    const stateBadge = badge(groupStatusLabel(group), group.configured ? "group" : group.discovered ? "pending" : "history");
    const statusStack = node("div", undefined, "group-detail-status-stack");
    statusStack.append(stateBadge, routeFacts);
    head.append(title, statusStack);
    const metrics = node("div", undefined, "group-detail-metrics");
    const metric = (label, value) => { const cell = node("div"); cell.append(node("span", label), node("strong", value)); metrics.append(cell); };
    metric(group.discovered && !group.configured ? "V2 成员记录" : "群聊成员", `${members.length} 人`);
    metric("已观察发言", `${observedMessages} 条`);
    metric("互动边", `${edges.length} 条`);
    metric("短时接话", `${number(network?.interaction_count)} 次`);

    detail.append(head, metrics);
    const access = node("section", undefined, "detail-section group-access-section");
      const accessHead = node("div", undefined, "group-members-head");
      accessHead.append(node("h4", "V2 群聊接入"), badge(group.configured ? "已接入" : "未接入",
        group.configured ? "group" : ""));
    access.append(accessHead, node("p", group.configured
      ? "这个群已随账号下的角色映射自动接入。"
      : group.discovered ? "AstrBot 已发现这个群会话；账号下的人格归入角色后会自动接入。"
        : "这是历史往来记录，等待 AstrBot 再次发现这个群会话。",
    "auth-empty"), node("p", "成员线索和熟人卡只用于认识群友，不决定群聊是否接入，也不会逐个授予权限。",
    "auth-empty"));
    const actions = node("div", undefined, "group-access-actions");
    const block = groupTargetBlock(group);
    if (block) {
      actions.append(node("p", block.message, "auth-empty"));
      if (block.tab) actions.append(managementButton(block.tab, block.label));
    } else if (group.discovered && !group.configured) {
      actions.append(node("p", "请在“账号与会话”中把该账号的人格归入角色；保存后这里会自动更新。", "auth-empty"));
      actions.append(managementButton("host", "账号与会话"));
    }
    if (group.configured && group.role_id) {
      const replySettings = node("button", "本群回应设置", "secondary group-access-button");
      replySettings.type = "button";
      replySettings.addEventListener("click", () => window.parent?.postMessage?.({
        channel: "companion-workspace", kind: "navigate",
        target: {tab: "group-reply", platform_id: group.platform_id, bot_id: group.bot_id,
          account_id: group.account_id, group_id: id, role_id: group.role_id},
      }, "*"));
      actions.append(replySettings);
      const groupArchive = node("button", "群公共档案", "secondary group-access-button");
      groupArchive.type = "button";
      groupArchive.addEventListener("click", () => window.parent?.postMessage?.({
        channel: "companion-workspace", kind: "navigate",
        target: {tab: "group-memory", platform_id: group.platform_id, bot_id: group.bot_id,
          account_id: group.account_id, group_id: id, role_id: group.role_id},
      }, "*"));
      actions.append(groupArchive);
    }
    access.append(actions);
    detail.append(access);
    if (network || group.configured) {
      const activitySection = node("section", undefined, "detail-section group-activity-section");
      const activityHead = node("div", undefined, "group-members-head");
      activityHead.append(node("h4", "群聊近况"),
        node("span", network?.updated_at ? `最近观察 ${dateTime(network.updated_at)}` : "尚无观察时间"));
      activitySection.append(activityHead);
      const recent = groupRecentActivity(network);
      if (recent.length) {
        const list = node("div", undefined, "group-activity-list");
        recent.forEach((item) => {
          const row = node("article", undefined, "group-activity-item");
          const meta = node("div", undefined, "group-activity-meta");
          meta.append(node("strong", item.name), node("time", dateTime(item.at)));
          row.append(meta, node("p", item.text));
          list.append(row);
        });
        activitySection.append(list);
        activitySection.append(node("p", "摘录来自 V2 已记录的群聊发言，不代表完整消息流。", "group-activity-note"));
      } else {
        activitySection.append(node("p", group.configured
          ? "V2 尚未记录到近期群聊发言。收到群消息后，这里会显示近期活动。"
          : "该群尚未接入 V2，目前没有可展示的群聊活动。", "group-activity-empty"));
      }
      if (activeMembers.length) {
        const activeSection = node("section", undefined, "group-active-section");
        const activeHead = node("div", undefined, "group-members-head");
        activeHead.append(node("h4", "活跃群友"), node("span", `按已观察发言排序 · ${activeMembers.length} 人`));
        const rows = node("div", undefined, "group-active-list");
        activeMembers.slice(0, 6).forEach(({member}) => {
          const row = node("div", undefined, "group-active-row");
          const name = member.display_name || `成员 ${member.native_account_id}`;
          row.append(node("strong", name), node("small",
            `${number(member.group_observation?.message_count)} 条 · ${number(member.group_observation?.active_days)} 天 · 最近 ${dateTime(member.group_observation?.last_seen)}`));
          rows.append(row);
        });
        activeSection.append(activeHead, rows);
        activitySection.append(activeSection);
      }
      detail.append(activitySection);
    }
    if (network && members.length) {
      const networkSection = node("section", undefined, "detail-section member-network-section");
      const networkHead = node("div", undefined, "group-members-head");
      networkHead.append(node("h4", "成员互动"), node("span", `${edges.length} 条边`));
      const viewport = node("div", undefined, "member-network-viewport");
      viewport.innerHTML = memberNetworkMarkup(network, members);
      viewport.addEventListener("click", (event) => {
        const target = event.target?.closest?.("[data-member-account]");
        if (target) openGroupMember(group, target.dataset.memberAccount);
      });
      viewport.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        const target = event.target?.closest?.("[data-member-account]");
        if (!target || target.getAttribute("role") !== "button") return;
        event.preventDefault();
        openGroupMember(group, target.dataset.memberAccount);
      });
      networkSection.append(networkHead, viewport,
        node("p", "边只记录 180 秒内相邻成员发言的次数、最近时间和表达氛围，不代表现实关系。", "group-network-caveat"));
      const edgeList = interactionRows(network, members);
      if (edgeList) networkSection.append(edgeList);
      detail.append(networkSection);
    }

    const memberSection = node("section", undefined, "detail-section");
    const memberHead = node("div", undefined, "group-members-head");
    memberHead.append(node("h4", "成员与熟人卡"), node("span", `${members.length} 位 V2 已记录成员`));
    const roster = node("div", undefined, "group-members");
    if (!members.length) {
      const emptyText = group.discovered && !group.configured
        ? "AstrBot 已发现该群会话；V2 尚无群成员记录。"
        : group.configured ? "当前没有群内成员记录。" : "当前目录中没有该群的成员明细。";
      const empty = node("p", emptyText, "auth-empty");
      roster.append(empty);
    }
    members.forEach(({member, person}) => {
      const name = person ? nameOf(person) : member.display_name || `未命名成员 ${member.native_account_id}`;
      const row = node(person ? "button" : "div", undefined, "member-chip" + (person ? "" : " is-readonly"));
      if (person) row.type = "button";
      const initial = node("span", Array.from(name)[0] || "？", "member-avatar");
      initial.style.setProperty("--hue", String(hue(member.native_account_id)));
      const copy = node("span", undefined, "member-copy");
      copy.append(node("strong", name));
      const observation = member.group_observation || {};
      const sourceLabel = observation.basis === "acquaintance_card" ? "熟人卡群内记录" : "关系网观察";
      copy.append(node("small", `${sourceLabel} ${number(observation.message_count)} 条 · ${number(observation.active_days)} 天`));
      const clues = (member.clues || []).slice(0, 6);
      if (clues.length) {
        const clueSummary = clues.map((clue) => `${clue.name || "未命名"} · ${clue.basis === "declared_account" ? "账号声明" : "名称唯一匹配"}`);
        copy.append(node("small", `成员线索：${clueSummary.join("；")}`, "member-clue-label"));
      }
      const acquaintance = member.acquaintance || {};
      const cardMeta = `熟人卡 ${number(acquaintance.message_count)} 条${acquaintance.private_chat ? " · 有私聊" : ""}`;
      row.append(initial, copy, node("small", cardMeta));
      if (person) row.addEventListener("click", () => openPerson(person));
      roster.append(row);
    });
    memberSection.append(memberHead, roster);
    detail.append(memberSection);
    if (unmatchedClues.length) {
      const clueSection = node("section", undefined, "detail-section unresolved-clues");
      const clueHead = node("div", undefined, "group-members-head");
      clueHead.append(node("h4", "待确认成员线索"), node("span", `${unmatchedClues.length} 条`));
      clueSection.append(clueHead);
      unmatchedClues.forEach((clue) => {
        const row = node("div", undefined, "unresolved-clue");
        const explanation = clue.basis === "ambiguous_display_label"
          ? `名称对应多位成员：${(clue.candidate_account_ids || []).join("、")}` : "尚未对应到群内账号";
        const declared = (clue.declared_account_ids || []).join("、");
        const attribution = clue.basis === "unmatched" && declared ? ` · 线索声明账号 ${declared}` : "";
        row.append(node("strong", clue.name || "未命名线索"), node("small", explanation + attribution));
        if (clue.content) row.append(node("p", clue.content));
        clueSection.append(row);
      });
      detail.append(clueSection);
    }
    panel.append(detail);
  }

  function groupRoster(group) {
    const rows = new Map();
    const people = peopleInGroup(group);
    const personFor = (account) => people.find((person) => String(person.native_account_id) === String(account));
    for (const member of group.network?.members || []) {
      if (!member?.native_account_id) continue;
      rows.set(String(member.native_account_id), {member, person: personFor(member.native_account_id) || null});
    }
    for (const person of people) {
      const account = String(person.native_account_id || "");
      if (!account || rows.has(account)) continue;
      const record = groupsOf(person)[groupKey(group)] || {};
      rows.set(account, {member: {native_account_id: account, display_name: nameOf(person), clues: [],
        group_observation: {basis: "acquaintance_card", message_count: record.msg_count,
          active_days: record.active_days},
        acquaintance: {message_count: person.total?.msg_count, private_chat: person.private_chat}}, person});
    }
    return [...rows.values()].sort((a, b) => number(b.member.group_observation?.message_count)
      - number(a.member.group_observation?.message_count)
      || String(a.member.display_name || "").localeCompare(String(b.member.display_name || ""), "zh-CN"));
  }

  function memberNetworkMarkup(network, roster) {
    const members = roster.slice(0, 20);
    const columns = [members.filter((_, index) => index % 2 === 0), members.filter((_, index) => index % 2 === 1)];
    const positions = new Map();
    columns.forEach((items, column) => items.forEach((item, index) => positions.set(
      String(item.member.native_account_id), {x: column ? 452 : 28, y: 42 + index * 66, item})));
    const height = Math.max(144, 78 + Math.max(...columns.map((items) => items.length), 0) * 66);
    const edges = (network.edges || []).filter((edge) => positions.has(String(edge.a)) && positions.has(String(edge.b)));
    const paths = edges.slice(0, 4).map((edge) => {
      const a = positions.get(String(edge.a)), b = positions.get(String(edge.b));
      const first = a.x < b.x ? a : b, second = a.x < b.x ? b : a;
      const width = Math.min(5, 1 + Math.log2(Math.max(1, number(edge.count))));
      return `<path class="member-network-edge tone-${esc(edge.tone || "ordinary")}" d="M ${first.x + 204} ${first.y} C 296 ${first.y}, 384 ${second.y}, ${second.x} ${second.y}" style="stroke-width:${width}"><title>${esc(edge.summary || "近期连续接话")} · ${number(edge.count)} 次 · ${esc(shortTime(edge.last_seen))}</title></path>`;
    }).join("");
    const nodes = [...positions.values()].map(({x, y, item}) => {
      const member = item.member, account = String(member.native_account_id);
      const name = member.display_name || `成员 ${account}`;
      const recent = member.group_observation?.last_seen ? `最近 ${shortTime(member.group_observation.last_seen)}` : "群内记录";
      return `<g class="member-network-node${item.person ? " is-openable" : ""}" data-member-account="${esc(account)}" tabindex="${item.person ? "0" : "-1"}" role="${item.person ? "button" : "img"}" aria-label="${esc(name)} · ${number(member.group_observation?.message_count)} 条群内记录">
        <rect x="${x}" y="${y - 22}" width="204" height="44" rx="5"></rect><circle cx="${x + 17}" cy="${y}" r="11" fill="hsl(${hue(account)} 42% 43%)"></circle>
        <text class="member-network-initial" x="${x + 17}" y="${y + 4}">${esc(Array.from(name)[0] || "？")}</text>
        <text class="member-network-name" x="${x + 34}" y="${y - 2}">${esc(graphLabel(name, 15))}</text>
        <text class="member-network-meta" x="${x + 34}" y="${y + 14}">${number(member.group_observation?.message_count)} 条 · ${esc(recent)}</text></g>`;
    }).join("");
    return `<svg class="member-network-svg" viewBox="0 0 684 ${height}" role="group" aria-label="群内成员互动图"><desc>连线仅表示短时相邻接话，不推断现实关系。</desc>${paths}${nodes}</svg>`;
  }

  function interactionRows(network, roster) {
    const byAccount = new Map(roster.map(({member}) => [String(member.native_account_id),
      member.display_name || `成员 ${member.native_account_id}`]));
    const edges = (network.edges || []).slice(0, 80);
    if (!edges.length) return null;
    const renderRows = (items) => {
      const list = node("div", undefined, "interaction-rows");
      items.forEach((edge) => {
        const tone = {ordinary: "接话", banter: "玩笑", tension: "紧绷"}[edge.tone] || "接话";
        const row = node("div", undefined, "interaction-row tone-" + (edge.tone || "ordinary"));
        row.append(node("strong", `${byAccount.get(String(edge.a)) || edge.a} ↔ ${byAccount.get(String(edge.b)) || edge.b}`),
          node("small", `${number(edge.count)} 次 · ${tone} · 最近 ${shortTime(edge.last_seen)}`));
        list.append(row);
      });
      return list;
    };
    const section = node("div", undefined, "interaction-list");
    section.append(renderRows(edges.slice(0, 4)));
    if (edges.length > 4) {
      const details = node("details", undefined, "interaction-more");
      details.append(node("summary", `其余 ${edges.length - 4} 条互动`), renderRows(edges.slice(4)));
      section.append(details);
    }
    return section;
  }

  function groupRecentActivity(network) {
    const entries = [];
    for (const member of network?.members || []) {
      const name = member.display_name || `成员 ${member.native_account_id || ""}`;
      for (const item of member.group_observation?.recent || []) {
        const text = typeof item?.text === "string" ? item.text.trim() : "";
        if (text) entries.push({name, text, at: item.at || member.group_observation?.last_seen || ""});
      }
    }
    return entries.sort((a, b) => (Date.parse(b.at) || 0) - (Date.parse(a.at) || 0)).slice(0, 8);
  }

  function openGroupMember(group, account) {
    const person = groupRoster(group).find((row) => String(row.member.native_account_id) === String(account))?.person;
    if (person) {
      openPerson(person);
      window.CompanionPrivateMembers?.selectPane("relationship");
    }
  }

  function openPerson(person) {
    filter = personFilter(person) || "stable";
    searchTerm = "";
    $("directory-search").value = "";
    $("clear-search").hidden = true;
    updateFilterButtons();
    switchTab("people");
    select(person);
  }

  function renderCounts() {
    const counts = data?.counts;
    $("count-total").textContent = counts ? String(number(counts.total)) : "—";
    const stableCount = data?.persons.filter(person => personFilter(person) === "stable").length;
    const temporaryCount = data?.persons.filter(person => person.group_member).length;
    $("count-private").textContent = counts ? String(number(counts.private_chat)) : "—";
    $("count-group").textContent = counts ? String(number(counts.group_member)) : "—";
    $("count-both").textContent = counts ? String(number(counts.both)) : "—";
    $("count-split").textContent = counts ? `${number(counts.private_chat)} · ${number(counts.group_member)} · ${number(counts.both)}` : "—";
    $("profile").textContent = data ? (data.profile_id || "当前陪伴记录") : "—";
    $("operator").textContent = data?.operator_name ?? "—";
    $("chip-stable").textContent = String(stableCount || 0);
    $("chip-temporary").textContent = String(temporaryCount || 0);
    $("chip-discovered").textContent = String(data?.persons.filter(person => !knownPerson(person)).length || 0);
    $("nav-people-count").textContent = `${number(counts?.total)} 位`;
    $("nav-groups-count").textContent = `${allGroups().length} 个`;
  }
  function updateFilterButtons() {
    document.querySelectorAll(".chip").forEach((button) => {
      const active = button.dataset.filter === filter;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }
  function renderAll({preserveDetails = false} = {}) {
    renderCounts();
    renderNetworkOverview();
    renderGraph();
    renderPeople();
    renderGroups({preserveDetail: preserveDetails});
    if (!preserveDetails) renderDetail();
    updateGraphFilters();
    updateFilterButtons();
  }
  function personRouteKey(person) {
    return JSON.stringify([person.subject_id, person.platform, person.bot_id, person.account_id, person.role_id]);
  }
  function renderDetail() {
    const panel = $("person-detail");
    if (!selected) {
      panel.replaceChildren(node("div", "选择一位成员", "placeholder"));
      return;
    }
    if (!window.CompanionPrivateMembers) {
      panel.replaceChildren(node("p", "成员编辑器未加载，请刷新页面。", "notice error"));
      return;
    }
    window.CompanionPrivateMembers.mount(panel, selected, {
      bridge, groupLabel: id => groupLabel(groupById(id)),
      openGroup: id => navigateToGroup(id),
      onChanged(person, patch, {identity = false} = {}) {
        if (!data) return;
        memberChangeSequence++;
        const key = displayPersonKey(person);
        const routeIdentity = route => JSON.stringify([route.platform, route.bot_id, route.account_id, route.role_id]);
        const updateDisplay = item => {
          const updated = {...item, ...patch};
          if (identity || !Array.isArray(item.display_routes)) return updated;
          const selectedRoute = routeIdentity(person);
          updated.display_routes = item.display_routes.map(route => routeIdentity(route) === selectedRoute
            ? {...route, ...patch} : route);
          if (patch.revision !== undefined) {
            updated.revision = Math.max(0, ...updated.display_routes.map(route => number(route.revision)));
          }
          return updated;
        };
        data.persons = data.persons.map(item => identity && item.subject_id === person.subject_id
          ? {...item, identity_tier: patch.identity_tier}
          : displayPersonKey(item) === key ? updateDisplay(item) : item);
        if (selected && displayPersonKey(selected) === key) {
          Object.assign(selected, updateDisplay(selected));
        }
        renderPeople();
      },
    });
  }
  function select(person) {
    selected = person;
    renderPeople();
    renderDetail();
  }
  function selectGroup(group) {
    selectedGroupId = groupRouteKey(group);
    renderGroups();
  }
  function switchTab(next) {
    if (!["network", "people", "groups"].includes(next)) return;
    activeView = next;
    document.querySelector(".relationship-app").dataset.view = next;
    const pageMeta = {
      network: {name: "关系网", description: "私聊与群聊往来总览", refresh: "重新读取关系网"},
      people: {name: "用户", description: "用户资料与联系权限", refresh: "重新读取用户目录"},
      groups: {name: "群聊", description: "群聊接入与成员往来", refresh: "重新读取群聊目录"},
    }[next];
    const pageTitle = $("page-title");
    if (pageTitle) pageTitle.textContent = pageMeta.name;
    const pageDescription = $("page-description");
    if (pageDescription) pageDescription.textContent = pageMeta.description;
    const documentTitle = $("document-title");
    if (documentTitle) documentTitle.textContent = `${pageMeta.name} · 陪伴`;
    const refresh = $("refresh");
    if (refresh) refresh.title = pageMeta.refresh;
    /* 新用户默认权限是「用户」目录自己的规则，入口跟着标题走；其他两个视图
       是关系总览和群聊，放这个按钮只会让人误以为它管的是当前视图。 */
    const access = $("open-private-access");
    if (access) access.hidden = next !== "people";
    if (next === "people") void loadStrangers();
    const currentNotice = $("notice");
    currentNotice.hidden = !currentNotice.textContent || Boolean(noticeView && noticeView !== next);
    document.querySelectorAll(".tab").forEach((item) => {
      const active = item.dataset.tab === next;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", String(active));
      item.tabIndex = active ? 0 : -1;
    });
    document.querySelectorAll(".view-panel").forEach((panel) => {
      const active = panel.id === "panel-" + next;
      panel.hidden = !active;
      panel.classList.toggle("active", active);
      if (active) { panel.classList.remove("is-entering"); void panel.offsetWidth; panel.classList.add("is-entering"); }
    });
  }
  function navigateToPerson(id) {
    const person = data?.persons.find((item) => personRouteKey(item) === id || item.subject_id === id);
    if (!person) return;
    filter = personFilter(person) || "stable";
    updateFilterButtons();
    switchTab("people");
    select(person);
  }
  function navigateToGroup(id, route = "") {
    const groups = allGroups();
    const group = groups.find((item) => route && groupRouteKey(item) === route)
      || groups.find((item) => groupKey(item) === id);
    if (!group) return;
    switchTab("groups");
    selectGroup(group);
  }
  function activateGraphNode(target) {
    const person = target?.closest?.("[data-person-id]");
    if (person) { navigateToPerson(person.dataset.personId); return; }
    const cluster = target?.closest?.("[data-graph-cluster]");
    if (cluster) {
      graphExpandedCluster = graphExpandedCluster === cluster.dataset.graphCluster ? "" : cluster.dataset.graphCluster;
      renderGraph();
      return;
    }
    const group = target?.closest?.("[data-group-id]");
    if (group) navigateToGroup(group.dataset.groupId, group.dataset.groupRoute);
  }
  function applyDirectory(result, {background = false} = {}) {
    const persons = mergeDirectoryPeople(Array.isArray(result.persons) ? result.persons : []);
    const privateCount = persons.filter(isPrivateUser).length;
    const groupCount = persons.filter(item => item.group_member).length;
    const bothCount = persons.filter(item => isPrivateUser(item) && item.group_member).length;
    data = { ...result, persons, groups: Array.isArray(result.groups) ? result.groups : [],
      counts: {...(result.counts || {}), total: persons.filter(knownPerson).length, private_chat: privateCount,
        group_member: groupCount, both: bothCount, private_only: privateCount - bothCount,
        group_only: groupCount - bothCount} };
    const previous = selected ? displayPersonKey(selected) : null;
    selected = data.persons.find(person => displayPersonKey(person) === previous) || visiblePeople()[0] || null;
    if (!background) window.CompanionPrivateMembers?.refresh();
    const foundGroup = selectedGroupId && allGroups().some((group) => groupRouteKey(group) === selectedGroupId);
    if (!foundGroup) selectedGroupId = "";
    renderAll({preserveDetails: background});
    if (background && previous && (!selected || displayPersonKey(selected) !== previous)) renderDetail();
  }

  function strangerRow(item) {
    const row = document.createElement("li");
    row.className = "stranger-row";
    const who = document.createElement("strong");
    who.textContent = `账号 ${item.peer || "未知"} · 收到方 ${item.login || "未知"}`;
    const outcomes = {
      stranger_notice_sent: "已回婉拒",
      stranger_notice_disabled: "未回（婉拒已关闭）",
      stranger_notice_cooldown: "未回（冷却中）",
      stranger_notice_daily_cap: "未回（超每日上限）",
      stranger_notice_delivery_unavailable: "未回（通道不可用）",
      stranger_notice_send_failed: "未回（发送失败）",
    };
    const meta = document.createElement("small");
    const seen = item.last_seen_at ? new Date(item.last_seen_at).toLocaleString() : "未知";
    meta.textContent = `${outcomes[item.last_outcome] || "未回"} · 共 ${Number(item.attempts) || 0} 次 · 最近 ${seen}`;
    row.append(who, meta);
    return row;
  }
  /* 被拒私聊不在目录里（没有身份/路由），但管理员需要看到“谁在敲门”。
     只读、有界，账号与时间而已，不在页面里提供授予入口。 */
  async function loadStrangers() {
    const section = $("stranger-contacts");
    if (!section || !bridge) return;
    try {
      const payload = unwrap(await bridge.apiGet("acquaintance/strangers", {}));
      const items = Array.isArray(payload?.items) ? payload.items : [];
      const list = $("stranger-list");
      if (list) list.replaceChildren(...items.map(strangerRow));
      const count = $("stranger-count");
      if (count) count.textContent = `${items.length} 人`;
      section.hidden = !items.length;
    } catch {
      section.hidden = true;
    }
  }
  async function supplementDirectory(sequence, edits) {
    try {
      const result = unwrap(await bridge.apiGet(endpoint, {}));
      if (sequence !== requestSequence) return;
      if (edits !== memberChangeSequence) {
        setSyncState("ready", "本地关系已读取");
        return;
      }
      applyDirectory(result, {background: true});
      setSyncState("ready", "V2 数据已同步");
    } catch (error) {
      if (sequence !== requestSequence) return;
      const failure = localized(error);
      if (["dashboard_identity_required", "core_access_required", "staging_not_initialized",
           "staging_host_terminated"].includes(error?.code)) {
        data = null;
        selected = null;
        selectedGroupId = "";
        renderAll();
        setSyncState("error", "读取未完成");
        notice(failure.message, true);
        return;
      }
      // The local records remain usable when optional native discovery fails.
      setSyncState("ready", "本地关系已读取，补充资料暂未更新");
    }
  }

  async function load({preserveNotice = false} = {}) {
    const sequence = ++requestSequence;
    const refresh = $("refresh");
    refresh.disabled = true;
    refresh.classList.add("is-loading");
    setSyncState("", "正在读取");
    try {
      const result = unwrap(await bridge.apiGet(endpoint, {read_mode: "stored"}));
      if (sequence !== requestSequence) return;
      applyDirectory(result);
      void loadStrangers();
      setSyncState("ready", result.directory_refresh_pending ? "本地关系已读取，正在更新补充资料" : "V2 数据已同步");
      if (!preserveNotice) notice("");
      if (result.directory_refresh_pending) void supplementDirectory(sequence, memberChangeSequence);
      return true;
    } catch (error) {
      if (sequence !== requestSequence) return;
      const failure = localized(error);
      setSyncState("error", "读取未完成");
      if (preserveNotice) {
        const previous = $("notice").textContent.replace(/[。.!?]+$/, "");
        notice(`${previous}；目录重读失败：${failure.message}`, true);
      } else {
        data = null;
        selected = null;
        selectedGroupId = "";
        renderAll();
        notice(failure.message, true);
      }
      return false;
    } finally {
      if (sequence === requestSequence) {
        refresh.disabled = false;
        refresh.classList.remove("is-loading");
      }
    }
  }

  document.querySelectorAll(".tab").forEach((item) => {
    item.addEventListener("click", () => switchTab(item.dataset.tab));
    item.addEventListener("keydown", (event) => {
      const tabs = [...document.querySelectorAll(".tab")];
      const index = tabs.indexOf(item);
      const next = event.key === "ArrowRight" ? (index + 1) % tabs.length
        : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length
        : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1;
      if (next < 0) return;
      event.preventDefault();
      switchTab(tabs[next].dataset.tab);
      tabs[next].focus();
    });
  });
  document.querySelectorAll(".chip").forEach((item) => item.addEventListener("click", () => {
    filter = item.dataset.filter;
    updateFilterButtons();
    if (selected && !visiblePeople().some((person) => personRouteKey(person) === personRouteKey(selected))) select(null);
    renderPeople();
  }));
  const idleToggle = $("hide-idle-users");
  if (idleToggle) idleToggle.addEventListener("change", () => {
    hideIdleUsers = Boolean(idleToggle.checked);
    saveDirectoryPreference();
    if (selected && !visiblePeople().some((person) => personRouteKey(person) === personRouteKey(selected))) selected = null;
    renderPeople();
    renderDetail();
  });
  document.querySelectorAll(".group-status-filter").forEach((item) => item.addEventListener("click", () => {
    groupStatusFilter = item.dataset.groupFilter || "all";
    updateGroupFilters();
    renderGroups();
  }));
  document.querySelectorAll(".graph-filter").forEach((item) => item.addEventListener("click", () => {
    graphScope = item.dataset.graphScope;
    graphExpandedCluster = "";
    updateGraphFilters();
    renderGraph();
  }));
  $("directory-search").addEventListener("input", (event) => {
    searchTerm = String(event.target.value || "").trim().toLocaleLowerCase();
    graphExpandedCluster = "";
    $("clear-search").hidden = !searchTerm;
    renderNetworkOverview(); renderGraph(); renderPeople(); renderGroups();
    if (selected && !visiblePeople().some((person) => personRouteKey(person) === personRouteKey(selected))) select(null);
  });
  $("clear-search").addEventListener("click", () => {
    $("directory-search").value = "";
    searchTerm = "";
    graphExpandedCluster = "";
    $("clear-search").hidden = true;
    renderNetworkOverview(); renderGraph(); renderPeople(); renderGroups();
  });
  $("relationship-graph").addEventListener("click", (event) => activateGraphNode(event.target));
  $("relationship-graph").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const target = event.target?.closest?.("[data-person-id], [data-graph-cluster], [data-group-id]");
    if (!target) return;
    event.preventDefault();
    activateGraphNode(target);
  });
  $("refresh").addEventListener("click", () => { notice(""); load(); });
  /* 这里只做转发：设置弹窗由外壳（陪伴小屋）统一渲染，工作区里再画一份会
     出现两套配置控件争同一个 sparse owner。 */
  $("open-private-access")?.addEventListener("click", () => {
    parent.postMessage({channel: "companion-workspace", kind: "open-settings", target: "private-access"}, "*");
  });
  document.addEventListener("companion-mappings-change", () => { if (bridge) void load(); });
  document.addEventListener("companion-navigate-failed", (event) => {
    notice(event.detail?.message || "目标入口暂时不可用，请重新读取群聊目录后再试。", true);
  });
  (async function start() {
    window.lucide?.createIcons();
    readDirectoryPreferences();
    switchTab(activeView);
    bridge = window.AstrBotPluginPage;
    if (!bridge) { setSyncState("error", "页面桥未连接"); notice(errors.dashboard_identity_required, true); return; }
    try { await bridge.ready(); await load(); }
    catch (error) { setSyncState("error", "读取未完成"); notice(localized(error).message, true); }
  })();
})();
