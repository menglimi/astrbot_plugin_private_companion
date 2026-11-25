"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const PAGE_DIR = path.join(ROOT, "pages", "陪伴小屋");
const readPage = name => fs.readFileSync(path.join(PAGE_DIR, name), "utf8").replace(/\r\n/g, "\n");
const stripComments = css => css.replace(/\/\*[\s\S]*?\*\//g, "");

/* ---------- 需求 4：顶栏操作簇统一 & 「每日巡视设置」移入 Header ---------- */

test("需求 4：panelHead 统一使用右侧 .shell-panel-actions，「每日巡视设置」位于治理与维护顶栏「回到小屋」左侧", () => {
    const appJs = readPage("app.js");
    const shellCss = stripComments(readPage("shell.css"));
    const maintCss = stripComments(readPage("maintenance-page.css"));

    const start = appJs.indexOf("function panelHead(");
    const end = appJs.indexOf("const FURNITURE_WORKSPACES=", start);
    assert.ok(start > 0 && end > start, "应能定位 panelHead 定义");
    const panelHeadSrc = appJs.slice(start, end);

    const sandbox = {
        I: name => `<i data-lucide="${name}" aria-hidden="true"></i>`,
        button: (text, attrs = "", kind = "") => `<button class="button ${kind}" ${attrs}>${text}</button>`,
    };
    vm.runInNewContext(panelHeadSrc, sandbox);

    const maintHtml = sandbox.panelHead(
        "治理与维护",
        "账号与会话运行状态。",
        "settings-2",
        sandbox.button("回到小屋", 'data-shell-home'),
        '<button class="button small shell-panel-secondary-action" type="button" data-maint-open-patrol><i data-lucide="clipboard-check"></i><span>每日巡视设置</span></button>',
    );
    assert.match(maintHtml, /<div class="shell-panel-actions">/);
    assert.doesNotMatch(maintHtml, /panel-spacer/);
    assert.ok(
        maintHtml.indexOf("data-maint-open-patrol") < maintHtml.indexOf("data-shell-home"),
        "「每日巡视设置」应排在「回到小屋」左侧",
    );

    const exprHtml = sandbox.panelHead(
        "表达",
        "说话风格与语音朗读。",
        "settings-2",
        sandbox.button("回到小屋", 'data-shell-home'),
        '<a class="text-button reply-diagnostics-link" href="#manage/maintenance/reply-diagnostics">查看本会话回复诊断</a>',
    );
    assert.match(exprHtml, /<div class="shell-panel-actions">[\s\S]*reply-diagnostics-link[\s\S]*data-shell-home[\s\S]*<\/div>/);

    assert.match(
        appJs,
        /id === 'maintenance' && maintenancePane !== 'patrol' && maintenancePane !== 'reply-diagnostics'[\s\S]*data-maint-open-patrol/,
        "治理与维护仅在非巡视/非回复诊断子页时于顶栏展示「每日巡视设置」",
    );
    assert.doesNotMatch(appJs, /maintenance-settings-entry/, "正文不再渲染独立占行的 .maintenance-settings-entry");
    assert.doesNotMatch(maintCss, /\.maintenance-settings-entry\b/, "maintenance-page.css 不应残留 .maintenance-settings-entry");
    assert.doesNotMatch(shellCss, /\.panel-spacer\b/, "shell.css 不应残留割裂的 .panel-spacer");
    assert.match(shellCss, /\.shell-panel-actions\s*\{[^}]*margin-left:\s*auto[^}]*display:\s*flex[^}]*flex-wrap:\s*wrap/s);
    assert.match(shellCss, /\.shell-panel-head\s+\.shell-panel-secondary-action\s*\{[^}]*color:\s*var\(--t-text-soft\)/s);
    assert.match(shellCss, /\.shell-panel-head\s+\.reply-diagnostics-link\s*\{[^}]*white-space:\s*nowrap/s);
    assert.match(
        shellCss,
        /@media\s*\(\s*max-width:\s*720px\s*\)\s*\{[\s\S]*?\.shell-panel-head\s*\{[^}]*flex-wrap:\s*wrap[\s\S]*?\.shell-panel-actions\s*\{[^}]*margin-left:\s*0[^}]*width:\s*100%[^}]*justify-content:\s*flex-start/s,
        "<= 720px 窄屏下顶栏操作簇应整组自然换行左对齐",
    );
});

