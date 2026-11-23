const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/reply-diagnostics.js'), 'utf8');
const now = Date.parse('2026-10-24T08:40:36.918Z');
const window = {};
vm.runInNewContext(source, {window, Date: class extends Date { static now() { return now; } }});
const {renderRecords} = window.CompanionReplyDiagnostics;
const summary = item => renderRecords([item]).match(/<summary>([\s\S]*?)<\/summary>/)[1];

const native = {execution_path: 'astrbot_core', created_at: '2026-10-24T05:40:36.918433+00:00',
  state: 'completed', response_type: 'no_reply', delivery_state: 'not_started', send_returns: 0,
  turn_type: 'astrbot.core.reply', current_inputs: 1};

test('renderRecords returns the empty state without needing a host, selection, or page heading', () => {
  for (const input of [[], undefined, null, {}, [null]]) {
    const html = renderRecords(input);
    assert.match(html, /暂无近期回复记录/);
    assert.doesNotMatch(html, /<section|<h[1-6]|<button|<details|选择|读取|重试/);
  }
});

test('records can be expanded to see bounded metadata and validated fingerprints', () => {
  const privateValue = 'DO_NOT_RENDER';
  const rawHash = 'a'.repeat(64);
  const item = {state: 'complete', response_type: 'text_or_mixed', batch_id: privateValue,
    reason: privateValue, captured: {settings: {custom_prompt: privateValue}},
    turn_type: 'conversation.reply', current_inputs: 2, group_history_items: 3,
    parts: [{sequence: 1, chars: 8, text: privateValue}],
    injected_materials: [{owner: 'conversation.group_context', purpose: 'group_conversation_history', state: 'ready', body: privateValue}],
    offered_tools: ['slang.search'], filtered_tools: [{action: 'media.read', reason: 'disabled'}],
    rounds: [{round_no: 0, state: 'tools', tool_actions: [{action: 'slang.search', args: privateValue}]}],
    tool_calls: [{action: 'slang.search', state: 'succeeded', effect: 'none', result: privateValue}],
    calls: [{position: 0, failure_code: 'native_response_parse_failed', response_diagnostics: {
      completion_present: true, parse_state: 'native_rejected', finish_reason: 'stop',
      raw: {chars: 2, shape: 'json_array', sha256: rawHash, has_internal_markers: true, text: privateValue},
      parsed: null, visible: {chars: 1, shape: 'text', sha256: privateValue}, error: privateValue}}]};
  const html = renderRecords([item, native]);
  assert.equal((html.match(/<details class="reply-record">/g) || []).length, 2);
  assert.match(html, /最近 2 轮 · 本地时间/);
  assert.match(html, /任务 conversation\.reply · 有效输入 2 条 · 轮次 1 · 群历史 3 条 · 注入材料 1 项/);
  assert.match(html, /提供工具：slang\.search/);
  assert.match(html, /未提供：media\.read · disabled/);
  assert.match(html, /第 1 轮 · tools · 请求 slang\.search/);
  assert.match(html, /slang\.search · succeeded · none/);
  assert.match(html, /group_conversation_history/);
  assert.match(html, /native_response_parse_failed/);
  assert.match(html, /第 1 轮 · 宿主解析失败/);
  assert.match(html, /原始 2 字 · JSON 数组 · 含标签样式标记/);
  assert.match(html, new RegExp(`<details><summary>内容指纹</summary><p>原始 ${rawHash}`));
  assert.match(html, /分段 1 条/);
  assert.doesNotMatch(html, /DO_NOT_RENDER|<h[1-6]|<button/);
});

test('failure and host send returns are independent evidence in the collapsed summary', () => {
  const call = {state: 'unknown', provider: 'example/line-b', failure_code: 'UnprocessableEntityError'};
  // A later call can fail. Host send returns can also be for a notice in the same turn.
  for (const calls of [[call], [{state: 'returned'}, call]]) {
    const item = {...native, calls, state: 'complete', response_type: 'native_reply',
      delivery_state: 'returned', send_returns: 2, platform_confirmed: false};
    const row = summary(item);
    assert.match(row, /模型线路拒绝请求 422/);
    assert.match(row, /宿主发送返回 2 次/);
    assert.doesNotMatch(row, /首次|回退|送达|已读|发送成功/);
    assert.match(renderRecords([item]), /UnprocessableEntityError（example\/line-b）/);
  }
  assert.match(summary({...native, delivery_state: 'failed', calls: [call]}), /模型线路拒绝请求 422.*宿主发送返回 0 次.*宿主发送失败/);
  assert.match(summary({...native, calls: [{state: 'failed'}]}), /模型请求失败/);
  assert.match(summary({...native, delivery_state: 'unknown'}), /宿主发送返回 0 次.*发送结果未知/);
  assert.doesNotMatch(summary(native), /失败|送达/);
});

test('local timestamps and elapsed wait survive microseconds, missing dates, and request failures', () => {
  const row = summary({...native, state: 'generating', calls: [{failure_code: 'provider_timeout'}]});
  const start = new Date('2026-10-24T05:40:36.918Z');
  const pad = n => String(n).padStart(2, '0');
  const expected = `${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())} `
    + `${pad(start.getHours())}:${pad(start.getMinutes())}:${pad(start.getSeconds())}`;
  assert.ok(row.includes(`<time>${expected}</time>`));
  assert.match(row, /模型请求超时/);
  assert.match(row, /已等待 3 小时/);
  const invalid = summary({...native, state: 'awaiting_request', created_at: 'bad-date'});
  assert.match(invalid, /<time>时间未知<\/time>/);
  assert.doesNotMatch(invalid, /已等待|刚开始|NaN/);
});

test('metadata values are escaped in both the summary and expanded details', () => {
  const untrusted = '<img src=x onerror="x">&';
  const item = {...native, turn_type: untrusted, calls: [{failure_code: untrusted, provider: untrusted}],
    offered_tools: [untrusted], injected_materials: [{purpose: untrusted}]};
  const html = renderRecords([item]);
  assert.match(summary(item), /&lt;img src=x onerror=&quot;x&quot;&gt;&amp;/);
  assert.match(html, /提供工具：&lt;img/);
  assert.doesNotMatch(html, /<img/);
});
