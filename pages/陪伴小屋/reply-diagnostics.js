/* 当前会话的回复记录。只投影诊断元数据，正文、配置快照与内部标识不进入 HTML。 */
"use strict";
window.CompanionReplyDiagnostics = (() => {
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char =>
    ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
  const isRecord = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const records = value => Array.isArray(value) ? value.filter(isRecord) : [];
  const label = (labels, key, fallback) => Object.hasOwn(labels, key) ? labels[key] : fallback;
  const count = value => Number.isSafeInteger(value) && value >= 0 ? value : "未知";

  const stateLabels = {complete: "已完成", blocked: "受阻", closed: "已关闭", ready: "待投递",
    sealed: "已封存", collecting: "收集中", generating: "生成中", running: "生成中", delivering: "投递中",
    failed: "失败", completed: "生成完成", awaiting_request: "等待宿主请求", prepared: "已准备"};
  const responseLabels = {no_reply: "本轮未生成分段", image_only: "仅图片", text_or_mixed: "文字或混合内容"};
  const deliveryLabels = {not_started: "未观察到发送调用", started: "正在发送", partial: "部分返回",
    failed: "宿主发送失败", denied: "宿主拒绝发送", unknown: "发送结果未知"};
  const failureLabels = {UnprocessableEntityError: "模型线路拒绝请求 422", ClientError: "模型调用被拒绝",
    provider_request_failed: "模型请求失败", model_request_failed: "模型请求失败",
    native_response_parse_failed: "宿主回复解析失败", response_parse_failed: "回复解析失败",
    response_contract_rejected: "回复格式校验失败", stream_rejected: "流式校验失败",
    provider_timeout: "模型请求超时", model_timeout: "模型请求超时"};
  const parseLabels = {not_received: "未收到完整响应", raw_observed: "已收到原始响应",
    native_parsed: "宿主已解析", native_rejected: "宿主解析失败", stream_rejected: "流式校验失败",
    contract_rejected: "回复格式校验失败", validated: "回复已通过校验", tools: "工具请求已通过校验",
    captured: "已交给记忆解析", refusal: "模型拒绝"};
  const shapeLabels = {empty: "空文本", text: "文本", json_object: "JSON 对象", json_array: "JSON 数组", json_scalar: "JSON 值"};

  function localTime(timestamp) {
    if (!Number.isFinite(timestamp)) return "时间未知";
    const date = new Date(timestamp);
    const pad = number => String(number).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
      + `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  }

  function waitingText(started, now) {
    if (!Number.isFinite(started)) return "";
    const minutes = Math.floor((now - started) / 60000);
    if (minutes < 1) return "刚开始";
    if (minutes < 60) return `已等待 ${minutes} 分钟`;
    if (minutes < 1440) return `已等待 ${Math.floor(minutes / 60)} 小时`;
    return `已等待 ${Math.floor(minutes / 1440)} 天`;
  }

  function actionList(actions) {
    if (!Array.isArray(actions) || !actions.length) return "无";
    return actions.map(value => escape(typeof value === "string" ? value : value?.action || "未知工具")).join("、");
  }

  const stages = [["raw", "原始"], ["parsed", "宿主解析"], ["visible", "待呈现"]];
  function renderResponse(call) {
    const diagnostic = call.response_diagnostics;
    const summaries = stages.map(([key, name]) => {
      const text = diagnostic[key];
      if (!isRecord(text)) return `<span>${name}未取得文本</span>`;
      return `<span>${name} ${count(text.chars)} 字 · ${label(shapeLabels, text.shape, "类型未知")}`
        + `${text.has_internal_markers === true ? " · 含标签样式标记" : ""}</span>`;
    });
    const hashes = stages.flatMap(([key, name]) => {
      const hash = diagnostic[key]?.sha256;
      return typeof hash === "string" && /^[0-9a-f]{64}$/i.test(hash) ? [`${name} ${hash}`] : [];
    });
    const round = Number.isSafeInteger(call.position) && call.position >= 0 ? Math.floor(call.position / 2) + 1 : "未知";
    return `<li>第 ${round} 轮 · ${label(parseLabels, diagnostic.parse_state, "解析状态未知")}`
      + ` · 完整响应${diagnostic.completion_present ? "已收到" : "未收到"} · 结束原因 ${escape(diagnostic.finish_reason || "未知")}`
      + `<p>${summaries.join(" → ")}</p>`
      + (hashes.length ? `<details><summary>内容指纹</summary><p>${hashes.join("<br>")}</p></details>` : "") + "</li>";
  }

  function renderRecord(item, now) {
    const rounds = records(item.rounds);
    const materials = records(item.injected_materials);
    const tools = records(item.tool_calls);
    const filtered = records(item.filtered_tools);
    const calls = records(item.calls);
    const failures = calls.filter(call => call.failure_code || call.state === "failed");
    const responses = calls.filter(call => isRecord(call.response_diagnostics));
    const native = item.execution_path === "astrbot_core";
    /* 宿主返回次数与模型失败分别观测，两者并存也无法证明发生了线路回退。 */
    const summary = [`<span>${label(stateLabels, item.state, "状态未知")}</span>`];
    if (failures.length) {
      const code = failures[0].failure_code;
      const failure = label(failureLabels, code, code ? `模型请求失败 ${code}` : "模型请求失败");
      summary.push(`<span class="reply-record-note is-warn">${escape(failure)}</span>`);
    }
    if (native) {
      summary.push(`<span>宿主发送返回 ${count(item.send_returns)} 次</span>`);
      if (item.delivery_state !== "returned") {
        const warning = ["failed", "denied", "unknown"].includes(item.delivery_state);
        summary.push(`<span class="reply-record-note${warning ? " is-warn" : ""}">`
          + `${label(deliveryLabels, item.delivery_state, "发送状态未知")}</span>`);
      }
    } else {
      summary.push(`<span>${label(responseLabels, item.response_type, "回应类型未知")}</span>`);
    }
    /* 宿主和 V2 的带时区 ISO 时间可含微秒，先截到毫秒再以本地时区显示。 */
    const timestamp = typeof item.created_at === "string" ? Date.parse(item.created_at.replace(/(\.\d{3})\d+/, "$1")) : NaN;
    if (["generating", "running", "awaiting_request"].includes(item.state)) {
      const waiting = waitingText(timestamp, now);
      if (waiting) summary.push(`<span class="reply-record-note">${waiting}</span>`);
    }
    return `<details class="reply-record"><summary>${summary.join("")}<time>${localTime(timestamp)}</time></summary>`
      + `<p>任务 ${escape(item.turn_type || "conversation.reply")} · 有效输入 ${count(item.current_inputs)} 条 · 轮次 ${rounds.length} · 群历史 ${count(item.group_history_items)} 条 · 注入材料 ${materials.length} 项</p>`
      + `<p>提供工具：${actionList(item.offered_tools)}</p>`
      + (filtered.length ? `<p>未提供：${filtered.map(tool => `${escape(tool.action || "未知工具")} · ${escape(tool.reason || "原因未知")}`).join("；")}</p>` : "")
      + (rounds.length ? `<ul>${rounds.map(round => `<li>第 ${count(round.round_no + 1)} 轮 · ${escape(round.state || "状态未知")} · 请求 ${actionList(round.tool_actions)}</li>`).join("")}</ul>` : "")
      + (tools.length ? `<ul>${tools.map(tool => `<li>${escape(tool.action || "未知工具")} · ${escape(tool.state || "状态未知")} · ${escape(tool.effect || "效果未知")}</li>`).join("")}</ul>` : "")
      + (materials.length ? `<p>材料：${materials.map(material => `${escape(material.owner || "未知来源")} / ${escape(material.purpose || "未分类")} · ${escape(material.state || "状态未知")}`).join("；")}</p>` : "")
      + (failures.length ? `<p>模型失败分类：${failures.map(call => escape(call.failure_code || "未记录失败码")
        + (call.provider ? `（${escape(call.provider)}）` : "")).join("、")}</p>` : "")
      + (responses.length ? `<ul>${responses.map(renderResponse).join("")}</ul>` : "")
      + (native ? "" : `<p>分段 ${Array.isArray(item.parts) ? item.parts.length : 0} 条</p>`)
      + "</details>";
  }

  function renderRecords(recent) {
    const items = records(recent);
    if (!items.length) return '<p class="reply-empty">暂无近期回复记录。</p>';
    const now = Date.now();
    return `<p class="reply-diag-note">最近 ${items.length} 轮 · 本地时间。宿主发送返回仅记录调用结果，平台送达情况需另行确认。记录不含提示词正文与消息原文。</p>`
      + items.map(item => renderRecord(item, now)).join("");
  }

  let root = null, current = null;

  function retry(event) {
    if (event.target.closest("[data-reload-reply-diagnostics]") && current && !current.loading) {
      current = null;
      mount(root);
    }
  }

  function unmount() {
    root?.removeEventListener("click", retry);
    root = null;
    current = null;
  }

  function mount(element) {
    const replaced = element !== root;
    if (replaced) {
      root?.removeEventListener("click", retry);
      root = element;
      root.addEventListener("click", retry);
    }
    const product = window.CompanionProduct;
    const target = product.target();
    const nativeId = target?.scope_kind === "group" ? target.group_id : target?.participant_id;
    if (!nativeId || nativeId === "*") {
      current = null;
      root.textContent = "请选择当前角色的具体私聊或群聊窗口，再查看回复诊断。";
      return;
    }
    // The role-default editor may borrow another private window. Read the route of the selected window itself.
    const endpoint = product.windowRole()?.reply_api;
    let path;
    try { path = product.relative(endpoint); }
    catch {
      current = null;
      root.textContent = "当前窗口的回复接口暂不可用，请核对账号与角色映射。";
      return;
    }
    const scope = product.scopeKey();
    if (current?.scope === scope && current.endpoint === endpoint) {
      if (replaced) root.innerHTML = current.html;
      return;
    }
    const read = current = {scope, endpoint, loading: true, html: '<p role="status">正在读取本会话的回复记录…</p>'};
    root.innerHTML = read.html;
    const live = () => current === read && product.scopeKey() === scope && product.windowRole()?.reply_api === endpoint;
    (async () => {
      try {
        const data = await product.request("GET", path, {view: "diagnostics"});
        if (!live()) return;
        if (!Array.isArray(data?.recent)) throw new Error("invalid_response");
        read.html = '<button type="button" class="button small" data-reload-reply-diagnostics>刷新记录</button>' + renderRecords(data.recent);
      } catch (error) {
        if (!live()) return;
        const reason = error?.code === "reply_window_required"
          ? "所选窗口没有独立的回复上下文，不会显示其他窗口的记录。" : product.errorLabel(error?.code);
        read.html = '<p role="alert">读取本会话回复诊断失败。' + escape(reason)
          + '</p><button type="button" class="button small" data-reload-reply-diagnostics>重新读取</button>';
      }
      read.loading = false;
      if (root?.isConnected) root.innerHTML = read.html;
    })();
  }

  return {renderRecords, mount, unmount};
})();
