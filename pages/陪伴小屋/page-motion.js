/* 小屋页面的呈现动效。内容与焦点同步提交，动画只作用于已经提交的 DOM。 */
"use strict";
window.CompanionPageMotion = (() => {
  const pages = new WeakMap();
  const animations = new Map();
  const navigation = new WeakMap();
  const systemMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const ease = "cubic-bezier(.22, .72, .24, 1)";
  let observedMode = "";

  function mode() {
    const preference = document.documentElement.dataset.motion;
    if (systemMotion.matches || preference === "off"
      || document.body?.classList.contains("reduce-motion") && preference !== "low") return "off";
    return preference === "low" ? "low" : "full";
  }
  function duration() {
    const value = getComputedStyle(document.documentElement).getPropertyValue("--dur").trim();
    const parsed = parseFloat(value);
    return Number.isFinite(parsed) ? parsed * (value.endsWith("ms") ? 1 : 1000) : 240;
  }
  function cancel(element) {
    animations.get(element)?.cancel();
    animations.delete(element);
  }
  function cancelWithin(host) {
    for (const element of animations.keys()) if (element === host || host.contains(element)) cancel(element);
  }
  function animate(element, frames, options = {}) {
    cancel(element);
    if (!element?.animate || mode() === "off" || !element.getClientRects().length) return null;
    const animation = element.animate(frames, {duration: duration(), easing: ease, fill: "both", ...options});
    animations.set(element, animation);
    const release = () => {
      if (animations.get(element) === animation) animations.delete(element);
      animation.cancel();
    };
    animation.finished.then(release, release);
    return animation;
  }
  function reveal(element, {enabled = true, direction = 1, delay = 0, distance = 7} = {}) {
    if (!enabled || !element || mode() === "off" || document.body.classList.contains("is-entrance")) return;
    /* 外层页面已在入场时，内层标签初始化不再叠加第二遍位移。 */
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      if (animations.has(parent)) return;
    }
    const reduced = mode() === "low";
    const frames = reduced ? [{opacity: .72}, {opacity: 1}]
      : [{opacity: .58, transform: `translate(${direction * distance}px, ${distance}px)`},
        {opacity: 1, transform: "translate(0, 0)"}];
    animate(element, frames, {delay: reduced ? 0 : delay});
  }
  function paintPage(host, key, commit) {
    const previous = pages.get(host);
    cancelWithin(host);
    commit();
    const selected = document.querySelector('#shellNav [aria-pressed="true"][data-shell-section]');
    const links = [...document.querySelectorAll("#shellNav [data-shell-section]")];
    const index = selected ? links.indexOf(selected) : -1;
    pages.set(host, {key, index});
    if (!previous || previous.key === key || host.hidden) return;
    const direction = index < previous.index ? -1 : 1;
    reveal(host.querySelector(".shell-panel-head"), {direction, distance: 3});
    reveal(host.querySelector(".shell-panel-body"), {direction, delay: 24});
  }

  function syncNavigation(host, {motion = true} = {}) {
    if (!host) return;
    let state = navigation.get(host);
    if (!state || state.indicator.parentElement !== host) {
      state?.observer.disconnect();
      const indicator = document.createElement("span");
      indicator.className = "shell-nav-indicator";
      indicator.setAttribute("aria-hidden", "true");
      host.append(indicator);
      const observer = new ResizeObserver(() => syncNavigation(host, {motion: false}));
      observer.observe(host);
      state = {indicator, observer, selected: null};
      navigation.set(host, state);
      host.dataset.motionNav = "";
    }
    const {indicator} = state;
    const selected = host.querySelector('[data-shell-section][aria-pressed="true"]');
    const old = !indicator.hidden && state.selected ? indicator.getBoundingClientRect() : null;
    cancel(indicator);
    indicator.hidden = !selected;
    if (!selected) {state.selected = null; return;}
    const x = selected.offsetLeft, y = selected.offsetTop;
    const width = selected.offsetWidth, height = selected.offsetHeight;
    const transform = `translate(${x}px, ${y}px)`;
    indicator.style.width = `${width}px`;
    indicator.style.height = `${height}px`;
    indicator.style.transform = transform;
    if (motion && old && selected !== state.selected && mode() === "full") {
      const bounds = host.getBoundingClientRect();
      animate(indicator, [
        {transform: `translate(${old.left - bounds.left + host.scrollLeft}px, ${old.top - bounds.top + host.scrollTop}px) scale(${old.width / width}, ${old.height / height})`},
        {transform},
      ]);
    }
    state.selected = selected;
  }
  function stop() {
    for (const element of animations.keys()) cancel(element);
  }
  function preferencesChanged() {
    const current = mode();
    if (current === observedMode) return;
    observedMode = current;
    stop();
    syncNavigation(document.getElementById("shellNav"), {motion: false});
  }
  function bind() {
    observedMode = mode();
    const preferences = new MutationObserver(preferencesChanged);
    preferences.observe(document.documentElement, {attributes: true, attributeFilter: ["data-motion"]});
    preferences.observe(document.body, {attributes: true, attributeFilter: ["class"]});
    systemMotion.addEventListener("change", preferencesChanged);
    window.addEventListener("resize", () => syncNavigation(document.getElementById("shellNav"), {motion: false}));
    document.addEventListener("visibilitychange", () => {if (document.hidden) stop();});
    window.addEventListener("pagehide", stop);
    /* 较早的浏览器保留原生 details 开合，只补展开内容的淡入。 */
    if (!CSS.supports("interpolate-size: allow-keywords") || !CSS.supports("selector(details::details-content)")) {
      document.addEventListener("toggle", event => {
        const details = event.target;
        if (details.tagName === "DETAILS" && details.open && details.closest("#homeShell, dialog")) {
          reveal(details.querySelector(":scope > :not(summary)"), {distance: 3});
        }
      }, true);
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind, {once: true});
  else bind();
  return {mode, paintPage, reveal, syncNavigation, stop};
})();
