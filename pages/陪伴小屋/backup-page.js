"use strict";
/* 备份与还原工作区。
 *
 * 为什么值得单独做一个模块：后端 `backup_api.py` 有六条真实路由和一台完整的
 * 两阶段状态机，而**此前没有任何页面调用过其中任何一条**——资料与维护页当时
 * 只放了一张 boundaryCard 解释「为什么没有入口」。那是「能接却没接」，
 * 属于要接上的那一类，不是要删的那一类。
 *
 * 后端状态机（`companion_v2/storage/backup.py`）：
 *
 *   listing            列出已完成快照，按 created_at 倒序
 *   create(note)       写一份快照；sidecar 最后写，它的存在标记快照完成
 *   inspect(snapshot_id)      只读打开并在内存里校验，不还原
 *   restore/preview(snapshot_id)  校验 + 报告当前三条表的行数
 *   restore(snapshot_id, confirmed_hash)  暂存还原请求
 *   pending            报告是否有还原在等重启
 *
 * 三处语义必须照实呈现，否则页面会误导用户：
 *
 *   1. 还原是两阶段的，**必须重启宿主才生效**。页面不能说「已还原」——
 *      只能说到「已暂存，等待重启」。后端也把 effect 写成 requires_restart /
 *      restart_required 两个值，页面对它们各自给话术。
 *
 *   2. 确认的是**字节**：restore 要求传 confirmed_hash，且必须与
 *      manifest.content_hash 逐字相等，否则 backup_confirmation_mismatch。
 *      所以页面必须先把 inspect/preview 拿到的哈希存下来，再原样回传，
 *      不能自己现算也不能省。
 *
 *   3. 暂存时会**自动先做一份安全副本**（pre-restore safety copy）并回传
 *      safety_snapshot_id。这条要显示出来——用户会想知道「还原失败还能退回哪」。
 *
 * 另有两条来自对抗审查的硬要求，页面上必须照实呈现：
 *
 *   4. 7.0.7 的旧格式快照（`installation-backup.v1`）只覆盖主库。后端把它标成
 *      `restore_scope: "main_database_only"` 并给出一串 warnings / preserves；
 *      页面必须把「旁库与文件不会还原」说清楚，并且暂存时要显式回传
 *      `confirmed_scope`，否则后端会以 backup_legacy_confirmation_required 拒绝。
 *      不能把它画成一次完整还原。
 *
 *   5. 一个无法应用的 pending（旧格式 / 读不出来 / 快照不在）会一直挡住新的还原。
 *      后端 pending 会回 `problem` 与 `cancellable`；页面要显示原因，并给一个
 *      二次确认的「放弃这个还原请求」出口（不会删任何快照或用户数据）。
 *
 * 信封与其它页一致：{status, message, data}，机器码在 message 与 data.code 上，
 * 两者都要看（见 [[project_companion_v2_page_error_codes]]）。
 */
