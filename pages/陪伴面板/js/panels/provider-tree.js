window.PrivateCompanionProviderTree = (() => {
  const embeddingProviderKeys = new Set(["EMBEDDING_PROVIDER_ID", "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID"]);

  function providerValuesForRender(context) {
    const { state } = context;
    return {
      ...(state.overview?.providers || {}),
      ...(state.providerDraft || {}),
    };
  }

  function normalizeTimeoutValue(value) {
    if (value === null || value === undefined || String(value).trim() === "") return "";
    const parsed = Math.round(Number(value));
    return Number.isFinite(parsed) && parsed >= 5 && parsed <= 600 ? parsed : "";
  }

  function providerTimeoutValuesForRender(context) {
    const { state } = context;
    const saved = state.overview?.settings?.model_timeout_overrides;
    let source = saved;
    if (typeof source === "string") {
      try { source = JSON.parse(source || "{}"); } catch (_error) { source = {}; }
    }
    const values = {};
    Object.entries(source && typeof source === "object" ? source : {}).forEach(([key, value]) => {
      const normalized = normalizeTimeoutValue(value);
      if (normalized !== "") values[key] = normalized;
    });
    return { ...values, ...(state.providerTimeoutDraft || {}) };
  }

  function currentProviderTimeoutValues(context) {
    const { document } = context;
    const values = providerTimeoutValuesForRender(context);
    document.querySelectorAll("[data-provider-timeout]").forEach((input) => {
      const normalized = normalizeTimeoutValue(input.value);
      if (normalized === "") delete values[input.dataset.providerTimeout];
      else values[input.dataset.providerTimeout] = normalized;
    });
    return values;
  }

  function normalizeTokenLimitValue(value) {
    if (value === null || value === undefined || String(value).trim() === "") return "";
    const parsed = Math.round(Number(value));
    return Number.isFinite(parsed) && parsed >= 256 && parsed <= 2000000 ? parsed : "";
  }

  function providerTokenLimitValuesForRender(context) {
    const { state } = context;
    const saved = state.overview?.settings?.model_token_limit_overrides;
    let source = saved;
    if (typeof source === "string") {
      try { source = JSON.parse(source || "{}"); } catch (_error) { source = {}; }
    }
    const values = {};
    Object.entries(source && typeof source === "object" ? source : {}).forEach(([key, value]) => {
      const normalized = normalizeTokenLimitValue(value);
      if (normalized !== "") values[key] = normalized;
    });
    const draft = state.providerTokenLimitDraft;
    if (draft && typeof draft === "object" && !Array.isArray(draft)) {
      Object.entries(draft).forEach(([key, value]) => {
        const normalized = normalizeTokenLimitValue(value);
        if (normalized === "") delete values[key];
        else values[key] = normalized;
      });
    }
    return values;
  }

  function currentProviderTokenLimitValues(context) {
    const { document } = context;
    const values = providerTokenLimitValuesForRender(context);
    document.querySelectorAll("[data-provider-token-limit]").forEach((input) => {
      const normalized = normalizeTokenLimitValue(input.value);
      if (normalized === "") delete values[input.dataset.providerTokenLimit];
      else values[input.dataset.providerTokenLimit] = normalized;
    });
    return values;
  }

  function normalizeFallbackValues(raw) {
    let source = raw;
    if (typeof source === "string") {
      try { source = JSON.parse(source || "{}"); } catch (_error) { source = {}; }
    }
    if (!source || typeof source !== "object" || Array.isArray(source)) return {};
    return Object.fromEntries(
      Object.entries(source)
        .map(([key, value]) => [key, String(value || "").trim()])
        .filter(([, value]) => Boolean(value)),
    );
  }

  function providerFallbackValuesForRender(context) {
    const { state } = context;
    const values = normalizeFallbackValues(state.overview?.settings?.model_fallback_overrides);
    const draft = state.providerFallbackDraft;
    if (!draft || typeof draft !== "object" || Array.isArray(draft)) return values;
    Object.entries(draft).forEach(([key, value]) => {
      const providerId = String(value || "").trim();
      if (providerId) values[key] = providerId;
      else delete values[key];
    });
    return values;
  }

  function currentProviderFallbackValues(context) {
    const { document } = context;
    const values = providerFallbackValuesForRender(context);
    document.querySelectorAll("[data-provider-fallback-key]").forEach((input) => {
      const key = input.dataset.providerFallbackKey || "";
      const providerId = input.value.trim();
      if (!providerId) delete values[key];
      else values[key] = providerId;
    });
    return values;
  }

  function providerGuideMarkup(context, key) {
    const { providerGuides, providerPreferenceMeta, providerPassiveImpactMeta, escapeHtml } = context;
    const guide = providerGuides[key];
    if (!guide) return "";
    const preference = providerPreferenceMeta[guide.preference || ""];
    const impact = providerPassiveImpactMeta[guide.passiveImpact || ""];
    return `
      <span class="provider-guide">
        <span><b>用途</b>${escapeHtml(guide.purpose)}</span>
        <span><b>适合</b>${escapeHtml(guide.fit)}</span>
        ${preference ? `<span><b>取向</b>${escapeHtml(preference.text)}</span>` : ""}
        ${impact ? `<span><b>速度</b>${escapeHtml(impact.text)}</span>` : ""}
        ${guide.note ? `<span><b>注意</b>${escapeHtml(guide.note)}</span>` : ""}
        <span><b>回退</b>${escapeHtml(guide.fallback)}</span>
      </span>
    `;
  }

  function providerSelect(context, key, value) {
    const { state, noFallbackProviderKeys, optionalNoFallbackProviderKeys, escapeHtml } = context;
    const embeddingProvider = embeddingProviderKeys.has(key);
    const providerItems = embeddingProvider
      ? (state.availableEmbeddingProviders || [])
      : state.availableProviders;
    const known = providerItems.some((item) => item.id === value);
    const customValue = value && !known ? value : "";
    const options = [
      `<option value="">${key === "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID" ? "留空继承通用模型/自动探测" : embeddingProvider ? "留空不启用" : noFallbackProviderKeys.has(key) || optionalNoFallbackProviderKeys.has(key) ? "留空不启用" : "留空自动回退"}</option>`,
      ...providerItems.map((item) => {
        const label = `${item.name || item.id}${item.model ? ` · ${item.model}` : ""}${item.is_default ? " · 默认" : ""}`;
        return `<option value="${escapeHtml(item.id)}" ${item.id === value ? "selected" : ""}>${escapeHtml(label)}</option>`;
      }),
      `<option value="__custom__" ${customValue ? "selected" : ""}>手动输入 Provider ID</option>`,
    ].join("");
    return `
      <select data-provider-select="${escapeHtml(key)}">${options}</select>
      <input data-provider-key="${escapeHtml(key)}" value="${escapeHtml(value || "")}" placeholder="自定义 Provider ID" ${customValue ? "" : "hidden"} />
    `;
  }

  function providerFallbackSelect(context, key, value) {
    const { state, escapeHtml } = context;
    const known = state.availableProviders.some((item) => item.id === value);
    const customValue = value && !known ? value : "";
    const options = [
      `<option value="">不启用备用模型</option>`,
      ...state.availableProviders.map((item) => {
        const label = `${item.name || item.id}${item.model ? ` · ${item.model}` : ""}${item.is_default ? " · 默认" : ""}`;
        return `<option value="${escapeHtml(item.id)}" ${item.id === value ? "selected" : ""}>${escapeHtml(label)}</option>`;
      }),
      `<option value="__custom__" ${customValue ? "selected" : ""}>手动输入 Provider ID</option>`,
    ].join("");
    return `
      <select data-provider-fallback-select="${escapeHtml(key)}">${options}</select>
      <input data-provider-fallback-key="${escapeHtml(key)}" value="${escapeHtml(value || "")}" placeholder="自定义备用 Provider ID" ${customValue ? "" : "hidden"} />
    `;
  }

  function currentProviderValues(context) {
    const { document, state } = context;
    const values = {
      ...(state.overview?.providers || {}),
      ...(state.providerDraft || {}),
    };
    document.querySelectorAll("[data-provider-key]").forEach((input) => {
      values[input.dataset.providerKey] = input.value.trim();
    });
    return values;
  }

  function resolveProviderId(context, key, values = currentProviderValues(context)) {
    const { noFallbackProviderKeys, optionalNoFallbackProviderKeys } = context;
    if (values[key]) return values[key];
    if (embeddingProviderKeys.has(key)) return "";
    if (noFallbackProviderKeys.has(key)) return "";
    if (optionalNoFallbackProviderKeys.has(key)) return "";
    const mode = typeof context.currentProviderConfigMode === "function"
      ? context.currentProviderConfigMode()
      : String(context.state?.providerConfigMode || "precision").trim().toLowerCase();
    const quick = mode === "quick";
    // 精准模式不能读取隐藏的快速配置值，否则切换模型后，留空项仍会显示并测试旧 Provider。
    const complex = quick
      ? (values.COMPLEX_REASONING_PROVIDER_ID || values.LLM_PROVIDER_ID || "")
      : (values.LLM_PROVIDER_ID || "");
    const fast = quick
      ? (values.FAST_RESPONSE_PROVIDER_ID || complex || "")
      : (values.MAI_STYLE_PROVIDER_ID || complex || "");
    const creative = quick
      ? (values.CREATIVE_MODEL_PROVIDER_ID || complex || fast)
      : (values.CREATIVE_PROVIDER_ID || values.MAI_STYLE_PROVIDER_ID || complex || fast);
    if (key === "LLM_PROVIDER_ID") return complex || "";
    if (key === "MAI_STYLE_PROVIDER_ID") return fast || complex || "";
    if (["DAILY_PLAN_PROVIDER_ID", "DETAIL_ENHANCEMENT_PROVIDER_ID", "HISTORY_SUMMARY_PROVIDER_ID", "RELATIONSHIP_ANALYSIS_PROVIDER_ID", "COMPANION_MEMORY_PROVIDER_ID", "DIALOGUE_EPISODE_PROVIDER_ID", "GROUP_EPISODE_PROVIDER_ID", "FORWARD_MESSAGE_PROVIDER_ID"].includes(key)) return complex || fast || "";
    if (["CREATIVE_PROVIDER_ID", "CREATIVE_OUTLINE_PROVIDER_ID", "CREATIVE_REVIEW_PROVIDER_ID", "DREAM_DIARY_PROVIDER_ID", "PHOTO_PROMPT_PROVIDER_ID"].includes(key)) return creative || values.MAI_STYLE_PROVIDER_ID || fast || complex || "";
    if (["RESPONSE_REVIEW_PROVIDER_ID", "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID", "TROUBLESHOOTING_PROVIDER_ID", "EMOTION_JUDGEMENT_PROVIDER_ID", "GROUP_INTERJECT_PROVIDER_ID", "GROUP_SLANG_PROVIDER_ID", "VOICE_PROMPT_PROVIDER_ID", "tts_conversion_provider_id", "NARRATION_PROVIDER_ID", "NEWS_PROVIDER_ID", "WEB_EXPLORATION_PROVIDER_ID"].includes(key)) return fast || values.MAI_STYLE_PROVIDER_ID || complex || "";
    if (key === "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID") return fast || complex || "";
    if (key === "SMART_SILENCE_PROVIDER_ID") return values.RESPONSE_REVIEW_PROVIDER_ID || values.SMART_MESSAGE_DEBOUNCE_PROVIDER_ID || fast || values.MAI_STYLE_PROVIDER_ID || complex || "";
    if (key === "REST_WAKEUP_PROVIDER_ID") return values.RESPONSE_REVIEW_PROVIDER_ID || fast || complex || "";
    if (key === "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID") return fast || "";
    if (key !== "LLM_PROVIDER_ID" && values.MAI_STYLE_PROVIDER_ID) return values.MAI_STYLE_PROVIDER_ID;
    return complex || "";
  }

  function setProviderStatus(context, key, message, level = "info", details = false) {
    const { document } = context;
    const status = document.querySelector(`[data-provider-status="${key}"]`);
    if (!status) return;
    status.className = `provider-status ${level}`;
    status.replaceChildren();
    const text = document.createElement("span");
    text.textContent = message;
    status.appendChild(text);
    if (details) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "查看诊断";
      button.dataset.testResultSource = "provider";
      button.dataset.testResultKey = key;
      status.appendChild(button);
    }
  }

  function rememberProviderDraft(context, key) {
    const { document, state } = context;
    const input = document.querySelector(`[data-provider-key="${key}"]`);
    if (!input) return;
    state.providerDraft[key] = input.value.trim();
  }

  function rememberProviderTimeoutDraft(context, input) {
    const { state } = context;
    const key = input.dataset.providerTimeout || "";
    if (!key) return;
    const normalized = normalizeTimeoutValue(input.value);
    state.providerTimeoutDraft = { ...(state.providerTimeoutDraft || {}) };
    state.providerTimeoutDraft[key] = normalized;
  }

  function rememberProviderTokenLimitDraft(context, input) {
    const { state } = context;
    const key = input.dataset.providerTokenLimit || "";
    if (!key) return;
    const normalized = normalizeTokenLimitValue(input.value);
    state.providerTokenLimitDraft = { ...(state.providerTokenLimitDraft || {}) };
    state.providerTokenLimitDraft[key] = normalized;
  }

  function rememberProviderFallbackDraft(context, key) {
    const { document, state } = context;
    const input = document.querySelector(`[data-provider-fallback-key="${key}"]`);
    if (!input) return;
    state.providerFallbackDraft = { ...(state.providerFallbackDraft || {}) };
    const value = input.value.trim();
    // Keep an empty draft as a deletion marker. Removing the key here would
    // expose the still-saved overview value again on the immediate re-render.
    state.providerFallbackDraft[key] = value;
  }

  function syncProviderInput(context, select) {
    const { document } = context;
    const key = select.dataset.providerSelect;
    const input = document.querySelector(`[data-provider-key="${key}"]`);
    if (!input) return;
    if (select.value === "__custom__") {
      input.hidden = false;
      input.focus();
    } else {
      input.hidden = true;
      input.value = select.value;
    }
  }

  function syncProviderFallbackInput(context, select) {
    const { document } = context;
    const key = select.dataset.providerFallbackSelect;
    const input = document.querySelector(`[data-provider-fallback-key="${key}"]`);
    if (!input) return;
    if (select.value === "__custom__") {
      input.hidden = false;
      input.focus();
    } else {
      input.hidden = true;
      input.value = select.value;
    }
  }

  async function testProvider(context, key) {
    const { optionalNoFallbackProviderKeys, noFallbackProviderKeys, postJson, state } = context;
    const providerId = resolveProviderId(context, key);
    setProviderStatus(context, key, "测试中...", "info");
    if (!providerId && (noFallbackProviderKeys.has(key) || optionalNoFallbackProviderKeys.has(key))) {
      setProviderStatus(context, key, optionalNoFallbackProviderKeys.has(key) ? "未单独配置：当前不会调用该模型" : "未配置，无法测试", "warn");
      return;
    }
    try {
      const timeoutSeconds = currentProviderTimeoutValues(context)[key] || null;
      const result = await postJson("/provider/test", { key, provider_id: providerId, timeout_seconds: timeoutSeconds });
      state.providerTestResults = { ...(state.providerTestResults || {}), [key]: result };
      if (result.ok) {
        setProviderStatus(context, key, `正常 ${result.duration_ms || result.elapsed_ms || 0}ms`, "ok", true);
      } else {
        setProviderStatus(context, key, result.next_step || result.error || "未返回内容", "warn", true);
      }
    } catch (error) {
      const result = { ok: false, error: error.message, test_status: "failed", error_category: "请求失败" };
      state.providerTestResults = { ...(state.providerTestResults || {}), [key]: result };
      setProviderStatus(context, key, error.message, "warn", true);
    }
  }

  function providerMatchesFilter(context, key, label, providers) {
    const { state, providerGroupByKey, providerGuides, providerPreferenceMeta, providerPassiveImpactMeta, providerNeedsLowLatency } = context;
    const mode = state.providerMode || "all";
    const configured = Boolean(providers[key]);
    const group = providerGroupByKey[key];
    if (mode === "configured" && !configured) return false;
    if (mode === "inherited" && configured) return false;
    if (mode === "vision" && group?.id !== "media") return false;
    const guide = providerGuides[key] || {};
    if (mode === "speed" && !providerNeedsLowLatency(key)) return false;
    if (mode === "quality" && guide.preference !== "quality") return false;
    const query = (state.providerFilter || "").trim().toLowerCase();
    if (!query) return true;
    const preference = providerPreferenceMeta[guide.preference || ""];
    const impact = providerPassiveImpactMeta[guide.passiveImpact || ""];
    const haystack = [key, label, group?.title || "", guide.purpose || "", guide.fit || "", guide.note || "", guide.fallback || "", preference?.label || "", preference?.text || "", impact?.label || "", impact?.text || "", providers[key] || ""].join(" ").toLowerCase();
    return haystack.includes(query);
  }

  function providerCardMarkup(context, key, label, providers) {
    const { providerGroupByKey, noFallbackProviderKeys, optionalNoFallbackProviderKeys, providerGuides, providerPreferenceMeta, providerPassiveImpactMeta, escapeHtml } = context;
    const selected = providers[key] || "";
    const resolved = resolveProviderId(context, key, providers);
    const configured = Boolean(selected);
    const embeddingProvider = embeddingProviderKeys.has(key);
    const noFallback = noFallbackProviderKeys.has(key);
    const optionalNoFallback = optionalNoFallbackProviderKeys.has(key);
    const group = providerGroupByKey[key];
    const statusLabel = configured ? "已单独配置" : (key === "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID" ? "继承通用/自动探测" : (embeddingProvider ? "未配置" : (noFallback ? "未配置" : (optionalNoFallback ? "可选未启用" : "自动回退"))));
    const guide = providerGuides[key] || {};
    const preference = providerPreferenceMeta[guide.preference || "balanced"];
    const impact = providerPassiveImpactMeta[guide.passiveImpact || ""];
    const preview = [guide.purpose || "", guide.fit || ""].filter(Boolean).join(" ");
    const timeoutValue = providerTimeoutValuesForRender(context)[key] || "";
    const tokenLimitValue = providerTokenLimitValuesForRender(context)[key] || "";
    const fallbackValue = providerFallbackValuesForRender(context)[key] || "";
    return `
      <article class="provider-card ${configured ? "configured" : "inherited"}" data-provider-card="${escapeHtml(key)}">
        <div class="provider-tree-node">
          <span class="provider-tree-branch">
            <span class="provider-tree-title-wrap">
              <span class="provider-card-kicker">${escapeHtml(group?.title || "模型配置")}</span>
              <strong class="provider-tree-title">${escapeHtml(label)}</strong>
            </span>
          </span>
          <span class="provider-badge ${configured ? "configured" : "inherited"}">${escapeHtml(statusLabel)}</span>
        </div>
        <div class="provider-tree-preview">${escapeHtml(preview || "为当前能力指定专用 Provider")}</div>
        <div class="provider-tags provider-tree-tags">
          ${preference ? `<span class="provider-tag ${escapeHtml(preference.className)}" title="${escapeHtml(preference.text)}">${escapeHtml(preference.label)}</span>` : ""}
          ${impact ? `<span class="provider-tag impact-${escapeHtml(impact.className)}" title="${escapeHtml(impact.text)}">${escapeHtml(impact.label)}</span>` : ""}
        </div>
        <div class="provider-tree-body">
          <label class="provider-field">
            <span>Provider</span>
            ${providerSelect(context, key, selected)}
          </label>
          ${embeddingProvider ? "" : `<div class="provider-limit-grid">
            <label class="provider-field provider-limit-field provider-timeout-field">
              <span>请求超时</span>
              <span class="provider-limit-control provider-timeout-control">
                <input type="number" min="5" max="600" step="1" inputmode="numeric" data-provider-timeout="${escapeHtml(key)}" value="${escapeHtml(timeoutValue)}" placeholder="默认" aria-label="${escapeHtml(label)}请求超时秒数" />
                <b>秒</b>
              </span>
            </label>
            <label class="provider-field provider-limit-field provider-token-limit-field">
              <span>单次 Token 上限（预估）</span>
              <span class="provider-limit-control provider-token-limit-control">
                <input type="number" min="256" max="2000000" step="256" inputmode="numeric" data-provider-token-limit="${escapeHtml(key)}" value="${escapeHtml(tokenLimitValue)}" placeholder="不限制" aria-label="${escapeHtml(label)}单次 Token 上限" />
                <b>Token</b>
              </span>
            </label>
          </div>`}
          ${embeddingProvider ? "" : `<label class="provider-field provider-fallback-field">
            <span>备用模型 <small>主模型失败、超时、Token 超限或空响应时尝试一次</small></span>
            ${providerFallbackSelect(context, key, fallbackValue)}
          </label>`}
          <div class="provider-current">
            <span>当前使用</span>
            <b>${escapeHtml(resolved || (key === "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID" ? "继承通用模型 / 自动探测" : (embeddingProvider ? "未配置" : (noFallback ? "未配置" : (optionalNoFallback ? "未启用" : "AstrBot 默认模型")))))}</b>
          </div>
          ${providerGuideMarkup(context, key)}
          <div class="provider-row">
            <span class="hint">${escapeHtml(key)}</span>
            <button type="button" data-provider-test="${escapeHtml(key)}">测试</button>
          </div>
          <span class="provider-status" data-provider-status="${escapeHtml(key)}"></span>
        </div>
      </article>
    `;
  }

  function renderProviderSummary(context, providers) {
    const { document, providerLabels, visibleConfigKey, providerAllowedInCurrentMode, noFallbackProviderKeys, optionalNoFallbackProviderKeys, state, providerGuides, providerNeedsLowLatency, currentProviderConfigMode, escapeHtml } = context;
    const keys = Object.keys(providerLabels).filter((key) => visibleConfigKey(key)).filter((key) => providerAllowedInCurrentMode(key));
    const configured = keys.filter((key) => Boolean(providers[key])).length;
    const inherited = keys.filter((key) => !providers[key] && !noFallbackProviderKeys.has(key) && !optionalNoFallbackProviderKeys.has(key)).length;
    const requiredMissing = keys.filter((key) => !providers[key] && noFallbackProviderKeys.has(key)).length;
    const available = state.availableProviders.length;
    const passiveSensitive = keys.filter((key) => providerGuides[key]?.passiveImpact === "direct").length;
    const speedRecommended = keys.filter((key) => providerNeedsLowLatency(key)).length;
    const qualityRecommended = keys.filter((key) => providerGuides[key]?.preference === "quality").length;
    const genericVision = providers.PLUGIN_VISION_PROVIDER_ID || "跟随 AstrBot 本体图片转文字";
    const readingVisionAvailable = visibleConfigKey("READING_ARCHIVE_VISION_PROVIDER_ID");
    const readingVision = providers.READING_ARCHIVE_VISION_PROVIDER_ID || "未配置";
    const vision = readingVisionAvailable
      ? `通用：${genericVision} · 资料归档：${readingVision}`
      : `通用：${genericVision}`;
    const visionHint = readingVisionAvailable
      ? "通用识图与资料归档识图使用独立 Provider"
      : "当前仅显示已安装能力使用的视觉通道";
    document.getElementById("providerSummary").innerHTML = `
      <div class="provider-summary-card strong"><span>单独配置</span><b>${configured}/${keys.length}</b><small>已指定专用 Provider</small></div>
      <div class="provider-summary-card"><span>自动回退</span><b>${inherited}</b><small>留空项会按兜底链路执行</small></div>
      <div class="provider-summary-card speed"><span>被动速度相关</span><b>${passiveSensitive}</b><small>这些项建议优先关注延迟</small></div>
      <div class="provider-summary-card quality"><span>效果优先项</span><b>${qualityRecommended}</b><small>适合更强推理或多模态模型</small></div>
      <div class="provider-summary-card"><span>低延迟优先项</span><b>${speedRecommended}</b><small>卡顿时优先检查这些项</small></div>
      ${requiredMissing ? `<div class="provider-summary-card warn"><span>未配置专用项</span><b>${requiredMissing}</b><small>这些任务留空时不会回退</small></div>` : ""}
      <div class="provider-summary-card"><span>可选 Provider</span><b>${available}</b><small>${escapeHtml(available ? "来自 AstrBot 当前配置" : "暂无可选项，可手动输入 ID")}</small></div>
      <div class="provider-summary-card"><span>视觉通道</span><b>${escapeHtml(vision)}</b><small>${escapeHtml(visionHint)}</small></div>
    `;
  }

  function deepseekPeakProviderControl(context, value) {
    const { state, escapeHtml } = context;
    const known = state.availableProviders.some((item) => item.id === value);
    const options = [
      `<option value="">请选择替代 Provider</option>`,
      ...state.availableProviders.map((item) => {
        const label = `${item.name || item.id}${item.model ? ` · ${item.model}` : ""}${item.is_default ? " · 默认" : ""}`;
        return `<option value="${escapeHtml(item.id)}" ${item.id === value ? "selected" : ""}>${escapeHtml(label)}</option>`;
      }),
      `<option value="__custom__" ${value && !known ? "selected" : ""}>手动输入 Provider ID</option>`,
    ].join("");
    return `
      <select data-deepseek-peak-provider-select>${options}</select>
      <input data-deepseek-peak-provider-input value="${escapeHtml(value || "")}" placeholder="自定义 Provider ID" ${value && !known ? "" : "hidden"} />
    `;
  }

  function currentDeepseekPeakValues(context) {
    const { document, state } = context;
    const settings = state.overview?.settings || {};
    const providers = state.overview?.providers || {};
    const checkbox = document.querySelector("[data-deepseek-peak-enabled]");
    const providerInput = document.querySelector("[data-deepseek-peak-provider-input]");
    const windows = document.querySelector("[data-deepseek-peak-windows]");
    const timezone = document.querySelector("[data-deepseek-peak-timezone]");
    const keywords = document.querySelector("[data-deepseek-peak-keywords]");
    return {
      enabled: checkbox ? checkbox.checked : Boolean(settings.enable_deepseek_peak_replacement),
      provider: providerInput ? providerInput.value.trim() : String(providers.DEEPSEEK_PEAK_REPLACEMENT_PROVIDER_ID || "").trim(),
      windows: windows ? windows.value.trim() : String(settings.deepseek_peak_windows || "09:00-12:00\n14:00-18:00").trim(),
      timezone: timezone ? timezone.value.trim() : String(settings.deepseek_peak_timezone || "Asia/Shanghai").trim(),
      keywords: keywords ? keywords.value.trim() : String(settings.deepseek_peak_match_keywords || "deepseek,深度求索").trim(),
    };
  }

  function replacementProviderControl(context, value, selectAttr, inputAttr, placeholder) {
    const { state, escapeHtml } = context;
    const known = state.availableProviders.some((item) => item.id === value);
    const options = [
      `<option value="">留空不启用</option>`,
      ...state.availableProviders.map((item) => {
        const label = `${item.name || item.id}${item.model ? ` · ${item.model}` : ""}${item.is_default ? " · 默认" : ""}`;
        return `<option value="${escapeHtml(item.id)}" ${item.id === value ? "selected" : ""}>${escapeHtml(label)}</option>`;
      }),
      `<option value="__custom__" ${value && !known ? "selected" : ""}>手动输入 Provider ID</option>`,
    ].join("");
    return `
      <select ${selectAttr}>${options}</select>
      <input ${inputAttr} value="${escapeHtml(value || "")}" placeholder="${escapeHtml(placeholder)}" ${value && !known ? "" : "hidden"} />
    `;
  }

  function modelReplacementValuesForRender(context) {
    const { state } = context;
    const settings = state.overview?.settings || {};
    const providers = state.overview?.providers || {};
    let rules = settings.model_replacement_rules;
    if (typeof rules === "string") {
      try { rules = JSON.parse(rules || "[]"); } catch (_error) { rules = []; }
    }
    if (!Array.isArray(rules)) rules = [];
    return {
      scope: String(settings.model_replacement_scope || "plugin").trim().toLowerCase() || "plugin",
      rules,
      sensitiveEnabled: Boolean(settings.enable_sensitive_model_replacement),
      sensitiveProvider: String(providers.SENSITIVE_REPLACEMENT_PROVIDER_ID || "").trim(),
      sensitiveKeywords: String(settings.sensitive_replacement_keywords || "很抱歉，我无法；很抱歉,我无法；我有我自己的底线；我们可以聊聊别的；我无法满足；露骨性行为；没办法提交这个请求；这个请求没办法提交；The prompt could not be submitted；prompt could not be submitted；The request could not be submitted；request could not be submitted"),
    };
  }

  function currentModelReplacementValues(context) {
    const { document } = context;
    const defaults = modelReplacementValuesForRender(context);
    const scope = document.querySelector("[data-model-replacement-scope]");
    const rules = document.querySelector("[data-model-replacement-rules]");
    const sensitiveEnabled = document.querySelector("[data-sensitive-replacement-enabled]");
    const sensitiveProvider = document.querySelector("[data-sensitive-replacement-provider-input]");
    const sensitiveKeywords = document.querySelector("[data-sensitive-replacement-keywords]");
    let parsedRules = defaults.rules;
    if (rules) {
      try {
        const candidate = JSON.parse(rules.value || "[]");
        if (Array.isArray(candidate)) parsedRules = candidate;
      } catch (_error) {
        // Keep the last valid value until the user fixes the JSON.
      }
    }
    return {
      scope: scope?.value || defaults.scope,
      rules: parsedRules,
      sensitiveEnabled: sensitiveEnabled ? sensitiveEnabled.checked : defaults.sensitiveEnabled,
      sensitiveProvider: sensitiveProvider ? sensitiveProvider.value.trim() : defaults.sensitiveProvider,
      sensitiveKeywords: sensitiveKeywords ? sensitiveKeywords.value.trim() : defaults.sensitiveKeywords,
    };
  }

  function renderModelReplacementCard(context) {
    const { document, escapeHtml } = context;
    const root = document.getElementById("modelReplacementCard");
    if (!root) return;
    const values = modelReplacementValuesForRender(context);
    const rulesText = JSON.stringify(values.rules, null, 2);
    root.innerHTML = `
      <article class="model-replacement-card ${values.sensitiveEnabled ? "enabled" : ""}">
        <div class="model-replacement-head">
          <div>
            <span class="model-replacement-kicker">模型路由 · 统一策略</span>
            <h3>模型替换策略</h3>
            <p>关键词换模、DeepSeek 高价时段替换和敏感拒答重试共用作用范围；未命中时保持原模型。</p>
          </div>
          <label class="model-replacement-scope"><span>作用范围</span>
            <select data-model-replacement-scope>
              <option value="plugin" ${values.scope === "plugin" ? "selected" : ""}>仅插件调用</option>
              <option value="conversation" ${values.scope === "conversation" ? "selected" : ""}>仅普通对话模型</option>
              <option value="all" ${values.scope === "all" ? "selected" : ""}>全部生效</option>
            </select>
          </label>
        </div>
        <div class="model-replacement-body">
          <label class="provider-field model-replacement-rules-field">
            <span class="model-replacement-field-label"><b>关键词换模规则</b><small>JSON 数组 · 支持 contains / exact / regex、优先级、任一/全部关键词</small></span>
            <textarea rows="9" data-model-replacement-rules spellcheck="false" placeholder="[{\"name\":\"代码问题\",\"keywords\":[\"写代码\"],\"provider_id\":\"替代模型\"}]">${escapeHtml(rulesText)}</textarea>
          </label>
          <section class="model-replacement-sensitive">
            <div class="model-replacement-sensitive-head">
              <div>
                <span class="model-replacement-section-kicker">安全兜底</span>
                <strong>敏感拒答重试</strong>
                <p>命中常见拒答表达时切换模型重试，避免把原始拒答直接发出。</p>
              </div>
              <label class="model-replacement-toggle">
                <input type="checkbox" data-sensitive-replacement-enabled ${values.sensitiveEnabled ? "checked" : ""} />
                <span class="model-replacement-toggle-track" aria-hidden="true"></span>
                <b>${values.sensitiveEnabled ? "已启用" : "未启用"}</b>
              </label>
            </div>
            <label class="provider-field">
              <span>敏感拒答替代模型</span>
              ${replacementProviderControl(context, values.sensitiveProvider, "data-sensitive-replacement-provider-select", "data-sensitive-replacement-provider-input", "自定义 Provider ID")}
            </label>
            <label class="provider-field">
              <span>敏感拒答匹配词 <small>每行或用分号分隔</small></span>
              <textarea rows="3" data-sensitive-replacement-keywords>${escapeHtml(values.sensitiveKeywords)}</textarea>
            </label>
          </section>
        </div>
        <div class="model-replacement-note">规则目标 Provider 必须是 AstrBot 当前可用模型；敏感拒答替换只作为重试路由，不会修改原模型配置。</div>
      </article>
    `;
    const toggle = root.querySelector("[data-sensitive-replacement-enabled]");
    toggle?.addEventListener("change", () => {
      root.querySelector(".model-replacement-card")?.classList.toggle("enabled", toggle.checked);
      const label = root.querySelector(".model-replacement-toggle b");
      if (label) label.textContent = toggle.checked ? "已启用" : "未启用";
    });
    const select = root.querySelector("[data-sensitive-replacement-provider-select]");
    const input = root.querySelector("[data-sensitive-replacement-provider-input]");
    select?.addEventListener("change", () => {
      const custom = select.value === "__custom__";
      input.hidden = !custom;
      if (!custom) input.value = select.value;
      if (custom) input.focus();
    });
  }

  function renderDeepseekPeakCard(context) {
    const { document, state, escapeHtml } = context;
    const root = document.getElementById("deepseekPeakCard");
    if (!root) return;
    const values = currentDeepseekPeakValues(context);
    const runtime = state.overview?.deepseek_peak_routing || {};
    let statusLabel = "未启用";
    let statusTone = "off";
    if (runtime.enabled && !runtime.configured) {
      statusLabel = "等待选择替代模型";
      statusTone = "warn";
    } else if (runtime.active) {
      statusLabel = "高价时段 · 正在替换";
      statusTone = "active";
    } else if (runtime.enabled) {
      statusLabel = "低价时段 · DeepSeek 直连";
      statusTone = "ready";
    }
    root.innerHTML = `
      <article class="deepseek-peak-card ${values.enabled ? "enabled" : ""}">
        <div class="deepseek-peak-head">
          <div>
            <span class="deepseek-peak-kicker">成本路由 · 可选</span>
            <h3>DeepSeek 高价时段替代</h3>
            <p>按上方统一作用范围临时替换 DeepSeek 的运行时路由，不改写原有模型分工；离开高价时段会自动恢复。</p>
          </div>
          <div class="deepseek-peak-head-actions">
            <span class="deepseek-peak-status ${escapeHtml(statusTone)}">${escapeHtml(statusLabel)}</span>
            <label class="switch deepseek-peak-switch"><input type="checkbox" data-deepseek-peak-enabled ${values.enabled ? "checked" : ""} /><span></span></label>
          </div>
        </div>
        <div class="deepseek-peak-body" ${values.enabled ? "" : "hidden"}>
          <label class="provider-field deepseek-peak-provider-field">
            <span>高价时段替代模型</span>
            ${deepseekPeakProviderControl(context, values.provider)}
          </label>
          <label class="provider-field deepseek-peak-window-field">
            <span>高价时段 <small>每行或逗号分隔，支持跨午夜</small></span>
            <textarea rows="3" data-deepseek-peak-windows placeholder="09:00-12:00&#10;14:00-18:00">${escapeHtml(values.windows)}</textarea>
          </label>
          <label class="provider-field deepseek-peak-timezone-field">
            <span>时区</span>
            <input data-deepseek-peak-timezone value="${escapeHtml(values.timezone)}" placeholder="Asia/Shanghai" />
          </label>
          <label class="provider-field deepseek-peak-keywords-field">
            <span>DeepSeek 匹配关键词 <small>匹配 ID、名称、模型和 API Base</small></span>
            <textarea rows="2" data-deepseek-peak-keywords>${escapeHtml(values.keywords)}</textarea>
          </label>
          <div class="deepseek-peak-runtime">
            <span>当前时间 <b>${escapeHtml(runtime.current_time || "保存后刷新")}</b></span>
            <span>下次切换 <b>${escapeHtml(runtime.next_transition || "暂无")}</b></span>
            <span>当前替代 <b>${escapeHtml(runtime.replacement_provider_id || values.provider || "未配置")}</b></span>
          </div>
        </div>
        <div class="deepseek-peak-note">高价时段仅控制临时路由，不会覆盖任务模型配置；是否作用于插件、普通对话或全部请求由“模型替换策略”的作用范围统一决定。</div>
      </article>
    `;
    const enabled = root.querySelector("[data-deepseek-peak-enabled]");
    enabled?.addEventListener("change", () => {
      root.querySelector(".deepseek-peak-card")?.classList.toggle("enabled", enabled.checked);
      const body = root.querySelector(".deepseek-peak-body");
      if (body) body.hidden = !enabled.checked;
    });
    const select = root.querySelector("[data-deepseek-peak-provider-select]");
    const input = root.querySelector("[data-deepseek-peak-provider-input]");
    select?.addEventListener("change", () => {
      const custom = select.value === "__custom__";
      input.hidden = !custom;
      if (!custom) input.value = select.value;
      if (custom) input.focus();
    });
  }

  function renderProviderFlow(context, providers) {
    const { document, currentProviderConfigMode, escapeHtml, providerLabels, visibleConfigKey, providerAllowedInCurrentMode, noFallbackProviderKeys, optionalNoFallbackProviderKeys } = context;
    const mode = currentProviderConfigMode();
    const fast = providers.FAST_RESPONSE_PROVIDER_ID || "未配置";
    const complex = providers.COMPLEX_REASONING_PROVIDER_ID || "未配置";
    const creative = providers.CREATIVE_MODEL_PROVIDER_ID || "未配置";
    const quickVision = providers.PLUGIN_VISION_PROVIDER_ID || "未配置";
    const main = resolveProviderId(context, "LLM_PROVIDER_ID", providers) || "AstrBot 默认模型";
    const mai = resolveProviderId(context, "MAI_STYLE_PROVIDER_ID", providers) || main;
    const pluginVision = providers.PLUGIN_VISION_PROVIDER_ID || "跟随 AstrBot 本体图片转文字";
    if (mode === "quick") {
      const readingVisionNode = visibleConfigKey("READING_ARCHIVE_VISION_PROVIDER_ID")
        ? `
          <span class="flow-arrow">·</span>
          <span class="flow-node primary">资料归档识图<br><b>${escapeHtml(providers.READING_ARCHIVE_VISION_PROVIDER_ID || "未配置")}</b></span>
        `
        : "";
      document.getElementById("providerFlow").innerHTML = `
        <div class="flow-lane">
          <span class="flow-node primary">快速响应<br><b>${escapeHtml(fast)}</b></span>
          <span class="flow-arrow">·</span>
          <span class="flow-node primary">复杂推理<br><b>${escapeHtml(complex)}</b></span>
          <span class="flow-arrow">·</span>
          <span class="flow-node primary">创作模型<br><b>${escapeHtml(creative)}</b></span>
          <span class="flow-arrow">·</span>
          <span class="flow-node primary">插件识图<br><b>${escapeHtml(quickVision)}</b></span>
          ${readingVisionNode}
        </div>
        <div class="flow-tasks"><span class="flow-node inherited">当前为快速配置<br><b>细分任务会按场景套用上方入口</b></span></div>
      `;
      return;
    }
    const tasks = Object.entries(providerLabels).filter(([key]) => !["FAST_RESPONSE_PROVIDER_ID", "COMPLEX_REASONING_PROVIDER_ID", "CREATIVE_MODEL_PROVIDER_ID", "LLM_PROVIDER_ID", "MAI_STYLE_PROVIDER_ID", "PLUGIN_VISION_PROVIDER_ID"].includes(key) && visibleConfigKey(key) && providerAllowedInCurrentMode(key));
    document.getElementById("providerFlow").innerHTML = `
      <div class="flow-lane">
        <span class="flow-node primary">主模型<br><b>${escapeHtml(main)}</b></span>
        <span class="flow-arrow">→</span>
        <span class="flow-node">陪伴通用<br><b>${escapeHtml(mai)}</b></span>
      </div>
      <div class="flow-lane">
        <span class="flow-node primary">默认图片转述<br><b>AstrBot 本体配置</b></span>
        <span class="flow-arrow">→</span>
        <span class="flow-node ${providers.PLUGIN_VISION_PROVIDER_ID ? "primary" : "inherited"}">插件识图链路<br><b>${escapeHtml(pluginVision)}</b></span>
      </div>
      <div class="flow-tasks">
        ${tasks.map(([key, label]) => {
          const resolved = resolveProviderId(context, key, providers);
          const value = providers[key] || (noFallbackProviderKeys.has(key) ? "未配置" : (optionalNoFallbackProviderKeys.has(key) ? "未启用" : (resolved || "AstrBot 默认模型")));
          const inherited = !providers[key];
          return `<span class="flow-node ${inherited ? "inherited" : "primary"}">${escapeHtml(label)}<br><b>${escapeHtml(value)}</b></span>`;
        }).join("")}
      </div>
    `;
  }

  function providerGroupMarkup(context, group, groupEntries, providers) {
    const { escapeHtml } = context;
    return `
      <section class="provider-group provider-tree-group" data-provider-group="${escapeHtml(group.id)}">
        <div class="provider-group-head">
          <div>
            <h3>${escapeHtml(group.title)}</h3>
            <p>${escapeHtml(group.desc)}</p>
          </div>
          <span>${groupEntries.length} 项</span>
        </div>
        <div class="provider-group-body">
          <div class="provider-grid provider-tree-grid">
            ${groupEntries.map(([key, label]) => providerCardMarkup(context, key, label, providers)).join("")}
          </div>
        </div>
      </section>
    `;
  }

  function bindProviderTests(context) {
    const { document } = context;
    document.querySelectorAll("[data-provider-select]").forEach((select) => {
      syncProviderInput(context, select);
      select.addEventListener("change", () => {
        const custom = select.value === "__custom__";
        syncProviderInput(context, select);
        rememberProviderDraft(context, select.dataset.providerSelect);
        // Keep the manual field mounted so the just-selected custom option is
        // not immediately replaced by a render based on its still-empty value.
        if (!custom) renderProviders(context);
      });
    });
    document.querySelectorAll("[data-provider-key]").forEach((input) => {
      input.addEventListener("input", () => rememberProviderDraft(context, input.dataset.providerKey));
    });
    document.querySelectorAll("[data-provider-fallback-select]").forEach((select) => {
      syncProviderFallbackInput(context, select);
      select.addEventListener("change", () => {
        const custom = select.value === "__custom__";
        syncProviderFallbackInput(context, select);
        rememberProviderFallbackDraft(context, select.dataset.providerFallbackSelect);
        if (!custom) renderProviders(context);
      });
    });
    document.querySelectorAll("[data-provider-fallback-key]").forEach((input) => {
      input.addEventListener("input", () => rememberProviderFallbackDraft(context, input.dataset.providerFallbackKey));
    });
    document.querySelectorAll("[data-provider-timeout]").forEach((input) => {
      input.addEventListener("input", () => rememberProviderTimeoutDraft(context, input));
      input.addEventListener("change", () => {
        const normalized = normalizeTimeoutValue(input.value);
        input.value = normalized === "" ? "" : String(normalized);
        rememberProviderTimeoutDraft(context, input);
      });
    });
    document.querySelectorAll("[data-provider-token-limit]").forEach((input) => {
      input.addEventListener("input", () => rememberProviderTokenLimitDraft(context, input));
      input.addEventListener("change", () => {
        const normalized = normalizeTokenLimitValue(input.value);
        input.value = normalized === "" ? "" : String(normalized);
        rememberProviderTokenLimitDraft(context, input);
      });
    });
    document.querySelectorAll("[data-provider-test]").forEach((button) => {
      button.addEventListener("click", async () => {
        await testProvider(context, button.dataset.providerTest);
      });
    });
  }

  function renderProviders(context) {
    const { syncProviderConfigModeControls, providerLabels, visibleConfigKey, providerAllowedInCurrentMode, providerGroups, providerGroupByKey, escapeHtml, document } = context;
    syncProviderConfigModeControls();
    const providers = providerValuesForRender(context);
    renderProviderSummary(context, providers);
    renderDeepseekPeakCard(context);
    renderModelReplacementCard(context);
    renderProviderFlow(context, providers);
    const entries = Object.entries(providerLabels)
      .filter(([key]) => visibleConfigKey(key))
      .filter(([key]) => providerAllowedInCurrentMode(key))
      .filter(([key, label]) => providerMatchesFilter(context, key, label, providers));
    const groups = providerGroups
      .map((group) => {
        const groupEntries = entries.filter(([key]) => providerGroupByKey[key]?.id === group.id);
        if (!groupEntries.length) return "";
        return providerGroupMarkup(context, group, groupEntries, providers);
      })
      .join("");
    document.getElementById("providerForm").innerHTML = groups || `
      <div class="empty provider-empty">
        <b>没有匹配的模型配置</b>
        <span>换个关键词，或切回“全部”查看完整模型分工。</span>
      </div>
    `;
    bindProviderTests(context);
  }

  function bindProviderToolbar(context) {
    const { document, state, setProviderConfigMode, rerenderProviders } = context;
    const filter = document.getElementById("providerFilter");
    if (filter && filter.dataset.providerToolbarBound !== "1") {
      filter.addEventListener("input", () => {
        state.providerFilter = filter.value;
        rerenderProviders();
      });
      filter.dataset.providerToolbarBound = "1";
    }
    document.querySelectorAll("[data-provider-config-mode]").forEach((button) => {
      if (button.dataset.providerToolbarBound === "1") return;
      button.addEventListener("click", () => setProviderConfigMode(button.dataset.providerConfigMode || "quick"));
      button.dataset.providerToolbarBound = "1";
    });
    const modeSwitch = document.querySelector(".provider-mode-switch");
    if (modeSwitch && modeSwitch.dataset.providerToolbarBound !== "1") {
      modeSwitch.addEventListener("click", (event) => {
        const button = event.target?.closest?.("[data-provider-mode]");
        if (!button || !modeSwitch.contains(button)) return;
        state.providerMode = button.dataset.providerMode || "all";
        rerenderProviders();
      });
      modeSwitch.dataset.providerToolbarBound = "1";
    }
  }

  return {
    renderProviders,
    bindProviderToolbar,
    currentProviderValues,
    currentProviderTimeoutValues,
    currentProviderTokenLimitValues,
    currentProviderFallbackValues,
    currentDeepseekPeakValues,
    currentModelReplacementValues,
    resolveProviderId,
    bindProviderTests,
    testProvider: (context, key) => testProvider(context, key),
  };
})();