/* ---------- 需求 5：表达 -> 语音朗读全通栏分组卡片 + 内部多列容器网格与去强隐 hack ---------- */

test("需求 5：语音朗读取消强隐面板 hack 与双列卡高矮空洞，采用全通栏分组卡片 + 内部容器多列网格", () => {
    const exprCss = stripComments(readPage("expression-config.css"));
    const voiceCss = stripComments(readPage("voice-page.css"));
    const wbJs = readPage("workbench-page.js");
    const replyJs = readPage("reply-page.js");

    assert.doesNotMatch(
        exprCss,
        /expression-voice-focus\s+\[data-wb-section-panel\]:not\(\[data-wb-section-panel="voice"\]\)/,
        "expression-config.css 不应强行隐藏除 voice 外的其他 6 组分节面板",
    );
    assert.doesNotMatch(
        exprCss,
        /expression-voice-focus\s+\.wb-section-sidebar\s*\{\s*display:\s*none\s*\}/,
        "expression-config.css 不应依赖 CSS 强隐 .wb-section-sidebar 来掩盖未重置的 navigationLimit",
    );

    assert.match(
        voiceCss,
        /#replyWorkspace\.expression-voice-focus\s+\.wb-card-body\s*\{[^}]*grid-template-columns:\s*minmax\(\s*0\s*,\s*1fr\s*\)/s,
        "voice-page.css 全部 7 张语音分组卡片应通栏单列堆叠，彻底消灭左右卡片高矮不齐造成的垂直空白",
    );
    assert.match(
        voiceCss,
        /\.expression-voice-workspace\s*\{[^}]*padding-bottom:\s*calc\(var\(--shell-pip-h,calc\(var\(--sp-8\)\s*\*\s*2\)\)\s*\+\s*var\(--sp-4\)\)/s,
        "语音页底部应留足安全距离防右下角悬浮迷你小屋遮挡",
    );
    assert.doesNotMatch(
        voiceCss,
        /\.wb-group\[data-wb-group="voice(?:_engine|_render|_primary|_limits|_extra|_channels)?"\]\s*\{[^}]*grid-row:\s*\d/,
        "voice-page.css 不应硬编码各语音分组的 grid-row / grid-column 坐标",
    );
    assert.match(
        voiceCss,
        /@container\s+wb-group\s*\(\s*min-width:\s*540px\s*\)\s*\{[\s\S]*?\.wb-fields\s*\{[^}]*repeat\(\s*2\s*,\s*minmax\(\s*0\s*,\s*1fr\s*\)\s*\)/s,
        "中等容器宽度下各分组内部字段应自动展开为 2 列网格",
    );
    assert.match(
        voiceCss,
        /@container\s+wb-group\s*\(\s*min-width:\s*720px\s*\)\s*\{[\s\S]*?\[data-wb-group="voice_channels"\]\s+\.wb-fields\s*\{[^}]*repeat\(\s*3\s*,\s*minmax\(\s*0\s*,\s*1fr\s*\)\s*\)/s,
        "私聊/群聊覆盖 3 列网格应由卡片自身 @container 宽度驱动",
    );
    assert.match(
        voiceCss,
        /@container\s+wb-group\s*\(\s*min-width:\s*1080px\s*\)\s*\{[\s\S]*?\[data-wb-group="voice"\]\s+\.wb-fields,\s*[\s\S]*?\[data-wb-group="voice_render"\]\s+\.wb-fields[\s\S]*?repeat\(\s*4\s*,\s*minmax\(\s*0\s*,\s*1fr\s*\)\s*\)/s,
        "仅在超宽容器（>= 1080px）下将「开关与触发」「朗读方式」「声线与 TTS 引擎」展开为 4 列，避免 760–1079px 区间被挤压成 165px 窄列",
    );
    assert.match(
        voiceCss,
        /\[data-wb-group="voice_extra"\]\s+\.wb-control\s+textarea\s*\{[^}]*min-height:\s*136px/s,
        "「关键词与语气」文本域应具备舒展高度避免局促滚动条",
    );
    assert.match(wbJs, /function setVisibleGroups\(ids,\s*preferredId = "",\s*options = \{\}\)/);
    assert.match(replyJs, /navigationLimit:\s*voice\s*\?\s*0\s*:\s*activeSections\.length/);
});

/* ---------- 需求 8：计价与汇率搜索框弹性宽度不裁切占位符 ---------- */

test("需求 8：.pw-search 采用弹性宽度与窄屏断点，确保完整展示占位符且不挤破工具栏", () => {
    const pricingCss = stripComments(readPage("pricing-workbench.css"));
    const searchBlock = pricingCss.match(/\.pw-search\s*\{([^}]*)\}/);
    assert.ok(searchBlock, "pricing-workbench.css 应定义 .pw-search");
    assert.doesNotMatch(searchBlock[1], /\bwidth:\s*220px\b/, ".pw-search 不得再写死 220px 裁切占位符");
    assert.match(searchBlock[1], /min-width:\s*min\(\s*100%\s*,\s*290px\s*\)/);
    assert.match(searchBlock[1], /flex:\s*1\s+1\s+290px/);
    assert.match(searchBlock[1], /max-width:\s*380px/);

    assert.match(
        pricingCss,
        /\.pw-wb-tools\s*\{[^}]*flex-wrap:\s*wrap/s,
        ".pw-wb-tools 应允许换行避免窄容器挤破工具栏",
    );
    assert.match(
        pricingCss,
        /@container\s+pricing-wb\s*\(\s*max-width:\s*520px\s*\)\s*\{[\s\S]*?\.pw-search\s*\{[^}]*width:\s*100%[^}]*min-width:\s*0/s,
        "<= 520px 容器下 .pw-search 应平滑退化为 100% 宽度且 min-width: 0",
    );
});

/* ---------- 需求 14：陪伴小屋首页双层根治大片空白 + 今日安排内部滚动与渐隐 ---------- */

test("需求 14（结构与样式）：>= 1121px 双态零空白（开面板左列衔接 stage+panel，纯首页左侧舞台 sticky），且 .shell-side 绝不挤压日程区", () => {
    const shellCss = stripComments(readPage("shell.css"));
    const homeCss = stripComments(readPage("home.css"));
    const appJs = readPage("app.js");

    const desktopMedia = shellCss.match(/@media\s*\(\s*min-width:\s*1121px\s*\)\s*\{([\s\S]*?)\n\}/);
    assert.ok(desktopMedia, "shell.css 应包含 @media (min-width: 1121px)");
    assert.match(
        desktopMedia[1],
        /body:not\(\.room-editing\)\s+#homeShell\[data-away="false"\]:not\(:has\(#shellPanel:not\(\[hidden\]\)\)\)\s+\.stage-region\s*\{[^}]*position:\s*sticky[^}]*top:\s*var\(--shell-pad\)[^}]*align-self:\s*start/s,
        "桌面纯首页态应将左侧舞台列 .stage-region 设为 sticky 贴顶跟随，彻底消除左下角留白",
    );
    assert.match(
        desktopMedia[1],
        /body:not\(\.room-editing\)\s+#homeShell\[data-away="false"\]:has\(#shellPanel:not\(\[hidden\]\)\)\s+\.shell-body\s*\{[^}]*grid-template-rows:\s*auto\s+minmax\(\s*min-content\s*,\s*1fr\s*\)[^}]*grid-template-areas:\s*"stage side"\s+"panel side"/s,
        "桌面首页打开面板时应将 stage 与 panel 置于左列上下衔接、side 跨两行",
    );
    const sideRule = desktopMedia[1].match(/body:not\(\.room-editing\)\s+#homeShell\[data-away="false"\]\s+\.shell-side\s*\{([^}]*)\}/);
    assert.ok(sideRule, "桌面纯首页态右栏应保持 align-self: start 自然流高度");
    assert.match(sideRule[1], /align-self:\s*start/);
    assert.doesNotMatch(sideRule[1], /max-height:\s*calc\(\s*100dvh/, "右栏不得以 100dvh 上限挤压下方今日安排滚动区");
    assert.doesNotMatch(sideRule[1], /overflow:\s*hidden/, "右栏容器本身不应 overflow:hidden，避免裁切状态悬浮提示");

    assert.match(
        desktopMedia[1],
        /body:not\(\.room-editing\)\s+#homeShell\[data-away="false"\]:has\(#shellPanel:not\(\[hidden\]\)\)\s+\.shell-side\s*\{[^}]*align-self:\s*stretch[^}]*display:\s*flex[^}]*flex-direction:\s*column/s,
        "桌面打开家具面板态右栏应撑满 stage+panel 完整高度，为今日安排提供吸顶轨道",
    );
    assert.match(
        desktopMedia[1],
        /body:not\(\.room-editing\)\s+#homeShell\[data-away="false"\]:has\(#shellPanel:not\(\[hidden\]\)\)\s+\.shell-side\s*>\s*\.home-schedule-band\s*\{[^}]*position:\s*sticky[^}]*top:\s*var\(--shell-pad\)/s,
        "桌面打开家具面板态下「今日安排」应优雅吸顶伴随左侧家具长面板滚动",
    );
    assert.match(
        appJs,
        /function homeScheduleBand\(\)\{return sideBand\('今日安排'[\s\S]*?'home-schedule-band'\);\}/,
        "homeScheduleBand 应挂载语义类 .home-schedule-band",
    );

    assert.match(
        shellCss,
        /#homeShell\[data-away="true"\]\s+\.shell-body\s*\{[^}]*grid-template-areas:\s*"panel"/s,
        "进入配置区 [data-away=true] 时主面板必须保持全宽展开",
    );

    assert.match(
        appJs,
        /const topChrome=\(scene\.getBoundingClientRect\(\)\.top-region\.getBoundingClientRect\(\)\.top\)\+number\(bodyStyle\.paddingTop\);/,
        "syncStageSpace 应相对 .stage-region 计算顶距，避免 sticky 滚动时误叠加 scrollY",
    );

    assert.match(homeCss, /\.home-agenda-scroll\{[^}]*flex:0 0 auto;max-height:640px;overflow-y:auto/);
    assert.match(homeCss, /\.home-agenda-scroll\[data-fade-top="true"\]\{[^}]*mask-image:linear-gradient/);
    assert.match(homeCss, /\.home-agenda-scroll\[data-fade-bottom="true"\]\{[^}]*mask-image:linear-gradient/);
    assert.match(homeCss, /\.home-agenda-row\[data-tone="current"\]\{[^}]*box-shadow:inset 3px 0 0 var\(--t-accent\)/);
    assert.match(homeCss, /\.home-agenda-row-detail\.is-clamped\{[^}]*-webkit-line-clamp:2/);
    assert.match(
        homeCss,
        /@media\(min-width:1121px\)\{[\s\S]*?\.shell-side\s+\.home-agenda-scroll\{[^}]*flex:0 0 auto;max-height:640px/,
        "桌面纯首页态今日安排滚动区应放宽至 640px，完整展示 5 条日程不被半截裁切",
    );
    assert.match(
        homeCss,
        /@media\(min-width:1121px\)\{[\s\S]*?body:not\(\.room-editing\)\s+#homeShell\[data-away="false"\]:has\(#shellPanel:not\(\[hidden\]\)\)\s+\.shell-side\s+\.home-agenda-scroll\{[^}]*min-height:0;[^}]*max-height:max\(320px,calc\(100dvh - 210px\)\)/s,
        "桌面打开家具面板态下今日安排滚动区应随视口高度充分舒展（calc(100dvh - 210px)）",
    );
    assert.doesNotMatch(
        homeCss,
        /\.shell-side\s+\.home-agenda-scroll\{[^}]*flex:\s*0\s+1\s+auto/,
        "今日安排滚动区绝不能参与 flex-shrink 收缩导致低视口下塌陷至 0px",
    );
    const desktopPos = homeCss.lastIndexOf("@media(min-width:1121px)");
    const mobilePos = homeCss.lastIndexOf("@media(max-width:760px)");
    assert.ok(mobilePos > desktopPos, "移动端 <= 760px 取消内层滚动的规则应位于桌面规则之后");
});

test("需求 14（行为）：今日安排渲染内部滚动容器、当前时段高亮、长详情两行折叠/展开与渐隐边界同步，短生理期与短位置成对排布", () => {
    const listeners = {};
    const document = {
        addEventListener(type, fn) { listeners[type] = fn; },
        querySelectorAll: () => [],
    };
    const window = {addEventListener() {}};
    vm.runInNewContext(readPage("home-sidebar.js"), {window, document});
    const {renderState, renderSchedule, syncScrollFade} = window.CompanionHomeSidebar;

    const now = Date.parse("2026-09-30T04:00:00Z");

    /* 首屏紧凑排布：短生理期（经前期 · 第 2 天）与短位置（维也纳家中客厅）应两列并排而非各占一整行 */
    const compactStateHtml = renderState({
        now,
        state: {
            settings: {enabled: true, planning_timezone: "Asia/Shanghai"},
            state: {
                revision: 1,
                cycle_timeline: {applicability: "applicable", phase: "pms", phase_day: 2},
                values: {
                    energy: 65,
                    health: "良好",
                    hunger: "胃里空得更明显了些，想喝一杯温热的焦糖茶",
                    mood: "缩在暖烘烘的客厅角落里，心里踏实又慵懒",
                    busy: "继续裹在被炉里偷懒歇神",
                    location: "维也纳家中客厅",
                },
            },
            body_sleep: {state: "sleeping", started_at: "2026-09-29T20:45:00Z"},
            occurrences: {items: [], partial: false},
            sleep_schedule: {enabled: false},
        },
        life: {activities: [], config: {diary_enabled: false}},
    });
    assert.match(compactStateHtml, /class="home-state-row" data-state-kind="cycle"/, "短生理期标签应紧凑排入两列网格");
    assert.match(compactStateHtml, /class="home-state-row" data-state-kind="location"/, "短位置标签应与短生理期并排节省首屏高度");
    assert.match(compactStateHtml, /class="home-state-row is-wide" data-state-kind="mood"/, "长心情文案仍占通栏");

    const longLocationHtml = renderState({
        now,
        state: {
            settings: {enabled: true, planning_timezone: "Asia/Shanghai"},
            state: {
                revision: 1,
                values: {energy: 80, health: "良好", location: "维也纳市中心靠窗安静的独立咖啡馆二楼角落"},
            },
            occurrences: {items: [], partial: false},
            sleep_schedule: {enabled: false},
        },
        life: {activities: [], config: {diary_enabled: false}},
    });
    assert.match(longLocationHtml, /class="home-state-row is-wide" data-state-kind="location"/, "超长位置文案自动占通栏不挤压");
    const longDetail = "第一句计划细则内容较长，用于验证超过四十四字时默认收起为两行显示并在点击展开全文后完整呈现。";
    const state = {
        settings: {enabled: true, planning_timezone: "Asia/Shanghai"},
        state: {revision: 1, values: {}},
        occurrences: {
            partial: false,
            items: [
                {
                    id: "past-1", sequence: 0, kind: "role_plan", state: "accepted",
                    title: "清晨散步", start: "2026-09-30T00:00:00Z", end: "2026-09-30T01:00:00Z",
                    detail: "短详情不折叠",
                },
                {
                    id: "now-1", sequence: 0, kind: "role_plan", state: "accepted",
                    title: "窗边读书", start: "2026-09-30T03:30:00Z", end: "2026-09-30T04:30:00Z",
                    detail: longDetail,
                },
                {
                    id: "next-1", sequence: 0, kind: "role_plan", state: "accepted",
                    title: "傍晚整理", start: "2026-09-30T10:00:00Z", end: "2026-09-30T11:00:00Z",
                },
            ],
        },
        sleep_schedule: {enabled: false},
    };
    const life = {activities: [], config: {diary_enabled: false}};

    const html = renderSchedule({now, state, life});
    assert.match(html, /<div class="home-agenda-scroll" tabindex="0"/, "应渲染带键盘聚焦能力的内部滚动容器");
    assert.match(html, /data-tone="current" data-current="true" aria-current="time"/, "当前时段行应有明确高亮与语义标记");
    assert.match(html, /<div class="home-agenda-row-heading"><strong>窗边读书<\/strong><span class="home-agenda-now-badge">进行中<\/span>/);
    assert.match(html, /<p class="home-agenda-row-detail">短详情不折叠<\/p>/, "短详情不折叠也不渲染多余按钮");
    assert.match(html, /<p class="home-agenda-row-detail is-clamped">/, "长详情默认两行折叠");
    assert.match(html, /data-agenda-detail-key="calendar:now-1:0" aria-expanded="false">展开全文<\/button>/);

    /* 点击展开全文后更新 DOM 并在后续定时刷新中保持展开态 */
    let clamped = true;
    const scroller = {
        dataset: {},
        scrollTop: 0,
        clientHeight: 300,
        scrollHeight: 680,
        classList: {contains: name => name === "home-agenda-scroll"},
    };
    const detailEl = {
        classList: {
            contains: name => name === "home-agenda-row-detail",
            toggle: (_name, force) => { clamped = force; },
        },
    };
    const toggleBtn = {
        dataset: {agendaDetailKey: "calendar:now-1:0"},
        previousElementSibling: detailEl,
        attrs: {},
        textContent: "展开全文",
        setAttribute(k, v) { this.attrs[k] = v; },
        closest: sel => (sel === ".home-agenda-scroll" ? scroller : null),
    };
    listeners.click({target: {closest: sel => (sel === "button[data-agenda-detail-key]" ? toggleBtn : null)}});
    assert.equal(clamped, false);
    assert.equal(toggleBtn.attrs["aria-expanded"], "true");
    assert.equal(toggleBtn.textContent, "收起详情");
    assert.equal(scroller.dataset.fadeTop, "false");
    assert.equal(scroller.dataset.fadeBottom, "true");

    const refreshed = renderSchedule({now, state, life});
    assert.match(refreshed, /data-agenda-detail-key="calendar:now-1:0" aria-expanded="true">收起详情<\/button>/);
    assert.doesNotMatch(refreshed, /home-agenda-row-detail is-clamped/);

    /* 折叠整个今日安排列表后，定时刷新保持收起状态 */
    listeners.toggle({target: {classList: {contains: name => name === "home-agenda-list"}, open: false, querySelectorAll: () => []}});
    assert.match(renderSchedule({now, state, life}), /<details class="home-agenda-list">/);
    listeners.toggle({target: {classList: {contains: name => name === "home-agenda-list"}, open: true, querySelectorAll: () => []}});
    assert.match(renderSchedule({now, state, life}), /<details class="home-agenda-list" open>/);

    /* 滚动渐隐边界在顶部、中间、底部及不可滚状态下均准确切换 */
    scroller.scrollTop = 120;
    listeners.scroll({target: scroller});
    assert.equal(scroller.dataset.fadeTop, "true");
    assert.equal(scroller.dataset.fadeBottom, "true");

    scroller.scrollTop = 380;
    listeners.scroll({target: scroller});
    assert.equal(scroller.dataset.fadeTop, "true");
    assert.equal(scroller.dataset.fadeBottom, "false");

    scroller.scrollHeight = 260;
    scroller.scrollTop = 0;
    syncScrollFade({querySelectorAll: () => [scroller]});
    assert.equal(scroller.dataset.fadeTop, "false");
    assert.equal(scroller.dataset.fadeBottom, "false");
});