window.CompanionBackupPage = (() => {
  const HOST = "astrbot_plugin_private_companion";
  const PREFIX = "/" + HOST + "/";
  const ROUTE = "backup";

  /* 后端 BackupError 对应的可读提示。 */
  const errors = {
    backup_snapshot_missing: "这份快照已经不在了，可能在别处被清理。刷新列表看看。",
    backup_snapshot_unreadable: "这份快照读不出来，文件可能已损坏。",
    backup_snapshot_corrupt: "这份快照完整性校验没通过，不能用来还原。",
    backup_snapshot_foreign: "这份快照属于另一个安装，不能还原到当前数据根。",
    backup_snapshot_schema_mismatch: "这份快照的结构版本与当前不一致，无法直接还原。",
    backup_snapshot_incomplete: "这份快照没有写完（缺少配套清单），已被跳过。",
    backup_snapshot_id_invalid: "快照标识不合法，请从列表中重新选择。",
    backup_snapshot_size_invalid: "快照体积超出允许范围，无法读取。",
    backup_snapshot_changed: "快照在预览之后发生了变化，出于安全没有继续。请重新预览。",
    backup_snapshot_broken_references: "快照里的引用关系不完整，可能缺少关联数据。",
    backup_confirmation_mismatch: "确认的摘要与快照当前内容不一致，已拒绝执行。请重新预览后再试。",
    backup_restore_pending: "已有一个还原在等待重启。重启插件即可完成；若那份请求已经无法应用，用下面的「放弃这个还原请求」清掉它。",
    backup_legacy_confirmation_required: "这是 7.0.7 的旧格式快照，只能还原主库。请按预览里的说明确认范围后再试。",
    backup_restore_scope_invalid: "这份快照是整安装包，不能按「只还原主库」执行。请重新预览。",
    backup_restore_unverified: "这份快照尚未通过校验，不能还原。",
    backup_note_invalid: "备注最多 200 个字。",
    backup_write_failed: "写快照失败，可能是磁盘空间或权限问题。",
    backup_clock_invalid: "系统时间不可用，无法为快照打时间戳。",
    backup_metadata_missing: "备份所需的元数据缺失，请确认 V2 数据根完整。",
    backup_pending_unreadable: "等待重启的还原记录读不出来，请检查数据根。",
    backup_pending_invalid: "等待重启的还原记录格式不对，可以放弃它后重新暂存。",
    backup_pending_legacy: "这是 7.0.7 留下的还原请求，本版本无法应用。可以放弃它，再从列表里重新暂存那份旧快照。",
    backup_cancel_confirmation_required: "放弃还原请求需要二次确认。",
    backup_restore_journal_pending: "上一次还原还没有回滚完成，请先重启插件让它收尾，再放弃请求。",
    backup_cancel_failed: "放弃还原请求失败，请检查数据根权限后重试。",
    backup_delete_confirmation_required: "删除快照需要二次确认。",
    backup_delete_incomplete: "这份快照不完整，请先刷新列表并使用隔离操作。",
    backup_delete_failed: "未能完成删除。请检查目录权限或文件占用，再刷新列表核对结果。",
    backup_quarantine_confirmation_required: "隔离不完整快照需要二次确认。",
    backup_quarantine_failed: "隔离失败，可能是文件被占用或权限不足；没有删除任何东西。",
    backup_operation_failed: "备份操作没有完成，请稍后重试或查看插件日志。",
    backup_preview_failed: "读取当前数据行数失败，请刷新核对。",
    /* 宿主与身份层的提示也在此处翻译。 */
    dashboard_identity_required: "请从已登录的 AstrBot 管理界面打开。",
    staging_not_initialized: "V2 数据尚未加载，请先在账号与会话中加载已配置的运行实例。",
    staging_host_terminated: "插件已停止，请重新打开拓展页。",
    backup_request_invalid: "请求参数不合法，请重新操作。",
    bridge_unavailable: "页面还没有接上 AstrBot。若这是从管理界面打开的插件页，刷新一次通常就好；若反复出现，请确认插件已启用。",
    bridge_timeout: "未收到确认，请刷新核对。",
    invalid_response: "服务返回的数据不完整，请刷新核对。",
  };
  const e = value => String(value ?? "").replace(/[&<>"']/g,
    c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const CODE = /^[a-z][a-z0-9_]{2,60}$/;
  const codeOf = wrapped => wrapped?.data?.code
    || (CODE.test(wrapped?.message || "") ? wrapped.message : "") || "";
  const reason = code => errors[code] || "备份操作未完成，请重新读取快照与还原状态后核对。";

  /* 三张被统计的表。中文名只为让用户看懂「还原会替换掉什么」，
     键名必须与 COUNTED_TABLES 一致。 */
  const TABLES = [
    ["memory_atoms", "记忆条目"],
    ["operations", "操作记录"],
    ["configuration_values", "配置项"],
  ];

  let root = null, bridge = null, version = 0;
  let data = {snapshots: [], problems: [], pending: null, pendingProblem: null,
    cancellable: false, cancelling: false, quarantining: null, loading: true, busy: "",
    error: false, notice: "", preview: null, confirming: null};

  /* 整安装备份/还原是真实的磁盘工作：GB 级安装可能跑几分钟。桥的默认 20 秒
     上限只适合读列表；生成快照、预览（整包校验）和暂存必须给足时间，否则用户
     每次都会看到一个「未收到确认」，而操作其实已经成功了。 */
  const LONG_TIMEOUT_MS = 10 * 60 * 1000;

  function bytes(value) {
    const size = Number(value);
    if (!Number.isFinite(size) || size <= 0) return "—";
    if (size < 1024) return size + " B";
    if (size < 1024 * 1024) return (size / 1024).toFixed(1) + " KB";
    return (size / 1024 / 1024).toFixed(1) + " MB";
  }
  function when(value) {
    return value && Number.isFinite(Date.parse(value))
      ? new Date(value).toLocaleString("zh-CN", {hour12: false}) : "时间未知";
  }
  /* 32 位十六进制摘要按 8 位一组显示更容易比对，整串仍保留在 title 里。 */
  function shortHash(hash) {
    const text = String(hash || "");
    return text.length > 12 ? text.slice(0, 12) + "…" : text;
  }

  function token() { return version; }
  function live(ticket) { return ticket === version && root?.isConnected; }
  function stop() { root = null; bridge = null; }
  function paint() { if (live(version)) render(); }

  function setNotice(message, isError = false) {
    data.notice = message; data.error = isError; paint();
  }

  async function bounded(promise, timeoutMs = 20000) {
    let timeout;
    try {
      return await Promise.race([promise, new Promise((_, reject) => {
        timeout = setTimeout(() => reject(Object.assign(new Error("bridge_timeout"), {code: "bridge_timeout"})), timeoutMs);
      })]);
    } finally { clearTimeout(timeout); }
  }

  /* 七个动作走同一台桥。GET 的那三个后面不带查询参数，POST 的体是严格字段集
     （后端 _body 要求字段集等于允许集合，多一个字段就 invalid）。 */
  async function call(method, path, body, timeoutMs) {
    const response = await bounded(method === "GET"
      ? bridge.apiGet(path, body) : bridge.apiPost(path, body), timeoutMs);
    const wrapped = ["ok", "error"].includes(response?.status) && response?.data !== undefined ? response
      : ["ok", "error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    if (wrapped?.status === "error") {
      const code = codeOf(wrapped) || "invalid_response";
      throw Object.assign(new Error(code), {code, confirmed: true});
    }
    const value = wrapped ? wrapped.data : response;
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw Object.assign(new Error("invalid_response"), {code: "invalid_response"});
    }
    return value;
  }

  async function load(ticket = version) {
    data.loading = true; data.error = false; paint();
    try {
      /* 桥可能比页面脚本晚到（正常时序），也可能真的没接上。
         两种情况都先等一等，等不到就抛出可翻译的码。 */
      bridge = bridge || await bridgeReady();
      const [listed, waiting] = await Promise.all([
        call("GET", ROUTE),
        /* pending 在早期版本可能不存在；单独失败不该让整页读不出列表。 */
        call("GET", ROUTE + "/pending").catch(() => ({pending: null})),
      ]);
      if (!live(ticket)) return;
      data.snapshots = Array.isArray(listed.snapshots) ? listed.snapshots : [];
      data.problems = Array.isArray(listed.problems) ? listed.problems : [];
      data.pending = waiting.pending || null;
      data.pendingProblem = waiting.problem || null;
      data.cancellable = waiting.cancellable === true;
      data.cancelling = false;
      data.deleting = null;
      data.loading = false;
    } catch (error) {
      if (!live(ticket)) return;
      data.loading = false; data.error = true;
      data.notice = reason(error.code); paint();
      return;
    }
    paint();
  }

  /* 创建快照。备注是可选字段，但后端严格要求 body 的键集等于 {note}，
     所以没填也要传空串，不能省略键。 */
  async function create(note) {
    if (data.busy) return;
    const ticket = token();
    data.busy = "create"; data.notice = ""; paint();
    try {
      const created = await call("POST", ROUTE + "/create", {note: String(note || "").slice(0, 200)},
        LONG_TIMEOUT_MS);
      if (!live(ticket)) return;
      data.busy = "";
      const retention = created.retention;
      const missed = Number(retention?.failed_count) || 0;
      setNotice(retention?.failed
        ? (missed ? `快照已生成，但有 ${missed} 份旧快照未能清理。` : "快照已生成，自动清理未完成。")
        : "快照已生成。", retention?.failed === true);
      await load(ticket);
    } catch (error) {
      if (!live(ticket)) return;
      data.busy = ""; setNotice(reason(error.code), true);
    }
  }

  /* 预览还原：校验快照 + 报告当前行数，并把哈希留在 data.preview 里等确认。
     哈希必须来自后端（inspect/preview 的 manifest.content_hash），
     页面不自己算——后端比对的就是它。 */
  async function preview(snapshotId) {
    if (data.busy) return;
    const ticket = token();
    data.busy = "preview:" + snapshotId; data.notice = ""; data.preview = null; paint();
    try {
      const result = await call("GET", ROUTE + "/restore/preview", {snapshot_id: snapshotId},
        LONG_TIMEOUT_MS);
      if (!live(ticket)) return;
      const manifest = result.verified?.manifest || {};
      data.busy = "";
      data.preview = {
        snapshot_id: result.snapshot_id,
        current: result.current || {},
        effect: result.effect,
        /* 这次还原影响哪些目标与角色。后端随预览一并下发：还原是整根替换，
           多目标下会同时替换该安装下所有目标的资料，而用户最容易误以为
           「只还原我当前看的这个角色」。旧版后端没有这一段，读不到就不显示。 */
        scope: result.scope || null,
        /* 旧格式快照只能还原主库。后端给 restore_scope / warnings / preserves，
           页面据此换一套更重的措辞并要求额外确认，绝不画成完整还原。 */
        restore_scope: result.restore_scope || "complete_installation",
        warnings: Array.isArray(result.warnings) ? result.warnings : [],
        preserves: Array.isArray(result.preserves) ? result.preserves : [],
        credentials: result.credentials || null,
        content_hash: manifest.content_hash || "",
        created_at: manifest.created_at,
        note: manifest.note || "",
        byte_size: manifest.byte_size,
      };
      paint();
    } catch (error) {
      if (!live(ticket)) return;
      data.busy = ""; setNotice(reason(error.code), true);
    }
  }

  /* 暂存还原。confirmed_hash 原样回传预览时拿到的那个。
     成功后**不是**「已还原」，而是「已暂存、等重启」——话术上必须区分开。
     旧格式快照还要带上 confirmed_scope，否则后端拒绝：一次只还原主库的请求
     必须由人明确确认过范围。 */
  async function stageRestore() {
    const previewState = data.preview;
    if (!previewState || data.busy) return;
    const ticket = token();
    const legacy = previewState.restore_scope === "main_database_only";
    data.busy = "restore"; data.notice = ""; paint();
    try {
      const body = {
        snapshot_id: previewState.snapshot_id,
        confirmed_hash: previewState.content_hash,
      };
      if (legacy) body.confirmed_scope = "main_database_only";
      const result = await call("POST", ROUTE + "/restore", body, LONG_TIMEOUT_MS);
      if (!live(ticket)) return;
      const safety = result.safety_snapshot_id ? shortHash(result.safety_snapshot_id) : "";
      data.busy = ""; data.preview = null; data.confirming = null;
      setNotice((legacy
        ? "只还原主库的请求已暂存：重启插件后主库回到这份 7.0.7 快照，旁库与文件保持现在的内容。"
        : "还原请求已暂存，将在下次启动插件时生效。")
        + (safety ? `还原前的数据已另存为安全副本 ${safety}。` : "")
        + "现在重启插件即可完成。");
      await load(ticket);
    } catch (error) {
      if (!live(ticket)) return;
      data.busy = ""; setNotice(reason(error.code), true);
    }
  }

  /* 放弃一个无法应用的还原请求。后端只清 pending 与已提交的 journal，不删任何
     快照或用户数据，并且会先做一份安全副本；页面这里只负责「说清楚 + 二次确认」。 */
  async function cancelPending() {
    if (data.busy || !data.cancellable) return;
    const ticket = token();
    data.busy = "cancel"; data.notice = ""; paint();
    try {
      const result = await call("POST", ROUTE + "/restore/cancel", {confirm: true}, LONG_TIMEOUT_MS);
      if (!live(ticket)) return;
      data.busy = ""; data.cancelling = false;
      const safety = result.safety_snapshot_id ? shortHash(result.safety_snapshot_id) : "";
      setNotice("等待重启的还原请求已放弃，没有删除任何快照。"
        + (safety ? `当前数据已另存为安全副本 ${safety}。` : "")
        + (result.safety_failure ? "（安全副本未能生成，原因：" + result.safety_failure + "）" : ""));
      await load(ticket);
    } catch (error) {
      if (!live(ticket)) return;
      data.busy = ""; setNotice(reason(error.code), true);
    }
  }

  /* 隔离一个不完整快照：移到同目录的 .incomplete/，不删除任何字节。
     它不再挡住备份目录迁移，也不会再出现在「不完整」清单里。 */
  async function quarantine(snapshotId) {
    if (data.busy) return;
    const ticket = token();
    data.busy = "quarantine"; data.notice = ""; paint();
    try {
      const result = await call("POST", ROUTE + "/quarantine",
        {snapshot_id: snapshotId, confirm: true}, LONG_TIMEOUT_MS);
      if (!live(ticket)) return;
      data.busy = ""; data.quarantining = null;
      setNotice(`已把不完整快照 ${shortHash(result.snapshot_id || snapshotId)} 移到 .incomplete/，没有删除任何文件。`);
      await load(ticket);
    } catch (error) {
      if (!live(ticket)) return;
      data.busy = ""; setNotice(reason(error.code), true);
    }
  }

  async function deleteSnapshot(snapshotId) {
    if (data.busy || data.pending || data.pendingProblem || data.deleting !== snapshotId) return;
    const item = data.snapshots.find(row => row.snapshot_id === snapshotId);
    if (!item?.content_hash) return;
    const ticket = token();
    data.busy = "delete"; data.notice = ""; paint();
    try {
      await call("POST", ROUTE + "/delete", {
        snapshot_id: snapshotId, confirmed_hash: item.content_hash, confirm: true,
      }, LONG_TIMEOUT_MS);
      if (!live(ticket)) return;
      data.busy = ""; data.deleting = null;
      if (data.preview?.snapshot_id === snapshotId) { data.preview = null; data.confirming = null; }
      setNotice(`已删除 ${when(item.created_at)} 的备份快照，运行资料未改动。`);
      await load(ticket);
    } catch (error) {
      if (!live(ticket)) return;
      data.busy = ""; data.deleting = null;
      setNotice(reason(error.code), true);
      await load(ticket);
    }
  }

  function snapshotRow(item) {
    const id = String(item.snapshot_id || "");
    const busy = data.busy === "preview:" + id;
    const pendingSame = data.pending?.snapshot_id === id;
    /* 旧格式快照只能还原主库：列表上就要标出来，用户点预览前就该知道。 */
    const legacy = item.restore_scope === "main_database_only";
    const deleting = data.deleting === id;
    const deleteBlocked = Boolean(data.pending || data.pendingProblem);
    const deleteDisabled = data.busy || deleteBlocked || !id || !item.content_hash;
    return `<li class="backup-snapshot-row">
      <div class="backup-snapshot-main">
        <div class="backup-snapshot-title">
          <strong>${e(when(item.created_at))}</strong>
          ${pendingSame ? `<span class="backup-tag is-pending">等待重启</span>` : ""}
          ${legacy ? `<span class="backup-tag is-legacy">旧格式 · 仅主库</span>` : ""}
        </div>
        ${item.note ? `<p class="backup-snapshot-note">${e(item.note)}</p>` : ""}
        ${deleting ? `<p class="backup-preview-warn">删除后无法用这份快照还原，当前运行资料不受影响。</p>` : ""}
        <small class="backup-snapshot-meta">${e(bytes(item.byte_size))} · 结构版本 ${e(item.schema_version || "未知")} · 摘要 <code title="${e(item.content_hash)}">${e(shortHash(item.content_hash))}</code></small>
      </div>
      <div class="backup-snapshot-actions">
        ${deleting
          ? `<button class="button small danger" data-backup-delete-confirm="${e(id)}"${deleteDisabled ? " disabled" : ""}>
               ${data.busy === "delete" ? "正在删除…" : "确认删除"}</button>
             <button class="button small" data-backup-delete-abort${data.busy ? " disabled" : ""}>再想想</button>`
          : `<button class="button small" data-backup-preview="${e(id)}"${data.busy || pendingSame ? " disabled" : ""}>
               ${busy ? "正在校验…" : "预览还原"}</button>
             <button class="button small danger" data-backup-delete="${e(id)}"${deleteDisabled ? " disabled" : ""}${deleteBlocked ? ' title="请先完成或放弃待重启的还原请求"' : ""}>删除</button>`}
      </div>
    </li>`;
  }

  /* 预览区。这里是全局唯一需要用户「看清楚再点」的地方，
     所以把会被替换掉的三个行数与哈希都摊开，并要求二次确认。
     旧格式快照的预览必须换一套措辞：这次只换主库，旁库与文件原样保留，
     两者拼在一起就是「混合代际」，这个风险要写在按钮上面。 */
  function previewBlock() {
    const state = data.preview;
    if (!state) return "";
    const rows = TABLES.map(([key, label]) => {
      const count = state.current?.[key];
      return `<div class="backup-count"><dt>${label}</dt><dd>${Number.isFinite(count) ? count : "—"}<small>行</small></dd></div>`;
    }).join("");
    const confirming = data.confirming === state.snapshot_id;
    const legacy = state.restore_scope === "main_database_only";
    const legacyWarnings = legacy
      ? `<div class="backup-preview-legacy" role="alert">
           <strong>这是 7.0.7 的旧格式快照：只还原主库</strong>
           <p>重启后 <code>companion-v2.sqlite3</code> 会回到这份快照，而
             ${e(state.preserves.slice(0, 6).join("、") || "旁库与文件")} 保持现在的内容。
             两者来自不同时间点，可能出现「记忆回到旧版本、图片还是新的」。</p>
         </div>`
      : "";
    const credentials = state.credentials && (state.credentials.files?.length || state.credentials.keys?.length)
      ? `<p class="backup-preview-credentials">还原不会清空当前凭据：
           ${state.credentials.files?.length ? `保留 ${state.credentials.files.length} 个凭据文件；` : ""}
           ${state.credentials.keys?.length ? `配置里的 ${state.credentials.keys.length} 组密钥沿用当前值；` : ""}
           备份本身仍然不含这些凭据。</p>`
      : "";
    /* 影响面：后端说这次还原覆盖几个目标、几个角色。多目标下这句话是必要的——
       数据根是整个安装的，不是某一个角色。 */
    const scope = state.scope;
    const scopeLine = scope
      ? `<p class="backup-preview-scope">还原将替换整个安装的数据：${Number(scope.targets) || 1} 个目标、${Number(scope.roles) || 1} 个角色${scope.restart_required === false ? "" : "；重启后生效"}。</p>`
      : "";
    return `<section class="backup-preview" aria-label="还原预览">
      <header class="backup-preview-head">
        <strong>${legacy ? "这份旧格式快照只会替换主库" : "还原这份快照会替换掉当前数据"}</strong>
        <small>${e(when(state.created_at))}${state.note ? " · " + e(state.note) : ""} · ${e(bytes(state.byte_size))}</small>
      </header>
      <p class="backup-preview-lead">当前数据行数</p>
      <dl class="backup-counts">${rows}</dl>
      ${scopeLine}
      ${legacyWarnings}
      ${credentials}
      <p class="backup-preview-hash">确认摘要 <code>${e(shortHash(state.content_hash))}</code></p>
      <p class="backup-preview-warn">暂存还原时会生成当前数据的安全副本。</p>
      <div class="backup-preview-actions">
        ${confirming
          ? `<button class="button small danger" data-backup-confirm ${data.busy === "restore" ? "disabled" : ""}>
               ${data.busy === "restore" ? "正在暂存…"
                 : (legacy ? "确认只还原主库" : "确认暂存还原")}</button>
             <button class="button small" data-backup-cancel>再想想</button>`
          : `<button class="button small danger" data-backup-stage>${legacy ? "暂存「仅主库」还原" : "暂存还原请求"}</button>
             <button class="button small" data-backup-cancel>取消</button>`}
      </div>
    </section>`;
  }

  /* 等待重启的还原请求。三种状态要分开说：
   *   - 可应用的正常请求：重启后生效；
   *   - 读不出来/旧格式/快照不在（problem 非空）：它会一直挡住新的还原，
   *     必须给出「放弃」这个出口，并说明放弃不会删任何快照；
   *   - 放弃中：按钮禁用，避免双击。
   * 旧版本后端没有 problem/cancellable，读不到就不显示这一段。 */
  function pendingBlock() {
    const pending = data.pending;
    const problem = data.pendingProblem;
    if (!pending && !problem) return "";
    const confirming = data.cancelling;
    const identifier = pending?.snapshot_id ? shortHash(pending.snapshot_id) : "";
    const problemLine = problem
      ? `<p class="backup-pending-problem">这个请求无法应用（<code>${e(problem.code || "")}</code>）：
           ${e(reason(problem.code))}</p>`
      : "";
    const actions = data.cancellable
      ? (confirming
        ? `<div class="backup-pending-actions">
             <button class="button small danger" data-backup-cancel-confirm ${data.busy === "cancel" ? "disabled" : ""}>
               ${data.busy === "cancel" ? "正在放弃…" : "确认放弃这个还原请求"}</button>
             <button class="button small" data-backup-cancel-abort>再想想</button>
           </div>`
        : `<div class="backup-pending-actions">
             <button class="button small" data-backup-cancel-request>放弃这个还原请求</button>
             <small>只清除等待队列并留一份当前数据的安全副本，不会删除任何快照。</small>
           </div>`)
      : "";
    return `<section class="backup-pending${problem ? " is-blocked" : ""}" role="${problem ? "alert" : "status"}">
      <strong>${problem ? "有一个还原请求卡住了后续还原" : "有一个还原在等待重启"}</strong>
      ${identifier
        ? `<p>它请求把数据还原到 ${e(identifier)}，于 ${e(when(pending.requested_at))} 暂存。
             ${problem ? "" : "重启插件后生效；在此之前不会改动任何数据。"}</p>`
        : ""}
      ${problemLine}
      ${pending?.safety_snapshot_id
        ? `<small>还原前的安全副本：<code>${e(shortHash(pending.safety_snapshot_id))}</code></small>` : ""}
      ${actions}
    </section>`;
  }

  function body() {
    if (data.loading) {
      return `<div class="backup-loading" aria-live="polite">正在读取快照列表…</div>`;
    }
    /* 读不到就不要再摆出那张「还没有任何快照」的空表单——那是一句假消息，
       用户会以为备份真的空着，还可能对着一个读不出来的列表点生成。 */
    if (data.error) {
      return `<section class="backup-unavailable">
        <p>${e(data.notice || "备份列表暂时读不出来。")}</p>
        <div class="backup-unavailable-actions">
          <button class="button small" data-backup-retry>重新读取</button>
        </div>
      </section>`;
    }
    const list = data.snapshots.length
      ? `<ul class="backup-snapshot-list">${data.snapshots.map(snapshotRow).join("")}</ul>`
      /* 空态要给「怎么让它不空」，不是「为什么没有」——所以直接指向下面的创建按钮。 */
      : `<p class="backup-empty">暂无快照</p>`;
    /* 不完整的快照不会出现在列表里，但也绝不能悄悄消失：它会让「切换备份目录」
       一直失败，用户需要知道它叫什么、为什么坏、可以怎么处理。 */
    const problems = data.problems.length
      ? `<section class="backup-problems" role="status">
          <strong>有 ${data.problems.length} 个不完整的快照</strong>
          <p>它们不会被列出、也不会被自动删除；切换备份目录时会跳过它们（留在原目录）。
             「隔离」把它们移进同目录的 <code>.incomplete/</code>，一个字节都不删。</p>
          <ul>${data.problems.map(item => {
            const id = String(item.snapshot_id || "");
            const confirming = data.quarantining === id && id;
            return `<li><code>${e(id || item.sidecar || "未知")}</code>
              <small>${e(item.reason || "")}</small>
              ${id ? (confirming
                ? `<span class="backup-problem-actions">
                     <button class="button small danger" data-backup-quarantine-confirm="${e(id)}"
                       ${data.busy === "quarantine" ? "disabled" : ""}>
                       ${data.busy === "quarantine" ? "正在隔离…" : "确认隔离"}</button>
                     <button class="button small" data-backup-quarantine-abort>再想想</button>
                   </span>`
                : `<button class="button small" data-backup-quarantine="${e(id)}">隔离这一项</button>`) : ""}
            </li>`;
          }).join("")}</ul>
        </section>`
      : "";
    return `${pendingBlock()}
    <div class="backup-create">
      <strong class="backup-create-title">生成快照</strong>
      <label for="backupNote">备注（可选，最多 200 字）</label>
      <div class="backup-create-row">
        <input id="backupNote" type="text" maxlength="200" placeholder="例如：升级插件之前">
        <button class="button small" data-backup-create ${data.busy === "create" ? "disabled" : ""}>
          ${data.busy === "create" ? "正在生成…" : "生成快照"}
        </button>
      </div>
    </div>
    ${previewBlock()}
    ${problems}
    <section class="backup-list-block" aria-label="已有快照">
      <header><strong>已有快照</strong><small>整个安装</small></header>
      ${list}
    </section>`;
  }

  function render() {
    if (!root) return;
    /* 错误态由 body() 自己说清并给出口；这里不再重复一遍同样的句子。 */
    const notice = data.notice && !data.error
      ? `<p class="backup-notice" role="status">${e(data.notice)}</p>` : "";
    root.innerHTML = `<div class="backup-workspace">
      ${notice}
      ${body()}
    </div>`;
    /* 输入框在重绘时会被重建，把用户正在打的备注放回去——
       否则一有通知刷新，备注就没了。 */
    const input = root.querySelector("#backupNote");
    if (input && data.draft != null) input.value = data.draft;
  }

  /* 取桥：先同步看一眼（不抛），真没出现才等。
   *
   * 不能写成 `product?.whenBridgeReady?.() ?? Promise.resolve(window.AstrBotPluginPage)`：
   * `??` 的右半边会**立刻求值**，product 没提供等待器时捕到的是
   * Promise.resolve(undefined)——一个「成功」的 promise 里躺着 undefined，
   * 于是「去等桥」这件事根本没发生，用户点重新读取只会再得一句没接上。
   */
  async function bridgeReady() {
    const product = window.CompanionProduct;
    const found = product?.bridgeNow?.() || (product?.bridgeFor
      ? await product.bridgeFor()
      : window.AstrBotPluginPage);
    if (!found?.apiGet) {
      throw Object.assign(new Error("bridge_unavailable"), {code: "bridge_unavailable"});
    }
    return found;
  }

  root = null;
  function mount(node, options = {}) {
    stop();
    root = node;
    bridge = options.bridge || window.AstrBotPluginPage;
    version += 1;
    data = {snapshots: [], problems: [], pending: null, pendingProblem: null,
      cancellable: false, cancelling: false, quarantining: null, loading: true, busy: "",
      error: false, notice: "", preview: null, confirming: null, draft: ""};
    /* 事件委托：面板每次重绘都会换掉按钮，逐个绑定会随重绘失效。
       它要在「桥还没到」那条早退之前绑上——早退分支里的重试按钮靠它生效。 */
    root.addEventListener("click", onClick);
    /* 缺桥不早退。早退的写法有两处不对：那条分支里没有任何出口
       （data.loading 仍是 true，渲染出来只有一句「正在读取快照列表…」，
       桥永远不来就永远停在那里），而且真实宿主里 mountWorkspace() 只在
       桥可用时才执行——这条分支平时走不到，也就没人发现它坏了。
       改成照常去读，读不到由 load() 落到带重试出口的错误态。 */
    root.addEventListener("input", event => {
      if (event.target?.id === "backupNote") data.draft = event.target.value;
    });
    load(version);
  }

  function unmount() {
    if (root) {
      root.removeEventListener("click", onClick);
      root.innerHTML = "";
    }
    version += 1;
    stop();
  }

  function onClick(event) {
    const button = event.target?.closest?.("[data-backup-create],[data-backup-preview],[data-backup-stage],[data-backup-confirm],[data-backup-cancel],[data-backup-retry],[data-backup-cancel-request],[data-backup-cancel-confirm],[data-backup-cancel-abort],[data-backup-quarantine],[data-backup-quarantine-confirm],[data-backup-quarantine-abort],[data-backup-delete],[data-backup-delete-confirm],[data-backup-delete-abort]");
    if (!button || !root?.contains(button)) return;
    if (button.hasAttribute("data-backup-retry")) {
      /* 就地等桥，不交给外层重渲染：外层会重建整页，用户刚在备注框里
         打的字会一起没掉，而他只是为了重试一次读取。 */
      data.loading = true; data.error = false; paint();
      bridge = null;
      load(version);
      return;
    }
    if (button.hasAttribute("data-backup-create")) {
      create(root.querySelector("#backupNote")?.value || "");
      return;
    }
    if (button.hasAttribute("data-backup-delete")) {
      if (!data.busy && !data.pending && !data.pendingProblem) {
        data.deleting = button.getAttribute("data-backup-delete"); paint();
      }
      return;
    }
    if (button.hasAttribute("data-backup-delete-confirm")) {
      deleteSnapshot(button.getAttribute("data-backup-delete-confirm")); return;
    }
    if (button.hasAttribute("data-backup-delete-abort")) {
      if (!data.busy) { data.deleting = null; paint(); }
      return;
    }
    if (button.hasAttribute("data-backup-preview")) {
      data.deleting = null;
      preview(button.getAttribute("data-backup-preview"));
      return;
    }
    /* 暂存分两步：先切到「确认」态，再真的发请求。
       先让用户看清要暂存的快照与还原范围。 */
    if (button.hasAttribute("data-backup-stage")) {
      data.confirming = data.preview?.snapshot_id || null; paint(); return;
    }
    if (button.hasAttribute("data-backup-confirm")) { stageRestore(); return; }
    if (button.hasAttribute("data-backup-cancel")) {
      data.confirming = null; data.preview = null; paint();
      return;
    }
    /* 放弃一个卡住的还原请求同样分两步：这一步只清队列，但它决定了用户
       还能不能发起新的还原，所以也要一次确认。 */
    if (button.hasAttribute("data-backup-cancel-request")) {
      data.cancelling = true; paint(); return;
    }
    if (button.hasAttribute("data-backup-cancel-confirm")) { cancelPending(); return; }
    if (button.hasAttribute("data-backup-cancel-abort")) {
      data.cancelling = false; paint();
      return;
    }
    /* 隔离同样分两步：它不删东西，但会改变那条快照所在的位置。 */
    if (button.hasAttribute("data-backup-quarantine")) {
      data.quarantining = button.getAttribute("data-backup-quarantine"); paint(); return;
    }
    if (button.hasAttribute("data-backup-quarantine-confirm")) {
      quarantine(button.getAttribute("data-backup-quarantine-confirm")); return;
    }
    if (button.hasAttribute("data-backup-quarantine-abort")) {
      data.quarantining = null; paint();
    }
  }

  return {
    mount, unmount,
    /* 供测试读取内部状态与走查渲染分支。 */
    get state() { return data; },
    _reason: reason, _bytes: bytes, _shortHash: shortHash,
  };
})();
