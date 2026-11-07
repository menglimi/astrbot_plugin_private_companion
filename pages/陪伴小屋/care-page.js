/* 陪伴小屋 · 在意：先有惦记，再有冲动；这一阶段只观察，不动手。 */
"use strict";
window.CompanionCarePage = (() => {
  const e = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const icons = {sleep_light: "moon", ac_temperature: "thermometer", humidity_low: "droplets",
    sedentary: "clock"};
  const weights = {high: "很在意", medium: "有点在意", low: "还不想管"};
  const verdicts = {held: "想了想，先记着", proposed: "想了想要做", acted: "做了", spoke: "说了一句"};
  let root = null;
  let data = null;
  let busy = false;
  let notice = "";
  let failed = false;
  let version = 0;

  async function call(action, payload) {
    const request = window.CompanionProduct?.request;
    if (!request) throw new Error("bridge_unavailable");
    const record = await request("POST", "extensions/action", {
      extension_id: "reality", action: `mihome_${action}`, payload,
    }, {timeoutMs: 65000});
    const result = record?.result;
    if (!result || typeof result !== "object") throw new Error("invalid_response");
    return result;
  }

  function timeOf(value) {
    const text = String(value || "");
    return text.length >= 16 ? text.slice(11, 16) : text;
  }

  function concernCard(item) {
    const config = data.concerns?.[item.id] || {};
    const weight = config.weight || item.weight;
    const enabled = config.enabled === true;
    const tone = enabled ? "" : " is-paused";
    const feedback = item.supported
      ? `<div class="care-feedback-actions">
          <button type="button" class="care-chip" data-care-feedback="pause" data-care-concern="${e(item.id)}" ${busy ? "disabled" : ""}>别管这个</button>
          <button type="button" class="care-chip" data-care-feedback="allow" data-care-concern="${e(item.id)}" ${busy ? "disabled" : ""}>可以管</button>
          <button type="button" class="care-chip accent" data-care-feedback="praise" data-care-concern="${e(item.id)}" ${busy ? "disabled" : ""}>刚刚做得很好</button>
        </div>`
      : `<p class="care-quiet">它暂时只会想想，不会来催你。</p>`;
    const learned = data.learning?.[item.id] || {};
    const learnedLine = (learned.praise || learned.pause)
      ? `<p class="care-learned">它学到：被表扬 ${e(learned.praise || 0)} 次 · 被叫停 ${e(learned.pause || 0)} 次</p>`
      : "";
    const actionMeta = (data.action_meta || []).find(meta => meta.id === item.id);
    const action = data.actions?.[item.id] || {};
    const riskLabels = {low: "低风险", medium: "中风险", high: "高风险"};
    const actionRow = actionMeta
      ? `<div class="care-action">
          <label class="care-action-toggle"><input type="checkbox" data-care-action="${e(item.id)}" ${action.enabled === true ? "checked" : ""}><span class="care-track"><span class="care-thumb"></span></span><span>允许它${e(actionMeta.label)}</span><span class="care-risk ${e(actionMeta.risk)}">${riskLabels[actionMeta.risk] || "需授权"}</span></label>
          <span class="care-action-limit">每天 <input type="number" min="1" max="5" step="1" value="${e(action.daily_cap ?? 1)}" data-care-cap="${e(item.id)}"> 次 · 等 <input type="number" min="15" max="120" step="5" value="${e(action.patience_minutes ?? 30)}" data-care-patience="${e(item.id)}"> 分钟</span>
          ${actionMeta.description ? `<span class="care-action-note">${e(actionMeta.description)}</span>` : ""}
        </div>`
      : "";
    return `<article class="care-concern${tone}" data-care-card="${e(item.id)}">
      <header class="care-concern-head">
        <span class="care-glyph" aria-hidden="true"><i data-lucide="${icons[item.id] || "heart"}"></i></span>
        <div class="care-concern-id"><strong>${e(item.label)}</strong><small>${e(item.note)}</small></div>
        <span class="care-weight${enabled ? "" : " is-off"}">${enabled ? weights[weight] || "有点在意" : "先不管"}</span>
      </header>
      ${item.cue ? `<p class="care-cue"><span>留意</span>${e(item.cue)}</p>` : ""}
      ${learnedLine}
      ${actionRow}
      ${feedback}
    </article>`;
  }

  function impulseRow(item) {
    const label = verdicts[item.verdict] || "想了想";
    const tone = item.verdict === "held" ? " is-held" : item.verdict === "spoke" ? " is-spoke" : " is-acted";
    return `<article class="care-impulse">
      <time>${e(timeOf(item.at))}</time>
      <div class="care-impulse-body"><span class="care-verdict${tone}">${e(label)}</span>
        <strong>${e(item.title || item.alias)}</strong>
        <p>${e(item.reason || "")}</p></div>
    </article>`;
  }

  function render() {
    if (!root) return;
    if (!data) {
      root.innerHTML = `<section class="care" aria-busy="true"><p class="care-loading">正在读取它在意的事…</p></section>`;
      return;
    }
    const meta = data.concerns_meta || [];
    const impulses = (data.impulses || []).slice().reverse();
    const armed = Object.values(data.actions || {}).some(action => action.enabled === true);
    root.innerHTML = `<section class="care" aria-labelledby="careTitle">
      <header class="care-head">
        <div class="care-copy">
          <p class="care-kicker">不是日程表 · 是它会惦记的事</p>
          <h2 id="careTitle">它在意的几件小事</h2>
          <p>它会先有自己的状态，再注意你身边的线索，偶尔产生一个念头。大多数念头会被它自己按住；${armed ? "现在允许它做已授权的小事，没授权的事仍然只会想想。" : "这一阶段只记录它想过什么，还不会动手。"}</p>
        </div>
        <label class="care-master">
          <input type="checkbox" data-care-enable ${data.enable ? "checked" : ""}>
          <span class="care-track"><span class="care-thumb"></span></span>
          <span class="care-master-text">${data.enable ? "正在留意" : "没有留意"}</span>
        </label>
      </header>
      <div class="care-chips">
        <span>${meta.filter(item => data.concerns?.[item.id]?.enabled === true).length} 件在意</span>
        <span>${(data.devices || []).length} 台可观察</span>
        <span>${impulses.length ? "最近 " + e(timeOf(impulses[0].at)) : "还没有念头"}</span>
      </div>
      <div class="care-columns">
        <section class="care-panel" aria-label="在意清单">
          <h3>在意</h3>
          <p class="care-panel-note">每一条都是“它在意什么”，不是“几点做什么”。</p>
          ${meta.map(concernCard).join("")}
        </section>
        <section class="care-panel" aria-label="冲动日志">
          <h3>刚刚它想过什么</h3>
          <p class="care-panel-note">包括“忍住”的念头；这比执行日志更像一个人。</p>
          ${impulses.length ? `<div class="care-feed">${impulses.map(impulseRow).join("")}</div>`
            : `<p class="care-empty">还没有值得记下的念头。等它真的注意到什么，会出现在这里。</p>`}
        </section>
      </div>
      <footer class="care-foot">
        <span><strong>${Object.values(data.actions || {}).filter(action => action.enabled === true).length ? `已授权 ${Object.values(data.actions || {}).filter(action => action.enabled === true).length} 个动作` : "只观察，不会动手"}</strong> · 关灯默认允许，其他动作需要你单独授权</span>
        <span class="care-foot-actions">
          <button type="button" class="care-secondary" data-care-think ${busy ? "disabled" : ""}>让它现在想一想</button>
          <button type="button" class="care-primary" data-care-save ${busy ? "disabled" : ""}>${busy ? "保存中…" : "保存这些在意"}</button>
        </span>
      </footer>
      ${notice ? `<p class="care-notice${failed ? " is-error" : ""}" role="status">${e(notice)}</p>` : ""}
    </section>`;
    const nodes = root.querySelectorAll("[data-lucide]");
    if (nodes.length && window.lucide?.createIcons) {
      window.lucide.createIcons({nodes: [...nodes], attrs: {"aria-hidden": "true"}});
    }
  }

  async function load() {
    const ticket = version;
    try {
      const value = await call("care_status", {});
      if (!root || ticket !== version) return;
      data = value;
      notice = "";
      failed = false;
    } catch (error) {
      if (!root || ticket !== version) return;
      notice = error?.code === "core_access_required" ? "当前账号没有管理权限。" : "它在意的这些暂时读取不到，请稍后再试。";
      failed = true;
    }
    render();
  }

  async function save() {
    if (busy || !data) return;
    busy = true; notice = ""; failed = false; render();
    try {
      data = await call("care_save", {care: {enable: data.enable, concerns: data.concerns,
        actions: data.actions}});
      notice = "已经记住你在意什么。";
    } catch (error) {
      notice = "没有保存成功，请稍后再试。";
      failed = true;
    } finally {
      busy = false; render();
    }
  }

  async function think() {
    if (busy) return;
    busy = true; notice = "正在看它现在会想到什么…"; failed = false; render();
    try {
      const result = await call("care_think", {});
      const impulses = result.impulses || [];
      data = {...data, impulses: [...(data.impulses || []), ...impulses].slice(-40)};
      notice = impulses.length ? "它刚刚注意到了什么，已经写进冲动日志。" : "它这次没有产生新的念头。";
    } catch (error) {
      notice = "这一次没有看成，请稍后再试。";
      failed = true;
    } finally {
      busy = false; render();
    }
  }

  async function feedback(concernId, verdict) {
    if (busy) return;
    busy = true; notice = ""; failed = false; render();
    try {
      data = await call("care_feedback", {concern_id: concernId, verdict});
      notice = verdict === "pause" ? "好，它不再管这件事，之后也会更谨慎。"
        : verdict === "allow" ? "它会把这件事重新放在心上。"
        : "它记住了：这样做是对的，下次会少犹豫一点。";
    } catch (error) {
      notice = "没有记住这次纠正，请稍后再试。";
      failed = true;
    } finally {
      busy = false; render();
    }
  }

  function click(event) {
    const button = event.target.closest?.("button");
    if (!button) return;
    if (button.matches("[data-care-save]")) { void save(); return; }
    if (button.matches("[data-care-think]")) { void think(); return; }
    const feedbackButton = button.closest("[data-care-feedback]");
    if (feedbackButton) {
      void feedback(feedbackButton.dataset.careConcern, feedbackButton.dataset.careFeedback);
    }
  }

  function change(event) {
    const target = event.target;
    if (!data) return;
    const clamp = (value, low, high, fallback) => {
      const number = Number(value);
      return Number.isFinite(number) ? Math.min(high, Math.max(low, Math.round(number))) : fallback;
    };
    if (target.matches("[data-care-enable]")) {
      data.enable = target.checked;
      render();
    } else if (target.matches("[data-care-action]")) {
      const id = target.dataset.careAction;
      data.actions = data.actions || {};
      data.actions[id] = {...(data.actions[id] || {}), enabled: target.checked};
      render();
    } else if (target.matches("[data-care-cap]") || target.matches("[data-care-patience]")) {
      const id = target.dataset.careCap || target.dataset.carePatience;
      if (!id || !data.actions?.[id]) return;
      if (target.matches("[data-care-cap]")) {
        data.actions[id].daily_cap = clamp(target.value, 1, 5, 1);
        target.value = String(data.actions[id].daily_cap);
      } else {
        data.actions[id].patience_minutes = clamp(target.value, 15, 120, 30);
        target.value = String(data.actions[id].patience_minutes);
      }
    }
  }

  function mount(host) {
    unmount();
    root = host;
    data = null; busy = false; notice = ""; failed = false;
    root.addEventListener("click", click);
    root.addEventListener("change", change);
    render();
    void load();
  }

  function unmount() {
    ++version;
    root?.removeEventListener("click", click);
    root?.removeEventListener("change", change);
    root = null;
  }

  return {mount, unmount};
})();
