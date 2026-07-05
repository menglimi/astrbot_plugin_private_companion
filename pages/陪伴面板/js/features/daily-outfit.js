window.PrivateCompanionDailyOutfit = (() => {
  const PREVIEW_OPEN_CLASS = "daily-outfit-preview-open";
  const MIN_SCALE = 1;
  const MAX_SCALE = 3;
  const SCALE_STEP = 0.16;

  function setPreviewScale(document, scale) {
    const overlay = document.getElementById("dailyOutfitPreview");
    const previewImage = document.getElementById("dailyOutfitPreviewImage");
    if (!overlay || !previewImage) return;
    const nextScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, Number(scale || 1)));
    overlay.dataset.previewScale = String(nextScale);
    previewImage.style.transform = `scale(${nextScale})`;
  }

  function bindPreview(context) {
    const { document } = context;
    const image = document.getElementById("dailyOutfitLogo");
    const overlay = document.getElementById("dailyOutfitPreview");
    const closeButton = document.getElementById("dailyOutfitPreviewClose");
    if (!image || !overlay || image.dataset.previewBound === "1") return;

    image.addEventListener("click", () => openPreview(context));
    image.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openPreview(context);
      }
    });
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay || event.target?.dataset?.previewDismiss === "1") {
        closePreview(context);
      }
    });
    overlay.addEventListener("wheel", (event) => {
      if (!overlay.classList.contains("is-open")) return;
      event.preventDefault();
      const currentScale = Number(overlay.dataset.previewScale || 1);
      const delta = event.deltaY < 0 ? SCALE_STEP : -SCALE_STEP;
      setPreviewScale(document, currentScale + delta);
    }, { passive: false });
    closeButton?.addEventListener("click", () => closePreview(context));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && overlay.classList.contains("is-open")) closePreview(context);
    });
    image.dataset.previewBound = "1";
  }

  function openPreview(context) {
    const { document } = context;
    const image = document.getElementById("dailyOutfitLogo");
    const overlay = document.getElementById("dailyOutfitPreview");
    const previewImage = document.getElementById("dailyOutfitPreviewImage");
    const previewMeta = document.getElementById("dailyOutfitPreviewMeta");
    if (!image || !overlay || !previewImage || !image.getAttribute("src")) return;

    previewImage.src = image.currentSrc || image.src;
    previewImage.alt = image.alt || "今日穿搭照片";
    previewMeta.textContent = overlay.dataset.previewMeta || image.alt || "今日穿搭照片";
    setPreviewScale(document, 1);
    overlay.classList.add("is-open");
    document.body.classList.add(PREVIEW_OPEN_CLASS);
  }

  function closePreview(context) {
    const { document } = context;
    const overlay = document.getElementById("dailyOutfitPreview");
    if (!overlay) return;
    overlay.classList.remove("is-open");
    setPreviewScale(document, 1);
    document.body.classList.remove(PREVIEW_OPEN_CLASS);
  }

  async function hydrate(context) {
    const { state, fetchJson, document } = context;
    const plate = document.querySelector(".folio-plate");
    const image = document.getElementById("dailyOutfitLogo");
    const overlay = document.getElementById("dailyOutfitPreview");
    if (!plate || !image) return;

    bindPreview(context);
    const outfit = state.overview?.daily_outfit || {};
    const endpoint = String(outfit.image_data_url || "");
    if (!outfit.available || !endpoint) {
      plate.classList.remove("has-daily-outfit");
      image.removeAttribute("src");
      image.dataset.source = "";
      image.dataset.loading = "0";
      image.removeAttribute("tabindex");
      image.removeAttribute("title");
      plate.title = "";
      if (overlay) overlay.dataset.previewMeta = "";
      closePreview(context);
      return;
    }
    if (image.dataset.source === endpoint && plate.classList.contains("has-daily-outfit")) return;
    if (image.dataset.loading === "1" && image.dataset.source === endpoint) return;

    image.dataset.source = endpoint;
    image.dataset.loading = "1";
    try {
      const result = await fetchJson(endpoint);
      if (image.dataset.source !== endpoint) return;
      if (!result?.data_url) throw new Error("每日穿搭图片为空");
      image.src = result.data_url;
      image.alt = `今日穿搭照片${outfit.date ? ` · ${outfit.date}` : ""}`;
      image.tabIndex = 0;
      image.title = "点击放大预览，滚轮缩放";
      plate.classList.add("has-daily-outfit");
      const meta = [
        outfit.date ? `每日穿搭：${outfit.date}` : "每日穿搭",
        outfit.backend ? `后端：${outfit.backend}` : "",
        outfit.generated_at ? `生成：${outfit.generated_at}` : "",
      ].filter(Boolean).join(" · ");
      plate.title = meta;
      if (overlay) overlay.dataset.previewMeta = meta;
    } catch (error) {
      plate.classList.remove("has-daily-outfit");
      image.removeAttribute("src");
      image.dataset.source = "";
      image.removeAttribute("tabindex");
      image.removeAttribute("title");
      if (overlay) overlay.dataset.previewMeta = "";
      closePreview(context);
    } finally {
      image.dataset.loading = "0";
    }
  }

  return {
    hydrateDailyOutfitLogo: hydrate,
    openDailyOutfitPreview: openPreview,
    closeDailyOutfitPreview: closePreview,
  };
})();
