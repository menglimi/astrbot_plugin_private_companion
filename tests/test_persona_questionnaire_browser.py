"""Exercise the original questionnaire through the real cottage and iframe bridge.

Only Dashboard responses and model outputs are synthetic; no live bot is used.
"""

import importlib.util
import unittest

from tests import test_persona_designer_browser as designer_support


BRIDGE = designer_support.HARNESS.split("<script>", 1)[1].split("</script>", 1)[0] + r"""
const host='astrbot_plugin_private_companion', base='targets/first/roles/role-a/persona';
window.questionnaire={calls:[], originals:{original:'原人格 A，保留空白。  \n\n',second:'原人格 B。'}, history:{
  original:[{version_id:'persona-draft-v000001',sequence:1,kind:'draft',draft_text:'原问卷已保存的旧稿',
    original_text:'原人格 A，保留空白。  \n\n',note:'旧版本',created_at:'2026-10-30T12:00:00Z'}],second:[]}};
const oldGet=AstrBotPluginPage.apiGet, oldPost=AstrBotPluginPage.apiPost;
AstrBotPluginPage.apiGet=async(endpoint,body)=>{
  if(endpoint==='page/targets')return {installation_id:'test',operator_id:'dashboard:admin',targets:[{
    target_id:'first',scope_kind:'private',participant_id:'200',platform_id:'qq-main',bot_id:'bot-a',
    account_id:'100',display_name:'测试会话',roles:[{role_id:'role-a',role_name:'测试角色',persona_ids:['original'],
    persona_api:'/'+host+'/'+base,state_api:'/'+host+'/targets/first/roles/role-a/state'}]}]};
  if(endpoint===base){
    questionnaire.calls.push({method:'GET',endpoint,body});
    const id=body?.persona_id||'original', versions=questionnaire.history[id];
    return {persona:{persona_id:id,name:id,text:questionnaire.originals[id],resolved:true},
      personas:Object.keys(questionnaire.originals).map(value=>({value,label:value})),
      versions:[...versions].reverse(),current:versions.at(-1)||null,count:versions.length};
  }
  return oldGet(endpoint,body);
};
AstrBotPluginPage.apiPost=async(endpoint,body)=>{
  if(!endpoint.startsWith(base+'/'))return oldPost(endpoint,body);
  questionnaire.calls.push({method:'POST',endpoint,body:clone(body)});
  if(endpoint===base+'/standardize')return {draft:{sections:{identity:'问卷生成的身份',personality:'谨慎且温和'},notes:[]}};
  if(endpoint===base+'/tryouts')return {variation:body.variation,scenarios:[{
    id:'everyday',title:'日常往来',context:'用户分享小事',message:'今天见到一只猫。',
    options:[{text:'它是不是也在等朋友？',tag:'自然'},{text:'猫很可爱。',tag:'简短'},{text:'说来听听。',tag:'倾听'}]}]};
  if(endpoint===base+'/style-rules')return {style:{text:'短句，保留自然的停顿。',examples:'用户：早。\n角色：早，今天还好吗？'}};
  if(endpoint===base+'/command'){
    const versions=questionnaire.history[body.persona_id], current=versions.at(-1);
    if(body.expected_current!==(current?.version_id||''))throw new Error('persona_draft_conflict');
    const previous=versions.find(v=>v.version_id===body.version_id);
    const version={version_id:'persona-draft-v'+String(versions.length+1).padStart(6,'0'),sequence:versions.length+1,
      kind:body.action==='restore'?'restore':'draft',draft_text:body.action==='restore'?previous.draft_text:body.text,
      original_text:body.original_text||questionnaire.originals[body.persona_id],note:body.note,created_at:'2026-11-02T12:00:00Z'};
    versions.push(version);return {version};
  }
  throw new Error('unexpected questionnaire route');
};
"""


