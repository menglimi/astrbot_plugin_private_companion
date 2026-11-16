/* 小屋缩略卡片：角色切换、拖动与浏览器位置偏好。 */
"use strict";

window.CompanionRoleCard = (() => {
  const MARGIN = 8;
  const DRAG_DISTANCE = 5;

  function clampPosition(position, size, viewport) {
    return {
      left: Math.min(Math.max(MARGIN, position.left), Math.max(MARGIN, viewport.width - size.width - MARGIN)),
      top: Math.min(Math.max(MARGIN, position.top), Math.max(MARGIN, viewport.height - size.height - MARGIN)),
    };
  }

  function mount({root = document, product = window.CompanionProduct, shell = window.CompanionShell} = {}) {
    const region = root.querySelector(".stage-region");
    const scene = root.querySelector("#stageScene");
    const name = root.querySelector("[data-role-card-name]");
    const toggle = root.querySelector("[data-role-card-switch]");
    const menu = root.querySelector("#roleCardMenu");
    const select = root.querySelector("[data-role-card-choice]");
    const status = root.querySelector("[data-role-card-status]");
    const reset = root.querySelector("[data-role-card-reset]");
    if (!region || !scene || !toggle || !product) return null;
    let position = null;
    let storageKey = null;
    let drag = null;
    let suppressClick = false;
    let startedOnControl = false;
    const controls = "button, a, select, input, label";

    const isAway = () => Boolean(shell.section) && !document.body.classList.contains("room-editing");
    const viewport = () => ({width: window.innerWidth, height: window.innerHeight});
    const currentKey = () => product.role() && product.roleTarget()
      ? product.roleChoiceKey({target: product.roleTarget(), role: product.role()}) : "";

    function savePosition() {
      if (!storageKey) return;
      try {
        if (position) window.localStorage.setItem(storageKey, JSON.stringify(position));
        else window.localStorage.removeItem(storageKey);
      } catch (_) { /* 浏览器禁止存储时，本次打开仍可拖动卡片。 */ }
    }

    function applyPosition(next) {
      position = next;
      if (position) {
        region.style.setProperty("--role-card-left", `${position.left}px`);
        region.style.setProperty("--role-card-top", `${position.top}px`);
        region.style.setProperty("--role-card-right", "auto");
        region.style.setProperty("--role-card-bottom", "auto");
      } else {
        for (const side of ["left", "top", "right", "bottom"]) region.style.removeProperty(`--role-card-${side}`);
      }
    }

    function restorePosition() {
      const nextKey = product.preferenceKey("room-card-position");
      if (nextKey === storageKey) return;
      storageKey = nextKey;
      let stored = null;
      try { stored = storageKey ? JSON.parse(window.localStorage.getItem(storageKey) || "null") : null; } catch (_) {}
      applyPosition(stored && Number.isFinite(stored.left) && Number.isFinite(stored.top) ? stored : null);
    }

    function placeMenu() {
      if (menu.hidden) return;
      const anchor = scene.getBoundingClientRect();
      const size = menu.getBoundingClientRect();
      const candidate = {
        left: anchor.right - size.width,
        top: anchor.top - size.height - MARGIN >= MARGIN ? anchor.top - size.height - MARGIN : anchor.bottom + MARGIN,
      };
      const placed = clampPosition(candidate, size, viewport());
      menu.style.left = `${placed.left}px`;
      menu.style.top = `${placed.top}px`;
    }

    function fitPosition() {
      if (!isAway()) return;
      if (position) applyPosition(clampPosition(position, scene.getBoundingClientRect(), viewport()));
      placeMenu();
    }

    function closeMenu({focus = false} = {}) {
      menu.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      if (focus) toggle.focus({preventScroll: true});
    }

    function render() {
      restorePosition();
      const role = product.role();
      name.textContent = role ? product.roleLabel(role) : "尚未选择角色";
      name.title = role ? `${product.accountLabel(product.roleTarget())} · ${product.roleLabel(role)}` : name.textContent;
      const choices = product.roleChoices();
      select.replaceChildren(...choices.map(choice => {
        const option = document.createElement("option");
        option.value = product.roleChoiceKey(choice);
        option.textContent = `${product.accountLabel(choice.target)} · ${product.roleLabel(choice.role)}`;
        return option;
      }));
      select.value = currentKey();
      select.disabled = choices.length < 2;
      status.textContent = choices.length ? "角色设置与资料跟随这里的选择。" : "尚无可选角色。";
      requestAnimationFrame(fitPosition);
    }

    toggle.addEventListener("click", event => {
      event.stopPropagation();
      if (!menu.hidden) { closeMenu(); return; }
      render();
      menu.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
      placeMenu();
      select.focus({preventScroll: true});
    });
    select.addEventListener("change", async () => {
      const key = select.value;
      select.disabled = true;
      try {
        await product.chooseRoleKey(key);
        render();
        if (currentKey() === key) closeMenu({focus: true});
      } catch (error) {
        select.disabled = product.roleChoices().length < 2;
        status.textContent = product.errorLabel?.(error?.code) || "切换角色失败，请重试。";
      }
    });
    reset.addEventListener("click", () => {
      applyPosition(null);
      savePosition();
      closeMenu({focus: true});
    });
    menu.addEventListener("keydown", event => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeMenu({focus: true}); }
    });
    document.addEventListener("pointerdown", event => {
      if (!menu.hidden && !menu.contains(event.target) && !toggle.contains(event.target)) closeMenu();
    });

    scene.addEventListener("pointerdown", event => {
      suppressClick = false;
      startedOnControl = Boolean(event.target.closest(controls));
      if (!isAway() || event.button !== 0 || startedOnControl) return;
      const rect = scene.getBoundingClientRect();
      drag = {id: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top, moved: false};
      scene.setPointerCapture(event.pointerId);
    });
    scene.addEventListener("pointermove", event => {
      if (!drag || drag.id !== event.pointerId) return;
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < DRAG_DISTANCE) return;
      drag.moved = true;
      event.preventDefault();
      region.dataset.roleCardDragging = "true";
      applyPosition(clampPosition({left: drag.left + dx, top: drag.top + dy}, scene.getBoundingClientRect(), viewport()));
      placeMenu();
    });
    function endDrag(event) {
      if (!drag || drag.id !== event.pointerId) return;
      if (drag.moved) { suppressClick = true; savePosition(); }
      drag = null;
      delete region.dataset.roleCardDragging;
      if (scene.hasPointerCapture(event.pointerId)) scene.releasePointerCapture(event.pointerId);
    }
    scene.addEventListener("pointerup", endDrag);
    scene.addEventListener("pointercancel", endDrag);
    scene.addEventListener("lostpointercapture", endDrag);
    scene.addEventListener("click", event => {
      const leftControl = startedOnControl && !event.target.closest(controls);
      startedOnControl = false;
      if ((!suppressClick && !leftControl) || event.detail === 0) return;
      suppressClick = false;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);

    window.addEventListener("resize", fitPosition);
    window.addEventListener("companion:scope-changed", render);
    shell.onChange(() => { closeMenu(); requestAnimationFrame(fitPosition); });
    render();
    return {render, resetPosition: () => { applyPosition(null); savePosition(); }, fitPosition};
  }

  return {mount, clampPosition};
})();
window.CompanionRoleCard.mount();
