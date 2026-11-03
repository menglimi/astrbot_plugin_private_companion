(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const CAMERA_PREVIEW_WIDTH = 1920;
  const CAMERA_PREVIEW_HEIGHT = 1080;
  const CAMERA_PREVIEW_FRAME_RATE = 30;
  const CAMERA_UPLOAD_MAX_WIDTH = 640;
  const CAMERA_UPLOAD_JPEG_QUALITY = .70;
  const TTS_VOLUME_STORAGE_KEY = "together_tts_volume_percent";

  const state = {
    socket: null,
    connected: false,
    pingTimer: 0,
    room: null,
    memberRole: "owner",
    mode: "call",
    sttMode: "auto",
    callActive: false,
    callTtsEnabled: true,
    callIdleTimer: 0,
    recognition: null,
    recognitionRunning: false,
    recognitionRestartTimer: 0,
    recognitionFailCount: 0,
    browserRecognitionUnavailable: false,
    sttFallbackPending: false,
    mediaStream: null,
    talkMode: "free",
    talkKeyCode: "Space",
    talkKeyCapturing: false,
    pushToTalkHeld: false,
    voiceActivityContext: null,
    voiceActivityAnalyser: null,
    voiceActivitySource: null,
    voiceActivityTimer: 0,
    voiceActivityLastHeardAt: 0,
    voiceActivityStartedAt: 0,
    voiceActivityFrames: 0,
    cameraStream: null,
    cameraEnabled: false,
    cameraDevices: [],
    selectedCameraId: "",
    cameraFrameTimer: 0,
    cameraCaptureBusy: false,
    inviteRequestPending: false,
    recorder: null,
    recording: false,
    botSpeaking: false,
    speakingWatchdogTimer: 0,
    audioQueue: [],
    currentAudio: null,
    currentAudioUrl: "",
    ttsVolume: 1,
    browserUtterance: null,
    draftNode: null,
    cancellableUtterances: new Map(),
    objectVideoUrl: "",
    videoSourceLabel: "",
    videoQualityLabel: "",
    dashAudioEnabled: false,
    lastWatchSpeechText: "",
    lastWatchSpeechAt: 0,
    lastFrameSentAt: 0,
    frameTimer: 0,
    frameCaptureBusy: false,
    lastSceneSignature: null,
    signatureCanvas: null,
    signatureContext: null,
    openingSent: false,
    openingTimer: 0,
    lastPlayerStateSentAt: 0,
    videoVolumeBeforeDuck: null,
    volumeAnimationFrame: 0,
    pseudoFullscreen: false,
    fullscreenSpeechToken: 0,
    fullscreenTypingTimer: 0,
    fullscreenHoldTimer: 0,
    fullscreenSafetyTimer: 0,
    fullscreenTypingDone: false,
    fullscreenSpeechStarted: false,
    fullscreenSpeechFinished: false,
    frameWarningShown: false,
    resolvingMedia: false,
    remoteCorsFallbackTried: false,
    toastTimer: 0,
    resumeToken: "",
    reconnectAttempts: 0,
    reconnectTimer: 0,
    wakeLock: null,
    pendingResumeTime: 0,
    videoSeekFeedbackTimer: 0,
    videoControlsHideTimer: 0,
    videoRateHoldTimer: 0,
    videoRateHoldActive: false,
    videoRateHoldSource: null,
    videoRateBeforeHold: 1,
    videoSurfaceSuppressClick: false,
    workContext: null,
    workState: null,
    novelBooks: [],
    novelBook: null,
    novelChapters: [],
    novelChapter: null,
    novelPosition: null,
    novelSelectedText: "",
    novelProgressTimer: 0,
    novelProgressFrame: 0,
    novelProgressSequence: 0,
    novelProgressDirty: false,
    novelRestoringPosition: false,
    novelPendingChapterIndex: null,
    novelNavigationSequence: 0,
    novelParagraphElements: [],
    novelDirectoryRange: { start: 0, end: 0 },
    novelImporting: false,
  };

  function icons() {
    if (window.lucide?.createIcons) window.lucide.createIcons();
  }

  function showToast(message, duration = 2800) {
    const toast = $("#toast");
    window.clearTimeout(state.toastTimer);
    toast.textContent = message;
    toast.hidden = false;
    state.toastTimer = window.setTimeout(() => { toast.hidden = true; }, duration);
  }

  function setSettingsOpen(open, restoreFocus = false) {
    const panel = $("#settingsPanel");
    const button = $("#settingsButton");
    if (!open && state.talkKeyCapturing) {
      state.talkKeyCapturing = false;
      updateTalkControls();
    }
    panel.hidden = !open;
    button.setAttribute("aria-expanded", String(Boolean(open)));
    if (open) {
      refreshCameraDevices(state.cameraStream?.getVideoTracks?.()[0]?.getSettings?.()?.deviceId || "").catch(() => {});
      $("#closeSettings").focus();
    }
    else if (restoreFocus) button.focus();
  }

  function setConnection(text, connected = state.connected) {
    $("#connectionStatus").textContent = text;
    $("#sendMessage").disabled = !connected;
    const button = $("#reconnectButton");
    if (button) {
      const retryable = !connected && /断开|无法|关闭/.test(text);
      button.hidden = !retryable;
    }
  }

  async function requestWakeLock() {
    if (!state.callActive || state.wakeLock || document.visibilityState === "hidden") return;
    if (!navigator.wakeLock?.request) return;
    try {
      const lock = await navigator.wakeLock.request("screen");
      if (!state.callActive) {
        try { lock.release(); } catch { /* already released */ }
        return;
      }
      state.wakeLock = lock;
      lock.addEventListener("release", () => {
        if (state.wakeLock === lock) state.wakeLock = null;
      }, { once: true });
    } catch {
      state.wakeLock = null;
    }
  }

  function releaseWakeLock() {
    const lock = state.wakeLock;
    state.wakeLock = null;
    if (!lock) return;
    try { lock.release(); } catch { /* the browser may already have released it */ }
  }

  function reconnectNow() {
    window.clearTimeout(state.reconnectTimer);
    state.reconnectAttempts = 0;
    if (state.socket && state.socket.readyState <= WebSocket.OPEN) return;
    const stale = state.socket;
    state.socket = null;
    if (stale) {
      try { stale.close(); } catch { /* already closed */ }
    }
    setConnection("正在重新连接", false);
    connect();
  }

  function pageCleanup() {
    releaseWakeLock();
    stopCall(false);
    stopAudio();
    window.clearTimeout(state.cameraFrameTimer);
    window.clearInterval(state.frameTimer);
    window.clearInterval(state.pingTimer);
    window.clearTimeout(state.reconnectTimer);
    window.clearTimeout(state.openingTimer);
    window.clearTimeout(state.videoControlsHideTimer);
    window.cancelAnimationFrame(state.volumeAnimationFrame);
    if (state.objectVideoUrl) URL.revokeObjectURL(state.objectVideoUrl);
    const socket = state.socket;
    state.socket = null;
    state.connected = false;
    if (socket?.readyState === WebSocket.OPEN) socket.close();
  }

  function handleVisibilityChange() {
    if (document.visibilityState === "hidden") {
      releaseWakeLock();
      window.clearTimeout(state.cameraFrameTimer);
      state.cameraFrameTimer = 0;
      return;
    }
    if (state.callActive) {
      requestWakeLock();
      if (state.cameraEnabled) scheduleCameraFrame(1200);
    }
    if (!state.connected
        && (!state.socket || state.socket.readyState === WebSocket.CLOSED)) {
      reconnectNow();
    }
  }

  function setRoomStatus(status, text) {
    $("#callStatus").textContent = text || "";
    $("#watchStatus").textContent = text || "";
    $("#workVoiceStatus").textContent = text || "";
    $("#novelStatus").textContent = text || "";
    $("#avatarStage").dataset.state = status === "speaking" ? "speaking" : (state.callActive ? "listening" : "idle");
  }

  function isWatchFullscreen() {
    const stage = $("#videoStage");
    return document.fullscreenElement === stage || document.webkitFullscreenElement === stage || stage.classList.contains("pseudo-fullscreen");
  }

  function updateFullscreenButton() {
    const active = isWatchFullscreen();
    const button = $("#toggleFullscreen");
    button.title = active ? "退出全屏" : "进入全屏";
    button.setAttribute("aria-label", active ? "退出全屏" : "进入全屏");
    button.innerHTML = `<i data-lucide="${active ? "minimize" : "maximize"}"></i>`;
    icons();
  }

  function setPseudoFullscreen(active) {
    state.pseudoFullscreen = Boolean(active);
    $("#videoStage").classList.toggle("pseudo-fullscreen", state.pseudoFullscreen);
    document.body.classList.toggle("watch-pseudo-fullscreen", state.pseudoFullscreen);
    if (!state.pseudoFullscreen) hideFullscreenSpeech(true);
    updateFullscreenButton();
  }

  async function toggleWatchFullscreen() {
    const stage = $("#videoStage");
    if (state.pseudoFullscreen) {
      setPseudoFullscreen(false);
      return;
    }
    if (document.fullscreenElement || document.webkitFullscreenElement) {
      try {
        if (document.exitFullscreen) await document.exitFullscreen();
        else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
      } catch { /* noop */ }
      return;
    }
    try {
      if (stage.requestFullscreen) await stage.requestFullscreen({ navigationUI: "hide" });
      else if (stage.webkitRequestFullscreen) stage.webkitRequestFullscreen();
      else setPseudoFullscreen(true);
    } catch {
      setPseudoFullscreen(true);
    }
  }

  function clearFullscreenSpeechTimers() {
    window.clearTimeout(state.fullscreenTypingTimer);
    window.clearTimeout(state.fullscreenHoldTimer);
    window.clearTimeout(state.fullscreenSafetyTimer);
  }

  function showFullscreenSpeech(text) {
    const content = String(text || "").trim();
    if (!content || state.mode !== "watch" || !isWatchFullscreen()) return;
    const overlay = $("#fullscreenCompanion");
    const output = $("#fullscreenSpeechText");
    const token = state.fullscreenSpeechToken + 1;
    state.fullscreenSpeechToken = token;
    clearFullscreenSpeechTimers();
    state.fullscreenTypingDone = false;
    state.fullscreenSpeechStarted = false;
    state.fullscreenSpeechFinished = false;
    overlay.hidden = false;
    overlay.classList.remove("avatar-visible", "bubble-visible", "typing-done", "leaving");
    output.textContent = "";
    window.requestAnimationFrame(() => {
      if (state.fullscreenSpeechToken !== token) return;
      overlay.classList.add("avatar-visible");
      window.setTimeout(() => {
        if (state.fullscreenSpeechToken !== token) return;
        overlay.classList.add("bubble-visible");
        typeFullscreenSpeech(content, token);
      }, 190);
    });
    state.fullscreenSafetyTimer = window.setTimeout(() => {
      if (state.fullscreenSpeechToken !== token) return;
      state.fullscreenSpeechFinished = true;
      maybeFinishFullscreenSpeech(token);
    }, 90000);
  }

  function typeFullscreenSpeech(text, token) {
    const characters = Array.from(text);
    const output = $("#fullscreenSpeechText");
    let index = 0;
    const baseDelay = characters.length > 80 ? 22 : (characters.length > 40 ? 28 : 36);
    const typeNext = () => {
      if (state.fullscreenSpeechToken !== token) return;
      if (index >= characters.length) {
        state.fullscreenTypingDone = true;
        $("#fullscreenCompanion").classList.add("typing-done");
        maybeFinishFullscreenSpeech(token);
        return;
      }
      const character = characters[index];
      output.textContent += character;
      index += 1;
      const punctuationPause = /[，。！？；、,.!?]/.test(character) ? 85 : 0;
      state.fullscreenTypingTimer = window.setTimeout(typeNext, baseDelay + punctuationPause);
    };
    typeNext();
  }

  function markFullscreenSpeechStarted() {
    const overlay = $("#fullscreenCompanion");
    if (overlay.hidden) return;
    state.fullscreenSpeechStarted = true;
    state.fullscreenSpeechFinished = false;
  }

  function markFullscreenSpeechFinished() {
    const overlay = $("#fullscreenCompanion");
    if (overlay.hidden) return;
    state.fullscreenSpeechFinished = true;
    maybeFinishFullscreenSpeech(state.fullscreenSpeechToken);
  }

  function maybeFinishFullscreenSpeech(token) {
    if (state.fullscreenSpeechToken !== token || !state.fullscreenTypingDone || !state.fullscreenSpeechFinished) return;
    window.clearTimeout(state.fullscreenHoldTimer);
    state.fullscreenHoldTimer = window.setTimeout(() => hideFullscreenSpeech(false, token), 2000);
  }

  function hideFullscreenSpeech(immediate = false, token = state.fullscreenSpeechToken) {
    if (state.fullscreenSpeechToken !== token) return;
    const overlay = $("#fullscreenCompanion");
    clearFullscreenSpeechTimers();
    state.fullscreenSpeechToken += 1;
    if (overlay.hidden) return;
    if (immediate) {
      overlay.hidden = true;
      overlay.classList.remove("avatar-visible", "bubble-visible", "typing-done", "leaving");
      $("#fullscreenSpeechText").textContent = "";
      return;
    }
    overlay.classList.add("leaving");
    window.setTimeout(() => {
      if (!overlay.classList.contains("leaving")) return;
      overlay.hidden = true;
      overlay.classList.remove("avatar-visible", "bubble-visible", "typing-done", "leaving");
      $("#fullscreenSpeechText").textContent = "";
    }, 560);
  }

  function ticketFromLocation() {
    const params = new URLSearchParams(window.location.search);
    const pathMatch = window.location.pathname.match(/^\/join\/([A-Za-z0-9_-]{16,128})\/?$/);
    const fromPath = pathMatch ? pathMatch[1] : "";
    const fromQuery = params.get("ticket") || "";
    const fromUrl = fromPath || fromQuery;
    if (fromUrl) sessionStorage.setItem("together_room_ticket", fromUrl);
    const mode = params.get("mode");
    if (["watch", "call", "work", "novel"].includes(mode)) state.mode = mode;
    if (fromUrl) {
      params.delete("ticket");
      const clean = `/${params.toString() ? `?${params}` : ""}`;
      window.history.replaceState({}, "", clean);
    }
    return fromUrl || sessionStorage.getItem("together_room_ticket") || "";
  }

  function connect() {
    const ticket = ticketFromLocation();
    const resumeToken = state.resumeToken || sessionStorage.getItem("together_resume_token") || "";
    if (!resumeToken && !ticket) {
      setConnection("房间链接缺少凭证", false);
      showToast("请使用拓展页、QQ 消息或房间内生成的完整邀请链接", 5000);
      return;
    }
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const query = resumeToken
      ? `resume=${encodeURIComponent(resumeToken)}`
      : `ticket=${encodeURIComponent(ticket)}`;
    const socket = new WebSocket(`${scheme}://${window.location.host}/ws?${query}`);
    state.socket = socket;
    setConnection(resumeToken ? "正在恢复房间" : "正在连接", false);

    socket.addEventListener("open", () => setConnection("正在确认房间", false));
    socket.addEventListener("message", (event) => {
      try { handleServerMessage(JSON.parse(event.data)); }
      catch (error) { console.error("Invalid room payload", error); }
    });
    socket.addEventListener("close", () => {
      if (state.socket !== socket) return;
      window.clearInterval(state.pingTimer);
      const wasConnected = state.connected;
      state.connected = false;
      state.inviteRequestPending = false;
      clearCancellableUtterances();
      $("#inviteButton").disabled = true;
      if (!wasConnected) {
        // 握手失败：resume 凭证可能已失效，清除后回退票据重试
        if (state.resumeToken || sessionStorage.getItem("together_resume_token")) {
          state.resumeToken = "";
          sessionStorage.removeItem("together_resume_token");
          scheduleReconnect();
          return;
        }
        setConnection("无法连接房间", false);
        showToast("请从插件拓展页或 QQ 指令重新打开房间", 5000);
        return;
      }
      $("#watchVideo").pause();
      stopCall(false);
      stopAudio();
      setConnection("房间已断开，正在重连", false);
      scheduleReconnect();
    });
    socket.addEventListener("error", () => {
      if (!state.connected) setConnection("无法连接房间", false);
    });
  }

  function scheduleReconnect() {
    window.clearTimeout(state.reconnectTimer);
    if (state.reconnectAttempts >= 8) {
      setConnection("房间已断开", false);
      showToast("房间连接已关闭，请重新从入口打开", 5000);
      return;
    }
    const delay = Math.min(8000, 1000 * (2 ** state.reconnectAttempts));
    state.reconnectAttempts += 1;
    state.reconnectTimer = window.setTimeout(connect, delay);
  }

  function send(payload) {
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
      showToast("房间尚未连接");
      return false;
    }
    state.socket.send(JSON.stringify(payload));
    return true;
  }

  function clearCallIdleTimer() {
    window.clearTimeout(state.callIdleTimer);
    state.callIdleTimer = 0;
  }

  function scheduleCallIdleTimer() {
    clearCallIdleTimer();
    if (!state.connected || !state.callActive || state.mode !== "call" || state.botSpeaking) return;
    if (!(state.room?.call?.proactive_enabled || state.room?.call?.model_hangup_enabled)) return;
    const idleSeconds = Math.max(60, Math.min(900, Number(state.room.call.idle_seconds) || 120));
    state.callIdleTimer = window.setTimeout(() => {
      if (state.callActive && state.mode === "call" && !state.botSpeaking) {
        send({ type: "call_idle", ...clientTimeContext() });
        scheduleCallIdleTimer();
      }
    }, idleSeconds * 1000);
  }

  function noteCallActivity() {
    if (state.callActive && state.mode === "call") scheduleCallIdleTimer();
  }

  function handleServerMessage(message) {
    switch (message.type) {
      case "ready":
        state.connected = true;
        state.reconnectAttempts = 0;
        state.room = message.room || {};
        state.memberRole = message.member_role === "guest" ? "guest" : "owner";
        document.body.dataset.memberRole = state.memberRole;
        $("#inviteButton").disabled = state.memberRole !== "owner" || state.room?.invite_available === false;
        $("#inviteButton").title = state.room?.invite_available === false
          ? "请在 V2 陪伴对话中创建邀请"
          : "生成邀请链接";
        if (message.resume_token) {
          state.resumeToken = message.resume_token;
          sessionStorage.setItem("together_resume_token", message.resume_token);
        }
        configureRoom();
        if (message.resumed) showToast("已恢复房间");
        if (message.joined) showToast("已加入同一个房间");
        setConnection("已连接", true);
        window.clearInterval(state.pingTimer);
        state.pingTimer = window.setInterval(() => {
          if (state.socket?.readyState === WebSocket.OPEN) {
            state.socket.send(JSON.stringify({ type: "ping" }));
          }
        }, 45000);
        break;
      case "work_context":
        applyWorkContext(message.context || {});
        break;
      case "work_state":
        applyWorkState(message.state || {});
        break;
      case "novel_library":
        renderNovelLibrary(message.books || []);
        break;
      case "novel_chapter":
        renderNovelChapter(message);
        break;
      case "novel_state":
        applyNovelState(message.state || {});
        break;
      case "invite_link":
        state.inviteRequestPending = false;
        $("#inviteButton").disabled = false;
        $("#inviteUrl").value = String(message.url || "");
        if (!$("#inviteDialog").open) $("#inviteDialog").showModal();
        break;
      case "status":
        if (state.botSpeaking && message.state !== "speaking") break;
        setRoomStatus(message.state, message.text);
        break;
      case "mode":
        setMode(message.mode, false);
        break;
      case "media_resolving":
        state.resolvingMedia = true;
        $("#videoTitle").textContent = "正在解析视频";
        $("#watchStatus").textContent = "正在解析";
        setVideoPlaceholder(message.message || "正在解析视频链接", true);
        break;
      case "media_ready":
        state.resolvingMedia = false;
        $("#watchStatus").textContent = "正在加载";
        state.pendingResumeTime = Math.max(0, Number(message.resume_time) || 0);
        loadVideo(
          message.url,
          message.title || "B 站视频",
          false,
          message.quality_label || "",
          message.playback_mode === "dash" ? message.audio_url : "",
        );
        break;
      case "media_error":
        state.resolvingMedia = false;
        $("#videoTitle").textContent = "尚未选择视频";
        $("#watchStatus").textContent = "等待视频";
        setVideoPlaceholder("选择一段视频", true);
        showToast(message.message || "视频链接解析失败", 5000);
        break;
      case "user_text":
        clearDraft();
        clearCancellableUtterances();
        addMessage("user", message.text, message.speaker || "", {
          utteranceId: message.cancellable ? String(message.utterance_id || "") : "",
        });
        break;
      case "utterance_excluded":
        removeCancellableUtterance(String(message.id || ""));
        if (message.excluded) showToast("已排除这条语音，不会进入回复");
        break;
      case "watch_tts":
        $("#watchTtsEnabled").checked = message.enabled !== false;
        break;
      case "call_tts":
        state.callTtsEnabled = message.enabled !== false;
        updateCallTtsButton();
        break;
      case "bot_delta":
        appendDraft(message.text);
        break;
      case "bot_text":
        clearDraft();
        clearCancellableUtterances();
        const visibleBotText = sanitizeBotDisplayText(message.text);
        addMessage(
          "bot",
          visibleBotText,
          message.source === "watch_comment" ? "观影" : (message.source === "call_proactive" ? "主动" : ""),
        );
        showFullscreenSpeech(visibleBotText);
        scheduleCallIdleTimer();
        break;
      case "audio":
        clearDraft();
        clearCancellableUtterances();
        if (state.mode === "call" && !state.callTtsEnabled) {
          if (message.display_text || message.text) addMessage("bot", message.display_text || message.text);
          break;
        }
        if (isDuplicateWatchSpeech(message)) break;
        enqueueAudio(message);
        break;
      case "tts_fallback":
        clearDraft();
        clearCancellableUtterances();
        if (state.mode === "call" && !state.callTtsEnabled) {
          if (message.display_text || message.text) addMessage("bot", message.display_text || message.text);
          break;
        }
        if (isDuplicateWatchSpeech(message)) break;
        if (state.room?.tts?.browser_fallback) {
          speakInBrowser(
            message.text,
            message.language,
            message.display_text,
            message.source,
            message.after_playback_action,
          );
        }
        else {
          revealSpeechMessage(message);
          markFullscreenSpeechStarted();
          markFullscreenSpeechFinished();
          resumeRecognitionAfterBot();
        }
        break;
      case "stop_audio":
        clearDraft();
        clearCancellableUtterances();
        stopAudio(false);
        break;
      case "notice":
        showToast(message.message || "房间状态发生变化", 4200);
        break;
      case "error":
        clearDraft();
        clearCancellableUtterances();
        setRoomStatus(state.callActive ? "listening" : "idle", state.callActive ? "正在听" : "等待接通");
        showToast(message.message || "房间处理失败", 4500);
        break;
      default:
        break;
    }
  }

  function configureRoom() {
    const room = state.room || {};
    const guest = state.memberRole === "guest";
    $("#inviteButton").disabled = guest || room.invite_available === false;
    $("#inviteButton").title = room.invite_available === false
      ? "请在 V2 陪伴对话中创建邀请"
      : "生成邀请链接";
    const botName = room.bot_name || "Bot";
    $("#brandName").textContent = botName;
    $("#callName").textContent = botName;
    $("#brandAvatar").src = room.avatar_url || "/avatar";
    $("#callAvatar").src = room.avatar_url || "/avatar";
    $("#cameraBotAvatar").src = room.avatar_url || "/avatar";
    $("#cameraBotName").textContent = botName;
    $("#workAvatar").src = room.avatar_url || "/avatar";
    $("#workBotName").textContent = botName;
    $("#fullscreenAvatar").src = room.avatar_url || "/avatar";
    const workAvailable = room.work?.available === true;
    const workTab = $('[data-mode-tab="work"]');
    workTab.hidden = !workAvailable;
    $$('[data-mode-tab]').forEach((button) => { button.disabled = guest; });
    $(".mode-tabs").classList.toggle("has-work", workAvailable);
    if (workAvailable) {
      applyWorkContext(room.work?.context || {});
      applyWorkState(room.work?.state || {});
    }
    const novelAvailable = room.novel?.enabled !== false;
    const novelTab = $('[data-mode-tab="novel"]');
    novelTab.hidden = guest || !novelAvailable;
    workTab.hidden = guest || !workAvailable;
    $(".mode-tabs").classList.toggle("has-novel", novelAvailable);
    renderNovelLibrary(room.novel?.books || []);
    if (room.novel?.state?.book_id) state.novelPosition = room.novel.state;
    state.sttMode = room.stt?.mode || "auto";
    state.callTtsEnabled = room.call?.tts_enabled !== false;
    setActiveSttButton(state.sttMode);
    $("#chatCapability").textContent = room.chat?.available ? (room.chat.label || "可用") : "未配置";
    $("#visionCapability").textContent = room.vision?.available ? (room.vision.label || "可用") : "未配置";
    $("#sttCapability").textContent = room.stt?.server_available
      ? (room.stt.server_label || "AstrBot STT")
      : (SpeechRecognition ? "浏览器免配置" : "仅文字可用");
    $("#ttsCapability").textContent = room.tts?.server_available
      ? (room.tts.server_label || "可用")
      : (room.tts?.browser_fallback ? "浏览器回退" : "未配置");
    const browserButton = $('[data-stt-mode="browser"]');
    const astrbotButton = $('[data-stt-mode="astrbot"]');
    browserButton.disabled = !SpeechRecognition;
    browserButton.title = SpeechRecognition ? "使用浏览器语音识别" : "当前浏览器不支持 Web Speech 语音识别";
    astrbotButton.disabled = !room.stt?.server_available;
    astrbotButton.title = room.stt?.server_available ? "使用 AstrBot STT Provider" : "AstrBot 尚未配置 STT Provider";
    $("#autoComment").checked = Boolean(room.watch?.auto_comment);
    $("#watchTtsEnabled").checked = room.watch?.tts_enabled !== false;
    $("#watchTtsEnabled").disabled = guest;
    $("#autoComment").disabled = guest;
    $("#callTtsToggle").disabled = guest;
    $("#videoCenterToggle").disabled = guest;
    $("#videoProgress").disabled = guest;
    $$(".video-controls button, .video-controls input").forEach((control) => { control.disabled = guest; });
    ["#openLocalVideo", "#openVideoUrl", "#observeFrame"].forEach((selector) => {
      $(selector).disabled = guest;
    });
    loadTtsVolumePreference();
    loadTalkPreferences();
    updateTalkControls();
    updateCallTtsButton();
    loadRememberedCameraDevice();
    updateCameraButton();
    refreshCameraDevices().catch(() => {});
    setMode(state.mode || room.mode || "call");
    resetSceneMonitor();
  }

  function workAgeLabel(seconds) {
    const age = Math.max(0, Number(seconds) || 0);
    if (age < 15) return "刚刚更新";
    if (age < 60) return `${Math.round(age)} 秒前`;
    if (age < 3600) return `${Math.round(age / 60)} 分钟前`;
    return "上下文较旧";
  }

  function applyWorkContext(context) {
    const value = context && typeof context === "object" ? context : {};
    const current = value.current && typeof value.current === "object" ? value.current : {};
    const observation = value.observation && typeof value.observation === "object" ? value.observation : {};
    state.workContext = value;
    const available = value.context_available === true;
    $("#workApp").textContent = current.app_name || current.resource_label || "未识别";
    $("#workScene").textContent = current.scene || current.type || "未识别";
    $("#workWindow").textContent = current.window || current.resource_label || "未读取";
    $("#workObservation").textContent = observation.summary || "暂无新观察";
    $("#workStatus").textContent = available
      ? `${value.privacy_masked ? "脱敏上下文" : "当前上下文"} · ${workAgeLabel(observation.age_seconds)}`
      : "屏幕伙伴已连接，等待可用上下文";
    $("#workConversationStatus").textContent = value.tracking_enabled ? "上下文同步中" : "按需读取";
    $("#refreshWorkContext").disabled = false;
  }

  function applyWorkState(workState) {
    const value = workState && typeof workState === "object" ? workState : {};
    const criteria = Array.isArray(value.success_criteria) ? value.success_criteria.filter(Boolean) : [];
    const blockers = Array.isArray(value.blockers) ? value.blockers.filter(Boolean) : [];
    const labels = {
      not_started: "等待确认",
      in_progress: "进行中",
      blocked: "受阻",
      completed: "已完成",
    };
    state.workState = value;
    $("#workGoal").textContent = value.goal || "尚未确认";
    $("#workCriteria").textContent = criteria.length ? criteria.join("；") : "尚未确认";
    $("#workProgress").textContent = [labels[value.status] || "等待目标", value.progress || value.current_step || ""]
      .filter(Boolean)
      .join(" · ");
    $("#workNextAction").textContent = value.next_action || "先描述想完成的结果";
    $("#workBlockers").textContent = blockers.length ? blockers.join("；") : "暂无";
  }

  function renderNovelLibrary(books) {
    state.novelBooks = Array.isArray(books) ? books : [];
    const query = String($("#novelSearch")?.value || "").trim().toLowerCase();
    const container = $("#novelBookList");
    if (!container) return;
    container.replaceChildren();
    const visible = state.novelBooks.filter((book) => {
      if (!query) return true;
      return `${book.title || ""} ${book.author || ""}`.toLowerCase().includes(query);
    });
    $("#novelLibraryMeta").textContent = state.novelBooks.length ? `${state.novelBooks.length} 本书` : "书库还是空的";
    if (!visible.length) {
      const empty = document.createElement("span");
      empty.className = "novel-list-empty";
      empty.textContent = state.novelBooks.length ? "没有匹配的小说" : "导入 TXT、Markdown 或 EPUB 开始阅读";
      container.appendChild(empty);
      return;
    }
    visible.forEach((book) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "novel-book-item";
      button.classList.toggle("active", state.novelBook?.id === book.id);
      const icon = document.createElement("i");
      icon.dataset.lucide = "book-marked";
      const text = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = book.title || "未命名小说";
      const meta = document.createElement("small");
      const progress = book.progress || {};
      meta.textContent = progress.updated_at
        ? `读到第 ${Number(progress.chapter_index || 0) + 1} 章 · ${book.author || book.source_type || "小说"}`
        : `${book.chapter_count || 0} 章 · ${book.author || book.source_type || "小说"}`;
      text.append(title, meta);
      button.append(icon, text);
      button.addEventListener("click", () => {
        send({ type: "novel_open", book_id: book.id });
        setNovelLibraryOpen(false);
      });
      container.appendChild(button);
    });
    icons();
  }

  function renderNovelChapter(message) {
    const navigationSequence = Number(message.navigation_sequence || 0);
    if (navigationSequence && navigationSequence < state.novelNavigationSequence) return;
    state.novelNavigationSequence = Math.max(state.novelNavigationSequence, navigationSequence);
    const previousBookId = state.novelBook?.id || "";
    const hasDirectory = Array.isArray(message.chapters);
    state.novelBook = message.book || null;
    if (hasDirectory) state.novelChapters = message.chapters;
    state.novelChapter = message.chapter || null;
    state.novelPosition = message.state || {};
    state.novelProgressSequence = Math.max(
      state.novelProgressSequence,
      Number(state.novelPosition?.progress_sequence || 0),
    );
    state.novelProgressDirty = false;
    state.novelPendingChapterIndex = null;
    window.clearTimeout(state.novelProgressTimer);
    window.cancelAnimationFrame(state.novelProgressFrame);
    state.novelProgressTimer = 0;
    state.novelProgressFrame = 0;
    if (hasDirectory || previousBookId !== state.novelBook?.id) renderNovelLibrary(state.novelBooks);
    $("#novelBookTitle").textContent = state.novelBook?.title || "未命名小说";
    $("#novelChapterTitle").textContent = state.novelChapter?.title || "正文";
    $("#novelChapterCount").textContent = `${state.novelChapters.length} 章`;
    $("#novelStatus").textContent = `第 ${Number(state.novelChapter?.chapter_index || 0) + 1} 章`;
    const chapterIndex = Number(state.novelChapter?.chapter_index || 0);
    const range = state.novelDirectoryRange;
    if (hasDirectory || previousBookId !== state.novelBook?.id || chapterIndex < range.start || chapterIndex >= range.end) {
      renderNovelDirectory(chapterIndex);
    } else {
      updateNovelDirectoryActive(chapterIndex);
    }
    const content = $("#novelContent");
    const fragment = document.createDocumentFragment();
    const heading = document.createElement("h1");
    heading.textContent = state.novelChapter?.title || "正文";
    fragment.appendChild(heading);
    state.novelParagraphElements = (state.novelChapter?.paragraphs || []).map((item) => {
      const paragraph = document.createElement("p");
      paragraph.dataset.paragraphIndex = String(item.paragraph_index || 0);
      paragraph.textContent = item.content || "";
      fragment.appendChild(paragraph);
      return paragraph;
    });
    content.replaceChildren(fragment);
    $("#novelEmpty").hidden = true;
    content.hidden = false;
    $("#novelPrevChapter").disabled = chapterIndex <= 0;
    $("#novelNextChapter").disabled = chapterIndex >= state.novelChapters.length - 1;
    const reader = $("#novelReader");
    state.novelRestoringPosition = true;
    window.requestAnimationFrame(() => {
      const paragraphIndex = Number(state.novelPosition?.paragraph_index || 0);
      const target = state.novelParagraphElements[paragraphIndex];
      const previousBehavior = reader.style.scrollBehavior;
      reader.style.scrollBehavior = "auto";
      reader.scrollTop = target ? Math.max(0, target.offsetTop - 24) : 0;
      updateNovelProgressVisual(paragraphIndex);
      window.requestAnimationFrame(() => {
        reader.style.scrollBehavior = previousBehavior;
        state.novelRestoringPosition = false;
      });
    });
  }

  function renderNovelDirectory(activeIndex, requestedStart = null) {
    const chapterList = $("#novelChapterList");
    const total = state.novelChapters.length;
    const windowSize = 240;
    let start = requestedStart === null
      ? Math.max(0, Number(activeIndex || 0) - Math.floor(windowSize / 2))
      : Math.max(0, Number(requestedStart || 0));
    start = Math.min(start, Math.max(0, total - windowSize));
    const end = Math.min(total, start + windowSize);
    state.novelDirectoryRange = { start, end };
    const fragment = document.createDocumentFragment();
    if (start > 0) {
      const previous = document.createElement("button");
      previous.type = "button";
      previous.className = "novel-chapter-page";
      previous.textContent = `显示前 ${Math.min(windowSize, start)} 章`;
      previous.addEventListener("click", () => renderNovelDirectory(activeIndex, Math.max(0, start - windowSize)));
      fragment.appendChild(previous);
    }
    state.novelChapters.slice(start, end).forEach((chapter) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "novel-chapter-item";
      button.dataset.chapterIndex = String(chapter.chapter_index);
      button.classList.toggle("active", Number(chapter.chapter_index) === Number(activeIndex));
      button.textContent = chapter.title || `第 ${Number(chapter.chapter_index) + 1} 章`;
      button.addEventListener("click", () => requestNovelChapter(Number(chapter.chapter_index)));
      fragment.appendChild(button);
    });
    if (end < total) {
      const next = document.createElement("button");
      next.type = "button";
      next.className = "novel-chapter-page";
      next.textContent = `显示后 ${Math.min(windowSize, total - end)} 章`;
      next.addEventListener("click", () => renderNovelDirectory(activeIndex, end));
      fragment.appendChild(next);
    }
    chapterList.replaceChildren(fragment);
  }

  function updateNovelDirectoryActive(activeIndex) {
    $("#novelChapterList").querySelectorAll("[data-chapter-index]").forEach((button) => {
      button.classList.toggle("active", Number(button.dataset.chapterIndex) === Number(activeIndex));
    });
  }

  function applyNovelState(position) {
    if (!position || typeof position !== "object") return;
    if (position.book_id && position.book_id !== state.novelBook?.id) return;
    if (position.chapter_index !== undefined
        && Number(position.chapter_index) !== Number(state.novelChapter?.chapter_index)) return;
    const sequence = Number(position.progress_sequence || 0);
    if (sequence && sequence < state.novelProgressSequence) return;
    if (state.novelProgressDirty) return;
    state.novelProgressSequence = Math.max(state.novelProgressSequence, sequence);
    state.novelPosition = { ...(state.novelPosition || {}), ...position };
    updateNovelProgressVisual();
  }

  function requestNovelChapter(index) {
    if (!state.novelBook?.id) return;
    const chapterIndex = Math.max(0, Math.min(Number(index) || 0, state.novelChapters.length - 1));
    if (chapterIndex === Number(state.novelChapter?.chapter_index) && state.novelPendingChapterIndex === null) return;
    sendNovelProgressNow();
    state.novelPendingChapterIndex = chapterIndex;
    state.novelNavigationSequence += 1;
    $("#novelStatus").textContent = `正在打开第 ${chapterIndex + 1} 章`;
    send({
      type: "novel_chapter_request",
      book_id: state.novelBook.id,
      chapter_index: chapterIndex,
      navigation_sequence: state.novelNavigationSequence,
    });
  }

  function currentNovelParagraph() {
    const reader = $("#novelReader");
    const paragraphs = state.novelParagraphElements;
    if (!paragraphs.length) return 0;
    const readerTop = reader.scrollTop + 32;
    let low = 0;
    let high = paragraphs.length - 1;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      if (paragraphs[middle].offsetTop <= readerTop) low = middle;
      else high = middle - 1;
    }
    return Number(paragraphs[low].dataset.paragraphIndex || 0);
  }

  function updateNovelProgressVisual(paragraphOverride = null) {
    const paragraphs = state.novelChapter?.paragraphs || [];
    const storedParagraph = state.novelPosition?.paragraph_index;
    const paragraph = Math.max(0, Number(
      paragraphOverride !== null
        ? paragraphOverride
        : storedParagraph !== undefined && storedParagraph !== null
          ? storedParagraph
          : currentNovelParagraph(),
    ) || 0);
    const chapterIndex = Math.max(0, Number(state.novelChapter?.chapter_index || 0));
    const chapterCount = Math.max(1, state.novelChapters.length);
    const inChapter = paragraphs.length ? paragraph / Math.max(1, paragraphs.length - 1) : 0;
    const percent = Math.max(0, Math.min(100, ((chapterIndex + inChapter) / chapterCount) * 100));
    $("#novelProgressBar").style.width = `${percent}%`;
    $("#novelStatus").textContent = `第 ${chapterIndex + 1} 章 · ${Math.round(percent)}%`;
  }

  function scheduleNovelProgressSave() {
    if (!state.novelBook?.id || !state.novelChapter || state.novelRestoringPosition) return;
    if (state.novelProgressFrame) return;
    state.novelProgressFrame = window.requestAnimationFrame(() => {
      state.novelProgressFrame = 0;
      if (state.novelRestoringPosition) return;
      const paragraphIndex = currentNovelParagraph();
      state.novelPosition = { ...(state.novelPosition || {}), paragraph_index: paragraphIndex };
      state.novelProgressDirty = true;
      updateNovelProgressVisual(paragraphIndex);
      window.clearTimeout(state.novelProgressTimer);
      state.novelProgressTimer = window.setTimeout(sendNovelProgressNow, 900);
    });
  }

  function sendNovelProgressNow() {
    window.clearTimeout(state.novelProgressTimer);
    state.novelProgressTimer = 0;
    if (!state.novelBook?.id || !state.novelChapter || state.novelRestoringPosition) return;
    const paragraphIndex = currentNovelParagraph();
    state.novelProgressSequence += 1;
    state.novelProgressDirty = false;
    state.novelPosition = {
      ...(state.novelPosition || {}),
      paragraph_index: paragraphIndex,
      progress_sequence: state.novelProgressSequence,
    };
    send({
      type: "novel_progress",
      book_id: state.novelBook.id,
      chapter_index: Number(state.novelChapter.chapter_index || 0),
      paragraph_index: paragraphIndex,
      progress_sequence: state.novelProgressSequence,
    });
  }

  async function importNovelFile(file) {
    if (!file || state.novelImporting || !state.room?.id || !state.resumeToken) return;
    const maxMb = Math.max(1, Number(state.room?.novel?.max_upload_mb) || 40);
    if (file.size > maxMb * 1024 * 1024) {
      showToast(`小说文件不能超过 ${maxMb} MB`, 4200);
      return;
    }
    state.novelImporting = true;
    $("#importNovel").disabled = true;
    $("#novelStatus").textContent = "正在导入";
    const body = new FormData();
    body.append("file", file, file.name);
    try {
      const response = await fetch("/api/novels/import", {
        method: "POST",
        headers: {
          "X-Together-Room": state.room.id,
          "X-Together-Resume": state.resumeToken,
        },
        body,
      });
      const data = await response.json().catch(() => ({ ok: false, message: "导入服务返回了无效响应" }));
      if (!response.ok || !data.ok) throw new Error(data.message || "小说导入失败");
      showToast(data.book?.duplicate ? "这本书已经在书库中" : "小说已导入");
      if (data.book?.id) send({ type: "novel_open", book_id: data.book.id });
    } catch (error) {
      showToast(error?.message || "小说导入失败", 5000);
    } finally {
      state.novelImporting = false;
      $("#importNovel").disabled = false;
      $("#novelFileInput").value = "";
    }
  }

  function setNovelLibraryOpen(open) {
    $("#novelLibraryPanel").classList.toggle("mobile-open", Boolean(open));
    $("#novelLibraryBackdrop").hidden = !open;
  }

  function updateNovelSelection() {
    if (state.mode !== "novel") return;
    const selection = window.getSelection();
    const text = String(selection?.toString() || "").trim().slice(0, 2400);
    const insideReader = selection?.anchorNode && $("#novelContent").contains(selection.anchorNode);
    state.novelSelectedText = insideReader ? text : "";
    $("#askNovelSelection").hidden = !state.novelSelectedText;
  }

  async function sendNovelQuestion(text, selectedText = "") {
    const sent = await sendUserText(text, "text", [], selectedText || state.novelSelectedText);
    if (sent) {
      state.novelSelectedText = "";
      $("#askNovelSelection").hidden = true;
      window.getSelection()?.removeAllRanges();
    }
  }

  function requestInviteLink() {
    if (!state.connected || state.inviteRequestPending) return;
    state.inviteRequestPending = true;
    $("#inviteButton").disabled = true;
    if (!send({ type: "create_invite" })) {
      state.inviteRequestPending = false;
      $("#inviteButton").disabled = false;
    }
  }

  async function copyInviteLink() {
    const input = $("#inviteUrl");
    const value = input.value.trim();
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      input.focus();
      input.select();
      if (!document.execCommand("copy")) {
        showToast("复制失败，请手动选择链接");
        return;
      }
    }
    showToast("邀请链接已复制");
  }

  function setMode(mode, notify = true) {
    let next = ["call", "watch", "work", "novel"].includes(mode) ? mode : "call";
    if (next === "work" && state.room?.work?.available !== true) {
      if (notify) showToast("工作协同当前不可用");
      next = "call";
    }
    const previous = state.mode;
    if (previous !== next) clearCancellableUtterances();
    if (previous !== next && next !== "call" && state.cameraEnabled) stopCamera(false);
    if (previous !== next && previous === "watch") $("#watchVideo").pause();
    if (previous !== next && state.botSpeaking) stopAudio();
    state.mode = next;
    document.body.dataset.mode = next;
    $$("[data-mode-tab]").forEach((button) => button.classList.toggle("active", button.dataset.modeTab === next));
    $("#callView").classList.toggle("active", next === "call");
    $("#watchView").classList.toggle("active", next === "watch");
    $("#workView").classList.toggle("active", next === "work");
    $("#novelView").classList.toggle("active", next === "novel");
    $(".camera-device-field").hidden = next === "work" || next === "novel";
    if (notify) send({ type: "set_mode", mode: next });
    if (next === "work" && notify) send({ type: "refresh_work_context" });
    if (next === "novel" && notify) send({ type: "novel_library_request" });
    updatePlayerState();
  }

  function updateCallButtons() {
    const connected = state.callActive;
    const callButton = $("#callToggle");
    callButton.classList.toggle("active", connected);
    callButton.title = connected ? "挂断" : "开始通话";
    callButton.setAttribute("aria-label", callButton.title);
    callButton.innerHTML = `<i data-lucide="${connected ? "phone-off" : "phone"}"></i>`;

    const watchButton = $("#watchCallToggle");
    watchButton.classList.toggle("active", connected);
    watchButton.title = connected ? "挂断语音" : "接通语音";
    watchButton.setAttribute("aria-label", watchButton.title);
    watchButton.innerHTML = `<i data-lucide="${connected ? "phone-off" : "phone"}"></i>`;

    const workButton = $("#workCallToggle");
    workButton.classList.toggle("active", connected);
    workButton.title = connected ? "结束语音协同" : "开始语音协同";
    workButton.setAttribute("aria-label", workButton.title);
    workButton.innerHTML = `<i data-lucide="${connected ? "phone-off" : "phone"}"></i>`;
    workButton.disabled = state.mode !== "work";
    updateCallTtsButton();
  }

  function updateCallTtsButton() {
    const button = $("#callTtsToggle");
    if (!button) return;
    const enabled = state.callTtsEnabled !== false;
    button.disabled = !state.callActive;
    button.classList.toggle("muted", !enabled);
    button.title = enabled ? "关闭语音" : "开启语音";
    button.setAttribute("aria-label", button.title);
    button.setAttribute("aria-pressed", String(!enabled));
    button.innerHTML = `<i data-lucide="${enabled ? "volume-2" : "volume-x"}"></i>`;
    icons();
  }

  function transcriptNodes() {
    return [$("#callTranscript"), $("#watchTranscript"), $("#workTranscript"), $("#novelTranscript")];
  }

  function sanitizeBotDisplayText(value) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    const spoken = [];
    const withoutBlocks = raw.replace(
      /<(?:pc[_-]?tts|t{2,}s)\b[^>]*>([\s\S]*?)<\/(?:pc[_-]?tts|t{2,}s)\s*>/gi,
      (_match, voice) => {
        if (String(voice || "").trim()) spoken.push(String(voice).trim());
        return "";
      },
    );
    const visible = withoutBlocks
      .replace(/<\/?(?:pc[_-]?tts|t{2,}s)\b[^>]*>/gi, "")
      .replace(/\s+/g, " ")
      .trim();
    if (visible) return visible;
    return spoken.join(" ")
      .replace(/\[(?:[a-z][a-z0-9 _-]{0,30})\]/gi, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function addMessage(role, text, label = "", { utteranceId = "" } = {}) {
    const content = role === "bot"
      ? sanitizeBotDisplayText(text)
      : String(text || "").trim();
    if (!content) return;
    const cancellableItems = [];
    transcriptNodes().forEach((container) => {
      const item = document.createElement("div");
      item.className = `message ${role}`;
      if (label) {
        const small = document.createElement("small");
        small.textContent = label;
        item.appendChild(small);
      }
      const span = document.createElement("span");
      span.textContent = content;
      item.appendChild(span);
      if (utteranceId) {
        item.classList.add("cancellable-utterance");
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "utterance-cancel";
        cancel.title = "排除这条识别结果";
        cancel.setAttribute("aria-label", cancel.title);
        cancel.innerHTML = '<i data-lucide="x"></i>';
        cancel.addEventListener("click", () => excludeUtterance(utteranceId));
        item.appendChild(cancel);
        cancellableItems.push(item);
      }
      container.appendChild(item);
      while (container.children.length > 40) container.firstElementChild?.remove();
      container.scrollTop = container.scrollHeight;
    });
    if (utteranceId) {
      state.cancellableUtterances.set(utteranceId, cancellableItems);
      icons();
    }
  }

  function removeCancellableUtterance(utteranceId) {
    const items = state.cancellableUtterances.get(utteranceId) || [];
    items.forEach((item) => item.remove());
    state.cancellableUtterances.delete(utteranceId);
  }

  function clearCancellableUtterances() {
    state.cancellableUtterances.forEach((items) => {
      items.forEach((item) => {
        item.classList.remove("cancellable-utterance");
        item.querySelector(".utterance-cancel")?.remove();
      });
    });
    state.cancellableUtterances.clear();
  }

  function excludeUtterance(utteranceId) {
    if (!utteranceId) return;
    if (!send({ type: "exclude_utterance", id: utteranceId })) return;
    removeCancellableUtterance(utteranceId);
    stopAudio();
  }

  function appendDraft(text) {
    if (!state.draftNode) {
      state.draftNode = [];
      transcriptNodes().forEach((container) => {
        const item = document.createElement("div");
        item.className = "message bot draft";
        item.textContent = "";
        container.appendChild(item);
        state.draftNode.push(item);
      });
    }
    state.draftNode.forEach((node) => {
      node.textContent += String(text || "");
      node.parentElement.scrollTop = node.parentElement.scrollHeight;
    });
  }

  function clearDraft() {
    if (!state.draftNode) return;
    state.draftNode.forEach((node) => node.remove());
    state.draftNode = null;
  }

  function resolvedSttMode() {
    if (state.sttMode === "browser") {
      return SpeechRecognition && !state.browserRecognitionUnavailable ? "browser" : "none";
    }
    if (state.sttMode === "astrbot") return "astrbot";
    if (SpeechRecognition && !state.browserRecognitionUnavailable) return "browser";
    return state.room?.stt?.server_available ? "astrbot" : "none";
  }

  function setActiveSttButton(mode) {
    $$("[data-stt-mode]").forEach((button) => button.classList.toggle("active", button.dataset.sttMode === mode));
  }

  function talkKeyName(code) {
    const names = {
      Space: "空格",
      Enter: "回车",
      Tab: "Tab",
      Backquote: "`",
      ShiftLeft: "左 Shift",
      ShiftRight: "右 Shift",
      ControlLeft: "左 Ctrl",
      ControlRight: "右 Ctrl",
      AltLeft: "左 Alt",
      AltRight: "右 Alt",
      CapsLock: "Caps Lock",
    };
    if (names[code]) return names[code];
    if (/^Key[A-Z]$/.test(code)) return code.slice(3);
    if (/^Digit\d$/.test(code)) return code.slice(5);
    if (/^Numpad\d$/.test(code)) return `小键盘 ${code.slice(6)}`;
    return String(code || "Space").replace(/([a-z])([A-Z])/g, "$1 $2");
  }

  function loadTalkPreferences() {
    try {
      const mode = localStorage.getItem("together_talk_mode");
      const keyCode = localStorage.getItem("together_talk_key");
      state.talkMode = mode === "push" ? "push" : "free";
      state.talkKeyCode = keyCode || "Space";
    } catch {
      state.talkMode = "free";
      state.talkKeyCode = "Space";
    }
  }

  function normalizeTtsVolumePercent(value, fallback = 100) {
    const parsed = Number(value);
    const normalized = Number.isFinite(parsed) ? parsed : Number(fallback);
    return Math.max(0, Math.min(100, Math.round(Number.isFinite(normalized) ? normalized : 100)));
  }

  function setTtsVolume(value, { persist = false } = {}) {
    const percent = normalizeTtsVolumePercent(value);
    state.ttsVolume = percent / 100;
    $("#ttsVolume").value = String(percent);
    $("#ttsVolumeValue").textContent = `${percent}%`;
    if (state.currentAudio) state.currentAudio.volume = state.ttsVolume;
    if (state.browserUtterance) state.browserUtterance.volume = state.ttsVolume;
    if (!persist) return;
    try { localStorage.setItem(TTS_VOLUME_STORAGE_KEY, String(percent)); }
    catch { /* 浏览器可能禁用本地存储 */ }
  }

  function loadTtsVolumePreference() {
    const configured = normalizeTtsVolumePercent(Number(state.room?.tts?.volume_ratio) * 100);
    let preferred = configured;
    try {
      const stored = localStorage.getItem(TTS_VOLUME_STORAGE_KEY);
      if (stored !== null && stored.trim() !== "" && Number.isFinite(Number(stored))) preferred = stored;
    } catch { /* 浏览器可能禁用本地存储 */ }
    setTtsVolume(preferred);
  }

  function saveTalkPreferences() {
    try {
      localStorage.setItem("together_talk_mode", state.talkMode);
      localStorage.setItem("together_talk_key", state.talkKeyCode);
    } catch { /* 浏览器可能禁用本地存储 */ }
  }

  function setTalkKey(code) {
    if (!code) return;
    state.talkKeyCode = code;
    state.talkKeyCapturing = false;
    saveTalkPreferences();
    updateTalkControls();
    if (state.callActive && state.talkMode === "push") {
      setRoomStatus("listening", `等待你按住${talkKeyName(state.talkKeyCode)}`);
    }
    showToast(`讲话按键已设为${talkKeyName(state.talkKeyCode)}`);
  }

  function isTalkKeyBlockedByTarget(target) {
    const tag = String(target?.tagName || "").toUpperCase();
    return Boolean(target?.isContentEditable || ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(tag));
  }

  function updateTalkControls() {
    const pushMode = state.talkMode === "push";
    const sttAvailable = resolvedSttMode() !== "none";
    $$("[data-talk-mode]").forEach((button) => button.classList.toggle("active", button.dataset.talkMode === state.talkMode));
    $("#talkKeySetting").hidden = !pushMode;
    $("#talkKeyLabel").textContent = state.talkKeyCapturing ? "请按键" : talkKeyName(state.talkKeyCode);
    $("#talkKeyCapture").classList.toggle("capturing", state.talkKeyCapturing);
    const showHold = state.callActive && pushMode && sttAvailable;
    $("#holdToTalk").hidden = !showHold;
    $("#holdHint").hidden = !showHold;
    const keyName = talkKeyName(state.talkKeyCode);
    $("#holdToTalk").title = `按住${keyName}说话`;
    $("#holdToTalk").setAttribute("aria-label", `按住${keyName}说话`);
    $("#holdHint").textContent = `按住${keyName}或麦克风说话`;
  }

  async function setTalkMode(mode, notify = true) {
    const next = mode === "push" ? "push" : "free";
    if (state.talkMode === next) {
      updateTalkControls();
      return;
    }
    state.talkMode = next;
    state.talkKeyCapturing = false;
    state.pushToTalkHeld = false;
    saveTalkPreferences();
    window.clearTimeout(state.recognitionRestartTimer);
    stopRecording(true);
    stopVoiceActivityDetection();
    if (state.recognitionRunning && state.recognition) {
      try { state.recognition.abort(); } catch { /* noop */ }
    }
    updateTalkControls();
    if (!state.callActive) return;
    const sttMode = resolvedSttMode();
    if (next === "free") {
      if (sttMode === "browser") startRecognition();
      else if (sttMode === "astrbot") await startVoiceActivityDetection();
      setRoomStatus("listening", "正在听");
    } else {
      setRoomStatus("listening", `等待你按住${talkKeyName(state.talkKeyCode)}`);
    }
    if (notify) showToast(next === "free" ? "已切换为自由讲话" : "已切换为按键讲话");
  }

  async function startCall() {
    if (!state.connected || state.callActive) return;
    const mode = resolvedSttMode();
    if (mode === "astrbot" && !state.room?.stt?.server_available) {
      showToast("AstrBot STT 尚未配置，请切换浏览器识别");
      return;
    }
    try {
      if (mode === "browser") {
        createRecognition();
      } else if (mode === "astrbot") {
        state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      }
    } catch (error) {
      showToast(error?.message || "无法获得麦克风权限", 4200);
      return;
    }
    state.callActive = true;
    document.body.classList.add("call-connected");
    requestWakeLock();
    send({ type: "call_state", active: true });
    updateCallButtons();
    updateCameraButton();
    updateTalkControls();
    icons();
    const pushMode = state.talkMode === "push";
    setRoomStatus(
      "listening",
      mode === "none" ? "文字通话已接通" : (pushMode ? `等待你按住${talkKeyName(state.talkKeyCode)}` : "正在听"),
    );
    if (mode === "browser" && !pushMode) startRecognition();
    if (mode === "astrbot" && !pushMode) await startVoiceActivityDetection();
    if (mode === "none") showToast("语音识别不可用，仍可使用文字和摄像头通话", 4200);
    scheduleCallIdleTimer();
  }

  function stopCall(notify = true) {
    const wasActive = state.callActive;
    state.callActive = false;
    releaseWakeLock();
    clearCancellableUtterances();
    document.body.classList.remove("call-connected");
    clearCallIdleTimer();
    window.clearTimeout(state.recognitionRestartTimer);
    if (state.recognition) {
      try { state.recognition.abort(); } catch { /* noop */ }
    }
    state.recognition = null;
    state.recognitionRunning = false;
    state.pushToTalkHeld = false;
    stopVoiceActivityDetection();
    stopRecording(true);
    if (state.mediaStream) state.mediaStream.getTracks().forEach((track) => track.stop());
    state.mediaStream = null;
    stopCamera(false);
    updateCallButtons();
    updateTalkControls();
    updateCameraButton();
    if (state.mode === "watch") {
      const video = $("#watchVideo");
      setRoomStatus("watching", video.ended ? "已经看完" : (video.paused ? "已经暂停" : "一起看着"));
    } else if (state.mode === "work") {
      setRoomStatus("idle", "文字协同已就绪");
    } else {
      setRoomStatus("idle", "等待接通");
    }
    if (wasActive && state.connected) send({ type: "call_state", active: false });
    if (notify) send({ type: "interrupt" });
    icons();
  }

  function cameraErrorMessage(error) {
    if (!window.isSecureContext) return "摄像头需要 HTTPS 或本机安全环境";
    if (!navigator.mediaDevices?.getUserMedia) return "当前浏览器不支持摄像头访问，请使用系统 Chrome、Edge 或 Safari";
    if (error?.name === "NotAllowedError") return "摄像头权限被拒绝，请在浏览器地址栏中重新允许";
    if (error?.name === "NotFoundError") return "没有检测到可用摄像头";
    if (error?.name === "NotReadableError") return "摄像头正被其他应用占用";
    if (["OverconstrainedError", "ConstraintNotSatisfiedError"].includes(error?.name)) return "当前摄像头不支持所请求的画面规格";
    if (error?.name === "SecurityError") return "当前页面没有摄像头访问权限，请改用系统浏览器打开";
    return error?.message || "无法开启摄像头";
  }

  function cameraVisionAvailable() {
    return Boolean(state.room?.call?.camera_vision_available);
  }

  function rememberCameraDevice(deviceId) {
    state.selectedCameraId = String(deviceId || "");
    try {
      if (state.selectedCameraId) localStorage.setItem("together_camera_device", state.selectedCameraId);
      else localStorage.removeItem("together_camera_device");
    } catch { /* 浏览器可能禁用本地存储 */ }
  }

  function loadRememberedCameraDevice() {
    if (state.selectedCameraId) return;
    try { state.selectedCameraId = localStorage.getItem("together_camera_device") || ""; }
    catch { state.selectedCameraId = ""; }
  }

  function cameraDeviceName(device, index = 0) {
    return String(device?.label || "").trim() || `摄像头 ${index + 1}`;
  }

  async function refreshCameraDevices(currentDeviceId = "") {
    const select = $("#cameraDeviceSelect");
    const hint = $("#cameraDeviceHint");
    if (!window.isSecureContext) {
      state.cameraDevices = [];
      select.disabled = true;
      hint.textContent = "摄像头需要 HTTPS 或本机安全环境";
      return [];
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      state.cameraDevices = [];
      select.disabled = true;
      hint.textContent = "当前浏览器不支持摄像头访问";
      return [];
    }
    if (!navigator.mediaDevices?.enumerateDevices) {
      state.cameraDevices = [];
      select.disabled = true;
      hint.textContent = cameraVisionAvailable()
        ? "可自动使用摄像头，当前浏览器不支持设备列表"
        : "可自动使用摄像头；未配置视觉模型，仅本机预览";
      return [];
    }
    try {
      const detectedDevices = (await navigator.mediaDevices.enumerateDevices())
        .filter((item) => item.kind === "videoinput");
      // Safari 和部分移动浏览器会在首次授权前隐藏 deviceId，不能据此判定无摄像头。
      const devices = detectedDevices.filter((item) => item.deviceId);
      state.cameraDevices = devices;
      if (state.selectedCameraId && !devices.some((item) => item.deviceId === state.selectedCameraId)) {
        rememberCameraDevice("");
      }
      const options = [new Option("自动选择", "")];
      devices.forEach((device, index) => options.push(new Option(cameraDeviceName(device, index), device.deviceId)));
      select.replaceChildren(...options);
      select.value = devices.some((item) => item.deviceId === state.selectedCameraId)
        ? state.selectedCameraId
        : "";
      select.disabled = devices.length === 0;
      const labelsVisible = devices.some((item) => String(item.label || "").trim());
      const currentIndex = devices.findIndex((item) => item.deviceId === currentDeviceId);
      const currentDevice = currentIndex >= 0 ? devices[currentIndex] : null;
      const currentSettings = state.cameraStream?.getVideoTracks?.()[0]?.getSettings?.() || {};
      const currentWidth = Number(currentSettings.width) || 0;
      const currentHeight = Number(currentSettings.height) || 0;
      const resolutionLabel = currentWidth && currentHeight ? ` · ${currentWidth}×${currentHeight}` : "";
      const visionSuffix = cameraVisionAvailable() ? "" : "；未配置视觉模型，仅本机预览";
      hint.textContent = detectedDevices.length === 0
        ? `尚未获得摄像头信息，接通后点击镜头按钮检测${visionSuffix}`
        : (currentDevice
          ? `当前使用：${cameraDeviceName(currentDevice, currentIndex)}${resolutionLabel}${visionSuffix}`
          : (labelsVisible
            ? `已检测到 ${detectedDevices.length} 台摄像头${visionSuffix}`
            : `检测到摄像头，首次开启授权后会显示设备名称${visionSuffix}`));
      $("#switchCamera").hidden = !state.cameraEnabled || devices.length < 2;
      return devices;
    } catch {
      state.cameraDevices = [];
      select.disabled = true;
      hint.textContent = "暂时无法读取摄像头列表";
      return [];
    }
  }

  function updateCameraButton() {
    const button = $("#cameraToggle");
    button.disabled = !state.callActive;
    button.classList.toggle("camera-active", state.cameraEnabled);
    button.title = !window.isSecureContext
      ? "摄像头需要 HTTPS 或本机安全环境"
      : (!navigator.mediaDevices?.getUserMedia
        ? "当前浏览器不支持摄像头访问"
        : (state.cameraEnabled
          ? "关闭摄像头"
          : (cameraVisionAvailable() ? "开启摄像头" : "开启摄像头（仅本机预览）")));
    button.setAttribute("aria-label", button.title);
    button.innerHTML = `<i data-lucide="${state.cameraEnabled ? "video-off" : "video"}"></i>`;
    icons();
  }

  function cameraLooksRear(value) {
    const text = String(value || "").toLowerCase();
    return /environment|rear|back|后置|后摄|背面/.test(text);
  }

  async function replaceCameraStream(
    videoConstraints = { facingMode: { ideal: "user" } },
    { releaseCurrent = false, mirror = true } = {},
  ) {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("当前环境不支持摄像头访问");
    const video = $("#callCamera");
    const previous = state.cameraStream;
    if (releaseCurrent && previous) {
      previous.getTracks().forEach((track) => track.stop());
      state.cameraStream = null;
      video.srcObject = null;
      // 部分手机在 track.stop() 后仍需一个短暂事件循环才能释放摄像头硬件。
      await new Promise((resolve) => window.setTimeout(resolve, 180));
    }
    const requestedVideo = videoConstraints === true
      ? true
      : {
        width: { ideal: CAMERA_PREVIEW_WIDTH },
        height: { ideal: CAMERA_PREVIEW_HEIGHT },
        frameRate: { ideal: CAMERA_PREVIEW_FRAME_RATE, max: CAMERA_PREVIEW_FRAME_RATE },
        ...videoConstraints,
      };
    const nextStream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: requestedVideo,
    });
    try {
      video.srcObject = nextStream;
      await video.play();
    } catch (error) {
      nextStream.getTracks().forEach((track) => track.stop());
      video.srcObject = null;
      throw error;
    }
    state.cameraStream = nextStream;
    state.cameraEnabled = true;
    document.body.classList.add("call-camera-on");
    if (!releaseCurrent) previous?.getTracks().forEach((track) => track.stop());
    const settings = nextStream.getVideoTracks()[0]?.getSettings?.() || {};
    $("#cameraStage").classList.toggle(
      "rear-camera",
      !mirror || cameraLooksRear(settings.facingMode),
    );
    $("#avatarStage").hidden = true;
    $("#cameraStage").hidden = false;
    await refreshCameraDevices(settings.deviceId || "");
    updateCameraButton();
  }

  function cameraFrameInterval() {
    const connection = navigator.connection || navigator.mozConnection || {};
    if (connection.saveData) return 20000;
    if (/^(slow-2g|2g|3g)$/.test(String(connection.effectiveType || ""))) return 20000;
    if (state.botSpeaking) return 15000;
    if (state.voiceActivityLastHeardAt && Date.now() - state.voiceActivityLastHeardAt > 20000) return 15000;
    return 8000;
  }

  function scheduleCameraFrame(delay = cameraFrameInterval()) {
    window.clearTimeout(state.cameraFrameTimer);
    state.cameraFrameTimer = 0;
    if (!state.cameraEnabled || !state.callActive || document.visibilityState === "hidden") return;
    state.cameraFrameTimer = window.setTimeout(async () => {
      await sendCameraFrame();
      scheduleCameraFrame();
    }, delay);
  }

  async function startCamera({ silent = false } = {}) {
    if (!state.callActive || state.cameraEnabled) return;
    try {
      loadRememberedCameraDevice();
      let openedPreferred = false;
      if (state.selectedCameraId) {
        const devices = await refreshCameraDevices();
        const preferred = devices.find((item) => item.deviceId === state.selectedCameraId);
        if (preferred) {
          try {
            await replaceCameraStream(
              { deviceId: { exact: preferred.deviceId } },
              { mirror: !cameraLooksRear(preferred.label) },
            );
            openedPreferred = true;
          } catch (error) {
            if (!["OverconstrainedError", "ConstraintNotSatisfiedError", "NotFoundError", "NotReadableError"].includes(error?.name)) {
              throw error;
            }
            rememberCameraDevice("");
          }
        }
      }
      if (!openedPreferred) {
        try {
          await replaceCameraStream({ facingMode: { exact: "user" } }, { mirror: true });
        } catch (error) {
          if (!["OverconstrainedError", "ConstraintNotSatisfiedError", "NotFoundError"].includes(error?.name)) {
            throw error;
          }
          try {
            await replaceCameraStream({ facingMode: { ideal: "user" } }, { mirror: true });
          } catch (fallbackError) {
            if (!["OverconstrainedError", "ConstraintNotSatisfiedError", "NotFoundError", "TypeError"].includes(fallbackError?.name)) {
              throw fallbackError;
            }
            await replaceCameraStream(true, { mirror: true });
          }
        }
      }
      window.clearTimeout(state.cameraFrameTimer);
      state.cameraFrameTimer = 0;
      scheduleCameraFrame(600);
      if (!silent) {
        showToast(cameraVisionAvailable() ? "摄像头已开启" : "摄像头已开启；未配置视觉模型，当前仅本机预览", 4200);
      }
      return true;
    } catch (error) {
      showToast(cameraErrorMessage(error), 4800);
      stopCamera(false);
      return false;
    }
  }

  function stopCamera(notify = true) {
    const wasEnabled = state.cameraEnabled;
    window.clearTimeout(state.cameraFrameTimer);
    state.cameraFrameTimer = 0;
    state.cameraStream?.getTracks().forEach((track) => track.stop());
    state.cameraStream = null;
    state.cameraEnabled = false;
    document.body.classList.remove("call-camera-on");
    const video = $("#callCamera");
    if (video) video.srcObject = null;
    $("#cameraStage").hidden = true;
    $("#avatarStage").hidden = false;
    $("#switchCamera").hidden = true;
    updateCameraButton();
    if (wasEnabled && state.connected) send({ type: "call_frame", active: false });
    if (wasEnabled && notify) showToast("摄像头已关闭");
  }

  async function toggleCamera() {
    if (!state.callActive) { showToast("请先接通通话"); return; }
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      showToast(cameraErrorMessage(), 4800);
      return;
    }
    if (state.cameraEnabled) stopCamera();
    else await startCamera();
  }

  async function switchToCameraDevice(device) {
    if (!state.cameraEnabled || !device?.deviceId) return false;
    const button = $("#switchCamera");
    const select = $("#cameraDeviceSelect");
    button.disabled = true;
    select.disabled = true;
    const currentStream = state.cameraStream;
    const currentTrack = currentStream?.getVideoTracks()[0];
    const currentSettings = currentTrack?.getSettings?.() || {};
    const currentId = currentSettings.deviceId || "";
    const currentMirror = !$("#cameraStage").classList.contains("rear-camera");
    const previousPreference = state.selectedCameraId;
    if (device.deviceId === currentId) {
      rememberCameraDevice(device.deviceId);
      await refreshCameraDevices(currentId);
      button.disabled = false;
      return true;
    }
    let restored = false;
    try {
      showToast("正在切换摄像头");
      await replaceCameraStream(
        { deviceId: { exact: device.deviceId } },
        { releaseCurrent: true, mirror: !cameraLooksRear(device.label) },
      );
      rememberCameraDevice(device.deviceId);
      await refreshCameraDevices(device.deviceId);
      await sendCameraFrame();
      const index = state.cameraDevices.findIndex((item) => item.deviceId === device.deviceId);
      showToast(`已切换到${cameraDeviceName(device, Math.max(0, index))}`);
      return true;
    } catch (error) {
      const restoreConstraints = currentId
        ? { deviceId: { exact: currentId } }
        : { facingMode: { ideal: currentSettings.facingMode || "user" } };
      if (state.cameraStream === currentStream && currentTrack?.readyState !== "ended") {
        restored = true;
      } else {
        try {
          await replaceCameraStream(
            restoreConstraints,
            { releaseCurrent: Boolean(state.cameraStream), mirror: currentMirror },
          );
          restored = true;
        } catch {
          stopCamera(false);
        }
      }
      rememberCameraDevice(previousPreference);
      await refreshCameraDevices(restored ? currentId : "");
      showToast(
        restored ? `切换失败，已恢复原摄像头：${cameraErrorMessage(error)}` : `切换失败，请重新开启摄像头：${cameraErrorMessage(error)}`,
        5200,
      );
      return false;
    } finally {
      button.disabled = !state.cameraEnabled;
      select.disabled = state.cameraDevices.length === 0;
    }
  }

  async function switchCamera() {
    if (!state.cameraEnabled || !navigator.mediaDevices?.enumerateDevices) return;
    const devices = await refreshCameraDevices(
      state.cameraStream?.getVideoTracks?.()[0]?.getSettings?.()?.deviceId || "",
    );
    const currentId = state.cameraStream?.getVideoTracks?.()[0]?.getSettings?.()?.deviceId || "";
    const currentIndex = Math.max(0, devices.findIndex((item) => item.deviceId === currentId));
    const next = devices[(currentIndex + 1) % devices.length];
    if (!next || next.deviceId === currentId) {
      showToast("没有其他可切换的摄像头");
      return;
    }
    await switchToCameraDevice(next);
  }

  async function selectCameraDevice(deviceId) {
    const selectedId = String(deviceId || "");
    if (!state.cameraEnabled) {
      rememberCameraDevice(selectedId);
      await refreshCameraDevices();
      showToast(selectedId ? "下次开启镜头时使用所选摄像头" : "已设为自动选择摄像头");
      return;
    }
    if (!selectedId) {
      rememberCameraDevice("");
      stopCamera(false);
      if (await startCamera({ silent: true })) showToast("已切换为自动选择摄像头");
      return;
    }
    const devices = state.cameraDevices.length ? state.cameraDevices : await refreshCameraDevices();
    const device = devices.find((item) => item.deviceId === selectedId);
    if (!device) {
      showToast("所选摄像头已经不可用");
      await refreshCameraDevices();
      return;
    }
    await switchToCameraDevice(device);
  }

  function createRecognition() {
    if (!SpeechRecognition) throw new Error("当前浏览器不支持免配置语音识别，请使用 Edge 或 Chrome");
    const recognition = new SpeechRecognition();
    recognition.lang = state.room?.stt?.browser_language || "zh-CN";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 3;
    recognition.addEventListener("start", () => {
      state.recognitionRunning = true;
      state.recognitionFailCount = 0;
      if (!state.botSpeaking) setRoomStatus("listening", "正在听");
    });
    recognition.addEventListener("result", (event) => {
      let interim = "";
      let finalText = "";
      const finalAlternatives = ["", "", ""];
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        const text = result?.[0]?.transcript || "";
        if (result.isFinal) {
          finalText += text;
          for (let alternativeIndex = 0; alternativeIndex < Math.min(3, result.length); alternativeIndex += 1) {
            finalAlternatives[alternativeIndex] += result[alternativeIndex]?.transcript || "";
          }
        }
        else interim += text;
      }
      if (interim) setRoomStatus("listening", interim.slice(0, 80));
      if (interim || finalText.trim()) noteCallActivity();
      if (finalText.trim()) {
        const alternatives = finalAlternatives.map((item) => item.trim()).filter(Boolean);
        sendUserText(finalText.trim(), "browser_stt", alternatives);
      }
    });
    recognition.addEventListener("error", (event) => {
      if (["not-allowed", "service-not-allowed"].includes(event.error)) {
        fallbackFromBrowserRecognition(event.error);
        return;
      }
      if (!["no-speech", "aborted"].includes(event.error)) {
        state.recognitionFailCount = Math.min(state.recognitionFailCount + 1, 5);
        // 持续失败时退避重启，toast 只报前两次避免刷屏
        if (state.recognitionFailCount <= 2) showToast(`浏览器语音识别：${event.error}`);
      }
    });
    recognition.addEventListener("end", () => {
      state.recognitionRunning = false;
      scheduleRecognitionRestart();
    });
    state.recognition = recognition;
  }

  async function fallbackFromBrowserRecognition(reason = "not-allowed") {
    if (!state.callActive || state.sttFallbackPending) return;
    state.sttFallbackPending = true;
    state.browserRecognitionUnavailable = true;
    window.clearTimeout(state.recognitionRestartTimer);
    const recognition = state.recognition;
    state.recognition = null;
    state.recognitionRunning = false;
    state.sttMode = state.room?.stt?.server_available ? "astrbot" : "auto";
    setActiveSttButton(state.sttMode);
    if (recognition) {
      try { recognition.abort(); } catch { /* noop */ }
    }
    if (state.room?.stt?.server_available) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (!state.callActive) {
          stream.getTracks().forEach((track) => track.stop());
          state.sttFallbackPending = false;
          return;
        }
        state.mediaStream = stream;
        updateTalkControls();
        if (state.talkMode === "free") await startVoiceActivityDetection();
        setRoomStatus(
          "listening",
          state.talkMode === "push" ? `等待你按住${talkKeyName(state.talkKeyCode)}` : "正在听",
        );
        showToast(
          state.talkMode === "push"
            ? "浏览器语音识别不可用，已切换到 AstrBot 按键讲话"
            : "浏览器语音识别不可用，已切换到 AstrBot 自由讲话",
          4600,
        );
        icons();
        state.sttFallbackPending = false;
        return;
      } catch (error) {
        state.mediaStream = null;
        showToast(error?.message || "无法获得麦克风权限，仍可使用文字通话", 4800);
      }
    } else {
      showToast(
        reason === "service-not-allowed"
          ? "浏览器语音服务不可用，仍可使用文字和摄像头通话"
          : "浏览器语音识别被拒绝，仍可使用文字和摄像头通话",
        4800,
      );
    }
    updateTalkControls();
    setRoomStatus("listening", "文字通话已接通");
    updateCameraButton();
    state.sttFallbackPending = false;
  }

  function startRecognition() {
    if (!state.callActive || state.botSpeaking || state.recognitionRunning || !state.recognition) return;
    try { state.recognition.start(); }
    catch (error) {
      if (["NotAllowedError", "SecurityError"].includes(String(error?.name || ""))) {
        fallbackFromBrowserRecognition("not-allowed");
        return;
      }
      state.recognitionFailCount = Math.min(state.recognitionFailCount + 1, 5);
      scheduleRecognitionRestart();
    }
  }

  function scheduleRecognitionRestart() {
    window.clearTimeout(state.recognitionRestartTimer);
    if (!state.callActive || state.botSpeaking || resolvedSttMode() !== "browser") return;
    if (state.talkMode === "push" && !state.pushToTalkHeld) return;
    const delay = Math.min(5000, 280 * (2 ** state.recognitionFailCount));
    state.recognitionRestartTimer = window.setTimeout(startRecognition, delay);
  }

  function pauseRecognitionForBot() {
    state.botSpeaking = true;
    clearCallIdleTimer();
    // 看门狗：音频或朗读事件丢失时防止麦克风永久停摆
    window.clearTimeout(state.speakingWatchdogTimer);
    state.speakingWatchdogTimer = window.setTimeout(() => {
      if (!state.botSpeaking) return;
      stopAudio();
      resumeRecognitionAfterBot();
    }, 90000);
    if (state.recognitionRunning && state.recognition) {
      try { state.recognition.abort(); } catch { /* noop */ }
    }
    duckVideoForBot();
    setRoomStatus("speaking", "正在说话");
  }

  function resumeRecognitionAfterBot() {
    window.clearTimeout(state.speakingWatchdogTimer);
    state.botSpeaking = false;
    restoreVideoVolume();
    if (state.mode === "watch") {
      const video = $("#watchVideo");
      setRoomStatus("watching", video.ended ? "已经看完" : (video.paused ? "已经暂停" : "一起看着"));
    } else {
      const statusText = state.callActive
        ? (state.talkMode === "push" ? `等待你按住${talkKeyName(state.talkKeyCode)}` : "正在听")
        : "等待接通";
      setRoomStatus(state.callActive ? "listening" : "idle", statusText);
    }
    scheduleRecognitionRestart();
    scheduleCallIdleTimer();
  }

  function animateVideoVolume(target, duration = 180) {
    const video = $("#watchVideo");
    window.cancelAnimationFrame(state.volumeAnimationFrame);
    const start = video.volume;
    const startedAt = performance.now();
    const step = (now) => {
      const progress = Math.min(1, (now - startedAt) / duration);
      video.volume = Math.max(0, Math.min(1, start + (target - start) * progress));
      if (progress < 1) state.volumeAnimationFrame = window.requestAnimationFrame(step);
    };
    state.volumeAnimationFrame = window.requestAnimationFrame(step);
  }

  function duckVideoForBot() {
    const video = $("#watchVideo");
    if (state.mode !== "watch" || !state.room?.watch?.duck_video_volume || video.muted || video.volume <= 0) return;
    if (state.videoVolumeBeforeDuck === null) state.videoVolumeBeforeDuck = video.volume;
    const ratio = Math.max(.05, Math.min(.8, Number(state.room?.watch?.duck_volume_ratio || .28)));
    animateVideoVolume(state.videoVolumeBeforeDuck * ratio);
  }

  function restoreVideoVolume() {
    if (state.videoVolumeBeforeDuck === null) return;
    const target = state.videoVolumeBeforeDuck;
    state.videoVolumeBeforeDuck = null;
    animateVideoVolume(target, 240);
  }

  function stopVoiceActivityDetection() {
    window.clearInterval(state.voiceActivityTimer);
    state.voiceActivityTimer = 0;
    try { state.voiceActivitySource?.disconnect(); } catch { /* noop */ }
    try { state.voiceActivityAnalyser?.disconnect(); } catch { /* noop */ }
    const context = state.voiceActivityContext;
    state.voiceActivitySource = null;
    state.voiceActivityAnalyser = null;
    state.voiceActivityContext = null;
    state.voiceActivityLastHeardAt = 0;
    state.voiceActivityStartedAt = 0;
    state.voiceActivityFrames = 0;
    if (context && context.state !== "closed") context.close().catch(() => {});
  }

  async function startVoiceActivityDetection() {
    stopVoiceActivityDetection();
    if (!state.callActive || state.talkMode !== "free" || resolvedSttMode() !== "astrbot") return false;
    if (!state.mediaStream) {
      try { state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
      catch (error) { showToast(error?.message || "无法使用麦克风"); return false; }
    }
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) {
      state.talkMode = "push";
      saveTalkPreferences();
      updateTalkControls();
      setRoomStatus("listening", `等待你按住${talkKeyName(state.talkKeyCode)}`);
      showToast("当前浏览器不支持自动分段，已切换为按键讲话", 4600);
      return false;
    }
    try {
      const context = new AudioContextClass();
      const analyser = context.createAnalyser();
      const source = context.createMediaStreamSource(state.mediaStream);
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = .45;
      source.connect(analyser);
      if (context.state === "suspended") await context.resume();
      state.voiceActivityContext = context;
      state.voiceActivityAnalyser = analyser;
      state.voiceActivitySource = source;
      const samples = new Uint8Array(analyser.fftSize);
      let noiseFloor = .012;
      state.voiceActivityTimer = window.setInterval(() => {
        if (!state.callActive || state.talkMode !== "free" || resolvedSttMode() !== "astrbot") {
          stopVoiceActivityDetection();
          return;
        }
        if (state.botSpeaking) {
          state.voiceActivityFrames = 0;
          if (state.recording) stopRecording(true);
          return;
        }
        analyser.getByteTimeDomainData(samples);
        let energy = 0;
        for (const sample of samples) {
          const normalized = (sample - 128) / 128;
          energy += normalized * normalized;
        }
        const level = Math.sqrt(energy / samples.length);
        if (!state.recording) noiseFloor = (noiseFloor * .96) + (Math.min(level, .04) * .04);
        const threshold = Math.max(.028, noiseFloor * 2.4);
        const now = Date.now();
        if (level >= threshold) {
          state.voiceActivityFrames += 1;
          state.voiceActivityLastHeardAt = now;
          if (!state.recording && state.voiceActivityFrames >= 2) {
            state.voiceActivityStartedAt = now;
            startRecording();
          }
        } else {
          state.voiceActivityFrames = 0;
        }
        if (
          state.recording
          && (
            (state.voiceActivityLastHeardAt && now - state.voiceActivityLastHeardAt >= 900)
            || (state.voiceActivityStartedAt && now - state.voiceActivityStartedAt >= 28000)
          )
        ) {
          stopRecording();
          state.voiceActivityStartedAt = 0;
        }
      }, 80);
      return true;
    } catch (error) {
      stopVoiceActivityDetection();
      state.talkMode = "push";
      saveTalkPreferences();
      updateTalkControls();
      setRoomStatus("listening", `等待你按住${talkKeyName(state.talkKeyCode)}`);
      showToast(`${error?.message || "无法启动自由讲话检测"}，已切换为按键讲话`, 4600);
      return false;
    }
  }

  async function startRecording() {
    if (!state.callActive || state.recording || resolvedSttMode() !== "astrbot") return;
    noteCallActivity();
    if (!state.mediaStream) {
      try { state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
      catch (error) { showToast(error?.message || "无法使用麦克风"); return; }
    }
    const preferred = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"]
      .find((type) => window.MediaRecorder?.isTypeSupported?.(type));
    try {
      const chunks = [];
      const recorder = preferred ? new MediaRecorder(state.mediaStream, { mimeType: preferred }) : new MediaRecorder(state.mediaStream);
      recorder.addEventListener("dataavailable", (event) => { if (event.data?.size) chunks.push(event.data); });
      recorder.addEventListener("stop", async () => {
        state.recording = false;
        $("#holdToTalk").classList.remove("recording");
        if (state.callActive && !state.botSpeaking) {
          setRoomStatus(
            "listening",
            state.talkMode === "push" ? `等待你按住${talkKeyName(state.talkKeyCode)}` : "正在听",
          );
        }
        if (recorder.__discard) return;
        if (!chunks.length) return;
        const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        // base64 膨胀 4/3 后需低于服务端 16MiB 消息上限，留足信封余量
        if (blob.size > 10 * 1024 * 1024) { showToast("这段语音太长，请分开说"); return; }
        const data = await blobToBase64(blob);
        const frame = cameraVisionAvailable() ? await captureCameraFrameData() : "";
        send({
          type: "audio_utterance",
          utterance_id: newUtteranceId(),
          mime: blob.type || "audio/webm",
          data,
          frame,
          ...clientTimeContext(),
        });
      });
      state.recorder = recorder;
      state.recording = true;
      $("#holdToTalk").classList.add("recording");
      setRoomStatus("listening", "正在录音");
      recorder.start(250);
    } catch (error) {
      state.recording = false;
      showToast(error?.message || "无法开始录音");
    }
  }

  function stopRecording(discard = false) {
    if (!state.recorder) return;
    const recorder = state.recorder;
    recorder.__discard = Boolean(discard);
    if (state.recorder.state !== "inactive") {
      try { state.recorder.stop(); } catch { /* noop */ }
    }
    state.recorder = null;
  }

  function beginPushToTalk() {
    if (!state.callActive || state.talkMode !== "push" || state.pushToTalkHeld || resolvedSttMode() === "none") return;
    state.pushToTalkHeld = true;
    noteCallActivity();
    if (state.botSpeaking) {
      send({ type: "interrupt" });
      stopAudio();
    }
    $("#holdToTalk").classList.add("recording");
    setRoomStatus("listening", "正在听你说");
    if (resolvedSttMode() === "browser") startRecognition();
    else startRecording();
  }

  function endPushToTalk(discard = false) {
    if (!state.pushToTalkHeld && !state.recording) return;
    state.pushToTalkHeld = false;
    window.clearTimeout(state.recognitionRestartTimer);
    $("#holdToTalk").classList.remove("recording");
    if (resolvedSttMode() === "browser") {
      if (state.recognition) {
        try {
          if (discard) state.recognition.abort();
          else state.recognition.stop();
        } catch { /* noop */ }
      }
    } else {
      stopRecording(discard);
    }
    if (state.callActive && !state.botSpeaking) {
      setRoomStatus("listening", `等待你按住${talkKeyName(state.talkKeyCode)}`);
    }
  }

  async function blobToBase64(blob) {
    const bytes = new Uint8Array(await blob.arrayBuffer());
    let binary = "";
    const size = 0x8000;
    for (let index = 0; index < bytes.length; index += size) {
      binary += String.fromCharCode(...bytes.subarray(index, index + size));
    }
    return btoa(binary);
  }

  async function sendUserText(text, source = "text", alternatives = [], selectedText = "") {
    const value = String(text || "").trim();
    if (!value) return false;
    const frame = state.mode === "watch"
      ? await captureFrameData(720, .76)
      : (state.mode === "call" && cameraVisionAvailable() ? await captureCameraFrameData() : "");
    if (state.botSpeaking) stopAudio();
    noteCallActivity();
    const utteranceId = source === "browser_stt" ? newUtteranceId() : "";
    const sent = send({
      type: "user_text",
      text: value,
      source,
      alternatives,
      utterance_id: utteranceId,
      state: playerState(),
      frame,
      selected_text: state.mode === "novel" ? String(selectedText || "").slice(0, 2400) : "",
      ...clientTimeContext(),
    });
    if (sent) {
      $("#messageInput").value = "";
    }
    return sent;
  }

  function newUtteranceId() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  }

  function clientTimeContext() {
    const now = new Date();
    let clientTimezone = "";
    try {
      clientTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    } catch { /* older WebView */ }
    const pad = (value) => String(value).padStart(2, "0");
    const offsetMinutes = -now.getTimezoneOffset();
    const offsetSign = offsetMinutes >= 0 ? "+" : "-";
    const offsetHours = pad(Math.floor(Math.abs(offsetMinutes) / 60));
    const offsetRemainder = pad(Math.abs(offsetMinutes) % 60);
    const clientLocalTime = [
      `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`,
      `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}${offsetSign}${offsetHours}:${offsetRemainder}`,
    ].join("T");
    return {
      client_local_time: clientLocalTime,
      client_timezone: clientTimezone,
    };
  }

  function revealSpeechMessage(message) {
    if (!message || message.revealed) return;
    message.revealed = true;
    const visible = sanitizeBotDisplayText(message.display_text || message.text);
    if (!visible) return;
    addMessage("bot", visible, message.source === "watch_comment" ? "观影" : "");
    showFullscreenSpeech(visible);
  }

  function runAfterPlaybackAction(action) {
    if (action !== "hangup") return false;
    state.audioQueue.length = 0;
    window.clearTimeout(state.speakingWatchdogTimer);
    state.botSpeaking = false;
    restoreVideoVolume();
    markFullscreenSpeechFinished();
    if (state.callActive) {
      stopCall(false);
      showToast(`${state.room?.bot_name || "对方"} 已结束语音连接`);
    }
    return true;
  }

  function isDuplicateWatchSpeech(message) {
    if (message?.source !== "watch_comment") return false;
    const text = sanitizeBotDisplayText(message.display_text || message.text);
    if (!text) return false;
    const now = Date.now();
    const duplicate = state.lastWatchSpeechText === text && now - state.lastWatchSpeechAt < 12000;
    if (!duplicate) {
      state.lastWatchSpeechText = text;
      state.lastWatchSpeechAt = now;
    }
    return duplicate;
  }

  function enqueueAudio(message) {
    state.audioQueue.push({
      data: message.data,
      mime: message.mime || "audio/wav",
      text: message.text,
      display_text: message.display_text,
      source: message.source,
      after_playback_action: message.after_playback_action,
      revealed: false,
    });
    if (!state.currentAudio) playNextAudio();
  }

  function playNextAudio() {
    if (!state.audioQueue.length) {
      state.currentAudio = null;
      markFullscreenSpeechFinished();
      resumeRecognitionAfterBot();
      return;
    }
    pauseRecognitionForBot();
    const item = state.audioQueue.shift();
    try {
      const binary = atob(item.data || "");
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
      const url = URL.createObjectURL(new Blob([bytes], { type: item.mime }));
      const audio = new Audio(url);
      audio.volume = state.ttsVolume;
      state.currentAudio = audio;
      state.currentAudioUrl = url;
      const finish = () => finishCurrentAudio(audio, url, item, true);
      const fail = () => {
        revealSpeechMessage(item);
        markFullscreenSpeechStarted();
        finishCurrentAudio(audio, url, item, false);
      };
      audio.addEventListener("playing", () => {
        revealSpeechMessage(item);
        markFullscreenSpeechStarted();
      }, { once: true });
      audio.addEventListener("ended", finish, { once: true });
      audio.addEventListener("error", fail, { once: true });
      audio.play().catch(() => {
        fail();
        showToast("浏览器阻止了音频播放，请点击页面后重试");
      });
    } catch {
      revealSpeechMessage(item);
      markFullscreenSpeechStarted();
      playNextAudio();
    }
  }

  function finishCurrentAudio(audio, url, item, playbackCompleted = true) {
    if (url) URL.revokeObjectURL(url);
    if (state.currentAudio !== audio) return;
    state.currentAudioUrl = "";
    state.currentAudio = null;
    if (!(playbackCompleted && runAfterPlaybackAction(item?.after_playback_action))) playNextAudio();
  }

  function speakInBrowser(text, language = "", displayText = "", source = "", afterPlaybackAction = "") {
    const message = { text, display_text: displayText, source, revealed: false };
    if (!window.speechSynthesis || !String(text || "").trim()) {
      revealSpeechMessage(message);
      markFullscreenSpeechStarted();
      markFullscreenSpeechFinished();
      resumeRecognitionAfterBot();
      return;
    }
    stopAudio(true);
    pauseRecognitionForBot();
    const utterance = new SpeechSynthesisUtterance(String(text));
    utterance.lang = language || state.room?.tts?.browser_language || "zh-CN";
    utterance.rate = 1.03;
    utterance.volume = state.ttsVolume;
    const finish = (playbackCompleted) => {
      if (state.browserUtterance !== utterance) return;
      revealSpeechMessage(message);
      state.browserUtterance = null;
      markFullscreenSpeechFinished();
      if (!(playbackCompleted && runAfterPlaybackAction(afterPlaybackAction))) resumeRecognitionAfterBot();
    };
    utterance.addEventListener("start", () => {
      revealSpeechMessage(message);
      markFullscreenSpeechStarted();
    }, { once: true });
    utterance.addEventListener("end", () => finish(true), { once: true });
    utterance.addEventListener("error", () => finish(false), { once: true });
    state.browserUtterance = utterance;
    try { window.speechSynthesis.speak(utterance); }
    catch { finish(false); }
  }

  function stopAudio(preserveFullscreenSpeech = false) {
    state.audioQueue.length = 0;
    const audio = state.currentAudio;
    const audioUrl = state.currentAudioUrl;
    state.currentAudio = null;
    state.currentAudioUrl = "";
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    state.browserUtterance = null;
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    resumeRecognitionAfterBot();
    if (!preserveFullscreenSpeech) hideFullscreenSpeech(true);
  }

  function openLocalVideo(file) {
    if (!file) return;
    if (state.objectVideoUrl) URL.revokeObjectURL(state.objectVideoUrl);
    state.objectVideoUrl = URL.createObjectURL(file);
    state.videoSourceLabel = file.name || "本地视频";
    loadVideo(state.objectVideoUrl, state.videoSourceLabel, false);
  }

  function setVideoPlaceholder(text, visible) {
    const empty = $("#videoEmpty");
    const label = empty.querySelector("strong");
    if (label) label.textContent = text || "选择一段视频";
    empty.hidden = !visible;
  }

  function videoHasSource() {
    const video = $("#watchVideo");
    return Boolean(video.currentSrc || video.getAttribute("src"));
  }

  function dashAudio() {
    return state.dashAudioEnabled ? $("#watchAudio") : null;
  }

  function resetDashAudio(url = "") {
    const audio = $("#watchAudio");
    audio.pause();
    audio.removeAttribute("src");
    state.dashAudioEnabled = Boolean(url);
    if (url) audio.src = url;
    audio.load();
  }

  function syncDashAudio({ seek = false, play = false } = {}) {
    const audio = dashAudio();
    if (!audio) return;
    const video = $("#watchVideo");
    audio.playbackRate = video.playbackRate || 1;
    audio.volume = video.volume;
    audio.muted = video.muted;
    if (Number.isFinite(video.currentTime) && Number.isFinite(audio.duration)) {
      const drift = Math.abs((audio.currentTime || 0) - video.currentTime);
      if (seek || drift > 0.3) {
        try { audio.currentTime = Math.min(video.currentTime, Math.max(0, audio.duration - 0.05)); }
        catch { /* audio metadata is not ready yet */ }
      }
    }
    if (video.paused || video.ended) audio.pause();
    else if (play && audio.paused) audio.play().catch(() => {});
  }

  function setVideoControlIcon(button, icon, label) {
    if (!button || button.dataset.icon === icon) return;
    button.dataset.icon = icon;
    button.innerHTML = `<i data-lucide="${icon}"></i>`;
    if (label) {
      button.title = label;
      button.setAttribute("aria-label", label);
    }
    icons();
  }

  function updateVideoControls() {
    const video = $("#watchVideo");
    const hasSource = videoHasSource();
    const duration = Number.isFinite(video.duration) ? Math.max(0, video.duration) : 0;
    const current = Number.isFinite(video.currentTime) ? Math.max(0, video.currentTime) : 0;
    const playing = hasSource && !video.paused && !video.ended;
    $("#videoStage").classList.toggle("is-playing", playing);
    setVideoControlIcon($("#videoPlayPause"), playing ? "pause" : "play", playing ? "暂停" : "播放");
    setVideoControlIcon($("#videoCenterToggle"), "play", "播放");
    setVideoControlIcon(
      $("#videoMute"),
      video.muted || video.volume <= 0 ? "volume-x" : (video.volume < .5 ? "volume-1" : "volume-2"),
      video.muted || video.volume <= 0 ? "取消静音" : "静音",
    );
    const progress = $("#videoProgress");
    progress.disabled = !hasSource || duration <= 0;
    const progressRatio = duration > 0 ? Math.max(0, Math.min(1, current / duration)) : 0;
    progress.value = String(Math.round(progressRatio * 1000));
    progress.style.setProperty("--video-progress", `${progressRatio * 100}%`);
    $("#videoTime").textContent = `${formatTime(current)} / ${formatTime(duration)}`;
    const visibleVolume = video.muted ? 0 : video.volume;
    $("#videoVolume").value = String(visibleVolume);
    $("#videoVolume").style.setProperty("--video-volume", `${visibleVolume * 100}%`);
    if (!state.videoRateHoldActive) $("#videoRate").value = String(video.playbackRate || 1);
    ["videoPlayPause", "videoSeekBack", "videoSeekForward", "videoMute"].forEach((id) => {
      $(`#${id}`).disabled = !hasSource;
    });
    $("#videoCenterToggle").disabled = !hasSource;
    if (!playing) showVideoControls({ autoHide: false });
  }

  function videoControlsHaveFocus() {
    const active = document.activeElement;
    return Boolean(
      active
      && ($("#videoControls").contains(active) || active === $("#toggleFullscreen"))
    );
  }

  function hideVideoControls() {
    window.clearTimeout(state.videoControlsHideTimer);
    state.videoControlsHideTimer = 0;
    const video = $("#watchVideo");
    if (video.paused || video.ended || state.videoRateHoldActive || videoControlsHaveFocus()) return;
    $("#videoStage").classList.add("controls-hidden");
  }

  function showVideoControls({ autoHide = true } = {}) {
    window.clearTimeout(state.videoControlsHideTimer);
    state.videoControlsHideTimer = 0;
    $("#videoStage").classList.remove("controls-hidden");
    const video = $("#watchVideo");
    if (autoHide && !video.paused && !video.ended) {
      state.videoControlsHideTimer = window.setTimeout(hideVideoControls, 2200);
    }
  }

  async function toggleVideoPlayback() {
    const video = $("#watchVideo");
    const audio = dashAudio();
    if (!videoHasSource()) return;
    if (!video.paused && !video.ended) {
      video.pause();
      audio?.pause();
      return;
    }
    try {
      syncDashAudio({ seek: true });
      const starts = [video.play()];
      if (audio) starts.push(audio.play());
      await Promise.all(starts);
    } catch (error) {
      showToast(error?.message || "视频暂时无法播放");
    }
  }

  function showVideoSeekFeedback(text, direction, duration = 700) {
    const feedback = $("#videoSeekFeedback");
    window.clearTimeout(state.videoSeekFeedbackTimer);
    feedback.textContent = text;
    feedback.dataset.direction = direction;
    feedback.hidden = false;
    if (duration > 0) {
      state.videoSeekFeedbackTimer = window.setTimeout(() => { feedback.hidden = true; }, duration);
    }
  }

  function hideVideoSeekFeedback() {
    window.clearTimeout(state.videoSeekFeedbackTimer);
    $("#videoSeekFeedback").hidden = true;
  }

  function seekVideoBy(seconds) {
    const video = $("#watchVideo");
    if (!videoHasSource() || !Number.isFinite(video.duration)) return;
    video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + seconds));
    showVideoSeekFeedback(`${seconds > 0 ? "+" : ""}${seconds} 秒`, seconds < 0 ? "back" : "forward");
    updateVideoControls();
  }

  function activateTemporaryVideoRate(source) {
    const video = $("#watchVideo");
    if (video.paused || video.ended || !videoHasSource()) return false;
    state.videoRateHoldActive = true;
    state.videoRateHoldSource = source;
    state.videoRateBeforeHold = video.playbackRate || 1;
    video.playbackRate = 2;
    source?.classList?.add("holding");
    showVideoSeekFeedback("2x", "rate", 0);
    return true;
  }

  function beginTemporaryVideoRate(source) {
    window.clearTimeout(state.videoRateHoldTimer);
    if ($("#watchVideo").paused) return;
    state.videoRateHoldTimer = window.setTimeout(() => activateTemporaryVideoRate(source), 360);
  }

  function endTemporaryVideoRate() {
    window.clearTimeout(state.videoRateHoldTimer);
    state.videoRateHoldTimer = 0;
    if (!state.videoRateHoldActive) return false;
    const video = $("#watchVideo");
    video.playbackRate = state.videoRateBeforeHold || 1;
    state.videoRateHoldSource?.classList?.remove("holding");
    state.videoRateHoldActive = false;
    state.videoRateHoldSource = null;
    hideVideoSeekFeedback();
    updateVideoControls();
    showVideoControls();
    return true;
  }

  function isBilibiliPageUrl(url) {
    try {
      const host = new URL(url).hostname.toLowerCase().replace(/\.$/, "");
      return host === "bilibili.com" || host.endsWith(".bilibili.com") || host === "b23.tv" || host.endsWith(".b23.tv");
    } catch {
      return false;
    }
  }

  function loadVideo(url, title, remote, qualityLabel = "", audioUrl = "") {
    const video = $("#watchVideo");
    video.pause();
    resetDashAudio(audioUrl);
    video.removeAttribute("src");
    if (remote) video.crossOrigin = "anonymous";
    else video.removeAttribute("crossorigin");
    video.src = url;
    video.load();
    updateVideoControls();
    state.videoSourceLabel = title || "视频";
    state.videoQualityLabel = String(qualityLabel || "");
    state.lastWatchSpeechText = "";
    state.lastWatchSpeechAt = 0;
    state.remoteCorsFallbackTried = false;
    $("#videoTitle").textContent = state.videoSourceLabel;
    setVideoPlaceholder("正在加载视频", true);
    state.frameWarningShown = false;
    state.lastFrameSentAt = Date.now();
    state.lastSceneSignature = null;
    state.openingSent = false;
    window.clearTimeout(state.openingTimer);
    updatePlayerState("loaded");
  }

  function playerState() {
    const video = $("#watchVideo");
    return {
      title: state.videoSourceLabel || "",
      source: state.objectVideoUrl ? "local" : (video.currentSrc || ""),
      current_time: Number.isFinite(video.currentTime) ? video.currentTime : 0,
      duration: Number.isFinite(video.duration) ? video.duration : 0,
      paused: video.paused,
      playback_rate: video.playbackRate || 1,
    };
  }

  function updatePlayerState(eventName = "") {
    const current = playerState();
    const quality = state.videoQualityLabel ? ` · ${state.videoQualityLabel}` : "";
    $("#videoMeta").textContent = `${formatTime(current.current_time)} / ${formatTime(current.duration)}${quality}`;
    if (state.connected && state.memberRole === "owner") {
      send({ type: "player_state", state: current, event: eventName });
    }
    state.lastPlayerStateSentAt = Date.now();
  }

  function formatTime(seconds) {
    const value = Math.max(0, Math.floor(Number(seconds) || 0));
    return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
  }

  function resetSceneMonitor() {
    window.clearInterval(state.frameTimer);
    state.frameTimer = window.setInterval(() => {
      const video = $("#watchVideo");
      if (state.mode !== "watch" || !$("#autoComment").checked || video.paused || video.readyState < 2) return;
      // 页面不可见时暂停画面观察，避免后台持续抓帧耗电耗流量
      if (document.visibilityState === "hidden" || state.frameCaptureBusy) return;
      const signature = sceneSignature(video);
      if (!signature) return;
      if (!state.lastSceneSignature) {
        state.lastSceneSignature = signature;
        return;
      }
      let difference = 0;
      for (let index = 0; index < signature.length; index += 1) {
        difference += Math.abs(signature[index] - state.lastSceneSignature[index]);
      }
      difference /= signature.length * 255;
      state.lastSceneSignature = signature;
      const elapsed = Date.now() - state.lastFrameSentAt;
      const sceneInterval = Math.max(8, Number(state.room?.watch?.scene_min_interval_seconds || 18)) * 1000;
      const heartbeat = Math.max(sceneInterval, Number(state.room?.watch?.comment_interval_seconds || 60) * 1000);
      if (difference >= .16 && elapsed >= sceneInterval) {
        captureAndSendFrame("scene_change", difference);
      } else if (elapsed >= heartbeat) {
        captureAndSendFrame("heartbeat", difference);
      }
    }, 1600);
  }

  function signatureCanvas() {
    // 场景签名使用独立的 32x18 画布，与大图抓帧画布分离，避免反复重建缓冲与上下文属性冲突
    if (!state.signatureCanvas) {
      const canvas = document.createElement("canvas");
      canvas.width = 32;
      canvas.height = 18;
      state.signatureCanvas = canvas;
      state.signatureContext = canvas.getContext("2d", { alpha: false, willReadFrequently: true });
    }
    return state.signatureContext;
  }

  function sceneSignature(video) {
    try {
      const context = signatureCanvas();
      if (!context) return null;
      context.drawImage(video, 0, 0, 32, 18);
      const pixels = context.getImageData(0, 0, 32, 18).data;
      const signature = new Uint8Array(32 * 18);
      for (let source = 0, target = 0; source < pixels.length; source += 4, target += 1) {
        signature[target] = Math.round(pixels[source] * .299 + pixels[source + 1] * .587 + pixels[source + 2] * .114);
      }
      return signature;
    } catch {
      return null;
    }
  }

  function captureVideoFrameData(video, maxWidth = 640, quality = .74) {
    // 异步 toBlob 编码，避免大图 toDataURL 阻塞主线程
    return new Promise((resolve) => {
      if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) { resolve(""); return; }
      const canvas = $("#frameCanvas");
      const scale = Math.min(1, maxWidth / video.videoWidth);
      const width = Math.max(1, Math.round(video.videoWidth * scale));
      const height = Math.max(1, Math.round(video.videoHeight * scale));
      if (canvas.width !== width) canvas.width = width;
      if (canvas.height !== height) canvas.height = height;
      try {
        const context = canvas.getContext("2d", { alpha: false });
        context.drawImage(video, 0, 0, width, height);
        canvas.toBlob((blob) => {
          if (!blob) { resolve(""); return; }
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result || ""));
          reader.onerror = () => resolve("");
          reader.readAsDataURL(blob);
        }, "image/jpeg", quality);
      } catch {
        resolve("");
      }
    });
  }

  function captureFrameData(maxWidth = 640, quality = .74) {
    return captureVideoFrameData($("#watchVideo"), maxWidth, quality);
  }

  async function captureCameraFrameData() {
    if (!state.cameraEnabled || !state.cameraStream) return "";
    return captureVideoFrameData(
      $("#callCamera"),
      CAMERA_UPLOAD_MAX_WIDTH,
      CAMERA_UPLOAD_JPEG_QUALITY,
    );
  }

  async function sendCameraFrame() {
    if (
      !state.connected
      || !state.callActive
      || !state.cameraEnabled
      || !cameraVisionAvailable()
      || state.mode !== "call"
      || document.visibilityState === "hidden"
      || state.cameraCaptureBusy
    ) return;
    state.cameraCaptureBusy = true;
    try {
      const image = await captureCameraFrameData();
      if (image) send({ type: "call_frame", active: true, image });
    } finally {
      state.cameraCaptureBusy = false;
    }
  }

  async function captureAndSendFrame(trigger = "heartbeat", sceneScore = 0) {
    const manual = trigger === "manual";
    if (!manual && (!$("#autoComment").checked || state.botSpeaking)) return;
    if (state.frameCaptureBusy) return;
    if (manual && state.botSpeaking) {
      stopAudio();
      send({ type: "interrupt" });
    }
    const video = $("#watchVideo");
    state.frameCaptureBusy = true;
    let image = "";
    try {
      image = await captureFrameData();
    } finally {
      state.frameCaptureBusy = false;
    }
    if (!image) {
      if (!state.frameWarningShown || manual) {
        showToast(manual ? "当前还没有可读取的视频画面" : "该视频源不允许读取画面，播放和聊天仍可继续", 4200);
      }
      state.frameWarningShown = true;
      state.lastFrameSentAt = Date.now();
      return;
    }
    state.frameWarningShown = false;
    state.lastFrameSentAt = Date.now();
    const sent = send({
      type: "watch_frame",
      image,
      state: playerState(),
      trigger,
      captured_at: Number.isFinite(video.currentTime) ? video.currentTime : 0,
      scene_score: Number(sceneScore) || 0,
      manual,
    });
    if (manual && sent) showToast("正在一起看这幕");
  }

  function bindEvents() {
    $$("[data-mode-tab]").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.modeTab)));
    $("#settingsButton").addEventListener("click", () => setSettingsOpen($("#settingsPanel").hidden));
    $("#inviteButton").addEventListener("click", requestInviteLink);
    $("#reconnectButton").addEventListener("click", reconnectNow);
    $("#copyInviteLink").addEventListener("click", copyInviteLink);
    $("#closeSettings").addEventListener("click", () => setSettingsOpen(false, true));
    $("#ttsVolume").addEventListener("input", (event) => setTtsVolume(event.target.value, { persist: true }));
    $$("[data-stt-mode]").forEach((button) => button.addEventListener("click", () => {
      const wasActive = state.callActive;
      if (wasActive) stopCall(true);
      state.sttMode = button.dataset.sttMode;
      if (["auto", "browser"].includes(state.sttMode)) state.browserRecognitionUnavailable = false;
      setActiveSttButton(state.sttMode);
      if (wasActive) startCall();
    }));
    $$("[data-talk-mode]").forEach((button) => button.addEventListener("click", () => {
      setTalkMode(button.dataset.talkMode).catch((error) => showToast(error?.message || "无法切换讲话方式"));
    }));
    $("#talkKeyCapture").addEventListener("click", () => {
      state.talkKeyCapturing = !state.talkKeyCapturing;
      updateTalkControls();
    });
    $("#callToggle").addEventListener("click", () => state.callActive ? stopCall(true) : startCall());
    $("#callTtsToggle").addEventListener("click", () => {
      if (!state.callActive) return;
      const enabled = !state.callTtsEnabled;
      if (!send({ type: "set_call_tts", enabled })) return;
      state.callTtsEnabled = enabled;
      updateCallTtsButton();
      if (!enabled) stopAudio();
      showToast(enabled ? "已开启通话语音" : "已静音，仅显示文字回复");
    });
    $("#watchCallToggle").addEventListener("click", () => state.callActive ? stopCall(true) : startCall());
    $("#workCallToggle").addEventListener("click", () => state.callActive ? stopCall(true) : startCall());
    $("#refreshWorkContext").addEventListener("click", () => {
      $("#refreshWorkContext").disabled = true;
      $("#workStatus").textContent = "正在刷新当前上下文";
      if (!send({ type: "refresh_work_context" })) $("#refreshWorkContext").disabled = false;
    });
    $("#watchTtsEnabled").addEventListener("change", (event) => {
      const enabled = Boolean(event.target.checked);
      if (!send({ type: "set_watch_tts", enabled })) {
        event.target.checked = !enabled;
        return;
      }
      showToast(enabled ? "后续观影回复将播放语音" : "后续观影回复仅显示文字");
    });
    $("#cameraToggle").addEventListener("click", toggleCamera);
    $("#switchCamera").addEventListener("click", switchCamera);
    $("#cameraDeviceSelect").addEventListener("change", (event) => {
      selectCameraDevice(event.target.value).catch((error) => showToast(cameraErrorMessage(error), 4800));
    });
    navigator.mediaDevices?.addEventListener?.("devicechange", () => {
      refreshCameraDevices(state.cameraStream?.getVideoTracks?.()[0]?.getSettings?.()?.deviceId || "").catch(() => {});
    });
    $("#interruptButton").addEventListener("click", () => { send({ type: "interrupt" }); stopAudio(); });
    $("#workInterruptButton").addEventListener("click", () => { send({ type: "interrupt" }); stopAudio(); });
    $("#toggleFullscreen").addEventListener("click", toggleWatchFullscreen);
    const onFullscreenChange = () => {
      if (!isWatchFullscreen()) hideFullscreenSpeech(true);
      updateFullscreenButton();
    };
    document.addEventListener("fullscreenchange", onFullscreenChange);
    document.addEventListener("webkitfullscreenchange", onFullscreenChange);
    window.addEventListener("keydown", (event) => {
      if (state.talkKeyCapturing) {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (event.code === "Escape") {
          state.talkKeyCapturing = false;
          updateTalkControls();
        } else if (!event.repeat) {
          setTalkKey(event.code);
        }
        return;
      }
      if (
        state.talkMode !== "push"
        || event.code !== state.talkKeyCode
        || event.repeat
        || isTalkKeyBlockedByTarget(event.target)
      ) return;
      event.preventDefault();
      beginPushToTalk();
    });
    window.addEventListener("keyup", (event) => {
      if (state.talkMode !== "push" || event.code !== state.talkKeyCode || !state.pushToTalkHeld) return;
      event.preventDefault();
      endPushToTalk();
    });
    window.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!$("#settingsPanel").hidden) setSettingsOpen(false, true);
      else if ($("#novelLibraryPanel").classList.contains("mobile-open")) setNovelLibraryOpen(false);
      else if (state.pseudoFullscreen) setPseudoFullscreen(false);
    });

    const hold = $("#holdToTalk");
    hold.addEventListener("pointerdown", (event) => { event.preventDefault(); hold.setPointerCapture?.(event.pointerId); beginPushToTalk(); });
    hold.addEventListener("pointerup", (event) => { event.preventDefault(); endPushToTalk(); });
    hold.addEventListener("pointercancel", () => endPushToTalk(true));
    hold.addEventListener("keydown", (event) => {
      if ([" ", "Enter"].includes(event.key) && !event.repeat) { event.preventDefault(); beginPushToTalk(); }
    });
    hold.addEventListener("keyup", (event) => {
      if ([" ", "Enter"].includes(event.key)) { event.preventDefault(); endPushToTalk(); }
    });

    $("#messageForm").addEventListener("submit", (event) => {
      event.preventDefault();
      if (state.mode === "novel") sendNovelQuestion($("#messageInput").value);
      else sendUserText($("#messageInput").value);
    });
    const chooseNovelFile = () => $("#novelFileInput").click();
    $("#importNovel").addEventListener("click", chooseNovelFile);
    $("#novelEmptyImport").addEventListener("click", chooseNovelFile);
    $("#novelFileInput").addEventListener("change", (event) => importNovelFile(event.target.files?.[0]));
    $("#novelSearch").addEventListener("input", () => renderNovelLibrary(state.novelBooks));
    $("#novelPrevChapter").addEventListener("click", () => requestNovelChapter(
      Number(state.novelPendingChapterIndex ?? state.novelChapter?.chapter_index ?? 0) - 1,
    ));
    $("#novelNextChapter").addEventListener("click", () => requestNovelChapter(
      Number(state.novelPendingChapterIndex ?? state.novelChapter?.chapter_index ?? 0) + 1,
    ));
    $("#novelReader").addEventListener("scroll", scheduleNovelProgressSave, { passive: true });
    $("#novelContent").addEventListener("mouseup", updateNovelSelection);
    $("#novelContent").addEventListener("touchend", () => window.setTimeout(updateNovelSelection, 80), { passive: true });
    $("#askNovelSelection").addEventListener("click", () => sendNovelQuestion("结合我选中的这段，这里具体是什么意思？", state.novelSelectedText));
    $("#askReadingProgress").addEventListener("click", () => sendNovelQuestion("我们上次读到哪里了？"));
    $$('[data-novel-question]').forEach((button) => button.addEventListener("click", () => sendNovelQuestion(button.dataset.novelQuestion)));
    $("#openNovelLibrary").addEventListener("click", () => setNovelLibraryOpen(true));
    $("#closeNovelLibrary").addEventListener("click", () => setNovelLibraryOpen(false));
    $("#novelLibraryBackdrop").addEventListener("click", () => setNovelLibraryOpen(false));
    $("#openLocalVideo").addEventListener("click", () => $("#localVideoInput").click());
    $("#localVideoInput").addEventListener("change", (event) => openLocalVideo(event.target.files?.[0]));
    $("#openVideoUrl").addEventListener("click", () => $("#videoUrlDialog").showModal());
    $("#observeFrame").addEventListener("click", () => captureAndSendFrame("manual"));
    $("#videoUrlForm").addEventListener("submit", (event) => {
      if (event.submitter?.value !== "default") return;
      event.preventDefault();
      const raw = $("#videoUrlInput").value.trim();
      try {
        const parsed = new URL(raw);
        if (!["http:", "https:"].includes(parsed.protocol)) throw new Error();
        if (state.objectVideoUrl) { URL.revokeObjectURL(state.objectVideoUrl); state.objectVideoUrl = ""; }
        if (isBilibiliPageUrl(parsed.toString())) {
          // 上一次解析仍在进行时不重复提交，避免服务端解析任务互相取消
          if (state.resolvingMedia) { showToast("上一个视频仍在解析，请稍候"); return; }
          if (!send({ type: "resolve_media", url: parsed.toString() })) return;
          state.resolvingMedia = true;
          $("#videoTitle").textContent = "正在解析视频";
          $("#watchStatus").textContent = "正在解析";
          setVideoPlaceholder("正在解析 B 站视频", true);
        } else {
          loadVideo(parsed.toString(), decodeURIComponent(parsed.pathname.split("/").pop() || parsed.hostname), true);
        }
        $("#videoUrlDialog").close();
      } catch {
        showToast("请输入有效的 HTTP 或 HTTPS 视频地址");
      }
    });

    const video = $("#watchVideo");
    const videoStage = $("#videoStage");
    const videoControls = $("#videoControls");
    videoStage.addEventListener("pointermove", () => showVideoControls());
    videoStage.addEventListener("pointerdown", () => showVideoControls());
    videoStage.addEventListener("pointerleave", hideVideoControls);
    videoControls.addEventListener("pointerenter", () => showVideoControls({ autoHide: false }));
    videoControls.addEventListener("pointerleave", () => showVideoControls());
    videoControls.addEventListener("focusin", () => showVideoControls({ autoHide: false }));
    videoControls.addEventListener("focusout", () => window.setTimeout(() => showVideoControls(), 0));
    $("#videoPlayPause").addEventListener("click", toggleVideoPlayback);
    $("#videoCenterToggle").addEventListener("click", toggleVideoPlayback);
    $("#videoSeekBack").addEventListener("click", () => seekVideoBy(-10));
    const seekForward = $("#videoSeekForward");
    seekForward.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      seekForward.setPointerCapture?.(event.pointerId);
      beginTemporaryVideoRate(seekForward);
    });
    seekForward.addEventListener("pointerup", (event) => {
      event.preventDefault();
      const held = endTemporaryVideoRate();
      if (!held) seekVideoBy(10);
    });
    seekForward.addEventListener("pointercancel", endTemporaryVideoRate);
    seekForward.addEventListener("contextmenu", (event) => event.preventDefault());
    seekForward.addEventListener("click", (event) => {
      if (event.detail === 0) seekVideoBy(10);
    });
    video.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || video.paused) return;
      video.setPointerCapture?.(event.pointerId);
      beginTemporaryVideoRate(video);
    });
    video.addEventListener("pointerup", () => {
      if (endTemporaryVideoRate()) state.videoSurfaceSuppressClick = true;
    });
    video.addEventListener("pointercancel", endTemporaryVideoRate);
    video.addEventListener("lostpointercapture", endTemporaryVideoRate);
    video.addEventListener("pointerleave", () => {
      if (endTemporaryVideoRate()) state.videoSurfaceSuppressClick = true;
      else window.clearTimeout(state.videoRateHoldTimer);
    });
    video.addEventListener("click", () => {
      if (state.videoSurfaceSuppressClick) {
        state.videoSurfaceSuppressClick = false;
        return;
      }
      toggleVideoPlayback();
    });
    video.addEventListener("contextmenu", (event) => event.preventDefault());
    $("#videoProgress").addEventListener("input", (event) => {
      if (!Number.isFinite(video.duration) || video.duration <= 0) return;
      video.currentTime = (Number(event.target.value) / 1000) * video.duration;
      syncDashAudio({ seek: true });
      updateVideoControls();
    });
    $("#videoMute").addEventListener("click", () => {
      video.muted = !video.muted;
      updateVideoControls();
    });
    $("#videoVolume").addEventListener("input", (event) => {
      video.volume = Math.max(0, Math.min(1, Number(event.target.value)));
      video.muted = video.volume === 0;
      updateVideoControls();
    });
    $("#videoRate").addEventListener("change", (event) => {
      video.playbackRate = Math.max(.5, Math.min(2, Number(event.target.value) || 1));
      updateVideoControls();
    });
    $("#videoStage").addEventListener("keydown", (event) => {
      if (["INPUT", "SELECT", "BUTTON"].includes(event.target.tagName)) return;
      const actions = {
        Space: toggleVideoPlayback,
        KeyK: toggleVideoPlayback,
        ArrowLeft: () => seekVideoBy(-5),
        KeyJ: () => seekVideoBy(-10),
        ArrowRight: () => seekVideoBy(5),
        KeyL: () => seekVideoBy(10),
        KeyM: () => { video.muted = !video.muted; updateVideoControls(); },
        KeyF: toggleWatchFullscreen,
      };
      const action = actions[event.code];
      if (!action) return;
      event.preventDefault();
      action();
    });
    video.addEventListener("loadeddata", () => {
      state.resolvingMedia = false;
      state.lastSceneSignature = null;
      setVideoPlaceholder("", false);
      $("#watchStatus").textContent = video.paused ? "已经就绪" : "一起看着";
      updatePlayerState("loaded");
      updateVideoControls();
    });
    video.addEventListener("error", () => {
      if (!video.currentSrc && !video.getAttribute("src")) return;
      if (video.crossOrigin === "anonymous" && !state.remoteCorsFallbackTried) {
        const source = video.currentSrc || video.src;
        state.remoteCorsFallbackTried = true;
        video.pause();
        video.removeAttribute("src");
        video.removeAttribute("crossorigin");
        video.src = source;
        video.load();
        setVideoPlaceholder("正在切换到仅播放模式", true);
        $("#watchStatus").textContent = "正在兼容视频源";
        showToast("该视频源不允许读取画面，已改为仅播放模式", 4200);
        return;
      }
      state.resolvingMedia = false;
      setVideoPlaceholder("视频加载失败", true);
      $("#watchStatus").textContent = "加载失败";
      showToast("视频没有成功加载，链接可能已失效或不是可播放的媒体", 5000);
    });
    $("#watchAudio").addEventListener("loadedmetadata", () => syncDashAudio({ seek: true }));
    $("#watchAudio").addEventListener("error", () => {
      if (state.dashAudioEnabled) showToast("高清音频轨加载失败，请重新解析视频", 5000);
    });
    ["play", "pause", "seeking", "seeked", "ratechange", "volumechange", "durationchange", "loadedmetadata", "timeupdate", "ended"].forEach((name) => {
      video.addEventListener(name, () => {
        const now = Date.now();
        if (name === "loadedmetadata" && state.pendingResumeTime > 0) {
          // 断线恢复：回到断开前的播放进度
          video.currentTime = Math.min(state.pendingResumeTime, Math.max(0, (video.duration || 0) - 1));
          state.pendingResumeTime = 0;
        }
        if (name === "play") syncDashAudio({ seek: true, play: true });
        else if (name === "seeking" || name === "seeked" || name === "loadedmetadata") syncDashAudio({ seek: true });
        else syncDashAudio();
        if (name !== "timeupdate" || now - state.lastPlayerStateSentAt >= 2000) updatePlayerState(name);
        updateVideoControls();
        if (name === "play") {
          showVideoControls();
          state.lastFrameSentAt = now;
          if (!state.openingSent && video.currentTime < 12) {
            state.openingSent = true;
            window.clearTimeout(state.openingTimer);
            state.openingTimer = window.setTimeout(() => {
              if (!video.paused && !video.ended) captureAndSendFrame("opening");
            }, 2400);
          }
        }
        if (name === "seeked") state.lastSceneSignature = null;
        if (name === "pause" || name === "ended") {
          endTemporaryVideoRate();
          showVideoControls({ autoHide: false });
        }
        if (name === "ended") {
          if ($("#autoComment").checked) captureAndSendFrame("ending");
        }
      });
    });
    window.addEventListener("blur", () => {
      endTemporaryVideoRate();
      if (state.pushToTalkHeld) endPushToTalk(true);
      if (state.talkKeyCapturing) {
        state.talkKeyCapturing = false;
        updateTalkControls();
      }
    });
    window.addEventListener("beforeunload", pageCleanup);
    window.addEventListener("pagehide", pageCleanup);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("online", () => {
      state.reconnectAttempts = 0;
      if (!state.connected) {
        setConnection("网络已恢复，正在重连", false);
        reconnectNow();
      }
    });
    window.addEventListener("offline", () => {
      if (!state.connected) setConnection("网络已断开，等待恢复", false);
    });
  }

  bindEvents();
  icons();
  updateVideoControls();
  ticketFromLocation();
  setMode(state.mode, false);
  connect();
})();
