window.PrivateCompanionQzonePanel = (() => {
  const state = {
    initialized: false,
    status: null,
    scope: "friends",
    targetUin: "",
    posts: [],
    selectedId: "",
    loading: false,
    loaded: false,
    page: 1,
    pendingLikes: new Set(),
    pendingDeletes: new Set(),
    deleteConfirmId: "",
    deleteConfirmAt: 0,
    detailLoadingId: "",
    context: null,
  };

  function text(value) {
    return String(value ?? "");
  }

  function postById(id) {
    return state.posts.find((item) => item.id === id) || null;
  }

  function setNotice(message = "", tone = "info") {
    const notice = document.getElementById("qzoneNotice");
    if (!notice) return;
    notice.hidden = !message;
    notice.textContent = message;
    notice.dataset.tone = tone;
  }

  function isRouteMissingError(error) {
    return /未找到该路由|找不到该路由|路由不存在|route\s*(?:not\s*found|missing)|not\s*found.*route|^not\s*found$|HTTP\s*404|\b404\b/i.test(String(error?.message || "").trim());
  }

  async function qzoneFetch(paths) {
    const candidates = Array.isArray(paths) ? paths : [paths];
    let lastError = null;
    for (const path of candidates) {
      try {
        return await state.context.fetchJson(path);
      } catch (error) {
        lastError = error;
        if (!isRouteMissingError(error)) break;
      }
    }
    throw lastError || new Error("QQ 空间请求失败");
  }

  async function qzonePost(paths, body = {}) {
    const candidates = Array.isArray(paths) ? paths : [paths];
    let lastError = null;
    for (const path of candidates) {
      try {
        return await state.context.postJson(path, body);
      } catch (error) {
        lastError = error;
        if (!isRouteMissingError(error)) break;
      }
    }
    throw lastError || new Error("QQ 空间请求失败");
  }

  async function refreshCookies() {
    return qzonePost([
      "/qzone/refresh_cookies",
      "/qzone/refresh-cookies",
      "/qzone/cookies/refresh",
      "/qzone/refresh",
      "/qzone/cookie/refresh",
    ], {});
  }

  function renderAccount() {
    const login = state.status?.login || {};
    const summary = state.status?.summary || {};
    const name = document.getElementById("qzoneAccountName");
    const meta = document.getElementById("qzoneAccountMeta");
    const accountName = login.nickname || (login.uin ? `QQ ${login.uin}` : "未绑定");
    if (name) name.textContent = accountName;
    const avatar = document.getElementById("qzoneAccountAvatar");
    const avatarFallback = document.getElementById("qzoneAccountAvatarFallback");
    const avatarUrl = text(login.avatar).trim();
    if (avatar) {
      if (avatarUrl) avatar.src = avatarUrl;
      else avatar.removeAttribute("src");
      avatar.hidden = !avatarUrl;
    }
    if (avatarFallback) {
      avatarFallback.hidden = Boolean(avatarUrl);
      avatarFallback.textContent = Array.from(accountName.trim())[0] || "Q";
    }
    const accountStatus = document.getElementById("qzoneAccountStatus");
    if (accountStatus) {
      const tone = !state.status ? "idle" : (summary.enabled ? "ready" : (summary.available ? "warn" : "error"));
      const label = !state.status ? "同步中" : (summary.enabled ? "已连接" : (summary.available ? "未启用" : "不可用"));
      accountStatus.dataset.tone = tone;
      accountStatus.textContent = label;
    }
    if (meta) {
      meta.textContent = !state.status
        ? "正在读取 QQ 空间状态"
        : summary.enabled
        ? `QQ 空间已启用 · ${summary.last_status || "等待操作"}`
        : (summary.available ? "QQ 空间模块已加载，但整合开关未开启" : (summary.unavailable_reason || "QQ 空间模块不可用"));
    }
    const summaryBox = document.getElementById("qzoneQuickSummary");
    if (summaryBox) {
      const imageStatus = summary.last_life_publish_generated_image_status || "";
      const imageNote = summary.last_life_publish_generated_image_note || "";
      const imageLabel = summary.last_life_publish_images > 0
        ? `${summary.last_life_publish_images} 张`
        : (imageStatus ? imageStatus.replace(/^skipped:/, "跳过:").replace(/^failed:/, "失败:") : `${state.posts.length} 条`);
      const referenceMeta = summary.last_life_publish_generated_image_reference
        ? `参考图${summary.last_life_publish_generated_image_reference_exists ? "可用" : "不可用"}`
        : "";
      const designMeta = [summary.last_life_publish_generated_image_anchor, summary.last_life_publish_generated_image_composition]
        .filter(Boolean)
        .join(" / ");
      const imageMeta = summary.generated_image_enabled
        ? ([imageNote, designMeta, referenceMeta].filter(Boolean).join(" · ") || `配图概率 ${Math.round(Number(summary.generated_image_probability || 0) * 100)}%`)
        : "说说配图关闭";
      summaryBox.innerHTML = `
        <article data-state="${summary.life_publish_enabled ? "ready" : "idle"}"><span>生活说说</span><b>${summary.life_publish_enabled ? "开启" : "关闭"}</b></article>
        <article data-state="${summary.comment_inbox_enabled ? "ready" : "idle"}"><span>评论收件箱</span><b>${summary.comment_inbox_enabled ? "开启" : "关闭"}</b></article>
        <article data-state="info" title="${state.context.escapeHtml(imageMeta)}"><span>最近配图</span><b>${state.context.escapeHtml(imageLabel)}</b></article>
      `;
    }
  }

  function postAuthorName(post) {
    return post.author?.nickname || post.author?.uin || "QQ空间用户";
  }

  function renderPostIdentity(post) {
    const { escapeHtml } = state.context;
    const author = postAuthorName(post);
    const initial = Array.from(String(author).trim())[0] || "Q";
    return `
      <div class="qzone-post-identity">
        <span class="qzone-author-mark" aria-hidden="true">${escapeHtml(initial)}</span>
        <div>
          <b>${escapeHtml(author)}</b>
          <small>${escapeHtml(post.created_at_text || "刚刚")}</small>
        </div>
      </div>
    `;
  }

  function renderPostMetrics(post) {
    const { escapeHtml } = state.context;
    return `
      <div class="qzone-post-feedback" aria-label="互动数据">
        <span>${escapeHtml(post.stats?.likes ?? 0)} 赞</span>
        <span>${escapeHtml(post.stats?.comments ?? 0)} 评论</span>
      </div>
    `;
  }

  function qzoneImageItems(post) {
    const rawItems = Array.isArray(post?.image_items) && post.image_items.length
      ? post.image_items
      : (Array.isArray(post?.images) ? post.images : []);
    return rawItems.map((item) => {
      if (item && typeof item === "object") {
        const previewUrl = text(item.preview_url || item.thumbnail_url || item.thumb_url || item.url || item.src || item.image_url).trim();
        const fullUrl = text(item.full_url || item.original_url || item.origin_url || item.raw_url || item.large_url || item.url || item.src || item.image_url).trim();
        return { previewUrl: previewUrl || fullUrl, fullUrl: fullUrl || previewUrl };
      }
      const source = text(item).trim();
      return { previewUrl: source, fullUrl: source };
    }).filter((item) => item.previewUrl || item.fullUrl);
  }

  function renderPreviewImage(item, post) {
    const { escapeHtml } = state.context;
    const author = postAuthorName(post);
    const alt = `${author}的说说图片`;
    const meta = ["QQ 空间图片", author, post.created_at_text || "刚刚"].filter(Boolean).join(" · ");
    const previewUrl = text(item?.previewUrl).trim();
    const fullUrl = text(item?.fullUrl || previewUrl).trim();
    if (!previewUrl || !fullUrl) return "";
    return `<img class="qzone-preview-image" src="${escapeHtml(previewUrl)}" alt="${escapeHtml(alt)}" loading="lazy" data-qzone-preview-image data-qzone-preview-src="${escapeHtml(fullUrl)}" data-qzone-preview-fallback-src="${escapeHtml(previewUrl)}" data-qzone-preview-meta="${escapeHtml(meta)}" tabindex="0" role="button" title="点击放大预览，滚轮缩放" />`;
  }

  function openImagePreview(image) {
    if (!image || !state.context) return false;
    const preview = window.PrivateCompanionDailyOutfit?.openImagePreview;
    const source = image.dataset.qzonePreviewSrc || image.currentSrc || image.src || image.getAttribute("src") || "";
    const fallbackSrc = image.dataset.qzonePreviewFallbackSrc || image.currentSrc || image.src || image.getAttribute("src") || "";
    if (!preview || !source) return false;
    return preview({ ...state.context, document }, {
      src: source,
      fallbackSrc,
      alt: image.alt || "QQ 空间图片",
      meta: image.dataset.qzonePreviewMeta || image.alt || "QQ 空间图片",
    });
  }

  function renderFeed() {
    const feed = document.getElementById("qzoneFeed");
    const meta = document.getElementById("qzoneFeedMeta");
    const feedState = document.getElementById("qzoneFeedState");
    if (!feed || !meta) return;
    if (feedState) {
      feedState.dataset.state = state.loading ? "loading" : (state.loaded ? "ready" : "idle");
      feedState.textContent = state.loading ? "同步中" : (state.loaded ? "已同步" : "待同步");
    }
    meta.textContent = state.loading
      ? "正在同步动态..."
      : `${state.scope === "profile" ? (state.targetUin || "指定 QQ") : state.scope === "friends" ? "好友动态" : "我的空间"} · ${state.posts.length} 条`;
    if (!state.posts.length) {
      feed.innerHTML = state.loading
        ? `<div class="qzone-feed-skeleton" role="status" aria-label="正在同步空间动态"><span></span><span></span><span></span></div>`
        : `<div class="qzone-empty"><b>暂无可显示的说说</b><span>可以先刷新，或切换到指定 QQ 试试。</span></div>`;
      renderDetail();
      return;
    }
    const { escapeHtml } = state.context;
    feed.innerHTML = state.posts.map((post) => `
      <article class="qzone-post-card ${state.selectedId === post.id ? "is-active" : ""}" data-qzone-open="${escapeHtml(post.id)}">
        <header>
          ${renderPostIdentity(post)}
          <span class="qzone-post-badge">${post.can_delete ? "我的说说" : "动态"}</span>
        </header>
        <p class="qzone-post-text">${escapeHtml(post.content || "无正文")}</p>
        ${qzoneImageItems(post).length ? `
          <div class="qzone-post-media">
            ${qzoneImageItems(post).slice(0, 4).map((item) => renderPreviewImage(item, post)).join("")}
          </div>
        ` : ""}
        <footer>
          ${renderPostMetrics(post)}
          <div class="qzone-post-actions">
            <button type="button" data-qzone-like="${escapeHtml(post.id)}" ${state.pendingLikes.has(post.id) ? "disabled" : ""}>点赞</button>
            <button type="button" data-qzone-open="${escapeHtml(post.id)}">评论</button>
            ${post.can_delete ? `<button type="button" class="danger-outline" data-qzone-delete="${escapeHtml(post.id)}" ${state.pendingDeletes.has(post.id) ? "disabled" : ""}>删除</button>` : ""}
          </div>
        </footer>
      </article>
    `).join("");
    renderDetail();
  }

  function renderDetail() {
    const empty = document.getElementById("qzoneDetailEmpty");
    const detail = document.getElementById("qzoneDetailContent");
    if (!empty || !detail) return;
    const post = postById(state.selectedId);
    if (!post) {
      empty.hidden = false;
      detail.hidden = true;
      detail.innerHTML = "";
      return;
    }
    empty.hidden = true;
    detail.hidden = false;
    const { escapeHtml } = state.context;
    detail.innerHTML = `
      <div class="qzone-detail-card">
        <div class="qzone-detail-head">
          ${renderPostIdentity(post)}
          <div class="qzone-detail-actions">
            <button type="button" data-qzone-like="${escapeHtml(post.id)}" ${state.pendingLikes.has(post.id) ? "disabled" : ""}>点赞这条</button>
            ${post.can_delete ? `<button type="button" class="danger-outline" data-qzone-delete="${escapeHtml(post.id)}" ${state.pendingDeletes.has(post.id) ? "disabled" : ""}>删除说说</button>` : ""}
          </div>
        </div>
        <p class="qzone-detail-text">${escapeHtml(post.content || "无正文")}</p>
        ${qzoneImageItems(post).length ? `
          <div class="qzone-detail-media">
            ${qzoneImageItems(post).map((item) => renderPreviewImage(item, post)).join("")}
          </div>
        ` : ""}
        ${renderPostMetrics(post)}
      </div>
      <div class="qzone-comment-block">
        <div class="qzone-comment-head">
          <b>全部评论</b>
          <span>${escapeHtml(post.comments?.length ?? 0)} 条</span>
        </div>
        <div class="qzone-comment-list">
          ${(post.comments || []).length ? post.comments.map((comment) => `
            <article class="qzone-comment-item">
              <b>${escapeHtml(comment.author?.nickname || comment.author?.uin || "QQ空间用户")}</b>
              <p>${escapeHtml(comment.content || "")}</p>
            </article>
          `).join("") : `<div class="qzone-empty compact"><span>还没有评论。</span></div>`}
        </div>
        <form id="qzoneCommentForm" class="qzone-comment-form">
          <textarea id="qzoneCommentInput" rows="3" placeholder="写一条公开评论"></textarea>
          <button type="submit">发送评论</button>
        </form>
      </div>
    `;
    const form = document.getElementById("qzoneCommentForm");
    form?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const input = document.getElementById("qzoneCommentInput");
      const content = input?.value || "";
      await sendComment(content);
      if (input) input.value = "";
    });
  }

  async function loadStatus(force = false) {
    if (state.status && !force) return state.status;
    try {
      state.status = await qzoneFetch(["/qzone/status", "/qzone/summary", "/qzone/state", "/qzone/health"]);
      renderAccount();
      return state.status;
    } catch (error) {
      state.status = state.status || { login: { bound: false, uin: 0, nickname: "", avatar: "" }, summary: { enabled: false, available: false } };
      setNotice(`状态加载失败：${error.message}`, "error");
      renderAccount();
      throw error;
    }
  }

  async function loadFeed(force = false) {
    if (state.scope === "profile" && !state.targetUin.trim()) {
      state.posts = [];
      state.loaded = false;
      state.loading = false;
      setNotice("输入 QQ 号后查看指定空间。", "warn");
      renderFeed();
      return state.posts;
    }
    if (state.loaded && !force) {
      renderFeed();
      return state.posts;
    }
    state.loading = true;
    setNotice("", "info");
    renderFeed();
    try {
      const params = new URLSearchParams();
      params.set("scope", state.scope);
      params.set("page", String(state.page || 1));
      if (state.scope === "profile" && state.targetUin.trim()) params.set("hostuin", state.targetUin.trim());
      const query = params.toString();
      const payload = await qzoneFetch([
        `/qzone/feed?${query}`,
        `/qzone/feeds?${query}`,
        `/qzone/list?${query}`,
      ]);
      state.posts = Array.isArray(payload.items) ? payload.items : [];
      state.loaded = true;
      if (!state.selectedId && state.posts[0]) state.selectedId = state.posts[0].id;
      renderAccount();
      renderFeed();
      return state.posts;
    } catch (error) {
      state.posts = [];
      state.loaded = false;
      state.selectedId = "";
      setNotice(`动态加载失败：${error.message}`, "error");
      renderFeed();
      throw error;
    } finally {
      state.loading = false;
      renderFeed();
    }
  }

  async function openDetail(id) {
    if (!id) return;
    state.selectedId = id;
    state.detailLoadingId = id;
    renderFeed();
    try {
      const encodedId = encodeURIComponent(id);
      const payload = await qzoneFetch([
        `/qzone/detail?id=${encodedId}`,
        `/qzone/post?id=${encodedId}`,
        `/qzone/item?id=${encodedId}`,
      ]);
      const post = payload.post;
      if (post) {
        state.posts = state.posts.map((item) => (item.id === id ? post : item));
      }
    } finally {
      state.detailLoadingId = "";
      renderFeed();
    }
  }

  async function likePost(id) {
    const post = postById(id);
    if (!post) return;
    state.pendingLikes.add(id);
    renderFeed();
    try {
      const payload = await qzonePost(["/qzone/like", "/qzone/post/like"], { id });
      state.posts = state.posts.map((item) => (
        item.id === id
          ? {
              ...item,
              liked: Boolean(payload?.liked),
              stats: {
                ...(item.stats || {}),
                likes: Number(item.stats?.likes || 0) + (!item.liked && payload?.liked ? 1 : 0),
              },
            }
          : item
      ));
      state.context.showToast(payload?.verified ? "点赞已确认" : (payload?.verify_message || "已发送点赞请求"));
    } catch (error) {
      state.context.showToast(`点赞失败：${error.message}`, "error");
    } finally {
      state.pendingLikes.delete(id);
      renderFeed();
    }
  }

  async function sendComment(content) {
    const post = postById(state.selectedId);
    const clean = text(content).trim();
    if (!post || !clean) return;
    try {
      const payload = await qzonePost(["/qzone/comment", "/qzone/post/comment"], { id: post.id, content: clean });
      if (payload?.post) {
        state.posts = state.posts.map((item) => (item.id === post.id ? payload.post : item));
      }
      state.context.showToast("评论已发送");
      renderFeed();
    } catch (error) {
      state.context.showToast(`评论失败：${error.message}`, "error");
    }
  }

  function confirmDeletePost(id, button = null) {
    const now = Date.now();
    if (state.deleteConfirmId === id && now - state.deleteConfirmAt <= 6000) return true;
    state.deleteConfirmId = id;
    state.deleteConfirmAt = now;
    if (button) {
      if (!button.dataset.originalText) button.dataset.originalText = button.textContent || "删除";
      button.textContent = "再次点击删除";
      button.classList.add("is-confirming");
      window.setTimeout(() => {
        if (state.deleteConfirmId === id && Date.now() - state.deleteConfirmAt >= 5900) {
          state.deleteConfirmId = "";
          state.deleteConfirmAt = 0;
        }
        if (button.isConnected && button.dataset.originalText) {
          button.textContent = button.dataset.originalText;
          delete button.dataset.originalText;
          button.classList.remove("is-confirming");
        }
      }, 6000);
    }
    state.context.showToast("再次点击会删除这条说说", "warn");
    return false;
  }

  async function deletePost(id, button = null) {
    const cleanId = text(id).trim();
    if (!cleanId) {
      state.context.showToast("没有拿到说说 ID，请刷新动态后重试", "error");
      return;
    }
    const post = postById(cleanId);
    if (!post) {
      state.context.showToast("这条说说的页面引用已失效，请刷新动态后重试", "error");
      return;
    }
    if (!post.can_delete) {
      state.context.showToast("只能删除当前登录 QQ 自己发布的说说", "error");
      return;
    }
    if (state.pendingDeletes.has(cleanId)) return;
    if (!confirmDeletePost(cleanId, button)) return;
    state.deleteConfirmId = "";
    state.deleteConfirmAt = 0;
    state.pendingDeletes.add(cleanId);
    renderFeed();
    try {
      state.context.showToast("正在删除说说...");
      await qzonePost(["/qzone/delete", "/qzone/post/delete"], { id: cleanId });
      state.posts = state.posts.filter((item) => item.id !== cleanId);
      if (state.selectedId === cleanId) state.selectedId = state.posts[0]?.id || "";
      state.context.showToast("说说已删除");
    } catch (error) {
      state.context.showToast(`删除失败：${error.message}`, "error");
    } finally {
      state.pendingDeletes.delete(cleanId);
      renderAccount();
      renderFeed();
    }
  }

  async function publish() {
    const input = document.getElementById("qzonePublishContent");
    const content = text(input?.value).trim();
    if (!content) {
      state.context.showToast("说说内容不能为空", "error");
      return;
    }
    try {
      await qzonePost(["/qzone/publish", "/qzone/post/publish", "/qzone/post"], { content });
      if (input) input.value = "";
      state.context.showToast("说说已发布");
      await loadFeed(true);
    } catch (error) {
      state.context.showToast(`发布失败：${error.message}`, "error");
    }
  }

  function bindDeleteEvents() {
    const panel = document.getElementById("panel-qzone");
    if (!panel || panel.dataset.qzoneDeleteBound === "1") return;
    panel.dataset.qzoneDeleteBound = "1";
    panel.addEventListener("click", async (event) => {
      const element = event.target instanceof Element ? event.target : null;
      const deleteButton = element?.closest("[data-qzone-delete]");
      if (!deleteButton || !panel.contains(deleteButton)) return;
      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === "function") event.stopImmediatePropagation();
      await deletePost(deleteButton.dataset.qzoneDelete || "", deleteButton);
    }, true);
  }

  function bindEvents() {
    const panel = document.getElementById("panel-qzone");
    if (!panel || panel.dataset.bound === "1") return;
    panel.dataset.bound = "1";
    panel.addEventListener("click", async (event) => {
      const element = event.target instanceof Element ? event.target : null;
      const previewImage = element?.closest("[data-qzone-preview-image]");
      if (previewImage && panel.contains(previewImage)) {
        event.preventDefault();
        event.stopPropagation();
        openImagePreview(previewImage);
        return;
      }
      const like = element?.closest("[data-qzone-like]");
      if (like) {
        await likePost(like.dataset.qzoneLike || "");
        return;
      }
      const deleteButton = element?.closest("[data-qzone-delete]");
      if (deleteButton) {
        event.preventDefault();
        event.stopPropagation();
        await deletePost(deleteButton.dataset.qzoneDelete || "", deleteButton);
        return;
      }
      const open = element?.closest("[data-qzone-open]");
      if (open) {
        await openDetail(open.dataset.qzoneOpen || "");
        return;
      }
      const scopeButton = element?.closest("[data-qzone-scope]");
      if (scopeButton) {
        state.scope = scopeButton.dataset.qzoneScope || "self";
        state.page = 1;
        document.querySelectorAll("[data-qzone-scope]").forEach((item) => {
          const active = item === scopeButton;
          item.classList.toggle("active", active);
          item.setAttribute("aria-pressed", String(active));
        });
        state.loaded = false;
        try {
          await loadFeed(true);
        } catch (error) {
          state.context.showToast(`动态加载失败：${error.message}`, "error");
        }
        return;
      }
    });
    panel.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const element = event.target instanceof Element ? event.target : null;
      const previewImage = element?.closest("[data-qzone-preview-image]");
      if (!previewImage || !panel.contains(previewImage)) return;
      event.preventDefault();
      event.stopPropagation();
      openImagePreview(previewImage);
    });
    document.getElementById("qzoneTargetForm")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const target = document.getElementById("qzoneTargetUin");
      state.targetUin = text(target?.value).trim();
      state.scope = "profile";
      state.page = 1;
      document.querySelectorAll("[data-qzone-scope]").forEach((item) => {
        const active = item.dataset.qzoneScope === "profile";
        item.classList.toggle("active", active);
        item.setAttribute("aria-pressed", String(active));
      });
      state.loaded = false;
      try {
        await loadFeed(true);
      } catch (error) {
        state.context.showToast(`查询失败：${error.message}`, "error");
      }
    });
    document.getElementById("qzonePublishForm")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      await publish();
    });
    document.getElementById("qzoneRefreshBtn")?.addEventListener("click", async () => {
      try {
        await loadStatus(true);
        await loadFeed(true);
      } catch (error) {
        state.context.showToast(`刷新失败：${error.message}`, "error");
      }
    });
    document.getElementById("qzoneRefreshCookiesBtn")?.addEventListener("click", async () => {
      try {
        const payload = await refreshCookies();
        state.context.showToast(`Cookies 已刷新：QQ ${payload?.uin || "未知"}`);
        await loadStatus(true);
        await loadFeed(true);
      } catch (error) {
        setNotice(`刷新 Cookies 失败：${error.message}`, "error");
        state.context.showToast(`刷新 Cookies 失败：${error.message}`, "error");
      }
    });
    document.getElementById("qzoneAccountAvatar")?.addEventListener("error", (event) => {
      const avatar = event.currentTarget;
      const avatarFallback = document.getElementById("qzoneAccountAvatarFallback");
      avatar.hidden = true;
      if (avatarFallback) avatarFallback.hidden = false;
    });
  }

  async function render(context) {
    state.context = context;
    bindDeleteEvents();
    bindEvents();
    renderAccount();
    renderFeed();
    try {
      await loadStatus();
      if (state.status?.summary?.platform_supported === false) {
        state.posts = [];
        state.loaded = true;
        renderFeed();
        setNotice(state.status.summary.unavailable_reason || "当前平台不支持 QQ 空间。", "warn");
        return;
      }
      await loadFeed();
    } catch (error) {
      setNotice(error.message || "QQ 空间面板初始化失败", "error");
    }
  }

  return { render };
})();
