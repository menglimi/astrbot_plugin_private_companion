"""Build an offline synthetic-data preview of the real private-member editor.
Usage: python tools/build_account_link_preview.py ABSOLUTE_OUTPUT_HTML
No production API, credentials, database or sender is used.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def build(output):
    css = (ROOT / "pages/关系与权限/style.css").read_text(encoding="utf-8")
    editor = (ROOT / "pages/关系与权限/member-editor.js").read_text(encoding="utf-8").replace("</", "<\\/")
    fixture = {
        "revision": 0, "group": None, "current": {},
        "supported": {"context": True, "settings": True, "identity": True, "proactive": True},
        "fields": {
            "profile": ["relationship.profile." + key for key in ("default_nickname", "user_gender", "user_birthday", "user_occupation", "role_relation", "interaction", "extra")],
            "reply": ["conversation.presentation." + key for key in ("enabled", "style_prompt", "voice_enabled", "voice_language", "max_segments", "image_understanding_private_enabled", "file_understanding_private_enabled")],
            "contact": ["proactive.contact." + key for key in ("enabled", "quiet_enabled", "quiet_start", "quiet_end", "note", "style_prompt")],
        },
        "candidates": [{"subject_id": "demo-" + platform, "route": {"platform": platform, "bot_id": "bot-" + platform,
            "account_id": "demo-account", "role_id": "demo-role"}, "native_account_id": "demo-user",
            "label": label} for platform, label in (("qq", "QQ · 示例用户（Bot 示例账号）"), ("wechat", "微信 · 示例用户（Bot 示例账号）"))]
    }
    bootstrap = r"""
const fixture = __FIXTURE__;
const selected = fixture.candidates[0];
const person = {subject_id:selected.subject_id,...selected.route,native_account_id:'10001',display_name:'示例成员',
  private_target:true,settings_available:true,identity_tier:'standard',persona_ids:['同一人格'],account_name:'示例 QQ 账号'};
const wrap=data=>({status:'ok',data});
const bridge={apiGet: async(path)=>path.endsWith('/account-links/history')?wrap({records:[
  {scope:{platform:'QQ',native_scope_id:'示例用户'},speaker:'user',at:'2026-11-15T09:20:00+08:00',text:'这一条保留 QQ 原记录归属。'},
  {scope:{platform:'微信',native_scope_id:'示例用户'},speaker:'assistant',at:'2026-11-15T09:21:00+08:00',text:'这一条保留微信原记录归属。'}],next_cursor:null}):
  path.endsWith('/account-links')?wrap(fixture):path.endsWith('/member-settings')?wrap({sections:{},section_errors:{}}):wrap({identity_tier:'standard',items:[]}),
  apiPost:async(path,body)=>{document.getElementById('result').textContent=JSON.stringify(body,null,2);return wrap(fixture);}};
window.CompanionPrivateMembers.mount(document.getElementById('panel'), person, {
  bridge,
  peerLabel: item => (item.platform === 'qq' ? 'QQ' : '微信') + ' ' + item.native_account_id,
  accountLabel: item => item.account_name || item.account_id
});
window.CompanionPrivateMembers.selectPane('links');
""".replace("__FIXTURE__", json.dumps(fixture, ensure_ascii=False))
    html = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>账号关联 · 离线验收</title><style>' + css + '\nbody{font-family:system-ui,Microsoft YaHei,sans-serif;margin:0;padding:24px;background:#f4f1ea;color:#282520}main{max-width:1024px;margin:auto}#panel{background:#fffdf8;padding:24px;border-radius:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}button,select,input{font:inherit}h1{font-size:22px}#demo-note{padding:12px;background:#f2e8d9;border-radius:8px}@media(max-width:600px){body{padding:8px}#panel{padding:12px}}</style><main><h1>关联用户 · 离线交互验收</h1><p id="demo-note">仅使用虚构账号。此页不会访问真实机器人或发送消息；字段清单为代表性子集，表单代码与正式页面相同。</p><div id="panel"></div><details><summary>模拟提交内容</summary><pre id="result">尚未提交</pre></details></main><script>' + editor + '</script><script>' + bootstrap + '</script></html>'
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    build(sys.argv[1])
