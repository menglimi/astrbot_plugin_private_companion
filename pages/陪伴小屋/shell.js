/* 陪伴小屋外壳：左栏导航 + 首页小屋 + 下方面板。
 *
 * 与旧外壳的关键差别，也是这个文件存在的理由：
 *
 *   1. 小屋只留在首页，但它是外壳栅格的一格，不是 #main 的内容。
 *      首页内的面板切换（点家具、看家具目录、看生活观察）不动它——
 *      体素控制器因此不会被销毁重建。旧结构把小屋放在 life/home 路由下，
 *      每次 render() 都要 dispose 控制器，回来再 mount 一次，
 *      既丢掉相机位姿也重付一次加载成本。
 *      点了左栏的配置分区就是离开首页，小屋随之收起（见 paint 里的 away）。
 *
 *   2. 家具管镜头，左侧导航管配置。点家具只改「选中项」与相机机位，
 *      下方面板跟着换成那件家具的用途说明；点左侧导航才换配置编辑器。
 *      两套选择各自记住，互相不覆盖。
 *
 *   3. 共享目录加载后，顶栏显示当前对象与全局工具；目录加载前先收起。
 *      搜索与显示偏好移入左栏底部。
 */
"use strict";

window.CompanionShell = (() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const I = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;

  /* 有真实机位、可以在场景里选中并飞过去的家具。
     这是体素包机位表的子集——包里有七个机位，其中 wardrobe 是房间陈设，
     其余六个各对应一件有用途的家具。清单与包不一致会静默回落全景，
     tests/test_entrance.cjs 里有一条对表断言盯着这件事。 */
  const STAGE_STATIONS = ["desk", "calendar", "shelf", "bed", "radio", "garden", "wardrobe"];

  const state = {
    /* 当前选中的家具（机位 id）。null 表示看全景。 */
    furniture: null,
    /* 当前选中的配置分区。null 表示下方面板不显示配置。 */
    section: null,
    /* 面板的整页视图：家具目录、生活观察、日程、日记。
       它们不属于任何一件家具，也不属于某一项配置分区——
       是「从家具入口点进来的整页内容」。 */
    view: null,
    /* 左栏是否收起（窄屏时用）。 */
    railOpen: false,
  };

  /* 合法的整页视图。清单固定，避免拼错的名字静默渲染出首页。 */
  const VIEWS = ["directory", "observation", "state", "diary"];

  const subscribers = new Set();
  function onChange(handler) {
    subscribers.add(handler);
    return () => subscribers.delete(handler);
  }
  function emit() {
    for (const handler of subscribers) {
      try { handler({...state}); } catch (error) { console.warn("外壳订阅者出错：", error); }
    }
  }

  /* 选中一件家具。furniture 为 null 时回到全景。
     返回是否真的发生了改变，便于调用方决定要不要飞镜头。 */
  function selectFurniture(id, {focus = true} = {}) {
    const next = STAGE_STATIONS.includes(id) ? id : null;
    const changed = next !== state.furniture;
    state.furniture = next;
    /* 选了家具就离开配置与整页视图——否则下方面板会同时想显示几样东西。 */
    if (next) { state.section = null; state.view = null; }
    if (changed) emit();
    return {changed, id: next, focus};
  }

  /* 选中一个配置分区。section 为 null 时回到上一级。
     与 selectFurniture / selectView 一致地带判重：重复选中同一项不触发订阅者。
     少了这一道，面板会被无谓地重绘一次——配置编辑器重绘会把用户正在填的
     输入框重建，光标与未提交的内容一起丢。 */
  function selectSection(name) {
    const next = name || null;
    const changed = next !== state.section;
    state.section = next;
    /* 进了配置就不再看家具说明，但镜头不动——用户可能还想看着小屋改设置。 */
    if (next) { state.furniture = null; state.view = null; }
    if (changed) emit();
    return {changed, section: state.section};
  }

  /* 打开展整页视图（家具目录、生活观察、日程、日记）。 */
  function selectView(name) {
    const next = VIEWS.includes(name) ? name : null;
    const changed = next !== state.view;
    state.view = next;
    if (next) { state.section = null; state.furniture = null; }
    if (changed) emit();
    return {changed, view: state.view};
  }

  function snapshot() { return {...state}; }

  /* 把外壳上的数据属性同步到 DOM。视觉全部由 CSS 依据这些属性决定，
     不在 JS 里写行内样式——属性是状态，样式是表现。 */
  function paint(root = document) {
    const shell = $("#homeShell", root);
    if (!shell) return;
    shell.dataset.selected = state.furniture || "";
    shell.dataset.section = state.section || "";
    /* 小屋只留在首页。选了一项配置分区就是离开首页去看那一项设置，
       小屋与右栏状态卡都不该跟着——它们是首页的东西。
       家具说明与家具目录/生活观察仍属于首页，所以只有 section 会触发离开。 */
    shell.dataset.away = String(Boolean(state.section));
  }

  /* 左栏导航。三组都有标题：此前第一组没有分组名，「关系网」到「治理与维护」
     六项悬在左栏顶部，而下面两组却有「能力配置」「设置」——同一列里一半有
     标题一半没有，看起来像漏渲染。
     「状态与日程」「日记与生活」不再单列成组：它们本来就能从首页右栏和家具
     进入，单开一组把左栏撑到要滚动。 */
  const RAIL_GROUPS = [
    ["专题配置", [
      ["identity", "network", "关系网"],
      ["contact", "heart-handshake", "主动与联系"],
      ["memory", "orbit", "事实与经历"],
      ["learning", "graduation-cap", "学习与反思"],
      ["models", "sliders-horizontal", "模型与连接"],
      ["maintenance", "settings-2", "治理与维护"],
      ["phone", "smartphone", "手机使用"],
      ["mobile", "link-2", "手机端连接"],
    ]],
    ["能力配置", [
      ["perception", "scan-eye", "感知"],
      ["understanding", "brain-circuit", "理解"],
      ["decision", "brain", "决策"],
      ["action", "play", "行动"],
      ["output", "shapes", "产出"],
      ["expression", "message-square-more", "表达"],
      ["governance", "user-round", "自我"],
    ]],
    ["设置", [
      ["setup", "rocket", "开始使用"],
      ["appearance", "palette", "外观与设置"],
      ["host", "power", "账号与会话"],
      ["data", "database", "资料与维护"],
    ]],
  ];

  /* 导航的唯一一份分组数据。左栏和「全部功能」浮层都读它，
     免得两处各维护一套名字（曾经浮层里叫「学习与成长」「资料与备份」，
     左栏叫「学习与反思」「资料与维护」，同一个页面两个名字）。 */
  function railGroups() {
    return RAIL_GROUPS.map(([group, items]) => [group, items.map(([id, icon, label, kind]) => ({
      id, icon, label, view: kind === "view",
    }))]);
  }

  function railHTML() {
    return RAIL_GROUPS.map(([group, items]) => `
      <div class="shell-nav-group">
        <span class="shell-nav-title">${group}</span>
        ${items.map(([id, icon, label, kind]) => {
          const isView = kind === "view";
          const active = isView ? state.view === id : state.section === id;
          const action = isView ? `data-shell-view="${id}"` : `data-shell-section="${id}"`;
          return `
          <button class="shell-nav-link" ${action}
                  aria-pressed="${active}" ${active ? 'aria-current="page"' : ""} title="${label}">
            ${I(icon)}<span>${label}</span>
          </button>`;
        }).join("")}
      </div>`).join("");
  }

  return {
    $, $$, I,
    STAGE_STATIONS, VIEWS,
    onChange, emit, paint, railHTML, railGroups,
    selectFurniture, selectSection, selectView, snapshot,
    get furniture() { return state.furniture; },
    get section() { return state.section; },
    get view() { return state.view; },
  };
})();
