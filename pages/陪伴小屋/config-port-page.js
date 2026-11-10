"use strict";
/* 配置导入导出：只包含开关与数值，不含角色档案、关系网、记忆、日记等资料。
   导出的 JSON 可以在另一套安装（或另一个角色）里导入；导入逐分区走正常配置
   校验，失败的分区会单独报告，不会把其余分区一起丢掉。 */
window.CompanionConfigPortPage = (() => {
  const e = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  let root = null, data = null, version = 0;

  function render() {
    if (!root) return;
    const state = data || {};
    root.innerHTML = `<div class="data-page-view" aria-labelledby="config-port-title">
      <header class="data-page-view-head"><span>04</span><h3 id="config-port-title">配置导入导出</h3></header>
      <section class="storage-panel">
        <h4>导出配置</h4>
        <p class="muted-text">导出当前角色的开关与配置数值（JSON），包含标记为「所有角色共用」的安装级分区。不含角色档案、关系网、记忆、日记、图片或任何资料；其他角色与另一套安装可以导入同一份文件。</p>
        <div class="storage-actions"><button type="button" class="button small" data-config-port-export${state.busy ? " disabled" : ""}>导出配置 JSON</button></div>
      </section>
      <section class="storage-panel">
        <h4>导入配置</h4>
        <p class="muted-text">选择之前导出的 JSON。只会写入当前角色可编辑的字段，逐分区校验；标记为「所有角色共用」的分区会同时影响其他角色，失败的分区会在下面列出。</p>
        <div class="storage-actions">
          <button type="button" class="button small" data-config-port-import${state.busy ? " disabled" : ""}>选择文件并导入</button>
          <input type="file" accept="application/json,.json" data-config-port-file hidden>
        </div>
        ${state.message ? `<p class="muted-text" role="status"${state.error ? ' style="color:var(--t-risk-ink)"' : ""}>${e(state.message)}</p>` : ""}
      </section>
    </div>`;
  }

  async function request(method, path, body) {
    const product = window.CompanionProduct;
    if (typeof product?.request !== "function") throw Object.assign(new Error("bridge_unavailable"), {code: "bridge_unavailable"});
    return product.request(method, path, body);
  }

  function stamp() {
    const now = new Date();
    const pad = value => String(value).padStart(2, "0");
    return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
  }

  async function exportValues(ticket) {
    data.busy = true; data.message = "正在读取配置…"; data.error = false; render();
    try {
      const document = await request("GET", "config/port/export");
      if (ticket !== version) return;
      const text = JSON.stringify(document, null, 2);
      const blob = new Blob([text], {type: "application/json"});
      const link = window.document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `companion-config-${stamp()}.json`;
      window.document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      const sections = Object.keys(document?.sections || {}).length;
      const unavailable = Object.keys(document?.unavailable || {});
      data.message = `已导出 ${sections} 个分区的配置。` + (unavailable.length ? `以下分区读取失败，未包含：${unavailable.join("、")}。` : "");
    } catch (error) {
      if (ticket === version) { data.error = true; data.message = `导出失败${error?.code ? "（" + error.code + "）" : ""}。`; }
    }
    data.busy = false; render();
  }

  async function importValues(ticket, file) {
    if (!file) return;
    if (file.size > 262144) { data.error = true; data.message = "文件超过 256 KB，这不是配置导出文件。"; render(); return; }
    let document;
    try {
      document = JSON.parse(await file.text());
    } catch {
      data.error = true; data.message = "文件不是有效的 JSON。"; render(); return;
    }
    const sections = Object.keys(document?.sections || {});
    const confirmed = window.CompanionConfirm
      ? await window.CompanionConfirm({title: "导入配置", message: `将导入 ${sections.length} 个分区的配置到当前角色。安装级共用分区（如预算、表情、主动联系）会同时影响所有角色；只写入可编辑字段，失败的分区会单独列出。`, confirmText: "开始导入"})
      : window.confirm(`将导入 ${sections.length} 个分区的配置到当前角色，安装级共用分区会同时影响所有角色，继续？`);
    if (!confirmed || ticket !== version) return;
    data.busy = true; data.error = false; data.message = "正在导入…"; render();
    try {
      const result = await request("POST", "config/port/import", {document});
      if (ticket !== version) return;
      const applied = Object.keys(result?.applied || {});
      const failed = Object.keys(result?.failed || {});
      data.message = `已导入 ${applied.length} 个分区。` + (failed.length ? `以下分区未导入：${failed.map(name => `${name}（${result.failed[name]}）`).join("、")}。` : "");
      data.error = failed.length > 0;
    } catch (error) {
      if (ticket === version) { data.error = true; data.message = `导入失败${error?.code ? "（" + error.code + "）" : ""}。`; }
    }
    data.busy = false; render();
  }

  function mount(node) {
    unmount();
    root = node; version += 1; data = {busy: false, message: "", error: false};
    const ticket = version;
    root.addEventListener("click", event => {
      const button = event.target.closest("button");
      if (!button || !root?.contains(button) || button.disabled) return;
      if (button.hasAttribute("data-config-port-export")) { exportValues(ticket); return; }
      if (button.hasAttribute("data-config-port-import")) root.querySelector("[data-config-port-file]")?.click();
    });
    root.addEventListener("change", event => {
      if (event.target?.matches?.("[data-config-port-file]")) {
        const file = event.target.files?.[0];
        event.target.value = "";
        importValues(ticket, file);
      }
    });
    render();
  }

  function unmount() {
    if (root) root.innerHTML = "";
    root = null; data = null; version += 1;
  }

  return {mount, unmount};
})();
