window.PrivateCompanionAppearance = (() => {
  const FONT_FAMILIES = new Set([
    "original", "yahei", "dengxian", "source_han", "simsun", "kaiti", "fangsong", "cheng",
  ]);

  function normalizeFontFamily(value) {
    const font = String(value || "original").trim().toLowerCase();
    return FONT_FAMILIES.has(font) ? font : "original";
  }

  function applyFontFamily(value) {
    const font = normalizeFontFamily(value);
    document.documentElement.dataset.pageFont = font;
    try { localStorage.setItem("pc_font", font); } catch (e) {}
    document.querySelectorAll("[data-page-font-select]").forEach((select) => {
      if (select instanceof HTMLSelectElement) select.value = font;
    });
    return font;
  }

  return {
    normalizeFontFamily,
    applyFontFamily,
  };
})();
