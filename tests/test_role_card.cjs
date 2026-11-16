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

  await t.test("鼠标切换和恢复位置后移开即隐藏入口，键盘关闭保留可见焦点", async () => {
    const toggle = page.locator("[data-role-card-switch]");
    const hiddenToggle = async () => {
      await page.mouse.move(400, 50);
      await page.waitForFunction(() => getComputedStyle(document.querySelector("[data-role-card-switch]")).opacity === "0");
    };
    await page.locator("#stageScene").hover();
    await toggle.click();
    await page.locator("[data-role-card-choice]").selectOption(JSON.stringify(["bot-b", "shared-role"]));
    assert.equal(await page.locator("#roleCardMenu").isVisible(), false);
    await hiddenToggle();
    await page.locator("#stageScene").hover();
    await toggle.click();
    await page.locator("[data-role-card-reset]").click();
    await hiddenToggle();
    await page.locator("#stageScene").hover();
    await toggle.click();
    await toggle.click();
    await hiddenToggle();
    await toggle.focus();
    await toggle.press("Enter");
    await page.locator("[data-role-card-choice]").press("Escape");
    assert.equal(await toggle.evaluate(node => document.activeElement === node), true);
    await page.waitForFunction(() => getComputedStyle(document.querySelector("[data-role-card-switch]")).opacity === "1");
    assert.equal(await page.evaluate(() => window.homeClicks), 0);
  });

  await t.test("点击页面空白或嵌入工作区关闭菜单，不被冒泡拦截影响", async () => {
    const toggle = page.locator("[data-role-card-switch]");
    await page.evaluate(() => {
      const panel = document.querySelector(".shell-panel");
      panel.addEventListener("pointerdown", event => event.stopPropagation());
      const frame = document.createElement("iframe");
      frame.id = "outsideWorkspace";
      frame.setAttribute("sandbox", "allow-scripts");
      frame.srcdoc = '<body style="margin:0;min-height:120px">工作区空白</body>';
      frame.style.cssText = "position:fixed;left:300px;top:100px;width:240px;height:140px";
      panel.append(frame);
    });
    await page.locator("#stageScene").hover();
    await toggle.click();
    await page.mouse.click(400, 50);
    assert.equal(await page.locator("#roleCardMenu").isVisible(), false);
    for (let attempt = 0; attempt < 2; attempt++) {
      await page.locator("#stageScene").hover();
      await toggle.click();
      await page.frameLocator("#outsideWorkspace").locator("body").click();
      await page.locator("#roleCardMenu").waitFor({state:"hidden"});
    }
    assert.equal(await page.evaluate(() => window.homeClicks), 0);
    await page.locator("#outsideWorkspace").evaluate(node => node.remove());
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
    const bounds = await page.locator(".shell-body").boundingBox();
    assert.ok(Math.abs(stored.right - (bounds.x + bounds.width - box.x - box.width)) < 1);
    assert.ok(Math.abs(stored.bottom - (bounds.y + bounds.height - box.y - box.height)) < 1);
    await page.reload();
    await page.waitForFunction(()=>window.CompanionProduct?.browserPreferences().room_card_position);
    const restored = await page.locator("#stageScene").boundingBox();
    assert.ok(Math.abs(restored.x - box.x) < 1);
    assert.ok(Math.abs(restored.y - box.y) < 1);
    await page.mouse.click(restored.x + 50, restored.y + 70);
    assert.equal(await page.evaluate(() => window.homeClicks), 1);
  });

  await t.test("缩放保持主区右下角距离，窄屏限位后放大和刷新恢复锚点", async () => {
    const saved = await page.evaluate(() => CompanionProduct.browserPreferences().room_card_position);
    const expectAnchor = async () => page.waitForFunction(expected => {
      const card = document.querySelector("#stageScene").getBoundingClientRect();
      const main = document.querySelector(".shell-body").getBoundingClientRect();
      return Math.abs(Math.min(innerWidth, main.right) - card.right - expected.right) < 1
        && Math.abs(Math.min(innerHeight, main.bottom) - card.bottom - expected.bottom) < 1;
    }, saved);
    await page.setViewportSize({width:1200, height:900});
    await expectAnchor();
    await page.setViewportSize({width:600, height:600});
    await expectAnchor();
    await page.setViewportSize({width:180, height:180});
    await page.waitForFunction(() => {
      const card = document.querySelector("#stageScene").getBoundingClientRect();
      const rail = document.querySelector(".rail").getBoundingClientRect();
      return card.left >= 8 && card.right <= innerWidth - 8
        && card.top >= rail.bottom + 8 && card.bottom <= innerHeight - 8;
    });
    assert.deepEqual(await page.evaluate(() => CompanionProduct.browserPreferences().room_card_position), saved);
    await page.setViewportSize({width:1100, height:800});
    await expectAnchor();
    await page.reload();
    await page.waitForFunction(() => window.CompanionRoleCard);
    await expectAnchor();
    await page.setViewportSize({width:1000, height:700});
    await expectAnchor();
  });

  await t.test("拖动限位，缩小视口后仍可见，支持恢复默认位置", async () => {
    let box = await page.locator("#stageScene").boundingBox();
    const mainLeft = await page.locator(".shell-body").evaluate(node => node.getBoundingClientRect().left);
    await page.mouse.move(box.x + 50, box.y + 70);
    await page.mouse.down();
    await page.mouse.move(mainLeft + 50, 0, {steps: 5});
    await page.mouse.up();
    await page.waitForFunction(() => !document.querySelector(".stage-region").hasAttribute("data-role-card-settling"));
    box = await page.locator("#stageScene").boundingBox();
    assert.equal(box.x, mainLeft + 8);
    assert.equal(box.y, 8);
    await page.evaluate(() => { document.querySelector("#homeShell").dataset.rail = "expanded"; });
    await page.waitForFunction(() => document.querySelector("#stageScene").getBoundingClientRect().left >= document.querySelector(".rail").getBoundingClientRect().right + 8);
    box = await page.locator("#stageScene").boundingBox();
    assert.ok(box.x > mainLeft + 8);
    await page.evaluate(() => { delete document.querySelector("#homeShell").dataset.rail; });
    await page.waitForFunction(left => document.querySelector("#stageScene").getBoundingClientRect().left === left + 8, mainLeft);
    box = await page.locator("#stageScene").boundingBox();
    await page.mouse.move(box.x + 50, box.y + 70);
    await page.mouse.down();
    await page.mouse.move(850, 695, {steps: 5});
    await page.mouse.up();
    await page.setViewportSize({width: 400, height: 500});
    await page.waitForFunction(() => {
      const card = document.querySelector("#stageScene").getBoundingClientRect();
      return card.left >= 0 && card.right <= innerWidth && card.top >= 0 && card.bottom <= innerHeight;
    });
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

  await t.test("左半区保存左下角距离，缩放和侧栏变化保持锚点，跨中线改用右下角", async () => {
    await page.setViewportSize({width:1000, height:700});
    await page.reload();
    await page.waitForFunction(() => window.CompanionRoleCard);
    const moveCard = async side => {
      const box = await page.locator("#stageScene").boundingBox();
      const main = await page.locator(".shell-body").boundingBox();
      const left = side === "left" ? main.x + 70 : main.x + main.width - box.width - 70;
      const top = main.y + main.height - box.height - 90;
      await page.mouse.move(box.x + 50, box.y + 70);
      await page.mouse.down();
      await page.mouse.move(left + 50, top + 70, {steps:5});
      await page.mouse.up();
      await page.waitForFunction(() => !document.querySelector(".stage-region").hasAttribute("data-role-card-settling"));
    };
    await moveCard("left");
    const saved = await page.evaluate(() => CompanionProduct.browserPreferences().room_card_position);
    assert.deepEqual(saved, {left:70, bottom:90});
    const expectLeft = () => page.waitForFunction(expected => {
      const card = document.querySelector("#stageScene").getBoundingClientRect();
      const main = document.querySelector(".shell-body").getBoundingClientRect();
      return Math.abs(card.left - main.left - expected.left) < 1
        && Math.abs(Math.min(innerHeight, main.bottom) - card.bottom - expected.bottom) < 1;
    }, saved);
    await page.setViewportSize({width:1250, height:850});
    await expectLeft();
    await page.evaluate(() => { document.querySelector("#homeShell").dataset.rail = "expanded"; });
    await expectLeft();
    await page.setViewportSize({width:600, height:600});
    await expectLeft();
    assert.deepEqual(await page.evaluate(() => CompanionProduct.browserPreferences().room_card_position), saved);
    await page.reload();
    await page.waitForFunction(() => window.CompanionRoleCard);
    await expectLeft();
    await page.setViewportSize({width:1000, height:700});
    await expectLeft();
    await moveCard("right");
    assert.deepEqual(await page.evaluate(() => CompanionProduct.browserPreferences().room_card_position), {right:70, bottom:90});
  });

  await t.test("旧坐标不恢复也不转换，重新拖动才保存底部锚点距离", async () => {
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
      assert.deepEqual(await page.evaluate(async () => (await (await fetch("/preferences")).json()).preferences.room_card_position), position);
      await page.mouse.move(card.x + 50, card.y + 70);
      await page.mouse.down();
      await page.mouse.move(card.x + 80, card.y + 40, {steps: 3});
      await page.mouse.up();
      await page.waitForFunction(async () => {
        const {preferences} = await (await fetch("/preferences")).json();
        return "bottom" in preferences.room_card_position && !("top" in preferences.room_card_position);
      });
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
  const dragToLeft = async (target, overflow) => {
    const box = await target.locator("#stageScene").boundingBox();
    const left = await target.locator(".shell-body").evaluate(node => Math.max(0, node.getBoundingClientRect().left));
    await target.mouse.move(box.x + box.width - 20, box.y + 70);
    await target.mouse.down();
    await target.mouse.move(left + box.width - overflow - 20, box.y + 70, {steps:5});
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
    const handleBox = await page.locator("[data-role-card-handle]").boundingBox();
    const arrowBox = await page.locator("[data-role-card-handle] svg").boundingBox();
    assert.ok(Math.abs(arrowBox.y + arrowBox.height / 2 - handleBox.y - handleBox.height / 2) < 1);
    assert.ok(Math.abs(arrowBox.x + arrowBox.width / 2 - handleBox.x - handleBox.width / 2) < 1);
    assert.equal(await page.locator("#stageScene").evaluate(node => node.inert), true);
    assert.equal(await page.evaluate(() => window.homeClicks), clicks);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(await page.evaluate(() => {
      const card = document.querySelector("#stageScene").getBoundingClientRect();
      const node = document.elementFromPoint(innerWidth - card.width / 2, card.top + card.height / 2);
      return !node.closest(".stage-region");
    }), true);
    const stored = await page.evaluate(() => CompanionProduct.browserPreferences().room_card_position);
    assert.ok(stored.right >= 8);
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

  await t.test("左侧半幅收起为朝右把手，避开侧栏，点击或向右拖出恢复", async () => {
    await page.setViewportSize({width:1000, height:700});
    await page.reload();
    await page.waitForFunction(() => window.CompanionRoleCard);
    const clicks = await page.evaluate(() => window.homeClicks);
    const width = (await page.locator("#stageScene").boundingBox()).width;
    await dragToLeft(page, width / 2 - 10);
    await page.mouse.up();
    await settled(page);
    assert.equal(await page.locator(".stage-region").getAttribute("data-role-card-docked"), "false");
    await dragToLeft(page, width / 2);
    await page.mouse.up();
    await settled(page);
    assert.equal(await page.locator(".stage-region").getAttribute("data-role-card-docked"), "true");
    assert.equal(await page.locator(".stage-region").getAttribute("data-role-card-dock-side"), "left");
    assert.equal(await page.locator("[data-role-card-handle]").getAttribute("title"), "点击展开，或向右拖出小屋卡片");
    assert.equal(await page.locator("[data-role-card-handle] svg").evaluate(node => getComputedStyle(node).transform), "matrix(-1, 0, 0, -1, 0, 0)");
    const mainLeft = await page.locator(".shell-body").evaluate(node => node.getBoundingClientRect().left);
    const handle = await page.locator("[data-role-card-handle]").boundingBox();
    assert.equal(handle.x, mainLeft);
    assert.equal(await page.evaluate(() => {
      const main = document.querySelector(".shell-body").getBoundingClientRect();
      const handle = document.querySelector("[data-role-card-handle]").getBoundingClientRect();
      return !document.elementFromPoint(main.left - 1, handle.top + handle.height / 2).closest(".stage-region");
    }), true);
    await page.locator("[data-role-card-handle]").click();
    await settled(page);
    assert.equal(await page.locator("[data-role-card-handle]").isVisible(), false);
    assert.equal(await page.evaluate(() => window.homeClicks), clicks);
    await dragToLeft(page, width / 2 + 10);
    await page.mouse.up();
    await settled(page);
    await page.evaluate(() => { document.querySelector("#homeShell").dataset.rail = "expanded"; });
    await page.waitForFunction(() => document.querySelector("[data-role-card-handle]").getBoundingClientRect().left >= document.querySelector(".rail").getBoundingClientRect().right);
    await page.setViewportSize({width:400, height:500});
    await page.waitForFunction(() => {
      const handle = document.querySelector("[data-role-card-handle]").getBoundingClientRect();
      return handle.left === 0 && handle.top >= document.querySelector(".rail").getBoundingClientRect().bottom && handle.bottom <= innerHeight;
    });
    const movedHandle = await page.locator("[data-role-card-handle]").boundingBox();
    await page.mouse.move(movedHandle.x + movedHandle.width / 2, movedHandle.y + movedHandle.height / 2);
    await page.mouse.down();
    await page.mouse.move(movedHandle.x + movedHandle.width / 2 + 12, movedHandle.y + movedHandle.height / 2, {steps:3});
    await page.mouse.up();
    await settled(page);
    assert.equal(await page.locator("[data-role-card-handle]").isVisible(), false);
    assert.equal(await page.evaluate(() => window.homeClicks), clicks);
    await page.setViewportSize({width:1000, height:700});
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
    await settled(phone);
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
    box = await phone.locator("#stageScene").boundingBox();
    await touch("touchStart", box.x + box.width - 20, box.y + 70);
    await touch("touchMove", box.width / 2 - 30, box.y + 70);
    await touch("touchEnd");
    await settled(phone);
    assert.equal(await phone.locator(".stage-region").getAttribute("data-role-card-dock-side"), "left");
    assert.equal(await phone.locator(".stage-region").getAttribute("data-role-card-docked"), "true");
    await phone.locator("[data-role-card-handle]").tap();
    await phone.locator("[data-role-card-handle]").waitFor({state:"hidden",timeout:5000});
    await settled(phone);
    assert.equal(await phone.locator("[data-role-card-handle]").isVisible(), false);
    await mobile.close();
  });

  assert.deepEqual(errors, []);
});
