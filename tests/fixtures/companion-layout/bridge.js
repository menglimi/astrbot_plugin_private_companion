/*
 * 合成页面桥：只替换“控制台 SDK 数据边界”。
 *
 * 真实小屋里 `window.AstrBotPluginPage` 由 AstrBot 控制台注入，负责认证与把
 * 管理 API 转发给插件。布局夹具不需要真实后台，只需要让页面按正常形状拿到
 * 目录、被拒记录与显示偏好，因此这里提供最小只读实现，并记录所有请求供断言
 * 使用（`window.__fixtureRequests`）。
 *
 * 读取的数据刻意做成偏长（60 位私聊用户），让工作区内容高度超过宿主上限
 * 2400，从而复现“工作区 iframe 2400 + 外层视口 765”的真实几何。
 */
(function () {
  const host = "astrbot_plugin_private_companion";
  const requests = [];
  window.__fixtureRequests = requests;

  const PERSON_COUNT = 60;
  function person(index) {
    const id = "fixture-" + String(index).padStart(2, "0");
    return {
      subject_id: id, native_account_id: id, display_name: "夹具用户 " + index,
      name_basis: "platform", platform: "qq-main", account_id: "100", role_id: "role-a", bot_id: "bot-a",
      revision: 3, private_chat: true, private_target: true, settings_available: true, group_member: false,
      private: { msg_count: 12, active_days: 3, last_seen: "2026-11-02T10:00:00+00:00" },
      groups: {},
      total: { msg_count: 12, active_days: 3, first_seen: "2026-10-01T10:00:00+00:00",
        last_seen: "2026-11-02T10:00:00+00:00" },
      updated_at: "2026-11-02T10:00:00+00:00",
    };
  }
  const persons = Array.from({ length: PERSON_COUNT }, (_, index) => person(index + 1));

  function directory() {
    return {
      installation_id: "fixture-installation", profile_id: "fixture-profile", operator_name: "夹具管理员",
      persons, groups: [], group_networks: [],
      group_access: { mode: "shared", editable: true, running: false,
        accounts: [{ platform_id: "qq-main", account_id: "100", bot_id: "bot-a" }],
        persona_bots: ["bot-a"] },
      counts: { total: persons.length, private_chat: persons.length, group_member: 0, both: 0,
        private_only: persons.length, group_only: 0 },
      page_managed_capabilities: ["conversation.reply", "message.delivery", "proactive.contact"],
      directory_refresh_pending: false,
    };
  }

  /* 一条被拒记录：发信账号 999 未曾配置私聊目标，收到方是 qq-main/100。
     添加按钮应当可点，这正是要测的入口。 */
  const stranger = {
    platform_id: "qq-main", login: "100", peer: "999", attempts: 3,
    first_seen_at: "2026-11-02T09:00:00+00:00", last_seen_at: "2026-11-03T09:30:00+00:00",
    last_outcome: "stranger_notice_sent", target_state: "addable", target_id: null,
  };

  /* 添加成功后，服务端下一次读取会把这行投影成 added；夹具照此记账。 */
  const state = { strangerAdded: false };

  window.AstrBotPluginPage = {
    ready: async () => ({ pluginName: host }),
    onContext: () => () => {},
    getContext: async () => ({ pluginName: host, tab: "people" }),
    apiGet: async (path, body) => {
      requests.push({ method: "GET", path, body });
      switch (path) {
        case "page/targets":
          return {
            installation_id: "fixture-installation", operator_id: "dashboard:fixture",
            accounts_api: "/" + host + "/accounts",
            acquaintance_api: "/" + host + "/acquaintance",
            targets: [{
              target_id: "fixture-private", scope_kind: "private", platform_id: "qq-main",
              account_id: "100", bot_id: "bot-a", participant_id: "999",
              roles: [{ role_id: "role-a", role_name: "夹具人格",
                model_api: "/" + host + "/accounts/models" }],
            }],
          };
        case "integration/manifest":
          return { extensions: [], providers: [],
            summary: { connected_count: 0, extension_ready_count: 0, unassembled_extension_count: 0, provider_count: 0 } };
        case "page/display-preferences":
          return { preferences: {} };
        case "acquaintance":
          return directory();
        case "acquaintance/strangers":
          return { items: [state.strangerAdded
            ? { ...stranger, target_state: "added", target_id: "private-fixture-stranger" }
            : stranger] };
        case "acquaintance/authorization":
          return { subject_id: persons[0].subject_id, items: [], counts: {}, companion: false,
            identity_tier: "standard", identity_revision: 1 };
        case "acquaintance/settings":
          return { subject_id: persons[0].subject_id, settings: {}, identity: {} };
        default:
          throw new Error("fixture: unexpected read " + path);
      }
    },
    apiPost: async (path, body) => {
      requests.push({ method: "POST", path, body: body && JSON.parse(JSON.stringify(body)) });
      if (path === "acquaintance/stranger-target") {
        state.strangerAdded = true;
        return { state: "added", target_id: "private-fixture-stranger", saved: true,
          requires_reload: false, requires_initialize: false, started: true, startup_error: null };
      }
      throw new Error("fixture: unexpected write " + path);
    },
  };
})();
