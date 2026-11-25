"use strict";

window.CompanionTuningPage = (() => {
  const ROUTE = "performance/tuning";
  const MACHINE_CODE_RE = /^[a-z][a-z0-9_]{2,80}$/;
  const FIELD_KEY_RE = /^[a-z][a-z0-9_]{1,64}$/;
  const text = value => String(value ?? "").replace(/[&<>"']/g,
    character => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[character]));

  let root = null, bridge = null, version = 0, listenerAbort = null, boundListeners = [];
  let state = emptyState();

  function emptyState() {
    return {
      loading: true,
      busy: false,
      error: false,
      message: "",
      data: null,
      draftProfile: "auto",
      draftOverrides: {},
      advancedOpen: false,
      pendingSwitchProfile: null,
      highlightField: "",
    };
  }

  function live(ticket) { return ticket === version && root?.isConnected; }
  function stop() {
    version += 1;
    if (listenerAbort) {
      try { listenerAbort.abort(); } catch {}
      listenerAbort = null;
    }
    if (root && boundListeners.length && typeof root.removeEventListener === "function") {
      for (const [type, fn] of boundListeners) {
        try { root.removeEventListener(type, fn); } catch {}
      }
    }
    boundListeners = [];
    root = null;
    bridge = null;
  }

  function extractMachineCode(error) {
    const candidates = [error?.code, error?.data?.code, error?.message];
    for (const raw of candidates) {
      if (typeof raw === "string") {
        const trimmed = raw.trim();
        if (MACHINE_CODE_RE.test(trimmed)) return trimmed;
      }
    }
    return "";
  }

  function normalizeBridgeError(error) {
    const code = extractMachineCode(error);
    const rawMessage = typeof error?.message === "string" ? error.message.trim() : "";
    const detail = rawMessage && !MACHINE_CODE_RE.test(rawMessage) ? rawMessage : "";
    const normalized = error instanceof Error ? error : new Error(rawMessage || code || "tuning_request_invalid");
    if (code && !normalized.code) {
      try { normalized.code = code; } catch {}
    }
    if (code && normalized.code !== code) {
      return Object.assign(new Error(detail || code), {code, detail});
    }
    try { normalized.detail = detail; } catch {}
    return normalized;
  }

  async function getBridge() {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      if (window.AstrBotPluginPage?.apiGet) return window.AstrBotPluginPage;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    throw Object.assign(new Error("bridge_unavailable"), {code: "bridge_unavailable", detail: ""});
  }

  async function call(method, body) {
    bridge = bridge || await getBridge();
    let response;
    try {
      response = await (method === "GET" ? bridge.apiGet(ROUTE) : bridge.apiPost(ROUTE, body));
    } catch (error) {
      throw normalizeBridgeError(error);
    }
    const wrapped = ["ok", "error"].includes(response?.status) && response?.data !== undefined ? response
      : ["ok", "error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    if (wrapped?.status === "error") {
      const code = extractMachineCode({code: wrapped.data?.code || wrapped.message}) || "tuning_request_invalid";
      const rawMsg = wrapped.reason || wrapped.message || code;
      const detail = typeof rawMsg === "string" && !MACHINE_CODE_RE.test(rawMsg.trim()) ? rawMsg.trim() : "";
      throw Object.assign(new Error(detail || code), {code, detail});
    }
    const result = wrapped ? wrapped.data : response;
    if (!result || typeof result !== "object" || Array.isArray(result)) {
      throw Object.assign(new Error("invalid_response"), {code: "invalid_response", detail: ""});
    }
    return result;
  }

  function baselineFor(profileId) {
    const data = state.data;
    if (!data) return {};
    if (data.baselines && typeof data.baselines === "object" && data.baselines[profileId]) {
      return data.baselines[profileId];
    }
    if (profileId === data.profile && data.baseline) return data.baseline;
    return data.baseline || {};
  }

  function normalizeFieldKey(rawKey) {
    if (typeof rawKey !== "string") return "";
    const trimmed = rawKey.trim();
    if (!FIELD_KEY_RE.test(trimmed)) return "";
    if (state.data && Array.isArray(state.data.fields)) {
      return state.data.fields.some(spec => spec.key === trimmed) ? trimmed : "";
    }
    return trimmed;
  }

  function computeActiveBadgeLabel(overrideCount) {
    const data = state.data || {};
    const hw = data.hardware || {};
    if (overrideCount > 0) {
      return `基于「${profileLabel(state.draftProfile)}」· 已微调 ${overrideCount} 项`;
    }
    if (state.draftProfile === "auto") {
      const autoLabel = hw.recommended_profile_label
        || profileLabel(hw.recommended_profile || "balanced")
        || data.resolved_profile_label;
      return `当前生效：自动推荐（${autoLabel}）`;
    }
    return `当前生效：${profileLabel(state.draftProfile)}`;
  }

  function dirtyStatusText(dirty) {
    return dirty
      ? "有未保存的调优更改，保存后大部分参数立即热生效。"
      : "当前性能配置已生效，随时可切换档位或微调单项。";
  }

  function compactOverrides(profileId, overrides) {
    const base = baselineFor(profileId);
    const out = {};
    for (const [k, v] of Object.entries(overrides || {})) {
      if (v !== null && v !== undefined && base[k] !== v) out[k] = v;
    }
    return out;
  }

  function isDirty() {
    if (!state.data) return false;
    if (state.draftProfile !== state.data.profile) return true;
    const saved = compactOverrides(state.data.profile, state.data.overrides || {});
    const draft = compactOverrides(state.draftProfile, state.draftOverrides || {});
    const savedKeys = Object.keys(saved).sort();
    const draftKeys = Object.keys(draft).sort();
    if (savedKeys.length !== draftKeys.length) return true;
    return savedKeys.some(key => saved[key] !== draft[key]);
  }

  function activeOverrideCount() {
    return Object.keys(compactOverrides(state.draftProfile, state.draftOverrides)).length;
  }

  function profileLabel(profileId) {
    const found = (state.data?.profiles || []).find(item => item.id === profileId);
    return found ? found.label : profileId;
  }

  function renderFieldOverrideDot(overridden) {
    return overridden ? `<span class="tuning-override-dot" title="已手动微调"></span>` : "";
  }

  function renderFieldOverrideBadge(overridden) {
    return overridden ? `<span class="tuning-badge">已微调</span>` : "";
  }

  function renderFieldResetButton(key, overridden) {
    return overridden
      ? `<button type="button" class="tuning-reset-link" data-tuning-reset-field="${text(key)}"${state.busy ? " disabled" : ""}>恢复默认</button>`
      : "";
  }

  function renderResetAllButton(overrideCount) {
    return overrideCount > 0
      ? `<button type="button" class="tuning-reset-link" data-tuning-reset-all${state.busy ? " disabled" : ""}>全部恢复档位默认（${overrideCount}）</button>`
      : "";
  }

  function renderDiscardButton(dirty) {
    return dirty
      ? `<button type="button" class="button small" data-tuning-discard${state.busy ? " disabled" : ""}>撤销更改</button>`
      : "";
  }

  function renderField(spec, baseline, effective, overridden, clampInfo) {
    const value = overridden ? state.draftOverrides[spec.key] : baseline[spec.key];
    const displayValue = value ?? effective[spec.key];
    const isHighlighted = state.highlightField === spec.key;
    const classes = [
      "tuning-field",
      overridden ? "is-overridden" : "",
      clampInfo ? "is-clamped" : "",
      isHighlighted ? "is-highlighted" : "",
    ].filter(Boolean).join(" ");

    let control = "";
    if (spec.kind === "enum") {
      control = `<div class="tuning-enum-group" role="radiogroup" aria-label="${text(spec.label)}">${
        (spec.options || []).map(opt => {
          const active = String(displayValue) === String(opt.value);
          return `<button type="button" class="tuning-enum-btn${active ? " is-active" : ""}" role="radio" aria-checked="${active}" data-tuning-enum-key="${text(spec.key)}" data-tuning-enum-val="${text(opt.value)}"${state.busy ? " disabled" : ""}>${text(opt.label)}</button>`;
        }).join("")
      }</div>`;
    } else {
      const min = spec.minimum ?? 0;
      const max = spec.maximum ?? 100;
      const step = spec.step ?? 1;
      control = `<div class="tuning-field-control">
        <input class="tuning-range" type="range" min="${min}" max="${max}" step="${step}" value="${text(displayValue)}" data-tuning-input="${text(spec.key)}" data-tuning-range="${text(spec.key)}" aria-label="${text(spec.label)} 滑块"${state.busy ? " disabled" : ""}>
        <label class="tuning-number-wrap">
          <input type="number" min="${min}" max="${max}" step="${step}" value="${text(displayValue)}" data-tuning-input="${text(spec.key)}" data-tuning-number="${text(spec.key)}" aria-label="${text(spec.label)} 数值"${state.busy ? " disabled" : ""}>
          ${spec.unit ? `<span class="tuning-unit">${text(spec.unit)}</span>` : ""}
        </label>
        <span class="tuning-field-baseline">档位默认 ${text(baseline[spec.key])}${spec.unit ? ` ${text(spec.unit)}` : ""}</span>
      </div>`;
    }

    const clampNote = clampInfo
      ? `<p class="tuning-field-clamp-note">已自动安全限幅为 ${text(clampInfo.effective)}${spec.unit ? ` ${text(spec.unit)}` : ""}：${text(clampInfo.reason)}</p>`
      : "";

    return `<div class="${classes}" data-tuning-field="${text(spec.key)}">
      <div class="tuning-field-top">
        <div class="tuning-field-label-wrap">
          <span class="tuning-inline-slot" data-tuning-field-dot="${text(spec.key)}">${renderFieldOverrideDot(overridden)}</span>
          <span class="tuning-field-label">${text(spec.label)}</span>
          <span class="tuning-inline-slot" data-tuning-field-badge="${text(spec.key)}">${renderFieldOverrideBadge(overridden)}</span>
        </div>
        <div class="tuning-field-tags">
          <span class="tuning-apply-tag">${text(spec.apply_mode || (spec.hot_reload ? "立即热生效" : "下次打开副库生效"))}</span>
          <span class="tuning-inline-slot" data-tuning-field-reset-slot="${text(spec.key)}">${renderFieldResetButton(spec.key, overridden)}</span>
        </div>
      </div>
      <p class="tuning-field-hint">${text(spec.effect_hint)}</p>
      ${control}
      ${clampNote}
    </div>`;
  }

  function render() {
    if (!root) return;
    if (state.loading) {
      root.innerHTML = `<div class="tuning-loading" role="status">正在识别本机硬件与性能策略…</div>`;
      return;
    }
    const data = state.data;
    if (!data) {
      root.innerHTML = `<div class="tuning-dashboard">
        <p class="tuning-message is-error" role="status">${text(state.message || "性能调优数据读取失败。")}</p>
        <div><button class="button small" type="button" data-tuning-reload>重新读取</button></div>
      </div>`;
      return;
    }

    const hw = data.hardware || {};
    const clampedList = Array.isArray(data.clamped_fields) ? data.clamped_fields : [];
    const clampMap = Object.fromEntries(clampedList.map(item => [item.field, item]));
    const draftCompact = compactOverrides(state.draftProfile, state.draftOverrides);
    const overrideCount = Object.keys(draftCompact).length;
    const dirty = isDirty();
    const baseline = baselineFor(state.draftProfile);
    const effective = data.values || {};
    const activeBadgeLabel = computeActiveBadgeLabel(overrideCount);

    const memText = hw.effective_memory_gib != null ? `${hw.effective_memory_gib} GB` : "未识别";
    const clampBanner = clampedList.length
      ? `<div class="tuning-clamp-banner" role="status">
          <strong>防爆内存护栏已生效（自动限幅 ${clampedList.length} 项）</strong>
          <ul class="tuning-clamp-list">${clampedList.map(item =>
            `<li>${text(item.label)}：请求 ${text(item.requested)} → 生效 ${text(item.effective)}（${text(item.reason)}）</li>`
          ).join("")}</ul>
        </div>`
      : "";

    const profileCards = (data.profiles || []).map(item => {
      const selected = state.draftProfile === item.id;
      const isHwRec = item.id !== "auto" && item.id === hw.recommended_profile;
      return `<button type="button" class="tuning-profile-card${selected ? " is-selected" : ""}" role="radio" aria-checked="${selected}" data-tuning-profile="${text(item.id)}"${state.busy ? " disabled" : ""}>
        <div class="tuning-profile-head">
          <span class="tuning-profile-name">${text(item.label)}</span>
          <span class="tuning-badge${isHwRec ? " is-ok" : selected ? "" : " is-subtle"}">${text(isHwRec ? "本机推荐" : item.badge)}</span>
        </div>
        <p class="tuning-profile-summary">${text(item.summary)}</p>
        <p class="tuning-profile-fit">适用：${text(item.recommended_for)}</p>
        <div class="tuning-profile-metrics">
          <span>缓存：${text(item.preview_cache)}</span>
          <span>并发：${text(item.preview_concurrency)}</span>
          <span>写盘：${text(item.preview_disk)}</span>
        </div>
      </button>`;
    }).join("");

    const switchPrompt = state.pendingSwitchProfile
      ? `<div class="tuning-switch-prompt" role="region" aria-label="切换档位微调处理">
          <span>你当前保留了 ${overrideCount} 项手动微调，切换至「${text(profileLabel(state.pendingSwitchProfile))}」时如何处理？</span>
          <div class="tuning-switch-actions">
            <button type="button" class="button primary small" data-tuning-switch-clear>全部改用新档默认</button>
            <button type="button" class="button small" data-tuning-switch-keep>保留这 ${overrideCount} 项微调</button>
            <button type="button" class="button small" data-tuning-switch-cancel>取消</button>
          </div>
        </div>`
      : "";

    const fieldsByGroup = {};
    for (const spec of data.fields || []) {
      (fieldsByGroup[spec.group] = fieldsByGroup[spec.group] || []).push(spec);
    }
    const groupsHtml = (data.groups || []).map(group => {
      const items = fieldsByGroup[group.id] || [];
      if (!items.length) return "";
      return `<div class="tuning-group">
        <div class="tuning-group-head">
          <h5>${text(group.title)}</h5>
          <p>${text(group.description)}</p>
        </div>
        <div class="tuning-field-grid">${items.map(spec =>
          renderField(spec, baseline, effective, Object.prototype.hasOwnProperty.call(draftCompact, spec.key), clampMap[spec.key])
        ).join("")}</div>
      </div>`;
    }).join("");

    const lite = data.lite_page || {};
    const liteSaved = Boolean(lite.saved_enabled);
    const liteReload = Boolean(lite.reload_required);
    const messageHtml = state.message
      ? `<p class="tuning-message${state.error ? " is-error" : ""}" role="status">${text(state.message)}</p>`
      : "";

    root.innerHTML = `<div class="tuning-dashboard">
      <section class="tuning-hero" aria-label="硬件识别与当前生效策略">
        <div class="tuning-hero-top">
          <div class="tuning-hero-title">
            <div class="tuning-hero-heading">
              <h4>本机硬件识别与性能策略</h4>
              <span class="tuning-badge is-ok" data-tuning-active-badge>${text(activeBadgeLabel)}</span>
            </div>
            <p class="tuning-hero-reason">${text(hw.recommendation_reason || "自动根据本机可用内存与处理器核心数选择稳妥参数。")}</p>
          </div>
          <div class="tuning-hw-pills">
            <span class="tuning-hw-pill">可用内存 <strong>${text(memText)}</strong>${hw.cgroup_limited ? "（容器限额）" : ""}</span>
            <span class="tuning-hw-pill">处理器 <strong>${text(hw.cpu_count ?? "—")} 核</strong></span>
            <span class="tuning-hw-pill">当前查库通道 <strong>${text(data.runtime?.reader_pool_size ?? effective.reader_pool_size ?? "—")}</strong></span>
          </div>
        </div>
        ${clampBanner}
      </section>

      <section class="tuning-section" aria-label="性能预设档位">
        <div class="tuning-section-head">
          <h4>选择性能档位</h4>
          <p>默认「自动推荐」即可安心使用；机械硬盘、NAS 或老旧磁盘建议选「兼容稳妥」。</p>
        </div>
        <div class="tuning-profile-grid" role="radiogroup" aria-label="性能调优预设档位" data-tuning-profiles>
          ${profileCards}
        </div>
        ${switchPrompt}
      </section>

      <section class="tuning-advanced-panel${state.advancedOpen ? " is-open" : ""}" data-tuning-advanced>
        <div class="tuning-advanced-header">
          <button type="button" class="tuning-advanced-toggle" aria-expanded="${state.advancedOpen}" data-tuning-advanced-toggle>
            <span class="tuning-chevron" aria-hidden="true">▸</span>
            <span data-tuning-advanced-label>${state.advancedOpen ? "收起高级性能微调" : "展开高级性能微调"}</span>
          </button>
          <div class="tuning-advanced-meta">
            <span>共 ${(data.fields || []).length} 项参数 · 仅保存你改过的项，其余始终跟随档位</span>
            <span class="tuning-inline-slot" data-tuning-reset-all-slot>${renderResetAllButton(overrideCount)}</span>
          </div>
        </div>
        <div class="tuning-advanced-collapse">
          <div class="tuning-advanced-inner">
            <div class="tuning-advanced-body">${groupsHtml}</div>
          </div>
        </div>
      </section>

      <div class="tuning-actions-bar${dirty ? " is-dirty" : ""}" data-tuning-actions-bar>
        <span class="tuning-actions-status" data-tuning-actions-status>${dirtyStatusText(dirty)}</span>
        <div class="tuning-actions-buttons">
          <button type="button" class="button primary small" data-tuning-save${state.busy || !dirty ? " disabled" : ""}>${state.busy ? "正在应用…" : "保存并立即生效"}</button>
          <span class="tuning-inline-slot" data-tuning-discard-slot>${renderDiscardButton(dirty)}</span>
          <button type="button" class="button small" data-tuning-reload${state.busy ? " disabled" : ""}>刷新状态</button>
        </div>
      </div>

      ${messageHtml}

      <div class="tuning-shortcuts-grid">
        <section class="tuning-shortcut-card" data-tuning-lite-card>
          <div class="tuning-shortcut-head">
            <div>
              <h4>极简网页模式（低配设备专用）</h4>
              <p>关闭 3D 陪伴小屋只留轻量状态页，大幅降低浏览器内存与远端加载开销；聊天、记忆与主动联系照常运行。</p>
            </div>
            <button class="switch" type="button" role="switch" aria-checked="${liteSaved}" aria-label="启用极简网页模式" data-tuning-lite-toggle${state.busy ? " disabled" : ""}></button>
          </div>
          <div class="tuning-shortcut-foot${liteReload ? " is-warn" : ""}">
            <span>${liteReload ? "已保存切换，请重载插件后生效。" : liteSaved ? "当前已开启极简网页模式，关闭并重载可恢复完整小屋。" : "当前为完整陪伴小屋模式；切换后需重载插件生效。"}</span>
          </div>
        </section>

        <section class="tuning-shortcut-card" data-tuning-storage-link>
          <div class="tuning-shortcut-head">
            <div>
              <h4>数据库体检与一键瘦身</h4>
              <p>检查主库与副库体积、重复上下文、冷遥测明细和空闲页碎片，一键无损瘦身或执行全量深度重组释放磁盘空间。</p>
            </div>
          </div>
          <div class="tuning-shortcut-foot">
            <span>明细保留天数（当前 ${text(effective.telemetry_ttl_days ?? 14)} 天）会在瘦身时自动生效。</span>
            <a class="button small" href="#manage/data/storage">前往数据库瘦身</a>
          </div>
        </section>
      </div>
    </div>`;
  }

  function applyServerData(result) {
    state.data = result;
    state.draftProfile = result.profile || "auto";
    state.draftOverrides = {...(result.overrides || {})};
    state.pendingSwitchProfile = null;
  }

  async function load(ticket = version) {
    state.loading = true;
    render();
    try {
      const result = await call("GET");
      if (!live(ticket)) return;
      applyServerData(result);
      if (state.highlightField) {
        state.highlightField = normalizeFieldKey(state.highlightField);
      }
      state.loading = false;
      state.error = false;
      state.message = "";
    } catch (error) {
      if (!live(ticket)) return;
      state.loading = false;
      state.error = true;
      const code = extractMachineCode(error);
      state.message = code === "dashboard_identity_required"
        ? "请从 AstrBot 管理界面登录后查看性能调优。"
        : "性能调优数据读取失败，请检查管理界面连接后重试。";
    }
    render();
    if (!state.error && state.highlightField) {
      scrollToHighlightedField();
    }
  }

  async function save(payload, successMessage, ticket = version) {
    state.busy = true;
    state.message = "";
    render();
    try {
      const result = await call("POST", payload);
      if (!live(ticket)) return;
      applyServerData(result);
      state.error = false;
      const clampCount = (result.clamped_fields || []).length;
      state.message = clampCount
        ? `${successMessage}（其中 ${clampCount} 项已由防爆内存护栏自动安全限幅）`
        : successMessage;
    } catch (error) {
      if (!live(ticket)) return;
      state.error = true;
      const code = extractMachineCode(error);
      const messages = {
        tuning_request_invalid: "调优参数超出安全范围，请核对数值后重试。",
        tuning_config_save_failed: "性能配置未能写入配置文件，请检查文件权限。",
        tuning_config_save_unavailable: "当前宿主暂不支持保存插件配置。",
        dashboard_identity_required: "请从已登录的 AstrBot 管理页面保存性能调优。",
        core_access_required: "当前管理身份没有修改性能调优的权限。",
      };
      state.message = messages[code] || "保存未完成，请稍后重试。";
      if (code && !state.message.includes(code)) state.message += `（${code}）`;
    }
    state.busy = false;
    render();
  }

  function scrollToHighlightedField() {
    const validKey = normalizeFieldKey(state.highlightField);
    if (!validKey || !root?.querySelector) return;
    const target = root.querySelector(`[data-tuning-field="${validKey}"]`);
    if (target) {
      target.scrollIntoView?.({behavior: "smooth", block: "center"});
      target.querySelector?.("input, button")?.focus?.({preventScroll: true});
    }
  }

  function syncAdvancedPanelOpen() {
    const panel = root?.querySelector?.("[data-tuning-advanced]");
    if (!panel) return false;
    panel.classList?.toggle?.("is-open", state.advancedOpen);
    const toggleBtn = root.querySelector("[data-tuning-advanced-toggle]");
    if (toggleBtn?.setAttribute) {
      toggleBtn.setAttribute("aria-expanded", String(state.advancedOpen));
    }
    const labelEl = root.querySelector("[data-tuning-advanced-label]");
    if (labelEl) {
      labelEl.textContent = state.advancedOpen ? "收起高级性能微调" : "展开高级性能微调";
    }
    return true;
  }

  function focusField(fieldKey) {
    const validKey = normalizeFieldKey(fieldKey);
    if (!validKey) return;
    state.advancedOpen = true;
    state.highlightField = validKey;
    if (!state.loading && state.data) {
      if (syncAdvancedPanelOpen()) {
        if (typeof root?.querySelectorAll === "function") {
          for (const el of root.querySelectorAll("[data-tuning-field]")) {
            el.classList?.toggle?.("is-highlighted", el.getAttribute?.("data-tuning-field") === validKey);
          }
        }
      } else {
        render();
      }
      scrollToHighlightedField();
    }
  }

  function syncDraftIndicators(changedKey, value, sourceType, {commit = false} = {}) {
    if (!root?.querySelector) return false;
    let touched = false;
    const validKey = normalizeFieldKey(changedKey);
    if (validKey) {
      if (sourceType === "range" || (commit && sourceType !== "enum")) {
        const numberEl = root.querySelector(`[data-tuning-number="${validKey}"]`);
        if (numberEl) {
          numberEl.value = String(value);
          touched = true;
        }
      }
      if (sourceType === "number" || (commit && sourceType !== "enum")) {
        const rangeEl = root.querySelector(`[data-tuning-range="${validKey}"]`);
        if (rangeEl) {
          rangeEl.value = String(value);
          touched = true;
        }
      }
      if (sourceType === "enum" && typeof root.querySelectorAll === "function") {
        const radios = root.querySelectorAll(`[data-tuning-enum-key="${validKey}"]`);
        for (const radio of radios) {
          const active = String(radio.getAttribute?.("data-tuning-enum-val")) === String(value);
          radio.classList?.toggle?.("is-active", active);
          radio.setAttribute?.("aria-checked", String(active));
          touched = true;
        }
      }
      const base = baselineFor(state.draftProfile);
      const isOverridden = base[validKey] !== value;
      const fieldEl = root.querySelector(`[data-tuning-field="${validKey}"]`);
      if (fieldEl?.classList?.toggle) {
        fieldEl.classList.toggle("is-overridden", isOverridden);
        touched = true;
      }
      const dotSlot = root.querySelector(`[data-tuning-field-dot="${validKey}"]`);
      if (dotSlot) {
        dotSlot.innerHTML = renderFieldOverrideDot(isOverridden);
        touched = true;
      }
      const badgeSlot = root.querySelector(`[data-tuning-field-badge="${validKey}"]`);
      if (badgeSlot) {
        badgeSlot.innerHTML = renderFieldOverrideBadge(isOverridden);
        touched = true;
      }
      const resetSlot = root.querySelector(`[data-tuning-field-reset-slot="${validKey}"]`);
      if (resetSlot) {
        resetSlot.innerHTML = renderFieldResetButton(validKey, isOverridden);
        touched = true;
      }
    }
    const overrideCount = activeOverrideCount();
    const dirty = isDirty();
    const badge = root.querySelector("[data-tuning-active-badge]");
    if (badge) {
      badge.textContent = computeActiveBadgeLabel(overrideCount);
      touched = true;
    }
    const resetAllSlot = root.querySelector("[data-tuning-reset-all-slot]");
    if (resetAllSlot) {
      resetAllSlot.innerHTML = renderResetAllButton(overrideCount);
      touched = true;
    }
    const bar = root.querySelector("[data-tuning-actions-bar]");
    if (bar?.classList?.toggle) {
      bar.classList.toggle("is-dirty", dirty);
      touched = true;
    }
    const status = root.querySelector("[data-tuning-actions-status]");
    if (status) {
      status.textContent = dirtyStatusText(dirty);
      touched = true;
    }
    const saveBtn = root.querySelector("[data-tuning-save]");
    if (saveBtn) {
      saveBtn.disabled = state.busy || !dirty;
      if (typeof saveBtn.toggleAttribute === "function") {
        saveBtn.toggleAttribute("disabled", state.busy || !dirty);
      }
      touched = true;
    }
    const discardSlot = root.querySelector("[data-tuning-discard-slot]");
    if (discardSlot) {
      discardSlot.innerHTML = renderDiscardButton(dirty);
      touched = true;
    }
    return touched;
  }

  function handleNumericInput(key, rawValue, {inPlace = true, sourceType = "", commit = false} = {}) {
    const spec = (state.data?.fields || []).find(item => item.key === key);
    if (!spec) return;
    const base = baselineFor(state.draftProfile);
    const parsed = Number.parseInt(String(rawValue), 10);
    if (!Number.isFinite(parsed)) {
      if (commit) {
        const current = Object.prototype.hasOwnProperty.call(state.draftOverrides, key)
          ? state.draftOverrides[key]
          : (base[key] ?? state.data?.values?.[key]);
        if (current !== undefined && !syncDraftIndicators(key, current, sourceType, {commit: true})) {
          render();
        }
      }
      return;
    }
    const min = spec.minimum ?? parsed;
    const max = spec.maximum ?? parsed;
    if (inPlace && !commit && sourceType === "number" && (parsed < min || parsed > max)) {
      return;
    }
    const clamped = Math.min(max, Math.max(min, parsed));
    if (base[key] === clamped) delete state.draftOverrides[key];
    else state.draftOverrides[key] = clamped;
    if (inPlace) {
      const synced = syncDraftIndicators(key, clamped, sourceType, {commit});
      if (!synced && commit) render();
    } else {
      render();
    }
  }

  function mount(node, options = {}) {
    stop();
    root = node;
    const ticket = version;
    state = emptyState();
    const initialFocus = normalizeFieldKey(options.focusField);
    if (initialFocus) {
      state.advancedOpen = true;
      state.highlightField = initialFocus;
    }

    const signal = typeof AbortController === "function"
      ? (listenerAbort = new AbortController()).signal
      : null;
    const listen = (type, handler) => {
      if (signal) {
        root.addEventListener(type, handler, {signal});
      } else {
        root.addEventListener(type, handler);
      }
      boundListeners.push([type, handler]);
    };

    listen("input", event => {
      if (!live(ticket) || state.busy) return;
      const key = event.target?.getAttribute?.("data-tuning-input");
      if (!key) return;
      if (event.target.type === "range") {
        handleNumericInput(key, event.target.value, {inPlace: true, sourceType: "range"});
      } else if (event.target.type === "number") {
        handleNumericInput(key, event.target.value, {inPlace: true, sourceType: "number"});
      }
    });

    listen("change", event => {
      if (!live(ticket) || state.busy) return;
      const key = event.target?.getAttribute?.("data-tuning-input");
      if (!key) return;
      const sourceType = event.target.type === "range" ? "range" : "number";
      handleNumericInput(key, event.target.value, {inPlace: true, sourceType, commit: true});
    });

    listen("click", event => {
      if (!live(ticket) || state.busy) return;
      const button = event.target?.closest?.("button");
      if (!button) return;

      const profileId = button.getAttribute?.("data-tuning-profile");
      if (profileId) {
        if (profileId === state.draftProfile) {
          state.pendingSwitchProfile = null;
          render();
          return;
        }
        if (activeOverrideCount() > 0) {
          state.pendingSwitchProfile = profileId;
          render();
        } else {
          state.draftProfile = profileId;
          state.draftOverrides = {};
          state.pendingSwitchProfile = null;
          render();
        }
        return;
      }

      if (button.hasAttribute("data-tuning-switch-clear")) {
        if (state.pendingSwitchProfile) {
          state.draftProfile = state.pendingSwitchProfile;
          state.draftOverrides = {};
          state.pendingSwitchProfile = null;
          render();
        }
        return;
      }
      if (button.hasAttribute("data-tuning-switch-keep")) {
        if (state.pendingSwitchProfile) {
          state.draftProfile = state.pendingSwitchProfile;
          state.draftOverrides = compactOverrides(state.draftProfile, state.draftOverrides);
          state.pendingSwitchProfile = null;
          render();
        }
        return;
      }
      if (button.hasAttribute("data-tuning-switch-cancel")) {
        state.pendingSwitchProfile = null;
        render();
        return;
      }
      if (button.hasAttribute("data-tuning-advanced-toggle")) {
        state.advancedOpen = !state.advancedOpen;
        if (!syncAdvancedPanelOpen()) render();
        return;
      }
      const enumKey = button.getAttribute?.("data-tuning-enum-key");
      if (enumKey) {
        const enumVal = button.getAttribute("data-tuning-enum-val");
        const base = baselineFor(state.draftProfile);
        if (base[enumKey] === enumVal) delete state.draftOverrides[enumKey];
        else state.draftOverrides[enumKey] = enumVal;
        if (!syncDraftIndicators(enumKey, enumVal, "enum", {commit: true})) render();
        return;
      }
      const resetKey = button.getAttribute?.("data-tuning-reset-field");
      if (resetKey) {
        delete state.draftOverrides[resetKey];
        const spec = (state.data?.fields || []).find(item => item.key === resetKey);
        const baseVal = baselineFor(state.draftProfile)[resetKey];
        const srcType = spec?.kind === "enum" ? "enum" : "number";
        if (baseVal === undefined || !syncDraftIndicators(resetKey, baseVal, srcType, {commit: true})) {
          render();
        }
        return;
      }
      if (button.hasAttribute("data-tuning-reset-all")) {
        state.draftOverrides = {};
        state.pendingSwitchProfile = null;
        render();
        return;
      }
      if (button.hasAttribute("data-tuning-discard")) {
        if (state.data) applyServerData(state.data);
        state.message = "";
        render();
        return;
      }
      if (button.hasAttribute("data-tuning-reload")) {
        load(ticket);
        return;
      }
      if (button.hasAttribute("data-tuning-save")) {
        const cleanOverrides = compactOverrides(state.draftProfile, state.draftOverrides);
        save(
          {action: "save", profile: state.draftProfile, overrides: cleanOverrides},
          `已保存「${profileLabel(state.draftProfile)}」性能配置并立即热生效。`,
          ticket,
        );
        return;
      }
      if (button.hasAttribute("data-tuning-lite-toggle")) {
        const nextEnabled = !Boolean(state.data?.lite_page?.saved_enabled);
        save(
          {action: "lite_page", lite_page_enabled: nextEnabled},
          nextEnabled
            ? "已开启极简网页模式，请重载插件后生效。"
            : "已关闭极简网页模式，请重载插件恢复完整小屋。",
          ticket,
        );
      }
    });

    render();
    load(ticket);
  }

  return {mount, unmount: stop, focusField};
})();
