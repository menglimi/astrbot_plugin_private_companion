const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'pages/陪伴小屋/app.js'), 'utf8');
const styles = fs.readFileSync(path.join(root, 'pages/陪伴小屋/styles.css'), 'utf8');
const html = fs.readFileSync(path.join(root, 'pages/陪伴小屋/index.html'), 'utf8');
const catalogPath = path.join(root, 'pages/陪伴小屋/catalog.html');

test('config search batches input and caches the field index', () => {
  assert.match(app, /SEARCH_INPUT_DELAY_MS\s*=\s*90/);
  assert.match(app, /SEARCH_ENTRY_CACHE_MS\s*=\s*1500/);
  assert.match(app, /function scheduleSearchRender\(/);
  assert.match(app, /function invalidateSearchCache\(/);
  assert.match(app, /event\.isComposing/);
});

test('config search highlights matches and preserves the accessible listbox', () => {
  assert.match(app, /function highlightSearchText\(/);
  assert.match(app, /<mark>\$\{escapeHTML\(part\)\}<\/mark>/);
  assert.match(styles, /\.search-result mark\{/);
  assert.match(html, /id="globalSearch"[^>]*aria-autocomplete="list"/);
});

test('the full function map is packaged and reachable from the navigation overlay', () => {
  assert.ok(fs.existsSync(catalogPath), 'catalog.html must be packaged');
  assert.match(app, /catalog\.html/);
  assert.match(app, /全部功能地图/);
  assert.match(app, /companion-catalog-snapshot/);
  assert.match(app, /asset_token/);
  assert.match(fs.readFileSync(catalogPath, 'utf8'), /function readLive\(/);
  assert.match(fs.readFileSync(catalogPath, 'utf8'), /function pageHref\(/);
});
