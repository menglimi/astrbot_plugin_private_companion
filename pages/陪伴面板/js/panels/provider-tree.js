window.PrivateCompanionProviderTree = (() => {
  function stateForTree(context) {
    const { state } = context;
    if (!state.providerTreeState) {
      state.providerTreeState = { pinnedGroups: {}, pinnedCards: {} };
    }
    return state.providerTreeState;
  }

  function providerValuesForRender(context) {
    const { state } = context;
    return {
      ...(state.overview?.providers || {}),
      ...(state.providerDraft || {}),
    };
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
    const known = state.availableProviders.some((item) => item.id === value);
    const customValue = value && !known ? value : "";
    const options = [
      `<option value="">${noFallbackProviderKeys.has(key) || optionalNoFallbackProviderKeys.has(key) ? "留空不启用" : "留空自动回退"}</option>`,
      ...state.availableProviders.map((item) => {
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
    if (noFallbackProviderKeys.has(key)) return "";
    if (optionalNoFallbackProviderKeys.has(key)) return "";
    const fast = values.FAST_RESPONSE_PROVIDER_ID || "";
    const complex = values.COMPLEX_REASONING_PROVIDER_ID || values.LLM_PROVIDER_ID || "";
    const creative = values.CREATIVE_MODEL_PROVIDER_ID || complex || fast;
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

  function setProviderStatus(context, key, message, level = "info") {
    const { document } = context;
    const status = document.querySelector(`[data-provider-status="${key}"]`);
    if (!status) return;
    status.className = `provider-status ${level}`;
    status.textContent = message;
  }

  function rememberProviderDraft(context, key) {
    const { document, state } = context;
    const input = document.querySelector(`[data-provider-key="${key}"]`);
    if (!input) return;
    state.providerDraft[key] = input.value.trim();
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

  async function testProvider(context, key) {
    const { optionalNoFallbackProviderKeys, noFallbackProviderKeys, postJson } = context;
    const providerId = resolveProviderId(context, key);
    setProviderStatus(context, key, "测试中...", "info");
    if (!providerId && (noFallbackProviderKeys.has(key) || optionalNoFallbackProviderKeys.has(key))) {
      setProviderStatus(context, key, optionalNoFallbackProviderKeys.has(key) ? "未单独配置：当前不会调用该模型" : "未配置，无法测试", "warn");
      return;
    }
    try {
      const result = await postJson("/provider/test", { key, provider_id: providerId });
      if (result.ok) {
        const suffix = result.sample ? ` · ${result.sample}` : "";
        setProviderStatus(context, key, `正常 ${result.elapsed_ms}ms${suffix}`, "ok");
      } else {
        setProviderStatus(context, key, result.error || "未返回内容", "warn");
      }
    } catch (error) {
      setProviderStatus(context, key, error.message, "warn");
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
    const noFallback = noFallbackProviderKeys.has(key);
    const optionalNoFallback = optionalNoFallbackProviderKeys.has(key);
    const group = providerGroupByKey[key];
    const statusLabel = configured ? "已单独配置" : (noFallback ? "未配置" : (optionalNoFallback ? "可选未启用" : "自动回退"));
    const guide = providerGuides[key] || {};
    const preference = providerPreferenceMeta[guide.preference || "balanced"];
    const impact = providerPassiveImpactMeta[guide.passiveImpact || ""];
    const preview = [guide.purpose || "", guide.fit || ""].filter(Boolean).join(" ");
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
          <div class="provider-current">
            <span>当前使用</span>
            <b>${escapeHtml(resolved || (noFallback ? "未配置" : (optionalNoFallback ? "未启用" : "AstrBot 默认模型")))}</b>
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
    const providerConfigMode = currentProviderConfigMode();
    const vision = providerConfigMode === "quick"
      ? (providers.PLUGIN_VISION_PROVIDER_ID || "跟随 AstrBot 本体/工具转述")
      : (providers.PRIVATE_READING_VISION_PROVIDER_ID || providers.NARRATION_PROVIDER_ID || "精准分流 / 默认链路");
    document.getElementById("providerSummary").innerHTML = `
      <div class="provider-cost-notice">
        <b>成本提醒</b>
        <span>火山方舟协作计划免费额度已延期到 2026-07-31 左右，具体以控制台/官方活动页为准。使用火山方舟 Provider 时，请检查每日 Token 限额和后台任务开关，注意成本控制。</span>
      </div>
      <div class="provider-summary-card strong"><span>单独配置</span><b>${configured}/${keys.length}</b><small>已指定专用 Provider</small></div>
      <div class="provider-summary-card"><span>自动回退</span><b>${inherited}</b><small>留空项会按兜底链路执行</small></div>
      <div class="provider-summary-card speed"><span>被动速度相关</span><b>${passiveSensitive}</b><small>这些项建议优先关注延迟</small></div>
      <div class="provider-summary-card quality"><span>效果优先项</span><b>${qualityRecommended}</b><small>适合更强推理或多模态模型</small></div>
      <div class="provider-summary-card"><span>低延迟优先项</span><b>${speedRecommended}</b><small>卡顿时优先检查这些项</small></div>
      ${requiredMissing ? `<div class="provider-summary-card warn"><span>未配置专用项</span><b>${requiredMissing}</b><small>这些任务留空时不会回退</small></div>` : ""}
      <div class="provider-summary-card"><span>可选 Provider</span><b>${available}</b><small>${escapeHtml(available ? "来自 AstrBot 当前配置" : "暂无可选项，可手动输入 ID")}</small></div>
      <div class="provider-summary-card"><span>视觉通道</span><b>${escapeHtml(vision)}</b><small>${escapeHtml(providerConfigMode === "quick" ? "图片、识屏与素材理解" : "精准模式不使用快速视觉入口")}</small></div>
    `;
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
    const pluginVision = providers.NARRATION_PROVIDER_ID || "跟随工具结果转述 / 主模型";
    if (mode === "quick") {
      document.getElementById("providerFlow").innerHTML = `
        <div class="flow-lane">
          <span class="flow-node primary">快速响应<br><b>${escapeHtml(fast)}</b></span>
          <span class="flow-arrow">·</span>
          <span class="flow-node primary">复杂推理<br><b>${escapeHtml(complex)}</b></span>
          <span class="flow-arrow">·</span>
          <span class="flow-node primary">创作模型<br><b>${escapeHtml(creative)}</b></span>
          <span class="flow-arrow">·</span>
          <span class="flow-node primary">插件识图<br><b>${escapeHtml(quickVision)}</b></span>
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
        <span class="flow-node ${providers.NARRATION_PROVIDER_ID ? "primary" : "inherited"}">插件识图链路<br><b>${escapeHtml(pluginVision)}</b></span>
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

  function shouldExpandGroup(context, groupEntries, groupId) {
    const treeState = stateForTree(context);
    if (Object.prototype.hasOwnProperty.call(treeState.pinnedGroups, groupId)) return treeState.pinnedGroups[groupId];
    const query = (context.state.providerFilter || "").trim();
    if (query) return true;
    return groupEntries.some(([key]) => Boolean(context.state.providerDraft?.[key] || context.state.overview?.providers?.[key]));
  }

  function providerGroupMarkup(context, group, groupEntries, providers) {
    const { escapeHtml } = context;
    const expanded = shouldExpandGroup(context, groupEntries, group.id);
    return `
      <section class="provider-group provider-tree-group ${expanded ? "is-expanded" : ""}" data-provider-group="${escapeHtml(group.id)}">
        <button type="button" class="provider-group-head provider-group-toggle" data-provider-group-toggle="${escapeHtml(group.id)}" aria-expanded="${expanded ? "true" : "false"}">
          <div>
            <h3>${escapeHtml(group.title)}</h3>
            <p>${escapeHtml(group.desc)}</p>
          </div>
          <span>${groupEntries.length} 项</span>
        </button>
        <div class="provider-group-body">
          <div class="provider-grid provider-tree-grid">
            ${groupEntries.map(([key, label]) => providerCardMarkup(context, key, label, providers)).join("")}
          </div>
        </div>
      </section>
    `;
  }

  function applyTreeInteractions(context) {
    const { document } = context;
    const treeState = stateForTree(context);
    document.querySelectorAll("[data-provider-group-toggle]").forEach((button) => {
      const groupId = button.dataset.providerGroupToggle;
      const section = button.closest(".provider-tree-group");
      if (!section) return;
      const setExpanded = (expanded, pinned) => {
        section.classList.toggle("is-expanded", expanded);
        button.setAttribute("aria-expanded", expanded ? "true" : "false");
        if (pinned) treeState.pinnedGroups[groupId] = expanded;
      };
      button.addEventListener("click", () => setExpanded(!section.classList.contains("is-expanded"), true));
      button.addEventListener("mouseenter", () => {
        if (!Object.prototype.hasOwnProperty.call(treeState.pinnedGroups, groupId)) setExpanded(true, false);
      });
      section.addEventListener("mouseleave", () => {
        if (!Object.prototype.hasOwnProperty.call(treeState.pinnedGroups, groupId) && !(context.state.providerFilter || "").trim()) setExpanded(false, false);
      });
    });
  }

  function bindProviderTests(context) {
    const { document } = context;
    document.querySelectorAll("[data-provider-select]").forEach((select) => {
      syncProviderInput(context, select);
      select.addEventListener("change", () => {
        syncProviderInput(context, select);
        rememberProviderDraft(context, select.dataset.providerSelect);
        renderProviders(context);
      });
    });
    document.querySelectorAll("[data-provider-key]").forEach((input) => {
      input.addEventListener("input", () => rememberProviderDraft(context, input.dataset.providerKey));
    });
    document.querySelectorAll("[data-provider-test]").forEach((button) => {
      button.addEventListener("click", async () => {
        await testProvider(context, button.dataset.providerTest);
      });
    });
    applyTreeInteractions(context);
  }

  function renderProviders(context) {
    const { syncProviderConfigModeControls, providerLabels, visibleConfigKey, providerAllowedInCurrentMode, providerGroups, providerGroupByKey, escapeHtml, document } = context;
    syncProviderConfigModeControls();
    const providers = providerValuesForRender(context);
    renderProviderSummary(context, providers);
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
    const { document, state, setProviderConfigMode } = context;
    const filter = document.getElementById("providerFilter");
    if (filter && filter.dataset.providerToolbarBound !== "1") {
      filter.addEventListener("input", () => {
        state.providerFilter = filter.value;
        renderProviders(context);
      });
      filter.dataset.providerToolbarBound = "1";
    }
    document.querySelectorAll("[data-provider-config-mode]").forEach((button) => {
      if (button.dataset.providerToolbarBound === "1") return;
      button.addEventListener("click", () => setProviderConfigMode(button.dataset.providerConfigMode || "quick"));
      button.dataset.providerToolbarBound = "1";
    });
    document.querySelectorAll("[data-provider-mode]").forEach((button) => {
      if (button.dataset.providerToolbarBound === "1") return;
      button.addEventListener("click", () => {
        state.providerMode = button.dataset.providerMode || "all";
        document.querySelectorAll("[data-provider-mode]").forEach((item) => {
          item.classList.toggle("active", item === button);
        });
        renderProviders(context);
      });
      button.dataset.providerToolbarBound = "1";
    });
  }

  return {
    renderProviders,
    bindProviderToolbar,
    currentProviderValues,
    testProvider: (context, key) => testProvider(context, key),
  };
})();
