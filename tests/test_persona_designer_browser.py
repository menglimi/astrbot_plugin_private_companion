"""Optional real-browser checks. Install Playwright + Chromium to run these.

Only a synthetic Dashboard bridge is used; no AstrBot account or provider is called.
"""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
from threading import Thread
import unittest


PAGE_ROOT = Path(__file__).resolve().parents[1] / "pages" / "陪伴小屋"
HARNESS = r"""<!doctype html><html lang="zh-CN" data-room-ready><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="tokens.css">
<style>body{margin:0;background:var(--t-canvas);color:var(--t-text);font:14px system-ui}nav{padding:12px}
iframe{display:block;width:100%;border:0;min-height:800px}.product-workspace-loading{display:none}</style>
<script>
window.mock={hold:false,fail:false,saveFail:false,calls:[],reply:'先从她与你的关系开始。\n<svg onload="window.injected=true">只是设计材料</svg>'};
const clone=value=>JSON.parse(JSON.stringify(value));
window.AstrBotPluginPage={ready:async()=>({pluginName:'astrbot_plugin_private_companion'}),
apiGet:async(endpoint,body)=>{
  mock.calls.push({method:'GET',endpoint,body});
  if(endpoint==='page/targets')throw Object.assign(new Error('staging_not_initialized'),{code:'staging_not_initialized'});
  if(endpoint==='integration/manifest')return {extensions:[],providers:[]};
  if(endpoint==='page/display-preferences')return {preferences:null};
  if(endpoint==='persona-designer/options')return {models:[{id:'design-model',name:'共创模型'},{id:'design-model-2',name:'备用共创模型'}],
    personas:[{id:'configured',name:'配置人设',default:true},{id:'other',name:'另一人设'}],default_persona_id:'configured'};
  if(endpoint==='persona-designer/persona')return {id:body.id,text:'原文强调！ 原文强调！  \n\n'};
  if(endpoint==='persona-designer/drafts')return {items:(await testSaved()).map(s=>({id:s.id,title:s.title,created_at:'2026-11-01T09:00:00Z'})),next_before:null};
  if(endpoint==='persona-designer/draft')return (await testSaved()).find(s=>s.id===body.id);
  return {};
},apiPost:async(endpoint,body)=>{
  mock.calls.push({method:'POST',endpoint,body:clone(body)});
  if(endpoint==='persona-designer/turn'){
    if(mock.fail)throw Object.assign(new Error('persona_designer_unavailable'),{code:'persona_designer_unavailable'});
    const result={reply:mock.reply,options:['搭档','朋友'],confirmed:body.state.confirmed||'关系：搭档',stage:'personality',
      draft:body.action==='draft'?'# 人设\n保留用户的原话。':body.state.draft,lorebook:body.state.lorebook};
    if(mock.hold)return new Promise(resolve=>{mock.release=()=>resolve({status:'ok',data:result});mock.cancel=()=>resolve({status:'error',message:'persona_designer_cancelled',data:{code:'persona_designer_cancelled'}})});
    return {status:'ok',data:result};
  }
  if(endpoint==='persona-designer/cancel'){mock.cancel?.();return {cancelled:true};}
  if(endpoint==='persona-designer/drafts'){
    await testSave(clone(body));
    if(mock.saveFail){mock.saveFail=false;throw Object.assign(new Error('bridge_timeout'),{code:'bridge_timeout'});}
    return {status:'ok',data:{id:body.id,title:body.title}};
  }
  return {};
}};
</script><script src="persona-designer-memory.js"></script><script src="product-page.js"></script></head>
<body><nav><button id="open" onclick="mountDesigner()">新的她</button><button id="other" onclick="CompanionProduct.unmountWorkspace();document.getElementById('area').innerHTML='<p id=core>主体页面正常</p>'">其他页面</button></nav>
<div id="area"></div><template data-workspace-bundle="designer"><script src="workspace-bundles/designer.js"></script></template>
<script>window.mountDesigner=()=>{document.getElementById('area').innerHTML=CompanionProduct.workspace('designer');CompanionProduct.mountWorkspace();};CompanionProduct.connect().then(mountDesigner);</script></body></html>"""


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_): pass


