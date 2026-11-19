'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
let chromium;
try { ({chromium} = require('playwright')); } catch (_) {}
const root = path.join(__dirname, '../pages/陪伴小屋');
const read = name => fs.readFileSync(path.join(root,name),'utf8');
test('范围提示支持悬停、键盘和触屏，并在裁剪容器与窄屏中完整显示', {skip:chromium ? false : '未安装 playwright'}, async t => {
  const browser = await chromium.launch({headless:true,args:['--disable-dev-shm-usage'],
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? {executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE} : {})});
  t.after(()=>browser.close());
  async function prepare(options) {
    const context = await browser.newContext(options), page = await context.newPage();
    page.setDefaultTimeout(6000);
    await page.setContent(`<meta name="viewport" content="width=device-width,initial-scale=1"><style>${read('tokens.css')}\n${read('workbench.css')}</style>
      <style>body{margin:0}.clip{position:absolute;right:8px;top:150px;width:180px;height:34px;overflow:clip;transform:translateZ(0)}
      #host{display:flex;justify-content:flex-end}#next{position:absolute;top:220px}</style>
      <div class="clip"><div id="host"></div></div><button id="next">其他操作</button>`);
    await page.addScriptTag({content:read('workbench-page.js')});
    await page.evaluate(()=>{
      document.documentElement.dataset.themeReady="true"; document.documentElement.dataset.roomReady="true";
      const wb=CompanionWorkbench, host=document.querySelector('#host');
      host.innerHTML='<span class="wb-save-context"><button type="button" id="save">保存</button>'+wb.scopeHintMarkup('保存到：测试群的覆盖设置\n继承自：璃的角色默认\n修改仅影响当前会话，不影响其他群聊。')+'</span>';
      window.saves=0;document.querySelector('#save').onclick=()=>window.saves++;
      host.addEventListener('click',wb.scopeHintClick);
      document.addEventListener('click',wb.scopeHintClick);
      document.addEventListener('keydown',wb.scopeHintKeydown);
      for (const event of ['pointerover','pointerout','focusin','focusout']) document.addEventListener(event,wb.scopeHintReset);
    });
    return {page,context};
  }
  await t.test('桌面悬停、键盘聚焦、Escape 和离开收起',async()=>{
    const {page,context}=await prepare({viewport:{width:900,height:600}});
    const hint=page.locator('[data-scope-hint]'),tip=page.locator('[role=tooltip]');
    assert.equal(await tip.isVisible(),false);
    await page.locator('#save').hover(); await tip.waitFor({state:'visible'});
    assert.equal(await page.evaluate(()=>saves),0);
    await page.mouse.move(10,10); await tip.waitFor({state:'hidden'});
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'save');
    await tip.waitFor({state:'visible'});
    await page.keyboard.press('Tab'); await tip.waitFor({state:'visible'});
    assert.equal(await hint.evaluate(node=>node===document.activeElement),true);
    await page.keyboard.press('Escape'); await tip.waitFor({state:'hidden'});
    await page.keyboard.press('Tab');
    await page.keyboard.press('Shift+Tab'); await tip.waitFor({state:'visible'});
    await page.keyboard.press('Tab'); await tip.waitFor({state:'hidden'});
    await context.close();
  });
  await t.test('触屏展开不保存，窄屏靠右提示避开边界与裁剪',async()=>{
    const {page,context}=await prepare({viewport:{width:360,height:300},hasTouch:true,isMobile:true});
    const hint=page.locator('[data-scope-hint]'),tip=page.locator('[role=tooltip]');
    await hint.tap();await tip.waitFor({state:'visible'});
    assert.equal(await hint.getAttribute('aria-expanded'),'true');
    const box=await tip.boundingBox();
    assert.ok(box.x>=0 && box.x+box.width<=360,JSON.stringify(box));
    assert.ok(box.y>=0 && box.y+box.height<=300,JSON.stringify(box));
    const visible=await tip.evaluate(node=>{
      const rect=node.getBoundingClientRect();
      return document.elementFromPoint(rect.left+5,rect.bottom-5)===node;
    });
    assert.equal(visible,true,'popover escapes overflow:clip and transformed ancestor');
    assert.equal(await page.evaluate(()=>saves),0);
    await hint.tap();await tip.waitFor({state:'hidden'});
    assert.equal(await hint.getAttribute('aria-expanded'),'false');
    await page.locator('#save').tap();assert.equal(await page.evaluate(()=>saves),1);
    await context.close();
  });
});
