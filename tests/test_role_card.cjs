"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const {cookiePreferences} = require("./fixtures/browser_preferences_server.cjs");

let chromium;
try { ({chromium} = require("playwright")); } catch (_) {}

const source = path.join(__dirname, "..", "pages", "陪伴小屋");
const read = filename => fs.readFileSync(path.join(source, filename), "utf8");
const stage = read("index.html").match(/<section class="stage-region"[\s\S]*?<\/section>/)[0];
const css = ["tokens.css", "styles.css", "shell.css"].map(read).join("\n");
const html = `<!doctype html><html data-theme-ready data-room-ready><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}</style></head><body>
<div id="homeShell" data-away="true"><nav class="rail" aria-label="插件导航">导航</nav><div class="shell-body">${stage}<section class="shell-panel">配置内容</section></div></div>
<script>
(async()=>{
const request=async(method,patch)=>{
  const response=await fetch("/preferences",{method,headers:{"Content-Type":"application/json"},...(method==="POST"?{body:JSON.stringify(patch)}:{})});
  return (await response.json()).preferences;
};
let preferences=await request("GET");
const choices = [
  {target:{bot_id:"bot-a",account_name:"账号甲"},role:{role_id:"shared-role",role_name:"角色甲"}},
  {target:{bot_id:"bot-b",account_name:"账号乙"},role:{role_id:"shared-role",role_name:"角色乙"}}
];
let current = 0;
window.roleGuard = true;
window.homeClicks = 0;
window.CompanionShell = {section:"expression",onChange:handler => {window.shellChanged=handler;}};
window.CompanionProduct = {
  role:() => choices[current].role, roleTarget:() => choices[current].target,
  roleChoices:() => choices,
  roleChoiceKey:choice => JSON.stringify([choice.target.bot_id,choice.role.role_id]),
  roleLabel:role => role.role_name, accountLabel:target => target.account_name,
  browserPreferences:() => preferences,
  browserPreferencesIdentity:() => "card-test",
  browserPreferencesNotice:() => "",
  saveBrowserPreferences:async patch => {preferences={...preferences,...patch};await request("POST",patch);return true;},
  chooseRoleKey:async key => {
    if (!window.roleGuard) return false;
    current=choices.findIndex(choice=>CompanionProduct.roleChoiceKey(choice)===key);
    window.dispatchEvent(new CustomEvent("companion:scope-changed"));
  }
};

${read("role-card.js")}

document.querySelector("#stageScene").addEventListener("click",event=>{
  if(event.target.closest("button, a, select, input, label"))return;
  window.homeClicks++;
});
})();
</script></body></html>`;