@unittest.skipUnless(importlib.util.find_spec("playwright"), "optional Playwright dependency is not installed")
class PersonaDesignerBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(PAGE_ROOT)))
        cls.server_thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/test-designer"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True, args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.playwright.stop()
        cls.server.shutdown(); cls.server.server_close(); cls.server_thread.join()

    def setUp(self):
        self.saved = {}
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 1000})
        self.context.set_default_timeout(8000)
        self.page = self.context.new_page()
        self.browser_errors = []
        self.page.on("pageerror", lambda error: self.browser_errors.append(str(error)))
        self.page.expose_function("testSaved", lambda: list(reversed(list(self.saved.values()))))
        self.page.expose_function("testSave", lambda snapshot: self.saved.setdefault(snapshot["id"], snapshot))
        self.page.route("**/test-designer", lambda route: route.fulfill(body=HARNESS, content_type="text/html"))
        self.page.goto(self.url)
        self.frame = self.page.frame_locator(".product-workspace-frame")
        self.frame.locator("#choose-model").wait_for()

    def tearDown(self):
        self.context.close()

    def choose_model(self):
        self.frame.locator("#choose-model").click()
        self.frame.locator("#model-select").select_option("design-model")
        self.frame.locator("#confirm-model").click()

    def wait_reply(self):
        self.frame.locator(".chat-message.assistant").first.wait_for()
        self.frame.locator("#send-turn:not([disabled])").wait_for()

    def test_late_reply_modes_and_inputs_survive_internal_navigation_without_autosave(self):
        self.choose_model()
        self.frame.locator("#choose-persona").click()
        self.assertEqual(self.frame.locator("#persona-select").input_value(), "configured")
        self.frame.locator("#load-persona").click()
        self.frame.locator("#persona-dialog").wait_for(state="hidden")
        self.assertEqual(self.frame.locator("#source-text").input_value(), "原文强调！ 原文强调！  \n\n")
        self.page.evaluate("mock.hold=true")
        self.frame.locator("#chat-input").fill("先保留她的嘴硬。")
        self.frame.locator("#send-turn").click()
        self.page.wait_for_function("typeof mock.release==='function'")
        self.frame.locator("#chat-input").fill("下一条还没发完")
        self.page.locator("#other").click()
        self.assertEqual(self.page.locator("#core").inner_text(), "主体页面正常")
        self.page.evaluate("mock.release()")
        self.page.locator("#open").click(); self.wait_reply()
        self.assertEqual(self.frame.locator("#chat-input").input_value(), "下一条还没发完")
        self.assertIn("<svg", self.frame.locator(".chat-message.assistant").inner_text())
        self.assertIsNone(self.page.evaluate("window.injected"))
        self.frame.locator('[data-mode="create"]').click()
        self.frame.locator("#chat-input").fill("从零模式的想法")
        self.frame.locator('[data-mode="optimize"]').click()
        self.assertEqual(self.frame.locator("#chat-input").input_value(), "下一条还没发完")
        calls = self.page.evaluate("mock.calls.filter(c=>c.method==='POST'&&c.endpoint.startsWith('persona-designer/'))")
        self.assertEqual([c["endpoint"] for c in calls], ["persona-designer/turn"])
        self.assertFalse(self.saved)
        self.assertFalse(self.browser_errors, self.browser_errors)

    def test_real_cottage_entry_mounts_both_modes_and_keeps_new_design_on_return(self):
        self.page.add_init_script(HARNESS.split("<script>", 1)[1].split("</script>", 1)[0])
        self.page.goto(self.url.replace("test-designer", "index.html#manage/governance/questionnaire"))
        self.page.wait_for_function("typeof window.navigate==='function'")
        self.page.evaluate("document.documentElement.dataset.roomReady='';document.documentElement.dataset.themeReady='';CompanionProduct.setAppearance('motion','off');navigate('manage','governance','questionnaire')")
        self.assertEqual(self.page.locator("#self-tab-questionnaire").inner_text(), "人格再造")
        self.page.locator('[data-persona-recreation="designer"]').click()
        designer = self.page.frame_locator('[data-workspace="designer"] iframe')
        designer.locator("#source-text").fill("切换问卷后也要保留的原文")
        self.page.locator('[data-persona-recreation="questionnaire"]').click()
        self.assertEqual(self.page.locator('[data-persona-recreation="questionnaire"]').get_attribute("aria-pressed"), "true")
        self.page.locator('[data-persona-recreation="designer"]').click()
        self.assertEqual(designer.locator("#source-text").input_value(), "切换问卷后也要保留的原文")
        self.page.screenshot(path="/tmp/companion-persona-designer-preview/cottage-integration.png", full_page=True)

    def test_explicit_save_restores_conversation_edits_model_and_unsent_input_after_refresh(self):
        self.choose_model(); self.frame.locator('[data-mode="create"]').click()
        self.frame.locator("#chat-input").fill("她是一个登山搭档")
        self.frame.locator("#send-turn").click(); self.wait_reply()
        self.frame.locator("#chat-input").fill("还没发送")
        self.frame.locator("#generate-draft").click()
        self.page.wait_for_function("mock.calls.filter(c=>c.endpoint==='persona-designer/turn').length===2")
        self.frame.locator("#send-turn:not([disabled])").wait_for()
        self.assertEqual(self.frame.locator("#chat-input").input_value(), "还没发送")
        self.assertEqual(self.frame.locator("#draft-text").input_value(), "# 人设\n保留用户的原话。")
        self.frame.locator("#draft-text").fill("手改的正文，保留！保留！  \n")
        self.frame.locator(".lorebook-panel summary").click()
        self.frame.locator("#lorebook").fill("长篇背景")
        self.frame.locator("#save-draft").click()
        self.frame.locator("#draft-title").fill("登山搭档初稿")
        self.frame.locator("#confirm-save").click()
        self.page.wait_for_function("mock.calls.some(c=>c.endpoint==='persona-designer/drafts'&&c.method==='POST')")
        self.frame.locator("#notice").filter(has_text="已保存").wait_for()
        self.assertEqual(len(self.saved), 1)
        self.page.reload()
        self.frame.locator("#source-text").wait_for()
        self.assertEqual(self.frame.locator("#source-text").input_value(), "")
        self.assertEqual(self.frame.locator("#choose-model").inner_text(), "选择模型")
        self.frame.locator("#open-drafts").click()
        self.frame.locator(".saved-item button").click()
        self.frame.locator("#drafts-dialog").wait_for(state="hidden")
        self.assertEqual(self.frame.locator('[data-mode="create"]').get_attribute("aria-pressed"), "true")
        self.assertEqual(self.frame.locator("#chat-input").input_value(), "还没发送")
        self.assertEqual(self.frame.locator("#draft-text").input_value(), "手改的正文，保留！保留！  \n")
        self.assertEqual(self.frame.locator("#lorebook").input_value(), "长篇背景")
        self.assertIn("共创模型", self.frame.locator("#choose-model").inner_text())
        self.assertEqual(self.frame.locator(".chat-message").count(), 4)
        self.assertFalse(self.browser_errors, self.browser_errors)

    def test_model_error_stop_and_uncertain_save_retain_edits_and_core_is_usable(self):
        self.choose_model(); self.frame.locator('[data-mode="create"]').click()
        self.frame.locator("#chat-input").fill("保持这句话")
        self.frame.locator("#draft-text").fill("原稿")
        self.page.evaluate("mock.fail=true")
        self.frame.locator("#send-turn").click()
        self.frame.locator("#retry-turn").wait_for(state="visible")
        self.assertEqual(self.frame.locator("#chat-input").input_value(), "保持这句话")
        self.assertEqual(self.frame.locator("#draft-text").input_value(), "原稿")
        self.page.evaluate("mock.fail=false;mock.hold=true")
        self.frame.locator("#choose-model").click()
        self.frame.locator("#model-select").select_option("design-model-2")
        self.frame.locator("#confirm-model").click()
        self.frame.locator("#retry-turn").click()
        self.page.wait_for_function("typeof mock.cancel==='function'")
        self.frame.locator("#stop-turn").click()
        self.frame.locator("#notice").filter(has_text="已停止").wait_for()
        self.assertEqual(self.page.evaluate("mock.calls.filter(c=>c.endpoint==='persona-designer/turn').at(-1).body.state.provider_id"), "design-model-2")
        self.assertEqual(self.frame.locator("#chat-input").input_value(), "保持这句话")
        self.page.evaluate("mock.saveFail=true")
        self.frame.locator("#save-draft").click(); self.frame.locator("#confirm-save").click()
        self.frame.locator("#retry-save").wait_for(state="visible")
        self.frame.locator("#retry-save").click()
        self.frame.locator("#notice").filter(has_text="已保存").wait_for()
        self.assertEqual(len(self.saved), 1, "a timed-out save retries the same immutable version")
        self.page.evaluate("delete window.CompanionPersonaDesignerMemory")
        self.page.locator("#other").click(); self.page.locator("#open").click()
        self.frame.locator("#notice.error").wait_for(state="visible")
        self.page.locator("#other").click()
        self.assertEqual(self.page.locator("#core").inner_text(), "主体页面正常")

    def test_themes_density_mobile_layout_and_no_unsaved_browser_storage(self):
        for width, theme in ((1280, "courtyard"), (1280, "night"), (390, "dawn"), (320, "contrast")):
            self.page.set_viewport_size({"width": width, "height": 1100})
            self.page.evaluate("theme=>{CompanionProduct.setAppearance('motion','off');CompanionProduct.setAppearance('theme',theme);CompanionProduct.setAppearance('density','compact');CompanionProduct.setAppearance('radius','round');CompanionProduct.setAppearance('typeScale','l');}", theme)
            frame = self.page.frames[1]
            frame.wait_for_function("theme=>document.documentElement.dataset.theme===theme", arg=theme)
            self.assertLessEqual(frame.evaluate("document.body.scrollWidth"), width)
            self.assertEqual(frame.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--pad').trim()"), "14px")
            artifacts = Path("/tmp/companion-persona-designer-preview")
            artifacts.mkdir(exist_ok=True)
            self.frame.locator("main").screenshot(path=str(artifacts / f"{theme}-{width}.png"))
        self.frame.locator("#source-text").fill("只在内存中的人设材料")
        storage = self.page.evaluate("JSON.stringify({local:{...localStorage},session:{...sessionStorage}})")
        self.assertNotIn("只在内存中的人设材料", storage)
        self.page.goto("about:blank"); self.page.goto(self.url)
        self.assertEqual(self.frame.locator("#source-text").input_value(), "")
        self.assertFalse(self.browser_errors, self.browser_errors)


if __name__ == "__main__":
    unittest.main()
