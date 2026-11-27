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
    def _cleanup_resources(cls):
        designer_support.PersonaDesignerBrowserTests._cleanup_resources.__func__(cls)

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
        self.assertEqual(self.page.locator('[data-persona-recreation="questionnaire"]').get_attribute("aria-selected"), "true")
        # Slim outer hub bar has no redundant mode cards or 3-step guide bar; embedded iframe hides duplicate .pw-head
        self.assertEqual(self.page.locator(".persona-mode-grid").count(), 0)
        self.assertEqual(self.page.locator(".persona-guide").count(), 0)
        self.assertFalse(self.frame.locator(".pw-head").is_visible())
        self.assertTrue(self.frame.locator(".pw-context-bar").is_visible())
        # Step 1 optional clues start collapsed at 0/10
        self.assertFalse(self.frame.locator('[data-answer="identity"]').is_visible())
        self.assertEqual(self.frame.locator("#clues-count").inner_text().strip(), "0/10")

        self.frame.locator("#load-version").click()
        self.assertEqual(self.frame.locator('[data-review-raw="right"]').input_value(), "原问卷已保存的旧稿")
        self.frame.locator('[data-prev="1"]').click()
        self.frame.locator("#optional-clues > summary").click()
        self.frame.locator('[data-answer="identity"]').fill("用户填写的职业")
        self.assertEqual(self.frame.locator("#clues-count").inner_text().strip(), "1/10")
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
        # Step 5 auto-fills timestamped note and supports one-click adopt all
        self.assertTrue(self.frame.locator("#note").input_value().startswith("规范化整理 · "))
        self.frame.locator("#adopt-all-right").click()
        self.frame.locator("#note").fill("问卷完整流程保存")
        self.frame.locator("#save").click()
        self.frame.locator("#notice").filter(has_text="已保存为第 2 版").wait_for()
        self.assertTrue(self.frame.locator("#save-success-guide").is_visible())
        self.assertTrue(self.frame.locator("#copy-saved-full").is_visible())
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
        self.page.locator('[data-persona-recreation="create"]').click()
        designer = self.page.frame_locator('[data-workspace="designer"] iframe')
        # Auto-preselected default model enables sending immediately without opening model dialog
        designer.locator("#send-turn:not([disabled])").wait_for()
        self.page.evaluate("mock.fail=true")
        designer.locator("#send-turn").click()
        designer.locator("#retry-turn").wait_for(state="visible")
        self.page.locator('[data-persona-recreation="questionnaire"]').click()
        # Use modern searchable persona card picker
        self.frame.locator("#persona-search").fill("second")
        self.frame.locator('[data-persona-card="second"]').click()
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
        # Verify 360px mobile viewport has zero horizontal overflow in persona workshop
        self.page.set_viewport_size({"width": 360, "height": 840})
        frame = self.page.frames[1]
        self.assertLessEqual(frame.evaluate("document.documentElement.scrollWidth"), 360)
        self.assertFalse(self.browser_errors, self.browser_errors)

    def test_designer_handoff_sends_draft_into_workshop_paste_mode(self):
        self.page.locator('[data-persona-recreation="create"]').click()
        designer = self.page.frame_locator('[data-workspace="designer"] iframe')
        designer.locator("#draft-text").wait_for()
        designer.locator("#draft-text").fill("从新的她共创出来的角色设定初稿")
        designer.locator("#send-to-workshop").click()
        # Switches back to questionnaire tab and pre-fills paste mode automatically
        self.page.locator('[data-persona-recreation="questionnaire"][aria-selected="true"]').wait_for()
        self.frame.locator("#source-text").wait_for(state="visible")
        self.assertEqual(self.frame.locator("#source-text").input_value(), "从新的她共创出来的角色设定初稿")
        self.frame.locator("#notice").filter(has_text="新的她").wait_for()
        self.assertFalse(self.browser_errors, self.browser_errors)

    def test_workshop_card_picker_keyboard_and_structured_draft_clears_stale_raw_block(self):
        # 1. Hidden select excluded from tab order; roving tabindex + ArrowDown + Enter keeps focus after async load
        self.assertEqual(self.frame.locator("#source-persona").get_attribute("tabindex"), "-1")
        self.assertEqual(self.frame.locator("#source-persona").get_attribute("aria-hidden"), "true")
        first_card = self.frame.locator('#persona-picker-grid .persona-pick-card[data-persona-card="original"]')
        first_card.focus()
        self.assertEqual(first_card.get_attribute("tabindex"), "0")
        self.page.keyboard.press("ArrowDown")
        second_card = self.frame.locator('#persona-picker-grid .persona-pick-card[data-persona-card="second"]')
        self.assertEqual(second_card.get_attribute("tabindex"), "0")
        self.assertTrue(second_card.evaluate("el => el === document.activeElement"))
        self.page.keyboard.press("Enter")
        self.frame.locator("#persona-name").filter(has_text="second").wait_for()
        self.frame.locator('#persona-picker-grid .persona-pick-card[data-persona-card="second"]:not([disabled])').wait_for()
        selected_second = self.frame.locator('#persona-picker-grid .persona-pick-card[data-persona-card="second"]')
        self.assertEqual(selected_second.get_attribute("aria-selected"), "true")
        self.assertTrue(selected_second.evaluate("el => el === document.activeElement"),
                        "focus must be restored to the selected persona card after load completes")

        # 2. Switch back to original (which has unstructured saved draft v1), load it into rightRaw,
        #    then re-run standardize returning structured draft.sections -> ghost rightRaw must disappear
        self.frame.locator('[data-persona-card="original"]').click()
        self.frame.locator("#persona-name").filter(has_text="original").wait_for()
        self.frame.locator("#load-version").click()
        self.frame.locator("[data-review-raw]").wait_for(state="visible")
        self.frame.locator('[data-flow-btn="1"]').click()
        self.frame.locator("#generate-draft").click()
        self.frame.locator("#notice").filter(has_text="基础稿已生成").wait_for()
        self.assertEqual(self.frame.locator("[data-review-raw]").count(), 0)
        self.frame.locator('[data-flow-btn="5"]').click()
        self.assertEqual(self.frame.locator('[data-pair="raw"] [data-side="right"]').input_value(), "")
        self.assertFalse(self.browser_errors, self.browser_errors)


if __name__ == "__main__":
    unittest.main()
