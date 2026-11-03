/* V2 owns native Screen settings and access; Reality retains its own provider settings. */
"use strict";
window.CompanionPerceptionPage = (() => {
  const product = window.CompanionProduct;
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  /* 后端把机器码放在信封的 message 上（页面桥只转发 message、丢掉其余字段），
     共享的 CompanionProduct.labels 表也不含 perception 域的码，所以这一页要
     自带一张表。未知原因使用当前操作的提示。 */
  const errors = {
    perception_provider_unavailable:"现实触及提供方当前不可用，请确认已安装并启用。",
    perception_request_invalid:"请求格式有误，请刷新后重试。",
    perception_save_failed:"保存未通过，请核对后重试。",
    staging_not_initialized:"角色数据尚未就绪，请在「账号与会话」保存角色映射；已保存可重新读取状态。",
    staging_host_terminated:"插件已停止，请重新打开插件页面。",
    dashboard_identity_required:"请从已登录的 AstrBot 管理页面打开陪伴小屋。",
    core_access_required:"当前管理账号没有陪伴小屋的查看权限。",
    bridge_timeout:"连接超时，请核对连接后重试。",
    invalid_response:"配置数据不完整，请刷新核对。",
  };
  const localized = (failure, fallback) => errors[failure?.code]
    || errors[failure?.message]
    || fallback;
  const realitySections = {
    global: {title:"运行与模型", fields:[
      ["enabled","启用现实触及","bool"],["timezone","时区","string"]]},
    camera: {title:"摄像头", fields:[
      ["camera_enabled","启用摄像头","bool"],["camera_index","摄像头序号","int",0,100000],
      ["min_interval_seconds","最短观察间隔 / 秒","int",10,3600],["capture_timeout_seconds","采集超时 / 秒","int",2,20],
      ["analysis_timeout_seconds","分析超时 / 秒","int",5,90],["proactive_curiosity_enabled","主动观察","bool"],
      ["proactive_min_tier","主动观察最低关系等级","int",1,5],["proactive_max_daily","每日主动观察上限","int",0,10],
      ["proactive_cooldown_minutes","主动观察冷却 / 分钟","int",10,1440]]},
    mobile: {title:"移动端与位置", fields:[
      ["enabled","启用移动端接收","bool"],["host","监听地址","string"],["port","监听端口","int",1,65535],
      ["public_base_url","外部访问地址","string"],["allowed_user_id","允许的用户 ID","string"],
      ["session_ttl_hours","配对会话有效期 / 小时","int",1,720],["location_ttl_seconds","位置有效期 / 秒","int",60,86400],
      ["amap_reverse_geocode_enabled","启用高德逆地理编码","bool"],["amap_api_key","高德 API Key","secret"],
      ["amap_cache_ttl_seconds","地理编码缓存 / 秒","int",60,604800],["amap_request_timeout_seconds","地理编码超时 / 秒","int",1,20],
      ["telemetry_enabled","接收设备状态","bool"],["telemetry_ttl_seconds","设备状态有效期 / 秒","int",60,604800],
      ["activity_enabled","接收活动状态","bool"],["activity_ttl_seconds","活动状态有效期 / 秒","int",60,86400],
      ["screen_upload_enabled","接收移动端画面","bool"],["pairing_token","配对令牌","secret"]]},
    health: {title:"健康数据", fields:[
      ["enabled","启用健康感知","bool"],["ttl_seconds","健康数据有效期 / 秒","int",60,604800]]},
  };
  let root, scope, abort, providers, generation = 0;
  const current = ticket => root?.isConnected && scope === product.scopeKey() && ticket === generation;

  function field(key, meta, value, prefix) {
    const type = meta.type || "text", id = prefix + "-" + key;
    const label = escape(meta.description || key);
    const hint = escape(meta.hint || "");
    const configured = meta.sensitive && meta.configured ? "已设置，留空保持原值。" : meta.sensitive ? "留空保持原值。" : "";
    const common = `id="${escape(id)}" name="${escape(key)}" data-perception-field="${escape(key)}"`;
    let control;
    if (type === "bool") {
      return `<div class="perception-setting perception-setting-toggle"><span><strong>${label}</strong>${hint ? `<small>${hint}</small>` : ""}</span><label class="model-switch"><input ${common} type="checkbox" ${value === true ? "checked" : ""}><i></i></label></div>`;
    }
    if (Array.isArray(meta.enum) && meta.enum.length) {
      control = `<select ${common}>${meta.enum.map(option => `<option value="${escape(option)}" ${String(option) === String(value ?? "") ? "selected" : ""}>${escape(option)}</option>`).join("")}</select>`;
    } else if (type === "text") {
      control = `<textarea ${common} rows="3">${escape(value)}</textarea>`;
    } else {
      const numeric = type === "int" || type === "integer" || type === "float";
      const inputType = meta.sensitive || type === "secret" || type === "password" ? "password" : numeric ? "number" : "text";
      const bounds = numeric ? ` step="${type === "float" ? "any" : "1"}"${meta.min == null ? "" : ` min="${escape(meta.min)}"`}${meta.max == null ? "" : ` max="${escape(meta.max)}"`}` : "";
      control = `<input ${common} type="${inputType}" value="${inputType === "password" ? "" : escape(value)}"${bounds}${numeric ? " required" : ""} autocomplete="off">`;
    }
    return `<label class="perception-setting" for="${escape(id)}"><strong>${label}</strong>${control}${hint || configured ? `<small>${hint}${configured ? " " + configured : ""}</small>` : ""}</label>`;
  }

  function form(provider, section, group, title, description, fields, values) {
    return `<form class="perception-settings-form" data-perception-form="${provider}" data-perception-section="${section}" data-perception-group="${escape(group)}"><div class="perception-settings-heading"><div><h4>${escape(title)}</h4></div><button class="button small" type="submit">保存本组</button></div><div class="perception-settings-grid">${fields.map(([key, meta]) => field(key, meta, values[key], `perception-${provider}-${group}`)).join("")}</div><p class="perception-save-status" role="status" aria-live="polite"></p></form>`;
  }

  function realityForms(settings) {
    return Object.entries(realitySections).map(([section, spec]) => {
      const values = settings[section] || {};
      const fields = spec.fields.map(([key, label, type, min, max]) => [key, {description:label, type, min, max,
        sensitive:type === "secret", configured:settings.secrets?.[key + "_configured"] === true}]);
      return form("reality", section, section, spec.title, "", fields, values);
    }).join("");
  }

  function render() {
    if (!root) return;
    for (const id of ["reality"]) {
      const node = root.querySelector(`[data-perception-provider="${id}"]`);
      if (!node) continue;
      if (!providers) {
        /* hosted() 为假说明宿主目录还没读到（桥注入晚于脚本执行），
           这时不能断言「连不上」——给一个重新读取的入口就够了。 */
        node.innerHTML = product.hosted()
          ? '<p class="memory-notice" role="status">正在读取实际配置…</p>'
          : '<p class="memory-notice" role="status">角色与会话目录暂未读到。</p><div class="dialog-actions"><button class="button small" type="button" data-workspace-retry>重新读取</button></div>';
        continue;
      }
      const entry = providers[id];
      node.innerHTML = entry?.available && entry.settings
        ? realityForms(entry.settings)
        : `<p class="memory-notice" role="status">${escape(entry?.reason || "配置暂不可读取，请确认插件已启用并更新。")}</p>`;
    }
  }

  async function api(method, payload) {
    /* 等桥就绪，而不是当场读 window.AstrBotPluginPage。
       主页面此前不引页面桥 SDK，桥是在 product.connect() 里动态补上的；
       这一页若在它补好之前就发请求，读到的是 undefined，用户看到的是
       「未连接」——一个只在几百毫秒窗口里出现的假故障。 */
    const bridge = window.CompanionProduct.bridgeNow() || await window.CompanionProduct.bridgeFor();
    const response = await (method === "GET" ? bridge.apiGet("perception") : bridge.apiPost("perception", payload));
    const wrapped = ["ok", "error"].includes(response?.status) ? response : response?.data;
    if (wrapped?.status === "error") throw new Error(wrapped.message || "保存失败，请刷新核对。");
    return product.unwrap(response);
  }

  async function load() {
    if (!product.hosted() || !root) return;
    const ticket = ++generation;
    providers = null;
    render();
    try {
      const data = await api("GET");
      if (current(ticket)) { providers = data.providers || {}; render(); }
    } catch (error) {
      if (current(ticket)) {
        providers = {reality:{available:false, reason:localized(error, "读取失败")}};
        render();
      }
    }
  }

  async function save(formNode) {
    const provider = formNode.dataset.perceptionForm, section = formNode.dataset.perceptionSection;
    const group = formNode.dataset.perceptionGroup;
    const settings = providers?.[provider]?.settings;
    if (!settings || !formNode.reportValidity()) return;
    const original = settings[section];
    const updates = {};
    for (const input of formNode.querySelectorAll("[data-perception-field]")) {
      const key = input.dataset.perceptionField;
      const value = input.type === "checkbox" ? input.checked : input.type === "number" ? Number(input.value) : input.value;
      if (input.type === "password" && !value.trim()) continue;
      if (value !== original?.[key]) updates[key] = value;
    }
    const status = formNode.querySelector("[role=status]");
    if (!Object.keys(updates).length) { status.textContent = "没有待保存的修改"; return; }
    const ticket = generation;
    const button = formNode.querySelector('button[type="submit"]');
    button.disabled = true;
    status.textContent = "正在保存…";
    try {
      const data = await api("POST", {provider, section, updates});
      if (!current(ticket)) return;
      const drafts = [...root.querySelectorAll("[data-perception-form]")].filter(node => node !== formNode).map(node => {
        const owner = node.dataset.perceptionForm, sectionId = node.dataset.perceptionSection;
        const previous = providers?.reality?.settings?.[sectionId];
        return {owner, sectionId, groupId:node.dataset.perceptionGroup,
          fields:[...node.querySelectorAll("[data-perception-field]")].filter(input => {
            const value = input.type === "checkbox" ? input.checked : input.type === "number" ? Number(input.value) : input.value;
            return input.type === "password" ? !!input.value : value !== previous?.[input.dataset.perceptionField];
          }).map(input => ({key:input.dataset.perceptionField, value:input.value, checked:input.checked}))};
      });
      providers[provider] = {available:true, settings:data.settings};
      render();
      for (const draft of drafts) {
        const container = root.querySelector(`[data-perception-provider="${draft.owner}"]`);
        const target = [...(container?.querySelectorAll("[data-perception-form]") || [])]
          .find(node => node.dataset.perceptionSection === draft.sectionId && node.dataset.perceptionGroup === draft.groupId);
        for (const item of draft.fields) {
          const input = [...(target?.querySelectorAll("[data-perception-field]") || [])]
            .find(node => node.dataset.perceptionField === item.key);
          if (input) {
            if (input.type === "checkbox") input.checked = item.checked;
            else input.value = item.value;
          }
        }
      }
      const saved = [...root.querySelectorAll(`[data-perception-provider="${provider}"] [data-perception-group]`)]
        .find(item => item.dataset.perceptionGroup === group)?.querySelector("[role=status]");
      if (saved) saved.textContent = "已保存到现实触及";
    } catch (error) {
      if (current(ticket)) { button.disabled = false; status.textContent = localized(error, "保存失败，请重试。"); }
    }
  }

  function mount(node) {
    unmount();
    root = node; scope = product.scopeKey(); abort = new AbortController();
    window.CompanionRealityAccess?.mount(root.querySelector("[data-reality-access]"));
    node.addEventListener("submit", event => {
      const formNode = event.target.closest("[data-perception-form]");
      if (!formNode) return;
      event.preventDefault();
      save(formNode);
    }, {signal:abort.signal});
    load();
  }

  function unmount() {
    generation++; abort?.abort(); abort = null; root = null; scope = null; providers = null;
    window.CompanionRealityAccess?.unmount();
  }
  return {mount, unmount};
})();
