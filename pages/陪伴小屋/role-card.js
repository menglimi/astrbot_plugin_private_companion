/* 小屋缩略卡片：角色切换、拖动与浏览器位置偏好。 */
"use strict";

window.CompanionRoleCard = (() => {
  const MARGIN = 8;
  const DRAG_DISTANCE = 5;

  function clampPosition(position, size, viewport) {
    const left = (viewport.left || 0) + MARGIN;
    const top = (viewport.top || 0) + MARGIN;
    return {
      left: Math.min(Math.max(left, position.left), Math.max(left, (viewport.left || 0) + viewport.width - size.width - MARGIN)),
      top: Math.min(Math.max(top, position.top), Math.max(top, (viewport.top || 0) + viewport.height - size.height - MARGIN)),
    };
  }

  function mount({root = document, product = window.CompanionProduct, shell = window.CompanionShell} = {}) {
    const region = root.querySelector(".stage-region");
    const scene = root.querySelector("#stageScene");
    const handle = root.querySelector("[data-role-card-handle]");
    const name = root.querySelector("[data-role-card-name]");
    const toggle = root.querySelector("[data-role-card-switch]");
    const menu = root.querySelector("#roleCardMenu");
    const select = root.querySelector("[data-role-card-choice]");
    const status = root.querySelector("[data-role-card-status]");
    const reset = root.querySelector("[data-role-card-reset]");
    if (!region || !scene || !toggle || !product) return null;
    let position = null;
    let anchoredPosition = null;
    let docked = false;
    let slide = null;
    let preferenceIdentity = null;
    let preferencePosition = "null";
    let drag = null;
    let suppressClick = false;
    let startedOnControl = false;
    let menuKeyboard = false;
    const controls = "button, a, select, input, label";

    const isAway = () => Boolean(shell.section) && !document.body.classList.contains("room-editing");
    const main = root.querySelector(".shell-body");
    const rail = root.querySelector("#homeShell > .rail");
    function contentBounds() {
      const rect = main.getBoundingClientRect();
      const left = Math.max(0, rect.left);
      const right = Math.min(window.innerWidth, rect.right);
      let top = Math.max(0, rect.top);
      const bottom = Math.min(window.innerHeight, rect.bottom);
      if (rail) {
        const navigation = rail.getBoundingClientRect();
        if (navigation.left < right && navigation.right > left && navigation.top <= top) {
          top = Math.max(top, navigation.bottom);
        }
      }
      return {left, top, width: right - left, height: bottom - top};
    }
    const currentKey = () => product.role() && product.roleTarget()
      ? product.roleChoiceKey({target: product.roleTarget(), role: product.role()}) : "";

    function anchorPosition(next) {
      const bounds = contentBounds();
      const size = scene.getBoundingClientRect();
      const placed = clampPosition(next, size, bounds);
      const bottom = Math.max(0, bounds.top + bounds.height - placed.top - size.height);
      return placed.left + size.width / 2 <= bounds.left + bounds.width / 2
        ? {left:Math.max(0, placed.left - bounds.left), bottom}
        : {right:Math.max(0, bounds.left + bounds.width - placed.left - size.width), bottom};
    }

    function savePosition() {
      preferencePosition = JSON.stringify(anchoredPosition);
      return product.saveBrowserPreferences({room_card_position:anchoredPosition});
    }

    function applyPosition(next) {
      position = next;
      if (position) {
        const bounds = contentBounds();
        const anchor = Math.min(Math.max(bounds.left, position.left), bounds.left + bounds.width - scene.offsetWidth);
        const fromLeft = anchoredPosition && "left" in anchoredPosition;
        region.style.setProperty("--role-card-left", fromLeft ? `${anchor}px` : "auto");
        region.style.setProperty("--role-card-offset", `${position.left - anchor}px`);
        region.dataset.roleCardOverflow = String(position.left !== anchor);
        region.style.setProperty("--role-card-top", "auto");
        region.style.setProperty("--role-card-right", fromLeft ? "auto"
          : `${document.documentElement.getBoundingClientRect().width - anchor - scene.offsetWidth}px`);
        region.style.setProperty("--role-card-bottom", `${document.documentElement.clientHeight - position.top - scene.offsetHeight}px`);
      } else {
        region.style.removeProperty("--role-card-offset");
        delete region.dataset.roleCardOverflow;
        for (const side of ["left", "top", "right", "bottom"]) region.style.removeProperty(`--role-card-${side}`);
      }
    }

    function restorePosition() {
      const nextIdentity = product.browserPreferencesIdentity();
      const stored = product.browserPreferences().room_card_position;
      const nextPosition = JSON.stringify(stored);
      if (nextIdentity === preferenceIdentity && nextPosition === preferencePosition) return;
      preferenceIdentity = nextIdentity;
      preferencePosition = nextPosition;
      anchoredPosition = stored && "bottom" in stored ? stored : null;
      applyPosition(null);
    }

    function placeMenu() {
      if (menu.hidden) return;
      const anchor = scene.getBoundingClientRect();
      const size = menu.getBoundingClientRect();
      const candidate = {
        left: anchor.right - size.width,
        top: anchor.top - size.height - MARGIN >= MARGIN ? anchor.top - size.height - MARGIN : anchor.bottom + MARGIN,
      };
      const placed = clampPosition(candidate, size, contentBounds());
      menu.style.left = `${placed.left}px`;
      menu.style.top = `${placed.top}px`;
    }

    function fitPosition() {
      if (!isAway()) {
        setDocked(false);
        return;
      }
      if (drag) return;
      stopSlide();
      const bounds = contentBounds();
      region.style.setProperty("--role-card-max-width", `${Math.max(0, bounds.width - MARGIN * 2)}px`);
      region.style.setProperty("--role-card-max-height", `${Math.max(0, bounds.height - MARGIN * 2)}px`);
      if (!anchoredPosition) applyPosition(null);
      const rect = scene.getBoundingClientRect();
      const candidate = anchoredPosition ? {
        left:"left" in anchoredPosition ? bounds.left + anchoredPosition.left
          : bounds.left + bounds.width - rect.width - anchoredPosition.right,
        top:bounds.top + bounds.height - rect.height - anchoredPosition.bottom,
      } : rect;
      const next = clampPosition(candidate, rect, bounds);
      if (docked) next.left = docked === "left" ? bounds.left - rect.width : bounds.left + bounds.width;
      if (position || next.left !== rect.left || next.top !== rect.top) applyPosition(next);
      placeMenu();
    }

    function setDocked(value) {
      docked = value;
      region.dataset.roleCardDocked = String(Boolean(value));
      const side = value || (drag?.fromHandle && drag.wasDocked) || "right";
      region.dataset.roleCardDockSide = side;
      handle.title = `点击展开，或向${side === "left" ? "右" : "左"}拖出小屋卡片`;
      scene.inert = Boolean(value);
      handle.hidden = !value && !drag?.fromHandle;
      if (value) closeMenu();
    }

    function stopSlide() {
      slide?.cancel();
      slide = null;
      delete region.dataset.roleCardSettling;
    }

    function settle(before) {
      stopSlide();
      const after = scene.getBoundingClientRect();
      const offset = Number.parseFloat(region.style.getPropertyValue("--role-card-offset")) || 0;
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches
          || ["off", "low"].includes(document.documentElement.dataset.motion)) return;
      region.dataset.roleCardSettling = "true";
      slide = scene.animate([
        {transform:`translate(${offset + before.left - after.left}px, ${before.top - after.top}px)`},
        {transform:`translate(${offset}px, 0px)`},
      ], {duration:180, easing:"ease-out"});
      slide.onfinish = () => { slide = null; delete region.dataset.roleCardSettling; };
    }

    function revealCard() {
      const before = scene.getBoundingClientRect();
      setDocked(false);
      fitPosition();
      savePosition();
      settle(before);
      scene.focus({preventScroll:true});
    }

    function closeMenu({focus = false} = {}) {
      menu.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      if (focus && menuKeyboard) toggle.focus({preventScroll: true});
      else if (menu.contains(document.activeElement) || document.activeElement === toggle) document.activeElement.blur();
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
      status.textContent = product.browserPreferencesNotice()
        || (choices.length ? "角色设置与资料跟随这里的选择。" : "尚无可选角色。");
      requestAnimationFrame(fitPosition);
    }

    toggle.addEventListener("click", event => {
      event.stopPropagation();
      if (!menu.hidden) { closeMenu(); return; }
      menuKeyboard = event.detail === 0;
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
      setDocked(false);
      anchoredPosition = null;
      applyPosition(null);
      savePosition();
      fitPosition();
      closeMenu({focus: true});
    });
    menu.addEventListener("keydown", event => {
      menuKeyboard = true;
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeMenu({focus: true}); }
    });
    document.addEventListener("pointerdown", event => {
      menuKeyboard = false;
      if (!menu.hidden && !menu.contains(event.target) && !toggle.contains(event.target)) closeMenu();
    }, true);
    // 嵌入工作区的点击不冒泡到父页面，通过焦点进入 iframe 收起弹窗。
    window.addEventListener("blur", () => {
      setTimeout(() => {
        if (!menu.hidden && document.activeElement?.tagName === "IFRAME") closeMenu();
      }, 0);
    });

    function startDrag(event) {
      suppressClick = false;
      const fromHandle = event.currentTarget === handle;
      startedOnControl = !fromHandle && Boolean(event.target.closest(controls));
      if (!isAway() || event.button !== 0 || startedOnControl) return;
      stopSlide();
      closeMenu();
      const rect = scene.getBoundingClientRect();
      drag = {id:event.pointerId, owner:event.currentTarget, x:event.clientX, y:event.clientY,
        left:rect.left, top:rect.top, moved:false, fromHandle, wasDocked:docked};
      drag.owner.setPointerCapture(event.pointerId);
    }
    function moveDrag(event) {
      if (!drag || drag.id !== event.pointerId) return;
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < DRAG_DISTANCE) return;
      drag.moved = true;
      event.preventDefault();
      region.dataset.roleCardDragging = "true";
      setDocked(false);
      const bounds = contentBounds();
      const next = clampPosition({left:drag.left + dx, top:drag.top + dy}, scene.getBoundingClientRect(), bounds);
      next.left = Math.min(Math.max(bounds.left - scene.offsetWidth, drag.left + dx), bounds.left + bounds.width);
      applyPosition(next);
    }
    function endDrag(event) {
      if (!drag || drag.id !== event.pointerId) return;
      const ended = drag;
      const before = scene.getBoundingClientRect();
      drag = null;
      delete region.dataset.roleCardDragging;
      if (ended.moved) {
        suppressClick = true;
        if (event.type !== "pointerup") {
          setDocked(ended.wasDocked);
          applyPosition({left:ended.left, top:ended.top});
        } else {
          const bounds = contentBounds();
          const right = bounds.left + bounds.width;
          // 使用布局宽度判定半幅，不受变换后浮点尺寸的影响。
          const center = position.left + scene.offsetWidth / 2;
          let side = center <= bounds.left ? "left" : center >= right ? "right" : false;
          if (ended.fromHandle && ended.wasDocked === "left" && position.left > ended.left && side === "left") side = false;
          if (ended.fromHandle && ended.wasDocked === "right" && position.left < ended.left && side === "right") side = false;
          setDocked(side);
        }
        anchoredPosition = anchorPosition(position);
        fitPosition();
        savePosition();
        settle(before);
      } else setDocked(ended.wasDocked);
      if (ended.owner.hasPointerCapture(event.pointerId)) ended.owner.releasePointerCapture(event.pointerId);
      if (!ended.moved && ended.fromHandle && event.type === "pointerup" && event.pointerType === "touch") {
        suppressClick = true;
        revealCard();
      }
    }
    for (const target of [scene, handle]) {
      target.addEventListener("pointerdown", startDrag);
      target.addEventListener("pointermove", moveDrag);
      target.addEventListener("pointerup", endDrag);
      target.addEventListener("pointercancel", endDrag);
      target.addEventListener("lostpointercapture", endDrag);
    }
    handle.addEventListener("click", event => {
      event.stopPropagation();
      if (suppressClick && event.detail !== 0) { suppressClick = false; return; }
      revealCard();
    });
    scene.addEventListener("click", event => {
      const leftControl = startedOnControl && !event.target.closest(controls);
      startedOnControl = false;
      if ((!suppressClick && !leftControl) || event.detail === 0) return;
      suppressClick = false;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);

    window.addEventListener("resize", fitPosition);
    window.addEventListener("scroll", fitPosition, {passive: true});
    const layoutObserver = new ResizeObserver(fitPosition);
    layoutObserver.observe(main);
    if (rail) layoutObserver.observe(rail);
    window.addEventListener("companion:scope-changed", render);
    window.addEventListener("companion:browser-preferences-changed", render);
    shell.onChange(() => { closeMenu(); requestAnimationFrame(fitPosition); });
    render();
    return {render, resetPosition: () => { setDocked(false); anchoredPosition = null; applyPosition(null); savePosition(); fitPosition(); }, fitPosition};
  }

  return {mount, clampPosition};
})();
window.CompanionRoleCard.mount();
