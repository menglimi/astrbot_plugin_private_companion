/* One-time 当前版本 setup. Progress is owned by the host and survives page and plugin reloads. */
"use strict";
window.CompanionSetup = (() => {
  const PREFIX = "setup";
  /* 八步对齐旧版引导：基础连通 → 模型 → 人格与世界知识 → 关系教学 →
     主动功能（含私聊强度） → 日程与观察 → 群聊配置 → 主动消息测试。 */
  const ORDER = ["binding", "model", "knowledge", "relationships", "proactive", "life", "group",
                 "proactive_test"];
  /* 四步角色驱动迁移：设置角色 → 只读核对 → 显式迁移 → 完成。
     逐项能力移出向导，放在独立的「逐项迁移」页面。 */
  const MIGRATION_STEPS = [
    {id: 1, short: "设置角色", title: "设置接收旧版资料的角色", note: "先确认接收旧版资料的角色；没有角色时在本页引导创建。"},
    {id: 2, short: "核对", title: "核对配置变更与迁移范围", note: "只读核对配置将改写、失效与保留的项目，以及人格、群和私聊范围。"},
    {id: 3, short: "迁移", title: "确认迁移方式并开始", note: "明确选择沿用旧版配置或保留当前配置，点「开始迁移」后才写入。"},
    {id: 4, short: "完成", title: "查看结果并开始使用", note: "汇总已导入、需确认和仍需处理的内容。"},
  ];
  const LABELS = {
    binding: "核心管理员窗口",
    model: "插件模型", knowledge: "世界知识", relationships: "关系教学",
    proactive: "主动功能与强度", life: "日程与观察", group: "群聊配置",
    proactive_test: "主动消息测试",
  };
  /* 旧版配置引导的四段说明与「要点清单」：每一步先告诉用户这步在解决什么、
     大约花多久、要确认哪几件事，再给表单。 */
  const STEP_GROUPS = [
    {title: "目标用户与平台", steps: ["binding"]},
    {title: "核心模型配置", steps: ["model"]},
    {title: "主动功能与其他配置", steps: ["knowledge", "relationships", "proactive", "life", "group"]},
    {title: "主动消息测试", steps: ["proactive_test"]},
  ];
  const STEP_META = {
    binding: {
      tag: "第 1 步 · 约 2 分钟",
      body: "先在需要接收主动消息的私聊窗口发送绑定指令，本页会读取真实用户 ID 和平台会话；确认后，这个窗口同时作为管理员控制入口。",
      checks: ["在目标私聊发送绑定指令", "刷新并核对平台、Bot 账号与私聊对象", "确认这个窗口用于管理员指令与主动投递"],
    },
    model: {
      tag: "第 2 步 · 约 3 分钟",
      body: "插件自身的主动联系、日程、状态和每日反思都需要模型。日常对话仍跟随 AstrBot 当前人格与主链，这里只选插件内部任务用哪个 Provider。",
      checks: ["选择插件通用模型", "按需给主动联系、日程等任务分配路由", "日常回复模型在 AstrBot 主链里配置，不在这里改动"],
    },
    knowledge: {
      tag: "第 3 步 · 可选",
      body: "从 AstrBot 当前人格整理角色稳定档案。草稿会先给你核对，确认后才写入；想先跑通聊天可以跳过，之后随时回来补。",
      checks: ["确认人格来源", "按需补充已确认的角色事实", "不覆盖已有档案内容"],
    },
    relationships: {
      tag: "第 4 步 · 可选",
      body: "整理对主要陪伴对象的称呼、关系和相处方式，只保存到这段私聊的熟人卡，不会带到其他用户或群聊。",
      checks: ["确认对用户的称呼", "按需补充关系与相处方式", "已有内容默认保留，需要时再勾选覆盖"],
    },
    proactive: {
      tag: "第 5 步 · 约 2 分钟",
      body: "只选主动能力的大开关和私聊强度：要不要主动开口、多久一次、哪些时段安静。更细的契机、新闻与空间素材不在这里，避免一次堆太多。",
      checks: ["选择是否开启私聊主动", "选择主动强度与每日上限", "确认免打扰时段仍然生效"],
    },
    life: {
      tag: "第 6 步 · 可选",
      body: "给角色安排一天的生活节奏，主动联系才有真实由头。日程是增强项，不影响基础聊天与保存；想先跑通可以先跳过。",
      checks: ["按需填写日常作息与固定安排", "确认日程不会写成任务汇报", "跳过也不影响回复与主动开关"],
    },
    group: {
      tag: "第 7 步 · 可选",
      body: "如果角色也进群，这里决定它在群里的参与方式：被叫到时怎么接、平时是否观察、要不要主动插话。私聊配置不受影响。",
      checks: ["选择群聊参与方式", "按需设置唤醒与插话", "只在需要的群里生效"],
    },
    proactive_test: {
      tag: "第 8 步 · 约 1 分钟",
      body: "最后向已绑定的管理员私聊发一条主动测试消息，把主动联系模型、策略检查与真实投递完整跑通一次；只有你勾选同意后才会发送。",
      checks: ["勾选同意后向管理员私聊发送一次测试消息", "核对模型生成、策略检查与平台投递回执", "收到后即可进入陪伴小屋日常使用"],
    },
  };
  const stepMeta = step => STEP_META[step] || STEP_META.binding;
  /* 这些步骤直接复用对应配置组的工作台：旧版引导也是「在向导里改完就保存」，
     改完点「已完成这部分」，不必先跳到别的页面。 */
  const STEP_EDITORS = {
    proactive: {kinds: ["proactive.contact"], views: {"proactive.contact": {
      sectionIds: ["master", "rhythm", "quiet", "greeting"],
      title: "主动联系", note: "决定是否主动开口、多久一次、哪些时段安静。"}}},
    life: {kinds: ["life"], views: {life: {
      sectionIds: ["routine", "sleep"], title: "日程与作息",
      note: "角色的日常作息与固定安排；只会变成自然的生活由头，不写成任务清单。"}}},
    group: {kinds: ["group_participation"], views: {group_participation: {
      sectionIds: ["atmosphere", "denoise", "interjection"], title: "群聊参与",
      note: "被叫到时怎么接、平时是否观察、要不要主动插话。"}}},
  };
  let stepEditor = null;
  function stopStepEditor() {
    stepEditor?.stop?.();
    stepEditor = null;
  }
  function mountStepEditor(step) {
    const block = STEP_EDITORS[step];
    const host = root?.querySelector("#setupStepEditor");
    if (!block || !host) return;
    stepEditor = window.CompanionConfigEditors?.create?.(block.kinds, () => host, block.views || {});
    if (stepEditor) {
      stepEditor.restore?.();
      stepEditor.paint?.();
      void stepEditor.read?.();
    } else {
      host.innerHTML = `<p class="setup-inline-unavailable">当前角色的设置工作台暂不可用，请重新读取后再试。</p>`;
    }
  }
  function nextStepPreview(step) {
    const index = ORDER.indexOf(step);
    const next = index >= 0 ? ORDER[index + 1] : "";
    if (!next) return "";
    return `<aside class="setup-next-step" aria-label="下一步"><span>接下来</span><b>${String(index + 2).padStart(2, "0")} · ${escape(LABELS[next] || next)}</b><small>${escape(stepMeta(next).tag)}，可以稍后回来调整。</small></aside>`;
  }

  function renderSettingsStep(step) {
    const complete = (state?.completed || []).includes(step);
    const skipped = (state?.skipped || []).includes(step);
    return `<section class="setup-stage">
      <p class="setup-kicker">${escape(stepMeta(step).tag)}</p><h3>${escape(LABELS[step] || step)}</h3>
      <p>改完下面的设置后点「已完成这部分」；这一步也可以先跳过，之后在对应页面继续调整。</p>
      <div id="setupStepEditor" class="setup-inline-editor"></div>
      ${!state?.prepared ? `<p class="setup-inline-loading" role="status">完成模型配置后，这里才能保存设置。</p>` : ""}
      ${complete || skipped ? `<p class="setup-recorded">${skipped ? "已选择暂时跳过" : "已标记完成"}</p>` : ""}
      ${noticeMarkup()}
      <div class="setup-actions">
        ${button("complete-step", complete ? "重新确认完成" : skipped ? "改为已完成" : "已完成这部分", {primary: true})}
        ${button("skip-step", "暂时跳过")}
      </div>
    </section>`;
  }
  function stageBanner(step) {
    const meta = stepMeta(step);
    return `<section class="setup-stage-banner" aria-label="本步说明">
      <p class="setup-kicker">${escape(meta.tag)}</p>
      <p class="setup-stage-body">${escape(meta.body)}</p>
      <ul class="setup-checklist">${meta.checks.map(item => `<li>${escape(item)}</li>`).join("")}</ul>
    </section>`;
  }
  const ADVANCED_BLOCKS = [
    {id: "understanding", label: "回复与分段", note: "在同一处调整分段规则，并即时查看消息效果。", mount: "reply"},
    {id: "relationships", label: "人物与资料", note: "角色资料、关系资料、世界知识与新用户默认权限。",
      editors: ["role_profile", "relationship_profile", "knowledge", "private_access"],
      views: {role_profile: {presentation: "journal"}, relationship_profile: {presentation: "journal"},
        knowledge: {sectionIds: ["import", "inject"], presentation: "journal", navigationLimit: 0}}},
    {id: "life", label: "日程与作息", note: "角色日程直接在这里维护；日记例程与睡眠作息按人格资料保存。",
      mount: "state", editors: ["life"],
      views: {life: {sectionIds: ["routine", "sleep"], title: "日记例程与睡眠作息", note: "作息跟随当前人格；日程配置也对该角色的所有私聊生效。"}}},
    {id: "action", label: "主动联系", note: "设置开口许可、频率、免打扰时段和常用契机。",
      editors: ["proactive.contact"],
      views: {"proactive.contact": {sectionIds: ["master", "rhythm", "quiet", "greeting"],
        title: "主动联系常用设置", note: "按当前角色与关系生效；更细的契机仍沿用同一配置合同。"}}},
    {id: "group", label: "群聊参与", note: "群聊回应判断、氛围参考与主动插话设置。",
      mount: "decision", editors: ["group_participation"],
      views: {group_participation: {sectionIds: ["atmosphere", "denoise", "interjection"],
        title: "群聊参与方式", note: "群聊回答由本页上方的回应规则决定；这里调整群内氛围和插话。"}}},
    {id: "runtime", label: "模型路由", note: "为主动联系、日程和后台任务分配模型；日常对话仍走 AstrBot 主链。", mount: "model"},
  ];
  const $ = (selector, root = document) => root.querySelector(selector);
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c =>
    ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
  const errorText = (error, fallback) => {
    const code = typeof error?.code === "string" ? error.code : typeof error?.message === "string" ? error.message : "";
    const messages = {
      dashboard_identity_required: "请从已登录的 AstrBot 管理页面打开。",
      core_access_required: "当前账号没有管理权限。",
      staging_not_initialized: "角色数据尚未就绪，请先在账号与会话中初始化。",
      staging_host_terminated: "插件已停止，请重新打开插件页面。",
      bridge_timeout: "连接超时，请重新读取；原操作结果需要核对。",
      invalid_response: "服务返回的数据不完整，请重新读取。",
      setup_progress_unavailable: "读取设置进度失败（已保留上次结果）；点「重新读取」再试一次，仍失败请查看 AstrBot 日志。",
      setup_model_invalid: "请选择当前已加载的文本模型后再试。",
      setup_model_apply_failed: "模型已通过校验，但保存或初始化没有完成。请查看 AstrBot 日志并重新读取状态。",
      model_route_unavailable: "当前小屋角色没有可编辑的模型路由，请切换到已关联私聊的人格。",
      model_route_scope_changed: "小屋角色已经切换，请重新读取当前角色的模型路由后再保存。",
      legacy_plan_stale: "配置在执行前已更新；重新执行会先重新试算再写入。",
      upgrade_mapping_unavailable: "旧版账号、人格或模型尚未就绪，请核对账号与会话。",
      account_role_persona_in_use: "这个 AstrBot 人格已归入角色，请选择另一个。",
      account_role_name_in_use: "这个 Bot 已有同名角色，请换个名字。",
      account_config_save_failed: "角色没有保存成功，请核对后重试。",
      native_catalog_registration_failed: "原生能力初始化失败，请查看 AstrBot 日志中的具体异常。",
    };
    const message = typeof error?.message === "string" ? error.message : "";
    return messages[code] || (message && message !== code && message !== "暂不可读取，请刷新核对。" ? message : "")
      || (code && !/^[a-z][a-z0-9_]{2,60}$/.test(code) ? code : code ? `${fallback}（${code}）` : fallback);
  };
  const product = () => window.CompanionProduct;
  const api = (method, path, body) => product().request(method, PREFIX + path, body);
  const LEGACY_TIMEOUT_MS = 300000;
  const migrationRequest = async (method, path, body) => {
    const activeGeneration = mountGeneration;
    const sourceKey = `${legacyTargetId}\u0000${legacyRoleId}\u0000${legacyRootDraft}`;
    const configurationScope = body?.configuration_scope ? legacyConfigScope : undefined;
    const result = await product().request(method, path, body, {timeoutMs: LEGACY_TIMEOUT_MS});
    if (activeGeneration !== mountGeneration || sourceKey !== `${legacyTargetId}\u0000${legacyRoleId}\u0000${legacyRootDraft}`
        || configurationScope && configurationScope !== legacyConfigScope) {
      const error = new Error("迁移页面或来源已切换，请重新读取当前目标。");
      error.code = "legacy_selection_changed";
      throw error;
    }
    return result;
  };
  let root = null;
  let state = null;
  let busy = false;
  const legacyJobs = new Map();
  const legacyWriteActions = new Set(["legacy-auto", "legacy-apply", "legacy-data-apply",
    "legacy-data-batch-apply", "legacy-group-data-apply", "legacy-private-memory-apply", "legacy-upgrade-confirm"]);
  let legacyDataRequestId = 0;
  let legacyDataRefreshing = false;
  let notice = "";
  let migrationProgress = "";
  let modelDraft = "";
  let modelTasks = null;
  let modelTasksStatus = "idle";
  let modelTasksError = "";
  let modelTasksPromise = null;
  let modelTasksScopeKey = null;
  let modelTaskDrafts = {};
  let profileState = null;
  let profileDraft = null;
  let profileDraftTarget = "role_profile";
  let profileFieldDrafts = {};
  let profileSelected = new Set();
  let profileSupplement = {role_profile: "", relationship_profile: ""};
  let profileOverwrite = false;
  let profileNotes = [];
  let proactiveOptIn = false;
  let proactiveWatcher = null;
  let legacyTargetId = "";
  let legacyBotKey = "";
  let legacyRoleId = "";
  let legacyRootDraft = "";
  let legacyConfigScope = "plugin";
  let legacyApi = "";
  let legacyFinding = null;
  let legacyPlan = null;
  const configurationReview = () => window.CompanionMigrationConfiguration;
  let legacyResult = null;
  let legacyDataPlan = null;
  let legacyDataInventory = null;
  let legacyView = "review";
  let legacyMappingView = "current";
  let legacyWizardStep = 1;
  let paintedMigrationStep = 0;
  let pendingMigrationFocus = false;
  let legacyConfigSkipped = false;
  let legacyApplyAllConfig = false;
  let legacyConfigNoChanges = false;
  let legacyLastErrorCode = "";
  let legacyManualOpen = false;
  let legacyRoleSwitcherOpen = false;
  let legacyDataCanApply = false;
  let legacyPendingConfirmations = [];
  let legacyMigrationFinished = false;
  const legacyOpenDetails = new Set();
  let legacySettingsKey = "";
  let legacySettingsEditor = null;
  let legacyDataResult = null;
  let legacyDataError = "";
  let legacyPrivateMemoryPlan = null;
  let legacyPrivateMemoryResult = null;
  let legacyPrivateMemoryConfirmed = false;
  let legacyArchivedWork = null;
  let legacyDataGroupId = "";
  let legacyDataPersonaId = "";
  let legacyScope = null;
  let legacyScopeBusy = false;
  let legacyScopeRequestId = 0;
  let legacyScopeChecked = false;
  let legacyScopeError = "";
  let legacyWizardExecuting = false;
  let legacyOneClickConfigError = "";
  let legacyBatchBusy = false;
  let legacyBatchResult = null;
  let legacyGroupTargetId = "";
  let legacyGroupRoleId = "";
  let legacyGroupPlan = null;
  let legacyGroupResult = null;
  let legacyGroupError = "";
  let legacyGroupImportNetwork = true;
  let legacyGroupImportWorldbook = true;
  let legacyGroupImportExpressions = true;
  let legacyGroupConfirmMapping = false;
  let legacyGroupConfirmMemberIds = false;
  let legacyGroupConfirmExpressionMapping = false;
  let legacyPersonaMappings = new Map();
  let legacyPersonaDestinationSequence = 0;
  let legacyPersonaBatchError = "";
  let legacyAccountCatalog = null;
  let legacyAccountCatalogState = "idle";
  let legacyRoleEditorOpen = false;
  let legacyNewRoleName = "";
  let legacyNewPersonaIds = new Set();
  let legacyRoleSaveBusy = false;
  let legacyRoleSaveMessage = "";
  let legacyPersonaBatchProgress = "";
  let legacyImportReactions = true;
  let legacyImportGroupNetwork = false;
  let legacyImportWorldbook = false;
  let legacyImportPrivateContacts = true;
  let startupUpgradePrompt = null;
  const startupUpgradeShown = new Set();

  async function promptUpgradeAtStartup(onContinue) {
    if (startupUpgradePrompt) return startupUpgradePrompt;
    startupUpgradePrompt = (async () => {
      let status;
      try { status = await api("GET", "/status"); }
      catch { return false; }
      const upgrade = status?.upgrade;
      if (!upgrade?.present || upgrade.configured) return false;
      const fingerprint = typeof upgrade.source_sha256 === "string" && upgrade.source_sha256
        ? upgrade.source_sha256 : "detected";
      // Only suppress duplicate prompts in this document. A restored installation
      // or a new page visit must not inherit an old browser-session dismissal.
      if (startupUpgradeShown.has(fingerprint)) return false;
      if (typeof window.CompanionConfirm !== "function") return false;
      const message = upgrade.can_prepare
        ? "已识别到旧版账号、人格和模型配置。确认后会自动接续这些设置，并直接打开旧版资料迁入；旧版资料保持原样。"
        : "已找到旧版配置，但当前在线账号、人格或模型还不能自动配对。确认后会直接打开「账号与会话」核对；补齐后即可继续迁入，旧版资料保持原样。";
      const accepted = await window.CompanionConfirm({
        title: "发现旧版配置",
        message,
        confirmText: upgrade.can_prepare ? "接续配置并继续" : "前往账号与会话",
        cancelText: "稍后处理",
      });
      startupUpgradeShown.add(fingerprint);
      if (accepted && typeof onContinue === "function") {
        try { await onContinue(upgrade); }
        catch (error) {
          startupUpgradeShown.delete(fingerprint);
          throw error;
        }
      }
      return true;
    })();
    try { return await startupUpgradePrompt; }
    finally { startupUpgradePrompt = null; }
  }

  async function refreshDirectoryQuietly() {
    try { await product().refreshDirectory(); }
    catch { /* The setup catalog remains available before staging starts. */ }
  }

  async function continueUpgrade(upgrade) {
    if (!upgrade?.can_prepare || typeof upgrade.plan_token !== "string" || !upgrade.plan_token) {
      window.navigate?.("manage", "host");
      return false;
    }
    const result = await api("POST", "/upgrade", {confirmed: true, plan_token: upgrade.plan_token});
    if (!result?.started) throw new Error(result?.startup_error || "旧版连接已保存，但当前运行环境尚未就绪。请在开始使用页查看详情。");
    await refreshDirectoryQuietly();
    window.navigate?.("manage", "data", "migration");
    return true;
  }
  let legacyImportReferenceImages = false;
  let legacyImportWardrobeItems = false;
  let legacyConfirmReferencePersonaMapping = false;
  let legacyImportArchive = true;
  let legacyImportMemoryDatabase = true;
  let legacyMemoryOwnerIds = new Set();
  let legacyMemoryGroupMappings = new Map();
  let legacyMemoryGroupMappingSeeded = false;
  let legacyMemoryPlanDirty = false;
  let legacyConfirmMemoryMapping = false;
  const MAX_LEGACY_MEMORY_OWNER_SELECTION = 128;
  let legacyConfirmMemberIds = false;
  let legacyImportJournals = false;
  let legacyImportExpressions = false;
  let legacyConfirmExpressionMapping = false;
  let legacyConfirmPersonaMapping = false;
  let legacyError = "";
  let advancedOpen = false;
  let advancedBlock = "understanding";
  let quickEditor = null;
  let migrationOnly = false;
  let onState = null;
  let generation = 0;
  let mountGeneration = 0;

  function legacyActionKey(action) {
    const source = [legacyTargetId, legacyRoleId, legacyRootDraft.trim()];
    if (action === "legacy-data-plan" || action === "legacy-data-apply") source.push(legacyDataQuery());
    else if (action.startsWith("legacy-group-data-")) {
      source.push(legacyDataPersonaId, legacyDataGroupId, legacyGroupTargetId, legacyGroupRoleId);
    } else if (action.startsWith("legacy-data-batch-")) {
      source.push([...legacyPersonaMappings].map(([id, mapping]) => [id, mapping.skip,
        mapping.destinations.map(item => [item.destination_id, item.target_id, item.role_id])]));
    } else if (["legacy-scan", "legacy-plan", "legacy-apply"].includes(action)) source.push(legacyConfigScope);
    return JSON.stringify(source);
  }

  function legacyActionBusy(action) {
    const job = legacyJobs.get(action);
    return Boolean(job && job.key === legacyActionKey(action));
  }

  function legacyActiveJobs() {
    return [...legacyJobs].filter(([action]) => legacyActionBusy(action));
  }

  function legacyWriting() {
    return [...legacyJobs.keys()].some(action => legacyWriteActions.has(action));
  }

  function legacyActionDisabled(action) {
    return legacyActionBusy(action) || legacyWriting()
      || legacyWriteActions.has(action) && legacyActiveJobs().length > 0;
  }

  function legacyProgressMarkup() {
    const messages = legacyActiveJobs().map(([, job]) => job.progress).filter(Boolean);
    return messages.length ? `<div class="setup-migration-pending" role="status">${messages.map(message => `<p>${escape(message)}</p>`).join("")}</div>` : "";
  }

  function renderAdvanced() {
    const current = ADVANCED_BLOCKS.find(item => item.id === advancedBlock) || ADVANCED_BLOCKS[0];
    const tabs = ADVANCED_BLOCKS.map(item => `<button type="button" class="setup-advanced-tab ${item.id === current.id ? "active" : ""}" data-setup-advanced-block="${item.id}" aria-pressed="${item.id === current.id}">${item.label}</button>`).join("");
    return `<div class="setup-shell setup-complete-shell setup-advanced-shell">
      <header class="setup-header"><div><span class="setup-kicker">开始使用</span><h2>快速配置</h2></div><button type="button" class="button" data-setup-action="advanced-close">返回开始使用</button></header>
      <nav class="setup-advanced-tabs" aria-label="快速配置分区">${tabs}</nav>
      <div class="setup-advanced-content"><p class="setup-advanced-note">${current.note}</p>
        <section class="setup-advanced-section"><h3>${current.label}</h3>
          ${current.mount === "model" ? renderQuickModels() : ""}
          <div id="setupQuickReplyHost" class="setup-inline-component" ${current.mount === "reply" ? "" : "hidden"}></div>
          <div id="setupQuickDecisionHost" class="setup-inline-component" ${current.mount === "decision" ? "" : "hidden"}></div>
          <div id="setupQuickStateHost" class="setup-inline-component" ${current.mount === "state" ? "" : "hidden"}></div>
          <div id="setupAdvancedEditorHost" class="setup-inline-editor" ${current.editors?.length ? "" : "hidden"}></div>
        </section>
      </div>
    </div>`;
  }

  function renderQuickModels() {
    syncModelTasksScope();
    const providers = Array.isArray(state?.model?.providers) ? state.model.providers : [];
    const providerId = modelDraft || state?.model?.provider_id || "";
    const canSetInitialProvider = state?.step === "model" && !state?.ready;
    const tasks = (modelTasks?.tasks || []).filter(task =>
      ["proactive.compose", "calendar.plan", "role.state.maintain", "role.self_review"].includes(task.kind));
    const taskProviders = Array.isArray(modelTasks?.providers) ? modelTasks.providers : [];
    const taskMarkup = tasks.map(task => {
      const draft = modelTaskDrafts[task.kind] || {};
      const provider = draft.provider ?? task.fields.provider.value;
      const fallback = draft.fallback ?? task.fields.fallback.value;
      const choices = taskProviders.filter(item => task.capabilities.every(capability => item.capabilities.includes(capability)));
      return `<details class="setup-task-route"><summary><strong>${escape(taskLabel(task.kind))}</strong><small>${escape(task.kind)}</small></summary><div class="setup-task-route-fields"><label><span>主模型</span><select data-setup-model-task="${escape(task.kind)}" data-setup-model-field="provider" ${busy || !choices.length ? "disabled" : ""}>${choices.map(item => `<option value="${escape(item.id)}" ${provider === item.id ? "selected" : ""}>${escape(item.name || item.id)}</option>`).join("")}</select></label><label><span>备用模型</span><select data-setup-model-task="${escape(task.kind)}" data-setup-model-field="fallback" ${busy || !choices.length ? "disabled" : ""}><option value="">不设置</option>${choices.filter(item => item.id !== provider).map(item => `<option value="${escape(item.id)}" ${fallback === item.id ? "selected" : ""}>${escape(item.name || item.id)}</option>`).join("")}</select></label>${button(`save-task:${task.kind}`, "保存", {disabled: !choices.length})}</div></details>`;
    }).join("");
    const providerMarkup = canSetInitialProvider
      ? providers.length ? `<label class="setup-model-field"><span>陪伴通用模型</span><select name="setup-provider" ${busy ? "disabled" : ""}><option value="">选择模型</option>${providers.map(item => `<option value="${escape(item.id)}" ${providerId === item.id ? "selected" : ""}>${escape(item.name)} · ${escape(item.id)}</option>`).join("")}</select></label>${button("save-model", state?.prepared ? "保存通用模型" : "初始化并载入模型", {primary: true, disabled: !providerId})}`
        : `<p class="setup-inline-error">没有可用的文本模型，请先在 AstrBot 中加载 Provider 后刷新本页。</p>`
      : `<div class="setup-model-default"><span>陪伴通用模型</span><strong>${escape(providerId || "未设置")}</strong></div>`;
    const routesMarkup = modelTasksStatus === "loading"
      ? `<p class="setup-inline-loading" role="status">正在读取后台模型路由…</p>`
      : modelTasksStatus === "error"
        ? `<div class="setup-inline-error" role="alert"><span>${escape(modelTasksError || "后台模型路由暂时无法读取。")}</span>${button("retry-model-routes", "重新读取")}</div>`
        : modelTasks ? taskMarkup || `<p class="setup-inline-empty">当前没有这些后台任务的模型路由。</p>`
          : `<p class="setup-inline-loading" role="status">正在读取后台模型路由…</p>`;
    return `<div class="setup-quick-models"><p class="setup-quick-models-note">陪伴模型用于插件内部任务；日常对话回复仍跟随 AstrBot 当前人格与主链。</p>
      ${providerMarkup}
      <section class="setup-model-routes"><header><div><h4>常用后台任务</h4><p>主动联系、日程、状态与每日反思</p></div><span>${tasks.length} 项</span></header>${routesMarkup}</section>
    </div>`;
  }

  function stopQuickWorkspace() {
    quickEditor?.stop?.();
    quickEditor = null;
    window.CompanionReplyPage?.unmount?.();
    window.CompanionStatePage?.unmount?.();
  }

  function mountQuickWorkspace() {
    const block = ADVANCED_BLOCKS.find(item => item.id === advancedBlock);
    if (!block || !root) return;
    const host = selector => root.querySelector(selector);
    if (block.mount === "reply") window.CompanionReplyPage?.mount?.(host("#setupQuickReplyHost"),
      {view: "expression", expressionModule: "expression-text", section: "segment"});
    if (block.mount === "decision") window.CompanionReplyPage?.mount?.(host("#setupQuickDecisionHost"), {view: "decision"});
    if (block.mount === "state") window.CompanionStatePage?.mount?.(host("#setupQuickStateHost"), {section: "state", feature: "planning"});
    if (block.editors?.length) {
      quickEditor = window.CompanionConfigEditors?.create?.(block.editors, () => host("#setupAdvancedEditorHost"), block.views || {});
      if (quickEditor) {
        quickEditor.restore?.();
        quickEditor.paint?.();
        void quickEditor.read?.();
      } else {
        const editorHost = host("#setupAdvancedEditorHost");
        if (editorHost) editorHost.innerHTML = `<p class="setup-inline-unavailable">当前角色的设置工作台暂不可用，请重新读取后再试。</p>`;
      }
    }
    if (block.mount === "model" && state?.prepared && modelTasksStatus === "idle") void loadQuickModels();
  }

  function currentModelScopeKey() {
    const productApi = product();
    return productApi?.scopeKey?.() || productApi?.role?.()?.model_api || "";
  }

  function syncModelTasksScope(scope = currentModelScopeKey()) {
    if (scope === modelTasksScopeKey) return scope;
    modelTasksScopeKey = scope;
    modelTasks = null;
    modelTasksStatus = "idle";
    modelTasksError = "";
    modelTasksPromise = null;
    modelTaskDrafts = {};
    return scope;
  }

  function currentRoleModelRoute() {
    const productApi = product();
    const endpoint = productApi?.role?.()?.model_api;
    if (typeof endpoint !== "string" || !endpoint) {
      throw Object.assign(new Error("model_route_unavailable"), {code: "model_route_unavailable"});
    }
    const path = productApi.relative(endpoint);
    if (typeof path !== "string" || !path) {
      throw Object.assign(new Error("model_route_unavailable"), {code: "model_route_unavailable"});
    }
    return {path, scope: productApi.scopeKey?.() || endpoint};
  }

  function modelTaskPayload(response) {
    let value = response;
    for (let depth = 0; depth < 3; depth += 1) {
      if (Array.isArray(value?.tasks) && Array.isArray(value?.providers)) return value;
      if (!value?.data || typeof value.data !== "object" || Array.isArray(value.data)) return null;
      value = value.data;
    }
    return null;
  }

  async function readModelTasks({force = false} = {}) {
    let route;
    try {
      route = currentRoleModelRoute();
      syncModelTasksScope(route.scope);
    } catch (error) {
      syncModelTasksScope();
      modelTasksStatus = "error";
      modelTasksError = errorText(error, "后台模型路由暂时无法读取。");
      return null;
    }
    if (modelTasksStatus === "loading" && modelTasksPromise) return modelTasksPromise;
    if (!force && modelTasksStatus === "loaded") return modelTasks;
    modelTasksStatus = "loading";
    modelTasksError = "";
    const request = (async () => {
      try {
        const result = await product().request("GET", route.path);
        if (route.scope !== currentModelScopeKey()) {
          syncModelTasksScope();
          return null;
        }
        const payload = modelTaskPayload(result);
        if (!payload) {
          throw Object.assign(new Error("invalid_response"), {code: "invalid_response"});
        }
        const names = new Map((state?.model?.providers || []).map(item => [item.id, item.name || item.id]));
        modelTasks = {...payload, providers: payload.providers.map(item => ({
          ...item, name: item.name || names.get(item.id) || item.id,
        }))};
        modelTasksStatus = "loaded";
      } catch (error) {
        if (route.scope !== currentModelScopeKey()) {
          syncModelTasksScope();
          return null;
        }
        modelTasks = null;
        modelTasksStatus = "error";
        modelTasksError = errorText(error, "后台模型路由暂时无法读取。");
      }
      return modelTasks;
    })();
    modelTasksPromise = request;
    try { return await request; }
    finally { if (modelTasksPromise === request) modelTasksPromise = null; }
  }

  async function loadQuickModels() {
    syncModelTasksScope();
    if (modelTasksStatus === "loading") return modelTasksPromise;
    await readModelTasks();
    if (root && advancedOpen && advancedBlock === "runtime") render();
  }

  async function retryQuickModels() {
    syncModelTasksScope();
    modelTasksStatus = "idle";
    modelTasksError = "";
    await loadQuickModels();
  }

  async function refreshModelTasks() {
    try {
      await readModelTasks({force: true});
      if (modelTasksStatus === "error") return modelTasksError;
    } catch (error) {
      modelTasks = null;
      modelTasksStatus = "error";
      modelTasksError = errorText(error, "后台模型路由暂时无法读取。");
      return modelTasksError;
    }
    return "";
  }

  function progress() {
    const done = new Set(state?.completed || []);
    const skipped = new Set(state?.skipped || []);
    const bindingDone = Boolean(state?.binding_confirmed);
    const prepared = Boolean(state?.prepared);
    return ORDER.map(id => {
      const complete = id === "binding" ? bindingDone
        : id === "proactive_test" ? Boolean(state?.ready)
        : done.has(id) || skipped.has(id);
      return {id, label: LABELS[id], complete};
    });
  }

  function stepIndex(step) {
    const index = ORDER.indexOf(step);
    return index < 0 ? 0 : index;
  }

  function renderRail(step) {
    /* 旧版引导的分组树：四段标题下挂各自的步骤，完成打勾、当前高亮。 */
    const items = progress();
    const byId = new Map(items.map(item => [item.id, item]));
    const groups = STEP_GROUPS.map(group => {
      const rows = group.steps.map(id => {
        const item = byId.get(id);
        if (!item) return "";
        const index = items.findIndex(entry => entry.id === id);
        return `<li class="setup-progress-step ${item.complete ? "complete" : ""} ${item.id === step ? "current" : ""}">
          <span class="setup-progress-marker">${item.complete ? "✓" : String(index + 1).padStart(2, "0")}</span>
          <span>${escape(item.label)}</span>
        </li>`;
      }).join("");
      const complete = group.steps.every(id => byId.get(id)?.complete);
      const currentGroup = group.steps.includes(step);
      return `<li class="setup-progress-group ${complete ? "complete" : ""} ${currentGroup ? "current" : ""}">
        <strong>${escape(group.title)}</strong>
        <ol class="setup-progress-steps">${rows}</ol>
      </li>`;
    }).join("");
    return `<ol class="setup-progress" aria-label="首次连接进度">${groups}</ol>`;
  }

  function noticeMarkup() {
    const text = notice || migrationProgress;
    if (!text) return "";
    return `<p class="setup-notice" role="status">${escape(text)}</p>`;
  }

  function connectionDiagnostic() {
    const failure = state?.host?.last_failure;
    const message = {staging_profile_conflict: "固定运行档的绑定条件发生变化，请检查 Bot 账号、私聊对象和人格。模型参数变化不会阻止连接。",
      host_binding_changed: "Bot 账号、私聊对象或人格绑定发生变化，请重新核对当前窗口。"}[failure?.code];
    return message ? `<p class="setup-inline-error" role="alert">${escape(message)}</p>` : "";
  }

  function button(action, label, {primary = false, disabled = false} = {}) {
    return `<button type="button" class="button${primary ? " primary" : ""}" data-setup-action="${escape(action)}" ${disabled || busy ? "disabled" : ""}>${escape(label)}</button>`;
  }

  function homeButton(label, primary = false) {
    return `<button type="button" class="button${primary ? " primary" : ""}" data-go="life">${escape(label)}</button>`;
  }

  const legacyTargetLabel = item => product()?.targetLabel?.(item) || item.display_name
    || (item?.scope_kind === "group" ? "未命名群目标" : "未命名私聊范围");
  const legacyRoleLabel = (item,index) => product()?.roleLabel?.(item,index) || item.role_name || item.persona_ids?.filter(Boolean).join(" / ") || "当前角色";
  const legacyAccountLabel = item => product()?.accountLabel?.(item) || item.account_name || "当前账号";
  const legacySources = {"private.host_configuration": "陪伴插件配置", "private.core_profiles": "人物与关系档案",
    "private.authoritative_memory": "旧版私聊权威记忆",
    "private.group_relationship_graph": "群成员互动图", "private.reaction_assets": "表情素材",
    "memory.timeline_and_governance": "记忆与历史记录", "private.work_and_delivery": "任务与投递记录",
    "private.world_assets": "世界与相册素材", "content.creative_projects": "创作项目",
    "image.generation_state": "图片生成记录", "reality.device_and_observations": "设备与观察记录",
     "extensions.session_and_media": "拓展会话与媒体"};
  const legacyConfigurationSources = {
    plugin: ["private.host_configuration"],
    proactive: ["private.host_configuration"],
    reality: ["reality.configuration"],
    all: ["private.host_configuration", "reality.configuration"],
  };
  const legacyConfigurationSourceLabels = {
    "private.host_configuration": "旧版陪伴插件配置",
    "reality.configuration": "旧版 Reality 配置",
  };
  function legacyDisplayText(value) {
    return String(value ?? "")
      .replace(/^台账共核对 [\d,]+ 个 V1 配置键；当前来源有 (\d+) 项未自动迁移，原因已列在差异审计中。$/, "共有 $1 项配置兼容说明，请查看分类中的设置入口与接续方式。")
      .replace(/\bV1\b/g, "旧版").replace(/\bV2\b/g, "当前版本");
  }
  function legacyPersonaLabel(persona, fallback = "旧版人格") {
    const label = String(persona?.label || "");
    return label ? label.replace(/^V1\s+(?=人格\s*[:：])/i, "旧版") : fallback;
  }
  const PROFILE_LABELS = {
    name:"姓名", species:"种族", birthday:"生日", age:"年龄感", gender:"性别", occupation:"职业 / 身份",
    identity:"身份经历", appearance_points:"外貌识别点", hair:"发型与发色", eyes:"眼部特征", clothing:"服饰风格",
    image_identity_hint:"图像识别线索", appearance:"外貌补充", personality:"性格", first_person:"自称与代词",
    speech_style:"说话方式", emotional_style:"情绪表达", social_distance:"社交距离", reaction_patterns:"典型反应",
    voice_examples:"口吻示例", signature_phrases:"标志性表达", daily_habits:"日常习惯", key_lore:"关键设定",
    character_extra:"其他角色补充", motivations:"价值与目标", core_values:"重视的事", desire:"长期目标", hobbies:"爱好",
    boundaries:"互动边界", taboos:"禁忌", world_name:"所处世界", era:"时代背景", tone:"世界基调", rules:"世界规则",
    scenes:"主要场景", network:"世界观关系网", world_extra:"其他世界设定", world:"世界设定补充",
    default_nickname:"对用户的称呼", user_gender:"用户性别", user_birthday:"生日或纪念日", user_occupation:"用户职业",
    role_relation:"用户与角色的关系", interaction:"相处方式", extra:"其他关系补充",
  };
  const TASK_LABELS = {
    "conversation.reply":"对话回复", "proactive.compose":"主动联系", "proactive.judge":"主动内容审核",
    "memory.reflect":"记忆整理", "role.state.maintain":"角色状态", "calendar.plan":"日程规划",
    "role.growth.progress":"成长进展", "role.self_review":"自我复盘", "role.expression.learn":"表达学习",
    "role.expression.review":"回复表达复核", "role.expression.contact_review":"主动表达复核",
    "role.reading.consume":"阅读理解", "image.review":"图片审核", "context.caption":"图片描述",
    "screen.interpret":"识屏理解", "content.authoring":"内容创作", "role.journal":"日记与梦境",
  };

  function operationKey(prefix) {
    return window.crypto?.randomUUID?.() || `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  function taskLabel(kind) {
    if (TASK_LABELS[kind]) return TASK_LABELS[kind];
    return kind.split(".").map(part => part.replace(/_/g, " ")).join(" · ");
  }

  function mergeLegacyRole(base, current) {
    const merged = {...(base || {}), ...(current || {})};
    for (const key of ["legacy_api", "legacy_group_data_api"]) {
      if (!merged[key] && base?.[key]) merged[key] = base[key];
    }
    merged.persona_ids = [...new Set([...(base?.persona_ids || []), ...(current?.persona_ids || [])])];
    return merged;
  }

  /* Before the first runtime profile is started, product().targets() can be
     empty even though /setup/targets already knows the configured roles. Keep
     both directories and merge roles by id; the runtime view wins where it has
     a value, while the setup view fills migration paths and missing personas. */
  function legacyDirectoryTargets() {
    const byId = new Map();
    const add = target => {
      if (!target?.target_id) return;
      const existing = byId.get(target.target_id);
      if (!existing) {
        byId.set(target.target_id, {...target, roles: [...(target.roles || [])]});
        return;
      }
      const roles = new Map((existing.roles || []).map(role => [role.role_id, role]));
      for (const role of target.roles || []) {
        roles.set(role.role_id, mergeLegacyRole(roles.get(role.role_id), role));
      }
      byId.set(target.target_id, {...existing, ...target, roles: [...roles.values()]});
    };
    for (const target of legacyAccountCatalog?.targets || []) add(target);
    for (const target of product()?.targets?.() || []) add(target);
    return [...byId.values()];
  }

  function legacyChoices() {
    const targets = legacyDirectoryTargets().filter(target =>
      (target.roles || []).some(role => role.legacy_api));
    const target = targets.find(item => item.target_id === legacyTargetId) || null;
    const roles = target?.roles?.filter(role => role.legacy_api) || [];
    const role = roles.find(item => item.role_id === legacyRoleId) || null;
    return {targets, target, roles, role};
  }

  function legacyBotKeyOf(target) {
    if (!target) return "";
    // A Bot can own several private ranges. Prefer the stable account tuple;
    // fixtures without native ids fall back to the target so separate bots do
    // not collapse into one row in the picker.
    const stable = [target.platform_id || "", target.bot_id || "", target.account_id || ""];
    return stable.some(Boolean) ? JSON.stringify(stable) : String(target.target_id || "");
  }

  function legacyBotGroups() {
    const groups = new Map();
    const ensure = (source, fallbackKey = "") => {
      if (!source) return null;
      const key = legacyBotKeyOf(source) || fallbackKey;
      if (!key) return null;
      const group = groups.get(key) || {key, targets: [], roles: new Map(), account: source};
      if (!group.account || source.account_name || source.display_name) group.account = {...group.account, ...source};
      groups.set(key, group);
      return group;
    };
    /* page/targets also carries the connected account/persona inventory. Keep
       these entries even when no private target has been configured yet: the
       first migration step must tell the user what still needs preparation. */
    const accounts = legacyAccountCatalog?.accounts || [];
    for (const account of accounts) {
      ensure(account);
    }
    for (const persona of (legacyAccountCatalog?.personas || [])) {
      const matchesAccount = item => item.bot_id === persona.bot_id
        && (!persona.platform_id || item.platform_id === persona.platform_id)
        && (!persona.account_id || item.account_id === persona.account_id);
      const account = accounts.find(matchesAccount)
        || legacyDirectoryTargets().find(matchesAccount)
        || {platform_id: persona.platform_id || "", account_id: persona.account_id || "",
          bot_id: persona.bot_id || "", account_name: persona.account_name || ""};
      const group = ensure(account);
      if (!group) continue;
      const roleId = persona.role_id || `${persona.bot_id || ""}:${persona.persona_id || ""}`;
      const current = group.roles.get(roleId);
      const personaId = persona.persona_id === "[%None]" ? "[%None]" : persona.persona_id || "";
      const role = {role_id: roleId, role_name: persona.label || persona.persona_id || "默认人格",
        persona_ids: personaId ? [personaId] : [], legacy_api: null};
      if (!current) group.roles.set(roleId, {role, target: null});
      else if (personaId && !current.role.persona_ids.includes(personaId)) {
        current.role.persona_ids.push(personaId);
      }
    }
    for (const target of legacyDirectoryTargets()) {
      if (target.scope_kind === "group") continue;
      const group = ensure(target);
      if (!group) continue;
      group.targets.push(target);
      for (const role of target.roles || []) {
        const current = group.roles.get(role.role_id);
        if (!current || (!current.role.legacy_api && role.legacy_api)) {
          group.roles.set(role.role_id, {role: {...role, persona_ids: [
            ...new Set([...(current?.role.persona_ids || []), ...(role.persona_ids || [])])]}, target});
        }
      }
    }
    return [...groups.values()].map(group => ({...group, roles: [...group.roles.values()]}))
      .sort((a, b) => String(a.account?.account_name || a.account?.account_id || a.key)
        .localeCompare(String(b.account?.account_name || b.account?.account_id || b.key), "zh-CN"));
  }

  async function loadLegacyAccountCatalog(generationAtStart) {
    try {
      const catalog = await api("GET", "/targets");
      if (generationAtStart !== mountGeneration || !migrationOnly || !root) return;
      legacyAccountCatalog = catalog && Array.isArray(catalog.accounts) && Array.isArray(catalog.personas)
        ? {...catalog, targets: Array.isArray(catalog.targets) ? catalog.targets : []} : null;
      legacyAccountCatalogState = legacyAccountCatalog ? "ready" : "unavailable";
      if (!legacyBotKey && legacyBotGroups().length === 1) legacyBotKey = legacyBotGroups()[0].key;
      if (!legacyRoleId) preselectUniqueLegacyDestination();
      render();
    } catch {
      if (generationAtStart !== mountGeneration || !migrationOnly || !root) return;
      legacyAccountCatalog = null;
      legacyAccountCatalogState = "unavailable";
      render();
    }
  }

  function legacySelectedBotGroup() {
    const groups = legacyBotGroups();
    return groups.find(item => item.key === legacyBotKey)
      || groups.find(item => item.targets.some(target => target.target_id === legacyTargetId))
      || null;
  }

  function legacyRoleCandidates() {
    return legacyDirectoryTargets().flatMap(target =>
      (target.scope_kind === "group" ? [] : target.roles || []).map((role, index) => ({target, role, index,
        ready: Boolean(role.legacy_api)})));
  }

  async function refreshLegacyRoles() {
    const current = mountGeneration;
    if (legacyRoleSaveMessage.startsWith("角色目录暂未更新：")) legacyRoleSaveMessage = "";
    let directoryError = null;
    try { await product().refreshDirectory(); }
    catch (error) { directoryError = error; }
    if (current !== mountGeneration || !root) return;
    await loadLegacyAccountCatalog(current);
    /* The setup API is authoritative before the first runtime profile starts.
       product().refreshDirectory() legitimately fails with staging_not_initialized
       at that point; only surface it when the setup catalog also has no targets. */
    if (directoryError && !legacyDirectoryTargets().length) {
      legacyRoleSaveMessage = `角色目录暂未更新：${errorText(directoryError, "请重试读取")}`;
    }
    const selected = legacyRoleCandidates().find(item => item.role.role_id === legacyRoleId
      && legacyBotKeyOf(item.target) === legacyBotKey && item.target.scope_kind === "private")
      || legacyRoleCandidates().find(item => item.role.role_id === legacyRoleId
        && legacyBotKeyOf(item.target) === legacyBotKey);
    if (selected) {
      legacyTargetId = selected.target.target_id;
      legacyApi = legacyAddress();
      if (!legacyRoleSaveMessage) legacyRoleSaveMessage = "角色已创建，下一步：核对旧版配置与范围。";
    } else if (legacyRoleId && !legacyRoleSaveMessage) {
      legacyRoleSaveMessage = "角色已创建，但暂时读不到迁移范围。请重新读取角色。";
    }
    render();
    if (legacyRoleReady()) void loadLegacyReview();
  }

  async function saveLegacyRole() {
    if (legacyRoleSaveBusy) return;
    const account = legacySelectedBotGroup()?.account;
    const roleId = legacyNewRoleName.trim();
    const personaIds = [...legacyNewPersonaIds];
    const available = new Set((legacyAccountCatalog?.personas || []).filter(item => account
      && item.bot_id === account.bot_id && item.platform_id === account.platform_id
      && item.account_id === account.account_id && item.mapped === false)
      .map(item => item.persona_id));
    if (!account || !roleId || roleId.length > 128 || !personaIds.length
        || personaIds.some(id => !available.has(id))) {
      legacyRoleSaveMessage = "请填写角色名，并从该 Bot 的人格中至少选择一个。";
      render();
      return;
    }
    const current = mountGeneration;
    const botKey = legacyBotKey;
    legacyRoleSaveBusy = true;
    legacyRoleSaveMessage = "";
    render();
    try {
      await product().request("POST", "accounts/role", {platform_id: account.platform_id,
        account_id: account.account_id, bot_id: account.bot_id, persona_ids: personaIds, role_id: roleId});
    } catch (error) {
      // A lost response can follow a committed write. Read back before inviting a retry.
      let saved = false;
      try {
        const currentBindings = await product().request("GET", "accounts");
        saved = personaIds.every(id => (currentBindings?.personas || []).some(item =>
          item.bot_id === account.bot_id && item.persona_id === id && item.role_id === roleId));
      } catch { /* Keep the original save error. */ }
      if (!saved) {
        if (current !== mountGeneration || !root) return;
        legacyRoleSaveBusy = false;
        legacyRoleSaveMessage = errorText(error, "保存角色失败");
        render();
        return;
      }
    }
    if (current !== mountGeneration || !root) return;
    legacyRoleSaveBusy = false;
    legacyRoleEditorOpen = false;
    legacyRoleSwitcherOpen = false;
    legacyRoleId = roleId;
    legacyRoleSaveMessage = "角色已创建，正在读取可迁入的范围…";
    render();
    legacyRoleSaveMessage = "";
    await refreshLegacyRoles();
  }

  function legacyRoleSetupMarkup() {
    const choices = legacyChoices();
    const groups = legacyBotGroups();
    const selectedGroup = legacySelectedBotGroup();
    const readyRoleCount = groups.reduce((sum, group) =>
      sum + group.roles.filter(item => item.role.legacy_api).length, 0);
    const roles = selectedGroup?.roles || [];
    const account = selectedGroup?.account || null;
    const availablePersonas = (legacyAccountCatalog?.personas || []).filter(item =>
      account && item.bot_id === account.bot_id && item.platform_id === account.platform_id
        && item.account_id === account.account_id && item.mapped === false);
    const selected = Boolean(choices.role && legacyAddress());
    const topology = groups.map(group => {
      const readyRoles = group.roles.filter(item => item.role.legacy_api);
      const account = group.account || group.targets[0] || {};
      const accountName = legacyAccountLabel(account);
      const roleRows = group.roles.length
        ? `<ul class="setup-migration-role-topology-roles">${group.roles.map((item, index) => `<li>
          <span>${escape(legacyRoleLabel(item.role, index))}</span>
          <small class="${item.role.legacy_api ? "is-ready" : "is-pending"}">${item.role.legacy_api ? "可接收迁移" : "尚未建立角色"}</small>
        </li>`).join("")}</ul>`
        : `<p class="setup-migration-role-topology-empty">尚未分配 AstrBot 人格</p>`;
      return `<li>
      <div class="setup-migration-role-topology-account"><strong>${escape(accountName)}</strong><span>${escape(product()?.platformLabel?.(account) || "当前平台")} · Bot ${escape(account.bot_id || "未标记")}</span><small>${readyRoles.length} / ${group.roles.length} 个角色可接收旧版资料</small></div>
      ${roleRows}
    </li>`; }).join("");
    return `<section class="setup-migration-role-setup">
      <div class="setup-migration-role-lead">
        <p class="setup-migration-manager-title">先设置接收角色</p>
        <p>旧版没有伴侣角色。选择已连接的 Bot，再选择现有角色或在这里新建角色。</p>
      </div>
      <div class="setup-migration-context setup-migration-role-form">
        <section class="setup-migration-step"><span class="setup-migration-step-index">01</span><div>
          <p class="setup-migration-manager-label">选择本次检查所用的 Bot 和角色</p>
          <div class="setup-legacy-scope">
            <label><span>Bot 账号</span><select data-setup-legacy="bot" ${legacyRoleSaveBusy ? "disabled" : ""}><option value="">选择 Bot 账号</option>${groups.map(group => {
              const readyCount = group.roles.filter(item => item.role.legacy_api).length;
              return `<option value="${escape(group.key)}" ${legacyBotKey === group.key ? "selected" : ""}>${escape(legacyAccountLabel(group.account || group.targets[0]))} · ${readyCount ? `${readyCount} 个可接收角色` : "尚未准备角色"}</option>`;
            }).join("")}</select></label>
            <label><span>伴侣角色</span><select data-setup-legacy="role" ${selectedGroup && !legacyRoleSaveBusy ? "" : "disabled"}><option value="">选择伴侣角色</option>${roles.map((item,index) => `<option value="${escape(item.role.role_id)}"${legacyRoleId === item.role.role_id ? " selected" : ""}${item.role.legacy_api ? "" : " disabled"}>${escape(legacyRoleLabel(item.role,index))}${item.role.legacy_api ? "" : " · 暂无可用迁移范围"}</option>`).join("")}</select></label>
          </div>
          <p class="setup-migration-field-note">账号决定旧版资料按哪个 Bot 归入；角色决定资料最终归谁。</p>
          <p class="setup-migration-step-note">${selected ? `当前选择「${escape(legacyRoleLabel(choices.role))}」用于读取旧版配置和资料清单；需要其他角色时可以在下方新建。` : "还没有可接收的角色？在下方直接新建，或去「账号与会话」管理角色。"}</p>
        </div></section>
      </div>
      <div class="setup-actions setup-migration-role-actions"><button type="button" class="button" data-setup-legacy-wizard="create-role" ${account && !legacyRoleSaveBusy ? "" : "disabled"}>${legacyRoleEditorOpen ? "收起新建角色" : "新建角色"}</button><button type="button" class="button button-quiet" data-setup-legacy-wizard="refresh-roles" ${legacyRoleSaveBusy ? "disabled" : ""}>重新读取角色</button></div>
      ${!legacyRoleEditorOpen && legacyRoleSaveMessage ? `<p class="setup-migration-step-note" role="status">${escape(legacyRoleSaveMessage)}</p>` : ""}
      ${legacyRoleEditorOpen ? `<section class="setup-migration-role-editor" aria-label="新建伴侣角色">
        <p class="setup-migration-manager-label">为 ${escape(legacyAccountLabel(account || {}))} 新建角色</p>
        <p>先给角色命名，再选择归属这个角色的 AstrBot 人格。可以选多个，至少选一个。</p>
        <div class="setup-legacy-scope">
          <label><span>伴侣角色名</span><input type="text" maxlength="128" autocomplete="off" data-setup-legacy="new-role-name" value="${escape(legacyNewRoleName)}" placeholder="例如：星缘" ${legacyRoleSaveBusy ? "disabled" : ""}></label>
        </div>
        <p class="setup-migration-field-note">角色名用于在小屋和迁移记录里识别这位角色。</p>
        <fieldset class="setup-migration-persona-fieldset" ${legacyRoleSaveBusy ? "disabled" : ""}>
          <legend>AstrBot 人格（至少选择一个）</legend>
          <div class="setup-migration-persona-options">${availablePersonas.map(item => `<label class="setup-migration-persona-option"><input type="checkbox" data-setup-legacy="new-persona" value="${escape(item.persona_id)}" ${legacyNewPersonaIds.has(item.persona_id) ? "checked" : ""}><span>${escape(item.persona_id === "[%None]" ? "默认人格（不使用额外人格）" : item.label || item.persona_id)}</span></label>`).join("")}</div>
          ${availablePersonas.length ? `<p class="setup-migration-persona-count">已选择 ${legacyNewPersonaIds.size} 个</p>` : ""}
        </fieldset>
        <p class="setup-migration-field-note">旧版资料会按人格归入这个角色；至少选择一个人格。</p>
        ${availablePersonas.length ? "" : `<p class="setup-migration-step-note">${account ? "这个 Bot 的已加载人格都已归入角色。如需新的人格，请先在 AstrBot 中创建。" : "请先选择 Bot 账号。"}</p>`}
        ${legacyRoleSaveMessage ? `<p class="setup-migration-step-note" role="status">${escape(legacyRoleSaveMessage)}</p>` : ""}
        <div class="setup-actions"><button type="button" class="button primary" data-setup-legacy-wizard="save-role" ${legacyRoleSaveBusy || !legacyNewRoleName.trim() || !legacyNewPersonaIds.size || !account ? "disabled" : ""}>${legacyRoleSaveBusy ? "正在保存…" : "创建角色并继续"}</button></div>
        ${legacyRoleSaveBusy || !legacyNewRoleName.trim() || !legacyNewPersonaIds.size || !account
          ? `<small class="setup-migration-primary-reason">${legacyRoleSaveBusy ? "正在保存角色…" : !account ? "先选择 Bot 账号" : !legacyNewRoleName.trim() ? "先填写角色名" : "至少选择一个人格"}</small>` : ""}
      </section>` : ""}
      ${topology ? `<section class="setup-migration-role-topology"><header><p class="setup-migration-manager-label">Bot 与伴侣角色</p><span>${groups.length} 个 Bot · ${readyRoleCount} 个角色可接收旧版资料</span></header><ul>${topology}</ul></section>`
        : legacyAccountCatalogState === "loading" ? `<p class="setup-migration-empty" role="status">正在读取已连接的 Bot 账号和 AstrBot 人格…</p>`
          : legacyAccountCatalogState === "unavailable" ? `<p class="setup-migration-empty" role="alert">暂时读不到 Bot 与人格目录。请重试读取。</p>`
            : `<p class="setup-migration-empty" role="status">尚未发现已连接的 OneBot 账号。请先在 AstrBot 连接 Bot 账号。</p>`}
    </section>`;
  }

  /* 第 1 步：先解释角色是什么，再给「当前角色」卡或页内创建表单。 */
  function legacyRoleCardMarkup() {
    const selection = legacySelection();
    const role = selection.role;
    const account = legacySelectedBotGroup()?.account || selection.target || {};
    const personas = legacyRolePersonas(role);
    const managerOpen = !role || legacyRoleSwitcherOpen || legacyRoleEditorOpen;
    const explanation = `<p class="setup-migration-lead">迁移需要一位接收旧版资料的当前版本角色：它绑定一个账号，包含一个或多个人格；旧版资料会按人格归入这个角色。没有角色时可以直接在下面创建。</p>`;
    if (!role) {
      return `<section class="setup-migration-block">
        <h3>接收角色</h3>
        ${explanation}
        <div class="setup-migration-role-empty">
          <p><strong>还没有可接收旧版资料的角色</strong></p>
          <p class="setup-migration-micro">先在下方选择账号并创建角色；也可以去「账号与会话」管理角色，回来后会重新读取。</p>
          <div class="setup-migration-role-actions">
            <button type="button" class="button" data-go="manage" data-manage="host">去角色管理</button>
            <button type="button" class="button button-quiet" data-setup-legacy-wizard="refresh-roles" ${legacyRoleSaveBusy ? "disabled" : ""}>重新读取</button>
            ${legacyRoleEditorOpen ? "" : `<button type="button" class="button primary" data-setup-legacy-wizard="create-role" ${legacyRoleSaveBusy ? "disabled" : ""}>新建角色</button>`}
          </div>
          ${legacyRoleEditorOpen ? "" : `<small class="setup-migration-primary-reason">创建角色后会自动回到这一卡；角色至少需要一个人格。</small>`}
        </div>
        <div class="setup-migration-role-manager" ${managerOpen ? "" : "hidden"}>${legacyRoleSetupMarkup()}</div>
      </section>`;
    }
    const personaText = personas.length
      ? `${personas.length} 个人格：${personas.slice(0, 6).map(item => item.label).join("、")}${personas.length > 6 ? ` 等 ${personas.length} 个` : ""}`
      : "未标注人格（后端未返回人格列表）";
    const ready = legacyRoleReady();
    return `<section class="setup-migration-block">
      <h3>当前角色</h3>
      ${explanation}
      <div class="setup-migration-role-card" aria-label="当前接收角色">
        <div class="setup-migration-role-card-main">
          <p class="setup-migration-role-card-name">${escape(legacyRoleLabel(role))}</p>
          <p class="setup-migration-role-card-account">${escape(legacyAccountLabel(account))} · ${escape(product()?.platformLabel?.(account) || "当前平台")}</p>
          <p class="setup-migration-role-card-personas">${escape(personaText)}</p>
        </div>
        <span class="setup-migration-chip ${ready ? "is-ok" : "is-warn"}">${ready ? "角色已就绪" : "还缺人格"}</span>
      </div>
      <div class="setup-migration-role-actions">
        <button type="button" class="button" data-setup-legacy-wizard="switch-role">换一个角色</button>
        ${legacyRoleEditorOpen
          ? `<button type="button" class="button" data-setup-legacy-wizard="create-role">收起新建角色</button>`
          : `<button type="button" class="button" data-setup-legacy-wizard="create-role">新建角色</button>
             <button type="button" class="button primary" data-setup-legacy-wizard="next" ${ready ? "" : "disabled"}>就用这个角色</button>`}
      </div>
      ${ready ? "" : `<small class="setup-migration-primary-reason">这个角色还没有人格；请先新建一个至少包含一个人格的角色。</small>`}
      ${legacyRoleSaveMessage ? `<p class="setup-migration-role-message" role="status">${escape(legacyRoleSaveMessage)}</p>` : ""}
      <p class="setup-migration-micro">下一步：只读核对配置与迁移范围，不会写入。</p>
      <div class="setup-migration-role-manager" ${managerOpen ? "" : "hidden"}>${legacyRoleSetupMarkup()}</div>
    </section>`;
  }

  function legacyStatusStripMarkup() {
    const selection = legacySelection();
    const role = selection.role;
    const target = selection.target;
    const source = legacySourceStatus();
    const sourceText = source.path || "自动查找";
    const stateText = source.unreadable ? "不可读" : source.found ? "可读" : legacyFinding ? "未发现资料" : "待检测";
    return `<div class="setup-migration-statusline ${source.unreadable ? "is-warn" : ""}" role="status">
      <div class="setup-migration-statusline-item"><span>当前角色</span><strong>${escape(role ? legacyRoleLabel(role) : "尚未设置")}</strong><small>${escape(role ? `${legacyAccountLabel(target || {})} · ${legacyTargetLabel(target || {})}` : "先完成第 1 步")}</small></div>
      <div class="setup-migration-statusline-item is-source"><span>旧版来源</span><strong>${escape(sourceText)}</strong><small>${escape(stateText)}</small></div>
      ${source.unreadable ? `<div class="setup-migration-statusline-alert"><span>${escape(source.reason || "旧版来源不可读")}</span></div>` : ""}
      <div class="setup-migration-statusline-actions">
        <button type="button" class="setup-migration-text-action" data-setup-legacy-action="legacy-scan">${legacyFinding ? "重新检测" : "检测来源"}</button>
        <details class="setup-migration-source-manual">
          <summary>手动指定来源</summary>
          <label class="setup-legacy-path"><span>旧版数据目录（可选）</span><input type="text" inputmode="text" autocomplete="off" spellcheck="false" data-setup-legacy="root" value="${escape(legacyRootDraft)}" placeholder="留空时自动查找"></label>
          <p class="setup-migration-micro">使用旧版插件数据目录的绝对路径；留空时自动查找。</p>
        </details>
      </div>
    </div>`;
  }

  function preselectUniqueLegacyDestination() {
    const destinations = legacyDirectoryTargets().flatMap(target =>
      (target.roles || []).filter(role => role.legacy_api).map(role => ({target, role})));
    const privateDestinations = destinations.filter(item => item.target.scope_kind === "private");
    const choices = privateDestinations.length === 1 ? privateDestinations : destinations;
    if (choices.length !== 1) return false;
    const [{target, role}] = choices;
    legacyTargetId = target.target_id;
    legacyBotKey = legacyBotKeyOf(target);
    legacyRoleId = role.role_id;
    legacyApi = product().relative(role.legacy_api);
    return Boolean(legacyApi);
  }

  function legacyAddress() {
    const role = legacyChoices().role;
    return role?.legacy_api ? product().relative(role.legacy_api) : "";
  }

  function legacySelection() {
    const choices = legacyChoices();
    return {...choices, api: legacyAddress()};
  }

  /* 当前角色里的人格：优先用目录里的名字，退回到 role.persona_ids。 */
  function legacyRolePersonas(role = legacySelection().role) {
    if (!role) return [];
    const catalog = (legacyAccountCatalog?.personas || []).filter(item =>
      item && item.role_id === role.role_id && item.persona_id);
    const ids = [...new Set([
      ...(Array.isArray(role.persona_ids) ? role.persona_ids.filter(Boolean) : []),
      ...catalog.map(item => item.persona_id),
    ])];
    return ids.map(id => ({persona_id: id,
      label: catalog.find(item => item.persona_id === id)?.label
        || (id === "[%None]" ? "默认人格" : id)}));
  }

  /* 角色就绪：有接收接口，且至少包含一个人格。后端未返回 persona_ids 时
     不把“未知”当成“没有”，避免旧接口被误判为空角色。 */
  function legacyRoleReady(role = legacySelection().role) {
    if (!role || !legacyAddress()) return false;
    if (Array.isArray(role.persona_ids) && role.persona_ids.length === 0) return false;
    return true;
  }

  function legacySourceStatus() {
    const findings = legacyFinding?.findings || [];
    const unreadable = findings.filter(item => item?.state === "unreadable");
    const path = legacyFinding?.v1_root || legacyRootDraft.trim();
    const failedScan = !legacyFinding && Boolean(legacyError);
    const unreadableNow = failedScan || unreadable.length > 0;
    const found = Boolean(path || legacyFinding?.install?.v1_present);
    return {path, unreadable: unreadableNow, found,
      reason: failedScan ? legacyError : unreadable[0]?.detail || (legacyFinding ? "部分旧版来源不可读取。" : "")};
  }

  function legacyConfigRows() {
    const review = configurationReview();
    if (review?.supported()) return review.rows();
    return [];
  }

  function legacyRoute(suffix = "") {
    return `${legacyApi}${suffix}`;
  }

  function legacyQuery(scope = legacyConfigScope) {
    const query = {configuration_scope: scope};
    const root = legacyRootDraft.trim();
    if (root) query.v1_root = root;
    return query;
  }

  function legacyDataQuery() {
    const query = {};
    const root = legacyRootDraft.trim();
    if (root) query.v1_root = root;
    if (legacyDataGroupId) query.source_group_id = legacyDataGroupId;
    if (legacyDataPersonaId) query.source_persona_id = legacyDataPersonaId;
    if (legacyImportMemoryDatabase && legacyMemoryOwnerIds.size) query.memory_owner_ids = [...legacyMemoryOwnerIds].sort().join(",");
    if (legacyImportMemoryDatabase && legacyMemoryGroupMappings.size) query.memory_group_mappings = JSON.stringify(
      [...legacyMemoryGroupMappings.entries()].sort(([left], [right]) => left.localeCompare(right))
        .map(([owner_id, target]) => ({owner_id, target_id: target.target_id, role_id: target.role_id})));
    query.reference_slots_available = "24";
    query.reference_bytes_available = String(24 * 20 * 1024 * 1024);
    query.wardrobe_slots_available = "24";
    return query;
  }

  function seedMemoryGroupMappings(data) {
    if (legacyMemoryGroupMappingSeeded) return;
    legacyMemoryGroupMappingSeeded = true;
    let added = false;
    const owners = data?.memory_database?.identity_inventory?.owners || [];
    const targets = data?.memory_database?.group_targets || [];
    for (const owner of owners.filter(item => item.source?.scope === "group")) {
      const source = owner.source;
      const exact = targets.filter(target => target.group_id === source.group_id
        && target.platform_id === source.platform && target.bot_id === source.bot_id);
      const sameNumber = exact.length ? exact : targets.filter(target => target.group_id === source.group_id);
      const choices = sameNumber.flatMap(target => {
        const matchingRoles = target.roles.filter(role => role.persona_ids?.includes(source.persona_id));
        const roles = matchingRoles.length ? matchingRoles : target.roles.length === 1 ? target.roles : [];
        return roles.map(role => ({target_id: target.target_id, role_id: role.role_id}));
      });
      if (choices.length === 1) {
        legacyMemoryGroupMappings.set(owner.owner_id, choices[0]);
        added = true;
      }
    }
    if (added && legacyImportMemoryDatabase) {
      legacyMemoryPlanDirty = true;
      legacyConfirmMemoryMapping = false;
    }
  }

  const LEGACY_SCOPE_REASONS = {
    same_platform_account_and_id: "同平台、同账号、同号，自动归入",
    target_group_missing: "当前账号下没有这个群",
    target_platform_differs: "平台不同",
    target_account_differs: "账号不同",
    target_group_role_missing: "目标群还没有绑定角色",
    multiple_target_candidates: "同号群不唯一",
    persona_not_assigned_to_role: "人格未归属当前角色",
    persona_assigned_to_other_role: "人格属于其他角色",
  };
  function legacyScopeReason(code) { return LEGACY_SCOPE_REASONS[code] || code || "需确认"; }

  /* 逐项回执里的失败原因码也翻译成人话；未知码保留原值，方便核对日志。 */
  const LEGACY_RECEIPT_REASONS = {
    legacy_scope_stale: "迁移范围已变化，请重新读取后再执行",
    legacy_plan_stale: "配置在执行前已更新，重新执行会先重新试算",
    legacy_request_invalid: "请求未被接受，重新执行会先重新试算",
    legacy_source_missing: "旧版来源已不可读取",
    legacy_source_changed: "旧版来源在试算后发生变化，请重新试算",
    legacy_nothing_to_import: "没有可导入内容",
    legacy_group_not_found: "旧版没有这条群记录",
    legacy_memory_private_target_required: "需要先选择私聊目标和角色",
    legacy_native_source_unavailable: "旧版原生配置暂不可读",
  };
  function legacyReceiptReason(item) {
    if (!item?.reason) return "";
    // 原因码不再由状态决定查哪张表：「没有可导入内容」现在是 review 项，
    // 但仍要显示成人话。先查逐项回执词典，未命中再退回范围原因码。
    return LEGACY_RECEIPT_REASONS[item.reason] || legacyScopeReason(item.reason);
  }

  function legacyScopeValid(scope = legacyScope) {
    return Boolean(scope && typeof scope.token === "string" && scope.token
      && Array.isArray(scope.personas) && Array.isArray(scope.groups));
  }

  async function readLegacyScope() {
    if (legacyScopeBusy || legacyWriting()) return;
    const address = legacyAddress();
    const sourceKey = `${legacyTargetId}\u0000${legacyRoleId}\u0000${legacyRootDraft.trim()}`;
    const requestId = ++legacyScopeRequestId;
    const current = () => requestId === legacyScopeRequestId
      && sourceKey === `${legacyTargetId}\u0000${legacyRoleId}\u0000${legacyRootDraft.trim()}`;
    legacyScopeChecked = true;
    if (!address) {
      legacyScope = null;
      legacyScopeError = "先在第 1 步确认接收旧版资料的当前版本角色。";
      render();
      return;
    }
    legacyScopeBusy = true;
    legacyScopeError = "";
    legacyBatchResult = null;
    render();
    try {
      const scope = await migrationRequest("GET", address + "/scope");
      if (!current()) return;
      if (!legacyScopeValid(scope)) throw new Error("迁移范围返回不完整，请重新读取。");
      legacyScope = scope;
    } catch (error) {
      if (!current()) return;
      legacyScope = null;
      legacyScopeError = legacyDisplayText(errorText(error, "读取迁移范围失败。"));
    } finally {
      if (requestId === legacyScopeRequestId) legacyScopeBusy = false;
      render();
    }
  }

  async function applyLegacyBatch() {
    if (!legacyScopeValid() || legacyBatchBusy || legacyWriting()) return;
    const address = legacyAddress();
    if (!address) return;
    /* 自动项与需确认项一起提交：宿主逐项判定，自动项写入，需确认项只回执
       原因。这样一次执行就能得到完整回执，又不会猜测任何归属。 */
    const personas = legacyScope.personas.map(item =>
      (item && typeof item.source_id === "string") ? item.source_id : "");
    const groups = legacyScope.groups.filter(item => item && item.source_id).map(item => ({
      persona_id: typeof item.persona_id === "string" ? item.persona_id : "",
      group_id: item.source_id,
    }));
    if (!personas.length && !groups.length) {
      legacyBatchResult = {token: legacyScope.token, items: [],
        counts: {imported: 0, review: 0, failed: 0}, empty: true, memory_database_skipped: true};
      render();
      return;
    }
    legacyBatchBusy = true;
    legacyBatchResult = null;
    render();
    try {
      legacyBatchResult = await migrationRequest("POST", address + "/batch-apply",
        {token: legacyScope.token, personas, groups, options: {}});
    } catch (error) {
      if (error?.code !== "legacy_selection_changed") {
        legacyBatchResult = {error: legacyDisplayText(errorText(error, "一键迁移没有完成，请重新读取范围。"))};
      }
    } finally {
      legacyBatchBusy = false;
      render();
    }
  }

  /* 第 3 步的主操作：先写入已核对的配置，再按同一范围逐项迁移资料。
     任何一段失败都只记录在回执里，不会中断另一段。 */
  function legacyConfigFailureMessage(error) {
    const code = error?.code || legacyLastErrorCode || "";
    if (code === "legacy_plan_stale") return "配置在执行前已更新。重新执行会先重新试算，再写入最新结果。";
    if (code === "legacy_request_invalid") return "配置写入请求未被接受；重新执行会先重新试算。";
    return legacyDisplayText(errorText(error, "配置写入没有收到回执；重新执行会先重新试算。"));
  }

  async function runLegacyOneClick() {
    if (legacyWizardExecuting || legacyScopeBusy || legacyBatchBusy || legacyWriting()) return;
    const selection = legacySelection();
    if (!selection.api) {
      legacyError = "请先在第 1 步设置接收旧版资料的角色。";
      legacyWizardStep = 1;
      render();
      return;
    }
    legacyWizardExecuting = true;
    legacyError = "";
    legacyLastErrorCode = "";
    legacyBatchResult = null;
    legacyResult = null;
    legacyConfigNoChanges = false;
    legacyOneClickConfigError = "";
    render();
    try {
      if (!legacyConfigSkipped) {
        /* 执行前重新试算：不复用核对页的旧 plan_token，避免第二次执行时
           因设置已被写过而必然 legacy_plan_stale。 */
        let fresh = null;
        try {
          fresh = await migrationRequest("GET", legacyAddress() + "/plan", legacyQuery());
        } catch (error) {
          if (error?.code === "legacy_selection_changed") return;
          legacyOneClickConfigError = `重新试算配置失败：${legacyConfigFailureMessage(error)}`;
        }
        if (fresh) {
          legacyPlan = fresh;
          configurationReview()?.update(fresh);
          const changes = Number(fresh.change_count) || 0;
          if (!fresh.plan_token) {
            legacyOneClickConfigError = "重新试算没有返回可写入的计划；重新执行会先重新试算。";
          } else if (changes === 0) {
            legacyConfigNoChanges = true;
          } else {
            legacyApplyAllConfig = true;
            await act("legacy-apply");
            legacyApplyAllConfig = false;
            if (!legacyResult) {
              legacyOneClickConfigError = legacyConfigFailureMessage(
                legacyLastErrorCode ? {code: legacyLastErrorCode} : null);
            }
          }
        }
      }
      if (!legacyScopeValid()) await readLegacyScope();
      if (legacyScopeValid()) await applyLegacyBatch();
      else legacyBatchResult = {error: legacyScopeError || "迁移范围尚未读取，可重新读取后执行。"};
      legacyMigrationFinished = Boolean(legacyResult || legacyBatchResult || legacyConfigNoChanges);
    } finally {
      legacyApplyAllConfig = false;
      legacyWizardExecuting = false;
      render();
    }
  }

  function legacyOneClickReceipts() {
    const rows = [];
    if (legacyOneClickConfigError) {
      rows.push({state: "failed", label: "配置迁移", detail: legacyOneClickConfigError});
    } else if (legacyResult) {
      const errors = legacyResult.errors || [];
      const first = errors[0];
      rows.push({state: errors.length ? "failed" : "imported", label: "配置迁移",
        detail: errors.length
          ? `${errors.length} 项未写入：${legacyDisplayText(typeof first === "string" ? first : first?.message || first?.reason || "请查看配置对照")}；重新执行会先重新试算`
          : `写入 ${Number(legacyResult.written_count) || 0} 项${legacyResult.restart_required ? "，重载后生效" : ""}`});
    } else if (legacyConfigNoChanges) {
      rows.push({state: "kept", label: "配置迁移", detail: "配置已是最新，无需写入；没有重复执行。"});
    } else if (legacyConfigSkipped) {
      rows.push({state: "kept", label: "配置迁移", detail: "已选择保留当前设置，没有写入。"});
    } else if (legacyPlan && !Number(legacyPlan.change_count)) {
      rows.push({state: "kept", label: "配置迁移", detail: "配置没有需要改写的字段。"});
    }
    if (legacyBatchResult?.error) {
      rows.push({state: "failed", label: "迁移范围", detail: `${legacyBatchResult.error} 可回到核对页重新读取范围后重试。`});
    }
    for (const item of legacyBatchResult?.items || []) {
      const reason = legacyReceiptReason(item);
      const guidance = item.kind === "group"
        ? "；可在逐项迁移里手动映射群"
        : "；可在逐项迁移里手动分配人格";
      rows.push({
        state: item.state === "imported" ? "imported" : item.state === "review" ? "review" : "failed",
        label: item.kind === "group" ? `群 ${item.group_id || "未知群"}` : `人格 ${item.persona_id || "主角色"}`,
        detail: item.state === "imported"
          ? (reason || "已写入当前版本")
          : `${reason || "未写入"}${guidance}`,
      });
    }
    if (legacyBatchResult && !legacyBatchResult.error && !(legacyBatchResult.items || []).length) {
      rows.push({state: "kept", label: "迁移范围", detail: "旧版没有可自动迁移的人格或群窗口。"});
    }
    return rows;
  }

  function legacyOneClickResultMarkup() {
    const rows = legacyOneClickReceipts();
    if (!rows.length) return "";
    const count = state => rows.filter(row => row.state === state).length;
    const labels = {imported: "已导入", review: "需确认", failed: "失败", kept: "已保留"};
    return `<div class="setup-oneclick-result setup-migration-receipts" role="status" aria-live="polite">
      <header class="setup-migration-receipt-head"><strong>本次执行</strong><span>${count("imported")} 已导入 · ${count("review")} 需确认 · ${count("failed")} 失败</span></header>
      <ul class="setup-migration-receipt-list">${rows.map(row =>
        `<li class="is-${row.state}"><em>${labels[row.state] || "已记录"}</em><span>${escape(row.label)}</span><small>${escape(row.detail)}</small></li>`).join("")}</ul>
      <p class="setup-oneclick-notice">单项失败不会中断其余项目；需确认的窗口不会自动写入，仍保留在旧版与隔离归档中。</p>
    </div>`;
  }

  async function readLegacyDataPlan() {
    const address = legacyAddress();
    if (!address) throw new Error("先选择有迁移接口的当前版本目标与人格。");
    const requestId = ++legacyDataRequestId;
    const activeGeneration = mountGeneration;
    const selectionKey = legacyActionKey("legacy-data-plan");
    const current = () => requestId === legacyDataRequestId && activeGeneration === mountGeneration
      && selectionKey === legacyActionKey("legacy-data-plan");
    legacyApi = address;
    legacyDataInventory = legacyDataPlan || legacyDataInventory;
    legacyDataPlan = null;
    legacyDataRefreshing = true;
    render();
    try {
      const plan = await migrationRequest("GET", address + "/data-plan", legacyDataQuery());
      if (!current()) return null;
      if (!plan?.plan_token) throw new Error("资料试算返回不完整，请重新读取清单。");
      legacyDataPlan = legacyDataInventory = plan;
      legacyMemoryPlanDirty = false;
      seedMemoryGroupMappings(plan);
      syncLegacyPersonaMappings(plan.persona_sources || []);
      if (!legacyArchiveCanApply(plan.archive)) legacyImportArchive = false;
      return plan;
    } catch (error) {
      if (!current()) return null;
      throw error;
    } finally {
      if (requestId === legacyDataRequestId && activeGeneration === mountGeneration) legacyDataRefreshing = false;
    }
  }

  function invalidateLegacyDataSelection() {
    legacyDataRequestId++;
    legacyDataRefreshing = false;
    legacyDataInventory = legacyDataPlan || legacyDataInventory;
    legacyDataPlan = legacyDataResult = null;
    legacyConfirmMemberIds = legacyConfirmPersonaMapping = legacyConfirmExpressionMapping = false;
    legacyConfirmReferencePersonaMapping = legacyConfirmMemoryMapping = false;
    legacyGroupPlan = legacyGroupResult = null;
    legacyGroupError = legacyDataError = "";
    legacyGroupConfirmMapping = legacyGroupConfirmMemberIds = legacyGroupConfirmExpressionMapping = false;
  }

  function legacyArchiveCanApply(archive) {
    return ["ready", "incomplete"].includes(archive?.state)
      && (Number(archive.file_count) > 0 || Number(archive.excluded_count) > 0);
  }

  function legacyMemoryArchiveCanApply(archive) {
    return ["ready", "incomplete"].includes(archive?.state)
      && ((Array.isArray(archive.assets) && archive.assets.length > 0)
        || Number(archive.excluded_count) > 0);
  }

  function legacyPersonaQuery(personaId) {
    const query = {};
    const root = legacyRootDraft.trim();
    if (root) query.v1_root = root;
    if (personaId) query.source_persona_id = personaId;
    return query;
  }

  function legacyPersonaTargets() {
    return legacyDirectoryTargets().filter(target => target.scope_kind !== "group"
      && Array.isArray(target.roles) && target.roles.some(role => role.legacy_api));
  }

  function newLegacyPersonaDestination() {
    legacyPersonaDestinationSequence += 1;
    return {destination_id: `destination-${legacyPersonaDestinationSequence}`,
      target_id: "", role_id: "", include_reactions: true, include_worldbook: false, include_private_contacts: true,
      include_journals: false, confirm_member_ids: false, confirm_persona_mapping: false,
      include_reference_images: false, include_wardrobe_items: false,
      confirm_reference_persona_mapping: false,
      plan: null, result: null, status: "unmapped", error: ""};
  }

  function legacyPersonaRole(destination) {
    const target = legacyPersonaTargets().find(item => item.target_id === destination.target_id);
    return target?.roles?.find(role => role.role_id === destination.role_id && role.legacy_api) || null;
  }

  function legacyPersonaAssignments(personas) {
    return (personas || []).flatMap(persona => {
      const sourceId = persona.persona_id || "";
      const mapping = legacyPersonaMappings.get(sourceId);
      if (!mapping || mapping.skip) return [];
      return (mapping.destinations || []).map(destination => ({persona, sourceId, mapping, destination}));
    });
  }

  function legacyPersonaRoleTaken(targetId, roleId, sourceId, destinationId) {
    if (!targetId || !roleId) return false;
    for (const [otherSourceId, mapping] of legacyPersonaMappings) {
      if (mapping.skip) continue;
      for (const destination of mapping.destinations || []) {
        if (otherSourceId === sourceId && destination.destination_id === destinationId) continue;
        if (destination.target_id === targetId && destination.role_id === roleId) return true;
      }
    }
    return false;
  }

  function legacyPersonaMatchSuggestions(personas) {
    const ready = (personas || []).filter(item => item.state === "ready" && item.persona_id);
    const sourceCounts = new Map();
    for (const persona of ready) {
      const sourceId = persona.persona_id;
      sourceCounts.set(sourceId, (sourceCounts.get(sourceId) || 0) + 1);
    }
    const matchesBySource = new Map();
    const sourceClaimsByRole = new Map();
    for (const sourceId of sourceCounts.keys()) {
      const matches = legacyPersonaTargets().flatMap(target => (target.roles || [])
        .filter(role => role.legacy_api && Array.isArray(role.persona_ids)
          && role.persona_ids.includes(sourceId))
        .map(role => ({target_id: target.target_id, role_id: role.role_id})));
      matchesBySource.set(sourceId, matches);
      for (const match of matches) {
        const key = `${match.target_id}\u0000${match.role_id}`;
        if (!sourceClaimsByRole.has(key)) sourceClaimsByRole.set(key, new Set());
        sourceClaimsByRole.get(key).add(sourceId);
      }
    }
    const suggestions = new Map();
    for (const [sourceId, matches] of matchesBySource) {
      if (sourceCounts.get(sourceId) !== 1 || matches.length !== 1) continue;
      const match = matches[0];
      const key = `${match.target_id}\u0000${match.role_id}`;
      if (sourceClaimsByRole.get(key)?.size === 1) suggestions.set(sourceId, match);
    }
    return suggestions;
  }

  function hasDuplicateLegacyPersonaRoles(assignments) {
    const seen = new Set();
    for (const {destination} of assignments) {
      if (!destination.target_id || !destination.role_id) continue;
      const key = `${destination.target_id}\u0000${destination.role_id}`;
      if (seen.has(key)) return true;
      seen.add(key);
    }
    return false;
  }

  function syncLegacyPersonaMappings(personas) {
    const next = new Map();
    const suggestions = legacyPersonaMatchSuggestions(personas);
    for (const persona of personas || []) {
      if (persona.state !== "ready") continue;
      const sourceId = persona.persona_id || "";
      const previous = legacyPersonaMappings.get(sourceId) || {};
      let destinations = Array.isArray(previous.destinations) ? previous.destinations
        : (previous.target_id || previous.role_id ? [{...newLegacyPersonaDestination(),
          target_id: previous.target_id || "", role_id: previous.role_id || "",
          include_reactions: previous.include_reactions !== false,
          include_worldbook: previous.include_worldbook === true,
          include_private_contacts: previous.include_private_contacts !== false,
          include_journals: previous.include_journals === true,
          include_expressions: previous.include_expressions === true,
          confirm_expression_mapping: previous.confirm_expression_mapping === true,
          confirm_member_ids: previous.confirm_member_ids === true,
          confirm_persona_mapping: previous.confirm_persona_mapping === true,
          include_reference_images: previous.include_reference_images === true,
          include_wardrobe_items: previous.include_wardrobe_items === true,
          confirm_reference_persona_mapping: previous.confirm_reference_persona_mapping === true,
          plan: previous.plan || null, result: previous.result || null,
          status: previous.status || "unmapped", error: previous.error || ""}] : []);
      if (!previous.persona_match_suggestion_checked && !previous.skip && !destinations.length) {
        const suggestion = suggestions.get(sourceId);
        if (suggestion) destinations = [{...newLegacyPersonaDestination(), ...suggestion,
          persona_match_suggestion: true, status: "mapped"}];
      }
      next.set(sourceId, {
        source_persona_id: sourceId,
        skip: previous.skip === true,
        persona_match_suggestion_checked: true,
        destinations,
      });
    }
    legacyPersonaMappings = next;
  }

  function legacyPersonaBatchCanApply(destination) {
    const plan = destination?.plan;
    if (!plan?.plan_token || !legacyPersonaRole(destination)) return false;
    const reactions = plan.reaction_assets || {};
    const journals = plan.journals || {};
    const expressions = plan.expressions || {};
    const worldbook = plan.worldbook || {};
    const references = plan.reference_images || {};
    const wardrobe = plan.wardrobe_items || {};
    const worldbookReady = destination.include_worldbook && (
      (Number(worldbook.general_count) > 0 && !worldbook.base_entries_matches)
      || (destination.confirm_member_ids && Number(worldbook.member_count) > 0
        && !worldbook.base_members_matches));
    const referencesReady = (destination.include_reference_images && Number(references.batch_count) > 0)
      || (destination.include_wardrobe_items && Number(wardrobe.batch_count) > 0);
    return (destination.include_reactions && Number(reactions.batch_count) > 0)
      || (destination.include_journals && Number(journals.batch_count) > 0
        && destination.confirm_persona_mapping)
      || (destination.include_expressions && expressions.remaining > 0 && destination.confirm_expression_mapping)
      || (destination.include_private_contacts && legacyPrivateContactsReady(plan) > 0)
      || worldbookReady || (referencesReady && destination.confirm_reference_persona_mapping);
  }

  function renderLegacyPersonaBatch(personas, {allowApply = false} = {}) {
    /* 逐项分配只属于独立的「逐项迁移」入口，默认向导不展示这些控件。 */
    const locked = legacyWriting() || legacyWizardStep !== 2;
    const ready = (personas || []).filter(item => item.state === "ready");
    if (!ready.length) return "";
    syncLegacyPersonaMappings(personas);
    const targets = legacyPersonaTargets();
    const allDecided = ready.every(item => {
      const mapping = legacyPersonaMappings.get(item.persona_id || "");
      return mapping && (mapping.skip || mapping.destinations.every(destination =>
        destination.target_id && destination.role_id && legacyPersonaRole(destination)));
    });
    const assignments = legacyPersonaAssignments(ready);
    const duplicates = hasDuplicateLegacyPersonaRoles(assignments);
    const canPreview = allDecided && assignments.length > 0 && !duplicates && !locked;
    const canApply = allDecided && assignments.some(item => legacyPersonaBatchCanApply(item.destination))
      && assignments.every(({destination}) => destination.plan?.plan_token && !destination.error)
      && !duplicates && !legacyWriting();
    const statusLabel = destination => ({unmapped: "待映射", mapped: "待试算", planning: "正在试算", ready: "已试算",
      applying: "正在导入", imported: "本批已完成", partial: "仍有后续批次或未导入项",
      skipped: "已明确跳过", error: "执行失败", unknown: "结果未确认", stale: "来源已变化"}[destination.status]
      || "待试算");
    const rows = (personas || []).map((persona, index) => {
      const sourceId = persona.persona_id || "";
      if (persona.state !== "ready") return `<div class="setup-legacy-persona-row is-unavailable"><strong>${escape(legacyPersonaLabel(persona))}</strong><span>来源状态：${escape(persona.state || "不可读取")}，不进入自动导入。</span></div>`;
      const mapping = legacyPersonaMappings.get(sourceId);
      const hasSuggestion = mapping.destinations.some(destination => destination.persona_match_suggestion);
      const destinations = mapping.destinations.map((destination, destinationIndex) => {
        const target = targets.find(item => item.target_id === destination.target_id);
        const roles = target?.roles?.filter(role => role.legacy_api) || [];
        const summary = destination.plan;
        const reactionCount = Number(summary?.reaction_assets?.batch_count) || 0;
        const journalCount = Number(summary?.journals?.batch_count) || 0;
        const worldbook = summary?.worldbook || {};
        const references = summary?.reference_images || {};
        const wardrobe = summary?.wardrobe_items || {};
        const worldbookCount = (Number(worldbook.general_count) || 0) + (Number(worldbook.member_count) || 0);
        const result = destination.result;
        const resultLine = result ? `最近一批：表情 ${Number(result.reaction_created) || 0} 张，日记/梦境 ${Number(result.journal_created) || 0} 条，参考图 ${Number(result.reference_images?.reference_created) || 0} 张，衣物 ${Number(result.reference_images?.wardrobe_created) || 0} 条；参考图 ${Number(result.reference_images?.reference_remaining) || 0} 张、衣物 ${Number(result.reference_images?.wardrobe_remaining) || 0} 条待继续。` : "";
        return `<div class="setup-legacy-persona-destination" data-destination-row="${escape(destination.destination_id)}">
          <div class="setup-legacy-persona-destination-head"><strong>伴侣去向 ${destinationIndex + 1}</strong><span>${escape(statusLabel(destination))}${destination.error ? ` · ${escape(destination.error)}` : ""}</span><button type="button" data-setup-legacy-batch-remove="${escape(sourceId)}" data-destination-id="${escape(destination.destination_id)}" ${locked ? "disabled" : ""}>移除</button></div>
          <div class="setup-legacy-persona-destination-fields">
            <label><span>Bot 与私聊范围</span><select data-setup-legacy-batch-target data-persona-id="${escape(sourceId)}" data-destination-id="${escape(destination.destination_id)}" ${mapping.skip || locked ? "disabled" : ""}><option value="">选择 Bot / 私聊范围</option>${targets.map(item => `<option value="${escape(item.target_id)}" ${destination.target_id === item.target_id ? "selected" : ""}>${escape(legacyAccountLabel(item))} · ${escape(legacyTargetLabel(item))}</option>`).join("")}</select></label>
            <label><span>接收伴侣角色</span><select data-setup-legacy-batch-role data-persona-id="${escape(sourceId)}" data-destination-id="${escape(destination.destination_id)}" ${mapping.skip || !target || locked ? "disabled" : ""}><option value="">选择接收角色</option>${roles.map((role, roleIndex) => {
              const duplicate = legacyPersonaRoleTaken(destination.target_id, role.role_id, sourceId, destination.destination_id);
              return `<option value="${escape(role.role_id)}" ${destination.role_id === role.role_id ? "selected" : ""} ${duplicate ? "disabled" : ""}>${escape(legacyRoleLabel(role, roleIndex))}${duplicate ? " · 已映射" : ""}</option>`;
            }).join("")}</select></label>
          </div>
          ${destination.persona_match_suggestion ? `<p class="setup-legacy-persona-note">旧版人格 ID 与一个当前版本角色精确匹配，已预选为候选。请核对账号和角色，再试算并确认导入；此建议不会自动导入。</p>` : ""}
          ${summary ? `<div class="setup-legacy-persona-options">
            <label><input type="checkbox" data-setup-legacy-batch-option="reactions" data-persona-id="${escape(sourceId)}" data-destination-id="${escape(destination.destination_id)}" ${destination.include_reactions ? "checked" : ""} ${!reactionCount || locked ? "disabled" : ""}><span>表情 ${reactionCount} 张</span></label>
            <label><input type="checkbox" data-setup-legacy-batch-option="worldbook" data-persona-id="${escape(sourceId)}" data-destination-id="${escape(destination.destination_id)}" ${destination.include_worldbook ? "checked" : ""} ${!worldbookCount || locked ? "disabled" : ""}><span>世界书 ${worldbookCount} 条</span></label>
            <label><input type="checkbox" data-setup-legacy-batch-option="contacts" data-persona-id="${escape(sourceId)}" data-destination-id="${escape(destination.destination_id)}" ${destination.include_private_contacts ? "checked" : ""} ${!legacyPrivateContactsReady(summary) || locked ? "disabled" : ""}><span>私聊用户与权限（${Number(summary?.private_contacts?.ready) || 0} 位用户 · ${Number(summary?.private_contacts?.authorization?.ready) || 0} 位权限）</span></label>
            ${destination.include_worldbook && worldbook.requires_id_confirmation ? `<label><input type="checkbox" data-setup-legacy-batch-option="confirm-members" data-persona-id="${escape(sourceId)}" data-destination-id="${escape(destination.destination_id)}" ${destination.confirm_member_ids ? "checked" : ""} ${locked ? "disabled" : ""}><span>确认成员账号映射与目标平台一致</span></label>` : ""}
            <label><input type="checkbox" data-setup-legacy-batch-option="expressions" data-persona-id="${escape(sourceId)}" data-destination-id="${escape(destination.destination_id)}" ${destination.include_expressions ? "checked" : ""} ${!summary.expressions?.remaining || locked ? "disabled" : ""}><span>表达 ${Number(summary.expressions?.rule_count) || 0} 条 / 样本 ${Number(summary.expressions?.sample_count) || 0} 份</span></label>
            ${destination.include_expressions ? `<label><input type="checkbox" data-setup-legacy-batch-option="confirm-expressions" data-persona-id="${escape(sourceId)}" data-destination-id="${escape(destination.destination_id)}" ${destination.confirm_expression_mapping ? "checked" : ""} ${locked ? "disabled" : ""}><span>确认表达所属角色与私聊对象</span></label>` : ""}
            <label><input type="checkbox" data-setup-legacy-batch-option="journals" data-persona-id="${escape(sourceId)}" data-destination-id="${escape(destination.destination_id)}" ${destination.include_journals ? "checked" : ""} ${!journalCount || locked ? "disabled" : ""}><span>日记与梦境 ${journalCount} 条</span></label>
            ${destination.include_journals && journalCount ? `<label><input type="checkbox" data-setup-legacy-batch-option="confirm-journals" data-persona-id="${escape(sourceId)}" data-destination-id="${escape(destination.destination_id)}" ${destination.confirm_persona_mapping ? "checked" : ""} ${locked ? "disabled" : ""}><span>确认将该旧版人格资料归入所选角色</span></label>` : ""}
            <label><input type="checkbox" data-setup-legacy-batch-option="reference-images" data-persona-id="${escape(sourceId)}" data-destination-id="${escape(destination.destination_id)}" ${destination.include_reference_images ? "checked" : ""} ${!Number(references.batch_count) || locked ? "disabled" : ""}><span>参考图库 ${Number(references.batch_count) || 0} 张</span></label>
            <label><input type="checkbox" data-setup-legacy-batch-option="wardrobe-items" data-persona-id="${escape(sourceId)}" data-destination-id="${escape(destination.destination_id)}" ${destination.include_wardrobe_items ? "checked" : ""} ${!Number(wardrobe.batch_count) || locked ? "disabled" : ""}><span>衣柜 ${Number(wardrobe.batch_count) || 0} 件（图片 ${Number(wardrobe.image_count) || 0} 张）</span></label>
            ${(destination.include_reference_images && Number(references.batch_count) || destination.include_wardrobe_items && Number(wardrobe.batch_count)) ? `<label><input type="checkbox" data-setup-legacy-batch-option="confirm-reference-persona" data-persona-id="${escape(sourceId)}" data-destination-id="${escape(destination.destination_id)}" ${destination.confirm_reference_persona_mapping ? "checked" : ""} ${locked ? "disabled" : ""}><span>确认图片归入此角色图库并随角色使用</span></label>` : ""}
            ${resultLine ? `<p>${escape(resultLine)}</p>` : ""}
          </div>` : destination.status === "stale" ? `<p class="setup-legacy-persona-note">旧版来源清单在试算期间发生变化，请重新试算全部映射。</p>` : ""}
        </div>`;
      }).join("");
      return `<div class="setup-legacy-persona-row" data-persona-row="${index}">
        <div class="setup-legacy-persona-source"><strong>${escape(legacyPersonaLabel(persona, sourceId ? `旧版人格：${sourceId}` : "主角色（companions.json）"))}</strong><span>${mapping.skip ? "已明确跳过" : `${mapping.destinations.length} 个目的地${hasSuggestion ? " · 唯一人格 ID 匹配候选待核对" : ""}`}</span></div>
        <label class="setup-legacy-persona-skip"><input type="checkbox" data-setup-legacy-batch-skip data-persona-id="${escape(sourceId)}" ${mapping.skip ? "checked" : ""} ${locked ? "disabled" : ""}><span>明确跳过此人格</span></label>
        ${mapping.skip ? "" : `<div class="setup-legacy-persona-destinations">${destinations || `<p class="setup-legacy-persona-note">尚未添加目标。不会自动复制到任何当前版本私聊。</p>`}
          <button type="button" class="button" data-setup-legacy-batch-add="${escape(sourceId)}" ${locked ? "disabled" : ""}>添加一个 Bot / 伴侣角色</button>
        </div>`}
      </div>`;
    }).join("");
    const unready = (personas || []).filter(item => item.state !== "ready").length;
    return `<section class="setup-legacy-persona-batch">
      <h5>旧版人格与多 Bot / 多伴侣角色映射</h5>
      <p>一份旧版人格资料可以添加多个 Bot 和伴侣角色去向；每个去向独立试算和导入，不会自动复制。暂不选择的人格会留待之后处理。${unready ? `另有 ${unready} 份来源不可读取。` : ""}</p>
      ${legacyPersonaBatchError ? `<p class="setup-inline-error">${escape(legacyPersonaBatchError)}</p>` : ""}
      ${legacyPersonaBatchProgress ? `<p class="setup-legacy-persona-progress" role="status">${escape(legacyPersonaBatchProgress)}</p>` : ""}
      ${duplicates ? `<p class="setup-inline-error">同一个当前版本私聊目标与角色只能映射一次，请先移除重复项。</p>` : ""}
      <div class="setup-legacy-persona-list">${rows}</div>
      <div class="setup-actions">${legacyButton("legacy-data-batch-plan", "试算已选择的目的地", {primary: !assignments.every(item => item.destination.plan), disabled: !canPreview})}${canApply && allowApply ? legacyButton("legacy-data-batch-apply", "按目的地逐项导入", {primary: true}) : ""}</div>
    </section>`;
  }

  /* 多角色分配是可选支线：没有开始分配时收进一个抽屉，避免它在预览和导入
     步骤里占据整屏，把真正要确认的内容挤到下面。 */
  function legacyPersonaDrawer(personas, {allowApply = false} = {}) {
    const body = renderLegacyPersonaBatch(personas, {allowApply});
    if (!body) return "";
    const started = (personas || []).some(persona => {
      const mapping = legacyPersonaMappings.get(persona.persona_id || "");
      return Boolean(mapping?.skip || mapping?.destinations?.length);
    });
    const open = started || legacyOpenDetails.has("persona-assignments");
    return `<details class="setup-migration-drawer" data-migration-detail="persona-assignments" ${open ? "open" : ""}>
      <summary><span>按旧版人格分配到更多 Bot / 角色</span><small>${started ? "已开始分配" : "可选 · 不添加时只导入当前角色"}</small></summary>
      ${body}
    </details>`;
  }

  function legacyGroupSelection() {
    const targets = legacyDirectoryTargets().filter(item => item.scope_kind === "group"
      && (item.roles || []).some(role => role.legacy_group_data_api));
    const target = targets.find(item => item.target_id === legacyGroupTargetId) || null;
    const roles = target?.roles?.filter(role => role.legacy_group_data_api) || [];
    const role = roles.find(item => item.role_id === legacyGroupRoleId) || null;
    const inventory = legacyDataPlan || legacyDataInventory;
    const persona = (inventory?.persona_sources || []).find(item =>
      item.persona_id === legacyDataPersonaId) || (!legacyDataPersonaId
        ? {persona_id:"", label:"主角色（companions.json）"} : null);
    const group = (inventory?.group_networks || []).find(item =>
      item.source_group_id === legacyDataGroupId) || null;
    const worldbookGroup = (inventory?.worldbook?.groups || []).find(item =>
      item.source_group_id === legacyDataGroupId) || null;
    const worldbookMembers = (inventory?.worldbook?.member_profile_groups || []).find(item =>
      item.source_group_id === legacyDataGroupId) || null;
    const api = role?.legacy_group_data_api ? product().relative(role.legacy_group_data_api) : "";
    return {targets, target, roles, role, persona, group, worldbookGroup, worldbookMembers, api};
  }

  function renderLegacyGroupMapping() {
    const choices = legacyGroupSelection();
    const counts = legacyGroupPlan?.group_worldbook || {};
    const network = legacyGroupPlan?.source_group_network;
    const expression = legacyGroupPlan?.group_expression || {};
    const networkReady = Boolean(legacyGroupImportNetwork && network);
    const worldbookReady = Boolean(legacyGroupImportWorldbook
      && Number(counts.entries) + Number(counts.members) > 0 && !counts.conflict);
    const expressionReady = Boolean(legacyGroupImportExpressions && expression.available
      && Number(expression.remaining) > 0);
    const canPlan = Boolean(choices.persona && legacyDataGroupId && choices.target && choices.role && !legacyWriting());
    const canApply = Boolean(legacyGroupPlan?.plan_token && choices.role && !legacyWriting()
      && legacyGroupConfirmMapping
      && (networkReady || worldbookReady || expressionReady)
      && (!(networkReady || worldbookReady) || legacyGroupConfirmMemberIds)
      && (!expressionReady || legacyGroupConfirmExpressionMapping));
    return `<section class="setup-legacy-persona-batch setup-legacy-group-mapping">
      <h5>旧版群记录映射</h5>
      <p>选择对应的当前群与角色，核对群号后迁入群资料和已学表达。表达只进入同号群，原审核状态会保留。</p>
      <div class="setup-legacy-persona-row">
        <strong>${escape(legacyPersonaLabel(choices.persona, "先选择可读取的旧版人格"))}</strong>
        <strong>${escape(legacyDataGroupId ? `旧版群 ${legacyDataGroupId}` : "先选择旧版群记录")}</strong>
        <label><span>当前版本群目标</span><select data-setup-legacy="group-target" ${choices.targets.length && legacyDataGroupId ? "" : "disabled"}><option value="">明确选择群目标</option>${choices.targets.map(item => `<option value="${escape(item.target_id)}" ${item.target_id === legacyGroupTargetId ? "selected" : ""}>${escape(legacyTargetLabel(item))} · ${escape(legacyAccountLabel(item))}</option>`).join("")}</select></label>
        <label><span>当前版本角色</span><select data-setup-legacy="group-role" ${choices.target && legacyDataGroupId ? "" : "disabled"}><option value="">明确选择角色</option>${choices.roles.map((item, index) => `<option value="${escape(item.role_id)}" ${item.role_id === legacyGroupRoleId ? "selected" : ""}>${escape(legacyRoleLabel(item, index))}</option>`).join("")}</select></label>
      </div>
      ${legacyGroupError ? `<p class="setup-inline-error">${escape(legacyDisplayText(legacyGroupError))}</p>` : ""}
      ${legacyGroupPlan ? `<div class="setup-legacy-persona-result">
        <p>试算目标：${escape(legacyGroupPlan.target?.platform_id || "")} / ${escape(legacyGroupPlan.target?.account_id || "")} / 群 ${escape(legacyGroupPlan.target?.group_id || "")} · 角色 ${escape(legacyGroupPlan.target?.role_id || "")}</p>
        ${network ? `<p>旧版群成员互动图：${Number(network.member_count) || 0} 位成员 / ${Number(network.edge_count) || 0} 条互动边；超限或无效项已从导入数量中排除。</p>` : `<p>所选人格没有这条群成员互动图。</p>`}
        <p>旧版群资料：${Number(counts.entries) || 0} 条群范围知识，${Number(counts.members) || 0} 条群成员线索。${counts.conflict ? "当前版本已存在其他归属的同名来源，不能覆盖。" : counts.already_present ? "内容已存在。" : ""}</p>
        <p>旧版群表达：${Number(expression.rule_count) || 0} 条规则、${Number(expression.sample_count) || 0} 份样本；${Number(expression.remaining) || 0} 项待导入。${legacyDataGroupId !== choices.target?.group_id ? "表达仅迁入同号群。" : "保留原审核状态。"}</p>
        ${legacyGroupResult?.group_expression ? `<p class="setup-recorded">群表达新增 ${Number(legacyGroupResult.group_expression.created) || 0} 条，保留已迁 ${Number(legacyGroupResult.group_expression.preserved) || 0} 条；样本 ${Number(legacyGroupResult.group_expression.sample_count) || 0} 份。</p>` : ""}
        ${legacyGroupResult ? `<p class="setup-recorded">${legacyGroupResult.group_network ? `群成员互动图新增 ${Number(legacyGroupResult.group_network.members_added) || 0} 位成员、${Number(legacyGroupResult.group_network.edges_added) || 0} 条边。` : ""}${legacyGroupResult.group_worldbook ? `群资料 ${legacyGroupResult.group_worldbook.state === "unchanged" ? "已存在" : "已导入"} ${Number(legacyGroupResult.group_worldbook.entries_imported) || 0} 条知识、${Number(legacyGroupResult.group_worldbook.members_imported) || 0} 条成员线索。` : ""}</p>` : ""}
      </div>` : ""}
      <div class="setup-legacy-data-checks">
        <label><input type="checkbox" data-setup-legacy="group-import-network" ${legacyGroupImportNetwork ? "checked" : ""} ${network ? "" : "disabled"}><span>导入群成员互动图</span></label>
        <label><input type="checkbox" data-setup-legacy="group-import-worldbook" ${legacyGroupImportWorldbook ? "checked" : ""} ${Number(counts.entries) + Number(counts.members) > 0 ? "" : "disabled"}><span>导入群范围资料</span></label>
        <label><input type="checkbox" data-setup-legacy="group-import-expressions" ${legacyGroupImportExpressions ? "checked" : ""} ${expression.available && Number(expression.remaining) > 0 ? "" : "disabled"}><span>导入旧版群表达与样本</span></label>
      </div>
      ${legacyGroupPlan ? `<label class="setup-legacy-confirm"><input type="checkbox" data-setup-legacy="group-confirm-mapping" ${legacyGroupConfirmMapping ? "checked" : ""}><span>确认旧版群 ${escape(legacyGroupPlan.source_group_id)} 对应当前版本平台 ${escape(legacyGroupPlan.target?.platform_id || "")}、账号 ${escape(legacyGroupPlan.target?.account_id || "")}、群 ${escape(legacyGroupPlan.target?.group_id || "")}</span></label>
        ${(networkReady || worldbookReady) ? `<label class="setup-legacy-confirm"><input type="checkbox" data-setup-legacy="group-confirm-member-ids" ${legacyGroupConfirmMemberIds ? "checked" : ""}><span>确认所选目标平台中的成员账号 ID 与旧版历史记录一致</span></label>` : ""}
        ${expressionReady ? `<label class="setup-legacy-confirm"><input type="checkbox" data-setup-legacy="group-confirm-expression-mapping" ${legacyGroupConfirmExpressionMapping ? "checked" : ""}><span>确认旧版群表达归入所选角色及同号群，保留原审核状态</span></label>` : ""}` : ""}
      ${legacyGroupResult ? `<p>最近一次群迁移结果已记录；重复执行将按原有来源标识去重。</p>` : ""}
      <div class="setup-actions">${legacyButton("legacy-group-data-plan", legacyGroupPlan ? "重新试算群映射" : "试算群映射", {primary: !legacyGroupPlan, disabled: !canPlan})}${canApply ? legacyButton("legacy-group-data-apply", "导入所选群数据", {primary: true}) : ""}</div>
    </section>`;
  }

  function legacyScopeStatus(plan = legacyPlan) {
    return Array.isArray(plan?.scope_status) ? plan.scope_status : [];
  }

  function legacyConfigurationBlockers(scope, findings) {
    const sourceIds = legacyConfigurationSources[scope];
    if (!sourceIds) return [{source_id: "configuration_scope", state: "unknown", detail: "配置范围无法确认。"}];
    const byId = new Map((Array.isArray(findings) ? findings : [])
      .filter(item => typeof item?.source_id === "string").map(item => [item.source_id, item]));
    return sourceIds.flatMap(sourceId => {
      const finding = byId.get(sourceId);
      if (!finding) return [{source_id: sourceId, state: "missing", detail: "检查结果缺少此配置来源记录，请重新检查旧版。"}];
      if (["ready", "absent"].includes(finding.state)) return [];
      return [{source_id: sourceId, state: finding.state || "unknown",
        detail: finding.detail || "来源状态无法确认，请重新检查旧版。"}];
    });
  }

  function legacyConfigurationBlockerMessage(blockers) {
    const stateLabels = {missing: "缺少检查记录", unreadable: "存在但不可读取", no_handler: "暂缺转换器"};
    return `配置来源核验未通过，尚未写入：${blockers.map(item => {
      const label = legacyConfigurationSourceLabels[item.source_id] || "配置来源";
      const state = stateLabels[item.state] || item.state || "状态未知";
      return `${label}（${state}）：${legacyDisplayText(item.detail || "请检查来源后重新检查。")}`;
    }).join("；")}`;
  }

  function legacyConfigurationReviewMarkup(scope, findings) {
    const sourceIds = legacyConfigurationSources[scope] || [];
    const byId = new Map((Array.isArray(findings) ? findings : [])
      .filter(item => typeof item?.source_id === "string").map(item => [item.source_id, item]));
    const blockers = legacyConfigurationBlockers(scope, findings);
    const stateLabels = {ready: "可读取", absent: "未发现（可继续）", missing: "缺少检查记录",
      unreadable: "存在但不可读取", no_handler: "暂缺转换器"};
    const items = sourceIds.map(sourceId => {
      const finding = byId.get(sourceId);
      const state = finding?.state || "missing";
      const detail = blockers.find(item => item.source_id === sourceId)?.detail || "";
      return `<li class="${["ready", "absent"].includes(state) ? "is-ready" : "is-blocked"}"><strong>${escape(legacyConfigurationSourceLabels[sourceId])}</strong><span>${escape(stateLabels[state] || state)}</span>${detail ? `<small>${escape(detail)}</small>` : ""}</li>`;
    }).join("");
    return `<div class="setup-legacy-source-review" data-setup-legacy-configuration-sources>
      <strong>本次写入来源</strong><ul>${items}</ul>
      ${blockers.length ? `<p class="setup-inline-error" role="alert">${escape(legacyConfigurationBlockerMessage(blockers))}</p>` : ""}
    </div>`;
  }

  /* 覆盖升级提示：如果这一份当前版本是按旧版的目录名装上的（AstrBot 更新插件时
     替换原目录、目录名不变），插件配置文件名也会跟着是旧版那个文件。
     这是事实，写在页面上比让用户自己发现要好。 */
  function legacyInstallNotice() {
    const install = legacyFinding?.install;
    if (!install) return "";
    const notes = [];
    const actions = [];
    /* 升级状态机：新用户给首次配置指引，老用户按「备份 → 迁移 → 确认 → 清理」走。
       阶段由后端算好（install.phase），页面不再自己猜。 */
    if (install.phase === "upgrade_pending") {
      notes.push("检测到旧版数据：建议先备份旧版数据目录与当前版本配置，再按下面的顺序迁移。");
      if (install.configuration_scopes?.length) {
        notes.push(`已处理配置范围：${install.configuration_scopes.map(scope => ({all: "全部配置", plugin: "陪伴插件基础配置", proactive: "主动消息", reality: "Reality"}[scope] || scope)).join("、")}；可继续选择其余范围。`);
      }
    } else if (install.phase === "upgrade_restart_check") {
      if (install.restart_observed) {
        notes.push(install.configuration_scope_unknown
          ? "发现旧版配置迁移记录，但原记录没有保存迁移范围。请核对重载后的绑定、模型与回复；资料迁移需在下方单独查看。"
          : "旧版配置已迁移。请核对重载后的绑定、模型与回复；资料迁移需在下方单独查看，配置状态不代表资料已迁移。");
        actions.push(legacyButton("legacy-upgrade-confirm", "确认重载后运行正常", {primary: true}));
      } else {
        notes.push(install.configuration_scope_unknown
          ? "发现旧版配置迁移记录，但原记录没有保存迁移范围。重载本插件或重启 AstrBot 后回来核对绑定、模型与回复；资料迁移需在下方单独查看。"
          : "旧版配置已保存；重载本插件或重启 AstrBot 后回来核对绑定、模型与回复。资料迁移需在下方单独查看。");
      }
    } else if (install.phase === "upgrade_cleanup") {
      notes.push("重载后的配置核对已完成。资料迁移进度请在下方单独查看；旧版文件仍保留，确认资料无遗漏后再手动归档。"
        + (install.configuration_scope_unknown ? "旧版记录没有注明配置迁移范围，可在下方查看并补迁遗漏设置。" : "")
        + (install.v1_residues?.length ? `（${install.v1_residues.map(escape).join("、")}）` : "")
        + "旧版代码已被 7.0.0 替换，无需再卸载插件。");
    }
    if (install.takeover) {
      notes.push(`当前是按旧版目录名（${escape(install.plugin_dir || "astrbot_plugin_private_companion")}）升级安装的；`
        + "插件表单配置与旧版共用同一个配置文件，旧版原有键会保留。");
    }
    if (install.pending && install.v1_present) {
      notes.push(`检测到尚未导入的旧版数据（${escape(install.v1_root || "")}）。导入只写当前版本，旧版文件不动。`);
      actions.push(legacyButton("legacy-auto", "立即继承旧版配置", {primary: true}));
    }
    if (!notes.length && !actions.length) return "";
    return `<div class="setup-legacy-install-note" role="status">${notes.map(note => `<p>${note}</p>`).join("")}${actions.length ? `<div class="setup-actions">${actions.join("")}</div>` : ""}</div>`;
  }

  function legacyCurrentStep() {
    return Math.min(Math.max(Number(legacyWizardStep) || 1, 1), MIGRATION_STEPS.length);
  }

  function legacyStepDefinition() {
    return MIGRATION_STEPS[legacyCurrentStep() - 1];
  }

  function legacyStepDescription() {
    if (legacyWizardStep === 1) {
      if (!legacyChoices().role) return "迁移需要一位接收旧版资料的当前版本角色：它绑定一个账号，包含一个或多个人格；旧版资料会按人格归入这个角色。";
      return "确认接收旧版资料的角色。角色需要绑定账号并至少包含一个人格，旧版资料按人格归入这个角色。";
    }
    if (legacyWizardStep === 2) return "只读核对：这一步不会写入任何配置或资料。逐项处理放在独立入口里。";
    if (legacyWizardStep === 3) return "先选迁移方式，再点「开始迁移」才会写入；执行过程逐项回执。";
    return "查看本次结果与仍需处理的内容；未迁移的资料仍保留在旧版和隔离归档中。";
  }

  /* 页面级标题只说明这件事的边界，具体步骤标题放在舞台抬头。 */
  function legacyMigrationHeader() {
    const step = legacyStepDefinition();
    const current = legacyCurrentStep();
    const percent = Math.round(current / MIGRATION_STEPS.length * 100);
    return `<header class="setup-migration-header setup-migration-hero">
      <div class="setup-migration-hero-copy">
        <p class="setup-kicker">迁移设置 · 旧版资料</p>
        <p class="setup-migration-hero-title">四步完成旧版迁入</p>
        <p class="setup-migration-hero-lead">设置接收角色，只读核对配置与范围，明确确认后再迁移。旧版文件始终只读，写入只发生在当前版本。</p>
        <ul class="setup-migration-safety" aria-label="迁移边界">
          <li>四步完成</li><li>旧版只读</li><li>确认后才写入</li><li>可随时返回</li>
        </ul>
      </div>
      <div class="setup-migration-meter" role="group" aria-label="迁移进度">
        <p class="setup-migration-meter-label"><span>第 <b>${current}</b> / ${MIGRATION_STEPS.length} 步</span><span>${escape(step.short)}</span></p>
        <span class="setup-migration-meter-track" aria-hidden="true"><i style="width:${percent}%"></i></span>
        <p class="setup-migration-meter-current">${escape(step.title)}</p>
      </div>
    </header>`;
  }

  function legacyWriteStateLabel() {
    if (legacyWizardStep === 1) return legacyRoleReady() ? "角色已就绪" : "角色待设置";
    if (legacyWizardStep === 2) return "只读核对";
    if (legacyWizardStep === 3) {
      if (legacyWizardExecuting || legacyBatchBusy) return "正在迁移";
      if (legacyBatchResult || legacyResult || legacyConfigNoChanges) return "已收到逐项回执";
      return "等待确认迁移";
    }
    return legacyMigrationFinished ? "迁移结果已记录" : "待查看结果";
  }

  function legacyStageTags() {
    const tags = [];
    const tone = (value) => value ? " is-warn" : " is-ok";
    if (legacyWizardStep === 1) {
      const role = legacySelection().role;
      const personas = legacyRolePersonas(role);
      tags.push([role ? `${personas.length || "若干"} 个人格` : "还没有接收角色", tone(!role)]);
      if (role) tags.push(["账号与人格已绑定", " is-ok"]);
    } else if (legacyWizardStep === 2) {
      const source = legacySourceStatus();
      tags.push([source.unreadable ? "来源不可读" : source.found ? "来源可读" : "来源待检测",
        source.unreadable ? " is-warn" : source.found ? " is-ok" : ""]);
      if (legacyScopeValid()) {
        const counts = legacyScope.counts || {};
        tags.push([`${Number(counts.personas_auto) + Number(counts.groups_auto)} 项自动 · ${Number(counts.review) || 0} 项需确认`,
          Number(counts.review) ? " is-warn" : " is-ok"]);
      }
    } else if (legacyWizardStep === 3) {
      if (legacyWizardExecuting || legacyBatchBusy) tags.push(["正在写入", " is-warn"]);
      else if (legacyBatchResult || legacyResult || legacyConfigNoChanges) {
        const counts = legacyBatchResult?.counts || {};
        const failed = Number(counts.failed) || 0;
        tags.push([`${Number(counts.imported) || 0} 已导入 · ${Number(counts.review) || 0} 需确认 · ${failed} 失败`,
          failed || Number(counts.review) ? " is-warn" : " is-ok"]);
      } else tags.push([legacyConfigSkipped ? "保留当前配置" : "沿用旧版配置", " is-kept"]);
    } else {
      tags.push([legacyMigrationFinished ? "迁移已记录" : "待查看结果", legacyMigrationFinished ? " is-ok" : " is-warn"]);
    }
    return tags.map(([text, extra]) => `<span class="setup-migration-tag${extra}">${escape(text)}</span>`).join("");
  }

  function legacyStageHead() {
    const step = legacyStepDefinition();
    return `<header class="setup-migration-stage-head" data-migration-stage-head tabindex="-1">
      <div class="setup-migration-stage-copy">
        <p class="setup-migration-stage-eyebrow">第 ${String(step.id).padStart(2, "0")} 步 / 共 ${String(MIGRATION_STEPS.length).padStart(2, "0")} 步</p>
        <h2>${escape(step.title)}</h2>
        <p>${escape(legacyStepDescription())}</p>
      </div>
      <div class="setup-migration-stage-tags">
        <span class="setup-migration-tag is-strong">${legacyWriteStateLabel()}</span>
        ${legacyStageTags()}
      </div>
    </header>`;
  }

  function legacyConfigScopeMarkup() {
    const scopes = [["plugin", "陪伴插件基础配置（推荐）"], ["proactive", "主动消息与天气基础设置"],
      ["reality", "Reality 摄像头与音频"], ["all", "全部可迁移配置（含 Reality）"]];
    return `<label class="setup-legacy-path"><span>迁移配置范围</span><select data-setup-legacy="configuration-scope">${scopes.map(([value, label]) => `<option value="${value}" ${legacyConfigScope === value ? "selected" : ""}>${label}</option>`).join("")}</select></label><p class="setup-legacy-scope-note">写入前核对差异，旧版文件保持不变。</p>`;
  }

  function legacyConfigScopeLabel() {
    return ({
      plugin: "陪伴插件基础配置",
      proactive: "主动消息与天气基础设置",
      reality: "Reality 摄像头与音频",
      all: "全部可迁移配置",
    })[legacyConfigScope] || "迁移配置";
  }

  function legacyDataChoiceMarkup(key, label, {checked = false, disabled = false, detail = "", disabledReason = ""} = {}) {
    const hint = disabled && disabledReason ? disabledReason : detail;
    return `<label class="setup-legacy-data-choice${disabled ? " is-disabled" : ""}"><input type="checkbox" data-setup-legacy="${escape(key)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""}><span><strong>${escape(label)}</strong>${hint ? `<small>${escape(hint)}</small>` : ""}</span></label>`;
  }

  function legacyConfirmMarkup(key, label, checked, disabled = false) {
    return `<label class="setup-legacy-confirm"><input type="checkbox" data-setup-legacy="${escape(key)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""}><span>${escape(label)}</span></label>`;
  }

  function legacyButton(action, label, {primary = false, disabled = false} = {}) {
    return `<button type="button" class="button${primary ? " primary" : ""}" data-setup-legacy-action="${escape(action)}" ${disabled || legacyActionDisabled(action) ? "disabled" : ""} ${legacyActionBusy(action) ? 'aria-busy="true"' : ""}>${escape(label)}</button>`;
  }

  function legacyPrivateMemoryPanel(selection) {
    const plan = legacyPrivateMemoryPlan?.api === selection.api ? legacyPrivateMemoryPlan : null;
    const result = legacyPrivateMemoryResult?.api === selection.api ? legacyPrivateMemoryResult : null;
    return `<section class="setup-legacy-section"><header><div><h4>本体私聊记忆</h4><p>按旧身份中的机器人账号和用户核对，再迁入当前所选角色的历史资料库。</p></div></header>
      ${plan ? `<p>匹配 ${Number(plan.matched_person_count) || 0} 个旧身份，${Number(plan.matched_material_count) || 0} 条历史资料；${Number(plan.rejected?.length) || 0} 个来源暂不可转换。</p>
      ${plan.matched_person_count ? legacyConfirmMarkup("private-memory-confirm", "确认这些旧私聊记忆属于当前选中的角色", legacyPrivateMemoryConfirmed) : "<p>当前账号与私聊对象没有匹配的旧本体记忆。</p>"}` : ""}
      ${result ? `<p class="setup-recorded">历史记忆 ${Number(result.imported?.memories) || 0} 条，时间线 ${Number(result.imported?.timeline) || 0} 条${result.already_present ? "（已存在，未重复写入）" : ""}。旧状态与未完话头按历史保留，不启动旧任务。</p>` : ""}
      <div class="setup-actions">${legacyButton("legacy-private-memory-plan", plan ? "重新核对本体记忆" : "核对本体记忆", {disabled: !selection.api})}
      ${plan?.matched_person_count ? legacyButton("legacy-private-memory-apply", "导入本体私聊记忆", {primary: true, disabled: !legacyPrivateMemoryConfirmed}) : ""}
      ${legacyButton("legacy-archived-work", "查看保留的旧工作与投递记录", {disabled: !selection.api})}</div>
      ${legacyArchivedWork?.api === selection.api ? `<details open><summary>已保留的历史归档</summary><pre>${escape(JSON.stringify(legacyArchivedWork.archives, null, 2))}</pre><p>工作、版本和投递回执按原结构保留，可在本地归档中复核；旧任务不会恢复执行。</p></details>` : ""}</section>`;
  }

  function legacyTabs(items, current, attribute, label) {
    return `<nav class="setup-migration-tabs" aria-label="${escape(label)}">${items.map(([id, title]) =>
      `<button type="button" data-${attribute}="${id}" aria-pressed="${id === current}">${title}</button>`).join("")}</nav>`;
  }

  function legacyWizardReady(step) {
    if (step === 1) return legacyRoleReady() && !legacyRoleSaveBusy;
    if (step === 2) return legacyRoleReady();
    if (step === 3) return Boolean(legacyBatchResult || legacyResult || legacyConfigNoChanges)
      && !legacyWizardExecuting;
    if (step === 4) return Boolean(legacyBatchResult || legacyResult || legacyConfigNoChanges)
      && !legacyWizardExecuting;
    return true;
  }

  function legacyWizardReachable(step) {
    if (step <= legacyWizardStep) return true;
    return legacyWizardReady(step - 1);
  }

  function legacyWizardProgressMarkup() {
    const current = legacyCurrentStep();
    const check = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5.5 12.5 4.2 4.2 8.8-9.4"/></svg>`;
    return `<nav class="setup-migration-wizard" aria-label="旧版迁移步骤">
      <p class="setup-migration-wizard-title">迁移步骤</p>
      <ol>${MIGRATION_STEPS.map(item => {
      const active = item.id === current;
      const complete = !active && item.id < current;
      const ready = !active && !complete && legacyWizardReady(item.id);
      const reachable = legacyWizardReachable(item.id);
      const state = active ? "current" : complete ? "done" : ready ? "ready" : "pending";
      return `<li class="is-${state}" data-step-state="${state}">
        <button type="button" data-setup-legacy-step="${item.id}" aria-current="${active ? "step" : "false"}" ${reachable ? "" : "disabled"}>
          <span class="setup-migration-wizard-index">${complete ? check : String(item.id).padStart(2, "0")}</span>
          <span class="setup-migration-wizard-copy"><strong>${escape(item.short)}</strong><small>${escape(item.title)}</small></span>
        </button>
      </li>`;
    }).join("")}</ol>
      <p class="setup-migration-wizard-note">设置角色 → 只读核对 → 明确确认后迁移 → 完成。逐项处理在核对页底部的独立入口里，不占向导步骤。</p>
    </nav>`;
  }

  function legacyWizardNextLabel() {
    if (legacyWizardStep === 1) return "就用这个角色，进入核对";
    if (legacyWizardStep === 2) return "下一步：确认迁移方式";
    if (legacyWizardStep === 3) return "开始迁移";
    return "开始使用";
  }

  function legacyWizardHint() {
    if (legacyWizardStep === 1) {
      if (!legacySelection().role) return "先创建或选择一个接收角色。";
      if (!legacyRoleReady()) return "角色至少需要一个人格；先把人格归入该角色。";
      return "角色已就绪；下一步只读核对，不会写入。";
    }
    if (legacyWizardStep === 2) {
      if (!legacyScopeValid()) return "迁移范围尚未读取；仍可继续，但开始迁移前需要重新检测。";
      return "核对页只读；下一步只确认迁移方式。";
    }
    if (legacyWizardStep === 3) {
      if (legacyWizardExecuting || legacyBatchBusy) return "正在写入并逐项核对，请保持页面打开。";
      if (!legacyScopeValid()) return "迁移范围尚未读取成功，请回到核对页点「重新检测」后重试。";
      if (legacyBatchResult || legacyResult || legacyConfigNoChanges) return "回执已生成；下一步查看结果。";
      return "确认迁移方式后才会写入，单项失败不会中断其余项目。";
    }
    return "迁移记录会保留在「资料与维护」里。";
  }

  function legacyActionBar(left, primary, reason) {
    return `<div class="setup-migration-actionbar">
      <div class="setup-migration-actionbar-left">${left}</div>
      <div class="setup-migration-actionbar-right">${primary}
        ${reason ? `<small class="setup-migration-primary-reason" role="status">${escape(reason)}</small>` : ""}
      </div>
    </div>`;
  }

  function legacyWizardControls() {
    if (legacyWizardStep === 1) {
      /* 第 1 步的唯一 primary 在角色卡里，避免同屏两个主按钮。 */
      return "";
    }
    if (legacyWizardStep === 2) {
      const left = `<button type="button" class="button" data-setup-legacy-wizard="back">上一步</button>
        <button type="button" class="setup-migration-text-action" data-setup-legacy-wizard="manual">需要逐项处理？打开逐项迁移</button>`;
      return legacyActionBar(left,
        `<button type="button" class="button primary" data-setup-legacy-wizard="next">${legacyWizardNextLabel()}</button>`,
        legacyWizardHint());
    }
    if (legacyWizardStep === 3) {
      const executing = legacyWizardExecuting || legacyBatchBusy;
      const done = Boolean(legacyBatchResult || legacyResult || legacyConfigNoChanges);
      const left = `<button type="button" class="button" data-setup-legacy-wizard="back" ${executing ? "disabled" : ""}>上一步</button>
        ${done && !executing ? `<button type="button" class="button" data-setup-legacy-wizard="run">重新执行迁移</button>` : ""}`;
      const primary = done
        ? `<button type="button" class="button primary" data-setup-legacy-wizard="next" ${executing ? "disabled" : ""}>查看迁移结果</button>`
        : `<button type="button" class="button primary" data-setup-legacy-wizard="run" ${executing || !legacyScopeValid() ? "disabled" : ""} aria-busy="${executing}">${executing ? "正在迁移…" : "开始迁移"}</button>`;
      return legacyActionBar(left, primary, legacyWizardHint());
    }
    const left = `<button type="button" class="button" data-setup-legacy-wizard="back">返回核对</button>`;
    return legacyActionBar(left,
      `<button type="button" class="button primary" data-go="life">开始使用</button>`,
      legacyWizardHint());
  }

  function legacyConfigurationStepMarkup({findings, scope, settings, differences, audit, blocked, canApply}) {
    const review = configurationReview();
    if (!review?.supported() && legacyActionBusy("legacy-plan")) return `<section class="setup-migration-wizard-panel setup-migration-config-step" aria-busy="true"><h3>确认配置</h3><p class="setup-migration-empty">正在读取配置对照…</p></section>`;
    if (review?.supported()) return `<section class="setup-migration-wizard-panel setup-migration-config-step">
      ${review.render({locked: legacyWriting() || legacyActionBusy("legacy-plan")})}
      <div class="setup-migration-config-write">
        ${legacyButton("legacy-apply", "写入当前版本", {primary: true,
          disabled: !canApply || Boolean(review.stats().pending)})}
        <small class="setup-migration-micro">${review.stats().pending ? "先处理需确认的配置项" : "按当前逐项选择写入；也可以回到向导使用一键迁移"}</small>
      </div>
      <details class="setup-migration-config-options"><summary>更多迁移选项</summary>
        ${legacyConfigScopeMarkup()}<div class="setup-actions">${legacyButton("legacy-plan", "重新读取配置对照")}</div>
      </details></section>`;
    const pending = !legacyFinding ? "尚未检查旧版来源；先完成只读检查，页面才会显示可迁移范围。"
      : legacyResult ? `配置已写入 ${Number(legacyResult.written_count) || 0} 项；如需重新试算，先点「重新检查旧版」。`
        : !legacyPlan ? "来源已读取，点击“试算配置差异”查看将要写入的字段。"
          : legacyPlan.change_count > 0 && !legacyResult ? `已试算 ${Number(legacyPlan.change_count) || 0} 项配置，核对后写入当前版本。`
            : "配置没有需要改写的字段，可以继续核对资料范围。";
    return `<section class="setup-migration-check setup-migration-wizard-panel"><header><div><h3>确认配置</h3><p>${escape(pending)}</p></div><span class="setup-migration-status">${legacyResult ? "已写入" : legacyPlan ? "已试算" : legacyFinding ? "已检查" : "待检查"}</span></header>
      ${legacyConfigScopeMarkup()}
      <div class="setup-migration-wizard-actions setup-migration-inline-actions">
        ${legacyButton("legacy-scan", legacyFinding ? "重新检查旧版" : "检查旧版配置", {disabled: !legacySelection().api})}
        ${legacyFinding && !legacyPlan ? legacyButton("legacy-plan", "试算配置差异", {primary: true}) : ""}
        ${legacyPlan && !legacyResult && legacyPlan.change_count > 0 ? legacyButton("legacy-apply", "写入当前版本", {primary: true, disabled: !canApply}) : ""}
      </div>
      ${legacyError ? `<p class="setup-inline-error" role="alert">${escape(legacyDisplayText(legacyError))}</p>` : ""}
      ${legacyConfigurationCheckMarkup(findings, scope, settings, differences, audit)}
    </section>`;
  }

  function legacyDetails(key, title, body, {attention = false} = {}) {
    return `<details class="setup-migration-detail${attention ? " needs-attention" : ""}" data-migration-detail="${escape(key)}" ${legacyOpenDetails.has(key) ? "open" : ""}><summary>${title}</summary><div>${body}</div></details>`;
  }

  const LOST_CONFIG_OUTCOMES = new Set(["unsupported", "mechanism", "retained", "inapplicable", "unknown"]);
  const LOST_CONFIG_LABELS = {unsupported: "暂不支持，仅保留原件", mechanism: "新版机制已替代",
    retained: "未沿用，仅保留原件", inapplicable: "当前不适用", unknown: "尚待核实"};

  function legacyFormatConfigValue(value) {
    if (value === true || value === "True") return "开启";
    if (value === false || value === "False") return "关闭";
    if (value === null || value === undefined || value === "") return "未设置";
    if (Array.isArray(value)) return value.length ? `${value.length} 项` : "未设置";
    if (typeof value === "object") {
      if (Object.hasOwn(value, "configured")) return value.configured ? "已配置" : "未配置";
      try { return JSON.stringify(value); } catch { return "已配置"; }
    }
    return String(value);
  }

  /* 把配置对照整理成「改写 / 失效 / 保留」三组，供只读核对页的三个区块复用。 */
  function legacyConfigDerived() {
    const review = configurationReview();
    if (review?.supported()) {
      const rows = review.rows();
      const changedRows = rows.filter(row => row.selection_id);
      const lostRows = rows.filter(row => !row.selection_id && LOST_CONFIG_OUTCOMES.has(row.outcome));
      const unchanged = rows.filter(row => !changedRows.includes(row) && !lostRows.includes(row));
      return {hasData: true, changedRows, lostRows, unchanged,
        counts: {changed: changedRows.length, lost: lostRows.length, kept: unchanged.length}};
    }
    const differences = legacyPlan?.differences || legacyResult?.differences || [];
    const changedRows = differences.filter(item => !item.audit).map(item => ({
      label: item.field || item.source_key || "配置项",
      oldText: legacyFormatConfigValue(item.before),
      currentText: legacyFormatConfigValue(item.after),
      note: legacyDisplayText(item.explain || item.summary || "沿用旧版值"),
    }));
    const lostRows = differences.filter(item => item.audit).map(item => ({
      label: item.source_key || item.field || "旧设置",
      oldText: legacyFormatConfigValue(item.before),
      currentText: "不沿用",
      note: legacyDisplayText(item.explain || item.summary ||
        window.CompanionMigrationConfigReview?.describe(item)?.summary || "旧设置不会沿用"),
    }));
    const hasPlan = Boolean(legacyPlan || legacyResult);
    return {hasData: hasPlan || changedRows.length + lostRows.length > 0, changedRows, lostRows, unchanged: [],
      counts: {changed: Number(legacyPlan?.change_count) || changedRows.length,
        lost: lostRows.length, kept: 0}};
  }

  function legacyConfigSummaryBlockMarkup() {
    const derived = legacyConfigDerived();
    if (!derived.hasData) {
      return `<section class="setup-migration-block">
        <h3>配置变化</h3>
        <p class="setup-migration-lead">配置对照尚未读取。先在上方点「重新检测」读取旧版配置，这里会显示哪些项会改写、失效或保留。</p>
      </section>`;
    }
    const {changed, lost, kept} = derived.counts;
    return `<section class="setup-migration-block">
      <h3>配置变化</h3>
      <p class="setup-migration-summary-line">配置将改写 <strong>${changed}</strong> 项 · 失效 <strong>${lost}</strong> 项 · 保留 <strong>${kept}</strong> 项</p>
      <p class="setup-migration-micro">只读核对；第 3 步选择「沿用旧版配置」后才会写入。</p>
    </section>`;
  }

  function legacyConfigTableBlockMarkup() {
    const derived = legacyConfigDerived();
    if (!derived.changedRows.length && !derived.lostRows.length && !derived.counts.kept) {
      return `<section class="setup-migration-block">
        <h3>变更与失效明细</h3>
        <p class="setup-migration-micro">${derived.hasData ? "没有需要改写或会失效的配置项。" : "读取配置对照后显示明细。"}</p>
      </section>`;
    }
    const rowMarkup = (row, kind) => `<tr class="is-${kind}">
      <th scope="row"><span class="setup-migration-table-badge is-${kind}">${kind === "changed" ? "变更" : "失效"}</span><strong>${escape(row.label || "配置项")}</strong>${row.key ? `<code>${escape(row.key)}</code>` : ""}</th>
      <td data-label="现在" title="${escape(row.oldText || "")}">${escape(row.oldText || "—")}</td>
      <td data-label="迁移后" title="${escape(row.currentText || "")}">${escape(row.currentText || "—")}</td>
      <td data-label="影响与原因" title="${escape(row.note || "")}">${escape(row.note || "—")}</td>
    </tr>`;
    const unchangedLabels = derived.unchanged.map(row => row.label).filter(Boolean);
    return `<section class="setup-migration-block">
      <h3>变更与失效明细</h3>
      <div class="setup-migration-table-scroll">
        <table class="setup-migration-compact-table">
          <colgroup><col class="col-item"><col class="col-now"><col class="col-after"><col class="col-reason"></colgroup>
          <thead><tr><th scope="col">项目</th><th scope="col">现在</th><th scope="col">迁移后</th><th scope="col">影响与原因</th></tr></thead>
          <tbody>${derived.changedRows.map(row => rowMarkup(row, "changed")).join("")}${derived.lostRows.map(row => rowMarkup(row, "lost")).join("")}</tbody>
          ${derived.counts.kept ? `<tfoot><tr><td colspan="4"><details class="setup-migration-unchanged"><summary>其余 ${derived.counts.kept} 项未变化，保留当前</summary><p>${escape(unchangedLabels.slice(0, 40).join("、"))}${unchangedLabels.length > 40 ? ` 等 ${unchangedLabels.length} 项` : ""}</p></details></td></tr></tfoot>` : ""}
        </table>
      </div>
    </section>`;
  }

  function legacyScopeBlockMarkup() {
    const valid = legacyScopeValid();
    if (!valid) {
      return `<section class="setup-migration-block">
        <h3>迁移范围</h3>
        <p class="setup-migration-lead">${legacyScopeBusy ? "正在读取迁移范围…" : "迁移范围还没有读取成功。"}</p>
        ${legacyScopeError ? `<p class="setup-inline-error" role="alert">${escape(legacyScopeError)}</p>` : ""}
        <div class="setup-migration-role-actions">
          <button type="button" class="button" data-setup-legacy-run="scope" ${legacyScopeBusy ? "disabled" : ""}>${legacyScopeBusy ? "正在读取…" : "重新读取迁移范围"}</button>
        </div>
      </section>`;
    }
    const counts = legacyScope.counts || {};
    const autoPersonas = (legacyScope.personas || []).filter(item => item.auto);
    const autoGroups = (legacyScope.groups || []).filter(item => item.auto);
    const review = legacyScope.review || [];
    const autoRows = [
      ...autoPersonas.map(item => `<li><span>人格 ${escape(item.source_id || "主角色")}</span><small>自动归入当前角色</small></li>`),
      ...autoGroups.map(item => `<li><span>群 ${escape(item.source_id)}</span><small>${Number(item.counts?.members) || 0} 位成员 · ${Number(item.counts?.edges) || 0} 条关系边</small></li>`),
    ].join("");
    const reviewRows = review.map(item => `<li><span>${item.kind === "group" ? "群" : "人格"} ${escape(item.source_id || "主角色")}</span><small>${escape(legacyScopeReason(item.reason))}</small></li>`).join("");
    return `<section class="setup-migration-block">
      <h3>迁移范围</h3>
      <div class="setup-migration-scope-grid">
        <div class="setup-migration-scope-stat"><strong>${Number(counts.personas_auto) || 0}</strong><span>人格自动归入</span><small>共 ${Number(counts.personas) || 0} 个</small></div>
        <div class="setup-migration-scope-stat"><strong>${Number(counts.groups_auto) || 0}</strong><span>群自动归入</span><small>共 ${Number(counts.groups) || 0} 个</small></div>
        <div class="setup-migration-scope-stat"><strong>${Number(legacyScope.private?.contacts) || 0}</strong><span>私聊联系人</span><small>按账号自动对应</small></div>
        <div class="setup-migration-scope-stat${Number(counts.review) ? " is-warn" : ""}"><strong>${Number(counts.review) || 0}</strong><span>需确认</span><small>${Number(counts.review) ? "不会自动执行" : "没有遗留"}</small></div>
      </div>
      <div class="setup-migration-scope-columns">
        <section class="setup-migration-scope-column"><p class="setup-migration-column-title">自动归入 <span>${autoPersonas.length + autoGroups.length}</span></p><ul>${autoRows || `<li class="is-empty">没有可自动归入的窗口</li>`}</ul></section>
        <section class="setup-migration-scope-column is-warn"><p class="setup-migration-column-title">需要确认 <span>${review.length}</span></p><ul>${reviewRows || `<li class="is-empty">没有需要确认的窗口</li>`}</ul></section>
      </div>
      <p class="setup-migration-micro">需确认项不会自动写入；需要手动选择时，打开下方「逐项迁移」。</p>
    </section>`;
  }

  function legacyReviewStepMarkup() {
    return `${legacyConfigSummaryBlockMarkup()}${legacyConfigTableBlockMarkup()}${legacyScopeBlockMarkup()}`;
  }

  function legacyMigrationStepMarkup() {
    const valid = legacyScopeValid();
    const counts = valid ? (legacyScope.counts || {}) : {};
    const configWrites = legacyConfigSkipped ? 0 : (Number(legacyPlan?.change_count) || 0);
    const personas = Number(counts.personas_auto) || 0;
    const groups = Number(counts.groups_auto) || 0;
    const contacts = Number(legacyScope?.private?.contacts) || 0;
    const locked = legacyWizardExecuting || legacyBatchBusy
      || Boolean(legacyBatchResult || legacyResult || legacyConfigNoChanges);
    const confirmation = valid
      ? `${legacyConfigSkipped ? "保留当前配置" : `写入配置 ${configWrites} 项`} + 导入 ${personas} 个人格 / ${groups} 个群 / ${contacts} 位联系人`
      : "迁移范围尚未读取；回到核对页点「重新检测」后才能开始。";
    const executed = Boolean(legacyBatchResult || legacyResult || legacyConfigNoChanges);
    const receiptAction = (Number(legacyBatchResult?.counts?.review) || 0) || (Number(legacyBatchResult?.counts?.failed) || 0)
      ? `<button type="button" class="setup-migration-text-action" data-setup-legacy-wizard="manual">需要逐项处理？打开逐项迁移</button>` : "";
    return `<section class="setup-migration-block">
      <h3>迁移方式</h3>
      <div class="setup-migration-method" role="radiogroup" aria-label="配置迁移方式">
        <label class="setup-migration-method-option ${legacyConfigSkipped ? "" : "is-selected"}">
          <input type="radio" name="setup-config-mode" data-setup-legacy="config-mode" value="inherit" ${legacyConfigSkipped ? "" : "checked"} ${locked ? "disabled" : ""}>
          <span><strong>沿用旧版配置</strong><small>${configWrites ? `按核对结果写入 ${configWrites} 项` : "本次配置无变化"}</small></span>
        </label>
        <label class="setup-migration-method-option ${legacyConfigSkipped ? "is-selected" : ""}">
          <input type="radio" name="setup-config-mode" data-setup-legacy="config-mode" value="keep" ${legacyConfigSkipped ? "checked" : ""} ${locked ? "disabled" : ""}>
          <span><strong>保留当前配置</strong><small>只迁移资料，不改当前设置</small></span>
        </label>
      </div>
      <p class="setup-migration-confirm-line">${escape(confirmation)}</p>
    </section>
    ${legacyWizardExecuting || legacyBatchBusy ? `<div class="setup-migration-progress" role="progressbar" aria-label="正在迁移" aria-valuetext="正在迁移"><span></span></div>` : ""}
    ${executed ? `<section class="setup-migration-block">
      <h3>执行回执</h3>
      ${legacyOneClickResultMarkup()}
      ${receiptAction}
    </section>` : ""}`;
  }

  function legacyFinishStepMarkup() {
    const counts = legacyScopeValid() ? (legacyScope.counts || {}) : {};
    const imported = Number(legacyBatchResult?.counts?.imported) || 0;
    const review = Number(legacyBatchResult?.counts?.review) || Number(counts.review) || 0;
    const failed = Number(legacyBatchResult?.counts?.failed) || 0;
    const configState = legacyResult ? `已写入 ${Number(legacyResult.written_count) || 0} 项`
      : legacyConfigNoChanges ? "配置已是最新"
        : legacyConfigSkipped ? "保留当前配置" : "未写入配置";
    const remaining = [];
    if (legacyOneClickConfigError) remaining.push(["配置迁移未完成", legacyOneClickConfigError]);
    if (legacyResult?.errors?.length) remaining.push(["部分配置未写入", `${legacyResult.errors.length} 项；重新执行会先重新试算`]);
    if (legacyBatchResult?.error) remaining.push(["迁移没有完成", legacyBatchResult.error]);
    for (const item of legacyBatchResult?.items || []) {
      if (item.state === "review") remaining.push([`${item.kind === "group" ? "群" : "人格"} ${item.persona_id || item.group_id || "主角色"} 需确认`, legacyReceiptReason(item)]);
      if (item.state === "failed") remaining.push([`${item.kind === "group" ? "群" : "人格"} ${item.persona_id || item.group_id || "主角色"} 失败`, legacyReceiptReason(item) || "请查看回执"]);
    }
    if (legacyDataResult?.errors?.length) remaining.push(["逐项资料未完成", `${legacyDataResult.errors.length} 项，可在逐项迁移中查看`]);
    if (legacyBatchResult?.memory_database_skipped) remaining.push(["独立记忆库未迁移", "需要身份映射时，请到逐项迁移中处理"]);
    return `<section class="setup-migration-block">
      <h3>结果汇总</h3>
      <div class="setup-migration-finish-stats" aria-label="迁移结果汇总">
        <div><strong>${configState}</strong><span>配置迁移</span></div>
        <div><strong>${imported} 项</strong><span>资料已导入</span></div>
        <div class="${review ? "is-warn" : ""}"><strong>${review} 项</strong><span>仍需确认</span></div>
        <div class="${failed ? "is-danger" : ""}"><strong>${failed} 项</strong><span>执行失败</span></div>
      </div>
    </section>
    <section class="setup-migration-block">
      <h3>仍需处理</h3>
      ${remaining.length ? `<ul class="setup-migration-remaining">${remaining.map(([title, detail]) => `<li><strong>${escape(title)}</strong><small>${escape(detail || "")}</small></li>`).join("")}</ul>` : `<p class="setup-migration-micro">没有需要处理的内容。</p>`}
    </section>
    <section class="setup-migration-block">
      <h3>逐项回执</h3>
      ${legacyOneClickResultMarkup() || `<p class="setup-migration-micro">还没有执行记录。</p>`}
      ${legacyDataResult ? `<details class="setup-migration-manual-result"><summary>逐项资料回执</summary>${legacyDataResultMarkup()}</details>` : ""}
    </section>`;
  }

  function legacyResultTable(rows, columns = ["资料", "本次新增", "待继续"]) {
    return `<div class="setup-migration-table-wrap"><table class="setup-migration-table"><thead><tr>${columns.map(item => `<th scope="col">${escape(item)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${row.map((value, index) => index ? `<td>${escape(value)}</td>` : `<th scope="row">${escape(value)}</th>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }

  function legacyDataResultMarkup() {
    const result = legacyDataResult;
    if (!result) return "";
    const rows = [];
    if ("reaction_created" in result) rows.push(["表情素材", `${Number(result.reaction_created) || 0} 张`, `${Number(result.reaction_remaining) || 0} 张`]);
    if ("journal_created" in result) rows.push(["日记与梦境", `${Number(result.journal_created) || 0} 条`, `${(Number(result.journal_remaining) || 0) + (Number(result.journal_derivatives_remaining) || 0)} 条`]);
    if (result.private_contacts) rows.push(["旧版私聊用户", `${Number(result.private_contacts.created) || 0} 位`, `已存在 ${Number(result.private_contacts.already_present) || 0} 位 · 身份待识别 ${result.private_contacts.pending?.length || 0} 位`]);
    if (result.private_contacts?.authorization) {
      const access = result.private_contacts.authorization;
      rows.push(["身份与私聊权限", `${Number(access.identity_restored) || 0} 位核心身份 · ${Number(access.permissions_restored) || 0} 项权限`, `保留当前设置 ${Number(access.preserved) || 0} 项 · ${Number(access.deferred) || 0} 位等待私聊接续`]);
    }
    if (result.expressions) rows.push(["已学表达", `${Number(result.expressions.created) || 0} 条`, `保留已迁 ${Number(result.expressions.preserved) || 0} 条、样本 ${Number(result.expressions.sample_count) || 0} 份`]);
    if (result.reference_images) {
      const images = result.reference_images;
      rows.push(["参考图库", `${Number(images.reference_created) || 0} 张`, `${Number(images.reference_remaining) || 0} 张`]);
      rows.push(["衣柜", `衣柜新增 ${Number(images.wardrobe_created) || 0} 件`, `${Number(images.wardrobe_remaining) || 0} 件${images.wardrobe_images_skipped ? ` · ${Number(images.wardrobe_images_skipped)} 件仅保留文字` : ""}`]);
    }
    if (result.worldbook) {
      const world = result.worldbook;
      rows.push(["世界知识", `${Number(world.entries_imported) || 0} 条`, "—"]);
      rows.push(["熟人资料", `${Number(world.members_imported) || 0} 人`, `${Number(world.member_ids_unconfirmed) || 0} 条待确认身份`]);
      rows.push(["群知识", `${Number(world.group_entries_imported) || 0} 条`, `${Number(world.group_entries_unmapped) || 0} 条待映射`]);
    }
    if (result.group_network) rows.push(["群互动记录", `${Number(result.group_network.members_added) || 0} 人 / ${Number(result.group_network.edges_added) || 0} 条关系`, result.group_network.duplicate ? "此前已导入" : "—"]);
    if (result.memory_materials) rows.push(["历史记忆", `${Number(result.memory_materials.imported?.memories) || 0} 条 / 时间线 ${Number(result.memory_materials.imported?.timeline) || 0} 条`, result.memory_materials.state === "archive_only" ? "仅归档，未选择身份映射" : result.memory_materials.already_present ? "已存在，未重复写入" : "已按身份映射"]);
    if (result.memory_archive) rows.push(["独立记忆库快照", `${Number(result.memory_archive.record_count) || 0} 条`, result.memory_archive.source_complete === false ? "部分归档" : result.memory_archive.already_present ? "此前已归档" : "已保留"]);
    if (result.archive) rows.push(["原始资料归档", `${Number(result.archive.file_count) || 0} 个文件`, result.archive.complete === false ? `不完整 · 排除 ${Number(result.archive.excluded_count) || 0} 项` : result.archive.already_present ? "此前已归档" : "已保留"]);
    const errors = result.errors || [];
    return `<p class="setup-migration-detail-note">最近一次资料导入 · ${errors.length ? `${errors.length} 项未完成` : "已收到执行回执"}</p>${legacyResultTable(rows)}${errors.length ? legacyDetails("data-errors", `未完成项（${errors.length}）`, `<ul>${errors.map(item => `<li>${escape(legacyDisplayText(typeof item === "string" ? item : item.message || item.reason || item.code || "导入失败，请检查日志"))}</li>`).join("")}</ul>`, {attention: true}) : ""}`;
  }

  function legacyConfigurationCheckMarkup(findings, scope, settings, differences, audit) {
    const labels = {ready: "可转换", data_ready: "可导入数据", absent: "未发现", no_handler: "暂缺转换器", read_only: "只读保留",
      external: "由其他插件负责", held: "归属待确认", documented_skip: "按边界保留", unreadable: "无法读取"};
    const attention = findings.filter(item => ["unreadable", "held", "no_handler"].includes(item.state));
    const available = findings.filter(item => ["ready", "data_ready"].includes(item.state));
    const retained = findings.filter(item => !["unreadable", "held", "no_handler", "ready", "data_ready", "absent"].includes(item.state));
    const absent = findings.filter(item => item.state === "absent");
    const findingList = items => `<ul class="setup-migration-source-list">${items.map(item => `<li><strong>${escape(legacySources[item.source_id] || "其他旧版资料")}</strong><span>${escape(labels[item.state] || "待核对")}</span>${item.detail ? `<small>${escape(legacyDisplayText(item.detail))}</small>` : ""}</li>`).join("")}</ul>`;
    const review = window.CompanionMigrationConfigReview;
    const audits = (review?.categories || []).map(category => {
      const items = audit.map(item => ({item, description: review.describe(item)}))
        .filter(({description}) => description.outcome === category.id);
      if (!items.length) return "";
      return legacyDetails(`audit-${category.id}`, `${category.title}（${items.length}）`,
        `<p class="setup-migration-detail-note">${escape(category.note)}</p><ul class="setup-migration-field-list">${items.map(({item, description}) => {
          const editor = review.editor(description.editor);
          const explanation = legacyDisplayText(item.explain || item.summary || "暂无核实说明");
          return `<li><strong>${escape(description.title)}</strong>
            <span>${escape(description.summary || explanation)}</span>
            ${editor ? `<div class="setup-migration-field-action"><small>${escape(editor.location)}</small><button type="button" class="button small" data-setup-legacy-settings="${escape(description.editor)}" ${legacyWriting() ? "disabled" : ""}>${escape(editor.title)}</button></div>` : ""}
            ${description.summary ? `<details class="setup-migration-technical"><summary>查看原配置键与技术对照</summary><code>${escape(item.source_key || item.field || "")}</code><p>${escape(explanation)}</p></details>` : ""}</li>`;
        }).join("")}</ul>`, {attention: category.attention});
    }).join("");
    return `<div class="setup-migration-config-details">
      ${legacyResult ? `<p class="setup-recorded">${legacyConfigScopeLabel()}已写入 ${Number(legacyResult.written_count) || 0} 项当前版本设置。${legacyResult.configuration_imported ? "本次范围已记录。" : "仍有未完成项。"}${legacyResult.restart_required ? "重载插件后生效。" : ""}</p>` : ""}
      ${legacyResult?.errors?.length ? legacyDetails("configuration-errors", "未完成的配置", `<ul>${legacyResult.errors.map(item => `<li>${escape(legacyDisplayText(typeof item === "string" ? item : item.message || item.reason || item.code || "写入未完成"))}</li>`).join("")}</ul>`, {attention: true}) : ""}
      ${legacyFinding || legacyPlan ? `
      ${attention.length ? legacyDetails("source-attention", `需要处理（${attention.length}）`, findingList(attention), {attention: true}) : ""}
      ${legacyDetails("source-selected", "本次配置来源", legacyConfigurationReviewMarkup(scope, findings))}
      ${available.length ? legacyDetails("source-available", `可迁移来源（${available.length}）`, findingList(available)) : ""}
      ${retained.length || absent.length ? legacyDetails("source-retained", `保留与未发现（${retained.length + absent.length}）`, findingList([...retained, ...absent])) : ""}` : `<p class="setup-migration-empty">点击“检查旧版配置”读取来源。</p>`}
      ${(legacyPlan?.warnings || []).length ? legacyDetails("config-warnings", `试算说明（${legacyPlan.warnings.length}）`, `<ul>${legacyPlan.warnings.map(item => `<li>${escape(legacyDisplayText(item))}</li>`).join("")}</ul>`) : ""}
      ${differences.length ? legacyDetails("config-differences", `配置变化（${differences.length}）`, `<ul class="setup-migration-field-list">${differences.map(item => `<li><strong>${escape(item.field)}</strong><span>${item.before !== undefined || item.after !== undefined ? `${escape(typeof item.before === "object" ? JSON.stringify(item.before) : item.before ?? "")} → ${escape(typeof item.after === "object" ? JSON.stringify(item.after) : item.after ?? "")}` : ""}</span><small>${escape(item.explain || item.summary || "")}</small></li>`).join("")}</ul>`) : ""}
      ${audit.length ? legacyDetails("config-audit", `配置兼容说明（${audit.length}）`, audits || `<p>配置对照组件未加载，请重新打开页面查看；这些项目尚未确认迁移。</p>`) : ""}
      ${settings.length ? legacyDetails("config-values", `拟写入字段与数值（${settings.length}）`, `<ul class="setup-migration-field-list">${settings.map(item => `<li><strong>${escape(item.field)}</strong><code>${escape(typeof item.value === "string" ? item.value : JSON.stringify(item.value))}</code><small>${item.confidence === "medium" ? "中置信映射" : "高置信映射"}${item.pluginConfig ? " · 插件设置需重启生效" : ""}${item.sources.length ? ` · ${escape(item.sources.join("、"))}` : ""}</small></li>`).join("")}</ul>`) : ""}
    </div>`;
  }

  function legacyPrivateContactsReady(plan) {
    return (Number(plan?.private_contacts?.ready) || 0)
      + (Number(plan?.private_contacts?.authorization?.ready) || 0);
  }

  function legacyDataCheckMarkup() {
    const plan = legacyDataPlan;
    if (!plan) return `<p class="setup-migration-empty">${legacyDataRefreshing ? "正在刷新当前选择；上次清单保留在资料映射页。" : "尚未试算。读取资料清单后，按类型查看待导入、已存在和需映射的资料。"}</p><button type="button" class="button" data-setup-legacy-view="mapping">选择资料与映射</button>`;
    const count = value => Number(value) || 0;
    const rows = [
      ["表情素材", `${count(plan.reaction_assets?.batch_count)} 张`, `${count(plan.reaction_assets?.already_present)} 张`, `${count(plan.reaction_assets?.remaining_after_batch)} 张`],
      ["日记与梦境", `${count(plan.journals?.batch_count)} 条`, `${count(plan.journals?.already_present)} 个日期`, `${count(plan.journals?.remaining_after_batch)} 条`],
      ["旧版私聊用户", `${count(plan.private_contacts?.ready)} 位`, `${count(plan.private_contacts?.already_present)} 位`, `${plan.private_contacts?.pending?.length || 0} 位身份待识别`],
      ["身份与私聊权限", `${count(plan.private_contacts?.authorization?.ready)} 位`, `${count(plan.private_contacts?.authorization?.already_present)} 位`, `${count(plan.private_contacts?.authorization?.core_users)} 位核心用户 · ${count(plan.private_contacts?.authorization?.deferred)} 位等待私聊接续`],
      ["世界知识与熟人", `${count(plan.worldbook?.available_count)} 条`, plan.worldbook?.base_entries_matches && plan.worldbook?.base_members_matches ? "已存在" : "按来源去重", `${count(plan.worldbook?.group_count)} 条群知识需映射`],
      ["已学表达", `${count(plan.expressions?.remaining)} 条`, `${count(plan.expressions?.already_present)} 条`, `${count(plan.expressions?.sample_count)} 份样本`],
      ["参考图库", `${count(plan.reference_images?.batch_count)} 张`, `${count(plan.reference_images?.already_present)} 张`, `${count(plan.reference_images?.remaining_after_batch)} 张`],
      ["衣柜", `${count(plan.wardrobe_items?.batch_count)} 件`, `${count(plan.wardrobe_items?.already_present)} 件`, `${count(plan.wardrobe_items?.remaining_after_batch)} 件`],
      ["历史记忆", `${count(plan.memory_database?.projection?.imported?.memories)} 条`, "按来源去重", "先确认私聊 / 群身份"],
      ["原始资料归档", `${count(plan.archive?.file_count)} 个文件`, "按文件校验", plan.archive?.state === "incomplete" ? "归档不完整" : "仅保留，不参与运行"],
    ];
    return `<p class="setup-migration-detail-note">当前试算 · ${escape(legacyPersonaLabel((plan.persona_sources || []).find(item => item.persona_id === legacyDataPersonaId), "主角色"))}</p>${legacyResultTable(rows, ["资料类型", "本批可导入", "已存在", "后续处理"])}${legacyMemoryPlanDirty ? `<p class="setup-migration-pending">身份选择已变化，需重新试算。</p>` : ""}`;
  }

  function legacyImportBatchSummary() {
    const plan = legacyDataPlan;
    if (!plan || !legacyDataCanApply) return "";
    const count = value => Number(value) || 0;
    const rows = [];
    if (legacyImportReactions && count(plan.reaction_assets?.batch_count)) rows.push(["表情素材", `${count(plan.reaction_assets.batch_count)} 张`]);
    if (legacyImportPrivateContacts && legacyPrivateContactsReady(plan)) rows.push(["私聊用户与权限", `${count(plan.private_contacts.ready)} 位用户 · ${count(plan.private_contacts.authorization?.ready)} 位权限`]);
    if (legacyImportWorldbook && count(plan.worldbook?.available_count)) rows.push(["世界书与关系", `${count(plan.worldbook.available_count)} 条`]);
    if (legacyImportExpressions && count(plan.expressions?.remaining)) rows.push(["已学表达", `${count(plan.expressions.remaining)} 条 / 样本 ${count(plan.expressions.sample_count)} 份`]);
    if (legacyImportJournals && count(plan.journals?.batch_count)) rows.push(["日记与梦境", `${count(plan.journals.batch_count)} 条`]);
    if (legacyImportReferenceImages && count(plan.reference_images?.batch_count)) rows.push(["参考图库", `${count(plan.reference_images.batch_count)} 张`]);
    if (legacyImportWardrobeItems && count(plan.wardrobe_items?.batch_count)) rows.push(["衣柜", `${count(plan.wardrobe_items.batch_count)} 件`]);
    if (legacyImportMemoryDatabase && legacyMemoryArchiveCanApply(plan.memory_database)) rows.push(["独立记忆库", `${count(plan.memory_database?.record_count)} 条记录`]);
    if (legacyImportArchive && legacyArchiveCanApply(plan.archive)) rows.push(["原始资料归档", `${count(plan.archive?.file_count)} 个文件`]);
    if (!rows.length) return "";
    return `<section class="setup-migration-batch" aria-label="本批写入范围">
      <div class="setup-migration-batch-head"><h5>本批将写入</h5><span>按来源标识去重 · 完成后重新读取剩余清单</span></div>
      <ul>${rows.map(([label, value]) => `<li><span>${escape(label)}</span><strong>${escape(value)}</strong></li>`).join("")}</ul>
    </section>`;
  }

  function legacyImportStepMarkup() {
    const pending = legacyDataCanApply
      ? (legacyDataResult ? "上一批已有回执；确认后继续导入剩余批次。" : "已完成试算，可以逐项导入当前选择的资料。")
      : legacyDataPlan?.plan_token ? "当前清单没有满足条件的逐项导入批次，可以查看结果。" : "先在「逐项迁移」里完成资料试算和归属确认。";
    return `<section class="setup-migration-import-step setup-migration-wizard-panel">
      <header><div><h4>逐项写入本批资料</h4><p>${escape(pending)}</p></div><span class="setup-migration-status">${legacyDataCanApply ? "可以导入" : "无待导入批次"}</span></header>
      ${legacyImportBatchSummary()}
      ${legacyProgressMarkup()}
      ${legacyDataErrorMarkup()}
      ${legacyDataResult ? legacyDataResultMarkup() : ""}
      <div class="setup-migration-final-action"><header><div><h4>${legacyDataCanApply ? "确认写入当前批次" : "本次无需写入"}</h4><p>${legacyDataCanApply ? "重复内容按来源标识去重；导入完成后会重新读取剩余清单。" : "可直接进入结果页，仍需处理的内容会保留在迁移回执中。"}</p></div></header><div class="setup-actions">${legacyButton("legacy-data-apply", legacyDataResult ? "继续导入下一批" : "导入所选资料", {primary: true, disabled: !legacyDataCanApply})}</div></div>
    </section>`;
  }

  function stopLegacySettings() {
    legacySettingsEditor?.stop?.();
    for (const instance of legacySettingsEditor?.state?.instances || []) instance.unmount?.();
    legacySettingsEditor = null;
    if (window.CompanionMigrationConfigReview?.editor(legacySettingsKey)?.mount === "models") {
      window.CompanionModelsPage?.unmount?.();
    }
  }

  function legacySettingsMarkup() {
    const editor = window.CompanionMigrationConfigReview?.editor(legacySettingsKey);
    const selection = legacySelection();
    return `<section class="setup-migration-settings" aria-label="迁移配置调整">
      <header><div><h3>${escape(editor.title)}</h3><p>${escape(editor.location)} · ${editor.scope === "installation" ? "全局设置" : escape(legacyRoleLabel(selection.role, 0))}</p></div>
        <button type="button" class="button" data-setup-legacy-settings-back>返回迁移对照</button></header>
      <p id="setupLegacySettingsNotice" class="setup-migration-detail-note" role="status">在下方保存设置后返回，迁移选择会保留。返回时会重新试算，更新尚未写入的配置清单。</p>
      <div id="setupLegacySettingsHost"></div>
    </section>`;
  }

  function mountLegacySettings() {
    const editor = window.CompanionMigrationConfigReview?.editor(legacySettingsKey);
    const host = root?.querySelector("#setupLegacySettingsHost");
    const role = legacySelection().role;
    if (!editor || !host || !role) return;
    if (editor.mount === "models" && role.model_api && window.CompanionModelsPage?.mount) {
      window.CompanionModelsPage.mount(host, {endpoint: role.model_api});
      return;
    }
    // Role settings use the destination selected in the guide, not the shell's
    // current conversation. Installation settings keep their existing owner.
    if (editor.kinds && (editor.scope === "installation" || role.config_api)) {
      legacySettingsEditor = window.CompanionConfigEditors?.create?.(editor.kinds,
        () => root?.querySelector("#setupLegacySettingsHost"), editor.views || {}, editor.scope === "installation" ? "" : role.config_api);
    }
    if (legacySettingsEditor) {
      legacySettingsEditor.restore?.();
      legacySettingsEditor.paint?.();
      void legacySettingsEditor.read?.();
    } else {
      host.innerHTML = `<p class="setup-inline-error" role="alert">当前角色的设置入口尚不可用。返回迁移对照后重新读取账号与角色，再打开设置。</p>`;
    }
  }

  function legacyPanel() {
    const selection = legacySelection();
    configurationReview()?.update(legacyPlan);
    const findings = legacyPlan?.findings ?? legacyFinding?.findings ?? [];
    const sourceReviewScope = legacyPlan?.configuration_scope
      || legacyFinding?.configuration_scope || legacyConfigScope;
    const planScopes = legacyScopeStatus();
    const blocked = planScopes.length > 0 && !planScopes.some(item => item.available);
    const configurationBlockers = legacyConfigurationBlockers(legacyConfigScope, findings);
    const plannedSettings = (legacyPlan?.intents || []).flatMap(intent =>
      Object.entries(intent.values || {}).map(([field, value]) => ({
        field: intent.target_kind === "plugin_config" ? field : `${intent.group_id}.${field}`, value,
        confidence: intent.confidence?.[field] || "high",
        sources: intent.sources?.[field] || [],
        pluginConfig: intent.target_kind === "plugin_config",
      })));
    const auditDifferences = (legacyPlan?.differences || legacyResult?.differences || []).filter(item => item.audit);
    const reviewDifferences = (legacyPlan?.differences || legacyResult?.differences || []).filter(item => !item.audit);
    const canApply = Boolean(legacyPlan?.plan_token && legacyPlan.change_count > 0
      && planScopes.some(item => item.available) && !configurationBlockers.length);
    const inventory = legacyDataPlan || legacyDataInventory;
    const reactionSummary = inventory?.reaction_assets || {};
    const journalSummary = inventory?.journals || {};
    const expressionSummary = inventory?.expressions || {};
    const worldbookSummary = inventory?.worldbook || {};
    const referenceSummary = inventory?.reference_images || {};
    const wardrobeSummary = inventory?.wardrobe_items || {};
    const profileSummary = legacyDataPlan?.profile_data || {};
    const contactSummary = inventory?.private_contacts || {};
    const workSummary = legacyDataPlan?.work_delivery || {};
    const worldSummary = legacyDataPlan?.world_assets || {};
    const memoryDatabase = inventory?.memory_database || {};
    const memoryTables = Object.entries(memoryDatabase.table_counts || {});
    const memoryProjection = memoryDatabase.projection || {};
    const memoryInventory = memoryDatabase.identity_inventory || {};
    const memoryOwners = memoryInventory.owners || [];
    const privateMemoryOwners = memoryOwners.filter(item => item.source?.scope === "private");
    const groupMemoryOwners = memoryOwners.filter(item => item.source?.scope === "group");
    const memoryGroupTargets = memoryDatabase.group_targets || [];
    const memoryArchiveOnly = memoryDatabase.archive_only || {};
    const memoryArchiveAvailable = legacyMemoryArchiveCanApply(memoryDatabase);
    const sourceArchiveAvailable = legacyArchiveCanApply(inventory?.archive);
    const privateTargetAvailable = Boolean(inventory?.target_private_available);
    const referencesReady = Boolean(legacyImportReferenceImages
      && Number(referenceSummary.batch_count) > 0 && privateTargetAvailable);
    const wardrobeReady = Boolean(legacyImportWardrobeItems
      && Number(wardrobeSummary.batch_count) > 0 && privateTargetAvailable);
    const networkGroupOptions = inventory?.group_networks || [];
    const worldbookGroupOptions = inventory?.worldbook?.groups || [];
    const groupOptionMap = new Map(networkGroupOptions.map(item => [item.source_group_id, {...item,
      worldbook_entry_count: Number(worldbookGroupOptions.find(group => group.source_group_id === item.source_group_id)?.entry_count) || 0}]));
    for (const item of worldbookGroupOptions) {
      if (groupOptionMap.has(item.source_group_id)) continue;
      groupOptionMap.set(item.source_group_id, {source_group_id: item.source_group_id,
        member_count: 0, edge_count: 0, worldbook_entry_count: Number(item.entry_count) || 0});
    }
    for (const item of inventory?.worldbook?.member_profile_groups || []) {
      const existing = groupOptionMap.get(item.source_group_id);
      if (existing) existing.member_profile_count = Number(item.member_count) || 0;
      else groupOptionMap.set(item.source_group_id, {...item, member_count: 0, edge_count: 0,
        worldbook_entry_count: 0, member_profile_count: Number(item.member_count) || 0});
    }
    const groupOptions = [...groupOptionMap.values()];
    const personaOptions = inventory?.persona_sources || [];
    const selectedGroup = groupOptions.find(item => item.source_group_id === legacyDataGroupId) || null;
    const selectedNetworkGroup = networkGroupOptions.find(item => item.source_group_id === legacyDataGroupId) || null;
    const selectedWorldbookGroup = worldbookGroupOptions.find(item => item.source_group_id === legacyDataGroupId) || null;
    const selectedPersona = personaOptions.find(item => item.persona_id === legacyDataPersonaId) || null;
    const worldbookImportableCount = Number(worldbookSummary.available_count) || 0;
    const worldbookCanApply = Boolean(legacyImportWorldbook
      && ((Number(worldbookSummary.general_count) > 0 && !worldbookSummary.base_entries_matches)
        || (legacyConfirmMemberIds && Number(worldbookSummary.member_count) > 0
          && !worldbookSummary.base_members_matches)
        || (legacyConfirmMemberIds && legacyDataPlan?.group_target_available
          && Number(worldbookSummary.selected_group_entry_count) + Number(worldbookSummary.selected_group_member_count || 0) > 0
          && !worldbookSummary.group_matches)));
    const pendingConfirmations = [];
    if (legacyImportMemoryDatabase && (legacyMemoryOwnerIds.size || legacyMemoryGroupMappings.size) && !legacyConfirmMemoryMapping) pendingConfirmations.push("记忆身份映射");
    if ((legacyImportGroupNetwork && selectedNetworkGroup || legacyImportWorldbook && worldbookSummary.requires_id_confirmation) && !legacyConfirmMemberIds) pendingConfirmations.push("成员账号映射");
    if (legacyImportJournals && Number(journalSummary.batch_count) > 0 && !legacyConfirmPersonaMapping) pendingConfirmations.push("日记所属角色");
    if ((referencesReady || wardrobeReady) && !legacyConfirmReferencePersonaMapping) pendingConfirmations.push("角色图库归属");
    if (legacyImportExpressions && expressionSummary.remaining > 0 && !legacyConfirmExpressionMapping) pendingConfirmations.push("表达所属角色");
    legacyPendingConfirmations = pendingConfirmations;
    const canApplyData = Boolean(legacyDataPlan?.plan_token
      && !legacyMemoryPlanDirty
      && !pendingConfirmations.length
      && ((legacyImportReactions && Number(reactionSummary.batch_count) > 0)
        || (legacyImportArchive && sourceArchiveAvailable)
        || (legacyImportMemoryDatabase && memoryArchiveAvailable
          && (!legacyMemoryOwnerIds.size || legacyDataPlan.target_private_available)
          )
        || (legacyImportGroupNetwork && selectedNetworkGroup && legacyDataPlan.group_target_available
          && legacyConfirmMemberIds)
        || (legacyImportJournals && Number(journalSummary.batch_count) > 0
          && legacyDataPlan.target_private_available && legacyConfirmPersonaMapping)
        || ((referencesReady || wardrobeReady) && legacyConfirmReferencePersonaMapping)
        || (legacyImportExpressions && expressionSummary.available && expressionSummary.remaining > 0 && legacyConfirmExpressionMapping)
        || (legacyImportPrivateContacts && legacyPrivateContactsReady(legacyDataPlan) > 0 && legacyDataPlan.target_private_available)
        || worldbookCanApply));
    legacyDataCanApply = canApplyData;
    const dataReasons = {target_image_limit: "超过当前版本单张图片限制", unsupported_image_type: "当前版本不支持此图片格式",
      invalid_filename: "文件名不安全", image_missing: "素材文件缺失", image_path_outside_library: "文件位于素材目录之外",
      catalog_hash_mismatch: "目录哈希与文件不一致", asset_count_limit: "超过单次扫描条数",
      remote_url_not_downloaded: "远程图片 URL 不会在迁移时下载",
      identity_scope_unmapped: "素材绑定了具体用户或群，无法映射为角色级授权",
      image_access_not_role_wide: "授权范围不满足角色图库迁移条件",
      reference_disabled: "旧版中已停用的参考图不恢复", image_changed_after_preview: "试算后源图发生变化，请重新试算",
      entry_invalid: "世界书条目格式无法识别", entry_content_missing: "条目没有可导入正文",
      entry_too_large: "条目正文超过单条限制", member_scope_missing: "成员条目没有账号范围",
      member_scope_unmapped: "成员账号范围无法确认", group_scope_missing: "群条目没有群范围",
      group_scope_invalid: "群范围无法识别", unsupported_scoped_template: "此模板的范围语义未映射",
      worldbook_batch_bytes_limit: "超过世界书单批容量", worldbook_count_limit: "超过单次扫描条数",
      member_profile_preferred_or_deleted: "使用成员档案中的最新关系资料；已删除的节点保持删除"};
    const unavailableReason = summary => !inventory ? "先读取资料清单。"
      : legacyDataRefreshing ? "正在刷新，数量暂按上次清单显示。"
      : Number(summary.already_present) > 0 ? "可识别的资料已存在，没有待导入内容。" : "此来源未发现可导入内容。";
    const confirmations = [];
    if (legacyImportMemoryDatabase && (legacyMemoryOwnerIds.size || legacyMemoryGroupMappings.size)) {
      confirmations.push(legacyConfirmMarkup("confirm-memory-mapping",
        "确认所选旧私聊与群身份属于对应当前版本目标；群历史只映射到同号群，不新增或扩大读取权限",
        legacyConfirmMemoryMapping, !legacyImportMemoryDatabase));
    }
    if ((legacyImportGroupNetwork && selectedNetworkGroup
      || legacyImportWorldbook && worldbookSummary.requires_id_confirmation)) {
      confirmations.push(legacyConfirmMarkup("confirm-member-ids",
        "确认旧版与当前版本目标使用相同的平台身份，成员账号 ID 一致；所选群范围映射到当前版本群",
        legacyConfirmMemberIds));
    }
    if (legacyImportJournals && legacyDataPlan?.target_private_available) {
      confirmations.push(legacyConfirmMarkup("confirm-persona-mapping",
        `确认将 ${legacyPersonaLabel(selectedPersona, "主角色（companions.json）")} 的私人日记与梦境归入当前版本目标和人格`,
        legacyConfirmPersonaMapping));
    }
    if (referencesReady || wardrobeReady) {
      confirmations.push(legacyConfirmMarkup("confirm-reference-persona-mapping",
        `确认将 ${legacyPersonaLabel(selectedPersona, "主角色（companions.json）")} 的参考图与衣柜归入当前版本角色图库，图片随角色使用，不按旧版私聊用户区分；图片不可用时仍保留衣物文字资料`,
        legacyConfirmReferencePersonaMapping));
    }
    if (legacyImportExpressions && expressionSummary.available) {
      confirmations.push(legacyConfirmMarkup("confirm-expression-mapping",
        "确认旧版表达归入所选角色及同一私聊对象或群；保留原审核状态", legacyConfirmExpressionMapping));
    }
    const confirmationsMarkup = confirmations.length ? `<fieldset class="setup-legacy-confirmations"><legend>写入前确认</legend><p>只有与当前版本目标明确对应的资料才会写入；未确认的内容继续保留在隔离归档中。</p>${confirmations.join("")}</fieldset>` : "";
    const manualPanels = `      <div class="setup-legacy-data setup-migration-manual">
        <header class="setup-migration-section-head"><div><h4>逐项选择资料范围</h4><p>按旧版人格选择角色资料；用户、群和归档可以单独选择。默认的一键迁移不需要这一步。</p></div>
          ${legacyButton("legacy-data-plan", inventory ? "刷新资料清单" : "读取资料清单", {disabled: !selection.api})}</header>
        ${legacyDataErrorMarkup()}
        ${!inventory ? `<p class="setup-migration-empty">读取清单后选择旧版人格、资料类型和对应目标。</p>` : ""}
        ${legacyDataRefreshing ? `<p class="setup-migration-refresh" role="status">正在刷新当前选择，可继续调整资料范围与映射。${inventory ? "数量暂按上次清单显示。" : ""}</p>` : ""}
        <div class="setup-migration-manual-controls">
        <fieldset class="setup-legacy-data-controls" ${legacyWriting() ? "disabled" : ""}>
          <div class="setup-legacy-data-controls-head"><strong>选择资料来源</strong><span>先选旧版人格；群资料还需要选择旧版群记录。</span></div>
          <label class="setup-legacy-path"><span>旧版人格档案</span><select data-setup-legacy="data-persona" ${personaOptions.length ? "" : "disabled"}><option value="">主角色（companions.json）</option>${personaOptions.filter(item => item.persona_id).map(item => `<option value="${escape(item.persona_id)}" ${legacyDataPersonaId === item.persona_id ? "selected" : ""} ${item.state === "ready" ? "" : "disabled"}>${escape(legacyPersonaLabel(item))}${item.state === "ready" ? "" : " · 不可读取"}</option>`).join("")}</select></label>

          <label class="setup-legacy-path"><span>旧版群记录 / 范围</span><select data-setup-legacy="data-group" ${groupOptions.length ? "" : "disabled"}><option value="">不选择群</option>${groupOptions.map(item => `<option value="${escape(item.source_group_id)}" ${legacyDataGroupId === item.source_group_id ? "selected" : ""}>群 ${escape(item.source_group_id)} · ${Number(item.member_count) || 0} 位成员 / ${Number(item.edge_count) || 0} 条边 / ${Number(item.worldbook_entry_count) || 0} 条知识 / ${Number(item.member_profile_count) || 0} 份成员资料</option>`).join("")}</select></label>
          ${selectedGroup && legacyDataPlan ? `<p class="setup-legacy-scope-note">${selectedNetworkGroup ? `群成员互动图将从旧版群 ${escape(selectedGroup.source_group_id)} 映射到当前版本群 ${escape(legacyDataPlan.target_group_id || "（当前目标不是群）")}，含 ${Number(selectedGroup.member_count) || 0} 位成员和 ${Number(selectedGroup.edge_count) || 0} 条互动边。${Number(selectedGroup.omitted_member_count) || Number(selectedGroup.omitted_edge_count) ? `另有 ${Number(selectedGroup.omitted_member_count) || 0} 位成员、${Number(selectedGroup.omitted_edge_count) || 0} 条互动边超过当前版本上限，不会导入。` : ""}${Number(selectedGroup.rejected_member_count) || Number(selectedGroup.rejected_edge_count) ? `无法识别 ${Number(selectedGroup.rejected_member_count) || 0} 位成员、${Number(selectedGroup.rejected_edge_count) || 0} 条互动边。` : ""}` : ""}${selectedWorldbookGroup ? `所选旧版群还有 ${Number(selectedWorldbookGroup.entry_count) || 0} 条群范围知识条目。` : ""}</p>` : ""}
        </fieldset>
        </div>
        <div class="setup-migration-subnav">${legacyTabs([["current", "当前角色资料"], ["personas", "多角色映射"], ["groups", "群资料映射"], ["memory", "记忆身份映射"]], legacyMappingView, "setup-legacy-mapping-view", "资料映射分区")}</div>
        <fieldset class="setup-migration-mapping-panel" ${legacyWriting() ? "disabled" : ""} ${legacyMappingView === "current" ? "" : "hidden"}>
        <fieldset class="setup-legacy-option-groups">
          <legend>导入与保留范围</legend>
          <section class="setup-legacy-option-group"><header><strong>直接导入当前版本</strong><span>会写入当前已选目标；重复内容按来源标识去重。</span></header><div class="setup-legacy-data-checks">
            ${legacyDataChoiceMarkup("import-reactions", `表情素材（${Number(reactionSummary.batch_count) || 0} 张）`, {checked: legacyImportReactions, disabled: !Number(reactionSummary.batch_count), disabledReason: unavailableReason(reactionSummary), detail: "角色共用素材"})}
            ${legacyDataChoiceMarkup("import-group-network", "所选群成员互动图", {checked: legacyImportGroupNetwork, disabled: !(selectedNetworkGroup && inventory?.group_target_available), disabledReason: !selectedNetworkGroup ? "先选择含互动图的旧版群记录。" : "到「群资料映射」选择对应群目标。", detail: "按所选群和角色导入"})}
            ${legacyDataChoiceMarkup("import-private-contacts", `私聊用户与权限（${Number(contactSummary.ready) || 0} 位用户 · ${Number(contactSummary.authorization?.ready) || 0} 位权限待迁入）`, {checked: legacyImportPrivateContacts, disabled: !legacyPrivateContactsReady(inventory) || !inventory?.target_private_available, disabledReason: Number(contactSummary.already_present) ? "这些用户已经迁入。" : "当前角色与账号下没有待迁入的旧版私聊用户。", detail: `手动维护 ${Number(contactSummary.maintained_count) || 0} 位；保留称呼与私聊往来，并接续已确认的核心身份、私聊回复和主动联系权限。`})}
            ${legacyDataChoiceMarkup("import-worldbook", "世界书条目", {checked: legacyImportWorldbook, disabled: !worldbookImportableCount, disabledReason: unavailableReason(worldbookSummary), detail: "人物、关系和群范围知识按可映射范围导入。"})}
            ${legacyDataChoiceMarkup("import-expressions", `已学表达与样本（${Number(expressionSummary.rule_count) || 0} 条规则 / ${Number(expressionSummary.sample_count) || 0} 份样本）`, {checked: legacyImportExpressions, disabled: !expressionSummary.available || !expressionSummary.remaining, disabledReason: unavailableReason(expressionSummary), detail: `已采用 ${Number(expressionSummary.adopted_count) || 0} · 待审 ${Number(expressionSummary.pending_count) || 0} · 驳回或停用 ${Number(expressionSummary.rejected_count) || 0}；按原状态接续。`})}
            ${legacyDataChoiceMarkup("import-journals", `该人格的日记与梦境（${Number(journalSummary.batch_count) || 0} 条）`, {checked: legacyImportJournals, disabled: !Number(journalSummary.batch_count) || !privateTargetAvailable, disabledReason: !privateTargetAvailable && inventory ? "需在上方选择该角色对应的私聊目标。" : unavailableReason(journalSummary), detail: "需对应私聊目标"})}
            ${legacyDataChoiceMarkup("import-reference-images", `参考图库 ${Number(referenceSummary.batch_count) || 0} 张`, {checked: legacyImportReferenceImages, disabled: !(Number(referenceSummary.batch_count) > 0 && privateTargetAvailable), disabledReason: !privateTargetAvailable && inventory ? "需先选择该角色对应的私聊目标。" : unavailableReason(referenceSummary), detail: "随角色使用，迁移时校验本地图片"})}
            ${legacyDataChoiceMarkup("import-wardrobe-items", `衣柜 ${Number(wardrobeSummary.batch_count) || 0} 件（图片 ${Number(wardrobeSummary.image_count) || 0} 张）`, {checked: legacyImportWardrobeItems, disabled: !(Number(wardrobeSummary.batch_count) > 0 && privateTargetAvailable), disabledReason: !privateTargetAvailable && inventory ? "需先选择该角色对应的私聊目标。" : unavailableReason(wardrobeSummary), detail: "图片不可用时保留衣物文字资料。"})}
          </div></section>
          <section class="setup-legacy-option-group"><header><strong>素材与原始资料保留</strong><span>保留在隔离归档，方便复核；不会直接提供给对话或旧任务执行。</span></header><div class="setup-legacy-data-checks">
            ${legacyDataChoiceMarkup("import-memory-database", `独立记忆库（${inventory ? `${Number(memoryDatabase.record_count) || 0} 条 / ${Math.ceil((Number(memoryDatabase.total_bytes) || 0) / 1048576)} MB` : "试算后显示规模"}）`, {checked: legacyImportMemoryDatabase && memoryArchiveAvailable, disabled: !memoryArchiveAvailable, disabledReason: unavailableReason(memoryDatabase), detail: "先在「记忆身份映射」核对归属"})}
            ${legacyDataChoiceMarkup("import-archive", `${inventory?.archive?.state === "incomplete" ? "尽可能归档可读的原始资料" : "完整保留其余原始资料"}（${Math.ceil((Number(inventory?.archive?.total_bytes) || 0) / 1048576)} MB）`, {checked: legacyImportArchive && sourceArchiveAvailable, disabled: !sourceArchiveAvailable, disabledReason: unavailableReason(inventory?.archive || {}), detail: "保留原文件，供后续核对"})}
          </div></section>
        </fieldset>

        ${confirmationsMarkup}
        ${legacyDataPlan && !legacyDataPlan.group_target_available && selectedGroup ? `<p class="setup-legacy-scope-note">当前版本目标不是群会话；切换到对应群目标后才能导入群成员互动图。</p>` : ""}
        ${legacyDataPlan && !legacyDataPlan.target_private_available ? `<p class="setup-legacy-scope-note">日记与梦境只能导入到私聊目标，请切换到当前角色的当前版本私聊目标。</p>` : ""}
        <div class="setup-actions">
          ${legacyButton("legacy-data-plan", legacyDataPlan ? "重新试算所选资料" : "试算所选资料", {primary: !legacyDataPlan, disabled: !selection.api})}
          <span class="setup-migration-inline-hint">范围确认后，下一步会复核身份映射并预览导入结果。</span>
        </div>
        ${!canApplyData ? `<p class="setup-legacy-scope-note" role="status">${!legacyDataPlan ? "先读取资料清单。" : legacyMemoryPlanDirty ? "身份已调整，请重新试算。" : pendingConfirmations.length ? `尚需确认：${escape(pendingConfirmations.join("、"))}。` : "选择至少一项可导入资料。"}</p>` : ""}
      </fieldset>
      <fieldset class="setup-migration-mapping-panel" ${legacyWriting() ? "disabled" : ""} ${legacyMappingView === "personas" ? "" : "hidden"}>
        ${inventory ? renderLegacyPersonaBatch(personaOptions) : `<p class="setup-migration-empty">先读取资料清单。</p>`}
      </fieldset>
      <fieldset class="setup-migration-mapping-panel" ${legacyWriting() ? "disabled" : ""} ${legacyMappingView === "groups" ? "" : "hidden"}>
        ${inventory && legacyDataGroupId ? renderLegacyGroupMapping() : `<p class="setup-migration-empty">先在上方选择旧版群记录，再映射到当前版本群与角色。</p>`}
      </fieldset>
      <fieldset class="setup-migration-mapping-panel" ${legacyWriting() ? "disabled" : ""} ${legacyMappingView === "memory" ? "" : "hidden"}>
        <h5>独立记忆库身份</h5><p class="setup-legacy-scope-note">${inventory ? "仅迁入已映射身份的历史资料，未映射内容保留归档。" : "先读取资料清单。"}</p>
        ${legacyDataChoiceMarkup("import-memory-database", "导入独立记忆库", {checked: legacyImportMemoryDatabase && memoryArchiveAvailable, disabled: !memoryArchiveAvailable})}
        ${privateMemoryOwners.length ? `<details class="setup-legacy-values" data-migration-detail="memory-private" ${legacyOpenDetails.has("memory-private") ? "open" : ""}><summary>旧私聊身份映射（${legacyMemoryOwnerIds.size} / ${privateMemoryOwners.length}）</summary><p>仅迁入已映射的合规私聊历史；其余保留在隔离归档。</p><ul>${privateMemoryOwners.map(item => {
          const source = item.source || {};
          const label = `${source.platform || "未知平台"} · ${source.bot_id || "未知机器人"} · ${source.persona_id || "未知人格"} · ${source.session_id || "未知会话"} · ${source.actor_id || "未知用户"}`;
          const selected = legacyMemoryOwnerIds.has(item.owner_id);
          const atLimit = !selected && legacyMemoryOwnerIds.size >= MAX_LEGACY_MEMORY_OWNER_SELECTION;
          return `<li><label><input type="checkbox" data-setup-legacy="memory-owner" data-owner-id="${escape(item.owner_id)}" ${selected ? "checked" : ""} ${privateTargetAvailable && legacyImportMemoryDatabase && !atLimit ? "" : "disabled"}><strong>${escape(label)}</strong><small>${Number(item.memories) || 0} 条记忆 / ${Number(item.timeline) || 0} 条时间线</small></label></li>`;
        }).join("")}</ul></details>` : ""}
        ${groupMemoryOwners.length ? `<details class="setup-legacy-values" data-migration-detail="memory-group" ${legacyOpenDetails.has("memory-group") ? "open" : ""}><summary>旧群身份映射（${legacyMemoryGroupMappings.size} / ${groupMemoryOwners.length}）</summary><p>群号需与目标一致；平台和角色按目标隔离，导入不会新增读取权限。</p><ul>${groupMemoryOwners.map(item => {
          const source = item.source || {};
          const label = `${source.platform || "未知平台"} · ${source.bot_id || "未知机器人"} · ${source.persona_id || "未知人格"} · 群 ${source.group_id || "未知群号"}`;
          const selected = legacyMemoryGroupMappings.get(item.owner_id) || null;
          const targets = memoryGroupTargets.filter(target => target.group_id === source.group_id);
          const options = targets.flatMap(target => (target.roles || []).map(role => ({
            target_id: target.target_id, role_id: role.role_id,
            label: `${target.platform_id} · ${target.account_id} · ${target.bot_id} · ${role.role_name || role.role_id}`,
          })));
          return `<li><label><strong>${escape(label)}</strong><select data-setup-legacy="memory-group-target" data-owner-id="${escape(item.owner_id)}" ${legacyImportMemoryDatabase && options.length ? "" : "disabled"}><option value="">保留在隔离归档</option>${options.map(option => {
            const value = JSON.stringify({target_id: option.target_id, role_id: option.role_id});
            const selectedValue = selected ? JSON.stringify(selected) : "";
            return `<option value="${escape(value)}" ${value === selectedValue ? "selected" : ""}>${escape(option.label)}</option>`;
          }).join("")}</select><small>${Number(item.memories) || 0} 条记忆 / ${Number(item.timeline) || 0} 条时间线</small></label></li>`;
        }).join("")}</ul></details>` : ""}
        ${legacyMemoryPlanDirty ? `<p class="setup-migration-pending">身份选择已更改；请重新试算后再执行导入。</p>` : ""}
        ${legacyPrivateMemoryPanel(selection)}
        <div class="setup-actions">${legacyButton("legacy-data-plan", "重新试算身份映射", {disabled: !selection.api})}<button type="button" class="button" data-setup-legacy-mapping-view="current">返回范围确认与导入</button></div>
      </fieldset>
      <div class="setup-migration-data-review">
      ${legacyDataPlan ? legacyDetails("data-inventory", "资料明细与保留项目", `<div class="setup-legacy-plan setup-legacy-data-plan">
          <p class="setup-legacy-plan-lead"><strong>本次资料试算</strong><span>表情素材 ${Number(reactionSummary.ready) || 0} 张待导入，已存在 ${Number(reactionSummary.already_present) || 0} 张；本批 ${Number(reactionSummary.batch_count) || 0} 张 / ${Math.ceil((Number(reactionSummary.batch_bytes) || 0) / 1048576)} MB${Number(reactionSummary.remaining_after_batch) ? `，后续还需 ${Number(reactionSummary.remaining_after_batch)} 张` : ""}。</span></p>
          <p>日记与梦境：${Number(journalSummary.ready) || 0} 条待导入，${Number(journalSummary.already_present) || 0} 个日期已有当前版本记录会保留；本批 ${Number(journalSummary.batch_count) || 0} 条 / ${Math.ceil((Number(journalSummary.batch_bytes) || 0) / 1024)} KB${Number(journalSummary.remaining_after_batch) ? `，后续还需 ${Number(journalSummary.remaining_after_batch)} 条` : ""}。预览只显示日期和大小，不显示正文。</p>
          ${journalSummary.samples?.length ? `<p>本批日期：${journalSummary.samples.slice(0, 12).map(item => `${escape(item.date)}（${escape(item.kind === "dream" ? "梦境" : "日记")}，${Number(item.size) || 0} 字节）`).join("、")}</p>` : ""}
          ${journalSummary.conflicts?.length ? `<p>已有日期：${journalSummary.conflicts.slice(0, 12).map(item => `${escape(item.date)}（${escape(item.kind === "dream" ? "梦境" : "日记")}，${item.content_matches ? "正文与旧版一致" : item.imported ? "此前由迁移导入" : `当前版本已有 ${escape(item.state)} 记录，保留不覆盖`}）`).join("、")}</p>` : ""}
          <p>世界书条目：${Number(worldbookSummary.general_count) || 0} 条普通条目、${Number(worldbookSummary.member_count) || 0} 个学习关系网节点、${Number(worldbookSummary.group_count) || 0} 条群范围条目；当前目标可导入 ${worldbookImportableCount} 条。${Number(worldbookSummary.disabled_member_count) ? `另有 ${Number(worldbookSummary.disabled_member_count)} 条已禁用成员条目，当前版本无法保留其禁用状态，已跳过。` : ""}人物描述、身份备注、相处边界与重要印象一起迁移。同一账号合并为一个节点；群范围按所选旧版群单独映射，预览不显示正文。</p>
          <p>参考图库：${Number(referenceSummary.ready) || 0} 张可导入，${Number(referenceSummary.already_present) || 0} 张已存在；本批 ${Number(referenceSummary.batch_count) || 0} 张。衣柜：${Number(wardrobeSummary.ready) || 0} 件可导入，图片 ${Number(wardrobeSummary.image_count) || 0} 张；本批 ${Number(wardrobeSummary.batch_count) || 0} 件。源文件会复核 SHA-256；URL 不下载，无法安全映射的素材会跳过。</p>
          ${(referenceSummary.rejected?.length || wardrobeSummary.rejected?.length) ? `<details class="setup-legacy-values"><summary>参考图与衣柜跳过项（${(referenceSummary.rejected?.length || 0) + (wardrobeSummary.rejected?.length || 0)}）</summary><ul>${[...(referenceSummary.rejected || []), ...(wardrobeSummary.rejected || [])].map(item => `<li><strong>${escape(item.filename || item.source_id || "衣柜条目")}</strong><small>${escape(dataReasons[item.reason] || item.reason || "无法迁移")}</small></li>`).join("")}</ul></details>` : ""}
          <p>旧版私聊用户：${Number(contactSummary.ready) || 0} 位待迁入、${Number(contactSummary.already_present) || 0} 位已存在。${contactSummary.pending?.length ? `${contactSummary.pending.length} 位尚未识别到同账号身份，先保留原资料。` : ""}旧关系分数和待执行任务不恢复。${Number(profileSummary.dream_fragments) || 0} 个梦境碎片保留在原始资料中。</p>
          ${(contactSummary.samples || []).length ? legacyDetails("private-contacts", "旧版私聊用户名单", `<ul>${contactSummary.samples.map(item => `<li>${escape(item.nickname || item.native_account_id)} · ${escape(item.native_account_id)} · ${item.maintained ? "手动维护" : "私聊往来"} · ${Number(item.message_count) || 0} 条历史消息</li>`).join("")}</ul>`) : ""}
          ${(contactSummary.pending || []).length ? legacyDetails("private-contacts-pending", `身份待识别（${contactSummary.pending.length} 位）`, `<p>这些旧账号尚未出现在当前 Bot 的身份目录中。保留原资料，识别到同账号私聊身份后可重新试算迁入。</p><ul>${contactSummary.pending.map(item => `<li>QQ ${escape(item.native_account_id)}</li>`).join("")}</ul>`) : ""}
          <p>旧任务 / 投递：${escape(workSummary.work?.state || "未发现")}，${Object.values(workSummary.work?.tables || {}).reduce((sum, value) => sum + (Number(value) || 0), 0)} 条任务库记录；${escape(workSummary.delivery?.state || "未发现")}，${Object.values(workSummary.delivery?.tables || {}).reduce((sum, value) => sum + (Number(value) || 0), 0)} 条投递库记录。${escape(workSummary.reason || "")}</p>
          <p>旧相册：${escape(worldSummary.album?.state || "未发现")}，本地资源 ${Number(worldSummary.resource_files) || 0} 个、图片约 ${Math.ceil((Number(worldSummary.image_bytes) || 0) / 1048576)} MB。${escape(worldSummary.reason || "")}</p>
          <p>其余原始文件：${Number(legacyDataPlan.archive?.file_count) || 0} 个，约 ${Math.ceil((Number(legacyDataPlan.archive?.total_bytes) || 0) / 1048576)} MB；${Number(legacyDataPlan.archive?.excluded_count) ? `${Number(legacyDataPlan.archive.excluded_count)} 个链接或不可读项目无法安全归档；其余可读文件可以归档，清单会标记不完整。` : "全部普通文件可归档。"}归档按文件校验 SHA-256，不会提供给对话或执行旧任务。</p>
          <p>独立记忆库：${escape(memoryDatabase.state || "未试算")}，原始快照 ${Number(memoryDatabase.table_count) || 0} 张表 / ${Number(memoryDatabase.record_count) || 0} 条记录，约 ${Math.ceil((Number(memoryDatabase.total_bytes) || 0) / 1048576)} MB。已映射预览可查询 ${Number(memoryProjection.imported?.memories) || 0} 条记忆、${Number(memoryProjection.imported?.timeline) || 0} 条时间线；未通过映射或治理检查的内容继续只保留在隔离归档中，不会写入当前版本活跃事实或任务。</p>
          <p>归档专属：未迁入 ${Number(memoryArchiveOnly.group_owner_count) || 0} 个身份 / ${Number(memoryArchiveOnly.group_record_count) || 0} 条群记录；哈希命名空间 ${Number(memoryArchiveOnly.hashed_namespace_record_count) || 0} 条；身份缺失 ${Number(memoryArchiveOnly.unresolved_records) || 0} 条；未映射私聊 ${Number(memoryArchiveOnly.unmapped_private_records) || 0} 条。${Number(memoryDatabase.excluded_count) ? `另有 ${Number(memoryDatabase.excluded_count)} 个资产无法安全读取。` : ""}</p>
          ${memoryTables.length ? `<details class="setup-legacy-values"><summary>独立记忆库表清单（${memoryTables.length}）</summary><ul>${memoryTables.map(([name, count]) => `<li><strong>${escape(name)}</strong><small>${Number(count) || 0} 条</small></li>`).join("")}</ul></details>` : ""}
          ${reactionSummary.rejected?.length ? `<details class="setup-legacy-values"><summary>已跳过素材（${reactionSummary.rejected.length}）</summary><ul>${reactionSummary.rejected.slice(0, 30).map(item => `<li><strong>${escape(item.filename || "未命名素材")}</strong><small>${escape(dataReasons[item.reason] || item.reason || "无法读取")}</small></li>`).join("")}</ul></details>` : ""}
          ${journalSummary.rejected?.length ? `<details class="setup-legacy-values"><summary>未导入的日记或梦境（${journalSummary.rejected.length}）</summary><ul>${journalSummary.rejected.slice(0, 30).map(item => `<li><strong>${escape(item.date || item.kind || "记录")}</strong><small>${escape(item.reason || "记录格式无法识别")}</small></li>`).join("")}</ul></details>` : ""}
          ${worldbookSummary.rejected?.length ? `<details class="setup-legacy-values"><summary>未映射的世界书条目（${worldbookSummary.rejected.length}）</summary><ul>${worldbookSummary.rejected.slice(0, 30).map(item => `<li><strong>条目 ${Number(item.index) + 1}</strong><small>${escape(dataReasons[item.reason] || item.reason || "条目格式无法识别")}</small></li>`).join("")}</ul></details>` : ""}
          ${legacyDataPlan.warnings?.map(item => `<p>${escape(legacyDisplayText(item))}</p>`).join("") || ""}
        </div>`) : ""}
      </div>
        ${personaOptions.length ? `<div class="setup-migration-manual-personas">${legacyPersonaDrawer(personaOptions, {allowApply: true})}</div>` : ""}
        <div class="setup-migration-manual-import">${legacyImportStepMarkup()}</div>
      </div>`;

    if (legacyManualOpen) {
      return `<section class="setup-stage setup-legacy setup-migration-workspace setup-migration-manual-page">
        <header class="setup-migration-manual-head" data-migration-stage-head tabindex="-1">
          <div>
            <p class="setup-kicker">逐项迁移 · 独立入口</p>
            <h2>逐项处理旧版资料</h2>
            <p class="setup-migration-lead">这里保留完整的逐项能力：选择旧版人格 / 群、四个映射页签、勾选资料和手动导入。它不属于四步向导，也不会出现在默认路径里。</p>
          </div>
          <button type="button" class="button primary" data-setup-legacy-wizard="back-to-oneclick">返回一键迁移</button>
        </header>
        ${legacyError ? `<p class="setup-inline-error" role="alert">${escape(legacyDisplayText(legacyError))}</p>` : ""}
        ${legacyDataErrorMarkup()}
        ${notice && !legacyError && !legacyDataError ? noticeMarkup() : ""}
        ${legacyInstallNotice()}
        ${legacyConfigurationStepMarkup({findings, scope: sourceReviewScope, settings: plannedSettings,
          differences: reviewDifferences, audit: auditDifferences, blocked, canApply})}
        ${manualPanels}
        <div class="setup-migration-manual-foot">
          <button type="button" class="button" data-setup-legacy-wizard="back-to-oneclick">返回一键迁移</button>
        </div>
      </section>`;
    }

    return `<section class="setup-stage setup-legacy setup-migration-workspace" data-migration-step="${legacyCurrentStep()}">
      ${legacyMigrationHeader()}
      ${legacyWizardProgressMarkup()}
      <div class="setup-migration-main">
        ${legacyStageHead()}
        <div class="setup-migration-body" aria-busy="${legacyActiveJobs().length > 0 || legacyWizardExecuting}">
          <div class="setup-migration-compat" aria-hidden="true">${legacyTabs([["review", "迁移结果检查"], ["mapping", "资料范围与映射"]], legacyView, "setup-legacy-view", "迁移工作区")}</div>
          ${legacyError ? `<p class="setup-inline-error" role="alert">${escape(legacyDisplayText(legacyError))}</p>` : ""}
          ${legacyDataErrorMarkup()}
          ${notice && !legacyError && !legacyDataError ? noticeMarkup() : ""}
          ${legacyProgressMarkup()}
          <div class="setup-migration-step-panel setup-migration-role-step" ${legacyWizardStep === 1 ? "" : "hidden"}>
            ${legacyRoleCardMarkup()}
          </div>
          <div class="setup-migration-step-panel setup-migration-review-step" ${legacyWizardStep === 2 ? "" : "hidden"}>
            ${legacyStatusStripMarkup()}
            ${legacyReviewStepMarkup()}
          </div>
          <div class="setup-migration-step-panel setup-migration-run-step" ${legacyWizardStep === 3 ? "" : "hidden"}>
            ${legacyMigrationStepMarkup()}
          </div>
          <div class="setup-migration-step-panel setup-migration-finish-step" ${legacyWizardStep === 4 ? "" : "hidden"}>
            ${legacyFinishStepMarkup()}
          </div>
          ${legacyWizardControls()}
        </div>
      </div>
    </section>`;
  }

  function legacyDataErrorMarkup() {
    return legacyDataError ? `<p class="setup-inline-error">${escape(legacyDisplayText(legacyDataError))}</p>` : "";
  }

  function bindingFacts(binding) {
    if (!binding || !binding.participant_id) return "";
    return `<dl class="setup-facts">
      <div><dt>平台</dt><dd>${escape(product()?.platformLabel?.(binding) || "QQ · OneBot")}</dd></div>
      <div><dt>Bot 账号</dt><dd>${escape(legacyAccountLabel(binding))}</dd></div>
      <div><dt>私聊对象</dt><dd>${escape(legacyTargetLabel(binding))}</dd></div>
      <div><dt>AstrBot 人格</dt><dd>${escape(binding.persona_id === "[%None]" ? "默认人格" : binding.persona_id || "跟随当前会话")}</dd></div>
    </dl>`;
  }

  function renderBinding() {
    const binding = state?.binding || {};
    const command = "我会永远陪着你";
    if (binding.pending) {
      return `<section class="setup-stage">
        <p class="setup-kicker">第 1 步 · 核对窗口</p><h3>核对核心管理员窗口</h3>
        <p>已收到这条私聊里的绑定指令。核对它是你希望当前版本接收管理员指令、也作为主动消息主要投递窗口的私聊。</p>
        <p class="setup-command"><code>${escape(command)}</code></p>
        ${connectionDiagnostic()}
        ${bindingFacts(binding)}
        <p class="setup-confirm-copy">候选绑定只保存在本插件目录，不会触发聊天回复。平台、Bot 账号、私聊对象和人格都正确后，点「确认此窗口」。</p>
        ${noticeMarkup()}
        <div class="setup-actions">${button("confirm-binding", "确认此窗口", {primary: true})}${button("refresh", "重新读取")}</div>
      </section>`;
    }
    if (state?.binding_confirmed) {
      return `<section class="setup-stage">
        <p class="setup-kicker">第 1 步 · 已完成</p><h3>核心管理员窗口已绑定</h3>
        ${bindingFacts(binding)}
        <p>这个私聊窗口用于管理员控制，也是主动消息测试的目标。下一步配置模型后，当前版本才会建立运行实例。</p>
        ${noticeMarkup()}
        <div class="setup-actions">${button("refresh", "继续配置模型", {primary: true})}</div>
      </section>`;
    }
    return `<section class="setup-stage">
      <p class="setup-kicker">第 1 步 · 约 2 分钟</p><h3>绑定核心管理员窗口</h3>
      <p>在希望当前版本接收管理员指令、也作为主动消息主要投递窗口的私聊中，发送固定指令：</p>
      <p class="setup-command"><code>${escape(command)}</code></p>
      <p>回到这里核对平台、Bot 账号、私聊对象和人格，再由管理面板确认。若该账号使用多个人格，可发送 <code>我会永远陪着你 人格名</code> 指定。</p>
      ${connectionDiagnostic()}
      ${state?.settings_error ? `<p class="setup-inline-error">${escape(product()?.errorLabel?.(state.settings_error) || "运行设置尚未就绪，请核对账号、人格与模型。")}</p>` : ""}
      ${noticeMarkup()}
      <div class="setup-actions">${button("refresh", "我已发送，检查绑定", {primary: true})}</div>
    </section>`;
  }

  function renderSection(step) {
    const definitions = {
      knowledge: {number: "第 3 步 · 可选", title: "从人格整理角色设定", target: "role_profile", canSkip: true},
      relationships: {number: "第 4 步 · 可选", title: "整理熟人卡", target: "relationship_profile", canSkip: true},
    }[step];
    const complete = state?.completed?.includes(step);
    const skipped = state?.skipped?.includes(step);
    const target = definitions.target;
    const current = profileState?.[target];
    const draft = profileDraftTarget === target ? profileDraft : null;
    const fields = draft?.fields || {};
    const currentValues = current?.settings || {};
    const draftFields = Object.keys(fields).filter(name => profileSelected.has(name)
      || String(profileFieldDrafts[name] ?? fields[name] ?? "").trim());
    const fieldMarkup = draftFields.map(name => {
      const value = profileFieldDrafts[name] ?? fields[name] ?? "";
      const limit = Number(draft?.limits?.[name] || 0);
      const exists = Boolean(String(currentValues[name] || "").trim());
      return `<label class="setup-profile-field"><span><input type="checkbox" data-profile-select="${escape(name)}" ${profileSelected.has(name) ? "checked" : ""} ${busy ? "disabled" : ""}><strong>${escape(PROFILE_LABELS[name] || name)}</strong>${exists ? `<small>已有内容</small>` : ""}</span><textarea data-profile-field="${escape(name)}" rows="${value.length > 180 ? 5 : 3}" maxlength="${limit || 6000}" ${busy ? "disabled" : ""}>${escape(value)}</textarea><small class="setup-profile-count">${value.length}${limit ? ` / ${limit}` : ""}</small></label>`;
    }).join("");
    return `<section class="setup-stage">
      <p class="setup-kicker">${definitions.number}</p><h3>${definitions.title}</h3>
      <p>${target === "role_profile" ? "读取当前 AstrBot 人格，结合已导入的当前版本世界资料生成角色档案草稿。检查后直接保存为当前版本角色设定。" : "从当前人格整理对管理员的称呼、关系和相处方式；仅保存到当前私聊的熟人卡。"}</p>
      <label class="setup-profile-supplement"><span>${target === "role_profile" ? "补充已确认的角色事实" : "补充已确认的关系资料"}</span><textarea data-profile-supplement rows="3" maxlength="4000" ${busy ? "disabled" : ""} placeholder="${target === "role_profile" ? "例如：她是人造人，生日是第一次醒来的那天；表达担心时会比较含蓄。" : "例如：希望称呼我为小舟；我是角色的搭档，我们会直接提醒彼此休息。"}">${escape(profileSupplement[target] || "")}</textarea></label>
      ${current ? `<p class="setup-profile-source">当前角色：${escape(state?.binding?.persona_id === "[%None]" ? "默认人格" : state?.binding?.persona_id || "已绑定人格")} · 已保存 ${Object.values(currentValues).filter(value => String(value || "").trim()).length} 项</p>` : ""}
      ${profileNotes.length ? `<ul class="setup-profile-notes">${profileNotes.map(note => `<li>${escape(note)}</li>`).join("")}</ul>` : ""}
      ${draft ? `<section class="setup-profile-preview" aria-label="生成的资料草稿"><header><strong>${target === "role_profile" ? "角色档案草稿" : "熟人卡草稿"}</strong><span>${escape(draft.source?.name || "当前人格")}</span></header>${draft.source?.source_preview ? `<p>${escape(draft.source.source_preview)}</p>` : ""}${target === "role_profile" && draft.knowledge?.length ? `<details><summary>本次参考的世界资料 · ${draft.knowledge.length}</summary><ul>${draft.knowledge.map(item => `<li><strong>${escape(item.title)}</strong><span>${escape(item.source)}</span></li>`).join("")}</ul></details>` : ""}<div class="setup-profile-fields">${fieldMarkup || `<p>没有生成可审阅内容，请补充事实后重新生成。</p>`}</div><label class="setup-overwrite"><input type="checkbox" data-profile-overwrite ${profileOverwrite ? "checked" : ""} ${busy ? "disabled" : ""}><span>覆盖已有字段</span></label><div class="setup-actions">${button("save-profile", target === "role_profile" ? "保存所选角色设定" : "保存所选熟人卡", {primary: true, disabled: !draftFields.length || !current})}${button("generate-profile", "重新生成草稿")}</div></section>` : ""}
      ${!current && !notice ? `<p class="setup-loading">正在读取当前角色设定…</p>` : ""}
      ${complete || skipped ? `<p class="setup-recorded">${skipped ? "已选择暂时跳过" : "已标记完成"}</p>` : ""}
      ${noticeMarkup()}
      <div class="setup-actions">
        ${button("generate-profile", draft ? "重新生成人格草稿" : target === "role_profile" ? "从人格生成角色设定" : "从人格生成熟人卡", {primary: !draft, disabled: !current})}
        ${button("complete-step", complete ? "重新确认完成" : skipped ? "改为已完成" : "已完成这部分", {primary: true})}
        ${definitions.canSkip ? button("skip-step", "暂时跳过") : ""}
      </div>
    </section>`;
  }

  function renderModel() {
    const model = state?.model || {};
    const providers = Array.isArray(model.providers) ? model.providers : [];
    const providerId = modelDraft || model.provider_id || "";
    const taskProviders = Array.isArray(modelTasks?.providers) ? modelTasks.providers : [];
    const tasks = Array.isArray(modelTasks?.tasks) ? modelTasks.tasks : [];
    const selectedProvider = providers.find(item => item.id === providerId);
    const taskMarkup = tasks.map(task => {
      const draft = modelTaskDrafts[task.kind] || {};
      const provider = draft.provider ?? task.fields.provider.value;
      const fallback = draft.fallback ?? task.fields.fallback.value;
      const choices = taskProviders.filter(item => task.capabilities.every(capability => item.capabilities.includes(capability)));
      return `<details class="setup-task-route"><summary><strong>${escape(taskLabel(task.kind))}</strong><small>${escape(task.kind)} · ${escape(task.lane)}</small></summary><div class="setup-task-route-fields"><label><span>主 Provider</span><select data-setup-model-task="${escape(task.kind)}" data-setup-model-field="provider" ${busy || !choices.length ? "disabled" : ""}>${choices.map(item => `<option value="${escape(item.id)}" ${provider === item.id ? "selected" : ""}>${escape(item.name || item.id)}</option>`).join("")}</select></label><label><span>备用 Provider</span><select data-setup-model-task="${escape(task.kind)}" data-setup-model-field="fallback" ${busy || !choices.length ? "disabled" : ""}><option value="">不设置</option>${choices.filter(item => item.id !== provider).map(item => `<option value="${escape(item.id)}" ${fallback === item.id ? "selected" : ""}>${escape(item.name || item.id)}</option>`).join("")}</select></label>${button(`save-task:${task.kind}`, "保存路由", {disabled: !choices.length})}</div></details>`;
    }).join("");
    return `<section class="setup-stage setup-question-stage">
      ${connectionDiagnostic()}
      <div class="setup-question-card">
        <p class="setup-kicker">本步只问一件事</p>
        <h3>先选一个插件通用模型</h3>
        <p>日常聊天仍然使用 AstrBot 当前人格和主模型；这里只决定主动联系、日程、状态和记忆整理这些后台任务用哪个模型。</p>
        <ul class="setup-question-points">
          <li>选择插件通用模型：先选一个能稳定调用的文本模型即可，不必一开始就分得很细</li>
          <li>保存后会沿用为后台任务的默认模型，仍可逐项覆盖</li>
          <li>任务路由可以稍后在「模型与连接」中调整，不影响角色设定</li>
        </ul>
        ${providers.length ? `<label class="setup-model-field"><span>初始主 Provider（插件通用模型） <span class="setup-recommendation">推荐只选这里</span></span>
          <select name="setup-provider" ${busy ? "disabled" : ""}>
            <option value="">选择 Provider</option>
            ${providers.map(item => `<option value="${escape(item.id)}" ${providerId === item.id ? "selected" : ""}>${escape(item.name)} · ${escape(item.id)}</option>`).join("")}
          </select></label>
          ${selectedProvider ? `<p class="setup-question-note">当前选择：${escape(selectedProvider.name || selectedProvider.id)}。保存后先作为插件后台任务的默认入口。</p>` : ""}`
          : `<p class="setup-inline-error">AstrBot 当前没有已加载的文本 Provider。请先在 AstrBot 的提供商管理中添加模型，再回来刷新。</p>`}
        ${state?.settings_error ? `<p class="setup-inline-error">${escape(state.settings_error)}</p>` : ""}
        ${noticeMarkup()}
        <div class="setup-actions">${state?.prepared
          ? button("complete-step", "继续到角色设定", {primary: true})
          : button("save-model", "保存并继续", {primary: true, disabled: !providers.length || !providerId})}
          ${providers.length ? button("refresh", "刷新 Provider") : ""}
        </div>
      </div>
      ${state?.prepared ? `<details class="setup-model-advanced">
        <summary>任务模型路由 · 高级设置（${tasks.length} 项，可稍后再改）</summary>
        <p class="setup-question-note">不打开也可以正常使用。只有当你希望主动联系、日程或记忆使用不同模型时，才需要在这里逐项覆盖。</p>
        ${taskMarkup || `<p class="setup-inline-error">${modelTasks ? "当前没有已注册的任务路由。" : "任务路由尚未读取，请刷新后重试。"}</p>`}
      </details>` : ""}
    </section>`;
  }

  const PROACTIVE_TEST_STAGE_LABELS = {
    authorization: "管理授权", binding: "核心窗口绑定", runtime: "运行实例",
    settings: "主动联系设置", purpose: "联系目的", trusted_candidate: "测试机会",
    model_route: "模型路由", model_generation: "模型生成", persona_review: "人格复核",
    delivery: "平台投递", receipt: "投递回执",
  };
  const PROACTIVE_TEST_FAILURE_SUGGESTIONS = {
    setup_contact_generation_failed: "生成任务已结束，请检查模型路由与该任务的失败原因。",
    model_task_unregistered: "当前角色尚未配置主动联系模型任务。",
    model_provider_unavailable: "主动联系模型不可用，请检查模型路由。",
    model_budget_exhausted: "主动消息模型的每日额度已用完。",
    contact_diagnostic_source_required: "当前没有可核验的测试契机。",
    setup_test_interrupted: "上次测试已中断，投递结果仍需核对。",
    setup_contact_timeout: "本次等待已结束，投递结果仍需核对。",
    delivery_result_unknown: "平台尚未返回确定的投递回执。",
  };

  function proactiveTestProgressMarkup() {
    const test = state?.proactive_test;
    const stages = Array.isArray(test?.stages) ? test.stages : [];
    const labels = {passed: "已完成", running: "进行中", pending: "待处理",
      failed: "失败", skipped: "已跳过", not_required: "无需执行"};
    const progress = stages.map(item => `<li>${escape(PROACTIVE_TEST_STAGE_LABELS[item.stage] || item.stage)}：${escape(labels[item.state] || item.state)}${item.detail ? ` · ${escape(item.detail)}` : ""}</li>`).join("");
    const code = typeof test?.error_code === "string" ? test.error_code : "";
    return `${progress ? `<ol class="setup-checklist" aria-label="主动测试进度">${progress}</ol>` : ""}${code ? `<p class="setup-inline-error" role="alert">${escape(PROACTIVE_TEST_FAILURE_SUGGESTIONS[code] || "本次测试未完成，请核对模型与投递状态。")}（${escape(code)}）</p>` : ""}`;
  }

  function renderProactiveTest() {
    const test = state?.proactive_test;
    if (state?.ready) {
      const accepting = Boolean(state?.host?.accepting);
      return `<section class="setup-stage setup-finished">
        <p class="setup-kicker">首次连接</p><h3>首次设置已完成</h3>
        <p>核心管理员窗口、模型与初始资料已记录。之后直接使用陪伴小屋和聊天窗口，不需要重新走向导。</p>
        <p class="setup-test-receipt setup-connection-state ${accepting ? "" : "is-stopped"}">${accepting ? "当前版本正在接收消息" : "首次设置已保留，当前运行实例尚未接收消息"}</p>
        <p class="setup-test-receipt">${test?.state === "received" ? "你已确认收到测试消息" : "平台已确认发送测试消息"} · ${escape(test?.confirmed_at || test?.finished_at || "")}</p>
        <div class="setup-actions">${accepting ? "" : button("reconnect-host", "重新连接", {primary: true})}${homeButton("进入陪伴小屋", !accepting)}</div>
      </section>`;
    }
    if (test?.state === "started") {
      return `<section class="setup-stage">
        <p class="setup-kicker">第 8 步 · 验证使用</p><h3>主动测试进行中</h3>
        ${proactiveTestProgressMarkup()}${noticeMarkup()}
        <div class="setup-actions">${button("confirm-test-received", "已收到测试消息", {primary: true})}${button("refresh", "刷新状态")}${homeButton("进入陪伴小屋")}</div>
      </section>`;
    }
    if (test?.state === "unknown") {
      return `<section class="setup-stage">
        <p class="setup-kicker">第 8 步 · 验证使用</p><h3>主动测试结果未知</h3>
        <p>本次测试尚无确定结果。</p>
        ${proactiveTestProgressMarkup()}${noticeMarkup()}
        <div class="setup-actions">${button("confirm-test-received", "已收到测试消息", {primary: true})}${button("refresh", "刷新状态")}${homeButton("进入陪伴小屋")}</div>
      </section>`;
    }
    if (test?.state === "failed") {
      return `<section class="setup-stage"><p class="setup-kicker">第 8 步 · 验证使用</p><h3>主动联系没有送达</h3>${proactiveTestProgressMarkup()}${noticeMarkup()}<label class="setup-opt-in"><input type="checkbox" data-setup-proactive-opt-in ${proactiveOptIn ? "checked" : ""} ${busy ? "disabled" : ""}><span>我同意当前版本向已绑定的管理员私聊发送一次主动联系测试</span></label><div class="setup-actions">${button("proactive-test", "重新运行主动联系", {primary: true, disabled: !proactiveOptIn})}${button("refresh", "刷新状态")}${homeButton("进入陪伴小屋")}</div></section>`;
    }
    return `<section class="setup-stage">
      <p class="setup-kicker">第 8 步 · 约 1 分钟</p><h3>测试主动消息</h3>
      <p>当前版本会为当前角色发起一次主动联系，完整经过主动联系模型、策略检查与真实投递。只有勾选同意后才会发送；若平台回执未知，不会重复补发。</p>
      <label class="setup-opt-in"><input type="checkbox" data-setup-proactive-opt-in ${proactiveOptIn ? "checked" : ""} ${busy ? "disabled" : ""}><span>我同意当前版本向已绑定的管理员私聊发送一次主动联系测试</span></label>
      ${noticeMarkup()}
      <div class="setup-actions">${button("proactive-test", "运行主动联系测试", {primary: true, disabled: !proactiveOptIn})}</div>
    </section>`;
  }

  function renderUnavailable() {
    return `<section class="setup-stage">
      <p class="setup-kicker">连接状态</p><h3>暂时读不到设置进度</h3>
      <p>${escape(notice || "Dashboard 与当前版本管理接口暂时没有连通。")}</p>
      <div class="setup-actions">${button("refresh", "重新读取", {primary: true})}</div>
    </section>`;
  }

  /* 升级引导卡：自动接续无法确认的绑定时，显示缺项并保留手动核对入口。 */
  function upgradeCard() {
    const upgrade = state?.upgrade;
    if (upgrade?.present && !upgrade.configured) {
      const people = (upgrade.targets || []).map(item => item.participant_id).join("、");
      const voices = (upgrade.personas || []).map(item => item.persona_id).join("、");
      return `<section class="setup-upgrade-review" aria-labelledby="setup-upgrade-title">
        <header class="setup-upgrade-review-header"><h3 id="setup-upgrade-title">已识别旧版配置</h3></header>
        <dl class="setup-upgrade-facts">
          <div><dt>角色人格</dt><dd>${escape(voices || "待确认")}</dd></div>
          <div><dt>私聊对象</dt><dd>${escape(people || "待确认")}</dd></div>
          <div><dt>模型</dt><dd>${escape(upgrade.provider_id || "待确认")}</dd></div>
          <div><dt>群聊</dt><dd>${(upgrade.groups || []).length} 个</dd></div>
        </dl>
        <footer class="setup-upgrade-review-actions">
          <p>${upgrade.can_prepare ? "确认后继续迁移角色资料与历史数据。" : "请先在「账号与会话」核对在线账号、人格与模型。"}</p>
          ${button("prepare-upgrade", "确认并接续旧版配置", {primary: true, disabled: !upgrade.can_prepare})}
        </footer>
      </section>`;
    }
    const install = legacyFinding?.install;
    if (upgrade?.present && upgrade.configured && !install) {
      return `<section class="setup-upgrade-card" aria-labelledby="setup-upgrade-title">
        <div><span class="setup-kicker">旧版升级</span><h3 id="setup-upgrade-title">旧版连接已接续</h3>
        <p class="setup-upgrade-path">账号、人格与模型连接已沿用。需要导入旧版配置或历史资料可随时点右侧「继续迁移」；若已迁完或暂不导入，直接继续下方设置与主动消息测试即可。</p></div>
        ${button("open-migration", "继续迁移")}
      </section>`;
    }
    if (!install?.v1_present || install.phase === "new_user") return "";
    if (install.phase === "upgrade_restart_check") {
      return `<section class="setup-upgrade-card" aria-labelledby="setup-upgrade-title">
        <div><span class="setup-kicker">从旧版升级</span><h3 id="setup-upgrade-title">${install.configuration_scope_unknown ? "发现旧迁移记录" : "配置已迁移"}，${install.restart_observed ? "请完成重启核对" : "等待重启核对"}</h3>
        <p class="setup-upgrade-path">${install.configuration_scope_unknown ? "旧记录未保存配置范围，请核对后在下方补迁遗漏设置。" : "资料迁移状态单独处理；旧版文件仍保留。"}</p></div>
        <button type="button" class="button primary" data-setup-action="open-migration">去迁移页核对</button>
      </section>`;
    }
    if (install.phase === "upgrade_cleanup") {
      const completionTitle = install.restart_required && install.restart_observed
        ? "重启核对已完成" : "配置已核对，无需重启";
      return `<section class="setup-upgrade-card" aria-labelledby="setup-upgrade-title">
        <div><span class="setup-kicker">从旧版升级</span><h3 id="setup-upgrade-title">${completionTitle}</h3>
        <p class="setup-upgrade-path">资料迁移需单独查看；确认无遗漏后可手动归档旧版文件。</p></div>
        <button type="button" class="button" data-setup-action="open-migration">查看迁移记录</button>
      </section>`;
    }
    if (!install.pending) return "";
    return `<section class="setup-upgrade-card" aria-labelledby="setup-upgrade-title">
      <div><span class="setup-kicker">从旧版升级</span><h3 id="setup-upgrade-title">旧版的数据还在，按顺序走一遍就能接上</h3>
      <ol><li>先确认自动识别出的当前版本账号与角色；多个候选时再选择对应关系。</li>
      <li>点「去迁移页」继承旧版可转换配置，这一步不需要重新填写模型参数。</li>
      <li>重启后核对回复；角色资料可在同一页按需映射导入。</li></ol>
      <p class="setup-upgrade-path">无法唯一确认目标时，页面会要求补充对应关系，不会自动猜测。</p>
      <p class="setup-upgrade-path">旧版位置：${escape(install.v1_root || "")}</p></div>
      <button type="button" class="button primary" data-setup-action="open-migration">去迁移页</button>
    </section>`;
  }

  function legacyCommitStep(entering = false) {
    const head = root?.querySelector?.("[data-migration-stage-head]");
    if (!head) return;
    const motion = document.documentElement?.dataset?.motion;
    const reduce = motion === "off"
      || window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
    /* 步骤切换时给宿主节点做一次短过渡。innerHTML 会被随后的异步刷新整段替换，
       动画挂在宿主上才不会被替换打断。 */
    if (entering && !reduce && typeof root.animate === "function") {
      const low = motion === "low";
      for (const previous of root.getAnimations?.() || []) previous.cancel();
      root.animate(
        low ? [{opacity: .72}, {opacity: 1}]
          : [{opacity: .55, transform: "translateY(7px)"}, {opacity: 1, transform: "none"}],
        {duration: low ? 120 : 260, easing: "cubic-bezier(.22, .72, .24, 1)"},
      );
    }
    /* 窄屏的步骤条是横向滚动条：跳步后把当前步带回可见区域。 */
    const rail = root.querySelector?.(".setup-migration-wizard ol");
    const active = rail?.querySelector?.("li.is-current");
    if (rail && active && rail.scrollWidth > rail.clientWidth + 2) {
      rail.scrollLeft = Math.max(0, active.offsetLeft - (rail.clientWidth - active.offsetWidth) / 2);
    }
    /* 点击按钮的默认焦点处理发生在本函数之后：同步 focus 会被浏览器随后
       重置回 body。放到微任务里补，且只在焦点仍处于文档级时补一次。 */
    const focused = document.activeElement;
    const idle = !focused || focused === document.body || focused === document.documentElement
      || !root.contains?.(focused);
    if (pendingMigrationFocus && !idle) pendingMigrationFocus = false;
    if (pendingMigrationFocus && idle) {
      pendingMigrationFocus = false;
      const focusNow = () => {
        const current = root?.querySelector?.("[data-migration-stage-head]");
        const active = document.activeElement;
        if (!current) return;
        if (active && active !== document.body && active !== document.documentElement && current.contains(active)) return;
        current.focus?.({preventScroll: true});
      };
      if (typeof requestAnimationFrame === "function") requestAnimationFrame(focusNow);
      else setTimeout(focusNow, 0);
    }
    head.scrollIntoView?.({block: "nearest", behavior: reduce || motion === "low" ? "auto" : "smooth"});
  }

  /* 开始使用 = 旧版「配置引导」那种一页式清单：编号分区、每区一句说明、
     点开就在本页编辑，不再让用户去六个页面来回找。 */
  function renderStartGuide() {
    const items = ADVANCED_BLOCKS.map((item, index) => `<li class="setup-guide-item">
      <span class="setup-guide-number">${String(index + 1).padStart(2, "0")}</span>
      <div class="setup-guide-copy"><h3>${escape(item.label)}</h3><p>${escape(item.note)}</p></div>
      <button type="button" class="button small" data-setup-open-block="${escape(item.id)}">展开设置</button>
    </li>`).join("");
    return `<div class="setup-shell setup-complete-shell setup-guide-shell">
      <header class="setup-header"><div class="setup-header-copy"><span class="setup-kicker">我会永远陪着你</span><h2>开始使用 · 常用设置导览</h2>
        <p class="setup-header-sub">首次连接与主动测试已完成。下面按使用频率整理了 6 组常用设置，点「展开设置」即可就地修改，不必在多个页面来回寻找。</p></div>
        <div class="setup-header-actions"><button type="button" class="button" data-setup-action="advanced-open">快速配置</button>${homeButton("进入陪伴小屋")}</div></header>
      ${upgradeCard()}
      <p class="setup-guide-status" role="status">首次设置已完成 · 之后随时可以回来调整这些设置。</p>
      <ol class="setup-guide-list" aria-label="配置引导">${items}</ol>
      ${renderProactiveTest()}
    </div>`;
  }

  function render() {
    if (!root) return;
    for (const details of root.querySelectorAll?.("[data-migration-detail]") || []) {
      if (details.open) legacyOpenDetails.add(details.dataset.migrationDetail);
      else legacyOpenDetails.delete(details.dataset.migrationDetail);
    }
    stopLegacySettings();
    stopQuickWorkspace();
    onState?.({upgrading: Boolean(state?.upgrade?.present && !state?.upgrade?.configured)});
    if (migrationOnly && legacySettingsKey) {
      root.innerHTML = legacySettingsMarkup();
      mountLegacySettings();
      return;
    }
    if (migrationOnly) {
      /* 步骤切换才播放进入动效；勾选、刷新等同一屏重绘保持安静。 */
      const entering = paintedMigrationStep !== 0 && paintedMigrationStep !== legacyWizardStep;
      if (entering) pendingMigrationFocus = true;
      root.innerHTML = legacyPanel(entering);
      paintedMigrationStep = legacyWizardStep;
      if (entering || pendingMigrationFocus) legacyCommitStep(entering);
      return;
    }
    if (!state) {
      root.innerHTML = `<div class="setup-shell setup-complete-shell">${notice ? renderUnavailable()
        : `<p class="setup-loading" role="status">正在读取设置进度…</p>`}</div>`;
      return;
    }
    if (advancedOpen && (state.ready || state.prepared)) {
      root.innerHTML = renderAdvanced();
      mountQuickWorkspace();
      return;
    }
    if (state.upgrade?.present && !state.upgrade?.configured) {
      root.innerHTML = `<div class="setup-shell setup-upgrade-shell">
        <ol class="setup-upgrade-steps" aria-label="升级进度">
          <li ${state.upgrade.configured ? 'class="complete"' : 'aria-current="step"'}><span>${state.upgrade.configured ? "✓" : "1"}</span><strong>接续配置</strong></li>
          <li ${state.upgrade.configured ? 'aria-current="step"' : ""}><span>2</span><strong>迁移资料</strong></li>
          <li><span>3</span><strong>验证使用</strong></li>
        </ol>
        ${upgradeCard()}
        ${notice ? `<p class="setup-notice setup-inline-error" role="status">${escape(notice)}</p><div class="setup-actions">${button("refresh", "重新读取状态")}</div>` : ""}
      </div>`;
      return;
    }
    if (state.ready) {
      root.innerHTML = renderStartGuide();
      return;
    }
    const step = state.step || "binding";
    const content = step === "binding" ? renderBinding()
      : step === "model" ? renderModel()
      : step === "proactive_test" ? renderProactiveTest()
      : STEP_EDITORS[step] ? renderSettingsStep(step)
      : renderSection(step);
    const current = stepIndex(step);
    root.innerHTML = `<div class="setup-shell">
      <header class="setup-header">
        <div class="setup-header-copy"><span class="setup-kicker">我会永远陪着你</span><h2>开始使用</h2>
          <p class="setup-header-sub">第 ${current + 1} 步 / 共 ${ORDER.length} 步 · ${escape((STEP_GROUPS.find(group => group.steps.includes(step)) || {}).title || "")} · 可随时跳过或回来继续</p></div>
        <div class="setup-header-actions">${state.prepared ? `<button type="button" class="button" data-setup-action="advanced-open">快速配置</button>` : ""}<span class="setup-step-count">${String(current + 1).padStart(2, "0")} / ${String(ORDER.length).padStart(2, "0")}</span></div>
        <div class="setup-header-progress" aria-hidden="true"><i style="width:${((current + 1) / ORDER.length * 100).toFixed(1)}%"></i></div>
      </header>
      ${renderRail(step)}<div class="setup-content${step === "model" ? " setup-question-mode" : ""}">${upgradeCard()}${stageBanner(step)}${content}${nextStepPreview(step)}</div>
    </div>`;
    if (STEP_EDITORS[step] && state.prepared) mountStepEditor(step);
  }

  async function loadStepData(result) {
    let loadError = "";
    const quickModelsNeeded = advancedOpen && advancedBlock === "runtime" && result?.prepared;
    if (result?.prepared && ((result?.step === "model" && modelDraft) || quickModelsNeeded)) {
      loadError = await refreshModelTasks();
    } else if (!quickModelsNeeded) {
      modelTasks = null;
      modelTasksStatus = "idle";
      modelTasksError = "";
    }
    if (["knowledge", "relationships"].includes(result?.step) && result?.prepared) {
      try { profileState = await api("GET", "/profile"); }
      catch (error) { profileState = null; loadError = errorText(error, "角色档案暂时无法读取。"); }
    } else if (!["knowledge", "relationships"].includes(result?.step)) profileState = null;
    if (loadError) notice = loadError;
  }

  async function refresh() {
    const ticket = ++generation;
    try {
      const result = await api("GET", "/status");
      if (ticket !== generation) return;
      state = result;
      /* 成功读到新状态后，上一轮网络失败提示必须消失；子步骤若读失败，
         loadStepData() 会再写入更具体的提示。 */
      notice = "";
      modelDraft = result?.model?.provider_id || "";
      await loadStepData(result);
      render();
      /* 旧版探测只补充升级卡，不应延迟当前版本首屏。 */
      const discoveryAddress = !migrationOnly && !legacyFinding ? legacyAddress() : "";
      if (discoveryAddress) {
        void migrationRequest("GET", discoveryAddress, legacyQuery()).then(found => {
          if (ticket !== generation || !root || migrationOnly || !found || found.status) return;
          legacyFinding = found;
          render();
        }).catch(() => {});
      }
      if (result?.proactive_test?.state === "started") void watchProactive();
    } catch (error) {
      if (ticket !== generation) return;
      state = null;
      notice = errorText(error, "当前版本管理接口暂时没有连通。");
      render();
    }
  }

  async function generateProfileDraft() {
    const target = state?.step === "relationships" ? "relationship_profile" : "role_profile";
    const productApi = product();
    const bridge = productApi?.bridgeNow?.() || (productApi?.bridgeFor ? await productApi.bridgeFor() : window.AstrBotPluginPage);
    if (!bridge?.apiPost) throw new Error("Dashboard 页面接口尚未连接。");
    const route = productApi.relative("/astrbot_plugin_private_companion/setup/profile-draft");
    const supplement = profileSupplement[target] || "";
    let timer;
    try {
      const response = await Promise.race([
        bridge.apiPost(route, {target, supplement}),
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(Object.assign(new Error("人格草稿生成超时，请保留补充内容后重试。"), {code: "role_profile_draft_timeout"})), 120000);
        }),
      ]);
      const payload = productApi.unwrap(response);
      if (payload.target !== target || !payload.draft?.fields) throw new Error("草稿目标或内容无效，请重新读取后生成。");
      profileDraftTarget = target;
      profileDraft = payload.draft;
      profileFieldDrafts = {};
      profileSelected = new Set(Object.entries(payload.draft.fields)
        .filter(([, value]) => typeof value === "string" && value.trim()).map(([name]) => name));
      profileNotes = [...(payload.draft.notes || [])];
      profileDraft = {...payload.draft, source: payload.source, knowledge: payload.knowledge,
        limits: payload.limits, total_limit: payload.total_limit};
      notice = target === "role_profile" ? "角色档案草稿已生成，请检查推断和世界资料后再保存。" : "熟人卡草稿已生成，请核对称呼、关系与相处方式后再保存。";
    } finally { clearTimeout(timer); }
  }

  async function saveProfileDraft() {
    const target = state?.step === "relationships" ? "relationship_profile" : "role_profile";
    const current = profileState?.[target];
    if (!current?.edit_token || profileDraftTarget !== target || !profileDraft?.fields) throw new Error("当前草稿或配置版本已变化，请重新读取后生成。");
    const changes = {};
    for (const name of profileSelected) {
      const value = profileFieldDrafts[name] ?? profileDraft.fields[name] ?? "";
      if (!String(value).trim()) continue;
      if (!profileOverwrite && String(current.settings?.[name] || "").trim()) continue;
      changes[name] = value;
    }
    if (!Object.keys(changes).length) throw new Error(profileOverwrite ? "先选择至少一个有内容的字段。" : "所选字段已有内容；打开“覆盖已有字段”后再保存。");
    await api("POST", "/profile/save", {target, operation_key: operationKey("setup-profile"),
      edit_token: current.edit_token, changes});
    profileState = await api("GET", "/profile");
    profileSelected = new Set();
    notice = `已保存 ${Object.keys(changes).length} 项${target === "role_profile" ? "角色设定" : "熟人卡资料"}。`;
  }

  async function saveModelTask(kind) {
    const route = currentRoleModelRoute();
    syncModelTasksScope(route.scope);
    if (modelTasksStatus !== "loaded") throw Object.assign(new Error("model_route_scope_changed"), {code: "model_route_scope_changed"});
    const task = modelTasks?.tasks?.find(item => item.kind === kind);
    if (!task) throw new Error("没有读到这项模型任务，请刷新后重试。");
    const draft = modelTaskDrafts[kind] || {};
    const provider = draft.provider ?? task.fields.provider.value;
    const fallback = draft.fallback ?? task.fields.fallback.value;
    const saved = await product().request("POST", route.path + "/configure", {
      kind, operation_key: operationKey("setup-model-route"), edit_token: task.edit_token,
      changes: {provider, fallback}, reset: [],
    });
    if (saved?.task?.kind !== kind || typeof saved.replayed !== "boolean") {
      throw Object.assign(new Error("invalid_response"), {code: "invalid_response"});
    }
    if (route.scope !== currentModelScopeKey()) {
      syncModelTasksScope();
      notice = "原角色模型路由已保存；当前小屋已切换到另一个角色。";
      return;
    }
    modelTasks = {...modelTasks, tasks: modelTasks.tasks.map(item => item.kind === kind ? saved.task : item)};
    modelTaskDrafts[kind] = {};
    notice = saved.replayed ? `${taskLabel(kind)}模型路由原保存已确认。` : `${taskLabel(kind)}模型路由已保存。`;
  }

  async function watchProactive() {
    if (proactiveWatcher) return;
    const watcher = {generation: mountGeneration, attemptId: state?.proactive_test?.attempt_id};
    proactiveWatcher = watcher;
    const currentWatch = () => root && proactiveWatcher === watcher && mountGeneration === watcher.generation
      && !state?.ready && state?.proactive_test?.attempt_id === watcher.attemptId;
    try {
      for (let attempt = 0; attempt < 205 && currentWatch(); attempt += 1) {
        const result = await product().request("GET", PREFIX + "/proactive-test/status", undefined, {timeoutMs: 10000});
        if (!currentWatch() || result?.proactive_test?.attempt_id !== watcher.attemptId) return;
        state = {...state, ...result, ...(result.ready ? {step: "complete"} : {})};
        render();
        const current = result?.proactive_test?.state;
        if (result?.ready || current !== "started") {
          if (current === "failed") proactiveOptIn = false;
          notice = current === "delivered" || current === "received" ? "当前版本主动联系已完成，收到真实投递回执。"
            : current === "failed" ? "主动联系链路明确失败，可以检查配置后重试。"
            : "投递结果未知；请核对目标私聊，不要再次发送。";
          render();
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 750));
      }
      if (currentWatch()) {
        notice = "自动查询已暂停，本次测试仍待确认。";
        render();
      }
    } catch (error) {
      if (!currentWatch()) return;
      notice = errorText(error, "主动联系状态暂时无法读取，请稍后刷新。");
      render();
    } finally { if (proactiveWatcher === watcher) proactiveWatcher = null; }
  }

  async function runLegacy(action) {
    legacyError = "";
    legacyDataError = "";
    migrationProgress = ({
      "legacy-scan": "正在只读检查旧版来源…",
      "legacy-plan": "正在试算可转换的配置…",
      "legacy-apply": "正在写入当前版本并核对迁移结果…",
      "legacy-data-plan": "正在清点旧版文件与独立记忆库；数据较多时可能需要几分钟…",
      "legacy-private-memory-plan": "正在核对旧本体私聊记忆与当前身份…",
      "legacy-private-memory-apply": "正在导入本体私聊记忆…",
      "legacy-data-apply": "正在导入所选资料并核对结果…",
      "legacy-data-batch-plan": "正在逐个试算已映射的人格目的地…",
      "legacy-data-batch-apply": "正在按顺序导入已确认的人格目的地…",
      "legacy-group-data-plan": "正在试算所选群资料…",
      "legacy-group-data-apply": "正在导入所选群资料并核对结果…",
      "legacy-auto": "正在把旧版配置写入当前版本目标…",
      "legacy-upgrade-confirm": "正在记录重载后的配置核对…",
    })[action] || "";
    const job = legacyJobs.get(action);
    if (job) job.progress = migrationProgress;
    render();
    if (action === "legacy-auto") {
      /* 自动继承完整迁移旧版陪伴插件设置，不要求独立 Reality 配置可读。
         资料迁移不在这里——它要逐个人格选目的地，见下面的 legacy-data-* 分支。 */
      if (!legacyAddress()) throw new Error("先选择有迁移接口的当前版本目标与人格。");
      legacyApi = legacyAddress();
      const preflight = await migrationRequest("GET", legacyRoute(), legacyQuery("plugin"));
      legacyFinding = preflight;
      legacyPlan = null;
      legacyResult = null;
      const blockers = legacyConfigurationBlockers("plugin", preflight?.findings);
      if (blockers.length) throw new Error(legacyConfigurationBlockerMessage(blockers));
      const autoResult = await migrationRequest("POST", legacyApi + "/auto", {
        ...(legacyRootDraft.trim() ? {v1_root: legacyRootDraft.trim()} : {})});
      if (autoResult?.state === "already_imported") {
        notice = "这份旧版配置已经导入过当前版本，无需重复导入。";
      } else if (autoResult?.state === "nothing_to_import") {
        notice = autoResult?.completion_recorded === false
          ? "旧版没有需要改写的配置，但升级进度未能保存；请重新读取状态。"
          : "旧版里没有需要改写的配置；当前版本现有设置保持不变。";
      } else if (autoResult?.configuration_imported === false) {
        notice = "配置写入存在未完成项，升级进度未标记完成；请检查结果后重新试算。";
      } else {
        notice = autoResult?.restart_required
          ? "旧版配置已继承。插件级设置需要重载本插件或重启 AstrBot 后生效；资料可在下方继续选择导入。"
          : "旧版配置已继承；资料可在下方继续选择导入。";
      }
      legacyPlan = null;
      legacyResult = autoResult || null;
      if (autoResult?.configuration_imported === true
          || (autoResult?.state === "nothing_to_import" && autoResult?.completion_recorded === true)
          || autoResult?.state === "already_imported") {
        const install = legacyFinding?.install;
        if (install) legacyFinding = {...legacyFinding, install: {...install,
          phase: autoResult?.restart_required ? "upgrade_restart_check" : "upgrade_cleanup",
          pending: false, configuration_imported: true,
          configuration_scopes: ["plugin"], configuration_scope_unknown: false,
          restart_observed: false, restart_required: Boolean(autoResult?.restart_required),
          upgrade_verified: !autoResult?.restart_required}};
      }
      try {
        const refreshed = await migrationRequest("GET", legacyRoute(), legacyQuery("all"));
        if (refreshed && !refreshed.status) legacyFinding = refreshed;
      } catch {
        legacyError = "配置写入已收到成功回执，但升级状态刷新失败；请重新检查旧版状态后再继续，暂勿重复继承。";
      }
    } else if (action === "legacy-upgrade-confirm") {
      if (!legacyAddress()) throw new Error("先选择迁移目标与角色，再确认升级状态。");
      legacyApi = legacyAddress();
      const result = await migrationRequest("POST", legacyApi + "/upgrade-confirm", {confirmed: true});
      if (result?.install && legacyFinding) legacyFinding = {...legacyFinding, install: result.install};
      notice = "已记录重载后的配置核对；资料迁移状态仍需在下方单独查看。";
    } else if (action === "legacy-scan") {
      if (!legacyAddress()) throw new Error("先选择有迁移接口的当前版本目标与人格。");
      legacyApi = legacyAddress();
      legacyFinding = await migrationRequest("GET", legacyRoute(), legacyQuery());
      legacyPlan = null;
      /* 保留最近一次写入回执：扫描是只读复核，不应抹掉已经发生的迁移结果。 */
    } else if (action === "legacy-plan") {
      if (!legacyAddress()) throw new Error("先选择有迁移接口的当前版本目标与人格。");
      legacyApi = legacyAddress();
      if (!legacyApi || !legacyFinding) throw new Error("先检查旧版来源，再试算迁移。");
      legacyPlan = await migrationRequest("GET", legacyRoute("/plan"), legacyQuery());
      legacyResult = null;
    } else if (action === "legacy-apply") {
      if (!legacyAddress()) throw new Error("先选择有迁移接口的当前版本目标与人格。");
      legacyApi = legacyAddress();
      if (!legacyApi || !legacyPlan?.plan_token) throw new Error("先完成迁移试算，再执行写入。");
      if (!legacyApplyAllConfig && configurationReview()?.supported() && configurationReview().stats().pending) {
        throw new Error("请逐项处理仍需确认的配置，再继续。");
      }
      const selectedFields = configurationReview()?.supported()
        ? (legacyApplyAllConfig ? configurationReview().allSelection() : configurationReview().selection()) : null;
      legacyResult = await migrationRequest("POST", legacyApi + "/apply", {
        plan_token: legacyPlan.plan_token, configuration_scope: legacyConfigScope,
        ...(selectedFields && (!legacyApplyAllConfig || selectedFields.length) ? {selected_fields: selectedFields} : {}),
        ...(legacyRootDraft.trim() ? {v1_root: legacyRootDraft.trim()} : {})});
      if (legacyResult && !legacyResult.errors?.length) {
        legacyConfigSkipped = false;
        legacyConfigNoChanges = false;
      }
      notice = legacyResult?.restart_required
        ? "迁移设置已保存，请重载本插件或重启 AstrBot 后回来核对。"
        : "旧版配置已写入当前版本目标，无需重启；资料迁移可另行选择。";
      if (legacyResult?.completion_recorded === false) {
        notice = "配置写入已返回，但升级进度未能保存；请重新读取状态，暂不要重复执行迁移。";
      } else if (legacyResult?.errors?.length) {
        notice = "部分配置未能迁移；升级进度未标记完成，请检查错误后重新试算。";
      }
      legacyFinding = null;
      legacyPlan = null;
    } else if (action === "legacy-archived-work") {
      legacyApi = legacyAddress();
      if (!legacyApi) throw new Error("请选择当前版本私聊目标和角色。");
      const destination = legacyApi;
      const result = await migrationRequest("GET", destination + "/archived-work", {});
      if (destination !== legacyAddress()) return;
      legacyArchivedWork = {...result, api: destination};
    } else if (action === "legacy-private-memory-plan") {
      legacyApi = legacyAddress();
      if (!legacyApi) throw new Error("请选择当前版本私聊目标和角色。");
      const destination = legacyApi, sourceRoot = legacyRootDraft.trim();
      const result = await migrationRequest("GET", legacyRoute("/private-memory-plan"),
        sourceRoot ? {v1_root: sourceRoot} : {});
      if (destination !== legacyAddress() || sourceRoot !== legacyRootDraft.trim()) return;
      legacyPrivateMemoryPlan = {...result, api: destination};
      legacyPrivateMemoryConfirmed = false;
      legacyPrivateMemoryResult = null;
    } else if (action === "legacy-private-memory-apply") {
      legacyApi = legacyAddress();
      if (!legacyPrivateMemoryConfirmed || legacyPrivateMemoryPlan?.api !== legacyApi) {
        throw new Error("请先核对来源并确认当前角色。");
      }
      const destination = legacyApi, sourceRoot = legacyRootDraft.trim();
      const result = await migrationRequest("POST", destination + "/private-memory-apply", {
        plan_token: legacyPrivateMemoryPlan.plan_token, confirmed: true,
        ...(sourceRoot ? {v1_root: sourceRoot} : {})});
      if (destination !== legacyAddress() || sourceRoot !== legacyRootDraft.trim()) return;
      legacyPrivateMemoryResult = {...result, api: destination};
      notice = "本体私聊记忆已写入历史查询库。";
    } else if (action === "legacy-data-plan") {
      await readLegacyDataPlan();
    } else if (action === "legacy-data-apply") {
      if (!legacyAddress()) throw new Error("先选择有迁移接口的当前版本目标与人格。");
      legacyApi = legacyAddress();
      if (!legacyDataPlan?.plan_token) throw new Error("先完成数据试算，再执行导入。");
      if (legacyMemoryPlanDirty) throw new Error("身份选择已变化，请重新试算后导入。");
      legacyDataResult = null;
      const importMemoryDatabase = Boolean(legacyImportMemoryDatabase
        && legacyMemoryArchiveCanApply(legacyDataPlan?.memory_database));
      legacyDataResult = await migrationRequest("POST", legacyApi + "/data-apply", {
        plan_token: legacyDataPlan.plan_token,
        import_reactions: legacyImportReactions,
        import_group_network: legacyImportGroupNetwork,
        confirm_member_ids: legacyConfirmMemberIds,
        import_journals: legacyImportJournals,
        import_expressions: legacyImportExpressions,
        confirm_expression_mapping: legacyConfirmExpressionMapping,
        confirm_persona_mapping: legacyConfirmPersonaMapping,
        import_worldbook: legacyImportWorldbook,
        import_private_contacts: Boolean(legacyImportPrivateContacts && legacyPrivateContactsReady(legacyDataPlan) > 0 && legacyDataPlan.target_private_available),
        import_reference_images: legacyImportReferenceImages,
        import_wardrobe_items: legacyImportWardrobeItems,
        confirm_reference_persona_mapping: legacyConfirmReferencePersonaMapping,
        import_memory_database: importMemoryDatabase,
        memory_owner_ids: importMemoryDatabase ? [...legacyMemoryOwnerIds].sort() : [],
        memory_group_mappings: importMemoryDatabase
          ? [...legacyMemoryGroupMappings.entries()].sort(([left], [right]) => left.localeCompare(right))
            .map(([owner_id, target]) => ({owner_id, target_id: target.target_id, role_id: target.role_id})) : [],
        confirm_memory_mapping: importMemoryDatabase && legacyConfirmMemoryMapping,
        import_archive: Boolean(legacyImportArchive && legacyArchiveCanApply(legacyDataPlan?.archive)),
        source_persona_id: legacyDataPersonaId,
        source_group_id: legacyDataGroupId,
        ...(legacyRootDraft.trim() ? {v1_root: legacyRootDraft.trim()} : {})});
      legacyImportGroupNetwork = false;
      legacyImportArchive = false;
      legacyImportMemoryDatabase = false;
      legacyDataPlan = null;
      await readLegacyDataPlan();
      legacyDataError = "";
    } else if (action === "legacy-data-batch-plan") {
      const personas = ((legacyDataPlan || legacyDataInventory)?.persona_sources || []).filter(item => item.state === "ready");
      if (!personas.length) throw new Error("没有可安全读取的旧版人格档案。");
      syncLegacyPersonaMappings((legacyDataPlan || legacyDataInventory).persona_sources || []);
      const assignments = legacyPersonaAssignments(personas);
      if (hasDuplicateLegacyPersonaRoles(assignments)) {
        throw new Error("同一个当前版本私聊目标与角色只能映射一次，请先移除重复项。");
      }
      const undecided = personas.some(item => {
        const mapping = legacyPersonaMappings.get(item.persona_id || "");
        return !mapping?.skip && mapping?.destinations.some(destination =>
          !destination.target_id || !destination.role_id || !legacyPersonaRole(destination));
      });
      if (undecided) throw new Error("请补全已添加目的地的目标与角色，或移除该目的地。");
      if (!assignments.length) throw new Error("至少明确添加一个目的地后才能试算。");
      legacyPersonaBatchError = "";
      const sourceById = new Map(personas.map(item => [item.persona_id || "", item]));
      for (let index = 0; index < assignments.length; index += 1) {
        const {sourceId, destination} = assignments[index];
        const role = legacyPersonaRole(destination);
        if (!role) continue;
        const destinationKey = `${destination.target_id}\u0000${destination.role_id}`;
        const destinationCurrent = () => {
          const mapping = legacyPersonaMappings.get(sourceId);
          return !mapping?.skip && mapping?.destinations.includes(destination)
            && destinationKey === `${destination.target_id}\u0000${destination.role_id}`;
        };
        destination.plan = null;
        destination.result = null;
        destination.error = "";
        destination.status = "planning";
        legacyPersonaBatchProgress = `正在试算第 ${index + 1} / ${assignments.length} 个目的地：${sourceById.get(sourceId)?.label || "主角色"}`;
        render();
        try {
          const route = product().relative(role.legacy_api) + "/data-plan";
          const plan = await migrationRequest("GET", route, legacyPersonaQuery(sourceId));
          if (!destinationCurrent()) continue;
          if (!plan?.plan_token || plan.source_persona_id !== sourceId
              || typeof plan.archive?.fingerprint !== "string" || !plan.archive.fingerprint) {
            throw new Error("目标返回的试算计划不完整，请重新读取目标状态。");
          }
          destination.plan = plan;
          destination.status = "ready";
        } catch (error) {
          if (error?.code === "legacy_selection_changed") return;
          if (!destinationCurrent()) continue;
          destination.status = "error";
          destination.error = errorText(error, "该目的地试算失败");
        }
      }
      const planned = assignments.map(item => item.destination);
      const archiveFingerprints = new Set(planned.filter(item => item.plan).map(item => item.plan.archive.fingerprint));
      if (archiveFingerprints.size > 1) {
        for (const destination of planned) {
          destination.plan = null;
          destination.status = "stale";
          destination.error = "试算期间旧版来源清单发生变化";
        }
        legacyPersonaBatchError = "试算期间旧版文件清单发生变化；没有执行导入，请重新试算全部目的地。";
      } else if (planned.some(item => !item.plan)) {
        legacyPersonaBatchError = "部分目的地试算失败；请先修正目标或连接问题，再重新试算。";
      } else {
        legacyPersonaBatchError = "";
      }
      legacyPersonaBatchProgress = `已试算 ${planned.filter(item => item.plan).length} / ${assignments.length} 个目的地；可逐项核对导入范围。`;
    } else if (action === "legacy-data-batch-apply") {
      const personas = ((legacyDataPlan || legacyDataInventory)?.persona_sources || []).filter(item => item.state === "ready");
      const assignments = legacyPersonaAssignments(personas);
      if (!assignments.length || hasDuplicateLegacyPersonaRoles(assignments)) {
        throw new Error("请先为每个人格完成唯一的目标角色映射和试算。");
      }
      if (personas.some(item => {
        const mapping = legacyPersonaMappings.get(item.persona_id || "");
        return !mapping?.skip && mapping?.destinations.some(destination => !destination.plan?.plan_token
          || destination.error || !legacyPersonaRole(destination));
      })) throw new Error("请先为全部未跳过的人格的每个目的地完成试算和复核。");
      const runnable = assignments.filter(item => legacyPersonaBatchCanApply(item.destination));
      if (!runnable.length) throw new Error("没有已勾选且可导入的数据项。");
      legacyPersonaBatchError = "";
      for (let index = 0; index < runnable.length; index += 1) {
        const {sourceId, persona, destination} = runnable[index];
        const role = legacyPersonaRole(destination);
        destination.status = "applying";
        destination.error = "";
        legacyPersonaBatchProgress = `正在执行第 ${index + 1} / ${runnable.length} 个目的地：${legacyPersonaLabel(persona, "主角色")}`;
        render();
        const route = product().relative(role.legacy_api);
        let applyStarted = false;
        try {
          const fresh = await migrationRequest("GET", route + "/data-plan", legacyPersonaQuery(sourceId));
          if (!fresh?.plan_token || fresh.source_persona_id !== sourceId
              || fresh.source_fingerprint !== destination.plan.source_fingerprint
              || fresh.archive?.fingerprint !== destination.plan.archive?.fingerprint) {
            destination.plan = null;
            destination.status = "stale";
            destination.error = "旧版来源在试算后发生变化，请重新试算";
            legacyPersonaBatchError = `${legacyPersonaLabel(persona, "该人格")} 的来源已变化；本项目的当前版本目标及后续目的地未执行。`;
            break;
          }
          destination.plan = fresh;
          const worldbook = fresh.worldbook || {};
          const journals = fresh.journals || {};
          const references = fresh.reference_images || {};
          const wardrobe = fresh.wardrobe_items || {};
          const worldbookReady = destination.include_worldbook && (
            (Number(worldbook.general_count) > 0 && !worldbook.base_entries_matches)
            || (destination.confirm_member_ids && Number(worldbook.member_count) > 0
              && !worldbook.base_members_matches));
          applyStarted = true;
          const result = await migrationRequest("POST", route + "/data-apply", {
            plan_token: fresh.plan_token,
            import_reactions: destination.include_reactions && Number(fresh.reaction_assets?.batch_count) > 0,
            import_group_network: false,
            confirm_member_ids: destination.confirm_member_ids,
            import_expressions: Boolean(destination.include_expressions && fresh.expressions?.remaining),
            confirm_expression_mapping: destination.confirm_expression_mapping === true,
            import_journals: destination.include_journals && Number(journals.batch_count) > 0,
            confirm_persona_mapping: destination.confirm_persona_mapping,
            import_worldbook: Boolean(worldbookReady),
            import_private_contacts: Boolean(destination.include_private_contacts && legacyPrivateContactsReady(fresh) > 0),
            import_reference_images: destination.include_reference_images
              && Number(references.batch_count) > 0,
            import_wardrobe_items: destination.include_wardrobe_items
              && Number(wardrobe.batch_count) > 0,
            confirm_reference_persona_mapping: destination.confirm_reference_persona_mapping,
            import_memory_database: false,
            import_archive: false,
            source_persona_id: sourceId,
            source_group_id: "",
            ...(legacyRootDraft.trim() ? {v1_root: legacyRootDraft.trim()} : {})});
          if (!result || result.source_persona_id !== sourceId || !Array.isArray(result.errors)) {
            throw new Error("目标未返回可核对的导入回执");
          }
          destination.result = result;
          const remaining = (Number(result.reaction_remaining) || 0)
            + (Number(result.journal_remaining) || 0)
            + (Number(result.journal_derivatives_remaining) || 0)
            + (Number(result.reference_images?.reference_remaining) || 0)
            + (Number(result.reference_images?.wardrobe_remaining) || 0);
          if (result.errors.length) {
            destination.status = "error";
            destination.error = `${result.errors.length} 项未成功，已停止后续目的地`;
            legacyPersonaBatchError = `${legacyPersonaLabel(persona, "该人格")} 的目的地导入有失败项；本项目的当前版本目标及后续目的地未执行。`;
            break;
          }
          destination.status = remaining ? "partial" : "imported";
        } catch (error) {
          destination.status = applyStarted ? "unknown" : "error";
          if (applyStarted) destination.plan = null;
          destination.error = errorText(error, applyStarted ? "导入响应未确认" : "试算复核失败");
          legacyPersonaBatchError = applyStarted
            ? `${legacyPersonaLabel(persona, "该人格")} 的导入结果未确认；本项目的当前版本目标及后续目的地未执行，请先核对当前目标。`
            : `${legacyPersonaLabel(persona, "该人格")} 的来源复核失败；本项目的当前版本目标及后续目的地未执行。`;
          break;
        }
      }
      legacyPersonaBatchProgress = "本轮目的地执行已停止；各目标结果已分别保留。若有分批剩余，请重新试算后继续。";
    } else if (action === "legacy-group-data-plan") {
      const choices = legacyGroupSelection();
      if (!choices.api || !choices.persona || !legacyDataGroupId) {
        throw new Error("请先选定可读取的旧版人格和旧版群记录，再明确选择当前版本群目标与角色。");
      }
      const query = legacyPersonaQuery(legacyDataPersonaId);
      query.source_group_id = legacyDataGroupId;
      const selectionKey = legacyActionKey(action);
      const plan = await migrationRequest("GET", choices.api + "/plan", query);
      if (selectionKey !== legacyActionKey(action)) return;
      legacyGroupPlan = plan;
      legacyGroupResult = null;
      legacyGroupError = "";
      legacyGroupConfirmMapping = false;
      legacyGroupConfirmMemberIds = false;
      legacyGroupConfirmExpressionMapping = false;
    } else if (action === "legacy-group-data-apply") {
      const choices = legacyGroupSelection();
      if (!choices.api || !legacyGroupPlan?.plan_token) throw new Error("先完成当前版本群目标的映射试算。");
      legacyGroupResult = await migrationRequest("POST", choices.api + "/apply", {
        plan_token: legacyGroupPlan.plan_token,
        source_persona_id: legacyDataPersonaId,
        source_group_id: legacyDataGroupId,
        import_group_network: Boolean(legacyGroupImportNetwork && legacyGroupPlan.source_group_network),
        import_group_worldbook: Boolean(legacyGroupImportWorldbook
          && Number(legacyGroupPlan.group_worldbook?.entries) + Number(legacyGroupPlan.group_worldbook?.members) > 0),
        import_group_expression: Boolean(legacyGroupImportExpressions
          && legacyGroupPlan.group_expression?.available
          && Number(legacyGroupPlan.group_expression?.remaining) > 0),
        confirm_expression_mapping: legacyGroupConfirmExpressionMapping,
        confirm_member_ids: legacyGroupConfirmMemberIds,
        confirm_group_mapping: legacyGroupConfirmMapping,
        ...(legacyRootDraft.trim() ? {v1_root: legacyRootDraft.trim()} : {})});
      legacyGroupError = "";
    }
    migrationProgress = "";
  }

  async function act(action) {
    const migrationAction = action.startsWith("legacy-");
    if (migrationAction ? legacyActionDisabled(action) : busy) return;
    const activeGeneration = mountGeneration;
    const job = migrationAction ? {key: legacyActionKey(action), progress: ""} : null;
    if (job) legacyJobs.set(action, job);
    else busy = true;
    notice = "";
    render();
    try {
      if (action === "confirm-binding") {
        await api("POST", "/binding/confirm", {});
      } else if (action === "prepare-upgrade") {
        const result = await api("POST", "/upgrade", {confirmed: true, plan_token: state?.upgrade?.plan_token});
        if (!result.started) throw new Error(result.startup_error || "旧版连接已保存，请核对运行状态。");
        await refreshDirectoryQuietly();
        window.navigate?.("manage", "data", "migration");
        return;
      } else if (action === "prepare") {
        await api("POST", "/prepare", {});
      } else if (action === "save-model") {
        const saved = await api("POST", "/model", {provider_id: modelDraft});
        modelTaskDrafts = {};
        notice = saved?.restart_required || saved?.data?.restart_required
          ? "模型已保存；当前宿主仍在运行，重载插件后使用新模型。"
          : "已初始化模型；任务路由可直接选择 AstrBot 当前已加载的 Provider。";
      } else if (action.startsWith("save-task:")) {
        await saveModelTask(action.slice("save-task:".length));
      } else if (action === "generate-profile") {
        await generateProfileDraft();
      } else if (action === "save-profile") {
        await saveProfileDraft();
      } else if (action === "complete-step" || action === "skip-step") {
        await api("POST", "/complete", {step: state.step, skipped: action === "skip-step"});
      } else if (action === "proactive-test") {
        const result = await api("POST", "/proactive-test", {enable_proactive: true});
        if (mountGeneration !== activeGeneration) return;
        state = {...state, ready: result.ready, proactive_test: {...state?.proactive_test,
          state: result.state, ...(result.attempt_id ? {attempt_id: result.attempt_id} : {})}};
        proactiveOptIn = false;
        notice = "主动联系已启动，正在等待模型生成、策略检查和投递回执。";
        render();
        if (!result.ready && result.state === "started") void watchProactive();
        return;
      } else if (action === "confirm-test-received") {
        const result = await api("POST", "/proactive-test/confirm", {attempt_id: state.proactive_test.attempt_id});
        if (mountGeneration !== activeGeneration) return;
        proactiveWatcher = null;
        state = {...state, ready: result.ready, ...(result.ready ? {step: "complete"} : {}),
          proactive_test: result.proactive_test || {...state.proactive_test, state: result.state}};
        notice = "已记录你收到测试消息，首次设置已完成。";
        render();
        return;
      } else if (action === "reconnect-host") {
        await api("POST", "/host/start", {});
        notice = "当前版本已重新连接并开始接收消息。";
      } else if (action === "open-migration") {
        /* 升级引导：去拓展页的「资料与维护 → 旧版数据迁移」走完整流程。 */
        window.navigate?.("manage", "data", "migration");
        return;
      } else if (["legacy-scan", "legacy-plan", "legacy-apply", "legacy-auto", "legacy-data-plan", "legacy-data-apply",
        "legacy-data-batch-plan", "legacy-data-batch-apply", "legacy-group-data-plan",
        "legacy-group-data-apply", "legacy-upgrade-confirm", "legacy-private-memory-plan", "legacy-private-memory-apply", "legacy-archived-work"].includes(action)) {
        await runLegacy(action);
      }
      if (migrationOnly && action.startsWith("legacy-")) return;
      if (action === "refresh") {
        await refresh();
      } else {
        const result = await api("GET", "/status");
        state = result;
        modelDraft = result?.model?.provider_id || "";
        await loadStepData(result);
        if (result.ready) notice = "";
        render();
        if (result?.proactive_test?.state === "started") void watchProactive();
      }
    } catch (error) {
      if (mountGeneration !== activeGeneration) return;
      if (error?.code === "legacy_selection_changed" || job && (legacyJobs.get(action) !== job
          || job.key !== legacyActionKey(action))) return;
      migrationProgress = "";
      if (action.startsWith("legacy-")) {
        legacyLastErrorCode = typeof error?.code === "string" ? error.code : "";
        const message = legacyDisplayText(errorText(error, "旧版迁移未完成，请核对当前版本目标后重试。"));
        if (action.startsWith("legacy-data-")) {
          legacyDataError = message;
          if (action === "legacy-data-apply") legacyDataPlan = null;
        } else if (action.startsWith("legacy-group-data-")) {
          legacyGroupError = message;
          if (action === "legacy-group-data-apply") legacyGroupPlan = null;
        } else {
          legacyError = message;
        }
        if (action === "legacy-apply") legacyPlan = null;
      }
      notice = action.startsWith("legacy-")
        ? legacyDisplayText(errorText(error, "操作未完成，请重新读取状态。"))
        : errorText(error, "操作未完成，请重新读取状态。");
      if (!migrationOnly) {
        try { state = await api("GET", "/status"); } catch { state = null; }
      }
      render();
    } finally {
      if (mountGeneration === activeGeneration) {
        if (job) {
          if (legacyJobs.get(action) === job) legacyJobs.delete(action);
        } else busy = false;
        migrationProgress = legacyActiveJobs().map(([, activeJob]) => activeJob.progress).filter(Boolean).join(" ");
        if (root) render();
      }
    }
  }

  /* 进入第 2 步时自动读取来源、配置对照和迁移范围：默认路径不再要求先挑
     旧版人格或群，也不需要用户逐一触发读取。 */
  async function loadLegacyReview() {
    if (!legacyAddress() || legacyWizardExecuting) return;
    if (!legacyFinding) {
      await act("legacy-scan");
      if (!legacyFinding) return;
    }
    if (!legacyPlan && !legacyResult && !legacyActionBusy("legacy-plan")) await act("legacy-plan");
    if (!legacyScopeChecked && !legacyScopeBusy) await readLegacyScope();
  }

  async function advanceLegacyWizard() {
    if (legacyWizardExecuting) return;
    legacyError = "";
    if (legacyWizardStep === 1) {
      if (!legacyRoleReady()) {
        legacyError = legacySelection().role
          ? "这个角色还没有人格；请新建一个至少包含一个人格的角色。"
          : "请先创建或选择一个接收旧版资料的角色。";
        render();
        return;
      }
      legacyWizardStep = 2;
      legacyView = "review";
      render();
      void loadLegacyReview();
      return;
    }
    if (legacyWizardStep === 2) {
      if (!legacyRoleReady()) {
        legacyWizardStep = 1;
        legacyError = "接收角色已不可用，请重新设置。";
        render();
        return;
      }
      /* 只切到迁移方式确认页，不在这里执行任何写入。 */
      legacyWizardStep = 3;
      legacyView = "review";
      render();
      return;
    }
    if (legacyWizardStep === 3) {
      if (!legacyBatchResult && !legacyResult && !legacyConfigNoChanges) return;
      legacyWizardStep = 4;
      legacyView = "review";
      render();
    }
  }

  function setLegacyWizardStep(step) {
    const next = Number(step);
    if (!Number.isInteger(next) || next < 1 || next > MIGRATION_STEPS.length
        || !legacyWizardReachable(next) || next === legacyWizardStep) return;
    legacyWizardStep = next;
    legacyView = "review";
    legacyManualOpen = false;
    if (next === 2) {
      legacyMappingView = "current";
      render();
      void loadLegacyReview();
      return;
    }
    render();
  }

  function onClick(event) {
    const configAction = event.target.closest?.("[data-migration-config-action]");
    if (configAction) {
      configurationReview()?.click(configAction.dataset, legacyWriting());
      render();
      return;
    }
    const wizardStep = event.target.closest?.("[data-setup-legacy-step]")?.dataset.setupLegacyStep;
    if (wizardStep) {
      setLegacyWizardStep(wizardStep);
      return;
    }
    const wizardAction = event.target.closest?.("[data-setup-legacy-wizard]")?.dataset.setupLegacyWizard;
    if (wizardAction === "next") { void advanceLegacyWizard(); return; }
    if (wizardAction === "run") { void runLegacyOneClick(); return; }
    if (wizardAction === "create-role") {
      legacyRoleEditorOpen = !legacyRoleEditorOpen;
      legacyRoleSwitcherOpen = legacyRoleEditorOpen;
      legacyRoleSaveMessage = "";
      render();
      return;
    }
    if (wizardAction === "switch-role") {
      legacyRoleSwitcherOpen = !legacyRoleSwitcherOpen;
      legacyRoleEditorOpen = false;
      render();
      return;
    }
    if (wizardAction === "save-role") { void saveLegacyRole(); return; }
    if (wizardAction === "refresh-roles") { void refreshLegacyRoles(); return; }
    if (wizardAction === "manual") {
      legacyManualOpen = true;
      legacyWizardStep = 2;
      legacyView = "review";
      render();
      return;
    }
    if (wizardAction === "back-to-oneclick") {
      legacyManualOpen = false;
      render();
      return;
    }
    if (wizardAction === "back") {
      if (legacyWizardStep > 1) setLegacyWizardStep(legacyWizardStep - 1);
      return;
    }
    const detailKey = event.target.closest?.("[data-setup-legacy-detail]")?.dataset.setupLegacyDetail;
    if (detailKey) {
      const keys = detailKey.startsWith("audit-") ? ["review-configuration", "config-audit", detailKey] : [detailKey];
      for (const key of keys) {
        const details = root?.querySelector(`[data-migration-detail="${key}"]`);
        if (details) details.open = true;
        legacyOpenDetails.add(key);
      }
      render();
      root?.querySelector(`[data-migration-detail="${detailKey}"] > summary`)?.focus?.();
      return;
    }
    const settingsKey = event.target.closest?.("[data-setup-legacy-settings]")?.dataset.setupLegacySettings;
    if (settingsKey && !legacyWriting() && window.CompanionMigrationConfigReview?.editor(settingsKey)) {
      legacySettingsKey = settingsKey;
      render();
      return;
    }
    if (event.target.closest?.("[data-setup-legacy-settings-back]")) {
      // Re-read the plan after editing so an old plan cannot overwrite a setting
      // saved in the embedded workbench. Data receipts and choices stay intact.
      if (legacySettingsEditor?.state?.instances?.some(instance => instance.state?.busy)
          || window.CompanionMigrationConfigReview?.editor(legacySettingsKey)?.mount === "models" && window.CompanionModelsPage?.isBusy?.()) {
        const message = root?.querySelector("#setupLegacySettingsNotice");
        if (message) message.textContent = "正在保存设置，请等待保存结果后返回。";
        return;
      }
      stopLegacySettings();
      legacySettingsKey = "";
      legacyPlan = null;
      legacyDataPlan = null;
      legacyGroupPlan = null;
      legacyWizardStep = 2;
      legacyView = "review";
      render();
      if (legacyFinding && legacyAddress()) void act("legacy-plan");
      return;
    }
    const mappingShortcut = event.target.closest?.("[data-setup-legacy-open-mapping]")?.dataset.setupLegacyOpenMapping;
    if (["current", "personas", "groups", "memory"].includes(mappingShortcut)) {
      legacyManualOpen = true;
      legacyView = "mapping";
      legacyMappingView = mappingShortcut;
      legacyWizardStep = 2;
      render();
      if (!legacyDataInventory && legacyAddress()) void act("legacy-data-plan");
      return;
    }
    const migrationView = event.target.closest?.("[data-setup-legacy-view]")?.dataset.setupLegacyView;
    if (["review", "mapping"].includes(migrationView)) {
      legacyView = migrationView;
      legacyManualOpen = migrationView === "mapping";
      legacyWizardStep = migrationView === "mapping" ? 2
        : (legacyBatchResult || legacyResult || legacyConfigNoChanges ? 4 : 2);
      if (migrationView === "review") legacyMappingView = "current";
      render();
      if (migrationView === "mapping") {
        if (!legacyFinding && legacyAddress()) void loadLegacyReview();
        if (!legacyDataInventory && legacyAddress()) void act("legacy-data-plan");
      }
      return;
    }
    const batchRun = event.target.closest?.("[data-setup-legacy-run]")?.dataset.setupLegacyRun;
    if (batchRun) {
      if (batchRun === "scope") void readLegacyScope();
      else if (batchRun === "batch") void runLegacyOneClick();
      return;
    }
    const mappingView = event.target.closest?.("[data-setup-legacy-mapping-view]")?.dataset.setupLegacyMappingView;
    if (["current", "personas", "groups", "memory"].includes(mappingView)) {
      legacyManualOpen = true;
      legacyMappingView = mappingView;
      legacyWizardStep = 2;
      legacyView = "mapping";
      render();
      return;
    }
    const addDestination = event.target.closest?.("[data-setup-legacy-batch-add]");
    if (addDestination) {
      const sourceId = addDestination.dataset.setupLegacyBatchAdd || "";
      const mapping = legacyPersonaMappings.get(sourceId);
      if (mapping && !mapping.skip && !legacyWriting()) {
        mapping.destinations.push(newLegacyPersonaDestination());
        legacyPersonaBatchError = "";
        render();
      }
      return;
    }
    const removeDestination = event.target.closest?.("[data-setup-legacy-batch-remove]");
    if (removeDestination) {
      const sourceId = removeDestination.dataset.setupLegacyBatchRemove || "";
      const mapping = legacyPersonaMappings.get(sourceId);
      if (mapping && !legacyWriting()) {
        mapping.destinations = mapping.destinations.filter(item =>
          item.destination_id !== removeDestination.dataset.destinationId);
        legacyPersonaBatchError = "";
        render();
      }
      return;
    }
    const legacyAction = event.target.closest?.("[data-setup-legacy-action]")?.dataset.setupLegacyAction;
    if (legacyAction) { act(legacyAction); return; }
    if (busy) return;
    const openBlock = event.target.closest?.("[data-setup-open-block]");
    if (openBlock && (state?.ready || state?.prepared)) {
      const wanted = openBlock.dataset.setupOpenBlock;
      advancedBlock = ADVANCED_BLOCKS.some(item => item.id === wanted) ? wanted : ADVANCED_BLOCKS[0].id;
      advancedOpen = true;
      render();
      return;
    }
    const advancedBlockButton = event.target.closest?.("[data-setup-advanced-block]");
    if (advancedBlockButton) {
      advancedBlock = ADVANCED_BLOCKS.some(item => item.id === advancedBlockButton.dataset.setupAdvancedBlock)
        ? advancedBlockButton.dataset.setupAdvancedBlock : "understanding";
      render();
      return;
    }
    const action = event.target.closest?.("[data-setup-action]")?.dataset.setupAction;
    if (action === "retry-model-routes") { void retryQuickModels(); return; }
    if (action === "advanced-open" && (state?.ready || state?.prepared)) {
      advancedOpen = true;
      render();
      return;
    }
    if (action === "advanced-close") {
      advancedOpen = false;
      render();
      return;
    }
    if (action) act(action);
  }

  function onInput(event) {
    if (event.target.dataset?.setupLegacy === "new-role-name") {
      legacyNewRoleName = event.target.value;
      legacyRoleSaveMessage = "";
      return;
    }
    if (!event.target.matches?.("[data-migration-config-search]")) return;
    const position = event.target.selectionStart;
    configurationReview()?.search(event.target.value);
    render();
    const search = root?.querySelector("[data-migration-config-search]");
    search?.focus();
    if (position !== null) search?.setSelectionRange?.(position, position);
  }

  function onChange(event) {
    const input = event.target;
    if (legacyRoleSaveBusy && ["bot", "role", "new-persona", "new-role-name"].includes(input?.dataset?.setupLegacy)) {
      render();
      return;
    }
    const migrationInput = Object.keys(input?.dataset || {}).some(key => key.startsWith("setupLegacy"));
    if (migrationInput ? legacyWriting() : busy) { render(); return; }
    if (input?.dataset?.setupLegacy === "private-memory-confirm") {
      legacyPrivateMemoryConfirmed = Boolean(input.checked);
      render();
      return;
    }
    if (["target", "bot", "role", "root"].includes(input?.dataset?.setupLegacy)) {
      legacyDataRequestId++;
      legacyDataRefreshing = false;
      legacyScopeRequestId++;
      legacyScope = null;
      legacyScopeBusy = false;
      legacyScopeChecked = false;
      legacyScopeError = "";
      legacyBatchResult = null;
      legacyResult = null;
      legacyWizardExecuting = false;
      legacyOneClickConfigError = "";
      legacyConfigNoChanges = false;
      legacyLastErrorCode = "";
      legacyApplyAllConfig = false;
      legacyConfigSkipped = false;
      legacyManualOpen = false;
      legacyRoleSwitcherOpen = false;
      legacyRoleEditorOpen = false;
      legacyMigrationFinished = false;
      legacyPrivateMemoryPlan = legacyPrivateMemoryResult = null;
      legacyPrivateMemoryConfirmed = false;
      legacyArchivedWork = null;
      legacyDataInventory = null;
      legacyMemoryOwnerIds = new Set();
      legacyMemoryGroupMappings = new Map();
      legacyMemoryGroupMappingSeeded = legacyMemoryPlanDirty = legacyConfirmMemoryMapping = false;
      legacyGroupPlan = legacyGroupResult = null;
      legacyGroupConfirmMapping = legacyGroupConfirmMemberIds = legacyGroupConfirmExpressionMapping = false;
      legacyImportGroupNetwork = legacyImportJournals = legacyImportExpressions = false;
      legacyConfirmExpressionMapping = false;
    }
    if (input?.dataset?.setupLegacy === "config-mode") {
      legacyConfigSkipped = input.value === "keep";
      legacyConfigNoChanges = false;
      legacyOneClickConfigError = "";
      legacyError = "";
      render();
      return;
    }
    if (input?.dataset?.setupLegacy === "target") {
      legacyWizardStep = 1;
      legacyConfigSkipped = false;
      legacyMigrationFinished = false;
      legacyTargetId = input.value;
      legacyBotKey = legacyBotKeyOf(legacyDirectoryTargets().find(item => item.target_id === input.value));
      legacyRoleId = "";
      legacyApi = "";
      legacyFinding = legacyPlan = legacyResult = null;
      legacyDataPlan = legacyDataResult = null;
      legacyDataGroupId = "";
      legacyDataPersonaId = "";
      legacyImportWorldbook = false;
      legacyImportReferenceImages = false;
      legacyImportWardrobeItems = false;
      legacyConfirmReferencePersonaMapping = false;
      legacyConfirmMemberIds = false;
      legacyConfirmPersonaMapping = false;
      legacyError = "";
      legacyDataError = "";
      render();
    }
    if (input?.dataset?.setupLegacy === "bot") {
      legacyWizardStep = 1;
      legacyConfigSkipped = false;
      legacyMigrationFinished = false;
      legacyBotKey = input.value;
      legacyNewPersonaIds = new Set();
      legacyNewRoleName = legacyRoleSaveMessage = "";
      const group = legacyBotGroups().find(item => item.key === legacyBotKey);
      const readyRoles = (group?.roles || []).filter(item => item.role.legacy_api);
      const initial = readyRoles[0] || null;
      legacyTargetId = readyRoles.length === 1 ? initial?.target.target_id || "" : "";
      legacyRoleId = readyRoles.length === 1 ? initial?.role.role_id || "" : "";
      legacyApi = "";
      legacyFinding = legacyPlan = legacyResult = null;
      legacyDataPlan = legacyDataResult = null;
      legacyDataGroupId = legacyDataPersonaId = "";
      legacyImportWorldbook = legacyImportReferenceImages = legacyImportWardrobeItems = false;
      legacyConfirmReferencePersonaMapping = legacyConfirmMemberIds = legacyConfirmPersonaMapping = false;
      legacyError = legacyDataError = "";
      render();
      return;
    }
    if (input?.dataset?.setupLegacy === "new-persona") {
      if (input.checked) legacyNewPersonaIds.add(input.value);
      else legacyNewPersonaIds.delete(input.value);
      legacyRoleSaveMessage = "";
      render();
      return;
    }
    if (input?.dataset?.setupLegacy === "new-role-name") {
      legacyNewRoleName = input.value;
      return;
    }
    if (input?.dataset?.setupLegacy === "root") {
      legacyWizardStep = 2;
      legacyConfigSkipped = false;
      legacyConfigNoChanges = false;
      legacyMigrationFinished = false;
      legacyRootDraft = input.value;
      legacyDataPersonaId = legacyDataGroupId = "";
      legacyFinding = legacyPlan = legacyResult = null;
      legacyDataPlan = legacyDataResult = null;
      legacyDataInventory = null;
      legacyConfirmReferencePersonaMapping = false;
      legacyMemoryOwnerIds = new Set();
      legacyMemoryGroupMappings = new Map();
      legacyMemoryGroupMappingSeeded = false;
      legacyMemoryPlanDirty = false;
      legacyConfirmMemoryMapping = false;
      legacyPersonaMappings = new Map();
      legacyPersonaBatchError = "";
      legacyPersonaBatchProgress = "";
      legacyGroupPlan = legacyGroupResult = null;
      legacyGroupError = "";
      legacyError = "";
      legacyDataError = "";
      render();
      return;
    }
    if (input?.dataset?.setupLegacy === "configuration-scope") {
      legacyWizardStep = 2;
      legacyConfigSkipped = false;
      legacyConfigScope = ["plugin", "proactive", "reality", "all"].includes(input.value) ? input.value : "plugin";
      legacyFinding = legacyPlan = legacyResult = null;
      legacyError = "";
      render();
      void loadLegacyReview();
    }
    if (input?.dataset?.setupLegacy === "role") {
      legacyWizardStep = 1;
      legacyConfigSkipped = false;
      legacyConfigNoChanges = false;
      legacyMigrationFinished = false;
      legacyRoleSwitcherOpen = false;
      legacyRoleEditorOpen = false;
      legacyRoleSaveMessage = "";
      const group = legacySelectedBotGroup();
      const selected = group?.roles.find(item => item.role.role_id === input.value && item.role.legacy_api);
      legacyRoleId = selected?.role.role_id || input.value;
      if (selected) legacyTargetId = selected.target.target_id;
      legacyApi = "";
      legacyFinding = legacyPlan = legacyResult = null;
      legacyDataPlan = legacyDataResult = null;
      legacyDataGroupId = "";
      legacyDataPersonaId = "";
      legacyImportWorldbook = false;
      legacyImportReferenceImages = false;
      legacyImportWardrobeItems = false;
      legacyConfirmReferencePersonaMapping = false;
      legacyConfirmMemberIds = false;
      legacyConfirmPersonaMapping = false;
      legacyError = "";
      legacyDataError = "";
      legacyApi = legacyAddress();
      render();
      if (legacyRoleReady()) void loadLegacyReview();
      return;
    }
    if (input?.dataset?.setupLegacy === "data-group") {
      legacyWizardStep = 2;
      legacyDataGroupId = input.value;
      legacyImportGroupNetwork = Boolean(input.value && ((legacyDataPlan || legacyDataInventory)?.group_networks || []).some(item => item.source_group_id === input.value));
      invalidateLegacyDataSelection();
      render();
      void act("legacy-data-plan");
      return;
    }
    if (input?.dataset?.setupLegacy === "data-persona") {
      legacyWizardStep = 2;
      legacyDataPersonaId = input.value;
      legacyDataGroupId = "";
      legacyImportGroupNetwork = false;
      invalidateLegacyDataSelection();
      legacyImportJournals = false;
      legacyImportExpressions = false;
      legacyConfirmExpressionMapping = false;
      legacyConfirmPersonaMapping = false;
      legacyImportReferenceImages = false;
      legacyImportWardrobeItems = false;
      legacyConfirmReferencePersonaMapping = false;
      legacyDataError = "";
      render();
      void act("legacy-data-plan");
      return;
    }
    if (input?.dataset?.setupLegacy === "group-target") {
      legacyGroupTargetId = input.value;
      legacyGroupRoleId = "";
      legacyGroupPlan = legacyGroupResult = null;
      legacyGroupError = "";
      legacyGroupConfirmMapping = false;
      legacyGroupConfirmMemberIds = false;
      legacyGroupConfirmExpressionMapping = false;
      render();
    }
    if (input?.dataset?.setupLegacy === "group-role") {
      legacyGroupRoleId = input.value;
      legacyGroupPlan = legacyGroupResult = null;
      legacyGroupError = "";
      legacyGroupConfirmMapping = false;
      legacyGroupConfirmMemberIds = false;
      legacyGroupConfirmExpressionMapping = false;
      render();
    }
    if (input?.dataset?.setupLegacy === "group-import-network") {
      legacyGroupImportNetwork = Boolean(input.checked);
      legacyGroupConfirmMemberIds = false;
      render();
    }
    if (input?.dataset?.setupLegacy === "group-import-worldbook") {
      legacyGroupImportWorldbook = Boolean(input.checked);
      legacyGroupConfirmMemberIds = false;
      render();
    }
    if (input?.dataset?.setupLegacy === "group-import-expressions") {
      legacyGroupImportExpressions = Boolean(input.checked);
      legacyGroupConfirmExpressionMapping = false;
      render();
    }
    if (input?.dataset?.setupLegacy === "group-confirm-mapping") {
      legacyGroupConfirmMapping = Boolean(input.checked);
      render();
    }
    if (input?.dataset?.setupLegacy === "group-confirm-member-ids") {
      legacyGroupConfirmMemberIds = Boolean(input.checked);
      render();
    }
    if (input?.dataset?.setupLegacy === "group-confirm-expression-mapping") {
      legacyGroupConfirmExpressionMapping = Boolean(input.checked);
      render();
    }
    if (input?.dataset?.setupLegacyBatchTarget !== undefined) {
      const mapping = legacyPersonaMappings.get(input.dataset.personaId || "");
      const destination = mapping?.destinations.find(item => item.destination_id === input.dataset.destinationId);
      if (destination) {
        destination.target_id = input.value;
        destination.role_id = "";
        destination.persona_match_suggestion = false;
        destination.include_reactions = true;
        destination.include_worldbook = false;
        destination.include_journals = false;
        destination.include_expressions = false;
        destination.confirm_expression_mapping = false;
        destination.confirm_member_ids = false;
        destination.confirm_persona_mapping = false;
        destination.include_reference_images = false;
        destination.include_wardrobe_items = false;
        destination.confirm_reference_persona_mapping = false;
        destination.plan = destination.result = null;
        destination.status = input.value ? "mapped" : "unmapped";
        destination.error = "";
        legacyPersonaBatchError = "";
      }
      render();
    }
    if (input?.dataset?.setupLegacyBatchRole !== undefined) {
      const mapping = legacyPersonaMappings.get(input.dataset.personaId || "");
      const destination = mapping?.destinations.find(item => item.destination_id === input.dataset.destinationId);
      if (destination) {
        destination.persona_match_suggestion = false;
        if (legacyPersonaRoleTaken(destination.target_id, input.value,
            input.dataset.personaId || "", destination.destination_id)) {
          destination.role_id = "";
          destination.plan = destination.result = null;
          destination.status = "unmapped";
          destination.error = "该当前版本目标角色已映射到另一个目的地，请选另一个角色。";
          legacyPersonaBatchError = "已阻止重复的当前版本目标角色映射。";
        } else {
          destination.role_id = input.value;
          destination.include_reactions = true;
          destination.include_worldbook = false;
          destination.include_journals = false;
        destination.include_expressions = false;
        destination.confirm_expression_mapping = false;
          destination.confirm_member_ids = false;
          destination.confirm_persona_mapping = false;
          destination.include_reference_images = false;
          destination.include_wardrobe_items = false;
          destination.confirm_reference_persona_mapping = false;
          destination.plan = destination.result = null;
          destination.status = input.value ? "mapped" : "unmapped";
          destination.error = "";
          legacyPersonaBatchError = "";
        }
      }
      render();
    }
    if (input?.dataset?.setupLegacyBatchSkip !== undefined) {
      const mapping = legacyPersonaMappings.get(input.dataset.personaId || "");
      if (mapping) {
        mapping.skip = Boolean(input.checked);
        for (const destination of mapping.destinations) {
          destination.plan = destination.result = null;
          destination.status = destination.target_id && destination.role_id ? "mapped" : "unmapped";
          destination.error = "";
        }
        legacyPersonaBatchError = "";
      }
      render();
    }
    if (input?.dataset?.setupLegacyBatchOption) {
      const mapping = legacyPersonaMappings.get(input.dataset.personaId || "");
      const destination = mapping?.destinations.find(item => item.destination_id === input.dataset.destinationId);
      if (destination) {
        const property = {
          expressions: "include_expressions", "confirm-expressions": "confirm_expression_mapping",
          reactions: "include_reactions", worldbook: "include_worldbook", contacts: "include_private_contacts", journals: "include_journals",
          "confirm-members": "confirm_member_ids", "confirm-journals": "confirm_persona_mapping",
          "reference-images": "include_reference_images", "wardrobe-items": "include_wardrobe_items",
          "confirm-reference-persona": "confirm_reference_persona_mapping",
        }[input.dataset.setupLegacyBatchOption];
        if (property) destination[property] = Boolean(input.checked);
        if (input.dataset.setupLegacyBatchOption === "journals" && !input.checked) {
          destination.confirm_persona_mapping = false;
        }
        if (input.dataset.setupLegacyBatchOption === "worldbook" && !input.checked) {
          destination.confirm_member_ids = false;
        }
        if (input.dataset.setupLegacyBatchOption === "reference-images"
            || input.dataset.setupLegacyBatchOption === "wardrobe-items") {
          destination.confirm_reference_persona_mapping = false;
        }
        if (input.dataset.setupLegacyBatchOption === "confirm-reference-persona" && !input.checked) {
          destination.confirm_reference_persona_mapping = false;
        }
      }
      render();
    }
    if (input?.dataset?.setupLegacy === "import-reactions") {
      legacyImportReactions = input.checked;
      render();
    }
    if (input?.dataset?.setupLegacy === "import-group-network") {
      legacyImportGroupNetwork = input.checked;
      legacyConfirmMemberIds = false;
      render();
    }
    if (input?.dataset?.setupLegacy === "import-private-contacts") {
      legacyImportPrivateContacts = input.checked;
      render();
    }
    if (input?.dataset?.setupLegacy === "import-worldbook") {
      legacyImportWorldbook = input.checked;
      legacyConfirmMemberIds = false;
      render();
    }
    if (input?.dataset?.setupLegacy === "import-expressions") {
      legacyImportExpressions = input.checked;
      legacyConfirmExpressionMapping = false;
      render();
    }
    if (input?.dataset?.setupLegacy === "confirm-expression-mapping") {
      legacyConfirmExpressionMapping = input.checked;
      render();
    }
    if (input?.dataset?.setupLegacy === "import-journals") {
      legacyImportJournals = input.checked;
      legacyConfirmPersonaMapping = false;
      render();
    }
    if (input?.dataset?.setupLegacy === "import-reference-images") {
      legacyImportReferenceImages = Boolean(input.checked);
      legacyConfirmReferencePersonaMapping = false;
      render();
    }
    if (input?.dataset?.setupLegacy === "import-wardrobe-items") {
      legacyImportWardrobeItems = Boolean(input.checked);
      legacyConfirmReferencePersonaMapping = false;
      render();
    }
    if (input?.dataset?.setupLegacy === "confirm-reference-persona-mapping") {
      legacyConfirmReferencePersonaMapping = Boolean(input.checked);
      render();
    }
    if (input?.dataset?.setupLegacy === "import-memory-database") {
      legacyImportMemoryDatabase = input.checked;
      if (legacyMemoryOwnerIds.size || legacyMemoryGroupMappings.size) legacyMemoryPlanDirty = true;
      legacyConfirmMemoryMapping = false;
      render();
    }
    if (input?.dataset?.setupLegacy === "import-archive") {
      legacyImportArchive = Boolean(input.checked);
      render();
    }
    if (input?.dataset?.setupLegacy === "memory-owner") {
      const ownerId = input.dataset.ownerId;
      if (input.checked && legacyMemoryOwnerIds.size >= MAX_LEGACY_MEMORY_OWNER_SELECTION) {
        input.checked = false;
      } else if (input.checked) {
        legacyMemoryOwnerIds.add(ownerId);
      } else {
        legacyMemoryOwnerIds.delete(ownerId);
      }
      legacyMemoryPlanDirty = true;
      legacyConfirmMemoryMapping = false;
      legacyDataResult = null;
      render();
    }
    if (input?.dataset?.setupLegacy === "memory-group-target") {
      const ownerId = input.dataset.ownerId;
      if (!input.value) {
        legacyMemoryGroupMappings.delete(ownerId);
      } else {
        const selected = JSON.parse(input.value);
        legacyMemoryGroupMappings.set(ownerId, selected);
      }
      legacyMemoryPlanDirty = true;
      legacyConfirmMemoryMapping = false;
      legacyDataResult = null;
      render();
    }
    if (input?.dataset?.setupLegacy === "confirm-memory-mapping") {
      legacyConfirmMemoryMapping = Boolean(input.checked);
      render();
    }
    if (input?.dataset?.setupLegacy === "confirm-member-ids") {
      legacyConfirmMemberIds = input.checked;
      render();
    }
    if (input?.dataset?.setupLegacy === "confirm-persona-mapping") {
      legacyConfirmPersonaMapping = input.checked;
      render();
    }
    if (input?.name === "setup-provider") {
      modelDraft = input.value;
      render();
    }
    if (input?.dataset?.setupModelTask && input?.dataset?.setupModelField) {
      const {setupModelTask: kind, setupModelField: field} = input.dataset;
      modelTaskDrafts[kind] = {...(modelTaskDrafts[kind] || {}), [field]: input.value};
      if (field === "provider" && modelTaskDrafts[kind].fallback === input.value) {
        modelTaskDrafts[kind].fallback = "";
      }
      render();
    }
    if (input?.dataset?.profileSupplement !== undefined) {
      const target = state?.step === "relationships" ? "relationship_profile" : "role_profile";
      profileSupplement[target] = input.value;
    }
    if (input?.dataset?.profileField) {
      profileFieldDrafts[input.dataset.profileField] = input.value;
    }
    if (input?.dataset?.profileSelect) {
      if (input.checked) profileSelected.add(input.dataset.profileSelect);
      else profileSelected.delete(input.dataset.profileSelect);
      render();
    }
    if (input?.dataset?.profileOverwrite !== undefined) {
      profileOverwrite = Boolean(input.checked);
      render();
    }
    if (input?.dataset?.setupProactiveOptIn !== undefined) {
      proactiveOptIn = Boolean(input.checked);
      render();
    }
  }

  function mount(node, {migration = false, onState: stateListener = null} = {}) {
    unmount();
    root = node;
    busy = false;
    legacyJobs.clear();
    legacyDataRequestId++;
    legacyDataRefreshing = false;
    migrationOnly = migration;
    onState = typeof stateListener === "function" ? stateListener : null;
    root.addEventListener("click", onClick);
    root.addEventListener("change", onChange);
    root.addEventListener("input", onInput);
    state = null;
    notice = "";
    modelDraft = "";
    modelTasks = null;
    modelTasksStatus = "idle";
    modelTasksError = "";
    modelTasksPromise = null;
    modelTasksScopeKey = null;
    modelTaskDrafts = {};
    profileState = null;
    profileDraft = null;
    profileDraftTarget = "role_profile";
    profileFieldDrafts = {};
    profileSelected = new Set();
    profileSupplement = {role_profile: "", relationship_profile: ""};
    profileOverwrite = false;
    profileNotes = [];
    proactiveOptIn = false;
    advancedOpen = false;
    advancedBlock = "understanding";
    legacyTargetId = legacyBotKey = legacyRoleId = legacyApi = "";
    legacyFinding = legacyPlan = legacyResult = null;
    configurationReview()?.reset();
    legacyDataPlan = legacyDataResult = null;
    legacyDataInventory = null;
    legacyScopeRequestId++;
    legacyScope = null;
    legacyScopeBusy = false;
    legacyScopeChecked = false;
    legacyScopeError = "";
    legacyBatchResult = null;
    legacyWizardExecuting = false;
    legacyOneClickConfigError = "";
    legacyView = "review";
    legacyMappingView = "current";
    legacyWizardStep = 1;
    paintedMigrationStep = 0;
    pendingMigrationFocus = false;
    legacyConfigSkipped = false;
    legacyDataCanApply = false;
    legacyPendingConfirmations = [];
    legacyMigrationFinished = false;
    legacyOpenDetails.clear();
    legacySettingsKey = "";
    legacyRootDraft = "";
    legacyConfigScope = "plugin";
    legacyImportArchive = legacyImportMemoryDatabase = true;
    legacyMemoryOwnerIds = new Set();
    legacyMemoryGroupMappings = new Map();
    legacyMemoryGroupMappingSeeded = legacyMemoryPlanDirty = legacyConfirmMemoryMapping = false;
    legacyPrivateMemoryPlan = legacyPrivateMemoryResult = legacyArchivedWork = null;
    legacyPrivateMemoryConfirmed = false;
    legacyPersonaMappings = new Map();
    legacyPersonaBatchError = "";
    legacyPersonaBatchProgress = "";
    legacyGroupTargetId = legacyGroupRoleId = "";
    legacyGroupPlan = legacyGroupResult = null;
    legacyGroupError = "";
    legacyGroupImportNetwork = legacyGroupImportWorldbook = legacyGroupImportExpressions = true;
    legacyGroupConfirmMapping = legacyGroupConfirmMemberIds = legacyGroupConfirmExpressionMapping = false;
    legacyDataGroupId = "";
    legacyDataPersonaId = "";
    legacyImportReactions = true;
    legacyImportGroupNetwork = false;
    legacyImportWorldbook = false;
    legacyImportReferenceImages = false;
    legacyImportWardrobeItems = false;
    legacyConfirmReferencePersonaMapping = false;
    legacyConfirmMemberIds = false;
    legacyImportJournals = false;
      legacyImportExpressions = false;
      legacyConfirmExpressionMapping = false;
    legacyConfirmPersonaMapping = false;
    legacyError = "";
    legacyDataError = "";
    migrationProgress = "";
    legacyAccountCatalog = null;
    legacyAccountCatalogState = migrationOnly ? "loading" : "idle";
    legacyRoleEditorOpen = legacyRoleSaveBusy = false;
    legacyRoleSwitcherOpen = false;
    legacyNewRoleName = legacyRoleSaveMessage = "";
    legacyNewPersonaIds = new Set();
    legacyManualOpen = false;
    legacyConfigSkipped = false;
    legacyConfigNoChanges = false;
    legacyLastErrorCode = "";
    legacyApplyAllConfig = false;
    preselectUniqueLegacyDestination();
    render();
    if (migrationOnly) {
      void loadLegacyAccountCatalog(mountGeneration);
      if (legacyRoleReady()) void loadLegacyReview();
    }
    if (!migrationOnly) refresh();
  }

  function mountMigration(node) {
    mount(node, {migration: true});
  }

  function unmount() {
    generation++;
    mountGeneration++;
    proactiveWatcher = null;
    stopLegacySettings();
    stopQuickWorkspace();
    stopStepEditor();
    if (root) {
      root.removeEventListener("click", onClick);
      root.removeEventListener("change", onChange);
      root.removeEventListener("input", onInput);
    }
    root = null;
    migrationOnly = false;
    legacyScopeRequestId++;
    legacyScopeBusy = false;
    legacyWizardExecuting = false;
    paintedMigrationStep = 0;
    pendingMigrationFocus = false;
    onState = null;
  }

  // A retained task map (including an empty map after saving) is not a change.
  // The migration source path is a workflow input, not a setting to save.
  function changedFields() {
    if(!root)return [];
    const changed=[];
    if(modelDraft && modelDraft !== (state?.model?.provider_id || ''))changed.push('默认模型');
    for(const [kind,values] of Object.entries(modelTaskDrafts || {})){
      const task=modelTasks?.tasks?.find(item=>item.kind===kind);
      for(const [name,value] of Object.entries(values))if(value !== task?.fields?.[name]?.value)
        changed.push(`${taskLabel(kind)} · ${name==='provider'?'主模型':'备用模型'}`);
    }
    const target=state?.step==='relationships'?'relationship_profile':'role_profile';
    const labels=window.CompanionConfigEditors?.spec?.[target]?.labels || {};
    for(const [name,value] of Object.entries(profileFieldDrafts || {}))
      if(String(value ?? '') !== String(profileState?.[target]?.settings?.[name] ?? profileDraft?.fields?.[name] ?? ''))
        changed.push(labels[name] || name);
    return changed;
  }
  function isDirty() {
    return changedFields().length > 0;
  }
  async function saveBeforeLeave() {
    if(busy)return {error:'设置正在处理，请稍后保存。'};
    try{
      for(const [kind,values] of Object.entries(modelTaskDrafts || {})){
        const task=modelTasks?.tasks?.find(item=>item.kind===kind);
        if(Object.entries(values).some(([name,value])=>value !== task?.fields?.[name]?.value))await saveModelTask(kind);
      }
      if(modelDraft && modelDraft !== (state?.model?.provider_id || '')){
        await api('POST','/model',{provider_id:modelDraft});
        state=await api('GET','/status');
      }
      if(Object.keys(profileFieldDrafts || {}).length)await saveProfileDraft();
      return !isDirty()?true:{error:'还有所选资料未写入，请回到向导核对写入字段。'};
    }catch(error){return {error:error?.message || '设置未保存，改动仍保留。'};}
  }
  function discardChanges() {
    if(busy)return {error:'设置正在处理，请稍后切换。'};
    modelDraft=state?.model?.provider_id || ''; modelTaskDrafts={}; profileFieldDrafts={}; legacyRootDraft='';
    return true;
  }
  return {mount, mountMigration, unmount, refresh, isDirty, changedFields, saveBeforeLeave, discardChanges, promptUpgradeAtStartup, continueUpgrade};
})();
