/* Role-profile export / import with a per-field diff.
 *
 * The export is a plain JSON document.  Import never writes until the operator
 * has seen the current and imported value side by side and ticked the fields to
 * replace.  The backend validates lengths and the original revision; this file
 * only owns the file picker and the review dialog.
 */
(function () {
  "use strict";
  if (window.CompanionRoleProfileTransfer) return;

  const LABELS = {
    name: "姓名", species: "种族", birthday: "生日", age: "年龄", gender: "性别",
    occupation: "职业", identity: "身份", appearance_points: "外貌要点", hair: "头发",
    eyes: "眼睛", clothing: "服装", image_identity_hint: "图像识别线索", appearance: "外貌",
    personality: "性格", first_person: "第一人称", speech_style: "说话风格",
    emotional_style: "情绪表达", social_distance: "社交距离", reaction_patterns: "反应模式",
    voice_examples: "口吻示例", signature_phrases: "口头禅", daily_habits: "日常习惯",
    key_lore: "关键设定", character_extra: "角色补充", motivations: "动机", core_values: "核心价值",
    desire: "欲望", hobbies: "爱好", boundaries: "边界", taboos: "禁忌",
    world_name: "世界名称", era: "时代", tone: "基调", rules: "世界法则",
    scenes: "活动场景", network: "关系网", world_extra: "世界补充", world: "世界蓝图",
    main_companion_subject: "主要陪伴对象", expression_private_source_mode: "私聊表达来源模式",
    expression_private_source_subject_ids: "私聊表达来源对象",
    expression_private_application_mode: "私聊表达应用模式",
    expression_private_application_subject_ids: "私聊表达应用对象", avatar_data_url: "头像",
  };

  const product = window.CompanionProduct;
  const style = document.createElement("style");
  style.textContent = `
    .role-profile-transfer-body{min-height:0;overflow:auto;padding:var(--sp-4) var(--sp-5)}
    .role-profile-transfer-note{margin:0 0 var(--sp-3);color:var(--t-text-faint);font-size:var(--fs-tiny);line-height:1.6}
    .role-profile-transfer-list{display:grid;gap:1px;border:1px solid var(--t-line);border-radius:var(--r-sm);overflow:hidden}
    .role-profile-transfer-row{display:grid;grid-template-columns:28px minmax(110px,180px) minmax(0,1fr) minmax(0,1fr);gap:var(--sp-2);align-items:start;padding:var(--sp-2) var(--sp-3);background:var(--t-surface);border-bottom:1px solid var(--t-line)}
    .role-profile-transfer-row:last-child{border-bottom:0}
    .role-profile-transfer-row.is-head{position:sticky;top:0;z-index:1;background:var(--t-surface-soft);color:var(--t-text-soft);font-size:var(--fs-micro)}
    .role-profile-transfer-row input{margin-top:3px}
    .role-profile-transfer-row strong{color:var(--t-text-strong);font-size:var(--fs-tiny);font-weight:650}
    .role-profile-transfer-value{min-width:0;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--t-text-soft);font-size:var(--fs-micro);line-height:1.55;max-height:6.5em;overflow:auto}
    .role-profile-transfer-value.is-imported{color:var(--t-accent-ink)}
    .role-profile-transfer-actions{display:flex;flex-wrap:wrap;align-items:center;gap:var(--sp-2);padding:var(--sp-3) var(--sp-5);border-top:1px solid var(--t-line);background:var(--t-surface)}
    .role-profile-transfer-count{color:var(--t-text-faint);font-size:var(--fs-micro)}
    .role-profile-transfer-spacer{flex:1}
    @media(max-width:650px){.role-profile-transfer-row{grid-template-columns:26px minmax(0,1fr)}.role-profile-transfer-row.is-head{display:none}.role-profile-transfer-value{grid-column:2;max-height:none}}
  `;
  document.head.appendChild(style);
  let dialog = null;
  let fileInput = null;
  let importDocument = null;
  let apiBase = "";

  function button(label, attribute, icon) {
    const element = document.createElement("button");
    element.type = "button";
    element.className = "button small";
    element.setAttribute(attribute, "");
    if (icon) {
      const iconElement = document.createElement("i");
      iconElement.dataset.lucide = icon;
      iconElement.setAttribute("aria-hidden", "true");
      element.appendChild(iconElement);
    }
    const span = document.createElement("span");
    span.textContent = label;
    element.appendChild(span);
    return element;
  }

  function ensureControls() {
    const actions = document.querySelector(".self-organizer-actions");
    if (!actions || actions.querySelector("[data-role-profile-transfer]")) return;
    const exportButton = button("导出档案", "data-role-profile-export", "download");
    const importButton = button("导入档案", "data-role-profile-import", "upload");
    exportButton.dataset.roleProfileTransfer = "export";
    importButton.dataset.roleProfileTransfer = "import";
    actions.insertBefore(exportButton, actions.firstChild);
    actions.insertBefore(importButton, exportButton.nextSibling);
    if (window.lucide?.createIcons) window.lucide.createIcons({ attrs: { "aria-hidden": "true" } });
  }

  function ensureInput() {
    if (fileInput) return fileInput;
    fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = "application/json,.json";
    fileInput.hidden = true;
    fileInput.addEventListener("change", onFile);
    document.body.appendChild(fileInput);
    return fileInput;
  }

  function endpoint(suffix) {
    const role = product?.role?.();
    if (!role?.persona_api) throw new Error("当前角色还没有可用的档案接口");
    apiBase = String(product.relative(role.persona_api)).replace(/\/$/, "");
    return apiBase + suffix;
  }

  function download(documentValue) {
    const blob = new Blob([JSON.stringify(documentValue, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    const role = product?.role?.() || {};
    anchor.href = url;
    anchor.download = "companion-role-profile-" + String(role.role_name || role.role_id || "role").replace(/[^\w\u4e00-\u9fa5-]+/g, "-") + ".json";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function onExport() {
    try {
      const documentValue = await product.request("GET", endpoint("/profile-export"));
      download(documentValue);
    } catch (error) {
      alert(error?.message || "导出失败，请刷新页面后重试。");
    }
  }

  function ensureDialog() {
    if (dialog) return dialog;
    dialog = document.createElement("dialog");
    dialog.className = "role-profile-draft-dialog role-profile-transfer-dialog";
    dialog.innerHTML = `
      <div class="role-profile-draft-shell">
        <div class="role-profile-draft-head">
          <div><span>ROLE PROFILE · IMPORT</span><h2>导入角色档案</h2></div>
          <button type="button" class="icon-button" data-transfer-close aria-label="关闭">×</button>
        </div>
        <div class="role-profile-transfer-body">
          <p class="role-profile-transfer-note">只应用勾选的字段；未勾选的字段保留当前档案。导入文件不会直接覆盖任何内容。</p>
          <div class="role-profile-transfer-list" data-transfer-list></div>
        </div>
        <div class="role-profile-draft-actions role-profile-transfer-actions">
          <button type="button" class="button small" data-transfer-all>全选变更</button>
          <button type="button" class="button small" data-transfer-none>全不选</button>
          <span class="role-profile-transfer-count" data-transfer-count></span>
          <span class="role-profile-transfer-spacer"></span>
          <button type="button" class="button" data-transfer-close>取消</button>
          <button type="button" class="button primary" data-transfer-apply>应用选中字段</button>
        </div>
      </div>`;
    dialog.addEventListener("click", onDialogClick);
    document.body.appendChild(dialog);
    return dialog;
  }

  function closeDialog() {
    if (dialog?.open) dialog.close();
    importDocument = null;
  }

  function row(field) {
    const label = LABELS[field.field] || field.field;
    const wrapper = document.createElement("label");
    wrapper.className = "role-profile-transfer-row";
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = Boolean(field.default_selected);
    check.dataset.transferField = field.field;
    const name = document.createElement("strong");
    name.textContent = label;
    const before = document.createElement("span");
    before.className = "role-profile-transfer-value";
    before.textContent = field.current || "（空）";
    const after = document.createElement("span");
    after.className = "role-profile-transfer-value is-imported";
    after.textContent = field.imported || "（空）";
    wrapper.append(check, name, before, after);
    return wrapper;
  }

  function renderPreview(preview, documentValue) {
    importDocument = documentValue;
    const list = dialog.querySelector("[data-transfer-list]");
    list.textContent = "";
    if (!preview.fields?.length) {
      const empty = document.createElement("p");
      empty.className = "role-profile-transfer-note";
      empty.textContent = "导入文件与当前档案没有差异。";
      list.appendChild(empty);
    } else {
      const header = document.createElement("div");
      header.className = "role-profile-transfer-row is-head";
      header.innerHTML = "<span></span><strong>字段</strong><span>当前档案</span><span>导入文件</span>";
      list.appendChild(header);
      for (const field of preview.fields) list.appendChild(row(field));
    }
    updateCount();
    dialog.showModal();
  }

  function updateCount() {
    const checks = [...dialog.querySelectorAll("[data-transfer-field]")];
    const selected = checks.filter(item => item.checked).length;
    const count = dialog.querySelector("[data-transfer-count]");
    if (count) count.textContent = "已选 " + selected + " / " + checks.length + " 项";
  }

  async function onFile(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      const preview = await product.request("POST", endpoint("/profile-import/preview"), { document: parsed });
      ensureDialog();
      renderPreview(preview, parsed);
    } catch (error) {
      alert(error?.message || "这个文件不是可识别的角色档案导出。");
    }
  }

  async function applyImport() {
    if (!importDocument) return;
    const fields = [...dialog.querySelectorAll("[data-transfer-field]")]
      .filter(item => item.checked).map(item => item.dataset.transferField);
    if (!fields.length) {
      alert("至少勾选一个字段。");
      return;
    }
    const apply = dialog.querySelector("[data-transfer-apply]");
    apply.disabled = true;
    try {
      await product.request("POST", endpoint("/profile-import/apply"), { document: importDocument, fields });
      closeDialog();
      window.location.reload();
    } catch (error) {
      apply.disabled = false;
      alert(error?.message || "导入没有完成，请刷新后重试。");
    }
  }

  function onDialogClick(event) {
    const target = event.target;
    if (target.closest("[data-transfer-close]")) { closeDialog(); return; }
    if (target.closest("[data-transfer-all]")) {
      dialog.querySelectorAll("[data-transfer-field]").forEach(item => { item.checked = true; });
      updateCount(); return;
    }
    if (target.closest("[data-transfer-none]")) {
      dialog.querySelectorAll("[data-transfer-field]").forEach(item => { item.checked = false; });
      updateCount(); return;
    }
    if (target.closest("[data-transfer-apply]")) { applyImport(); return; }
    if (target.matches("[data-transfer-field]")) updateCount();
  }

  document.addEventListener("click", event => {
    if (event.target.closest("[data-role-profile-export]")) { onExport(); return; }
    if (event.target.closest("[data-role-profile-import]")) { ensureInput().click(); }
  });

  const observer = new MutationObserver(() => ensureControls());
  observer.observe(document.body, { childList: true, subtree: true });
  ensureControls();

  window.CompanionRoleProfileTransfer = { refresh: ensureControls };
})();