test("小屋角色卡片浏览器交互", {skip: chromium ? false : "未安装 playwright"}, async t => {
  const server = http.createServer(async (request, response) => {
    if (request.url === "/preferences") {
      let raw = "";
      for await (const chunk of request) raw += chunk;
      response.setHeader("Content-Type", "application/json");
      response.end(JSON.stringify(cookiePreferences(request,response,request.method,raw ? JSON.parse(raw) : {})));
      return;
    }
    response.setHeader("Content-Type", "text/html; charset=utf-8");
    response.end(html);
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch({headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? {executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE} : {}),
    args: ["--disable-dev-shm-usage"]});
  t.after(async () => { await browser.close(); await new Promise(resolve => server.close(resolve)); });
  const context = await browser.newContext({viewport: {width: 1000, height: 700}});
  const page = await context.newPage();
  const url = `http://127.0.0.1:${server.address().port}/`;
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(url);
  await page.locator("[data-role-card-name]").getByText("角色甲",{exact:true}).waitFor();

  await t.test("常显角色名，悬停和键盘聚焦显示切换入口，按账号区分同名角色", async () => {
    assert.equal(await page.locator("[data-role-card-name]").textContent(), "角色甲");
    assert.equal(await page.locator("[data-role-card-name]").isVisible(), true);
    const toggle = page.locator("[data-role-card-switch]");
    assert.equal(await toggle.evaluate(node => getComputedStyle(node).opacity), "0");
    await toggle.focus();
    await page.waitForFunction(() => getComputedStyle(document.querySelector("[data-role-card-switch]")).opacity === "1");
    await toggle.press("Enter");
    assert.equal(await page.locator("#roleCardMenu").isVisible(), true);
    assert.deepEqual(await page.locator("[data-role-card-choice] option").allTextContents(), ["账号甲 · 角色甲", "账号乙 · 角色乙"]);
    await page.locator("[data-role-card-choice]").selectOption(JSON.stringify(["bot-b", "shared-role"]));
    assert.equal(await page.locator("[data-role-card-name]").textContent(), "角色乙");
    assert.equal(await page.locator("#roleCardMenu").isVisible(), false);
    assert.equal(await page.evaluate(() => window.homeClicks), 0);
  });

  await t.test("取消切换保留当前角色，外部切换同步卡片，Escape仅关闭菜单", async () => {
    await page.locator("[data-role-card-switch]").click();
    await page.evaluate(() => { window.roleGuard = false; });
    await page.locator("[data-role-card-choice]").selectOption(JSON.stringify(["bot-a", "shared-role"]));
    assert.equal(await page.locator("[data-role-card-name]").textContent(), "角色乙");
    assert.equal(await page.locator("[data-role-card-choice]").inputValue(), JSON.stringify(["bot-b", "shared-role"]));
    await page.locator("[data-role-card-choice]").press("Escape");
    assert.equal(await page.locator("#roleCardMenu").isVisible(), false);
    await page.evaluate(async () => {
      window.roleGuard = true;
      await CompanionProduct.chooseRoleKey(JSON.stringify(["bot-a", "shared-role"]));
    });
    assert.equal(await page.locator("[data-role-card-name]").textContent(), "角色甲");
    assert.equal(await page.evaluate(() => window.homeClicks), 0);
  });

  await t.test("从切换按钮拖开不会移动卡片或触发返回", async () => {
    const before = await page.locator("#stageScene").boundingBox();
    const button = await page.locator("[data-role-card-switch]").boundingBox();
    await page.mouse.move(button.x + button.width / 2, button.y + button.height / 2);
    await page.mouse.down();
    await page.mouse.move(before.x + 50, before.y + 70, {steps: 4});
    await page.mouse.up();
    const after = await page.locator("#stageScene").boundingBox();
    assert.deepEqual(after, before);
    assert.equal(await page.evaluate(() => window.homeClicks), 0);
    assert.equal(await page.locator("#roleCardMenu").isVisible(), false);
  });

  await t.test("普通点击返回，拖动不误返回且保留位置", async () => {
    let box = await page.locator("#stageScene").boundingBox();
    await page.mouse.click(box.x + 50, box.y + 70);
    assert.equal(await page.evaluate(() => window.homeClicks), 1);
    await page.mouse.move(box.x + 50, box.y + 70);
    await page.mouse.down();
    await page.mouse.move(box.x - 150, box.y - 130, {steps: 5});
    await page.mouse.up();
    assert.equal(await page.evaluate(() => window.homeClicks), 1);
    box = await page.locator("#stageScene").boundingBox();
    const stored = await page.evaluate(() => CompanionProduct.browserPreferences().room_card_position);
    await page.waitForFunction(async expected => {
      const {preferences}=await (await fetch("/preferences")).json();
      return JSON.stringify(preferences.room_card_position)===JSON.stringify(expected);
    },stored);
    assert.ok(Math.abs(stored.left - box.x) < 1);
    assert.ok(Math.abs(stored.top - box.y) < 1);
    await page.reload();
    await page.waitForFunction(()=>window.CompanionProduct?.browserPreferences().room_card_position);
    const restored = await page.locator("#stageScene").boundingBox();
    assert.ok(Math.abs(restored.x - box.x) < 1);
    assert.ok(Math.abs(restored.y - box.y) < 1);
    await page.mouse.click(restored.x + 50, restored.y + 70);
    assert.equal(await page.evaluate(() => window.homeClicks), 1);
  });

  await t.test("拖动限位，缩小视口后仍可见，支持恢复默认位置", async () => {
    let box = await page.locator("#stageScene").boundingBox();
    await page.mouse.move(box.x + 50, box.y + 70);
    await page.mouse.down();
    await page.mouse.move(0, 0, {steps: 5});
    await page.mouse.up();
    box = await page.locator("#stageScene").boundingBox();
    const mainLeft = await page.locator(".shell-body").evaluate(node => node.getBoundingClientRect().left);
    assert.equal(box.x, mainLeft + 8);
    assert.equal(box.y, 8);
    await page.evaluate(() => { document.querySelector("#homeShell").dataset.rail = "expanded"; });
    await page.waitForFunction(() => document.querySelector("#stageScene").getBoundingClientRect().left >= document.querySelector(".rail").getBoundingClientRect().right + 8);
    box = await page.locator("#stageScene").boundingBox();
    assert.ok(box.x > mainLeft + 8);
    await page.evaluate(() => { delete document.querySelector("#homeShell").dataset.rail; });
    await page.mouse.move(box.x + 50, box.y + 70);
    await page.mouse.down();
    await page.mouse.move(850, 695, {steps: 5});
    await page.mouse.up();
    await page.setViewportSize({width: 400, height: 500});
    await page.waitForFunction(() => document.querySelector("#stageScene").getBoundingClientRect().right <= innerWidth);
    box = await page.locator("#stageScene").boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= 400);
    assert.ok(box.y >= 0 && box.y + box.height <= 500);
    await page.locator("#stageScene").hover();
    await page.locator("[data-role-card-switch]").click();
    await page.locator("[data-role-card-reset]").click();
    assert.equal(await page.evaluate(() => CompanionProduct.browserPreferences().room_card_position), null);
    assert.equal(await page.evaluate(() => document.querySelector(".stage-region").style.getPropertyValue("--role-card-left")), "");
    await page.setViewportSize({width: 160, height: 150});
    await page.waitForFunction(() => {
      const card = document.querySelector("#stageScene").getBoundingClientRect();
      const navigation = document.querySelector(".rail").getBoundingClientRect();
      return card.left >= 8 && card.right <= innerWidth - 8
        && card.top >= navigation.bottom + 8 && card.bottom <= innerHeight - 8;
    });
    box = await page.locator("#stageScene").boundingBox();
    assert.ok(box.width < 176 && box.height < 104);
  });

  await t.test("旧位置落在侧栏或视口外时，刷新后移回主区", async () => {
    await page.setViewportSize({width: 1000, height: 700});
    for (const position of [{left: 0, top: 0}, {left: 1800, top: 1200}]) {
      await page.evaluate(async position => {
        await CompanionProduct.saveBrowserPreferences({room_card_position:position});
      }, position);
      await page.reload();
      await page.waitForFunction(() => {
        const card = document.querySelector("#stageScene").getBoundingClientRect();
        const main = document.querySelector(".shell-body").getBoundingClientRect();
        return window.CompanionRoleCard && card.left >= main.left + 8 && card.right <= innerWidth - 8
          && card.top >= 8 && card.bottom <= innerHeight - 8;
      });
      const card = await page.locator("#stageScene").boundingBox();
      await page.mouse.move(card.x + 50, card.y + 70);
      await page.mouse.down();
      await page.mouse.move(card.x + 80, card.y + 40, {steps: 3});
      await page.mouse.up();
      assert.equal(await page.evaluate(() => window.homeClicks), 0);
    }
  });

  const settled = async target => {
    await target.waitForFunction(() => !document.querySelector(".stage-region").hasAttribute("data-role-card-settling"));
  };
  const dragToRight = async (target, overflow) => {
    const box = await target.locator("#stageScene").boundingBox();
    const right = await target.locator(".shell-body").evaluate(node => Math.min(innerWidth, node.getBoundingClientRect().right));
    await target.mouse.move(box.x + 20, box.y + 70);
    await target.mouse.down();
    await target.mouse.move(right - box.width + overflow + 20, box.y + 70, {steps: 5});
  };

  await t.test("右侧越过一半收起，未过一半回到主区，隐藏卡片不遮挡内容", async () => {
    await page.reload();
    await page.waitForFunction(() => window.CompanionRoleCard);
    const clicks = await page.evaluate(() => window.homeClicks);
    const width = (await page.locator("#stageScene").boundingBox()).width;
    await dragToRight(page, width / 2 - 10);
    await page.mouse.up();
    await settled(page);
    assert.equal(await page.locator(".stage-region").getAttribute("data-role-card-docked"), "false");
    let card = await page.locator("#stageScene").boundingBox();
    assert.ok(card.x + card.width <= 992);
    await dragToRight(page, width / 2);
    await page.mouse.up();
    await settled(page);
    assert.equal(await page.locator(".stage-region").getAttribute("data-role-card-docked"), "true");
    assert.equal(await page.locator("[data-role-card-handle]").isVisible(), true);
    assert.equal(await page.locator("#stageScene").evaluate(node => node.inert), true);
    assert.equal(await page.evaluate(() => window.homeClicks), clicks);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(await page.evaluate(() => {
      const card = document.querySelector("#stageScene").getBoundingClientRect();
      const node = document.elementFromPoint(innerWidth - card.width / 2, card.top + card.height / 2);
      return !node.closest(".stage-region");
    }), true);
    const stored = await page.evaluate(() => CompanionProduct.browserPreferences().room_card_position);
    assert.ok(stored.left + width <= 992);
    await page.locator("[data-role-card-handle]").click();
    await settled(page);
    assert.equal(await page.locator(".stage-region").getAttribute("data-role-card-docked"), "false");
    assert.equal(await page.locator("[data-role-card-handle]").isVisible(), false);
    assert.equal(await page.evaluate(() => window.homeClicks), clicks);
    card = await page.locator("#stageScene").boundingBox();
    await page.mouse.click(card.x + 50, card.y + 70);
    assert.equal(await page.evaluate(() => window.homeClicks), clicks + 1);
    await dragToRight(page, width / 2 + 10);
    await page.mouse.up();
    await settled(page);
    await page.locator("[data-role-card-handle]").focus();
    await page.locator("[data-role-card-handle]").press("Enter");
    await settled(page);
    assert.equal(await page.locator("[data-role-card-handle]").isVisible(), false);
    assert.equal(await page.evaluate(() => window.homeClicks), clicks + 1);
    await dragToRight(page, width / 2 + 10);
    await page.mouse.up();
    await settled(page);
    await page.evaluate(() => {
      CompanionShell.section = "";
      document.querySelector("#homeShell").dataset.away = "false";
      window.shellChanged();
    });
    await page.waitForFunction(() => !document.querySelector("#stageScene").inert);
    assert.equal(await page.locator("[data-role-card-handle]").isVisible(), false);
    await page.evaluate(() => {
      CompanionShell.section = "expression";
      document.querySelector("#homeShell").dataset.away = "true";
      window.shellChanged();
    });
    await page.waitForFunction(() => document.querySelector("#stageScene").getBoundingClientRect().right <= innerWidth);
  });

  await t.test("把手向左拖动恢复且不误返回，收起时缩放仍可操作，刷新恢复展开位置", async () => {
    const width = (await page.locator("#stageScene").boundingBox()).width;
    await dragToRight(page, width / 2 + 10);
    await page.mouse.up();
    await settled(page);
    await page.setViewportSize({width: 400, height: 500});
    await page.waitForFunction(() => {
      const rect = document.querySelector("[data-role-card-handle]").getBoundingClientRect();
      return rect.right <= innerWidth && rect.bottom <= innerHeight;
    });
    const handle = await page.locator("[data-role-card-handle]").boundingBox();
    assert.ok(handle.x >= 0 && handle.x + handle.width <= 400);
    assert.ok(handle.y >= 0 && handle.y + handle.height <= 500);
    const clicks = await page.evaluate(() => window.homeClicks);
    await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
    await page.mouse.down();
    await page.mouse.move(handle.x + handle.width / 2 - 12, handle.y + handle.height / 2, {steps: 3});
    await page.mouse.up();
    await settled(page);
    assert.equal(await page.locator(".stage-region").getAttribute("data-role-card-docked"), "false");
    assert.equal(await page.evaluate(() => window.homeClicks), clicks);
    await dragToRight(page, (await page.locator("#stageScene").boundingBox()).width / 2 + 10);
    await page.mouse.up();
    await settled(page);
    await page.reload();
    await page.waitForFunction(() => window.CompanionRoleCard);
    assert.equal(await page.locator("[data-role-card-handle]").isVisible(), false);
    const card = await page.locator("#stageScene").boundingBox();
    assert.ok(card.x >= 8 && card.x + card.width <= 392);
    await page.setViewportSize({width: 1000, height: 700});
  });

  await t.test("拖动中取消或失去捕获不会误收起", async () => {
    await page.setViewportSize({width: 1000, height: 700});
    await page.reload();
    await page.waitForFunction(() => window.CompanionRoleCard);
    for (const mode of ["pointercancel", "lostpointercapture"]) {
      const before = await page.locator("#stageScene").boundingBox();
      await page.locator("#stageScene").evaluate(node => {
        node.addEventListener("pointerdown", event => { window.activeDragPointer = event.pointerId; }, {once:true});
      });
      await dragToRight(page, before.width / 2 + 10);
      await page.locator("#stageScene").evaluate((node, mode) => {
        if (mode === "pointercancel") node.dispatchEvent(new PointerEvent(mode, {pointerId:window.activeDragPointer}));
        else node.releasePointerCapture(window.activeDragPointer);
      }, mode);
      await page.mouse.up();
      await settled(page);
      assert.equal(await page.locator(".stage-region").getAttribute("data-role-card-docked"), "false");
      const after = await page.locator("#stageScene").boundingBox();
      assert.ok(Math.abs(after.x - before.x) < 1);
      assert.ok(Math.abs(after.y - before.y) < 1);
    }
  });

  await t.test("触摸设备显示入口，点击切换不返回小屋", async () => {
    const mobile = await browser.newContext({viewport: {width: 390, height: 700}, isMobile: true, hasTouch: true});
    const phone = await mobile.newPage();
    await phone.goto(url);
    await phone.locator("[data-role-card-name]").getByText("角色甲",{exact:true}).waitFor();
    assert.equal(await phone.locator("#stageScene").isVisible(), true);
    let box = await phone.locator("#stageScene").boundingBox();
    await phone.mouse.move(box.x + 50, box.y + 70);
    await phone.mouse.down();
    await phone.mouse.move(0, 0, {steps: 5});
    await phone.mouse.up();
    box = await phone.locator("#stageScene").boundingBox();
    const navigationBottom = await phone.locator(".rail").evaluate(node => node.getBoundingClientRect().bottom);
    assert.ok(box.y >= navigationBottom + 8);
    assert.equal(box.x, 8);
    assert.equal(await phone.locator("[data-role-card-switch]").evaluate(node => getComputedStyle(node).opacity), "1");
    await phone.locator("[data-role-card-switch]").tap();
    assert.equal(await phone.locator("#roleCardMenu").isVisible(), true);
    await phone.locator("[data-role-card-choice]").selectOption(JSON.stringify(["bot-b", "shared-role"]));
    assert.equal(await phone.locator("[data-role-card-name]").textContent(), "角色乙");
    assert.equal(await phone.evaluate(() => window.homeClicks), 0);
    const client = await mobile.newCDPSession(phone);
    const touch = async (type, x, y) => client.send("Input.dispatchTouchEvent", {
      type, touchPoints: type === "touchEnd" ? [] : [{x, y, id:1}],
    });
    box = await phone.locator("#stageScene").boundingBox();
    await touch("touchStart", box.x + 20, box.y + 70);
    await touch("touchMove", 390 - box.width / 2 + 30, box.y + 70);
    await touch("touchEnd");
    await settled(phone);
    assert.equal(await phone.locator(".stage-region").getAttribute("data-role-card-docked"), "true");
    const handle = await phone.locator("[data-role-card-handle]").boundingBox();
    await touch("touchStart", handle.x + handle.width / 2, handle.y + handle.height / 2);
    await touch("touchMove", handle.x + handle.width / 2 - 30, handle.y + handle.height / 2);
    await touch("touchEnd");
    await settled(phone);
    assert.equal(await phone.locator(".stage-region").getAttribute("data-role-card-docked"), "false");
    assert.equal(await phone.evaluate(() => window.homeClicks), 0);
    await mobile.close();
  });

  assert.deepEqual(errors, []);
});
