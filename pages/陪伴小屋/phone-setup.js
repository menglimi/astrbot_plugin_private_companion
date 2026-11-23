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

  function markup() {
    return `<header class="phone-setup-head"><h3 id="phoneSetupTitle">连接 Android 手机</h3><span data-phone-enabled hidden></span></header>
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

  function mount(root, {configApi, editor}) {
    if (!root) return null;
    if (root.phoneSetup) return root.phoneSetup;
    if (typeof configApi !== "string" || !configApi.startsWith(prefix) || !editor) {
      root.textContent = "当前角色的手机设置不可用。";
      return null;
    }
    let mode = "usb", busy = false, devices = [], activation = null;
    const endpoint = configApi.slice(prefix.length) + "/phone-connection";
    const one = selector => root.querySelector(selector);
    const handle = () => editor.state.instances[0];
    root.innerHTML = markup();
    const results = one("[data-phone-results]"), noticeNode = one("[data-phone-notice]");

    function notice(message, code = "") {
      noticeNode.textContent = message;
      noticeNode.classList.toggle("is-error", Boolean(code));
      one("[data-phone-download]").hidden = code !== "adb_not_found";
    }

    function syncStatus(status = handle()?.state.status) {
      const label = one("[data-phone-enabled]");
      label.hidden = !status?.settings;
      const enabled = status?.settings?.enabled === true;
      label.textContent = enabled ? "已启用" : "未启用";
      label.classList.toggle("is-enabled", enabled);
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
        const response = await window.CompanionProduct.request("POST", endpoint, {action, ...params}, {timeoutMs:50000});
        if (!root.isConnected) return;
        if (action === "use") {
          activation = null;
          await editor.read("device.phone");
          if (root.isConnected) notice("已设为当前角色的操作手机。");
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
        if (!root.isConnected) return;
        const code = error?.code || error?.message || "phone_connection_failed";
        if (action === "use") await editor.read("device.phone");
        if (root.isConnected) notice(messages[code] || window.CompanionProduct.errorLabel(code), code);
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
    root.phoneSetup = {syncStatus};
    return root.phoneSetup;
  }
  return {mount};
})();
