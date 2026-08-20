(function () {
  "use strict";

  const directMarker = window.__PRIVATE_COMPANION_STANDALONE__;
  const webuiConfig = window.__PRIVATE_COMPANION_WEBUI__;
  const alternateConfig = window.__PRIVATE_COMPANION_WEBUI_CONFIG__;
  const modeMeta = document.querySelector('meta[name="private-companion-mode"]')?.content;
  const markedStandalone =
    directMarker === true ||
    (directMarker && typeof directMarker === "object") ||
    (webuiConfig && typeof webuiConfig === "object" && (
      webuiConfig.standalone === true || String(webuiConfig.mode || "").toLowerCase() === "standalone"
    )) ||
    (alternateConfig && typeof alternateConfig === "object" && (
      alternateConfig.standalone === true || String(alternateConfig.mode || "").toLowerCase() === "standalone"
    )) ||
    String(modeMeta || "").trim().toLowerCase() === "standalone";

  if (!markedStandalone) return;

  const runtimeConfig = directMarker && typeof directMarker === "object"
    ? directMarker
    : webuiConfig && typeof webuiConfig === "object"
      ? webuiConfig
      : alternateConfig && typeof alternateConfig === "object"
        ? alternateConfig
        : {};
  const apiBaseMeta = document.querySelector('meta[name="private-companion-api-base"]')?.content;
  const apiBase = String(runtimeConfig.apiBase || runtimeConfig.api_base || apiBaseMeta || "/api/v1")
    .trim()
    .replace(/\/+$/, "") || "/api/v1";
  const root = document.documentElement;
  let overlay = null;
  let loginForm = null;
  let tokenInput = null;
  let submitButton = null;
  let authMessage = null;
  let logoutButton = null;
  let logoutMessage = null;
  let expiryTimer = 0;
  let checkingStatus = false;

  function apiUrl(path) {
    return `${apiBase}/${String(path || "").replace(/^\/+/, "")}`;
  }

  function responseData(payload) {
    if (payload && typeof payload === "object" && payload.data && typeof payload.data === "object") {
      return payload.data;
    }
    return payload && typeof payload === "object" ? payload : {};
  }

  function responseError(payload, status) {
    const data = responseData(payload);
    const message = String(data.error || data.message || payload?.error || payload?.message || "").trim();
    const error = new Error(message || `HTTP ${status}`);
    error.status = status;
    return error;
  }

  async function authRequest(path, options) {
    const requestOptions = options || {};
    const headers = new Headers(requestOptions.headers || {});
    headers.set("Accept", "application/json");
    const response = await fetch(apiUrl(path), {
      ...requestOptions,
      cache: "no-store",
      credentials: "same-origin",
      headers,
    });
    const text = await response.text();
    let payload = {};
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (_error) {
        if (!response.ok) throw responseError({}, response.status);
      }
    }
    if (!response.ok) throw responseError(payload, response.status);
    if (payload && typeof payload === "object" && payload.success === false) {
      throw responseError(payload, response.status || 400);
    }
    return responseData(payload);
  }

  function addStyles() {
    if (document.getElementById("pcStandaloneAuthStyles")) return;
    const style = document.createElement("style");
    style.id = "pcStandaloneAuthStyles";
    style.textContent = `
      html.pc-standalone-auth-pending,
      html.pc-standalone-auth-pending body {
        overflow: hidden !important;
      }

      html:not(.pc-assets-ready) body > .pc-standalone-auth.pc-standalone-auth,
      .pc-standalone-auth {
        position: fixed;
        inset: 0;
        z-index: 2147483000;
        display: grid !important;
        box-sizing: border-box;
        place-items: center;
        overflow: auto;
        padding: max(20px, env(safe-area-inset-top)) max(16px, env(safe-area-inset-right)) max(20px, env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left));
        background: rgba(244, 247, 250, 0.98);
        color: #172033;
        font-family: var(--page-font-family, "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif);
      }

      .pc-standalone-auth[hidden] {
        display: none !important;
      }

      .pc-standalone-auth__dialog {
        width: min(100%, 392px);
        box-sizing: border-box;
        border: 1px solid #d7dee8;
        border-radius: 8px;
        padding: 28px;
        background: #fff;
        box-shadow: 0 18px 48px rgba(23, 32, 51, 0.14);
      }

      .pc-standalone-auth__eyebrow {
        margin: 0 0 8px;
        color: #a53b34;
        font-size: 11px;
        font-weight: 800;
        line-height: 1.4;
        letter-spacing: 0;
      }

      .pc-standalone-auth h1 {
        margin: 0;
        color: #172033;
        font-size: 24px;
        line-height: 1.3;
        letter-spacing: 0;
      }

      .pc-standalone-auth__message {
        min-height: 22px;
        margin: 10px 0 20px;
        color: #596579;
        font-size: 14px;
        line-height: 1.55;
      }

      .pc-standalone-auth__message[data-kind="error"] {
        color: #a12f29;
      }

      .pc-standalone-auth form[hidden] {
        display: none;
      }

      .pc-standalone-auth label {
        display: grid;
        gap: 8px;
        color: #2d3748;
        font-size: 13px;
        font-weight: 700;
      }

      .pc-standalone-auth input {
        width: 100%;
        min-width: 0;
        min-height: 44px;
        box-sizing: border-box;
        border: 1px solid #bcc7d5;
        border-radius: 6px;
        padding: 9px 11px;
        background: #fff;
        color: #172033;
        font: inherit;
        letter-spacing: 0;
        outline: none;
      }

      .pc-standalone-auth input:focus {
        border-color: #245f9e;
        box-shadow: 0 0 0 3px rgba(36, 95, 158, 0.14);
      }

      .pc-standalone-auth button,
      .pc-standalone-logout {
        min-height: 42px;
        box-sizing: border-box;
        border: 1px solid #245f9e;
        border-radius: 6px;
        background: #245f9e;
        color: #fff;
        font: inherit;
        font-size: 13px;
        font-weight: 800;
        letter-spacing: 0;
        cursor: pointer;
      }

      .pc-standalone-auth button {
        width: 100%;
        margin-top: 16px;
        padding: 0 16px;
      }

      .pc-standalone-auth button:hover,
      .pc-standalone-logout:hover {
        border-color: #174978;
        background: #174978;
      }

      .pc-standalone-auth button:focus-visible,
      .pc-standalone-logout:focus-visible {
        outline: 3px solid rgba(36, 95, 158, 0.22);
        outline-offset: 2px;
      }

      .pc-standalone-auth button:disabled,
      .pc-standalone-logout:disabled {
        cursor: wait;
        opacity: 0.66;
      }

      .pc-standalone-logout {
        width: 56px;
        min-height: 42px;
        flex: 0 0 56px;
        padding: 0 8px;
        border-color: #7b8797;
        background: #fff;
        color: #3f4b5d;
      }

      .pc-standalone-logout:hover {
        border-color: #a53b34;
        background: #fff;
        color: #a53b34;
      }

      .pc-standalone-logout-status {
        position: fixed;
        top: max(10px, env(safe-area-inset-top));
        right: max(10px, env(safe-area-inset-right));
        z-index: 2147482000;
        max-width: min(320px, calc(100vw - 20px));
        border: 1px solid #d7dee8;
        border-radius: 6px;
        padding: 8px 10px;
        background: #fff;
        color: #a12f29;
        font-size: 12px;
        line-height: 1.45;
        box-shadow: 0 8px 22px rgba(23, 32, 51, 0.12);
      }

      .pc-standalone-logout-status:empty {
        display: none;
      }

      @media (max-width: 480px) {
        .pc-standalone-auth__dialog {
          padding: 22px 18px;
        }

        .pc-standalone-auth h1 {
          font-size: 22px;
        }

        .pc-standalone-logout {
          flex: 1 1 72px;
          width: auto;
        }
      }
    `;
    document.head.appendChild(style);
  }

  function setAuthMessage(message, kind) {
    if (!authMessage) return;
    authMessage.textContent = message;
    authMessage.dataset.kind = kind || "status";
  }

  function setLoginVisible(visible) {
    if (!overlay || !loginForm) return;
    overlay.hidden = false;
    loginForm.hidden = !visible;
    root.classList.add("pc-standalone-auth-pending");
    if (visible) window.setTimeout(() => tokenInput?.focus(), 0);
  }

  function mountOverlay() {
    if (overlay) return;
    overlay = document.createElement("section");
    overlay.className = "pc-standalone-auth";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-labelledby", "pcStandaloneAuthTitle");
    overlay.innerHTML = `
      <div class="pc-standalone-auth__dialog">
        <p class="pc-standalone-auth__eyebrow">PRIVATE COMPANION</p>
        <h1 id="pcStandaloneAuthTitle">陪伴面板</h1>
        <p class="pc-standalone-auth__message" data-pc-standalone-auth-message role="status" aria-live="polite">正在检查登录状态...</p>
        <form data-pc-standalone-login hidden>
          <label>
            <span>访问令牌</span>
            <input type="password" name="access_token" autocomplete="off" autocapitalize="none" spellcheck="false" required />
          </label>
          <button type="submit">登录</button>
        </form>
      </div>
    `;
    document.body.appendChild(overlay);
    loginForm = overlay.querySelector("[data-pc-standalone-login]");
    tokenInput = loginForm.querySelector('input[name="access_token"]');
    submitButton = loginForm.querySelector('button[type="submit"]');
    authMessage = overlay.querySelector("[data-pc-standalone-auth-message]");
    loginForm.addEventListener("submit", handleLogin);
  }

  function hideOverlay() {
    if (overlay) overlay.hidden = true;
    root.classList.remove("pc-standalone-auth-pending");
  }

  function normalizedExpiry(value) {
    if (value === null || value === undefined || value === "") return 0;
    const numeric = Number(value);
    if (Number.isFinite(numeric) && numeric > 0) return numeric < 1e12 ? numeric * 1000 : numeric;
    const parsed = Date.parse(String(value));
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function scheduleExpiryCheck(expiresAt) {
    if (expiryTimer) window.clearTimeout(expiryTimer);
    expiryTimer = 0;
    const expiry = normalizedExpiry(expiresAt);
    if (!expiry) return;
    const delay = Math.max(1000, Math.min(2147483000, expiry - Date.now() + 250));
    expiryTimer = window.setTimeout(() => {
      expiryTimer = 0;
      void refreshAuthStatus();
    }, delay);
  }

  function installLogoutButton() {
    if (logoutButton || !document.body) return;
    logoutButton = document.createElement("button");
    logoutButton.type = "button";
    logoutButton.className = "pc-standalone-logout";
    logoutButton.textContent = "退出";
    logoutButton.title = "退出独立 WebUI";
    logoutButton.setAttribute("aria-label", "退出登录");
    logoutButton.addEventListener("click", handleLogout);

    const tools = document.querySelector(".folio-tools");
    if (tools) tools.appendChild(logoutButton);
    else document.body.appendChild(logoutButton);

    logoutMessage = document.createElement("div");
    logoutMessage.className = "pc-standalone-logout-status";
    logoutMessage.setAttribute("role", "status");
    logoutMessage.setAttribute("aria-live", "polite");
    document.body.appendChild(logoutMessage);
  }

  function removeLogoutButton() {
    logoutButton?.remove();
    logoutMessage?.remove();
    logoutButton = null;
    logoutMessage = null;
  }

  async function refreshAuthStatus() {
    if (checkingStatus) return;
    checkingStatus = true;
    try {
      const status = await authRequest("/auth/status");
      if (status.authenticated === true) {
        hideOverlay();
        installLogoutButton();
        scheduleExpiryCheck(status.expires_at);
        return;
      }
      removeLogoutButton();
      setLoginVisible(true);
      setAuthMessage("请输入访问令牌", "status");
    } catch (error) {
      removeLogoutButton();
      setLoginVisible(true);
      if (error?.status === 401 || error?.status === 403) {
        setAuthMessage("登录状态已失效，请重新登录", "error");
      } else {
        setAuthMessage("暂时无法检查登录状态，可以直接重试登录", "error");
      }
    } finally {
      checkingStatus = false;
    }
  }

  async function handleLogin(event) {
    event.preventDefault();
    const token = String(tokenInput?.value || "").trim();
    if (!token) {
      setAuthMessage("请输入访问令牌", "error");
      tokenInput?.focus();
      return;
    }
    tokenInput.value = "";
    tokenInput.disabled = true;
    submitButton.disabled = true;
    submitButton.textContent = "登录中...";
    setAuthMessage("正在验证访问令牌...", "status");
    try {
      await authRequest("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      setAuthMessage("登录成功，正在载入面板...", "status");
      window.location.reload();
    } catch (error) {
      const rateLimited = error?.status === 429;
      setAuthMessage(rateLimited ? "尝试次数过多，请稍后再试" : "访问令牌无效或登录失败", "error");
      tokenInput.disabled = false;
      submitButton.disabled = false;
      submitButton.textContent = "登录";
      tokenInput.focus();
    }
  }

  async function handleLogout() {
    if (!logoutButton || logoutButton.disabled) return;
    logoutButton.disabled = true;
    logoutButton.textContent = "退出中";
    if (logoutMessage) logoutMessage.textContent = "";
    try {
      await authRequest("/auth/logout", { method: "POST" });
      window.location.reload();
    } catch (_error) {
      logoutButton.disabled = false;
      logoutButton.textContent = "退出";
      if (logoutMessage) logoutMessage.textContent = "退出失败，请重试";
    }
  }

  function start() {
    addStyles();
    mountOverlay();
    root.classList.add("pc-standalone-auth-pending");
    void refreshAuthStatus();
  }

  window.PrivateCompanionStandaloneAuth = Object.freeze({
    refresh: refreshAuthStatus,
    logout: handleLogout,
  });

  addStyles();
  root.classList.add("pc-standalone-auth-pending");
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
