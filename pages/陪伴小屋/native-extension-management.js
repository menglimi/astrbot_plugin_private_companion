/* Explicit management actions; background refreshes only read local snapshots. */
"use strict";
window.CompanionNativeExtensionManagement = (() => {
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const qrSource = value => /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(String(value || "")) ? value : "";
  const cameraPreviewSource = value => /^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(String(value || "")) ? value : "";
  const actionNames = {mihome_start_login:"扫码登录", mihome_login_status:"检查登录状态", mihome_sync_devices:"同步设备", mihome_logout:"退出米家账号", mihome_device_status:"读取设备状态", mihome_save_mappings:"保存设备映射", mihome_device_profile:"读取设备能力", mihome_device_action:"设置设备属性",
    scan_camera:"扫描摄像头", consent_camera:"更新摄像头确认", update_camera_policy:"保存摄像头策略", connect:"连接 VTube Studio", request_auth:"请求 VTS 授权", list_hotkeys:"读取热键",
    scan_audio:"扫描音频设备", select_audio:"保存音频输出", test_audio:"播放设备测试音", request_audio_consent:"准备本人音频授权", revoke_audio_consent:"撤销音频授权",
    update_audio_policy:"保存语音策略", save:"保存早晨照料", disable:"关闭早晨照料", stop_session:"停止本次叫醒", test:"试听一次", cancel_reminder:"取消提醒"};
  actionNames.test_camera = "测试摄像头调用";
  const errors = {
    native_extension_inactive:"模块已暂停，请先启用模块再操作。",
    native_management_unavailable:"管理操作尚未就绪，请重新读取状态。",
    native_extension_action_failed:"操作未完成，请核对当前连接和配置。",
    vts_not_connected:"VTube Studio 尚未连接，请先连接。",
    vts_not_authenticated:"VTube Studio 尚未授权，请请求授权并在 VTS 弹窗中点允许。",
    vts_auth_timeout:"等待授权超时。请在 VTube Studio 弹窗中点允许，再重新连接核对授权结果。",
    camera_user_ineligible:"此用户没有主机摄像头使用权限，请选择管理员或主要用户。",
    camera_consent_required:"请先确认该用户的摄像头授权，再保存启用策略。",
    camera_user_not_configured:"请先确认该用户的摄像头授权，再保存使用策略。",
    native_mihome_unavailable:"米家运行时不可用，请在 AstrBot 运行环境安装米家依赖后重载插件。",
    native_service_not_running:"此模块尚未运行，请启用模块并等待启动完成。",
    native_service_changed:"模块已停止或重新配置，请刷新当前状态。",
    native_reality_action_failed:"操作未完成，请核对现实设备配置和当前用户授权。",
    native_reality_playback_failed:"音频未能播放，请核对输出设备、TTS 与当前用户授权。",
    native_reality_reminder_not_cancelled:"提醒未取消，请刷新提醒状态。",
    native_management_result_unknown:"操作结果暂不可确认，请刷新状态核对后再决定下一步。",
    native_vts_authorization_denied:"VTube Studio 未允许此次授权，请重新请求并在弹窗中点允许。",
    native_vts_authorization_required:"请在 VTube Studio 开启 API，并点击请求 VTS 授权。",
    native_vts_action_timeout:"操作超时，请核对 VTube Studio 连接，并在授权弹窗中点允许后重新连接。",
    native_vts_action_failed:"VTube Studio 操作未完成，请检查连接和设备状态。",
  };
  class Management {
    constructor(root, options) {
      this.root = root; this.options = options; this.name = options.extensionId;
      this.record = options.record; this.alive = true; this.busy = false; this.blocked = false; this.reads = 0;
      const saved = options.getState?.();
      this.cameraUser = saved?.user || ""; this.cameraDraft = saved?.draft || null;
      this.cameraOpen = saved?.open || false; this.cameraConsent = saved?.consent || false;
      this.audioOpen = saved?.audioOpen || false; this.audioDraft = saved?.audioDraft || null;
      this.cameraTestResult = saved?.cameraTestResult || null; this.cameraPreview = saved?.cameraPreview || null;
      this.mihomeFeedback = null;
      this.mihomeManage = false;
      this.mihomeOpen = new Set();
      this.mihomeProfiles = {};
      this.mihomeStatus = {};
      this.click = event => this.onClick(event);
      this.change = event => this.onChange(event);
      root.addEventListener("click", this.click); root.addEventListener("change", this.change);
      this.render();
    }
    current() { return this.alive && this.root.isConnected !== false; }
    stop() {
      this.alive = false; clearTimeout(this.timer); this.timer = null;
      this.root.removeEventListener("click", this.click); this.root.removeEventListener("change", this.change);
    }
    update(record) { this.saveCameraDraft(); this.record = record; if (this.current()) this.render(); }
    setBlocked(value) { this.blocked = value; this.updateButtons(); }
    button(action, text = actionNames[action], extra = "") {
      if (action.startsWith("mihome_")) extra += " data-native-requires-mihome";
      return `<button class="button small" type="button" data-native-action="${action}" ${extra}>${escape(text)}</button>`;
    }
    notice(message, failed = false) {
      this.message = message; this.failed = failed;
      const node = this.root.querySelector("[data-native-action-notice]");
      if (node) { node.textContent = message; node.classList.toggle("is-error", failed); }
    }
    render() {
      if (!["reality", "live"].includes(this.name)) { this.root.innerHTML = ""; return; }
      if (!this.record.management) {
        this.root.innerHTML = `<section class="native-management"><h4>连接与设备管理</h4><p>管理状态尚未读取。</p><button class="button small" type="button" data-native-management-refresh>重新读取管理状态</button><p data-native-action-notice role="status" aria-live="polite"></p></section>`;
      } else this.root.innerHTML = this.name === "reality" ? this.reality() : this.live();
      const icons = this.root.querySelectorAll?.("[data-lucide]");
      if (icons?.length && window.lucide?.createIcons) {
        window.lucide.createIcons({ nodes: [...icons], attrs: { "aria-hidden": "true" } });
      }
      if (this.name === "reality") {
        const cameraBody = this.root.querySelector("[data-native-camera-details] .native-management-body");
        if (cameraBody) cameraBody.insertAdjacentHTML("beforeend",
          `<div class="native-management-actions">${this.button("test_camera")}</div>${this.cameraTestMarkup()}`);
      }
      this.updateButtons(); this.notice(this.message || "", this.failed);
      this.scheduleRead();
    }
    reality() {
      const data = this.record.management, mi = data.mihome || {}, auth = mi.auth || {}, login = mi.login || {};
      const available = mi.available !== false && (mi.available === true || !!mi.auth || !!mi.login);
      const logged = auth.logged_in === true || login.credential_present === true;
      const loginLabels = {idle:"尚未登录", starting:"正在生成二维码", waiting:"等待扫码确认", pending:"等待扫码确认", success:"登录成功", already_logged_in:"已登录", expired:"二维码已过期", timeout:"登录已超时", error:"登录未完成"};
      const state = !available ? mi.available === false ? "米家运行时不可用" : "米家状态未读取"
        : logged ? "已登录" : loginLabels[login.status] || (login.running ? "等待扫码确认" : "尚未登录");
      const qr = qrSource(login.qr_image || this.lastQr);
      if (login.qr_available && login.qr_image) this.lastQr = qr;
      if (!login.qr_available || logged) this.lastQr = "";
      const qrMarkup = available && login.qr_available && !logged && qr ? `<div class="native-management-qr"><img src="${escape(qr)}" alt="米家登录二维码" width="180" height="180"><p>用米家 App 扫描，并在手机上确认登录。</p></div>` : "";
      const loginDetail = !logged && login.status === "error" && typeof login.detail === "string" ? login.detail.trim() : "";
      const loginDetailMarkup = loginDetail ? `<p class="native-management-login-detail">登录诊断：${escape(loginDetail)}</p>` : "";
      const qrNoticeMarkup = available && !logged && login.status === "waiting_scan" && !login.qr_available
        ? `<p class="native-management-login-detail">二维码图片未能显示或已过期，请重新发起扫码登录。</p>` : "";
      const devicesMarkup = this.mihomeDevices(mi, logged, available);
      return `<div class="native-management"><details open><summary>米家账号与设备 <span>${escape(state)}</span></summary><div class="native-management-body"><div class="native-management-actions">${this.button("mihome_start_login", logged ? "重新扫码登录" : "扫码登录")}${this.button("mihome_login_status")}${this.button("mihome_sync_devices", undefined, logged ? "" : "data-native-requires-login")}${this.button("mihome_logout", undefined, logged ? "" : "data-native-requires-login")}</div>${qrMarkup}<p class="native-management-muted">${escape(!available ? mi.reason || "米家管理状态暂不可读取，请刷新核对。" : login.message || auth.last_login_error || "")}</p>${loginDetailMarkup}${qrNoticeMarkup}${devicesMarkup}${mi.snapshot_at ? `<small class="native-management-muted">最近同步：${escape(mi.snapshot_at)}</small>` : ""}</div></details>${this.camera(data)}${this.audio(data)}<div class="native-management-bottom"><button class="button small" type="button" data-native-management-refresh>刷新管理状态</button><p data-native-action-notice role="status" aria-live="polite"></p></div></div>`;
    }
    mihomeStringList(value) {
      if (Array.isArray(value)) return value.map(item => String(item || "").trim()).filter(Boolean);
      if (typeof value === "string") return value.split(/[\r\n,，]+/).map(item => item.trim()).filter(Boolean);
      return [];
    }
    mihomeDevices(mi, logged, available) {
      const devices = Array.isArray(mi.devices) ? mi.devices : [];
      if (!devices.length) {
        const title = !available ? "米家还在准备" : logged ? "还没有同步到设备" : "先登录米家账号";
        const note = !available ? "米家运行时暂不可用，稍后刷新管理状态。"
          : logged ? "同步一次就能自动起好别名，之后只需要纠正。"
          : "扫码登录后会自动同步设备并生成别名。";
        return `<div class="mihome-empty"><span class="mihome-empty-icon"><i data-lucide="house-plug"></i></span>
          <strong>${title}</strong><p>${note}</p></div>`;
      }
      const settings = this.record.settings?.mihome || {}, managed = mi.tool_settings || {};
      const storedControl = settings.control_tool && typeof settings.control_tool === "object" ? settings.control_tool : {};
      const managedControl = managed.control_tool && typeof managed.control_tool === "object" ? managed.control_tool : {};
      const control = {...managedControl, ...storedControl};
      const allowed = new Set(this.mihomeStringList(control.allowed_devices));
      const readonly = settings.enable_readonly_tool === true
        || settings.enable_readonly_tool === undefined && managed.enable_readonly_tool === true;
      const controlEnabled = control.enable === true;
      const categories = Array.isArray(mi.categories) && mi.categories.length ? mi.categories : ["无类别"];
      const mappingRows = Array.isArray(mi.mappings) ? mi.mappings : [];
      const categoryFor = alias => {
        const row = mappingRows.find(item => item && item.alias === alias);
        return row && categories.includes(row.category) ? row.category : "";
      };
      const categoryOf = device => {
        const alias = (Array.isArray(device.aliases) ? device.aliases : [])[0] || "";
        const configured = categoryFor(alias);
        if (configured && configured !== "无类别") return configured;
        const model = String(device.model || "").toLowerCase();
        if (/acpartner|aircondition|lumi\.ac/.test(model)) return "空调类别";
        if (/plug|socket|adswb|switch/.test(model)) return "开关类别";
        if (/speaker|sound|wifispeaker/.test(model)) return "音箱类别";
        if (/lamp|light|yeelink/.test(model)) return "灯";
        if (/diffuser/.test(model)) return "香薰机";
        if (/vacuum|sweep/.test(model)) return "扫地机类别";
        if (/sensor_ht|thermo/.test(model)) return "温湿度计类别";
        return configured || "无类别";
      };
      const glyphFor = category => ({"空调类别":"snowflake", "净化器类别":"wind", "风扇类别":"fan",
        "扫地机类别":"bot", "音箱类别":"speaker", "开关类别":"toggle-left", "灯":"lightbulb",
        "香薰机":"droplets", "温湿度计类别":"thermometer", "体脂秤类别":"scale",
        "热水器类别":"flame", "路由器":"router", "门磁传感器类别":"door-open",
        "燃气传感器类别":"flame-kindling"}[category] || "cpu");
      const roomOf = device => String(device.room_name || "").trim() || "未分组";
      const rooms = [];
      for (const device of devices) {
        const room = roomOf(device);
        let group = rooms.find(item => item.room === room);
        if (!group) rooms.push(group = {room, devices: []});
        group.devices.push(device);
      }
      rooms.sort((left, right) => (left.room === "未分组" ? 1 : 0) - (right.room === "未分组" ? 1 : 0));
      const online = devices.filter(device => device.online === true).length;
      const offline = devices.filter(device => device.online === false).length;
      const named = devices.filter(device => (device.aliases || []).length > 0);
      const controllable = named.filter(device => (device.aliases || []).some(alias => allowed.has(alias))).length;
      const locked = named.filter(device => !(device.aliases || []).some(alias => allowed.has(alias))).length;
      const manage = this.mihomeManage === true;
      const roomGlyph = room => ({"卧室":"bed-double", "客厅":"sofa", "书房":"book-open",
        "餐厅":"utensils", "厨房":"cooking-pot", "浴室":"bath", "阳台":"sun",
        "玄关":"door-open", "儿童房":"baby", "衣帽间":"shirt"}[room] || "door-closed");
      const roomMarkup = rooms.map(group => `<section class="mihome-room">
        <header class="mihome-room-head"><i class="mihome-room-icon" data-lucide="${roomGlyph(group.room)}"></i><h4>${escape(group.room)}</h4><span class="mihome-room-rule"></span><small>${group.devices.length} 台</small></header>
        <div class="mihome-cards">${group.devices.map(device => this.mihomeCard(device, {
          allowed, readonly, controlEnabled, manage, categories, room: group.room, category: categoryOf(device),
          glyph: glyphFor(categoryOf(device)), categoryOptions: categories
            .map(name => `<option value="${escape(name)}"${name === categoryOf(device) ? " selected" : ""}>${escape(name)}</option>`).join(""),
        })).join("")}</div></section>`).join("");
      const pendingAliases = devices.flatMap(device => Array.isArray(device.aliases) ? device.aliases : [])
        .filter(alias => typeof alias === "string" && alias.trim() && !allowed.has(alias));
      const notes = [];
      if (!readonly) notes.push(`<span>状态读取未开启。</span><button class="button small" type="button" data-native-mihome-readonly>启用状态读取</button>`);
      if (pendingAliases.length && !locked) notes.push(`<button class="button small" type="button" data-native-mihome-allow-all>允许全部设备 · ${pendingAliases.length}</button>`);
      if (controlEnabled && control.admin_only !== false) notes.push(`<span class="mihome-note"><i data-lucide="shield-check"></i>控制只允许核心管理员发起</span>`);
      const feedback = this.mihomeFeedback ? `<div class="mihome-feedback${this.mihomeFeedback.failed ? " is-error" : ""}" role="status"><strong>${escape(this.mihomeFeedback.alias || "设备")}</strong><span>${escape(this.mihomeFeedback.message || "")}</span></div>` : "";
      return `<section class="mihome-home">
        <header class="mihome-hero">
          <div class="mihome-hero-copy">
            <span class="mihome-eyebrow">小米智能家居</span>
            <h3>家里现在 <em>${online}</em> 台在线</h3>
            <p>共 ${devices.length} 台设备${locked ? ` · ${locked} 台等待授权` : ""}${offline ? ` · ${offline} 台离线` : ""}</p>
          </div>
          <div class="mihome-hero-side">
            <div class="mihome-stats">
              <span><i class="mihome-stat-dot is-online"></i>在线<strong>${online}</strong></span>
              <span><i class="mihome-stat-dot is-control"></i>可控<strong>${controllable}</strong></span>
              <span><i class="mihome-stat-dot is-locked"></i>待授权<strong>${locked}</strong></span>
            </div>
            <button class="mihome-manage" type="button" data-native-mihome-manage aria-pressed="${manage}"><i data-lucide="${manage ? "check" : "pencil"}"></i>${manage ? "完成纠正" : "纠正设备"}</button>
          </div>
        </header>
        ${roomMarkup}
        ${manage ? `<div class="mihome-savebar"><span>改完别名或类别后保存；清空别名等于停止控制这台设备。</span><button class="button small" type="button" data-native-mihome-save-mappings>保存映射</button></div>` : ""}
        ${notes.length ? `<div class="mihome-policy">${notes.join("")}</div>` : ""}${feedback}</section>`;
    }
    mihomeCard(device, {allowed, readonly, controlEnabled, manage, room, category, glyph, categoryOptions}) {
      const aliases = (Array.isArray(device.aliases) ? device.aliases : [])
        .filter(alias => typeof alias === "string" && alias.trim());
      const primary = aliases[0] || "";
      const open = primary && this.mihomeOpen.has(primary);
      const status = primary ? (this.mihomeStatus[primary]?.properties || {}) : {};
      const locked = aliases.length > 0 && !aliases.some(alias => allowed.has(alias));
      const offline = device.online === false;
      const on = status.on;
      const tone = offline ? "offline" : locked ? "locked" : on === true ? "on" : on === false ? "off" : "unknown";
      const metaParts = [];
      if (status.mode != null) metaParts.push(String(status.mode));
      if (status.target_temperature != null) metaParts.push(`${status.target_temperature}°C`);
      if (status.brightness != null) metaParts.push(`亮度 ${status.brightness}`);
      if (status.fan_level != null) metaParts.push(`风速 ${status.fan_level}`);
      const stateText = offline ? "离线" : locked ? "待授权" : on === true ? "开" : on === false ? "关" : "未读";
      const meta = metaParts.join(" · ") || (aliases.length
        ? (locked ? "允许后即可控制" : Object.keys(status).length ? "状态可读取" : "状态未读取")
        : "还没有别名");
      const switchMarkup = controlEnabled && !locked && aliases.length
        ? `<button class="mihome-switch" type="button" role="switch" aria-checked="${on === true}" data-native-mihome-power-toggle="${escape(primary)}"${offline ? " disabled" : ""}><span class="mihome-switch-track"><span class="mihome-switch-thumb"></span></span><span class="mihome-switch-text">${on === true ? "开" : on === false ? "关" : "开关"}</span></button>`
        : "";
      const allowMarkup = locked
        ? `<button class="mihome-allow" type="button" data-native-mihome-allow="${escape(primary)}"><i data-lucide="unlock"></i>允许此设备</button>`
        : "";
      const detailMarkup = aliases.length && !locked
        ? `<button class="mihome-link" type="button" data-native-mihome-detail="${escape(primary)}">${open ? "收起" : "调节"}</button>`
        : "";
      const editor = manage ? `<div class="mihome-edit"><label><span>别名</span><input type="text" maxlength="64" value="${escape(primary)}" placeholder="如 卧室灯" data-native-mihome-alias-input></label><label><span>类别</span><select data-native-mihome-category>${categoryOptions}</select></label></div>` : "";
      const sheet = open ? `<div class="mihome-sheet"><div class="mihome-sheet-head"><span>调节</span><button class="mihome-icon-button" type="button" data-native-mihome-status="${escape(primary)}" title="刷新状态" aria-label="刷新状态"><i data-lucide="refresh-cw"></i></button></div>${this.mihomeControls(primary)}</div>` : "";
      const categoryLabel = String(category || "").replace(/类别$/, "") || "设备";
      return `<article class="mihome-card" data-native-mihome-row data-native-mihome-alias="${escape(primary)}" data-did="${escape(device.did || "")}" data-tone="${tone}">
        <header class="mihome-card-head"><span class="mihome-glyph" aria-hidden="true"><i data-lucide="${glyph}"></i></span>
          <div class="mihome-card-id"><strong>${escape(device.name || "设备")}</strong><small>${escape(room || "未分组")} · ${escape(categoryLabel)}</small></div>
          <span class="mihome-dot" data-online="${device.online === true ? "true" : device.online === false ? "false" : "unknown"}" title="${device.online === true ? "在线" : device.online === false ? "离线" : "未读取"}"></span></header>
        <div class="mihome-readout"><span class="mihome-value">${stateText}</span><span class="mihome-meta">${escape(meta)}</span></div>
        <div class="mihome-card-actions">${switchMarkup}${allowMarkup}${detailMarkup}</div>
        ${editor}${sheet}
      </article>`;
    }
    mihomeControls(alias) {
      const profile = this.mihomeProfiles[alias];
      if (!profile) return `<p class="mihome-loading">正在读取设备能力…</p>`;
      const status = this.mihomeStatus[alias]?.properties || {};
      const labels = [...(profile.writable_properties || []), ...(profile.readable_properties || [])]
        .reduce((map, item) => ({...map, [item.key]: item.name}), {});
      const chips = Object.entries(status).slice(0, 6)
        .filter(([, value]) => ["string", "number", "boolean"].includes(typeof value))
        .map(([key, value]) => `<span class="mihome-chip">${escape(labels[key] || key)}<strong>${escape(typeof value === "boolean" ? (value ? "开" : "关") : String(value))}</strong></span>`).join("");
      const fields = (profile.writable_properties || []).filter(item => item.key !== "on").map(item => {
        const current = status[item.key];
        const label = `<span class="mihome-field-label">${escape(item.name)}</span>`;
        if (item.kind === "select" && (item.options || []).length) {
          return `<div class="mihome-field">${label}<div class="mihome-segment">${item.options.map(option =>
            `<button type="button" class="${String(current) === option ? "is-active" : ""}" data-native-mihome-set="${escape(item.key)}" data-native-mihome-set-alias="${escape(alias)}" data-native-mihome-value="${escape(option)}">${escape(option)}</button>`).join("")}</div></div>`;
        }
        return `<div class="mihome-field">${label}<div class="mihome-stepper">
          <button type="button" data-native-mihome-step="-1" data-native-mihome-step-alias="${escape(alias)}" data-native-mihome-step-key="${escape(item.key)}" aria-label="减小">−</button>
          <input type="number" data-native-mihome-prop="${escape(item.key)}" value="${escape(current ?? item.examples?.[0] ?? "")}"${item.minimum != null ? ` min="${item.minimum}"` : ""}${item.maximum != null ? ` max="${item.maximum}"` : ""}>
          <button type="button" data-native-mihome-step="1" data-native-mihome-step-alias="${escape(alias)}" data-native-mihome-step-key="${escape(item.key)}" aria-label="增大">+</button>
          <button class="mihome-apply" type="button" data-native-mihome-apply="${escape(item.key)}" data-native-mihome-apply-alias="${escape(alias)}">应用</button></div></div>`;
      }).join("");
      const reason = profile.unsupported_reason ? `<p class="mihome-hint">${escape(profile.unsupported_reason)}</p>` : "";
      return `<div class="mihome-controls">${chips ? `<div class="mihome-chips">${chips}</div>` : ""}${reason}${fields || (!reason ? `<p class="mihome-hint">这台设备只支持开关，没有可调属性。</p>` : "")}</div>`;
    }
    async saveMihomeMappings() {
      if (this.busy || this.blocked || !this.current()) return;
      const rows = [];
      this.root.querySelectorAll("[data-native-mihome-row]").forEach(row => {
        const alias = String(row.querySelector("[data-native-mihome-alias-input]")?.value || "").trim();
        const category = String(row.querySelector("[data-native-mihome-category]")?.value || "无类别");
        rows.push({alias, did: String(row.dataset.did || ""), category});
      });
      if (rows.some(row => row.alias.length > 64)) {
        this.notice("设备别名最多 64 个字，请缩短后再保存。", true);
        return;
      }
      this.mihomeFeedback = null;
      await this.run("mihome_save_mappings", {mappings: rows});
    }
    toggleMihomeManage() {
      this.mihomeManage = !this.mihomeManage;
      this.render();
    }
    async toggleMihomeCard(alias) {
      if (!alias || this.busy) return;
      if (this.mihomeOpen.has(alias)) {
        this.mihomeOpen.delete(alias);
        this.render();
        return;
      }
      this.mihomeOpen.add(alias);
      this.render();
      if (!this.mihomeProfiles[alias]) await this.run("mihome_device_profile", {alias});
      await this.run("mihome_device_status", {alias});
      if (this.current()) this.render();
    }
    async toggleMihomePower(alias) {
      if (!alias || this.busy) return;
      const on = this.mihomeStatus[alias]?.properties?.on === true;
      await this.run("mihome_control_power", {alias, is_on: !on});
    }
    async applyMihomeProperty(alias, key, explicitValue) {
      if (!alias || !key || this.busy) return;
      let value = explicitValue;
      if (value == null) {
        const card = [...this.root.querySelectorAll("[data-native-mihome-alias]")]
          .find(node => node.dataset.nativeMihomeAlias === alias);
        const input = card ? [...card.querySelectorAll("[data-native-mihome-prop]")]
          .find(node => node.dataset.nativeMihomeProp === key) : null;
        value = input ? String(input.value || "").trim() : "";
      }
      if (!value) {
        this.notice("先填写要设置的值。", true);
        return;
      }
      await this.run("mihome_device_action", {alias, action: key, value});
      if (this.current()) await this.run("mihome_device_status", {alias});
    }
    adjustMihomeStep(button) {
      const input = button.closest?.(".mihome-stepper")?.querySelector("input");
      if (!input) return;
      const step = Number(button.dataset.nativeMihomeStep) || 1;
      const current = Number(input.value);
      const next = (Number.isFinite(current) ? current : 0) + step;
      const minimum = input.min === "" ? -Infinity : Number(input.min);
      const maximum = input.max === "" ? Infinity : Number(input.max);
      input.value = String(Math.min(maximum, Math.max(minimum, next)));
    }
    camera(data) {
      const users = Array.isArray(data.users) ? data.users : [], camera = data.camera || {};
      const selected = window.CompanionProduct?.target?.();
      if (!this.cameraUser) this.cameraUser = selected?.scope_kind === "private" ? String(selected.participant_id || "") : String(users[0]?.user_id || "");
      const user = users.find(row => String(row.user_id) === this.cameraUser), policy = {...(user?.camera || {}), ...(this.cameraDraft || {})};
      const eligible = user?.camera?.eligible, consented = user?.camera?.consented === true;
      const state = !Array.isArray(data.users) ? "授权状态未读取" : !user?.camera ? "尚无摄像头授权记录"
        : eligible === false ? "当前用户不可用" : consented ? "已确认授权" : "尚未确认";
      const modes = [["off","仅明确请求"],["ask","主动前先询问"],["auto","按授权主动"]];
      const cameras = Array.isArray(camera.devices) ? camera.devices : [];
      const list = cameras.length ? `<ul class="native-management-camera-devices">${cameras.map(device => `<li>${escape(device.name || device.label || "摄像头")} <span>索引 ${escape(device.index ?? device.camera_index ?? "—")}</span></li>`).join("")}</ul>` : `<p class="native-management-muted">尚未发现摄像头，点击扫描摄像头读取设备目录。</p>`;
      return `<details data-native-camera-details ${this.cameraOpen ? "open" : ""}><summary>摄像头确认与策略 <span>${state}</span></summary><div class="native-management-body"><p class="native-management-muted">${escape(camera.boundary || "按明确任务读取摄像头单帧，画面可能发送给所选视觉模型；默认不保存原图。")}</p><label class="native-management-field">用户 QQ / ID<input type="text" data-native-camera-user list="native-camera-users-${this.options.instance}" value="${escape(this.cameraUser)}" placeholder="选择或填写要管理的用户"><datalist id="native-camera-users-${this.options.instance}">${users.map(row => `<option value="${escape(row.user_id)}">${escape(row.label || row.user_id)}</option>`).join("")}</datalist></label><p class="native-management-camera-state">${escape(user?.label || this.cameraUser || "尚未选择用户")} · ${state}</p><label class="native-management-consent"><input type="checkbox" data-native-camera-consent ${this.cameraConsent ? "checked" : ""}><span>我已了解单帧读取和视觉模型处理方式，确认给此用户授权。</span></label><div class="native-management-actions">${this.button("consent_camera", "确认摄像头授权", "data-native-consent=\"true\" data-native-requires-consent")}${this.button("consent_camera", "撤销摄像头授权", "data-native-consent=\"false\"")}</div><fieldset class="native-management-policy"><legend>此用户的使用策略</legend><label class="native-management-consent"><input type="checkbox" data-native-camera-enabled ${policy.enabled ? "checked" : ""}>允许使用摄像头</label><label class="native-management-field">主动读取方式<select data-native-camera-mode>${modes.map(([value, title]) => `<option value="${value}" ${value === (policy.proactive_mode || "off") ? "selected" : ""}>${title}</option>`).join("")}</select></label><label class="native-management-field">每天主动读取次数<input type="number" data-native-camera-daily min="-1" max="10" step="1" value="${escape(policy.proactive_max_daily ?? -1)}"><small>-1 跟随全局设置，0 关闭主动读取。</small></label>${this.button("update_camera_policy")}</fieldset><div class="native-management-actions">${this.button("scan_camera")}</div>${list}${camera.devices_error ? `<p class="native-management-muted">${escape(camera.devices_error)}</p>` : ""}</div></details>`;
    }
    cameraTestMarkup() {
      const test = this.cameraTestResult;
      if (!test) return "";
      const preview = cameraPreviewSource(this.cameraPreview?.data_url);
      const title = test.status === "error" ? "摄像头测试未完成"
        : test.status === "observation_uncertain" ? "已读取，识别结果不确定" : "摄像头测试完成";
      return `<div class="native-management-camera-test" data-native-camera-test role="status" aria-live="polite"><strong>${title}</strong><p>${escape(test.message || "")}</p>${test.summary ? `<p>视觉摘要：${escape(test.summary)}</p>` : ""}${preview ? `<img class="native-management-camera-preview" src="${escape(preview)}" alt="摄像头测试单帧预览" loading="lazy">` : ""}</div>`;
    }
    audio(data) {
      const users = Array.isArray(data.users) ? data.users : [], output = data.audio_output || {};
      const user = users.find(row => String(row.user_id) === this.cameraUser), draft = this.audioDraft || {};
      const alarm = {...(user?.alarm || {}), ...(draft.alarm || {})}, policy = {...(user?.policy || {}), ...(draft.policy || {})};
      const consented = user?.consent?.local_audio === true || user?.consent?.confirmed === true;
      const devices = Array.isArray(output.devices) ? output.devices : [], selected = draft.device_id || output.selected_device_id || "system_default";
      const number = (key, title, value, min, max) => `<label class="native-management-field">${title}<input type="number" data-native-audio-input="${key}" min="${min}" max="${max}" step="1" value="${escape(value)}"></label>`;
      const days = Array.isArray(alarm.days) ? alarm.days : [0,1,2,3,4,5,6];
      const modes = [["audio_only","仅本机语音"],["audio_and_chat","本机语音与私聊"],["chat_on_failure","语音失败时发私聊"]];
      const reminders = (Array.isArray(user?.custom_reminders) ? user.custom_reminders : []).filter(row => ["registering","scheduled","triggered","delivering"].includes(row.status));
      return `<details data-native-audio-details ${this.audioOpen ? "open" : ""}><summary>本机语音与叫醒 <span>${consented ? "本人已授权" : "等待本人授权"}</span></summary><div class="native-management-body">
        <label class="native-management-field">音频输出<select data-native-audio-device>${(!devices.some(row => row.id === selected) ? `<option value="${escape(selected)}">${escape(output.label || "跟随系统默认输出")}</option>` : "")}${devices.map(row => `<option value="${escape(row.id)}" ${row.id === selected ? "selected" : ""}>${escape(row.name || row.id)}</option>`).join("")}</select></label>
        ${number("output_volume","设备测试音量（0–100）",draft.output_volume ?? output.playback_volume ?? 35,0,100)}
        <div class="native-management-actions">${this.button("scan_audio")}${this.button("select_audio")}${this.button("test_audio")}</div>${output.error ? `<p class="native-management-muted">${escape(output.error)}</p>` : ""}
        <label class="native-management-field">语音与叫醒用户 QQ / ID<input type="text" data-native-audio-user list="native-camera-users-${this.options.instance}" value="${escape(this.cameraUser)}" placeholder="选择已有私聊的用户"></label>
        <p class="native-management-muted">${escape(user?.label || this.cameraUser || "尚未选择用户")} · ${consented ? "本人音频授权已生效" : "准备授权后，将提示文字发到本人的私聊，由本人确认。"}</p>
        <div class="native-management-actions">${this.button("request_audio_consent")}${this.button("revoke_audio_consent")}</div>
        <fieldset class="native-management-policy"><legend>主动语音</legend><label class="native-management-consent"><input type="checkbox" data-native-audio-proactive ${policy.proactive_voice_enabled ? "checked" : ""}>将主动联系同步播放到本机</label>${number("policy_volume","主动语音音量（0–100）",policy.playback_volume ?? 35,0,100)}${this.button("update_audio_policy")}</fieldset>
        <fieldset class="native-management-policy"><legend>早晨照料</legend>
        <label class="native-management-consent"><input type="checkbox" data-native-alarm-enabled ${alarm.enabled ? "checked" : ""}>在早晨试着叫你，而不是按闹钟执行</label>
        <p class="native-management-muted">它会先看你醒没醒；叫一次没回应才继续，最晚到「约定时间」就停。需要本人完成本机语音授权后才会出声。</p>
        <div class="native-management-grid">
          <label class="native-management-field">大概几点起<input type="time" data-native-alarm-time value="${escape(alarm.time || "07:30")}"></label>
          <label class="native-management-field">最晚（可选）<input type="time" data-native-alarm-deadline value="${escape(alarm.deadline || "")}"></label>
          <label class="native-management-field">怎么叫<select data-native-alarm-mode>${[["gentle","轻一点"],["normal","正常"],["persistent","执着一点"]].map(([value,title]) => `<option value="${value}" ${value === (alarm.mode || "normal") ? "selected" : ""}>${title}</option>`).join("")}</select></label>
        </div>
        <label class="native-management-field">想让你记得的事（可选）<input type="text" data-native-alarm-message maxlength="240" value="${escape(alarm.message || "")}" placeholder="例如提醒吃早餐，文案由角色生成"></label>
        <div class="native-management-actions">${this.button("save")}${this.button("disable")}${this.button("stop_session")}${this.button("test")}</div><p class="native-management-muted">${alarm.next_trigger_text ? `下次：${escape(alarm.next_trigger_text)}` : "尚未安排"}${alarm.contact_session?.status ? ` · 本次：${escape(alarm.contact_session.status)}` : ""}</p></fieldset>
        ${reminders.length ? `<fieldset class="native-management-policy"><legend>待办提醒</legend><ul class="native-management-reminders">${reminders.map(row => `<li><span>${escape(row.scheduled_text || "时间待确认")} · ${escape(row.topic || "提醒")}</span>${this.button("cancel_reminder","取消这条提醒",`data-native-reminder-id="${escape(row.id)}"`)}</li>`).join("")}</ul></fieldset>` : ""}
      </div></details>`;
    }
    live() {
      const data = this.record.management, connected = data.connected === true, authenticated = data.authenticated === true;
      const hotkeys = Array.isArray(data.hotkeys) ? data.hotkeys : [];
      const label = authenticated ? "已连接并授权" : connected ? "已连接 · 等待授权" : "尚未连接";
      const table = hotkeys.length ? `<div class="native-management-table"><table><thead><tr><th>热键</th><th>ID</th><th>表情配置</th></tr></thead><tbody>${hotkeys.map((hotkey, index) => `<tr><td>${escape(hotkey.name || "未命名热键")}</td><td><code>${escape(hotkey.hotkeyID || hotkey.hotkey_id || hotkey.id || "")}</code></td><td><button class="button small" type="button" data-native-use-hotkey="${index}">添加到表情</button></td></tr>`).join("")}</tbody></table></div>` : `<p class="native-management-muted">连接并授权后，点击读取热键查看当前模型的可用表情。</p>`;
      return `<section class="native-management"><header><h4>VTube Studio</h4><span>${label}</span></header><p class="native-management-muted">先在 VTube Studio 开启 API。请求授权后，在 VTS 弹窗中点“允许”，再读取当前模型的热键。</p>${data.error_code ? `<p class="native-management-muted">${escape(errors[data.error_code] || "连接尚未就绪，请核对设备状态。")}</p>` : ""}<div class="native-management-actions">${this.button("connect", connected ? "重新连接" : "连接 VTube Studio")}${this.button("request_auth", "请求 VTS 授权")}${this.button("list_hotkeys", "读取热键", authenticated ? "" : "data-native-requires-auth")}</div>${table}<div class="native-management-bottom"><button class="button small" type="button" data-native-management-refresh>刷新管理状态</button><p data-native-action-notice role="status" aria-live="polite"></p></div></section>`;
    }
    updateButtons() {
      const mi = this.record.management?.mihome, logged = mi?.auth?.logged_in === true || mi?.login?.credential_present === true;
      const mihomeAvailable = mi?.available !== false && (mi?.available === true || !!mi?.auth || !!mi?.login);
      const consent = this.root.querySelector("[data-native-camera-consent]")?.checked === true;
      this.root.querySelectorAll("[data-native-action], [data-native-use-hotkey]").forEach(button => {
        button.disabled = this.busy || this.blocked || button.hasAttribute("data-native-requires-login") && !logged
          || button.hasAttribute("data-native-requires-mihome") && !mihomeAvailable
          || button.hasAttribute("data-native-requires-auth") && this.record.management?.authenticated !== true
          || button.hasAttribute("data-native-requires-consent") && !consent;
      });
      const refresh = this.root.querySelector("[data-native-management-refresh]");
      if (refresh) refresh.disabled = this.busy || this.blocked;
      this.root.querySelectorAll("input, select").forEach(node => { node.disabled = this.busy || this.blocked; });
      this.root.setAttribute("aria-busy", String(this.busy));
    }
    saveCameraDraft() {
      this.saveAudioDraft();
      const node = this.root.querySelector("[data-native-camera-enabled]");
      if (!node) return;
      this.cameraOpen = this.root.querySelector("[data-native-camera-details]").open;
      const draft = {enabled:node.checked, proactive_mode:this.root.querySelector("[data-native-camera-mode]").value,
        proactive_max_daily:Number(this.root.querySelector("[data-native-camera-daily]").value)};
      const stored = this.record.management?.users?.find(row => String(row.user_id) === this.cameraUser)?.camera;
      this.cameraDraft = this.differences(draft, {enabled:stored?.enabled === true,
        proactive_mode:stored?.proactive_mode || "off", proactive_max_daily:stored?.proactive_max_daily ?? -1});
      this.publishState();
    }
    differences(values, stored) {
      const result = {};
      for (const [key, value] of Object.entries(values)) {
        if (value && typeof value === "object" && !Array.isArray(value)) {
          const nested = this.differences(value, stored?.[key]);
          if (nested) result[key] = nested;
        } else if (JSON.stringify(value) !== JSON.stringify(stored?.[key])) result[key] = value;
      }
      return Object.keys(result).length ? result : null;
    }
    storedAudioValues() {
      const data = this.record.management || {}, output = data.audio_output || {};
      const user = data.users?.find(row => String(row.user_id) === this.cameraUser);
      const policy = user?.policy || {}, alarm = user?.alarm || {};
      return {device_id:output.selected_device_id || "system_default", output_volume:output.playback_volume ?? 35,
        policy:{proactive_voice_enabled:policy.proactive_voice_enabled === true, playback_volume:policy.playback_volume ?? 35},
        alarm:{enabled:alarm.enabled === true, time:alarm.time || "07:30", deadline:alarm.deadline || "",
          mode:alarm.mode || "normal", message:alarm.message || "",
          days:Array.isArray(alarm.days) ? alarm.days : [0,1,2,3,4,5,6],
          playback_volume:alarm.playback_volume ?? 35, repeat_count:alarm.repeat_count ?? 1,
          repeat_interval_seconds:alarm.repeat_interval_seconds ?? 20, snooze_minutes:alarm.snooze_minutes ?? 10,
          volume_step:alarm.volume_step ?? 8, max_volume:alarm.max_volume ?? 70, fade_in_ms:alarm.fade_in_ms ?? 800}};
    }
    audioFormValues() {
      const value = key => {
        const text = this.root.querySelector(`[data-native-audio-input="${key}"]`).value;
        return text.trim() ? Number(text) : "";
      };
      const alarm = {enabled:this.root.querySelector("[data-native-alarm-enabled]").checked,
        time:this.root.querySelector("[data-native-alarm-time]").value,
        deadline:this.root.querySelector("[data-native-alarm-deadline]").value,
        mode:this.root.querySelector("[data-native-alarm-mode]").value,
        message:this.root.querySelector("[data-native-alarm-message]").value};
      return {device_id:this.root.querySelector("[data-native-audio-device]").value, output_volume:value("output_volume"),
        policy:{proactive_voice_enabled:this.root.querySelector("[data-native-audio-proactive]").checked, playback_volume:value("policy_volume")}, alarm};
    }
    saveAudioDraft() {
      if (!this.root.querySelector("[data-native-audio-device]")) return;
      this.audioOpen = this.root.querySelector("[data-native-audio-details]").open;
      const values = this.audioFormValues();
      this.audioDraft = this.differences(values, this.storedAudioValues());
      this.publishState();
      return values;
    }
    publishState() { this.options.onState?.({user:this.cameraUser, draft:this.cameraDraft, open:this.cameraOpen, consent:this.cameraConsent, audioOpen:this.audioOpen, audioDraft:this.audioDraft, cameraTestResult:this.cameraTestResult, cameraPreview:this.cameraPreview}); }
    async read({quiet = false} = {}) {
      if (!this.current() || this.reading || this.busy && !quiet) return;
      this.reading = true;
      const operation = this.operation;
      try {
        const result = await window.CompanionProduct.request("GET", "extensions");
        if (!this.current() || operation !== this.operation) return;
        const record = result?.extensions?.[this.name];
        if (!record?.management) throw {code:"native_management_unavailable"};
        this.saveCameraDraft(); this.record.management = record.management;
        this.options.onRead?.(record); this.render();
        if (!quiet) this.notice("管理状态已刷新。");
      } catch (error) { if (this.current() && operation === this.operation && !quiet) this.notice(errors[error?.code] || "管理状态暂时读取不到，请重新刷新。", true); }
      finally { this.reading = false; }
    }
    scheduleRead() {
      const login = this.record.management?.mihome?.login;
      if (this.name !== "reality" || !login?.running || this.timer || this.reads >= 75) return;
      this.timer = setTimeout(async () => { this.timer = null; this.reads++; if (this.current()) await this.read(); }, 2000);
    }
    async run(action, payload) {
      if (this.busy || this.blocked || !this.current()) return;
      this.operation = (this.operation || 0) + 1;
      this.saveCameraDraft(); this.busy = true; this.options.onBusy?.(true); this.updateButtons();
      const waiting = action === "request_auth" ? "请在 VTube Studio 弹窗中点“允许”，正在等待授权结果…" : `正在${actionNames[action] || "执行操作"}…`;
      if (action.startsWith("mihome_")) this.mihomeFeedback = null;
      this.notice(waiting);
      try {
        const record = await window.CompanionProduct.request("POST", "extensions/action", {extension_id:this.name, action, payload}, {timeoutMs:action === "test_camera" ? 130000 : 65000});
        if (!this.current()) return;
        if (!record?.management) throw {code:"invalid_response"};
        this.record = record;
        if (action === "test_camera") {
          const result = record.result || {}, observation = result.observation || {};
          this.cameraTestResult = {status:result.captured === true ? result.status || "success" : "error",
            message:result.message || (result.captured === true ? "已完成摄像头单帧测试。" : "摄像头测试没有取得有效画面。"),
            summary:observation.summary || observation.purpose_answer || observation.scene_description || ""};
          const preview = record.camera_preview;
          this.cameraPreview = result.captured === true && preview?.ephemeral === true
            && String(preview.user_id) === String(payload.user_id) && cameraPreviewSource(preview.data_url) ? preview : null;
          this.publishState();
        }
        if (action === "update_camera_policy") this.cameraDraft = null;
        if (action === "consent_camera" && this.cameraDraft) delete this.cameraDraft.enabled;
        if (action === "consent_camera" || action === "update_camera_policy") { this.cameraConsent = false; this.publishState(); }
        if (this.audioDraft) {
          if (action === "select_audio") { delete this.audioDraft.device_id; delete this.audioDraft.output_volume; }
          if (action === "update_audio_policy") delete this.audioDraft.policy;
          if (action === "save") delete this.audioDraft.alarm;
          if (["disable","revoke_audio_consent"].includes(action) && this.audioDraft.alarm) delete this.audioDraft.alarm.enabled;
          if (action === "revoke_audio_consent" && this.audioDraft.policy) delete this.audioDraft.policy.proactive_voice_enabled;
          this.publishState();
        }
        this.options.onRecord?.(record);
        if (!this.current()) return;
        if (action === "mihome_device_profile") {
          const alias = String(payload?.alias || "");
          if (alias && record.result) this.mihomeProfiles[alias] = record.result;
        }
        if (action === "mihome_device_status") {
          const alias = String(payload?.alias || "");
          const result = record.result || {};
          if (alias && result) this.mihomeStatus[alias] = result;
          const propertyLabels = {on:"开关", power:"功率", brightness:"亮度", color_temperature:"色温",
            mode:"模式", temperature:"温度", humidity:"湿度", battery:"电量"};
          const properties = Object.entries(result.properties || {})
            .filter(([key, value]) => typeof key === "string" && ["string", "number", "boolean"].includes(typeof value))
            .map(([key, value]) => `${propertyLabels[key] || key}：${typeof value === "boolean" ? (value ? "开" : "关") : String(value)}`).join(" · ");
          this.mihomeFeedback = {alias:String(payload?.alias || "设备"), failed:result.available !== true,
            message:result.available ? (properties || "已读取，但设备没有返回公开属性。") : "设备没有返回可用状态，请确认设备在线。"};
        }
        if (action === "mihome_control_power") {
          const confirmed = record.result?.confirmed === true;
          this.mihomeFeedback = {alias:String(payload?.alias || "设备"), failed:!confirmed,
            message:`${payload?.is_on === true ? "开机" : "关机"}${confirmed ? "已确认执行。" : "已发出，但设备未确认；请刷新状态核对。"}`};
        }
        if (action === "mihome_device_action") {
          const confirmed = record.result?.confirmed === true;
          this.mihomeFeedback = {alias:String(payload?.alias || "设备"), failed:!confirmed,
            message:`${payload?.action || "设置"}：${confirmed ? "已确认执行。" : "已发出，但设备未确认；请刷新状态核对。"}`};
        }
        this.render();
        if (record.result?.confirmation_required) this.notice(record.result.prompt || "请由本人在私聊中完成音频授权确认。");
        else if (action === "request_auth" && record.management.authenticated !== true) this.notice("授权还未完成。请到 VTube Studio 弹窗中点“允许”，再重新连接核对。", true);
        else if (action === "mihome_start_login") { this.reads = 0; this.notice("登录已发起，正在生成二维码；显示后用米家 App 扫描并确认。"); this.scheduleRead(); }
        else if (action === "mihome_sync_devices") this.notice("设备已同步；原本没有别名的设备已按米家名称自动填好，确认后点「允许全部设备」即可。");
        else if (action === "test_camera") this.notice(this.cameraTestResult.message, this.cameraTestResult.status === "error");
        else if (action === "mihome_device_status" || action === "mihome_control_power" || action === "mihome_device_action") this.notice(this.mihomeFeedback?.message || "设备操作已完成。", this.mihomeFeedback?.failed === true);
        else if (action === "mihome_device_profile") { /* 卡片展开本身就是反馈 */ }
        else this.notice(`${actionNames[action] || "操作"}已完成。`);
      } catch (error) {
        if (this.current()) {
          const message = errors[error?.code] || window.CompanionProduct.errorLabel?.(error?.code) || "操作结果暂未确认，请刷新状态核对后再试。";
          if (action === "test_camera") { this.cameraTestResult = {status:"error", message, summary:""}; this.cameraPreview = null; this.publishState(); }
          this.notice(message, true);
          await this.read({quiet:true});
        }
      } finally {
        if (this.current()) { this.busy = false; this.options.onBusy?.(false); this.updateButtons(); }
      }
    }
    async updateMihomeConfig(updates, message) {
      if (this.busy || this.blocked || !this.current()) return;
      this.busy = true; this.options.onBusy?.(true); this.updateButtons();
      this.notice("正在保存设备控制策略并应用…");
      try {
        const record = await window.CompanionProduct.request("POST", "extensions/configure", {
          extension_id:"reality", updates:{mihome:updates}, revision:this.record.revision,
        }, {timeoutMs:60000});
        if (!this.current()) return;
        if (!record?.settings) throw {code:"invalid_response"};
        this.record = {...this.record, ...record, management:this.record.management};
        this.options.onRecord?.(this.record);
        this.render();
        this.notice(message);
        await this.read({quiet:true});
        this.notice(message);
      } catch (error) {
        if (this.current()) this.notice(errors[error?.code] || "设备控制策略保存失败，请刷新后重试。", true);
      } finally {
        if (this.current()) { this.busy = false; this.options.onBusy?.(false); this.updateButtons(); }
      }
    }
    async allowMihomeAlias(alias) {
      const control = this.record.settings?.mihome?.control_tool || {};
      const allowed = this.mihomeStringList(control.allowed_devices);
      if (!allowed.includes(alias)) allowed.push(alias);
      const enable = control.enable !== true;
      const update = {control_tool:{allowed_devices:allowed}};
      if (enable) update.control_tool.enable = true;
      await this.updateMihomeConfig(update, enable
        ? `已允许 ${alias}，并启用设备控制。`
        : `已允许 ${alias} 的设备控制。`);
    }
    async allowAllMihomeAliases() {
      const control = this.record.settings?.mihome?.control_tool || {};
      const allowed = this.mihomeStringList(control.allowed_devices);
      for (const device of this.record.management?.mihome?.devices || []) {
        for (const alias of Array.isArray(device.aliases) ? device.aliases : []) {
          if (typeof alias === "string" && alias.trim() && !allowed.includes(alias)) allowed.push(alias);
        }
      }
      if (!allowed.length) {
        this.notice("还没有可用别名，请先同步设备。", true);
        return;
      }
      await this.updateMihomeConfig({control_tool:{allowed_devices:allowed, enable:true}},
        `已允许全部 ${allowed.length} 台设备，并启用控制。`);
    }
    payload(action, button) {
      if (action === "mihome_login_status") return {qr_revision:this.record.management?.mihome?.login?.qr_revision || ""};
      if (action === "mihome_device_status" || action === "mihome_control_power") {
        const alias = String(button?.dataset?.nativeMihomeAlias || "").trim();
        if (!alias) throw new Error("设备别名缺失，请刷新页面后重试。");
        if (action === "mihome_control_power") return {alias, is_on:button.dataset.nativeMihomePower === "true"};
        return {alias};
      }
      if (["select_audio","test_audio","request_audio_consent","revoke_audio_consent","update_audio_policy","save","disable","stop_session","test","cancel_reminder"].includes(action)) {
        const draft = this.saveAudioDraft();
        if (action === "select_audio") return {device_id:draft.device_id, playback_volume:draft.output_volume};
        if (action === "test_audio") return {playback_volume:draft.output_volume};
        const user_id = this.root.querySelector("[data-native-audio-user]").value.trim();
        if (!user_id) throw new Error("先选择已有私聊的用户。");
        if (action === "update_audio_policy") return {user_id, ...draft.policy};
        if (action === "save") {
          if (!draft.alarm.time) throw new Error("请选择大概的起床时间。");
          if (draft.alarm.deadline && draft.alarm.deadline <= draft.alarm.time) throw new Error("最晚时间需要晚于起床时间。");
          return {user_id, ...draft.alarm};
        }
        if (action === "test") return {user_id, playback_volume:draft.alarm.playback_volume, message:draft.alarm.message};
        if (action === "cancel_reminder") return {user_id, reminder_id:button.dataset.nativeReminderId};
        return {user_id};
      }
      if (["consent_camera", "update_camera_policy"].includes(action)) {
        const user_id = this.root.querySelector("[data-native-camera-user]").value.trim();
        if (!user_id) throw new Error("先选择或填写要管理的用户。");
        if (action === "consent_camera") return {user_id, confirmed:button.dataset.nativeConsent === "true"};
        const daily = this.root.querySelector("[data-native-camera-daily]").value;
        const proactive_max_daily = Number(daily);
        if (!daily.trim() || !Number.isInteger(proactive_max_daily) || proactive_max_daily < -1 || proactive_max_daily > 10) throw new Error("主动读取次数应为 -1 到 10 的整数。");
        return {user_id, camera_enabled:this.root.querySelector("[data-native-camera-enabled]").checked,
          proactive_mode:this.root.querySelector("[data-native-camera-mode]").value, proactive_max_daily};
      }
      if (action === "test_camera") {
        const user_id = this.root.querySelector("[data-native-camera-user]").value.trim();
        if (!user_id) throw new Error("先选择已有 V2 私聊的用户。");
        return {user_id, purpose:"拓展页摄像头单帧测试"};
      }
      return {};
    }
    onClick(event) {
      const button = event.target.closest?.("button");
      if (!button || !this.root.contains(button) || button.disabled) return;
      if (button.hasAttribute("data-native-management-refresh")) { this.read(); return; }
      if (button.hasAttribute("data-native-use-hotkey")) {
        const hotkey = this.record.management.hotkeys[Number(button.dataset.nativeUseHotkey)];
        this.options.onUseHotkey?.(hotkey); return;
      }
      if (button.hasAttribute("data-native-mihome-allow")) {
        void this.allowMihomeAlias(button.dataset.nativeMihomeAllow); return;
      }
      if (button.hasAttribute("data-native-mihome-allow-all")) {
        void this.allowAllMihomeAliases(); return;
      }
      if (button.hasAttribute("data-native-mihome-manage")) {
        this.toggleMihomeManage(); return;
      }
      if (button.hasAttribute("data-native-mihome-detail")) {
        void this.toggleMihomeCard(button.dataset.nativeMihomeDetail); return;
      }
      if (button.hasAttribute("data-native-mihome-power-toggle")) {
        void this.toggleMihomePower(button.dataset.nativeMihomePowerToggle); return;
      }
      if (button.hasAttribute("data-native-mihome-status")) {
        void this.run("mihome_device_status", {alias: button.dataset.nativeMihomeStatus}); return;
      }
      if (button.hasAttribute("data-native-mihome-apply")) {
        void this.applyMihomeProperty(button.dataset.nativeMihomeApplyAlias, button.dataset.nativeMihomeApply); return;
      }
      if (button.hasAttribute("data-native-mihome-set")) {
        void this.applyMihomeProperty(button.dataset.nativeMihomeSetAlias,
          button.dataset.nativeMihomeSet, button.dataset.nativeMihomeValue); return;
      }
      if (button.hasAttribute("data-native-mihome-step")) {
        this.adjustMihomeStep(button); return;
      }
      if (button.hasAttribute("data-native-mihome-save-mappings")) {
        void this.saveMihomeMappings(); return;
      }
      if (button.hasAttribute("data-native-mihome-enable-control")) {
        void this.updateMihomeConfig({control_tool:{enable:true}}, "设备控制已启用。"); return;
      }
      if (button.hasAttribute("data-native-mihome-readonly")) {
        void this.updateMihomeConfig({enable_readonly_tool:true}, "设备状态读取已启用。"); return;
      }
      const action = button.dataset.nativeAction;
      if (action) { try { this.run(action, this.payload(action, button)); } catch (error) { this.notice(error.message, true); } }
    }
    onChange(event) {
      if (event.target.hasAttribute?.("data-native-camera-user") || event.target.hasAttribute?.("data-native-audio-user")) {
        this.cameraUser = event.target.value.trim(); this.cameraDraft = null; this.audioDraft = null; this.cameraConsent = false;
        this.cameraTestResult = null; this.cameraPreview = null;
        if (event.target.hasAttribute("data-native-audio-user")) this.audioOpen = true; else this.cameraOpen = true;
        this.publishState(); this.render();
      } else {
        if (event.target.hasAttribute?.("data-native-camera-consent")) this.cameraConsent = event.target.checked;
        this.saveCameraDraft(); this.updateButtons();
      }
    }
  }
  return {mount:(root, options) => new Management(root, options)};
})();