@unittest.skipUnless(importlib.util.find_spec("playwright"), "optional Playwright dependency is not installed")
class OriginalQuestionnaireBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        designer_support.PersonaDesignerBrowserTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        designer_support.PersonaDesignerBrowserTests.tearDownClass.__func__(cls)

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 1000})
        self.context.set_default_timeout(10000)
        self.page = self.context.new_page()
        self.browser_errors = []
        self.page.on("pageerror", lambda error: self.browser_errors.append(str(error)))
        self.page.add_init_script(BRIDGE)
        self.page.goto(self.url.replace("test-designer", "index.html#manage/governance/questionnaire"))
        self.frame = self.page.frame_locator('[data-workspace="persona"] iframe')
        self.frame.locator("#persona-name").filter(has_text="original").wait_for()

    def tearDown(self):
        self.context.close()

    def test_original_questionnaire_full_workflow_and_old_version_restore(self):
        self.assertEqual(self.page.locator('[data-persona-recreation="questionnaire"]').get_attribute("aria-pressed"), "true")
        self.frame.locator("#load-version").click()
        self.assertEqual(self.frame.locator('[data-review-raw="right"]').input_value(), "原问卷已保存的旧稿")
        self.frame.locator('[data-prev="1"]').click()
        self.frame.locator('[data-answer="identity"]').fill("用户填写的职业")
        self.frame.locator('[data-strength="light"]').click()
        self.frame.locator("#generate-draft").click()
        self.frame.locator("#notice").filter(has_text="基础稿已生成").wait_for()
        generated = self.page.evaluate("questionnaire.calls.find(c=>c.endpoint.endsWith('/standardize')).body")
        self.assertEqual(generated["answers"]["identity"], "用户填写的职业")
        self.assertEqual(generated["strength"], "light")
        self.assertEqual(generated["persona_id"], "original")
        self.frame.locator('[data-review-section="identity"]').fill("审核后手改的身份")
        self.frame.locator('[data-next="3"]').click()
        self.frame.locator("#generate-tryouts").click()
        self.frame.locator('[data-scene-choice="everyday"]').first.check()
        self.frame.locator("#speech-preference").fill("短句")
        self.frame.locator('[data-next="4"]').click()
        self.frame.locator("#generate-style").click()
        self.frame.locator("#notice").filter(has_text="风格规则已生成").wait_for()
        selected = self.page.evaluate("questionnaire.calls.find(c=>c.endpoint.endsWith('/style-rules')).body")
        self.assertEqual(selected["selections"], [{"scene_id": "everyday", "text": "它是不是也在等朋友？"}])
        self.assertIn("审核后手改的身份", selected["draft_text"])
        self.assertEqual(self.frame.locator("#style-text").input_value(), "短句，保留自然的停顿。")
        self.frame.locator('[data-next="5"]').click()
        self.frame.locator('[data-replace="identity"]').click()
        self.frame.locator("#note").fill("问卷完整流程保存")
        self.frame.locator("#save").click()
        self.frame.locator("#notice").filter(has_text="已保存为第 2 版").wait_for()
        saved = self.page.evaluate("questionnaire.history.original[1]")
        self.assertIn("审核后手改的身份", saved["draft_text"])
        self.assertEqual(saved["original_text"], "原人格 A，保留空白。  \n\n")
        self.frame.locator(".history summary").click()
        self.frame.locator(".version-open").filter(has_text="第 1 版").click()
        self.frame.get_by_role("button", name="恢复到右侧", exact=True).click()
        self.frame.locator("#notice").filter(has_text="已恢复到右侧").wait_for()
        self.assertEqual(self.page.evaluate("questionnaire.history.original.length"), 3)
        self.assertEqual(self.page.evaluate("questionnaire.history.original[2].draft_text"), "原问卷已保存的旧稿")
        self.assertEqual(self.page.evaluate("questionnaire.originals.original"), "原人格 A，保留空白。  \n\n")
        self.assertFalse(self.browser_errors, self.browser_errors)

    def test_designer_failure_then_questionnaire_persona_selection_and_paste_generation(self):
        self.page.locator('[data-persona-recreation="designer"]').click()
        designer = self.page.frame_locator('[data-workspace="designer"] iframe')
        designer.locator("#choose-model").click()
        designer.locator("#model-select").select_option("design-model")
        designer.locator("#confirm-model").click()
        designer.locator('[data-mode="create"]').click()
        self.page.evaluate("mock.fail=true")
        designer.locator("#send-turn").click()
        designer.locator("#retry-turn").wait_for(state="visible")
        self.page.locator('[data-persona-recreation="questionnaire"]').click()
        self.frame.locator("#source-persona").select_option("second")
        self.frame.locator("#persona-name").filter(has_text="second").wait_for()
        self.frame.locator('input[name="source-mode"][value="paste"]').check()
        self.frame.locator("#source-text").fill("列表以外的粘贴人设")
        self.frame.locator("#generate-draft").click()
        self.frame.locator("#notice").filter(has_text="基础稿已生成").wait_for()
        sent = self.page.evaluate("questionnaire.calls.find(c=>c.endpoint.endsWith('/standardize')).body")
        self.assertEqual(sent["persona_id"], "second")
        self.assertEqual(sent["source_mode"], "paste")
        self.assertEqual(sent["source_text"], "列表以外的粘贴人设")
        self.assertEqual(self.page.evaluate("questionnaire.history.original.length"), 1)
        self.assertFalse(self.browser_errors, self.browser_errors)


if __name__ == "__main__":
    unittest.main()
