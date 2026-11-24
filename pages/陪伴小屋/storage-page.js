"use strict";

window.CompanionStoragePage = (() => {
  const ROUTE = "storage";
  const HEALTH_ROUTE = "storage/health";
  const COMPACT_ROUTE = "storage/compact";
  const MACHINE_CODE_RE = /^[a-z][a-z0-9_]{2,80}$/;
  const text = value => String(value ?? "").replace(/[&<>"']/g,
    character => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[character]));
  const formatBytes = value => {
    const size = Number(value);
    if (!Number.isFinite(size) || size <= 0) return "0 B";
    if (size < 1024) return `${size} B`;
    if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`;
    if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} MB`;
    return `${(size / 1024 ** 3).toFixed(2)} GB`;
  };
  const labels = {database: "运行资料", backups: "备份快照", other: "其他文件"};
  const DB_LABELS = {
    main: "主数据库",
    semantic_catalog: "语义与表情库",
    memory_search: "记忆检索索引",
    telemetry_archive: "冷遥测归档库",
    native_image: "生图制品库",
    configuration: "配置库",
  };
  let root = null, bridge = null, version = 0, listenerAbort = null, boundListeners = [];
  let state = emptyState();

  function emptyState() {
    return {
      loading: true,
      busy: false,
      healthLoading: false,
      compacting: false,
      error: false,
      message: "",
      status: null,
      health: null,
      healthError: "",
      compactSummary: null,
      confirming: false,
      confirmingVacuum: false,
      form: {
        path: "",
        backup_path: "",
        retention_count: 10,
        retention_days: 90,
        auto_backup_enabled: false,
        auto_backup_interval_minutes: 1440,
      },
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

  function sizeBar(status) {
    const categories = status?.inventory?.categories || {};
    const total = Number(status?.inventory?.total_bytes) || 0;
    const segments = ["database", "backups", "other"].map(key => {
      const bytes = Number(categories[key]?.bytes) || 0;
      const width = total ? Math.max(bytes ? 1 : 0, bytes / total * 100) : 0;
      return `<span class="storage-usage-segment is-${key}" style="width:${width}%" title="${labels[key]} ${formatBytes(bytes)}"></span>`;
    }).join("");
    const legend = ["database", "backups", "other"].map(key =>
      `<div class="storage-usage-item"><span class="storage-usage-mark is-${key}"></span><span>${labels[key]}</span><strong>${formatBytes(categories[key]?.bytes)}</strong><small>${Number(categories[key]?.files) || 0} 个文件</small></div>`
    ).join("");
    return `<div class="storage-usage-bar" role="img" aria-label="插件存储占用 ${formatBytes(total)}">${segments || "<span></span>"}</div><div class="storage-usage-legend">${legend}</div>`;
  }

  function renderHealthCard() {
    const health = state.health;
    const disabled = state.busy || state.compacting;
    if (!health && state.healthLoading) {
      return `<section class="storage-health-card" data-db-health-card aria-label="数据库体检与一键瘦身">
        <div class="storage-health-head">
          <div class="storage-health-title"><h4>数据库体检与一键瘦身</h4><span class="storage-health-badge is-subtle">正在体检…</span></div>
          <div class="storage-health-head-actions"><a class="button small" href="#manage/data/tuning">性能调优</a></div>
        </div>
        <p class="storage-health-hint">正在扫描主库与副库体积、重复上下文、冷遥测明细和空闲页碎片…</p>
      </section>`;
    }

    if (!health) {
      const note = state.healthError || "尚未读取到运行中的数据库体检报告；可点击「立即体检」刷新。";
      return `<section class="storage-health-card" data-db-health-card aria-label="数据库体检与一键瘦身">
        <div class="storage-health-head">
          <div class="storage-health-title"><h4>数据库体检与一键瘦身</h4><span class="storage-health-badge is-subtle">待体检</span></div>
          <div class="storage-health-head-actions">
            <button class="button small" type="button" data-db-health-refresh${disabled ? " disabled" : ""}>立即体检</button>
            <a class="button small" href="#manage/data/tuning">性能调优</a>
          </div>
        </div>
        <p class="storage-health-hint">${text(note)}</p>
      </section>`;
    }

    const opp = health.opportunities || {};
    const dedupCount = (Number(opp.un_deduped_native_turns) || 0)
      + (Number(opp.un_deduped_native_observations) || 0)
      + (Number(opp.un_deduped_core_observations) || 0);
    const coldCount = (Number(opp.cold_core_observations) || 0) + (Number(opp.cold_laya_payloads) || 0);
    const mediaCount = (Number(opp.inline_conversation_images) || 0)
      + (Number(opp.inline_reaction_blobs) || 0)
      + (Number(opp.inline_native_image_blobs) || 0);
    const reclaimBytes = Number(health.total_reclaimable_bytes) || 0;
    const totalOppCount = dedupCount + coldCount + mediaCount;
    const fragPct = Math.round((Number(health.max_fragmentation_ratio) || 0) * 100);
    const recommended = health.status === "compact_recommended";
    const ttlDays = Number(health.telemetry_ttl_days) || 14;
    const canVacuum = health.can_full_vacuum !== false;
    const vacuumReqText = formatBytes(health.vacuum_required_bytes);
    const diskFreeText = health.disk_free_bytes == null ? "—" : formatBytes(health.disk_free_bytes);

    const dbChips = Object.entries(health.databases || {})
      .filter(([, item]) => item && item.exists)
      .map(([name, item]) => {
        const walPart = Number(item.wal_bytes) > 0 ? ` · WAL ${formatBytes(item.wal_bytes)}` : "";
        return `<span class="storage-health-db-chip"><span>${text(DB_LABELS[name] || name)}</span><strong>${formatBytes(item.total_bytes)}</strong><span>${text(walPart)}</span></span>`;
      }).join("");

    const recs = Array.isArray(health.recommendations) && health.recommendations.length
      ? `<ul class="storage-health-recs">${health.recommendations.map(item => `<li>${text(item.label)}</li>`).join("")}</ul>`
      : "";

    const vacuumConfirm = state.confirmingVacuum
      ? `<div class="storage-vacuum-confirm" role="region" aria-label="深度重组确认">
          <p><strong>确认执行深度整理压缩（全量 VACUUM）？</strong></p>
          <p>该操作会将已释放的空闲页彻底压缩为更小的硬盘文件并顺带启用增量回收，执行期间会短暂暂停写入数秒；绝不删除聊天记录、记忆、人物、日记、表达规则、衣橱、书架、配置或备份快照（超期遥测明细先完整归档至独立冷库并保留每日统计）。所在磁盘需预留至少 <strong>${text(vacuumReqText)}</strong>（约为当前最大数据库的 2.2 倍）。</p>
          ${!canVacuum ? (health.disk_free_bytes == null
            ? `<p>当前无法获取所在磁盘可用空间，出于安全保护已暂停全量深度重组；请改用「一键常规无损瘦身」。</p>`
            : `<p>当前可用磁盘空间（${text(diskFreeText)}）低于安全要求，请先释放磁盘空间或改用「一键常规无损瘦身」。</p>`) : ""}
          <div class="storage-health-buttons">
            <button class="button primary small" type="button" data-db-compact-vacuum-confirm${disabled || !canVacuum ? " disabled" : ""}>确认执行深度重组</button>
            <button class="button small" type="button" data-db-compact-vacuum-cancel${state.compacting ? " disabled" : ""}>取消</button>
          </div>
        </div>`
      : "";

    const summary = state.compactSummary;
    const summaryBanner = summary
      ? `<div class="storage-compact-summary" data-db-compact-summary role="status">
          <strong>${summary.vacuum ? "深度重组完成" : "常规无损瘦身完成"}（耗时 ${(summary.elapsedMs / 1000).toFixed(1)} 秒）：瘦身前 ${formatBytes(summary.beforeBytes)} → 瘦身后 ${formatBytes(summary.afterBytes)}，已释放 ${formatBytes(summary.reclaimedBytes)}</strong>
          <span>${text(summary.detail)}</span>
        </div>`
      : "";

    return `<section class="storage-health-card" data-db-health-card aria-label="数据库体检与一键瘦身">
      <div class="storage-health-head">
        <div class="storage-health-title">
          <h4>数据库体检与一键瘦身</h4>
          <span class="storage-health-badge${recommended ? " is-warn" : ""}">${recommended ? "建议瘦身" : "状态良好"}</span>
        </div>
        <div class="storage-health-head-actions">
          <button class="button small" type="button" data-db-health-refresh${disabled || state.healthLoading ? " disabled" : ""}>${state.healthLoading ? "正在体检…" : "重新体检"}</button>
          <a class="button small" href="#manage/data/tuning">性能调优</a>
        </div>
      </div>

      <div class="storage-health-kpis">
        <div class="storage-health-kpi">
          <span>数据库总占用</span>
          <strong>${formatBytes(health.total_sqlite_bytes)}</strong>
          <small>含主库、副库与 WAL 日志</small>
        </div>
        <div class="storage-health-kpi">
          <span>可回收空闲页</span>
          <strong>${formatBytes(reclaimBytes)}</strong>
          <small>最高碎片率 ${fragPct}%</small>
        </div>
        <div class="storage-health-kpi">
          <span>待优化记录</span>
          <strong>${totalOppCount} 项</strong>
          <small>去重 / 冷归档 / 外置媒体</small>
        </div>
        <div class="storage-health-kpi">
          <span>所在磁盘可用</span>
          <strong>${text(diskFreeText)}</strong>
          <small>${canVacuum ? `满足深度重组（需 ≥ ${text(vacuumReqText)}）` : `深度重组需 ≥ ${text(vacuumReqText)}`}</small>
        </div>
      </div>

      ${dbChips ? `<div class="storage-health-dbs">${dbChips}</div>` : ""}

      <div class="storage-health-opps">
        <div class="storage-health-opp${dedupCount > 0 ? " has-work" : ""}"><span>重复上下文 / 人设提示词去重</span><strong>${dedupCount > 0 ? `${dedupCount} 条可优化` : "已最简"}</strong></div>
        <div class="storage-health-opp${coldCount > 0 ? " has-work" : ""}"><span>冷遥测明细归档（超 ${ttlDays} 天）</span><strong>${coldCount > 0 ? `${coldCount} 条可归档` : "无需归档"}</strong></div>
        <div class="storage-health-opp${mediaCount > 0 ? " has-work" : ""}"><span>库内图片 / 表情外置到文件系统</span><strong>${mediaCount > 0 ? `${mediaCount} 项可外置` : "已外置"}</strong></div>
        <div class="storage-health-opp${reclaimBytes >= 4 * 1024 * 1024 ? " has-work" : ""}"><span>数据库空闲页回收</span><strong>${reclaimBytes > 0 ? formatBytes(reclaimBytes) : "无冗余页"}</strong></div>
      </div>

      ${recs}
      ${summaryBanner}
      ${vacuumConfirm}

      <div class="storage-health-actions">
        <p class="storage-health-hint">常规无损瘦身全程在线执行、不中断聊天，绝不删除聊天记录、记忆、人物、日记、表达规则、衣橱、书架、配置或备份快照（超期遥测明细先完整归档至独立冷库并保留每日统计）；需要进一步缩小硬盘文件体积时，再使用全量深度重组。</p>
        <div class="storage-health-buttons">
          <button class="button primary small" type="button" data-db-compact-normal${disabled ? " disabled" : ""}>${state.compacting ? "正在瘦身…" : "一键常规无损瘦身（推荐）"}</button>
          <button class="button small" type="button" data-db-compact-vacuum-review${disabled ? " disabled" : ""}>深度整理压缩（全量重组）</button>
        </div>
      </div>
    </section>`;
  }

  function renderUsageCardBody() {
    const status = state.status;
    const inventory = status?.inventory || {};
    const diskFree = (status?.restart_required ? status.destination_free_bytes : inventory.free_bytes) == null
      ? "—" : formatBytes(status?.restart_required ? status.destination_free_bytes : inventory.free_bytes);
    return `<header class="storage-usage-header"><div><span>插件资料占用</span><strong>${formatBytes(inventory.total_bytes)}</strong></div><div><span>${status?.restart_required ? "目标磁盘可用" : "所在磁盘可用"}</span><strong>${diskFree}</strong></div></header>
        ${sizeBar(status)}`;
  }

  function renderMessageHtml() {
    return state.message
      ? `<p class="storage-message${state.error ? " is-error" : ""}" role="status">${text(state.message)}</p>` : "";
  }

  function renderHealthSlot() {
    const slot = root?.querySelector?.("[data-storage-health-slot]");
    if (!slot) return false;
    slot.innerHTML = renderHealthCard();
    return true;
  }

  function renderUsageSlot() {
    const slot = root?.querySelector?.("[data-storage-usage-card]");
    if (!slot) return false;
    slot.innerHTML = renderUsageCardBody();
    return true;
  }

  function renderMessageSlot() {
    const slot = root?.querySelector?.("[data-storage-message-slot]");
    if (!slot) return false;
    slot.innerHTML = renderMessageHtml();
    return true;
  }

  function render() {
    if (!root) return;
    if (state.loading) {
      root.innerHTML = `<div class="storage-loading" role="status">正在读取插件存储…</div>`;
      return;
    }
    const status = state.status;
    const preview = status?.cleanup_preview || {};
    const locationState = status?.restart_required
      ? `<p class="storage-location-note is-pending" role="status">待重启生效 · ${text(status.desired_root)}</p>`
      : `<p class="storage-location-note">当前目录 · ${text(status?.active_root || "")}</p>`;
    const backupLocation = `<p class="storage-location-note">当前目录 · ${text(status?.backup_root || "")}</p><small>磁盘可用 ${formatBytes(status?.inventory?.backup_free_bytes ?? status?.inventory?.free_bytes)}</small>`;
    const cleanup = Number(preview.remove_count) > 0
      ? `<div class="storage-cleanup-preview"><div><strong>可清理 ${preview.remove_count} 份</strong><span>${formatBytes(preview.remove_bytes)}</span></div>${state.confirming
        ? `<button class="button small danger" type="button" data-storage-clean-confirm${state.busy ? " disabled" : ""}>确认清理</button><button class="button small" type="button" data-storage-clean-cancel>取消</button>`
        : `<button class="button small" type="button" data-storage-clean-review${state.busy ? " disabled" : ""}>清理快照</button>`}</div>`
      : `<p class="storage-cleanup-empty">${preview.snapshot_count ? "当前快照均在保留范围内。" : "暂无快照"}</p>`;
    const futureBackups = Number(preview.future_count) || 0;
    const futureNotice = futureBackups > 0
      ? `<p class="storage-location-note is-pending">${futureBackups} 份快照的时间晚于当前时间，暂不参与轮转；请核对系统时钟。</p>` : "";
    root.innerHTML = `<div class="storage-dashboard">
      <section class="storage-usage-card" data-storage-usage-card aria-label="插件资料占用">
        ${renderUsageCardBody()}
      </section>
      <div class="storage-health-slot" data-storage-health-slot>${renderHealthCard()}</div>
      <form class="storage-settings" data-storage-form>
        <section class="storage-panel"><h4>存储位置</h4><div class="storage-location-grid">
          <label class="storage-path-field"><span class="storage-field-label">运行资料位置 <em>重启生效</em></span><input name="path" type="text" autocomplete="off" spellcheck="false" placeholder="留空使用 AstrBot 默认目录" value="${text(state.form.path)}"><small>填写其他磁盘上的绝对目录。</small>${locationState}</label>
          <label class="storage-path-field"><span class="storage-field-label">备份快照位置 <em>${state.form.backup_path ? "独立目录" : "跟随资料"}</em></span><input name="backup_path" type="text" autocomplete="off" spellcheck="false" placeholder="留空跟随运行资料位置" value="${text(state.form.backup_path)}"><small>切换目录时迁移已有快照。</small>${backupLocation}</label>
        </div></section>
        <section class="storage-panel"><h4>自动滚动备份</h4>
          <div class="option-row"><div>启用自动备份<small>最近完整快照到期或不存在时自动新建，目录和保留策略与手动备份共用。</small></div>
            <button class="switch" type="button" role="switch" aria-checked="${state.form.auto_backup_enabled}"
              aria-label="启用自动备份" data-storage-auto-toggle${state.busy ? " disabled" : ""}></button></div>
          <fieldset class="storage-retention">
            <div class="storage-retention-fields"><label>每隔<input name="auto_backup_interval_minutes" type="number" min="1" max="43200" step="1" required value="${text(state.form.auto_backup_interval_minutes)}">分钟</label></div>
            <small>默认 1440 分钟。手动快照也计入间隔。删除最后一份后会自动补建。</small>
          </fieldset>
        </section>
        <section class="storage-panel storage-policy"><h4>备份清理策略</h4>
          <fieldset class="storage-retention">
            <div class="storage-retention-fields">
              <label>每个运行档最多保留<input name="retention_count" type="number" min="1" max="200" step="1" value="${text(state.form.retention_count)}">份</label>
              <label>保留最近<input name="retention_days" type="number" min="1" max="3650" step="1" value="${text(state.form.retention_days)}">天</label>
            </div>
            <div class="storage-retention-aside"><small>新建快照时自动清理，始终保留最新一份。</small>${cleanup}${futureNotice}</div>
          </fieldset>
          <div class="storage-actions storage-form-actions">
            <button class="button primary small" type="submit"${state.busy ? " disabled" : ""}>保存存储设置</button>
            <button class="button small" type="button" data-storage-refresh${state.busy ? " disabled" : ""}>重新读取</button>
          </div>
        </section>
        <section class="storage-panel storage-cache-panel"><h4>运行缓存</h4>
          <div class="storage-cache-row">
            <p class="storage-cache-note">图片转述、失败冷却和诊断报告都可以随时重建；清理只释放当前进程里的缓存，不会删除对话、记忆、日记或已发送的图片。</p>
            <div class="storage-actions"><button class="button small" type="button" data-storage-cache-clear${state.busy ? " disabled" : ""}>清理运行缓存</button></div>
          </div>
        </section>
      </form>
      <div class="storage-message-slot" data-storage-message-slot>${renderMessageHtml()}</div>
    </div>`;
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
    const normalized = error instanceof Error ? error : new Error(rawMessage || code || "storage_request_invalid");
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

  async function call(method, body, route = ROUTE) {
    bridge = bridge || await getBridge();
    let response;
    try {
      response = await (method === "GET" ? bridge.apiGet(route) : bridge.apiPost(route, body));
    } catch (error) {
      throw normalizeBridgeError(error);
    }
    const wrapped = ["ok", "error"].includes(response?.status) && response?.data !== undefined ? response
      : ["ok", "error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    if (wrapped?.status === "error") {
      const code = extractMachineCode({code: wrapped.data?.code || wrapped.message}) || "storage_request_invalid";
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

  function isHealthReport(payload) {
    return Boolean(payload && typeof payload === "object"
      && payload.databases && typeof payload.databases === "object"
      && payload.opportunities && typeof payload.opportunities === "object");
  }

  async function loadHealth(ticket = version, {silent = false} = {}) {
    state.healthLoading = true;
    if (!silent && !renderHealthSlot()) render();
    try {
      const report = await call("GET", null, HEALTH_ROUTE);
      if (!live(ticket)) return;
      if (isHealthReport(report)) {
        state.health = report;
        state.healthError = "";
      } else {
        state.health = null;
        state.healthError = "";
      }
    } catch (error) {
      if (!live(ticket)) return;
      state.health = null;
      const code = extractMachineCode(error);
      state.healthError = code === "staging_not_initialized"
        ? "插件运行档尚未加载，启动后即可执行数据库体检与瘦身。"
        : "数据库体检暂不可用，请稍后重试。";
    }
    state.healthLoading = false;
    if (!renderHealthSlot()) render();
  }

  async function load(ticket = version) {
    state.loading = true;
    render();
    try {
      const result = await call("GET", null, ROUTE);
      if (!live(ticket)) return;
      state.status = result;
      state.form = {
        path: result.selected_path || "",
        backup_path: result.selected_backup_path || "",
        retention_count: result.policy?.retention_count ?? 10,
        retention_days: result.policy?.retention_days ?? 90,
        auto_backup_enabled: result.auto_backup?.auto_backup_enabled ?? false,
        auto_backup_interval_minutes: result.auto_backup?.auto_backup_interval_minutes ?? 1440,
      };
      state.loading = false;
      state.healthLoading = true;
      state.error = false;
      state.message = "";
      render();
      loadHealth(ticket, {silent: true});
    } catch (error) {
      if (!live(ticket)) return;
      state.loading = false;
      state.error = true;
      const code = extractMachineCode(error);
      state.message = code === "dashboard_identity_required" ? "请从 AstrBot 管理界面登录后查看。"
        : code === "storage_location_unavailable" ? "存储磁盘暂不可用；资料不会自动写回默认位置。"
        : "插件存储读取失败，请重试并检查管理界面连接。";
      render();
    }
  }

  function formatCompactDetail(steps) {
    const parts = [];
    const dedup = steps?.dedup || {};
    const dedupTotal = (Number(dedup.role_blobs_migrated) || 0)
      + (Number(dedup.turn_contexts_migrated) || 0)
      + (Number(dedup.obs_contexts_migrated) || 0)
      + (Number(dedup.core_contexts_migrated) || 0);
    if (dedupTotal > 0) parts.push(`上下文/人设去重 ${dedupTotal} 条`);
    const arch = steps?.archive || {};
    const archTotal = (Number(arch.core_observations_archived) || 0)
      + (Number(arch.laya_payloads_archived) || 0);
    if (archTotal > 0) parts.push(`冷遥测归档 ${archTotal} 条`);
    const media = steps?.media_offload || {};
    const mediaTotal = (Number(media.conversation_images?.migrated) || 0)
      + (Number(media.reactions?.migrated) || 0)
      + (Number(media.native_images?.migrated) || 0);
    if (mediaTotal > 0) parts.push(`媒体外置 ${mediaTotal} 项`);
    const mainVac = steps?.vacuum?.main || {};
    if (mainVac.vacuumed) parts.push("已完成主库全量重组");
    else if ((Number(mainVac.freed_pages) || 0) > 0) parts.push(`增量回收 ${mainVac.freed_pages} 页`);
    return parts.length ? parts.join(" · ") : "数据库已处于紧凑健康状态，已同步刷新索引统计。";
  }

  async function runCompaction({vacuum = false} = {}, ticket = version) {
    state.compacting = true;
    state.confirmingVacuum = false;
    state.message = "";
    state.error = false;
    const patchedStart = renderHealthSlot();
    renderMessageSlot();
    if (!patchedStart) render();
    try {
      const result = await call("POST", {
        vacuum: Boolean(vacuum),
        enable_incremental_vacuum: Boolean(vacuum),
        dedup_native: true,
        archive_telemetry: true,
        offload_media: true,
      }, COMPACT_ROUTE);
      if (!live(ticket)) return;
      const beforeBytes = Number(result.before?.total_sqlite_bytes) || 0;
      const afterBytes = Number(result.after?.total_sqlite_bytes) || 0;
      const reclaimedBytes = Number(result.reclaimed_sqlite_bytes) || Math.max(0, beforeBytes - afterBytes);
      state.compactSummary = {
        vacuum: Boolean(vacuum),
        beforeBytes,
        afterBytes,
        reclaimedBytes,
        elapsedMs: Number(result.elapsed_ms) || 0,
        detail: formatCompactDetail(result.steps),
      };
      if (isHealthReport(result.after)) {
        state.health = result.after;
      }
      state.error = false;
      state.message = `数据库瘦身完成：${formatBytes(beforeBytes)} → ${formatBytes(afterBytes)}，释放 ${formatBytes(reclaimedBytes)}。`;
      try {
        const refreshed = await call("GET", null, ROUTE);
        if (live(ticket) && refreshed?.inventory) state.status = refreshed;
      } catch {}
    } catch (error) {
      if (!live(ticket)) return;
      state.error = true;
      const code = extractMachineCode(error);
      const humanDetail = typeof error?.detail === "string" && error.detail ? error.detail : "";
      const messages = {
        storage_insufficient_disk_for_vacuum: humanDetail || "磁盘剩余空间不足（需至少 2.2 倍主库空间），无法执行全量深度重组；请改用常规无损瘦身或先清理磁盘。",
        staging_not_initialized: "插件运行档尚未加载，请先初始化后再执行数据库瘦身。",
        dashboard_identity_required: "请从已登录的 AstrBot 管理页面执行数据库瘦身。",
        core_access_required: "当前管理身份没有执行数据库瘦身的权限。",
      };
      state.message = messages[code] || "数据库瘦身未能完成，请稍后重试。";
      if (code && !state.message.includes(code)) state.message += `（${code}）`;
    }
    state.compacting = false;
    const patchedEnd = renderHealthSlot();
    renderUsageSlot();
    renderMessageSlot();
    if (!patchedEnd) render();
  }

  async function update(body, ticket = version) {
    state.busy = true;
    state.message = "";
    render();
    try {
      const result = await call("POST", body, ROUTE);
      if (!live(ticket)) return;
      if (result.inventory) {
        state.status = result;
        state.form = {
          path: result.selected_path || "",
          backup_path: result.selected_backup_path || "",
          retention_count: result.policy?.retention_count ?? state.form.retention_count,
          retention_days: result.policy?.retention_days ?? state.form.retention_days,
          auto_backup_enabled: result.auto_backup?.auto_backup_enabled ?? state.form.auto_backup_enabled,
          auto_backup_interval_minutes: result.auto_backup?.auto_backup_interval_minutes ?? state.form.auto_backup_interval_minutes,
        };
      }
      const cache = result.cache_result || {};
      const cleanup = result.cleanup_result || {};
      const missed = Number(cleanup.failed_count) || 0;
      state.message = body.action === "clean"
        ? `已清理 ${cleanup.removed_count || 0} 份快照，释放 ${formatBytes(cleanup.removed_bytes)}。${missed ? `另有 ${missed} 份未能清理，请检查目录权限或文件占用。` : ""}`
        : body.action === "cache"
        ? `已清理运行缓存：图片转述 ${Number(cache.image_narration) || 0} 条、失败记录 ${Number(cache.image_narration_failures) || 0} 条、诊断报告 ${Number(cache.diagnostics) || 0} 条。`
        : result.restart_required ? "设置已保存，运行资料位置将在重启后切换。" : "存储设置已保存。";
      state.error = body.action === "clean" && cleanup.failed === true;
      state.confirming = false;
    } catch (error) {
      if (!live(ticket)) return;
      state.error = true;
      const code = extractMachineCode(error);
      const messages = {
        storage_config_save_failed: "存储设置未能保存，请检查配置文件是否可写。",
        storage_request_invalid: "存储设置不合法，请核对绝对路径、保留范围和备份间隔。",
        storage_operation_failed: "插件进程无法访问或迁移存储文件。请检查目录及磁盘连接；网络磁盘可填写对应的共享路径。",
        dashboard_identity_required: "请从已登录的 AstrBot 管理页面保存存储设置。",
        core_access_required: "当前管理身份没有修改存储设置的权限。",
      };
      state.message = messages[code] || "操作未完成，请核对存储目录与清理设置后重试。";
      if (code && !state.message.includes(code)) state.message += `（${code}）`;
    }
    state.busy = false;
    render();
  }

  function mount(node) {
    stop();
    root = node;
    const ticket = version;
    state = emptyState();
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
      if (!live(ticket)) return;
      if (event.target?.name === "auto_backup_interval_minutes") state.form.auto_backup_interval_minutes = event.target.value;
      if (event.target?.name === "path") state.form.path = event.target.value;
      if (event.target?.name === "backup_path") state.form.backup_path = event.target.value;
      if (event.target?.name === "retention_count") state.form.retention_count = event.target.value;
      if (event.target?.name === "retention_days") state.form.retention_days = event.target.value;
    });
    listen("submit", event => {
      if (!live(ticket) || !event.target?.matches("[data-storage-form]")) return;
      event.preventDefault();
      if (state.busy || state.compacting) return;
      update({
        action: "save",
        path: String(state.form.path || "").trim(),
        backup_path: String(state.form.backup_path || "").trim(),
        retention_count: Number(state.form.retention_count),
        retention_days: Number(state.form.retention_days),
        auto_backup_enabled: state.form.auto_backup_enabled,
        auto_backup_interval_minutes: Number(state.form.auto_backup_interval_minutes),
      }, ticket);
    });
    listen("click", event => {
      if (!live(ticket)) return;
      const button = event.target?.closest("button");
      if (!button || state.busy || state.compacting) return;
      if (button.hasAttribute("data-storage-auto-toggle")) {
        state.form.auto_backup_enabled = !state.form.auto_backup_enabled;
        button.setAttribute("aria-checked", String(state.form.auto_backup_enabled));
      }
      else if (button.hasAttribute("data-storage-refresh")) load(ticket);
      else if (button.hasAttribute("data-storage-cache-clear")) update({action: "cache"}, ticket);
      else if (button.hasAttribute("data-storage-clean-review")) { state.confirming = true; render(); }
      else if (button.hasAttribute("data-storage-clean-cancel")) { state.confirming = false; render(); }
      else if (button.hasAttribute("data-storage-clean-confirm")) update({action: "clean"}, ticket);
      else if (button.hasAttribute("data-db-health-refresh")) loadHealth(ticket);
      else if (button.hasAttribute("data-db-compact-normal")) runCompaction({vacuum: false}, ticket);
      else if (button.hasAttribute("data-db-compact-vacuum-review")) {
        state.confirmingVacuum = true;
        if (!renderHealthSlot()) render();
      }
      else if (button.hasAttribute("data-db-compact-vacuum-cancel")) {
        state.confirmingVacuum = false;
        if (!renderHealthSlot()) render();
      }
      else if (button.hasAttribute("data-db-compact-vacuum-confirm")) runCompaction({vacuum: true}, ticket);
    });
    render();
    load(ticket);
  }
  return {mount, unmount: stop};
})();
