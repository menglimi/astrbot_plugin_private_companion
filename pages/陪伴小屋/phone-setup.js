"use strict";
window.CompanionPhoneSetup = (() => {
  const prefix = "/astrbot_plugin_private_companion/";
  const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  const messages = {
    adb_not_found: "这台电脑还需要 Android Platform-Tools。",
    adb_unavailable: "电脑上的 ADB 无法启动。检查 Platform-Tools 或高级设置里的路径。",
    adb_address_invalid: "请填写手机当前显示的 IP 地址和端口。",
    adb_pair_code_invalid: "配对码是手机当前显示的 6 位数字。",
    adb_pair_failed: "配对未成功。请在手机上重新打开配对码弹窗，核对地址和新配对码。",
    adb_connect_failed: "没连上手机。请确认电脑和手机在同一网络，并核对无线调试主页的 IP 地址和端口。",
    adb_no_device: "手机已断开或尚未授权。重新连接后再查找。",
    adb_timeout: "手机没有及时响应。保持手机解锁后重试。",
    bridge_timeout: "操作已超时。请重新查找手机并核对当前状态。",
    configuration_revision_conflict: "角色配置刚刚更新，请重新选择这台手机。",
    configuration_edit_conflict: "角色配置刚刚更新，请重新选择这台手机。",
  };
  const states = {device:"已连接", unauthorized:"待手机授权", offline:"已离线"};
  const sourceLabels = {role_config:"角色层", "phone.json":"旧版 phone.json", default:"默认自动查找"};
  const ACTION_CATALOG = Object.freeze([
    {key:"launch", label:"启动应用", desc:"按包名打开目标应用"},
    {key:"tap_text", label:"点文字", desc:"按屏幕可见文字点击（优先推荐）"},
    {key:"tap", label:"点击坐标", desc:"按截图位置点击图标或无字区域"},
    {key:"swipe", label:"滑动", desc:"上下左右滑动或翻页"},
    {key:"type", label:"输入文字", desc:"向当前聚焦输入框输入文本"},
    {key:"key", label:"按键", desc:"发送返回、主页等系统按键"},
    {key:"wait_text", label:"等待文字", desc:"等待指定文字出现在屏幕"},
    {key:"done", label:"完成", desc:"标记任务已完成（系统必选，不可移除）", required:true},
    {key:"fail", label:"失败", desc:"无法继续时安全中止（系统必选，不可移除）", required:true},
    {key:"force_stop", label:"强制停止", desc:"强行结束卡死应用（谨慎开启，默认关闭）", caution:true},
  ]);
  const ACTION_BY_KEY = Object.fromEntries(ACTION_CATALOG.map(item => [item.key, item]));
  const REQUIRED_ACTIONS = ACTION_CATALOG.filter(item => item.required).map(item => item.key);
  const KNOWN_ACTION_KEYS = ACTION_CATALOG.map(item => item.key);
  const ACTION_TOKEN_SPLIT = /[,，\r\n]+/;

  function parseAllowedActions(rawValue) {
    const tokens = [];
    const seen = Object.create(null);
    const parts = String(rawValue ?? "").split(ACTION_TOKEN_SPLIT);
    for (const part of parts) {
      const token = part.trim();
      if (!token || seen[token]) continue;
      seen[token] = true;
      tokens.push(token);
    }
    return tokens;
  }

  function analyzeAllowedActions(rawValue) {
    const rawParts = String(rawValue ?? "").split(ACTION_TOKEN_SPLIT)
      .map(part => part.trim()).filter(Boolean);
    const tokens = parseAllowedActions(rawValue);
    const selected = Object.create(null);
    for (const token of tokens) selected[token] = true;
    const missingRequired = REQUIRED_ACTIONS.filter(key => !selected[key]);
    const customTokens = tokens.filter(token => !ACTION_BY_KEY[token]);
    const hasDuplicates = rawParts.length !== tokens.length;
    return {tokens, selected, missingRequired, customTokens, hasDuplicates};
  }

  function toggleAllowedAction(rawValue, actionKey) {
    const key = String(actionKey ?? "").trim();
    if (!key) return String(rawValue ?? "");
    const tokens = parseAllowedActions(rawValue);
    const index = tokens.indexOf(key);
    const meta = ACTION_BY_KEY[key];
    if (meta?.required) {
      if (index >= 0) return tokens.join(",");
      tokens.push(key);
      return tokens.join(",");
    }
    if (index >= 0) {
      tokens.splice(index, 1);
      return tokens.join(",");
    }
    const firstRequired = tokens.findIndex(token => ACTION_BY_KEY[token]?.required);
    if (firstRequired >= 0) tokens.splice(firstRequired, 0, key);
    else tokens.push(key);
    return tokens.join(",");
  }

  function restoreRequiredActions(rawValue) {
    const tokens = parseAllowedActions(rawValue);
    for (const key of REQUIRED_ACTIONS) {
      if (!tokens.includes(key)) tokens.push(key);
    }
    return tokens.join(",");
  }

  function emitFieldInput(field) {
    if (!field || typeof field.dispatchEvent !== "function") return;
    if (typeof Event === "function") {
      field.dispatchEvent(new Event("input", {bubbles:true}));
    } else {
      field.dispatchEvent({type:"input", bubbles:true, target:field});
    }
  }

  function buildActionChipButton(item, active, disabled) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `phone-action-chip${active ? " is-on" : ""}${item.required ? " is-required" : ""}${item.caution ? " is-caution" : ""}`;
    chip.dataset.phoneActionKey = item.key;
    if (item.required) chip.dataset.phoneActionRequired = "true";
    if (item.caution) chip.dataset.phoneActionCaution = "true";
    chip.setAttribute("aria-pressed", String(active));
    chip.title = `${item.label}（${item.key}）：${item.desc}`;
    chip.disabled = Boolean(disabled);
    const label = document.createElement("span");
    label.className = "phone-action-chip-label";
    label.textContent = item.label;
    const code = document.createElement("code");
    code.className = "phone-action-chip-code";
    code.textContent = item.key;
    chip.append(label, code);
    if (item.required || item.caution) {
      const badge = document.createElement("span");
      badge.className = `phone-action-chip-tag${item.caution ? " is-caution" : " is-required"}`;
      badge.textContent = item.required ? "必选" : "谨慎";
      chip.append(badge);
    }
    return chip;
  }

  function syncActionPicker(picker, rawValue, disabled = false) {
    if (!picker) return;
    const info = analyzeAllowedActions(rawValue);
    const isDisabled = Boolean(disabled);
    picker.querySelectorAll("[data-phone-action-key]").forEach(button => {
      const key = button.dataset.phoneActionKey;
      const on = Boolean(info.selected[key]);
      const pressed = String(on);
      if (button.classList.contains("is-on") !== on) button.classList.toggle("is-on", on);
      if (button.getAttribute("aria-pressed") !== pressed) button.setAttribute("aria-pressed", pressed);
      if (button.disabled !== isDisabled) button.disabled = isDisabled;
    });
    const summary = picker.querySelector("[data-phone-action-summary]");
    if (summary) {
      const knownCount = KNOWN_ACTION_KEYS.filter(key => info.selected[key]).length;
      const customNote = info.customTokens.length ? ` · 含 ${info.customTokens.length} 项自定义` : "";
      const nextSummary = `已选 ${knownCount} / ${KNOWN_ACTION_KEYS.length} 项${customNote}`;
      if (summary.textContent !== nextSummary) summary.textContent = nextSummary;
    }
    const warn = picker.querySelector("[data-phone-action-warn]");
    if (warn) {
      const missing = info.missingRequired;
      const shouldHide = !missing.length;
      if (warn.hidden !== shouldHide) warn.hidden = shouldHide;
      const copy = warn.querySelector("[data-phone-action-warn-text]");
      if (copy) {
        const nextCopy = missing.length
          ? `缺少必选动作：${missing.join("、")}（保存时后端会校验它们必须保留）`
          : "";
        if (copy.textContent !== nextCopy) copy.textContent = nextCopy;
      }
      const restoreBtn = warn.querySelector("[data-phone-action-restore]");
      if (restoreBtn && restoreBtn.disabled !== isDisabled) restoreBtn.disabled = isDisabled;
    }
  }

  function enhanceActionField(adbRoot) {
    if (!adbRoot || typeof adbRoot.querySelector !== "function" || typeof document?.createElement !== "function") return;
    const row = adbRoot.querySelector('[data-wb-row="allowed_actions"]');
    const textarea = row?.querySelector?.('textarea[data-wb-field="allowed_actions"]');
    const control = row?.querySelector?.(".wb-control");
    if (!row || !textarea || !control) return;
    let picker = control.querySelector("[data-phone-action-picker]");
    if (!picker) {
      picker = document.createElement("div");
      picker.className = "phone-action-picker";
      picker.dataset.phoneActionPicker = "allowed_actions";

      const bar = document.createElement("div");
      bar.className = "phone-action-bar";
      const barTitle = document.createElement("span");
      barTitle.className = "phone-action-bar-title";
      barTitle.textContent = "快捷勾选动作";
      const summary = document.createElement("span");
      summary.className = "phone-action-summary";
      summary.dataset.phoneActionSummary = "";
      bar.append(barTitle, summary);

      const grid = document.createElement("div");
      grid.className = "phone-action-chips";
      grid.setAttribute("role", "group");
      grid.setAttribute("aria-label", "允许的手机操作动作");
      const initial = analyzeAllowedActions(textarea.value);
      for (const item of ACTION_CATALOG) {
        grid.append(buildActionChipButton(item, Boolean(initial.selected[item.key]), textarea.disabled));
      }

      const warn = document.createElement("div");
      warn.className = "phone-action-warn";
      warn.dataset.phoneActionWarn = "";
      warn.hidden = true;
      const warnText = document.createElement("span");
      warnText.dataset.phoneActionWarnText = "";
      const restore = document.createElement("button");
      restore.type = "button";
      restore.className = "button small";
      restore.dataset.phoneActionRestore = "";
      restore.textContent = "补回必选项";
      warn.append(warnText, restore);

      const rawLabel = document.createElement("span");
      rawLabel.className = "phone-action-raw-label";
      rawLabel.textContent = "原始逗号清单（高级编辑 · 与上方按钮双向同步）";

      picker.append(bar, grid, warn, rawLabel);
      picker.addEventListener("click", event => {
        if (textarea.disabled) return;
        const restoreBtn = event.target?.closest?.("[data-phone-action-restore]");
        if (restoreBtn) {
          const next = restoreRequiredActions(textarea.value);
          if (next !== textarea.value) {
            textarea.value = next;
            syncActionPicker(picker, next, textarea.disabled);
            emitFieldInput(textarea);
          }
          return;
        }
        const chip = event.target?.closest?.("[data-phone-action-key]");
        if (!chip) return;
        const next = toggleAllowedAction(textarea.value, chip.dataset.phoneActionKey);
        if (next !== textarea.value) {
          textarea.value = next;
          syncActionPicker(picker, next, textarea.disabled);
          emitFieldInput(textarea);
        }
      });

      const wrap = control.querySelector(".wb-textarea-wrap") || textarea;
      control.insertBefore(picker, wrap);
    }
    if (!textarea.dataset.phoneActionBound) {
      textarea.dataset.phoneActionBound = "1";
      textarea.addEventListener("input", () => {
        syncActionPicker(control.querySelector("[data-phone-action-picker]"), textarea.value, textarea.disabled);
      });
    }
    syncActionPicker(picker, textarea.value, textarea.disabled);
  }

  function enhanceAdvancedWorkspace(adbRoot) {
    if (!adbRoot || typeof adbRoot.querySelectorAll !== "function") return;
    if (typeof document?.createElement === "function") {
      adbRoot.querySelectorAll(".wb-group").forEach(group => {
        const head = group.querySelector(".wb-group-head");
        if (!head || head.querySelector(".phone-group-count, .wb-group-count")) return;
        const count = group.querySelectorAll(".wb-field:not([hidden])").length;
        if (!count) return;
        const pill = document.createElement("span");
        pill.className = "phone-group-count";
        pill.textContent = `${count} 项`;
        head.append(pill);
      });
    }
    const subjects = adbRoot.querySelector('textarea[data-wb-field="allowed_subjects"]');
    if (subjects && !subjects.getAttribute("placeholder")) {
      subjects.setAttribute("placeholder", "例如：onebot-user:10001, onebot-user:10002（留空表示所有私聊对象可用）");
    }
    enhanceActionField(adbRoot);
  }

  function adbNotFoundMessage(diag) {
    if (!diag || typeof diag !== "object") return messages.adb_not_found;
    const path = typeof diag.configured_adb_path === "string" ? diag.configured_adb_path.trim() : "";
    const source = sourceLabels[diag.config_source] || "";
    const roleId = typeof diag.resolved_role_id === "string" ? diag.resolved_role_id.trim() : "";
    const botId = typeof diag.resolved_bot_id === "string" ? diag.resolved_bot_id.trim() : "";
    const scopeParts = [
      source ? `来源：${source}` : "",
      roleId ? `角色：${roleId}` : "",
      botId ? `账号：${botId}` : "",
    ].filter(Boolean);
    const scopeNote = scopeParts.length ? `（${scopeParts.join(" · ")}）` : "";
    if (diag.reason === "explicit_path_is_dir") {
      return `高级设置里的 ADB 路径填成了文件夹「${path}」，请改成指向里面的 adb.exe 或 adb 文件${scopeNote}。`;
    }
    if (diag.reason === "explicit_path_missing") {
      return `找不到高级设置里填写的 ADB 文件「${path}」，请核对路径是否拼写正确或已被移动${scopeNote}。`;
    }
    if (diag.reason === "explicit_path_not_executable") {
      return `高级设置里的 ADB 文件「${path}」无法作为可执行程序运行，请核对文件与权限${scopeNote}。`;
    }
    if (diag.reason === "auto_discovery_empty") {
      return `未在系统 PATH 或常见目录找到 ADB，且当前尚未填写自定义路径${scopeNote}。可先下载 Android Platform-Tools，或在下方「高级设置」填写 adb 路径。`;
    }
    return messages.adb_not_found + scopeNote;
  }

  function markup() {
    return `<header class="phone-setup-head"><div class="phone-setup-head-copy"><h3 id="phoneSetupTitle">连接 Android 手机</h3><p>让当前角色通过 USB 或无线 ADB 操作指定的 Android 手机。</p></div><span data-phone-enabled hidden></span></header>
      <div class="phone-setup-modes" role="group" aria-label="连接方式">
        <button type="button" aria-pressed="true" data-phone-mode="usb">${icon("usb")}USB 连接</button>
        <button type="button" aria-pressed="false" data-phone-mode="wireless">${icon("wifi")}无线连接</button>
      </div>
      <div class="phone-setup-usb" data-phone-panel="usb">
        <p>用数据线连接手机和运行 AstrBot 的电脑，在手机的「开发者选项」里开启 USB 调试。</p>
        <details class="phone-debug-help"><summary>找不到「开发者选项」</summary><p>打开手机的「设置 → 关于手机」，连续轻点「版本号」，再回到「设置」搜索开发者选项。</p></details>
        <button type="button" class="button primary" data-phone-scan>${icon("scan")}查找手机</button>
      </div>
      <div class="phone-setup-wireless" data-phone-panel="wireless" hidden>
        <button type="button" class="button" data-phone-scan>${icon("scan")}查找已配对手机</button>
        <details class="phone-wireless-option">
          <summary>${icon("link-2")}首次无线配对</summary>
          <p>电脑和手机连到同一个网络。在手机上打开「开发者选项 → 无线调试 → 使用配对码配对设备」。</p>
          <form data-phone-wireless="pair" class="phone-wireless-form">
            <label><span>弹窗里的 IP 地址和端口</span><input name="address" type="text" required maxlength="256" autocomplete="off" spellcheck="false" placeholder="例如 192.168.1.20:39751"></label>
            <label><span>6 位配对码</span><input name="code" type="password" required pattern="[0-9]{6}" maxlength="6" inputmode="numeric" autocomplete="off"></label>
            <button class="button primary" type="submit">${icon("link")}配对</button>
          </form>
        </details>
        <details class="phone-wireless-option" data-phone-manual>
          <summary>${icon("smartphone")}已配对，按地址连接</summary>
          <form data-phone-wireless="connect" class="phone-wireless-form">
            <label><span>无线调试主页的 IP 地址和端口</span><input name="address" type="text" required maxlength="256" autocomplete="off" spellcheck="false" placeholder="例如 192.168.1.20:37123"></label>
            <button class="button primary" type="submit">${icon("plug")}连接</button>
          </form>
        </details>
      </div>
      <div class="phone-setup-results" data-phone-results hidden aria-label="检测到的手机"></div>
      <p class="phone-setup-notice" data-phone-notice role="status"></p>
      <a class="text-button phone-setup-download" data-phone-download href="https://developer.android.com/tools/releases/platform-tools" target="_blank" rel="noopener noreferrer" hidden>下载 Android Platform-Tools</a>`;
  }

  function mount(root, {editor, configApi = editor?.endpoint, adbRoot = null}) {
    if (!root) return null;
    if (typeof configApi !== "string" || !configApi.startsWith(prefix) || !editor) {
      root.textContent = "当前角色的手机设置不可用。";
      return null;
    }
    const nextEndpoint = configApi.slice(prefix.length) + "/phone-connection";
    const resolvedAdbRoot = adbRoot || root.parentElement?.querySelector?.("#phoneAdbWorkspace") || null;
    if (root.phoneSetup) {
      root.phoneSetup.rebind({configApi, endpoint: nextEndpoint, editor, adbRoot: resolvedAdbRoot});
      return root.phoneSetup;
    }
    let mode = "usb", busy = false, devices = [], activation = null;
    let observer = null, observedRoot = null, enhancing = false;
    const binding = {configApi, endpoint: nextEndpoint, editor, adbRoot: resolvedAdbRoot};
    const one = selector => root.querySelector(selector);
    const handle = () => binding.editor.state.instances[0];
    root.innerHTML = markup();
    const results = one("[data-phone-results]"), noticeNode = one("[data-phone-notice]");

    function runEnhance() {
      const target = binding.adbRoot || root.parentElement?.querySelector?.("#phoneAdbWorkspace") || null;
      if (!target || enhancing) return;
      enhancing = true;
      if (observer) observer.disconnect();
      try {
        enhanceAdvancedWorkspace(target);
      } finally {
        enhancing = false;
        if (observer && observedRoot === target) {
          observer.observe(target, {childList:true, subtree:true});
        }
      }
    }

    function watchAdvanced() {
      const target = binding.adbRoot || root.parentElement?.querySelector?.("#phoneAdbWorkspace") || null;
      if (target === observedRoot) { runEnhance(); return; }
      if (observer) { observer.disconnect(); observer = null; }
      observedRoot = target;
      if (target && typeof MutationObserver === "function" && typeof target.nodeType === "number") {
        observer = new MutationObserver(() => { runEnhance(); });
      }
      runEnhance();
    }

    function notice(message, code = "", diag = null) {
      noticeNode.textContent = message;
      noticeNode.classList.toggle("is-error", Boolean(code));
      const showDownload = code === "adb_not_found"
        && (!diag || !diag.reason || ["auto_discovery_empty", "explicit_path_missing"].includes(diag.reason));
      one("[data-phone-download]").hidden = !showDownload;
    }

    function showAdbDiagnostic(diag) {
      if (diag?.reason?.startsWith("explicit_path_")) {
        const advanced = root.parentElement?.querySelector(".phone-advanced");
        if (advanced) advanced.open = true;
      }
      notice(adbNotFoundMessage(diag), "adb_not_found", diag);
    }

    function syncStatus(status = handle()?.state.status) {
      const label = one("[data-phone-enabled]");
      label.hidden = !status?.settings;
      const enabled = status?.settings?.enabled === true;
      label.textContent = enabled ? "已启用" : "未启用";
      label.classList.toggle("is-enabled", enabled);
      watchAdvanced();
    }

    function rebind(next) {
      const changed = next.endpoint !== binding.endpoint || next.editor !== binding.editor;
      binding.configApi = next.configApi;
      binding.endpoint = next.endpoint;
      binding.editor = next.editor;
      binding.adbRoot = next.adbRoot || root.parentElement?.querySelector?.("#phoneAdbWorkspace") || binding.adbRoot;
      if (changed) {
        devices = [];
        activation = null;
        drawResults();
        notice("");
      }
      syncStatus();
    }

    function drawResults() {
      results.replaceChildren();
      const matching = devices.filter(item => item.wireless === (mode === "wireless"));
      results.hidden = !matching.length;
      if (!matching.length) return matching;
      const list = document.createElement("ul");
      for (const device of matching) {
        const row = document.createElement("li");
        row.innerHTML = `<span class="phone-found-icon">${icon("smartphone")}</span><div><strong></strong><small></small></div><div class="phone-found-action"></div>`;
        row.querySelector("strong").textContent = device.model ? device.model.replaceAll("_", " ") : "Android 手机";
        row.querySelector("small").textContent = `${states[device.state] || "暂不可用"} · ${device.serial}`;
        if (device.state === "device") {
          const action = row.querySelector(".phone-found-action");
          action.innerHTML = `<button type="button" class="button small primary" data-phone-use>${icon("check")}使用这台手机</button>`;
          const button = action.firstElementChild;
          button.dataset.phoneUse = device.serial;
          button.setAttribute("aria-label", `使用手机 ${device.model || device.serial}`);
        }
        list.append(row);
      }
      results.append(list);
      window.lucide?.createIcons({nodes:results.querySelectorAll("[data-lucide]")});
      return matching;
    }

    async function run(action, params = {}) {
      if (busy) return;
      const {endpoint, editor: currentEditor} = binding;
      const active = () => root.isConnected && endpoint === binding.endpoint && currentEditor === binding.editor;
      const instance = handle(), status = instance?.state.status;
      if (action === "use") {
        if (!status?.edit_token) { notice("暂未读到角色设置，请稍后重试。", "settings_pending"); return; }
        if (instance.dirty()) {
          root.parentElement.querySelector(".phone-advanced").open = true;
          notice("高级设置有未保存的改动。", "advanced_dirty");
          return;
        }
        const key = `${status.edit_token}:${params.serial}`;
        if (activation?.key !== key) activation = {key, operation:crypto.randomUUID()};
        params = {...params, edit_token:status.edit_token, operation_key:activation.operation};
      }
      busy = true;
      const controls = [...root.querySelectorAll("button, input")];
      controls.forEach(control => { control.disabled = true; });
      notice(({scan:"正在查找手机…", pair:"正在配对…", connect:"正在连接…", use:"正在保存…"})[action]);
      try {
        const response = await window.CompanionProduct.request("POST", endpoint,
          {action, ...params, include_diagnostics:true}, {timeoutMs:50000});
        if (!active()) return;
        if (response?.adb_error?.code === "adb_not_found") {
          const detail = response.adb_error;
          const diag = response.adb_diagnostic || {
            reason: detail.adb_path ? "explicit_path_missing" : "auto_discovery_empty",
            configured_adb_path: detail.adb_path,
            config_source: detail.source === "role" ? "role_config" : detail.source,
            resolved_role_id: detail.role_id,
          };
          showAdbDiagnostic(diag);
          return;
        }
        if (action === "use") {
          activation = null;
          await currentEditor.read("device.phone");
          if (active()) notice("已设为当前角色的操作手机。");
          return;
        }
        devices = Array.isArray(response?.devices) ? response.devices : [];
        if (action === "pair") one('[name="code"]').value = "";
        const matching = drawResults();
        if (mode === "usb" && matching.some(item => item.state === "unauthorized")) {
          notice("解锁手机，在「允许 USB 调试」弹窗中点允许，然后重新查找。");
        } else if (action === "pair" && !matching.length) {
          one("[data-phone-manual]").open = true;
          notice("已配对，等待手机连接。可按无线调试主页的地址连接。");
        } else if (!matching.length) {
          notice(mode === "usb" ? "没找到手机。确认数据线支持传输数据，并检查 USB 调试是否已开启。" : "没找到已连接的无线设备。");
        } else {
          notice(matching.some(item => item.state === "device") ? "" : "手机暂不可用，重新连接后再查找。");
        }
      } catch (error) {
        if (!active()) return;
        const code = error?.code || error?.message || "phone_connection_failed";
        if (action === "use") await currentEditor.read("device.phone");
        if (!active()) return;
        if (code === "adb_not_found") {
          const diag = (error?.details && typeof error.details === "object" && error.details.reason)
            ? error.details
            : (handle()?.state?.status?.adb_diagnostic || null);
          showAdbDiagnostic(diag);
          return;
        }
        notice(messages[code] || window.CompanionProduct.errorLabel(code), code);
      } finally {
        busy = false;
        controls.forEach(control => { control.disabled = false; });
      }
    }

    root.addEventListener("click", event => {
      const button = event.target.closest("button");
      if (!button || busy) return;
      if (button.dataset.phoneMode && button.dataset.phoneMode !== mode) {
        mode = button.dataset.phoneMode;
        root.querySelectorAll("[data-phone-mode]").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
        root.querySelectorAll("[data-phone-panel]").forEach(panel => { panel.hidden = panel.dataset.phonePanel !== mode; });
        one('[name="code"]').value = "";
        notice("");
        drawResults();
      } else if (button.hasAttribute("data-phone-scan")) {
        void run("scan");
      } else if (button.dataset.phoneUse) {
        void run("use", {serial:button.dataset.phoneUse});
      }
    });
    root.addEventListener("submit", event => {
      const action = event.target.dataset.phoneWireless;
      if (!action) return;
      event.preventDefault();
      const params = Object.fromEntries([...new FormData(event.target)].map(([key,value]) => [key,value.trim()]));
      void run(action, params);
    });
    window.lucide?.createIcons({nodes:root.querySelectorAll("[data-lucide]")});
    syncStatus();
    root.phoneSetup = {syncStatus, rebind, enhance: runEnhance, get endpoint() { return binding.endpoint; }};
    return root.phoneSetup;
  }
  return {
    mount,
    adbNotFoundMessage,
    ACTION_CATALOG,
    parseAllowedActions,
    analyzeAllowedActions,
    toggleAllowedAction,
    restoreRequiredActions,
    enhanceAdvancedWorkspace,
  };
})();
