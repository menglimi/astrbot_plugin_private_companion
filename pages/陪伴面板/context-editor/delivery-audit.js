// Read-only transport history is deliberately separate from editable LLM context.
export function renderDeliveryAudit(bridge, conversation, anchor) {
  document.getElementById("delivery-audit")?.remove();
  const root = document.createElement("details");
  root.id = "delivery-audit";
  const summary = document.createElement("summary");
  summary.textContent = "逐条对话流水（点击展开；不等同于模型上下文）";
  const note = document.createElement("p");
  note.textContent = "按私聊对象汇总每条入站与每次出站，包含分段及插件发送。点击单条记录查看脱敏参数；平台回执不代表对方已读。每页 50 条，新记录在前，不合并重复内容。";
  const status = document.createElement("p");
  const list = document.createElement("div");
  list.style.cssText = "max-height:480px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere";
  const latest = document.createElement("button");
  latest.type = "button";
  latest.textContent = "最新／刷新";
  const older = document.createElement("button");
  older.type = "button";
  older.textContent = "更早 50 条";
  older.disabled = true;
  root.append(summary, note, status, latest, older, list);
  anchor.after(root);
  let cursor = 0;
  let loaded = false;
  const labels = {received: "已收到并进入处理", received_blocked: "已收到但被熔断拦截",
    accepted: "平台已返回消息 ID", unconfirmed: "API 返回但无消息 ID",
    uncertain: "调用异常／发送结果不确定", pending: "已预登记／结果待确认",
    blocked: "已拦截，未调用发送 API", historical_prepared: "旧日志：准备发送（未核实送达）"};
  async function load(before = 0) {
    latest.disabled = older.disabled = true;
    status.textContent = "正在读取发送流水…";
    try {
      const payload = await bridge.apiGet("page/deliveries", {
        conversation_id: conversation.conversationId, umo: conversation.umo, before,
      });
      if (!root.isConnected) return;
      loaded = true;
      if (payload.available === false) {
        status.textContent = "当前 AstrBot 核心未启用逐条收发审计；上下文查看和编辑不受影响";
        list.replaceChildren();
        older.disabled = true;
        return;
      }
      status.textContent = `共 ${payload.total} 条；${payload.circuit ? "已熔断，需人工审核解除：" + payload.circuit.reason : "未熔断"}`;
      list.replaceChildren();
      for (const item of payload.items || []) {
        const card = document.createElement("details");
        card.style.cssText = "padding:10px 0;border-bottom:1px solid #8885";
        const heading = document.createElement("summary");
        const direction = item.direction === "inbound" ? "← 用户" : "→ 机器人";
        heading.textContent = `#${item.id} · ${direction} · ${new Date(item.ts * 1000).toLocaleString()} · ${labels[item.status] || item.status}`;
        const textSection = document.createElement("section");
        textSection.className = "message-detail-section";
        const textLabel = document.createElement("strong");
        textLabel.textContent = "消息文本";
        const body = document.createElement("p");
        body.textContent = item.text || "[非文本消息]";
        textSection.append(textLabel, body);
        const parameterSection = document.createElement("section");
        parameterSection.className = "message-detail-section technical-parameters";
        const paramsLabel = document.createElement("strong");
        paramsLabel.textContent = "技术参数";
        const params = document.createElement("pre");
        params.style.cssText = "white-space:pre-wrap;overflow-wrap:anywhere";
        const onebotParameters = JSON.parse(JSON.stringify(item.params || {}));
        const messagePayload = onebotParameters.message ?? onebotParameters.messages;
        delete onebotParameters.message;
        delete onebotParameters.messages;
        delete onebotParameters.raw_message;
        let messageStructure = null;
        if (Array.isArray(messagePayload)) {
          messageStructure = messagePayload.map((segment) => {
            if (!segment || typeof segment !== "object") return { type: typeof segment };
            const copied = JSON.parse(JSON.stringify(segment));
            if (copied.data && typeof copied.data === "object") delete copied.data.text;
            delete copied.text;
            return copied;
          });
        } else if (messagePayload !== undefined) {
          messageStructure = { type: typeof messagePayload };
        }
        params.textContent = JSON.stringify({
          direction: item.direction || "outbound",
          status: item.status || "",
          action: item.action || "",
          messageId: item.receipt || "",
          reason: item.reason || "",
          onebotParameters,
          messageStructure,
        }, null, 2);
        parameterSection.append(paramsLabel, params);
        card.append(heading, textSection, parameterSection);
        list.append(card);
      }
      if (!payload.items?.length) list.textContent = "暂无发送流水。";
      cursor = payload.items?.at(-1)?.id || 0;
      older.disabled = !payload.hasMore;
      list.scrollTop = 0;
    } catch (error) {
      status.textContent = `流水读取失败：${error.message || error}`;
    } finally {
      latest.disabled = false;
    }
  }
  latest.addEventListener("click", () => load());
  older.addEventListener("click", () => load(cursor));
  root.addEventListener("toggle", () => { if (root.open && !loaded) load(); });
}
