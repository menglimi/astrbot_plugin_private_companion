const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(__dirname + '/../pages/companion-panel/app.js', 'utf8');
function extract(name, next) {
  const start = source.indexOf('function ' + name + '(');
  assert(start >= 0);
  return source.slice(start, source.indexOf('\nfunction ' + next + '(', start));
}
let back, bindings = 0, renders = 0, requested;
const root = {
  innerHTML: '',
  querySelector: selector => selector === '[data-reaction-library-back]'
    ? { addEventListener: (_event, fn) => { back = fn; } } : null,
};
const context = vm.createContext({
  state: { activeTab: 'experimental', experimentalSubpage: 'reaction-library', reactionLibraryRequestSeq: 0, reactionLibraryPage: 2 },
  $: () => root,
  window: { clearTimeout() {} },
  renderReactionLibraryWorkspace: () => '<section id="reactionLibraryWorkspace">grid</section>',
  bindReactionLibraryActions: () => { bindings++; },
  visibleExperimentalFeatureKeys: () => ['enable_reaction_expression_experiment'],
  renderExperimentalSubpage: () => 'settings',
  bindExperimentalSubpageActions() {},
  URLSearchParams,
  fetchJson: async url => { requested = url; return {items: [], total: 0}; },
  scheduleReactionLibraryPoll() {},
});
vm.runInContext(extract('renderExperimentalPage', 'reflectExperimentalToggleChange'), context);
context.renderExperimentalPage();
assert(root.innerHTML.includes('reactionLibraryWorkspace'));
assert(root.innerHTML.includes('返回表情表达'));
assert.equal(bindings, 1);
back();
assert.equal(context.state.experimentalSubpage, 'enable_reaction_expression_experiment');
assert.equal(root.innerHTML, 'settings');
assert(!source.includes('data-scroll-target="reactionLibraryWorkspace"'));
assert(!source.includes('${reactionLibraryHtml}'));
assert(source.includes('data-reaction-library-open'));
const start = source.indexOf('async function loadReactionLibrary(');
const end = source.indexOf('\nfunction scheduleReactionLibraryPoll(', start);
vm.runInContext(source.slice(start, end), context);
context.renderExperimentalPage = () => { renders++; };
(async () => {
  context.state.experimentalSubpage = 'reaction-library';
  await context.loadReactionLibrary();
  assert(requested.includes('page_size=24'));
  assert(requested.includes('page=2'));
  assert.equal(renders, 2);
  context.state.experimentalSubpage = 'enable_reaction_expression_experiment';
  await context.loadReactionLibrary();
  assert.equal(renders, 2, 'late response must not replace settings page');
  console.log('PASS: subpage render/back, 24-item pagination, late-response navigation guard');
})().catch(error => { console.error(error); process.exitCode = 1; });
