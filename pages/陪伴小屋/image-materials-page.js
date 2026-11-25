/* Native image materials: explicit album registration, references and wardrobe. */
"use strict";

window.CompanionImageMaterialsPage = (() => {
  const ROLES = [
    ["identity", "身份"], ["outfit", "穿搭"], ["pose", "姿态"],
    ["scene", "场景"], ["style", "风格"], ["background", "背景"],
    ["continuity", "连续性"], ["edit_source", "编辑源"], ["edit_mask", "编辑遮罩"],
  ];
  const ROLE_LABEL = Object.fromEntries(ROLES);
  const OUTFITS = {"": "不指定", home: "居家服", daily: "日常服", uniform: "制服", formal: "正式礼服", swim: "泳装", custom: "其他"};
  const OUTFIT_LOCKS = {request: "跟随本次要求", reference: "锁定参考图服装"};
  const USAGE_TAGS = [["scenes", "适用场景"], ["times", "适用时间"], ["excluded_scenes", "排除场景"], ["excluded_times", "排除时间"]];
  const QUICK_ROLES = {identity: ["identity", "continuity"], outfit: ["outfit", "edit_source"], scene: ["scene", "continuity"]};
  const defaultUsage = () => ({base_identity: false, outfit_category: "", outfit_lock: "request",
    preferred_scene: "", scenes: [], times: [], excluded_scenes: [], excluded_times: []});
  const MAX_UPLOAD_BYTES = 12 * 1024 * 1024;
  const ERRORS = {
    dashboard_identity_required: "请从已登录的 AstrBot 管理界面打开陪伴小屋。",
    core_access_required: "当前账号没有管理参考素材的权限。",
    bridge_unavailable: "页面还没有接上 AstrBot，请刷新后重试。",
    bridge_timeout: "连接超时，请刷新后重试。",
    staging_not_initialized: "V2 运行实例尚未加载，请先完成账号与会话设置。",
    staging_host_terminated: "插件已经停止，请重新打开管理页面。",
    image_material_request_invalid: "请求信息不完整，请刷新后重新选择素材。",
    generated_album_cursor_invalid: "相册分页已经失效，请刷新相册。",
    generated_image_missing: "请先从当前角色的产出相册选择一张图片。",
    generated_album_thumbnail_invalid: "这张产出图暂时无法预览，请刷新相册。",
    photo_reference_disabled: "参考图功能当前关闭，请先在上方配置中开启。",
    daily_outfit_disabled: "每日穿搭当前关闭，请先在参考素材配置中开启。",
    daily_outfit_image_unavailable: "当前角色没有可用的 Image 生图路线。",
    image_quota_closed: "主动生图额度已关闭。",
    image_quota_exhausted: "今日主动生图额度已用完。",
    wardrobe_disabled: "衣柜当前关闭，请先在上方配置中开启。",
    reference_operation_invalid: "这次登记没有生成有效的操作编号，请重新提交。",
    reference_operation_conflict: "这张图的登记编号已经对应了另一组资料，请重新选择后提交。",
    reference_roles_invalid: "请至少选择一个参考用途。",
    reference_label_invalid: "参考图名称不能超过 120 个字符。",
    reference_upload_too_large: "参考图请控制在 12 MiB 以内。",
    reference_image_type_invalid: "只支持 PNG、JPEG 或 WebP 图片。",
    reference_image_type_mismatch: "图片内容与文件类型不一致，请重新选择。",
    reference_image_invalid: "图片损坏或无法读取，请换一张完整图片。",
    reference_image_animated: "动态图片暂不能登记，请选择静态图片。",
    reference_thumbnail_invalid: "参考图缩略图暂不可读，请刷新后重试。",
    reference_revision_invalid: "参考图已更新，请重新载入后编辑。",
    reference_revision_conflict: "参考图已在其他页面更新，请刷新图库后重试。",
    reference_order_invalid: "图库顺序没有保存，请重新整理后再试。",
    reference_order_conflict: "参考图库已在其他页面更新，请重新读取后再保存顺序。",
    reference_usage_invalid: "使用方式未保存，请核对服装、场景与时间选项。",
    reference_asset_missing: "请先选择一条已登记的参考图。",
    reference_scope_invalid: "参考素材只支持已绑定的私聊角色。",
    reference_context_invalid: "当前角色上下文已变化，请刷新后重试。",
    binding_rebound: "绑定对象已经变化，请重新打开当前角色。",
    reference_target_changed: "当前角色或私聊对象已经变化，请刷新后重试。",
    wardrobe_operation_conflict: "这个衣柜编号已经对应另一件衣物，请重新提交。",
    wardrobe_tags_invalid: "标签请用逗号分隔，并保持为文字。",
    wardrobe_reference_invalid: "衣柜图片必须是已登记的穿搭参考图。",
    wardrobe_item_missing: "请先选择一件有效的衣物。",
    invalid_response: "返回的数据不完整，请刷新后重试。",
  };
  const state = {
    root: null, scope: "", ticket: 0, abort: null,
    inventory: null, inventoryLoading: false, inventoryError: "",
    outfitPhoto: null, outfitError: "", outfitTimer: null, outfitOperation: "",
    album: [], albumCursor: null, albumLoaded: false, albumLoading: false, albumError: "",
    selectedImage: "", selectedRoles: new Set(["identity"]), referenceLabel: "", referenceKey: "",
    uploadFile: null, uploadDataUrl: "", uploadLabel: "", uploadRoles: new Set(["identity"]), uploadKey: "",
    searchQuery: "", filterRole: "", editAssetId: "", editRevision: 0, editLabel: "", editRoles: new Set(), editUsage: defaultUsage(),
    galleryBaseOrder: [], galleryDraftOrder: [],
    wardrobeName: "", wardrobeDescription: "", wardrobeTags: "", wardrobeAssetId: "", wardrobeKey: "",
    action: "", notice: "", noticeError: false,
    uploadOpen: false, wardrobeOpen: false, showWithdrawnWardrobe: false,
  };

  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
  const makeError = code => Object.assign(new Error(ERRORS[code] || "请求没有完成，请刷新后重试。"), {code});
  const product = () => window.CompanionProduct;
  const live = ticket => state.root?.isConnected && ticket === state.ticket
    && state.scope === (product()?.scopeKey?.() || "");
  const role = () => product()?.role?.() || null;

  function operationKey(prefix) {
    let value = "";
    try { value = window.crypto?.randomUUID?.() || ""; } catch (_) {}
    if (!value) value = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    return `${prefix}-${value}`;
  }

  function apiPath(suffix = "") {
    const current = product();
    const endpoint = role()?.image_material_api;
    if (!current || !state.scope) throw makeError("reference_scope_invalid");
    if (typeof endpoint !== "string" || !endpoint) throw makeError("image_material_unavailable");
    try { return current.relative(endpoint) + suffix; }
    catch (_) { throw makeError("invalid_response"); }
  }

  function unwrap(response) {
    const current = product();
    if (current?.unwrap) return current.unwrap(response);
    const wrapped = ["ok", "error"].includes(response?.status) && response?.data !== undefined ? response
      : ["ok", "error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    if (wrapped?.status === "error") throw makeError(wrapped.message);
    const value = wrapped ? wrapped.data : response;
    if (!value || typeof value !== "object" || Array.isArray(value)) throw makeError("invalid_response");
    return value;
  }

  async function request(method, path, body) {
    const current = product();
    const bridge = current?.bridgeNow?.() || await current?.bridgeFor?.();
    if (!bridge) throw makeError("bridge_unavailable");
    try {
      const response = method === "GET" ? await bridge.apiGet(path, body) : await bridge.apiPost(path, body);
      return unwrap(response);
    } catch (error) {
      if (error?.code && ERRORS[error.code]) throw makeError(error.code);
      throw error;
    }
  }

  function errorText(error, fallback) {
    return ERRORS[error?.code] || ERRORS[error?.message] || fallback;
  }

  function settings() {
    return state.inventory?.settings || {};
  }
  function referenceSettings() { return settings().references || null; }
  function wardrobeSettings() { return settings().wardrobe || null; }
  function referencesEnabled() { return referenceSettings()?.enabled === true; }
  function wardrobeEnabled() { return wardrobeSettings()?.enabled === true; }
  function dailyEnabled() {
    return referencesEnabled() && referenceSettings()?.daily_outfit_enabled === true && wardrobeEnabled();
  }
  function activeReferences() {
    return (state.inventory?.references || []).filter(item => item?.state === "active");
  }
  function orderedReferences() {
    const records = activeReferences();
    const byId = new Map(records.map(item => [item.asset_id, item]));
    const ordered = state.galleryDraftOrder.map(id => byId.get(id)).filter(Boolean);
    const included = new Set(ordered.map(item => item.asset_id));
    return ordered.concat(records.filter(item => !included.has(item.asset_id)));
  }
  function galleryIds(value) {
    if (!Array.isArray(value?.references)) throw makeError("invalid_response");
    const ids = value.references.filter(item => item?.state === "active").map(item => item.asset_id);
    if (ids.some(id => typeof id !== "string" || !id) || new Set(ids).size !== ids.length) {
      throw makeError("invalid_response");
    }
    return ids;
  }
  function inventoryDocument(value) {
    if (!Array.isArray(value?.references) || !Array.isArray(value?.wardrobe)
        || !value.settings || typeof value.settings !== "object") throw makeError("invalid_response");
    galleryIds(value);
    return value;
  }
  function orderedInventory(value, ids) {
    const byId = new Map(value.references.map(item => [item.asset_id, item]));
    const active = ids.map(id => byId.get(id)).filter(Boolean);
    return {...value, references: active.concat(value.references.filter(item => item?.state !== "active"))};
  }
  function acceptInventory(value, draftOrder = null) {
    const baseOrder = galleryIds(value);
    const active = new Set(baseOrder);
    const next = draftOrder ? draftOrder.filter(id => active.has(id)) : [...baseOrder];
    const included = new Set(next);
    baseOrder.forEach(id => { if (!included.has(id)) next.push(id); });
    state.inventory = orderedInventory(value, next);
    state.galleryBaseOrder = baseOrder;
    state.galleryDraftOrder = next;
  }
  function matchesReference(item) {
    const query = state.searchQuery.trim().toLocaleLowerCase();
    const label = String(item.label || "").toLocaleLowerCase();
    const roles = item.roles || [];
    return (!state.filterRole || roles.includes(state.filterRole))
      && (!query || label.includes(query) || roles.some(value => (ROLE_LABEL[value] || value).toLocaleLowerCase().includes(query)));
  }
  function galleryOrderDirty() {
    return JSON.stringify(state.galleryDraftOrder) !== JSON.stringify(state.galleryBaseOrder);
  }
  function writesLocked() { return !!state.action || state.inventoryLoading; }
  const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  const usage = item => ({...defaultUsage(), ...item?.usage});
  const options = (values, selected) => Object.entries(values).map(([value, label]) => `<option value="${esc(value)}" ${selected === value ? "selected" : ""}>${esc(label)}</option>`).join("");
  function activeWardrobe() {
    return (state.inventory?.wardrobe || []).filter(item => item?.state === "active");
  }
  function withdrawnWardrobe() {
    return (state.inventory?.wardrobe || []).filter(item => item?.state !== "active");
  }
  function withdrawnWardrobeCount() {
    const reported = state.inventory?.wardrobe_counts?.withdrawn;
    const listed = withdrawnWardrobe().length;
    return Number.isInteger(reported) && reported > listed ? reported : listed;
  }

  function setNotice(message, isError = false) {
    state.notice = message || "";
    state.noticeError = !!isError;
  }

  function statusCard(title, body, kind = "empty") {
    return `<div class="image-materials-status image-materials-status--${kind}" role="status"><strong>${esc(title)}</strong>${kind === "error" && body ? `<p>${esc(body)}</p>` : ""}</div>`;
  }

  function renderSettings() {
    if (state.inventoryLoading && !state.inventory) return statusCard("正在读取当前角色", "正在核对参考图、衣柜和每日穿搭的配置。", "loading");
    if (!state.inventory) return "";
    const refs = referenceSettings();
    const wardrobe = wardrobeSettings();
    const refLabel = refs ? (refs.enabled ? "已开启" : "已关闭") : "未读取";
    const wardrobeLabel = wardrobe ? (wardrobe.enabled ? "已开启" : "已关闭") : "未读取";
    const dailyLabel = refs?.daily_outfit_enabled ? "已开启" : "已关闭";
    return `<div class="image-materials-switches" aria-label="素材功能状态">
      <span class="image-materials-switch ${refs?.enabled ? "is-on" : ""}"><b>参考图</b><em>${esc(refLabel)}</em></span>
      <span class="image-materials-switch ${wardrobe?.enabled ? "is-on" : ""}"><b>衣柜</b><em>${esc(wardrobeLabel)}</em></span>
      <span class="image-materials-switch ${refs?.daily_outfit_enabled ? "is-on" : ""}"><b>每日穿搭</b><em>${esc(dailyLabel)}</em></span>
    </div>`;
  }

  function renderAlbum() {
    const items = state.album;
    let body = "";
    if (state.albumLoading && !items.length) body = statusCard("正在读取产出相册", "只展示已经治理过的生成图；登记前不会自动成为参考素材。", "loading");
    else if (state.albumError && !items.length) body = statusCard("产出相册暂不可读", state.albumError, "error");
    else if (!state.albumLoaded && !items.length) body = statusCard("等待读取相册", "点击刷新后读取当前角色的已生成图片。", "empty");
    else if (!items.length) body = statusCard("相册里还没有生成图", "先完成一次图像生成，生成图才会出现在这里。", "empty");
    else {
      body = `<div class="image-materials-album-grid">${items.map(item => {
        const selected = item.image_id === state.selectedImage;
        const action = item.action ? ` · ${item.action}` : "";
        return `<button type="button" class="image-materials-album-item${selected ? " is-selected" : ""}" data-im-select="${esc(item.image_id)}" aria-pressed="${selected}" aria-label="选择${esc(item.created_at || "生成图片")}">
          <img src="${esc(item.thumbnail)}" alt="${esc(item.created_at || "生成图片")}" loading="lazy">
          <span class="image-materials-album-meta"><b>${esc(item.width || "—")} × ${esc(item.height || "—")}</b><small>${esc((item.created_at || "时间未记录") + action)}</small></span>
        </button>`;
      }).join("")}</div>`;
      if (state.albumCursor) body += `<button type="button" class="button small image-materials-load-more" data-im-load-more ${state.albumLoading ? "disabled" : ""}>${state.albumLoading ? "正在读取…" : "读取更早的产出"}</button>`;
    }
    return `<section class="image-materials-section image-materials-album-section"><header class="image-materials-section-head"><h4>从产出相册登记</h4><button type="button" class="button small" data-im-refresh ${state.albumLoading || state.inventoryLoading || state.action ? "disabled" : ""}>刷新</button></header>${body}${renderReferenceEditor()}</section>`;
  }

  function renderRoleOptions(prefix, selectedRoles) {
    return ROLES.map(([value, label]) => `<label class="image-materials-role"><input type="checkbox" data-im-${prefix}-role="${value}" ${selectedRoles.has(value) ? "checked" : ""}><span>${label}</span></label>`).join("");
  }

  function renderRoles() {
    return renderRoleOptions("role", state.selectedRoles);
  }

  function renderReferenceEditor() {
    const selected = state.album.find(item => item.image_id === state.selectedImage);
    if (!selected) return statusCard("先选择一张产出图", "选中后可以为它登记参考用途；未登记的相册图片不会参与后续生图。", "empty");
    const disabled = !referencesEnabled() || writesLocked();
    return `<div class="image-materials-editor image-materials-reference-editor">
      <div class="image-materials-selected"><img src="${esc(selected.thumbnail)}" alt="已选择的产出图"><div><strong>已选择一张产出图</strong><span>${esc(selected.created_at || "时间未记录")}</span></div></div>
      <label class="image-materials-field"><span>名称（可选）</span><input type="text" maxlength="120" data-im-reference-label value="${esc(state.referenceLabel)}" placeholder="例如：春日头像"></label>
      <fieldset class="image-materials-roles"><legend>允许的参考用途</legend><div class="image-materials-role-grid">${renderRoles()}</div></fieldset>
       <button type="button" class="button primary" data-im-register ${disabled || !state.selectedRoles.size ? "disabled" : ""}>${state.action === "register-reference" ? "正在登记…" : "登记为参考素材"}</button>
    </div>`;
  }

  function renderUploadForm() {
    const disabled = !referencesEnabled() || writesLocked();
    const preview = state.uploadDataUrl
      ? `<div class="image-materials-upload-preview"><img src="${esc(state.uploadDataUrl)}" alt="待登记参考图"><span>${esc(state.uploadFile?.name || "已选择图片")}</span></div>`
      : `<div class="image-materials-upload-preview is-empty"><span>选择图片后预览</span></div>`;
    return `<div class="image-materials-upload" id="image-materials-upload"${state.uploadOpen ? "" : " hidden"}>
      ${preview}
      <div class="image-materials-upload-fields">
        <label class="image-materials-field"><span>上传图片</span><input type="file" accept="image/png,image/jpeg,image/webp" data-im-upload-file ${disabled ? "disabled" : ""}></label>
        <label class="image-materials-field"><span>名称（可选）</span><input type="text" maxlength="120" data-im-upload-label value="${esc(state.uploadLabel)}" placeholder="例如：日常形象" ${disabled ? "disabled" : ""}></label>
        <fieldset class="image-materials-roles"><legend>参考用途</legend><div class="image-materials-role-grid">${renderRoleOptions("upload", state.uploadRoles)}</div></fieldset>
        <button type="button" class="button primary" data-im-upload ${disabled || !state.uploadDataUrl || !state.uploadRoles.size ? "disabled" : ""}>${state.action === "upload-reference" ? "正在上传…" : "加入图库"}</button>
      </div>
    </div>`;
  }

  function renderReferenceEdit(item) {
    if (state.editAssetId !== item.asset_id) return "";
    const disabled = !referencesEnabled() || writesLocked();
    const chosen = state.editUsage;
    const scenes = Object.fromEntries((state.inventory?.scene_presets || []).map(preset => [preset.id, preset.name]));
    if (chosen.preferred_scene && !scenes[chosen.preferred_scene]) scenes[chosen.preferred_scene] = "已保存场景";
    return `<div class="image-materials-reference-edit">
      <header class="image-materials-section-head"><h4>${esc(item.label || "参考图")} · 使用方式</h4></header>
      <label class="image-materials-field"><span>名称</span><input type="text" maxlength="120" data-im-edit-label value="${esc(state.editLabel)}" ${disabled ? "disabled" : ""}></label>
      <label class="image-materials-base-toggle"><input type="checkbox" data-im-edit-usage="base_identity" ${chosen.base_identity ? "checked" : ""} ${disabled ? "disabled" : ""}><span>基础身份参考</span></label>
      <fieldset class="image-materials-roles"><legend>参考用途</legend><div class="image-materials-role-grid">${renderRoleOptions("edit", state.editRoles)}</div></fieldset>
      <div class="image-materials-quick-roles"><button type="button" class="button small" data-im-quick-roles="identity" ${disabled ? "disabled" : ""}>${icon("user-round")}身份锚点</button><button type="button" class="button small" data-im-quick-roles="outfit" ${disabled ? "disabled" : ""}>${icon("shirt")}换衣底图</button><button type="button" class="button small" data-im-quick-roles="scene" ${disabled ? "disabled" : ""}>${icon("image")}场景延续</button></div>
      <div class="image-materials-usage-choices">
        <label class="image-materials-field"><span>服装类别</span><select data-im-edit-usage="outfit_category" ${disabled ? "disabled" : ""}>${options(OUTFITS, chosen.outfit_category)}</select></label>
        <label class="image-materials-field"><span>默认服装锁</span><select data-im-edit-usage="outfit_lock" ${disabled ? "disabled" : ""}>${options(OUTFIT_LOCKS, chosen.outfit_lock)}</select></label>
        <label class="image-materials-field"><span>首选场景预设</span><select data-im-edit-usage="preferred_scene" ${disabled ? "disabled" : ""}>${options({"": "不指定", ...scenes}, chosen.preferred_scene)}</select></label>
      </div>
      <div class="image-materials-form-grid">${USAGE_TAGS.map(([key, label]) => `<label class="image-materials-field"><span>${label}</span><input type="text" maxlength="1295" data-im-edit-usage="${key}" value="${esc(chosen[key].join("，"))}" ${disabled ? "disabled" : ""}></label>`).join("")}</div>
      <div class="image-materials-gallery-actions"><button type="button" class="button small primary" data-im-save-ref="${esc(item.asset_id)}" ${disabled || !state.editRoles.size ? "disabled" : ""}>${state.action === "edit-reference" ? "正在保存…" : "保存"}</button><button type="button" class="button small" data-im-cancel-edit>取消</button></div>
    </div>`;
  }

  function renderReferenceList() {
    const records = orderedReferences();
    const filtered = records.filter(matchesReference);
    const cards = filtered.map((item, visibleIndex) => {
      const roles = (item.roles || []).map(value => ROLE_LABEL[value] || value).join("、") || "未声明用途";
      const image = item.thumbnail
        ? `<img src="${esc(item.thumbnail)}" alt="${esc(item.label || "参考图")}" loading="lazy">`
        : `<div class="image-materials-gallery-placeholder" role="img" aria-label="没有可用预览">预览不可用</div>`;
      return `<article class="image-materials-gallery-card${state.editAssetId === item.asset_id ? " is-selected" : ""}" data-im-reference-card="${esc(item.asset_id)}">
        <div class="image-materials-gallery-image">${image}</div>
        <div class="image-materials-gallery-copy"><strong>${esc(item.label || "未命名参考图")}</strong><span>${esc(roles)}</span><small>${esc(item.mime_type || "图片")} · ${esc(item.size_bytes || 0)} B</small></div>
        <div class="image-materials-gallery-actions"><div class="image-materials-gallery-order"><button type="button" class="output-icon-button" data-im-move-ref="up" data-im-asset="${esc(item.asset_id)}" aria-label="上移${esc(item.label || "参考图")}" title="上移" ${state.action || visibleIndex <= 0 ? "disabled" : ""}>${icon("arrow-up")}</button><button type="button" class="output-icon-button" data-im-move-ref="down" data-im-asset="${esc(item.asset_id)}" aria-label="下移${esc(item.label || "参考图")}" title="下移" ${state.action || visibleIndex >= filtered.length - 1 ? "disabled" : ""}>${icon("arrow-down")}</button></div><button type="button" class="button small" data-im-edit-ref="${esc(item.asset_id)}" ${writesLocked() ? "disabled" : ""}>使用方式</button><button type="button" class="button small danger" data-im-withdraw-ref="${esc(item.asset_id)}" data-im-revision="${esc(item.revision || 1)}" ${writesLocked() ? "disabled" : ""}>撤回</button></div>
      </article>`;
    }).join("");
    const empty = !records.length
      ? statusCard("图库还是空的", "上传一张图片，或从产出相册登记现有图片。", "empty")
      : statusCard("没有匹配的参考图", "调整搜索内容或用途筛选。", "empty");
    const base = records.find(item => usage(item).base_identity);
    const identity = base || records.find(item => (item.roles || []).includes("identity"));
    const basePreview = base?.thumbnail ? `<img src="${esc(base.thumbnail)}" alt="基础身份参考">` : icon("user-round");
    const baseRow = `<div class="image-materials-base-reference"><div class="image-materials-base-preview">${basePreview}</div><strong>${base ? `基础身份参考 · ${esc(base.label || "未命名参考图")}` : "基础身份参考未设置"}</strong><button type="button" class="button small" data-im-edit-ref="${esc(identity?.asset_id || "")}" ${!identity || writesLocked() ? "disabled" : ""}>配置使用方式</button></div>`;
    return `<div class="image-materials-gallery-tools">
      <label class="image-materials-field"><span class="image-materials-sr-only">搜索名称或用途</span><input type="search" data-im-search value="${esc(state.searchQuery)}" placeholder="搜索名称或用途" autocomplete="off"></label>
      <label class="image-materials-field"><span>用途</span><select data-im-filter-role><option value="">全部用途</option>${ROLES.map(([value, label]) => `<option value="${value}" ${state.filterRole === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
      <span class="image-materials-count">${filtered.length} / ${records.length}${galleryOrderDirty() ? " · 顺序未保存" : ""}</span>
    </div>${baseRow}${filtered.length ? `<div class="image-materials-gallery-grid">${cards}</div>` : empty}${renderReferenceEdit(records.find(item => item.asset_id === state.editAssetId) || {})}`;
  }

  function renderWardrobeForm() {
    const refs = activeReferences().filter(item => (item.roles || []).includes("outfit"));
    const disabled = !wardrobeEnabled() || writesLocked();
    return `<div class="image-materials-editor image-materials-wardrobe-editor" id="image-materials-wardrobe-add"${state.wardrobeOpen ? "" : " hidden"}>
      <div class="image-materials-form-heading"><div><h5>新增衣柜条目</h5></div></div>
      <div class="image-materials-form-grid">
        <label class="image-materials-field"><span>名称</span><input type="text" maxlength="120" data-im-wardrobe-name value="${esc(state.wardrobeName)}" placeholder="例如：米白针织衫"></label>
        <label class="image-materials-field"><span>标签</span><input type="text" maxlength="640" data-im-wardrobe-tags value="${esc(state.wardrobeTags)}" placeholder="针织，浅色，日常"></label>
      </div>
      <label class="image-materials-field"><span>描述（可选）</span><textarea maxlength="2000" rows="3" data-im-wardrobe-description placeholder="记录材质、版型或适合的场合">${esc(state.wardrobeDescription)}</textarea></label>
      <label class="image-materials-field"><span>关联穿搭参考图（可选）</span><select data-im-wardrobe-asset><option value="">不关联图片</option>${refs.map((item, index) => `<option value="${esc(item.asset_id)}" ${state.wardrobeAssetId === item.asset_id ? "selected" : ""}>${esc(item.label || `穿搭参考图 ${index + 1}`)}</option>`).join("")}</select></label>
      <button type="button" class="button primary" data-im-wardrobe-submit ${disabled || !state.wardrobeName.trim() ? "disabled" : ""}>${state.action === "register-wardrobe" ? "正在保存…" : "加入衣柜"}</button>
    </div>`;
  }

  function renderWardrobeItem(item, daily) {
    const active = item.state === "active";
    const selected = daily?.item_id === item.item_id;
    return `<li class="image-materials-record ${active ? "" : "is-withdrawn"}${selected ? " is-daily" : ""}"><div><strong>${esc(item.name || "未命名衣物")}</strong><span>${esc((item.tags || []).join("、") || item.description || "未填写描述")}${item.asset_id ? " · 已关联图片" : ""}</span></div><div class="image-materials-record-action">${active && dailyEnabled() ? `<button type="button" class="button small" data-im-daily="${esc(item.item_id)}" ${writesLocked() || selected ? "disabled" : ""}>${state.action === "daily-" + item.item_id ? "正在选择…" : selected ? "今日参考" : "设为今日参考"}</button>` : ""}${active ? `<button type="button" class="button small danger" data-im-withdraw-wardrobe="${esc(item.item_id)}" ${writesLocked() ? "disabled" : ""}>撤回</button>` : ""}${!active ? "<em>已撤回</em>" : ""}</div></li>`;
  }

  function renderWardrobeList() {
    const active = activeWardrobe();
    const withdrawn = withdrawnWardrobe();
    const daily = state.inventory?.daily_outfit;
    if (!active.length && !withdrawn.length) return statusCard("衣柜还是空的", "", "empty");
    const activeBlock = active.length
      ? `<ul class="image-materials-record-list image-materials-wardrobe-list">${active.map(item => renderWardrobeItem(item, daily)).join("")}</ul>`
      : statusCard("衣柜里还没有生效衣物", "已撤回的衣物默认折叠收纳，点击上方按钮即可展开查看。", "empty");
    const withdrawnBlock = state.showWithdrawnWardrobe && withdrawn.length
      ? `<div class="image-materials-withdrawn-group"><h5 class="image-materials-withdrawn-title">已撤回衣物（${withdrawn.length}）</h5><ul class="image-materials-record-list image-materials-wardrobe-list is-withdrawn-list">${withdrawn.map(item => renderWardrobeItem(item, daily)).join("")}</ul></div>`
      : "";
    return activeBlock + withdrawnBlock;
  }

  const outfitInProgress = value => ["created", "admitted", "running", "retry_scheduled", "cancelling"].includes(value?.state);

  function renderOutfitPhoto() {
    const value = state.outfitPhoto, photo = value?.photo;
    const enabled = referenceSettings()?.daily_outfit_enabled === true;
    const working = state.action === "generate-outfit" || outfitInProgress(value);
    const thumbnail = typeof photo?.thumbnail === "string" && photo.thumbnail.startsWith("data:image/jpeg;base64,")
      ? photo.thumbnail : "";
    const status = state.outfitError || (working ? "正在生成穿搭图…" : !state.inventory ? "正在读取设置…"
      : !enabled ? "每日穿搭图已关闭" : !value ? "正在读取今日穿搭…"
        : value.state === "uncertain" ? "上次生图结果待确认" : ["failed", "expired", "cancelled", "dead_letter"].includes(value.state)
          ? ERRORS[value.code] || "这次穿搭图没有生成" : thumbnail ? "" : photo ? "今日穿搭图暂不可预览" : "今天还没有穿搭图");
    const weather = photo?.weather;
    const weatherText = weather ? [weather.role_location, weather.condition, `${weather.temperature_c}°C`].join(" · ") : "";
    const outfitTip = photo?.filename ? `点击查看高清原图（${photo.filename}）` : "点击放大查看高清原图";
    return `<header class="image-materials-section-head"><h4>每日穿搭图</h4>
      <button type="button" class="button small primary" data-im-generate-outfit ${!enabled || !value || working || state.action ? "disabled" : ""}>
        <i data-lucide="${thumbnail ? "refresh-cw" : "image-plus"}" aria-hidden="true"></i>${thumbnail ? "重新生成" : "生成今日穿搭"}</button></header>
      ${thumbnail ? `<div class="image-materials-outfit-body"><button type="button" class="image-materials-outfit-trigger" data-im-open-outfit aria-label="放大查看今日穿搭原图" title="${esc(outfitTip)}"><img src="${esc(thumbnail)}" alt="今日角色穿搭图"><span class="image-materials-outfit-zoom" aria-hidden="true">${icon("zoom-in")}<span>查看原图</span></span></button><dl>
        <div><dt>日期</dt><dd>${esc(value.local_date)}</dd></div>
        ${weatherText ? `<div><dt>天气</dt><dd>${esc(weatherText)}</dd></div>` : ""}
        ${photo.activities?.length ? `<div><dt>当日安排</dt><dd>${esc(photo.activities.join("、"))}</dd></div>` : ""}
      </dl></div>` : ""}
      <p role="status" class="image-materials-outfit-status" ${status ? "" : "hidden"}>${esc(status)}</p>`;
  }

  function openOutfitLightbox() {
    const value = state.outfitPhoto, photo = value?.photo;
    const thumbnail = typeof photo?.thumbnail === "string" && photo.thumbnail.startsWith("data:image/jpeg;base64,")
      ? photo.thumbnail : "";
    if (!thumbnail) return;
    let albumBase = "";
    try {
      if (photo.image_id) albumBase = apiPath("/album");
    } catch {}
    const localDate = photo.local_date || value.local_date || "";
    const fallbackName = `daily-outfit-${localDate || "today"}.png`;
    window.CompanionOutputPage?.openLightbox?.({
      image_id: photo.image_id || `wardrobe-outfit-preview:${localDate || "today"}`,
      filename: photo.filename || fallbackName,
      mime_type: photo.mime_type || "",
      width: photo.width ?? null,
      height: photo.height ?? null,
      size_bytes: photo.size_bytes ?? null,
      created_at: photo.created_at || "",
      action: "wardrobe.outfit",
      local_date: localDate,
      weather: photo.weather || null,
      activities: Array.isArray(photo.activities) ? photo.activities : [],
      thumbnail,
      _albumEndpoint: albumBase,
    });
  }

  function paintOutfitPhoto() {
    const root = state.root?.querySelector("[data-im-outfit-photo]");
    if (root) {
      root.innerHTML = renderOutfitPhoto();
      window.lucide?.createIcons?.({nodes: root.querySelectorAll("[data-lucide]")});
    }
  }

  async function loadOutfitPhoto(ticket) {
    if (!live(ticket)) return;
    clearTimeout(state.outfitTimer);
    const wasWorking = outfitInProgress(state.outfitPhoto) || state.action === "generate-outfit";
    try {
      const value = await request("GET", apiPath("/daily-outfit/photo"));
      if (!live(ticket)) return;
      if (typeof value.state !== "string" || typeof value.local_date !== "string") throw makeError("invalid_response");
      state.outfitPhoto = value; state.outfitError = "";
      if (outfitInProgress(value)) state.outfitTimer = setTimeout(() => loadOutfitPhoto(ticket), 2200);
      else if (wasWorking && value.state === "succeeded") window.CompanionOutputPage?.refreshAlbum?.();
    } catch (error) {
      if (live(ticket)) {
        state.outfitError = errorText(error, "今日穿搭图暂不可读。");
        if (outfitInProgress(state.outfitPhoto)) state.outfitTimer = setTimeout(() => loadOutfitPhoto(ticket), 4000);
      }
    }
    if (live(ticket)) paintOutfitPhoto();
  }

  async function generateOutfitPhoto() {
    if (!referenceSettings()?.daily_outfit_enabled || state.action || outfitInProgress(state.outfitPhoto)) return;
    const ticket = state.ticket;
    state.outfitOperation ||= operationKey("outfit-photo");
    state.action = "generate-outfit"; state.outfitError = ""; render();
    try {
      await request("POST", apiPath("/daily-outfit/generate"), {operation_key: state.outfitOperation});
      if (live(ticket)) {
        state.outfitOperation = "";
        await loadOutfitPhoto(ticket);
      }
    } catch (error) {
      if (live(ticket)) state.outfitError = errorText(error, "穿搭图未能开始生成，请重试。");
    } finally {
      if (live(ticket)) { state.action = ""; render(); }
    }
  }

  function render() {
    const root = state.root;
    if (!root?.isConnected) return;
    if (!state.scope || !role()?.image_material_api) {
      root.innerHTML = `<section class="image-materials-card"><div class="image-materials-empty-head"><h3>参考素材与衣柜</h3></div>${statusCard("等待私聊角色绑定", "参考图和衣柜需要一个已绑定的私聊角色。先到「账号与会话」完成绑定，再返回这里。", "empty")}</section>`;
      return;
    }
    const error = state.inventoryError || (!state.album.length && state.albumError);
    root.innerHTML = `<section class="image-materials-card">
      ${state.notice ? `<div class="image-materials-notice ${state.noticeError ? "is-error" : ""}" role="status">${esc(state.notice)}</div>` : ""}
      ${error ? statusCard("素材状态暂不可读", errorText({message:error}, "请刷新后重试。"), "error") : ""}
      <section class="image-materials-outfit" data-im-outfit-photo aria-label="每日穿搭图">${renderOutfitPhoto()}</section>
      <section class="image-materials-library"><header class="image-materials-section-head"><h4>参考图库</h4><span class="image-materials-count">${activeReferences().length} / 24 张生效</span><div class="image-materials-section-tools"><button type="button" class="button small primary" data-im-toggle-upload aria-expanded="${state.uploadOpen}" aria-controls="image-materials-upload">${state.uploadOpen ? "收起上传" : "添加参考图"}</button><button type="button" class="button small" data-im-refresh ${state.inventoryLoading || state.albumLoading || state.action ? "disabled" : ""}>刷新状态</button><button type="button" class="button small" data-im-save-gallery ${!galleryOrderDirty() || state.action || state.inventoryLoading ? "disabled" : ""}>${state.action === "save-gallery" ? "正在保存…" : "保存图库"}</button></div></header>${renderUploadForm()}${renderReferenceList()}</section>
      <div class="image-materials-columns">${renderAlbum()}<section class="image-materials-section image-materials-wardrobe-section"><header class="image-materials-section-head"><h4>衣柜</h4><span class="image-materials-count">${activeWardrobe().length} / 24 件生效</span></header><div class="image-materials-wardrobe-tools"><button type="button" class="button small image-materials-add-wardrobe" data-im-toggle-wardrobe aria-expanded="${state.wardrobeOpen}" aria-controls="image-materials-wardrobe-add">${state.wardrobeOpen ? "收起新增" : "新增衣物"}</button>${withdrawnWardrobeCount() > 0 ? `<button type="button" class="button small" data-im-toggle-withdrawn-wardrobe aria-pressed="${state.showWithdrawnWardrobe}">${state.showWithdrawnWardrobe ? "隐藏已撤回" : `显示已撤回 (${withdrawnWardrobeCount()})`}</button>` : ""}</div>${renderWardrobeForm()}${renderWardrobeList()}</section></div>
    </section>`;
    window.lucide?.createIcons?.({attrs: {"aria-hidden": "true"}});
  }

  async function loadInventory(ticket, syncEdit = false) {
    if (!live(ticket)) return;
    state.inventoryLoading = true; state.inventoryError = ""; render();
    try {
      const value = inventoryDocument(await request("GET", apiPath()));
      if (!live(ticket)) return;
      acceptInventory(value, galleryOrderDirty() ? [...state.galleryDraftOrder] : null);
      const edited = activeReferences().find(item => item.asset_id === state.editAssetId);
      if (syncEdit || !edited) {
        const item = edited || activeReferences()[0];
        state.editAssetId = item?.asset_id || "";
        state.editRevision = item?.revision || 0;
        state.editLabel = item?.label || "";
        state.editRoles = new Set(item?.roles || []);
        state.editUsage = usage(item);
      }
    } catch (error) {
      if (live(ticket)) { state.inventory = null; state.inventoryError = errorText(error, "参考素材暂不可读，请刷新后重试。"); }
    } finally {
      if (live(ticket)) { state.inventoryLoading = false; render(); }
    }
  }

  async function loadAlbum(ticket, append = false) {
    if (!live(ticket) || state.albumLoading || (append && !state.albumCursor)) return;
    state.albumLoading = true; if (!append) state.albumError = ""; render();
    try {
      const value = await request("GET", apiPath("/album"), append ? {cursor: state.albumCursor} : undefined);
      if (!live(ticket)) return;
      if (!Array.isArray(value.items)) throw makeError("invalid_response");
      const incoming = value.items.filter(item => item && typeof item.image_id === "string");
      if (append) {
        const known = new Set(state.album.map(item => item.image_id));
        state.album.push(...incoming.filter(item => !known.has(item.image_id)));
      } else state.album = incoming;
      if (state.selectedImage && !state.album.some(item => item.image_id === state.selectedImage)) state.selectedImage = "";
      state.albumCursor = typeof value.next_cursor === "string" && value.next_cursor ? value.next_cursor : null;
      state.albumLoaded = true;
    } catch (error) {
      if (live(ticket)) state.albumError = errorText(error, "产出相册暂不可读，请刷新后重试。");
    } finally {
      if (live(ticket)) { state.albumLoading = false; render(); }
    }
  }

  async function refresh() {
    const current = product();
    const nextScope = current?.scopeKey?.() || "";
    const previousScope = state.scope;
    if (state.action && previousScope === nextScope) return;
    state.action = "";
    state.scope = nextScope;
    const ticket = ++state.ticket;
    clearTimeout(state.outfitTimer);
    state.outfitPhoto = null; state.outfitError = "";
    /* A refresh supersedes an in-flight request.  Reset the local busy flags as
       well: the old promise deliberately cannot clear them after its ticket is
       stale, and otherwise the album's append guard would leave the new read
       permanently blocked. */
    state.inventoryLoading = false; state.albumLoading = false;
    state.inventory = null; state.inventoryError = ""; state.album = []; state.albumCursor = null;
    state.albumLoaded = false; state.albumError = ""; setNotice("");
    state.editAssetId = ""; state.editRevision = 0; state.editLabel = ""; state.editRoles = new Set(); state.editUsage = defaultUsage();
    if (previousScope !== nextScope) {
      state.outfitOperation = "";
      state.galleryBaseOrder = []; state.galleryDraftOrder = [];
      state.uploadFile = null; state.uploadDataUrl = ""; state.uploadLabel = "";
      state.uploadRoles = new Set(["identity"]); state.uploadKey = operationKey("upload");
      state.selectedImage = ""; state.selectedRoles = new Set(["identity"]); state.referenceLabel = ""; state.referenceKey = operationKey("reference");
      state.wardrobeName = ""; state.wardrobeDescription = ""; state.wardrobeTags = ""; state.wardrobeAssetId = ""; state.wardrobeKey = operationKey("wardrobe");
      state.searchQuery = ""; state.filterRole = "";
      state.uploadOpen = false; state.wardrobeOpen = false; state.showWithdrawnWardrobe = false;
    }
    render();
    if (!nextScope || !role()?.image_material_api) return;
    await Promise.allSettled([loadInventory(ticket), loadAlbum(ticket), loadOutfitPhoto(ticket)]);
  }

  async function registerReference() {
    if (!state.selectedImage || !state.selectedRoles.size || !referencesEnabled() || state.action) return;
    const ticket = state.ticket; state.action = "register-reference"; render();
    try {
      await request("POST", apiPath("/references"), {
        image_id: state.selectedImage, label: state.referenceLabel.trim(), roles: [...state.selectedRoles],
        operation_key: state.referenceKey || (state.referenceKey = operationKey("reference")),
      });
      if (!live(ticket)) return;
      setNotice("已登记为参考素材。相册图片仍保留在原位置，登记用途可以单独撤回。");
      await loadInventory(ticket);
    } catch (error) { if (live(ticket)) setNotice(errorText(error, "参考图登记没有完成，请重试。"), true); }
    finally { if (live(ticket)) { state.action = ""; render(); } }
  }

  async function chooseUploadFile(file) {
    state.uploadOpen = true;
    state.uploadFile = null;
    state.uploadDataUrl = "";
    state.uploadLabel = "";
    state.uploadKey = operationKey("upload");
    setNotice("");
    if (!file) { render(); return; }
    const allowed = ["image/png", "image/jpeg", "image/webp"];
    if (!allowed.includes(String(file.type || "").toLowerCase())) {
      setNotice("只支持 PNG、JPEG 或 WebP 图片。", true);
      render();
      return;
    }
    if (!Number.isFinite(Number(file.size)) || Number(file.size) <= 0 || Number(file.size) > MAX_UPLOAD_BYTES) {
      setNotice("参考图请控制在 12 MiB 以内。", true);
      render();
      return;
    }
    const ticket = state.ticket;
    const uploadKey = state.uploadKey;
    state.uploadFile = file;
    state.uploadLabel = String(file.name || "").replace(/\.[^.]+$/, "").trim().slice(0, 120);
    render();
    try {
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(makeError("reference_image_invalid"));
        reader.readAsDataURL(file);
      });
      if (!live(ticket) || uploadKey !== state.uploadKey) return;
      if (!/^data:image\/(png|jpeg|webp);base64,/.test(dataUrl)) throw makeError("reference_image_invalid");
      state.uploadDataUrl = dataUrl;
      render();
    } catch (error) {
      if (live(ticket) && uploadKey === state.uploadKey) {
        state.uploadFile = null;
        setNotice(errorText(error, "图片读取失败，请重新选择。"), true);
        render();
      }
    }
  }

  async function uploadReference() {
    if (!state.uploadDataUrl || !state.uploadRoles.size || !referencesEnabled() || state.action) return;
    const ticket = state.ticket; state.action = "upload-reference"; render();
    try {
      await request("POST", apiPath("/references/upload"), {
        data_url: state.uploadDataUrl, label: state.uploadLabel.trim(), roles: [...state.uploadRoles],
        operation_key: state.uploadKey || (state.uploadKey = operationKey("upload")),
      });
      if (!live(ticket)) return;
      state.uploadFile = null; state.uploadDataUrl = ""; state.uploadLabel = "";
      state.uploadKey = operationKey("upload");
      setNotice("已添加到当前角色的参考图库。");
      await loadInventory(ticket);
    } catch (error) { if (live(ticket)) setNotice(errorText(error, "图片没有加入图库，请重试。"), true); }
    finally { if (live(ticket)) { state.action = ""; render(); } }
  }

  function beginReferenceEdit(assetId) {
    const item = activeReferences().find(record => record.asset_id === assetId);
    if (!item) return;
    state.editAssetId = item.asset_id;
    state.editRevision = item.revision;
    state.editLabel = item.label || "";
    state.editRoles = new Set(item.roles || []);
    state.editUsage = usage(item);
    render();
  }

  async function saveReferenceEdit(assetId) {
    const item = activeReferences().find(record => record.asset_id === assetId);
    if (!item || !state.editRoles.size || !referencesEnabled() || state.action) return;
    const ticket = state.ticket; state.action = "edit-reference"; render();
    try {
      await request("POST", apiPath("/references/update"), {
        asset_id: item.asset_id, label: state.editLabel.trim(), roles: [...state.editRoles],
        expected_revision: state.editRevision, usage: state.editUsage,
      });
      if (!live(ticket)) return;
      setNotice("参考图信息已更新。");
      await loadInventory(ticket, true);
    } catch (error) { if (live(ticket)) setNotice(errorText(error, "参考图没有更新，请刷新后重试。"), true); }
    finally { if (live(ticket)) { state.action = ""; render(); } }
  }

  async function withdrawReference(assetId, revision) {
    if (!assetId || state.action) return;
    const ticket = state.ticket; state.action = `withdraw-reference-${assetId}`; render();
    try {
      await request("POST", apiPath("/references/withdraw"), {asset_id: assetId, expected_revision: Number(revision)});
      if (!live(ticket)) return;
      if (state.editAssetId === assetId) state.editAssetId = "";
      setNotice("参考素材已撤回。"); await loadInventory(ticket);
    } catch (error) { if (live(ticket)) setNotice(errorText(error, "参考素材撤回没有完成，请重试。"), true); }
    finally { if (live(ticket)) { state.action = ""; render(); } }
  }

  function parseTags(value) {
    return [...new Set(String(value || "").split(/[，,]/).map(item => item.trim()).filter(Boolean))];
  }

  async function registerWardrobe() {
    if (!wardrobeEnabled() || state.action || !state.wardrobeName.trim()) return;
    const ticket = state.ticket; state.action = "register-wardrobe"; render();
    try {
      await request("POST", apiPath("/wardrobe"), {
        operation_key: state.wardrobeKey || (state.wardrobeKey = operationKey("wardrobe")),
        name: state.wardrobeName.trim(), description: state.wardrobeDescription.trim(), tags: parseTags(state.wardrobeTags),
        asset_id: state.wardrobeAssetId || null,
      });
      if (!live(ticket)) return;
      state.wardrobeName = ""; state.wardrobeDescription = ""; state.wardrobeTags = ""; state.wardrobeAssetId = ""; state.wardrobeKey = operationKey("wardrobe");
      setNotice("衣物已加入当前角色的衣柜。");
      await loadInventory(ticket);
    } catch (error) { if (live(ticket)) setNotice(errorText(error, "衣柜条目没有保存，请重试。"), true); }
    finally { if (live(ticket)) { state.action = ""; render(); } }
  }

  async function withdrawWardrobe(itemId) {
    if (!itemId || state.action) return;
    const ticket = state.ticket; state.action = `withdraw-wardrobe-${itemId}`; render();
    try {
      await request("POST", apiPath("/wardrobe/withdraw"), {item_id: itemId});
      if (live(ticket)) { setNotice("已撤回这件衣物。已有的历史记录不会被改写。"); await loadInventory(ticket); }
    } catch (error) { if (live(ticket)) setNotice(errorText(error, "衣柜条目撤回没有完成，请重试。"), true); }
    finally { if (live(ticket)) { state.action = ""; render(); } }
  }

  async function selectDailyOutfit(itemId) {
    if (!itemId || !dailyEnabled() || state.action) return;
    const ticket = state.ticket; state.action = `daily-${itemId}`; render();
    try {
      await request("POST", apiPath("/daily-outfit"), {item_id: itemId});
      if (live(ticket)) { setNotice("已设为今天的穿搭参考。"); await loadInventory(ticket); }
    } catch (error) { if (live(ticket)) setNotice(errorText(error, "每日穿搭没有更新，请重试。"), true); }
    finally { if (live(ticket)) { state.action = ""; render(); } }
  }

  function moveReference(assetId, direction) {
    if (state.action) return;
    const visible = orderedReferences().filter(matchesReference);
    const index = visible.findIndex(item => item.asset_id === assetId);
    const next = index + (direction === "up" ? -1 : 1);
    if (index < 0 || next < 0 || next >= visible.length) return;
    const order = [...state.galleryDraftOrder];
    const from = order.indexOf(assetId);
    const to = order.indexOf(visible[next].asset_id);
    if (from < 0 || to < 0) return;
    [order[from], order[to]] = [order[to], order[from]];
    state.galleryDraftOrder = order;
    render();
  }

  async function rebaseGallery(ticket, requestedOrder) {
    const latest = inventoryDocument(await request("GET", apiPath()));
    if (!live(ticket)) return;
    acceptInventory(latest, requestedOrder);
    if (!state.galleryBaseOrder.includes(state.editAssetId)) {
      state.editAssetId = "";
      state.editLabel = "";
      state.editRoles = new Set();
    }
    setNotice("图库内容已更新；当前顺序草稿已保留，核对后再次保存。", false);
  }

  async function saveGalleryOrder() {
    if (!galleryOrderDirty() || state.action) return;
    const ticket = state.ticket;
    const expected = [...state.galleryBaseOrder];
    const order = [...state.galleryDraftOrder];
    state.action = "save-gallery";
    render();
    try {
      const value = await request("POST", apiPath("/references/order"), {
        expected_asset_ids: expected, asset_ids: order,
      });
      if (!live(ticket)) return;
      if (!Array.isArray(value.asset_ids) || value.asset_ids.length !== order.length
          || value.asset_ids.some((id, index) => id !== order[index])) throw makeError("reference_order_invalid");
      state.galleryBaseOrder = [...value.asset_ids];
      state.galleryDraftOrder = [...value.asset_ids];
      if (state.inventory) state.inventory = orderedInventory(state.inventory, value.asset_ids);
      setNotice("图库顺序已保存。");
    } catch (error) {
      if (!live(ticket)) return;
      if (error?.code === "reference_order_conflict") {
        try { await rebaseGallery(ticket, order); }
        catch (reloadError) {
          if (live(ticket)) setNotice(errorText(reloadError, "图库已变化，当前顺序草稿仍保留；重新读取后再试。"), true);
        }
      } else setNotice(errorText(error, "图库顺序没有保存，请重试。"), true);
    } finally {
      if (live(ticket)) { state.action = ""; render(); }
    }
  }

  function input(event) {
    const target = event.target;
    if (target.matches("[data-im-reference-label]")) state.referenceLabel = target.value;
    if (target.matches("[data-im-upload-label]")) state.uploadLabel = target.value;
    if (target.matches("[data-im-edit-label]")) state.editLabel = target.value;
    if (target.matches("input[type=text][data-im-edit-usage]")) state.editUsage[target.dataset.imEditUsage] = parseTags(target.value);
    if (target.matches("[data-im-search]")) {
      state.searchQuery = target.value;
      const cursor = target.selectionStart;
      render();
      const replacement = state.root?.querySelector?.("[data-im-search]");
      replacement?.focus?.();
      if (Number.isInteger(cursor)) replacement?.setSelectionRange?.(cursor, cursor);
    }
    if (target.matches("[data-im-wardrobe-name]")) {
      state.wardrobeName = target.value;
      const submit = state.root?.querySelector?.("[data-im-wardrobe-submit]");
      if (submit) submit.disabled = !wardrobeEnabled() || writesLocked() || !state.wardrobeName.trim();
    }
    if (target.matches("[data-im-wardrobe-description]")) state.wardrobeDescription = target.value;
    if (target.matches("[data-im-wardrobe-tags]")) state.wardrobeTags = target.value;
  }
  function change(event) {
    const target = event.target;
    if (target.matches("[data-im-upload-file]")) { chooseUploadFile(target.files?.[0]); return; }
    if (target.matches("[data-im-role]")) {
      if (target.checked) state.selectedRoles.add(target.dataset.imRole);
      else state.selectedRoles.delete(target.dataset.imRole);
      render();
    }
    if (target.matches("[data-im-upload-role]")) {
      if (target.checked) state.uploadRoles.add(target.dataset.imUploadRole);
      else state.uploadRoles.delete(target.dataset.imUploadRole);
      render();
    }
    if (target.matches("[data-im-edit-role]")) {
      if (target.checked) state.editRoles.add(target.dataset.imEditRole);
      else {
        state.editRoles.delete(target.dataset.imEditRole);
        if (target.dataset.imEditRole === "identity") state.editUsage.base_identity = false;
      }
      render();
    }
    if (target.matches("[data-im-edit-usage]")) {
      const key = target.dataset.imEditUsage;
      state.editUsage[key] = target.type === "checkbox" ? target.checked : target.tagName === "SELECT" ? target.value : parseTags(target.value);
      if (target.matches("input[type=text]")) return;
      if (key === "base_identity" && target.checked) state.editRoles.add("identity");
      render();
    }
    if (target.matches("[data-im-filter-role]")) { state.filterRole = target.value; render(); }
    if (target.matches("[data-im-wardrobe-asset]")) { state.wardrobeAssetId = target.value; render(); }
  }
  function click(event) {
    const target = event.target.closest("[data-im-open-outfit],[data-im-refresh],[data-im-load-more],[data-im-select],[data-im-register],[data-im-upload],[data-im-save-gallery],[data-im-move-ref],[data-im-edit-ref],[data-im-quick-roles],[data-im-save-ref],[data-im-cancel-edit],[data-im-withdraw-ref],[data-im-wardrobe-submit],[data-im-withdraw-wardrobe],[data-im-daily],[data-im-toggle-upload],[data-im-toggle-wardrobe],[data-im-toggle-withdrawn-wardrobe],[data-im-generate-outfit]");
    if (!target || !state.root?.contains(target) || target.disabled) return;
    if (target.matches("[data-im-open-outfit]")) { openOutfitLightbox(); return; }
    if (state.action) return;
    if (target.matches("[data-im-toggle-upload]")) { state.uploadOpen = !state.uploadOpen; render(); return; }
    if (target.matches("[data-im-toggle-wardrobe]")) { state.wardrobeOpen = !state.wardrobeOpen; render(); return; }
    if (target.matches("[data-im-toggle-withdrawn-wardrobe]")) { state.showWithdrawnWardrobe = !state.showWithdrawnWardrobe; render(); return; }
    if (target.matches("[data-im-refresh]")) { refresh(); return; }
    if (target.matches("[data-im-generate-outfit]")) { generateOutfitPhoto(); return; }
    if (target.matches("[data-im-load-more]")) { loadAlbum(state.ticket, true); return; }
    if (target.matches("[data-im-save-gallery]")) { saveGalleryOrder(); return; }
    if (target.matches("[data-im-move-ref]")) { moveReference(target.dataset.imAsset, target.dataset.imMoveRef); return; }
    if (target.matches("[data-im-quick-roles]")) {
      (QUICK_ROLES[target.dataset.imQuickRoles] || []).forEach(value => state.editRoles.add(value));
      render(); return;
    }
    if (target.matches("[data-im-select]")) {
      const id = target.dataset.imSelect;
      state.selectedImage = id; state.referenceLabel = ""; state.referenceKey = operationKey("reference"); state.selectedRoles = new Set(["identity"]); render(); return;
    }
    if (target.matches("[data-im-register]")) { registerReference(); return; }
    if (target.matches("[data-im-upload]")) { uploadReference(); return; }
    if (target.matches("[data-im-edit-ref]")) { beginReferenceEdit(target.dataset.imEditRef); return; }
    if (target.matches("[data-im-save-ref]")) { saveReferenceEdit(target.dataset.imSaveRef); return; }
    if (target.matches("[data-im-cancel-edit]")) { state.editAssetId = ""; render(); return; }
    if (target.matches("[data-im-withdraw-ref]")) { withdrawReference(target.dataset.imWithdrawRef, target.dataset.imRevision); return; }
    if (target.matches("[data-im-wardrobe-submit]")) { registerWardrobe(); return; }
    if (target.matches("[data-im-withdraw-wardrobe]")) { withdrawWardrobe(target.dataset.imWithdrawWardrobe); return; }
    if (target.matches("[data-im-daily]")) { selectDailyOutfit(target.dataset.imDaily); }
  }

  function mount(node) {
    unmount();
    state.root = node || null; state.scope = product()?.scopeKey?.() || ""; state.ticket++;
    state.inventory = null; state.inventoryError = ""; state.album = []; state.albumCursor = null; state.albumLoaded = false; state.albumError = "";
    state.galleryBaseOrder = []; state.galleryDraftOrder = [];
    state.selectedImage = ""; state.selectedRoles = new Set(["identity"]); state.referenceLabel = ""; state.referenceKey = operationKey("reference");
    state.uploadFile = null; state.uploadDataUrl = ""; state.uploadLabel = ""; state.uploadRoles = new Set(["identity"]); state.uploadKey = operationKey("upload");
    state.searchQuery = ""; state.filterRole = ""; state.editAssetId = ""; state.editLabel = ""; state.editRoles = new Set();
    state.editRevision = 0; state.editUsage = defaultUsage();
    state.wardrobeName = ""; state.wardrobeDescription = ""; state.wardrobeTags = ""; state.wardrobeAssetId = ""; state.wardrobeKey = operationKey("wardrobe");
    state.action = ""; setNotice("");
    state.uploadOpen = false; state.wardrobeOpen = false; state.showWithdrawnWardrobe = false;
    if (!state.root) return;
    state.abort = new AbortController();
    state.root.addEventListener("click", click, {signal: state.abort.signal});
    state.root.addEventListener("input", input, {signal: state.abort.signal});
    state.root.addEventListener("change", change, {signal: state.abort.signal});
    render();
    if (state.scope && role()?.image_material_api) { const ticket = state.ticket; Promise.allSettled([loadInventory(ticket), loadAlbum(ticket), loadOutfitPhoto(ticket)]); }
  }

  function unmount() {
    clearTimeout(state.outfitTimer);
    state.outfitPhoto = null; state.outfitError = ""; state.outfitOperation = "";
    state.ticket++; state.abort?.abort(); state.abort = null; state.root = null; state.scope = "";
    state.inventory = null; state.album = []; state.albumCursor = null; state.albumLoaded = false; state.albumError = ""; state.inventoryError = ""; state.action = "";
    state.galleryBaseOrder = []; state.galleryDraftOrder = [];
    state.uploadFile = null; state.uploadDataUrl = ""; state.uploadLabel = ""; state.editAssetId = "";
  }

  return {mount, unmount, refresh, reload: () => loadInventory(state.ticket)};
})();
