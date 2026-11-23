"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../astrbot_host/native_extensions/game/web/native.js"), "utf8");
const gomokuSource = fs.readFileSync(path.join(__dirname, "../astrbot_host/native_extensions/game/web/native-gomoku.js"), "utf8");
const inputSource = fs.readFileSync(path.join(__dirname, "../astrbot_host/native_extensions/game/web/native-gomoku-input.js"), "utf8");

class Element {
  constructor(tag = "div") {
    this.tag = tag; this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {};
    this.className = ""; this.text = ""; this.style = {setProperty() {}};
    this.classList = {
      add: (...values) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...values])].join(" "); },
      toggle: (name, enabled) => { this.className = this.className.split(/\s+/).filter(value => value !== name).join(" ");
        if (enabled) this.classList.add(name); },
    };
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(" "); }
  set innerHTML(value) { this._innerHTML = String(value); this.children = []; this.text = this._innerHTML.replace(/<[^>]*>/g, ""); }
  get innerHTML() { return this._innerHTML || ""; }
  append(...children) { this.children.push(...children); children.forEach(child => { child.parent = this; }); }
  appendChild(child) { this.append(child); child.parent = this; return child; }
  replaceChildren(...children) { this.text = ""; this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelector(selector) {
    return descendants(this).slice(1).find(child => selector.startsWith(".")
      ? child.className.split(/\s+/).includes(selector.slice(1)) : child.tag === selector) || null;
  }
  getBoundingClientRect() { return {left: 100, top: 100, width: 600, height: 600}; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
  focus() { if (this.onFocus) this.onFocus(this); }
  setPointerCapture(id) { this.capturedPointer = id; }
  releasePointerCapture(id) { if (this.capturedPointer === id) this.capturedPointer = null; }
  getContext(kind) {
    if (this.tag !== "canvas" || kind !== "2d") return null;
    if (!this._ctx) {
      this._ctx = {ops: [], clearRect(...a) { this.ops.push(["clearRect", ...a]); },
        fillRect(...a) { this.ops.push(["fillRect", ...a]); },
        beginPath() { this.ops.push(["beginPath"]); },
        moveTo(...a) { this.ops.push(["moveTo", ...a]); },
        lineTo(...a) { this.ops.push(["lineTo", ...a]); },
        stroke() { this.ops.push(["stroke"]); }};
    }
    return this._ctx;
  }
  toDataURL(mime = "image/png") {
    return this.tag === "canvas" ? `data:${mime};base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFxoAAAAASUVORK5CYII=` : "";
  }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
}

function descendants(element) { return [element, ...element.children.flatMap(descendants)]; }

async function render(game, {confirm = false, fetchAction, fetchState} = {}) {
  const elements = new Map();
  const $ = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  $("connectionBadge").append(new Element("span"));
  const requests = [];
  const events = {};
  let poll;
  const document = {
    getElementById: $,
    activeElement: null,
    createElement: tag => {
      const element = new Element(tag); element.onFocus = value => { document.activeElement = value; }; return element;
    },
    querySelector: selector => {
      const focusMatch = selector.match(/^\[data-focus-key="([^"]+)"\]$/);
      if (focusMatch) {
        for (const root of elements.values()) {
          const hit = descendants(root).find(node => node.dataset.focusKey === focusMatch[1]);
          if (hit) return hit;
        }
      }
      return null;
    },
  };
  document.createElementNS = (_, tag) => document.createElement(tag);
  const context = vm.createContext({document,
    window: {location: {pathname: "/native/fixture-ticket"}, matchMedia: () => ({matches: confirm}),
      setInterval(callback) { poll = callback; return 1; }, clearInterval() {},
      addEventListener(name, callback) { events[name] = callback; },
      removeEventListener(name, callback) { if (events[name] === callback) delete events[name]; }},
    fetch: async (url, options) => {
      requests.push({url, options});
      const value = options.method === "POST" ? await (fetchAction?.(JSON.parse(options.body)) ?? {game})
        : await (fetchState?.() ?? {game});
      return {ok: true, json: async () => value};
    },
  });
  vm.runInContext(inputSource, context);
  vm.runInContext(gomokuSource, context);
  vm.runInContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal($("notice").textContent, "");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].options.method, undefined);
  return {$, requests, poll, events, document};
}

function boardGame(kind, human, status = "active", outcome = null) {
  const board = Array.from({length: kind === "gomoku" ? 15 : 3}, () => Array(kind === "gomoku" ? 15 : 3).fill(0));
  board[0][0] = 1; board[1][2] = 2;
  return {game_type: kind, status, outcome, moves: [[0, 0, 1], [1, 2, 2]], state: {
    board, human_color: human, bot_color: human === 1 ? 2 : 1,
    human_mark: kind === "tictactoe" ? 1 : undefined, move_count: 2, turn: human,
    pieces: [{piece_id: "piece-1", row: 0, column: 0, color: 1, x: .5, y: .5},
             {piece_id: "piece-2", row: 1, column: 2, color: 2, x: .5, y: .5}],
  }};
}

test("五子棋按实际黑白颜色绘制，用户执白也不反色，落子历史归属与行列完整", async () => {
  for (const human of [1, 2]) {
    const {$} = await render(boardGame("gomoku", human));
    const pieces = descendants($("gameBoard")).filter(node => node.dataset.pieceId);
    assert.match(pieces[0].children[0].className, /stone-black/);
    assert.match(pieces[1].children[0].className, /stone-white/);
    const scores = $("gameSummary").children[0].children;
    assert.equal(scores[0].children[1].textContent, human === 1 ? "黑棋" : "白棋");
    assert.equal(scores[1].children[1].textContent, human === 1 ? "白棋" : "黑棋");
    const history = $("gameHistory").children[0].children.filter(child => child.tag === "span");
    assert.equal(history[0].textContent, human === 2 ? "你" : "陪伴者");
    assert.equal(history[1].textContent, "第 2 行，第 3 列");
  }
});

const flush = () => new Promise(resolve => setImmediate(resolve));
const click = (element) => element.listeners.click({stopPropagation() {}, clientX: 1, clientY: 999});
const point = ($, row, column) => descendants($("gameBoard")).find(element =>
  element.dataset.focusKey === `point-${row}-${column}`);
const byText = (host, text) => descendants(host).find(element => element.tag === "button" && element.textContent.trim() === text);

test("正常对弈点击交叉点自动应手，旧偏移棋子归位，不会拿起对方棋子", async () => {
  const game = boardGame("gomoku", 2);
  game.state.pieces[0].x = .02; game.state.pieces[0].y = .98;
  const {$, requests} = await render(game);
  const stone = descendants($("gameBoard")).find(element => element.dataset.pieceId === "piece-1");
  assert.equal(stone.style.left, `${(.5 / 15) * 100}%`);
  assert.equal(stone.disabled, true);
  assert.equal(stone.listeners.pointerdown, undefined);
  assert.equal($("pieceTray").hidden, true);
  click(point($, 7, 7));
  await flush();
  const actions = requests.filter(request => request.options.method === "POST");
  assert.equal(actions.length, 1);
  assert.deepEqual(JSON.parse(actions[0].options.body), {action: "move", row: 7, column: 7, x: .5, y: .5});
});

test("手机先选点再确认，取消与轮询不落子，未变棋盘保持原 DOM", async () => {
  const {$, requests, poll} = await render(boardGame("gomoku", 1), {confirm: true});
  click(point($, 7, 7));
  const board = $("gameBoard").children[0];
  await poll();
  assert.equal($("gameBoard").children[0], board);
  assert.match($("actionControls").textContent, /落点 H8/);
  assert.equal(requests.filter(request => request.options.method === "POST").length, 0);
  click(byText($("actionControls"), "取消"));
  assert.equal(byText($("actionControls"), "确认落子").disabled, true);
  click(point($, 6, 8));
  const confirmButton = byText($("actionControls"), "确认落子");
  click(confirmButton); click(confirmButton);
  await flush();
  const actions = requests.filter(request => request.options.method === "POST");
  assert.equal(actions.length, 1);
  assert.equal(JSON.parse(actions[0].options.body).row, 6);
  assert.equal(JSON.parse(actions[0].options.body).column, 8);
});

test("自由摆子单独取子，拖出棋盘取消，返回对弈提供对方应手入口", async () => {
  const game = boardGame("gomoku", 1);
  const {$, requests, events} = await render(game);
  click(byText($("boardToolbar"), "自由摆子"));
  assert.equal($("pieceTray").hidden, false);
  const tray = byText($("pieceTray"), "黑棋 · 你");
  tray.listeners.pointerdown({pointerId: 1, clientX: 110, clientY: 110, preventDefault() {}});
  events.pointermove({pointerId: 1, clientX: 650, clientY: 450});
  events.pointerup({pointerId: 1, clientX: 900, clientY: 450});
  assert.equal(requests.filter(request => request.options.method === "POST").length, 0);
  assert.match($("notice").textContent, /已取消拖动/);
  assert.equal(events.pointermove, undefined);
  game.state.turn = 2;
  click(byText($("boardToolbar"), "返回对弈"));
  const reply = byText($("actionControls"), "让陪伴者接着下");
  assert.equal(reply.disabled, false);
  click(reply); await flush();
  assert.deepEqual(JSON.parse(requests.find(request => request.options.method === "POST").options.body), {action: "reply"});
});

const pointer = (x, y, extras = {}) => ({pointerId: 1, clientX: x, clientY: y, button: 0,
  preventDefault() {}, stopPropagation() {}, ...extras});
const part = ($, className) => descendants($("gameBoard")).find(element => element.className.split(/\s+/).includes(className));

test("取子、换子、再次放回和预选只更新反馈，不重建棋盘或向服务端落子", async () => {
  const {$, requests, poll} = await render(boardGame("gomoku", 1), {confirm: true});
  const board = $("gameBoard").children[0];
  click(point($, 7, 7)); click(point($, 6, 8));
  assert.equal($("gameBoard").children[0], board);
  assert.match(part($, "gomoku-interaction-readout").textContent, /I7/);
  assert.equal(part($, "gomoku-lens").hidden, false);
  click(byText($("boardToolbar"), "自由摆子"));
  const freeBoard = $("gameBoard").children[0];
  const tray = byText($("pieceTray"), "黑棋 · 你");
  click(tray); await poll();
  assert.equal($("gameBoard").children[0], freeBoard);
  assert.equal(tray.attributes["aria-pressed"], "true");
  click(tray);
  assert.equal(tray.attributes["aria-pressed"], "false");
  assert.equal(byText($("actionControls"), "放回棋子").disabled, true);
  assert.equal(requests.filter(request => request.options.method === "POST").length, 0);
});

test("拖动既有棋子捕获指针、拿起源棋子、即时预览吸附落点，只提交一次", async () => {
  const {$, requests, events, poll} = await render(boardGame("gomoku", 1));
  click(byText($("boardToolbar"), "自由摆子"));
  const piece = part($, "gomoku-piece");
  piece.listeners.pointerdown(pointer(120, 120));
  assert.equal(piece.capturedPointer, 1);
  events.pointermove(pointer(400, 420));
  assert.match(piece.className, /piece-lifted/);
  assert.equal(part($, "gomoku-drag-piece").style.left, "300px");
  assert.equal(part($, "gomoku-drag-piece").style.top, "320px");
  assert.equal(part($, "gomoku-target").style.left, "50%");
  assert.match(part($, "gomoku-interaction-readout").textContent, /H9/);
  await poll();
  assert.equal(requests.length, 1);
  events.pointerup(pointer(400, 420));
  click(piece); // Browser's trailing click after pointerup must not select again.
  await flush();
  const actions = requests.filter(request => request.options.method === "POST");
  assert.equal(actions.length, 1);
  assert.deepEqual(JSON.parse(actions[0].options.body), {action: "interact", source: {kind: "board", piece_id: "piece-1", color: 1},
    target: {row: 8, column: 7, x: .5, y: .5}, interaction: "drag"});
  assert.equal(events.pointermove, undefined);
  assert.equal(piece.capturedPointer, null);
  assert.equal(part($, "gomoku-drag-piece"), undefined);
});

test("占用落点先在棋盘提示，拖到原位或拖出棋盘放回，不消耗一次请求", async () => {
  const {$, requests, events} = await render(boardGame("gomoku", 1));
  click(byText($("boardToolbar"), "自由摆子"));
  const piece = part($, "gomoku-piece");
  piece.listeners.pointerdown(pointer(120, 120));
  events.pointermove(pointer(200, 160));
  assert.match(part($, "gomoku-target").className, /invalid/);
  assert.match(part($, "gomoku-interaction-readout").textContent, /已有棋子/);
  events.pointerup(pointer(200, 160));
  assert.doesNotMatch(piece.className, /piece-lifted/);
  piece.listeners.pointerdown(pointer(120, 120));
  events.pointermove(pointer(400, 420));
  events.pointerup(pointer(120, 120));
  assert.match($("notice").textContent, /放回/);
  piece.listeners.pointerdown(pointer(120, 120));
  events.pointermove(pointer(800, 420));
  assert.equal(part($, "gomoku-drag-piece").style.left, "700px");
  assert.equal(part($, "gomoku-target").hidden, true);
  events.pointerup(pointer(800, 420));
  assert.match($("notice").textContent, /取消拖动/);
  assert.equal(requests.filter(request => request.options.method === "POST").length, 0);
});

test("触摸滑动选点有放大预览，松手只预选，确认后执行正常落子", async () => {
  const {$, requests, events} = await render(boardGame("gomoku", 1), {confirm: true});
  const start = point($, 7, 7);
  start.listeners.pointerdown(pointer(400, 400, {pointerType: "touch"}));
  assert.equal(part($, "gomoku-lens").hidden, false);
  events.pointermove(pointer(480, 360, {pointerType: "touch"}));
  assert.match(part($, "gomoku-interaction-readout").textContent, /J7/);
  assert.equal(part($, "gomoku-drag-piece").style.top, "228px");
  events.pointerup(pointer(480, 360, {pointerType: "touch"}));
  click(start);
  assert.equal(requests.filter(request => request.options.method === "POST").length, 0);
  assert.match($("actionControls").textContent, /落点 J7/);
  click(byText($("actionControls"), "确认落子"));
  await flush();
  assert.deepEqual(JSON.parse(requests.find(request => request.options.method === "POST").options.body),
    {action: "move", row: 6, column: 9, x: .5, y: .5});
});

test("Escape、触摸取消和窗口失焦会释放指针并还原棋子", async () => {
  const {$, requests, events} = await render(boardGame("gomoku", 1));
  click(byText($("boardToolbar"), "自由摆子"));
  const piece = part($, "gomoku-piece");
  for (const cancel of [() => events.keydown({key: "Escape", preventDefault() {}}),
    () => events.pointercancel(pointer(400, 420)), () => events.blur()]) {
    piece.listeners.pointerdown(pointer(120, 120));
    events.pointermove(pointer(400, 420));
    cancel();
    assert.equal(events.pointermove, undefined);
    assert.equal(piece.capturedPointer, null);
    assert.doesNotMatch(piece.className, /piece-lifted/);
    assert.equal(part($, "gomoku-drag-piece"), undefined);
  }
  assert.equal(requests.filter(request => request.options.method === "POST").length, 0);
});

test("请求未完成时暂停轮询，旧读取不会覆盖已提交的新棋盘", async () => {
  const oldGame = boardGame("gomoku", 1);
  const newGame = JSON.parse(JSON.stringify(oldGame));
  newGame.moves.push([7, 7, 1], [7, 8, 2]);
  newGame.state.board[7][7] = 1; newGame.state.board[7][8] = 2; newGame.state.move_count = 4;
  let finishPoll;
  let finishAction;
  let reads = 0;
  const {$, requests, poll} = await render(oldGame, {
    fetchState: () => ++reads === 1 ? {game: oldGame} : new Promise(resolve => { finishPoll = resolve; }),
    fetchAction: () => new Promise(resolve => { finishAction = resolve; }),
  });
  const slowPoll = poll();
  click(point($, 7, 7));
  await poll();
  assert.equal(reads, 2);
  finishAction({game: newGame}); await flush();
  finishPoll({game: oldGame}); await slowPoll;
  assert.match($("gameSummary").textContent, /已落子 4 手/);
  assert.equal(requests.filter(request => request.options.method === "POST").length, 1);
});

test("响应丢失后只核对状态，保留已落棋子，不自动重放操作", async () => {
  const game = boardGame("gomoku", 1);
  const committed = JSON.parse(JSON.stringify(game));
  committed.state.move_count = 4;
  let reads = 0;
  const {$, requests, poll} = await render(game, {
    fetchState: () => ({game: ++reads === 1 ? game : committed}),
    fetchAction: () => { throw new Error("connection lost"); },
  });
  click(point($, 7, 7)); await flush(); await poll();
  assert.match($("gameSummary").textContent, /已落子 4 手/);
  assert.equal(requests.filter(request => request.options.method === "POST").length, 1);
});

test("自由摆子中的对局结束后回到只读棋盘，不保留拿子状态", async () => {
  let state = boardGame("gomoku", 1);
  const {$, poll, requests} = await render(state, {fetchState: () => ({game: state})});
  click(byText($("boardToolbar"), "自由摆子"));
  click(byText($("pieceTray"), "黑棋 · 你"));
  state = boardGame("gomoku", 1, "finished", "bot");
  await poll();
  assert.equal($("gameBoard").children[0].dataset.mode, "play");
  assert.equal($("pieceTray").hidden, true);
  assert.equal($("actionControls").children.length, 0);
  assert.equal(requests.filter(request => request.options.method === "POST").length, 0);
});

test("结束棋局标明最后一手和真实五子连线，可选择显示每一手编号", async () => {
  const game = boardGame("gomoku", 2, "finished", "bot");
  game.state.board = Array.from({length: 15}, () => Array(15).fill(0));
  game.moves = Array.from({length: 5}, (_, index) => [7 + index, 7 - index, 1]);
  game.moves.forEach(([row, column, color]) => { game.state.board[row][column] = color; });
  game.state.winner = 1; game.state.last_move = [11, 3];
  const {$} = await render(game);
  const stones = descendants($("gameBoard")).filter(element => element.dataset.pieceId);
  assert.equal(stones.filter(element => element.className.includes("winning-piece")).length, 5);
  assert.equal(stones.filter(element => element.className.includes("last-move")).length, 1);
  const line = descendants($("gameBoard")).find(element => element.attributes.class === "gomoku-winning-line");
  assert.equal(line.children[0].attributes.x1, "225");
  assert.equal(line.children[0].attributes.x2, "105");
  assert.equal(line.children[0].attributes.y2, "345");
  assert.equal(stones[4].attributes["aria-label"].includes("最后一手"), true);
  click(byText($("boardToolbar"), "显示手数"));
  assert.equal(descendants($("gameBoard")).find(element => element.dataset.pieceId === "piece-5").children[0].textContent, "5");
});

test("已结束的棋局展示真实输赢，不再显示轮到谁或思考中", async () => {
  for (const [outcome, label] of [["player", "你赢了"], ["bot", "陪伴者赢了"], ["draw", "本局平局"]]) {
    const {$} = await render(boardGame("gomoku", 2, "finished", outcome));
    assert.equal($("roundState").textContent, "本局结束");
    assert.match($("gameSummary").textContent, new RegExp(label));
    assert.doesNotMatch($("gameSummary").textContent, /轮到|思考中/);
    assert.equal($("actionControls").children.length, 0);
    assert.equal($("pieceTray").hidden, true);
  }
});

test("井字棋仍显示圈叉，并按棋子标记区分双方", async () => {
  const {$} = await render(boardGame("tictactoe", 1));
  const pieces = descendants($("gameBoard")).filter(node => node.dataset.pieceId);
  assert.equal(pieces[0].textContent, "×");
  assert.equal(pieces[1].textContent, "○");
  assert.equal($("gameHistory").children[0].children[0].textContent, "陪伴者");
  assert.equal($("gameHistory").children[1].children[0].textContent, "你");
});

test("原生页面 Brand 不再指向无效旧根路径", () => {
  const html = fs.readFileSync(path.join(__dirname, "../astrbot_host/native_extensions/game/web/native.html"), "utf8");
  assert.doesNotMatch(html, /class="game-brand"[^>]*href="\/"/);
  assert.match(html, /<div class="game-brand"[\s>]/);
});

test("二十一点同时兼容符号花色与英文花色名并正确标红", async () => {
  const game = {
    game_type: "blackjack", status: "active", outcome: null, moves: [],
    state: {
      phase: "player_turn", dealer_hidden: false, dealer_total: 18,
      dealer_cards: [{rank: "A", suit: "♥"}, {rank: "7", suit: "spades"}],
      hands: {"1": {value: 20, cards: [{rank: "K", suit: "diamonds"}, {rank: "Q", suit: "♣"}, {rank: "10", suit: "♦"}, {rank: "9", suit: "hearts"}]}},
    },
  };
  const {$} = await render(game);
  const cards = descendants($("gameBoard")).filter(el => el.className.split(/\s+/).includes("playing-card"));
  assert.equal(cards.length, 6);
  const byRank = Object.fromEntries(cards.map(c => [c.children[0].textContent, c.className.split(/\s+/)]));
  assert.equal(byRank["A"].includes("red"), true);
  assert.equal(byRank["K"].includes("red"), true);
  assert.equal(byRank["10"].includes("red"), true);
  assert.equal(byRank["9"].includes("red"), true);
  assert.equal(byRank["7"].includes("red"), false);
  assert.equal(byRank["Q"].includes("red"), false);
});

test("中国象棋按执红/执黑自适应文案、翻转棋盘并高亮合法落点", async () => {
  const emptyBoard = () => Array.from({length: 10}, () => Array(9).fill("."));
  const blackBoard = emptyBoard();
  blackBoard[0][1] = "n";
  blackBoard[9][1] = "N";
  const blackGame = {
    game_type: "xiangqi", status: "active", outcome: null, moves: [],
    state: {board: blackBoard, human_side: "black", turn: "black", move_count: 1, legal_moves: [[0, 1, 2, 2]]},
  };
  const {$, requests} = await render(blackGame);
  assert.match($("actionControls").textContent, /点击黑方棋子开始选择/);
  const cells = descendants($("gameBoard")).filter(el => el.className.split(/\s+/).includes("xiangqi-cell"));
  assert.equal(cells.length, 90);
  // 执黑时棋盘翻转，第 10 行(row 9)在上方，第 1 行(row 0)在下方
  assert.match(cells[0].attributes["aria-label"], /^10 行 9 列/);
  const knightCell = cells.find(cell => cell.attributes["aria-label"].startsWith("1 行 2 列"));
  click(knightCell);
  const targetCell = descendants($("gameBoard")).find(cell => cell.attributes["aria-label"]?.startsWith("3 行 3 列"));
  assert.equal(targetCell.className.split(/\s+/).includes("legal-target"), true);
  click(targetCell);
  await flush();
  const post = requests.find(r => r.options.method === "POST");
  assert.deepEqual(JSON.parse(post.options.body), {action: "move", from_row: 0, from_column: 1, to_row: 2, to_column: 2});

  const redGame = {
    game_type: "xiangqi", status: "active", outcome: null, moves: [],
    state: {board: blackBoard, human_side: "red", turn: "red", move_count: 0, legal_moves: [[9, 1, 7, 2]]},
  };
  const redView = await render(redGame);
  assert.match(redView.$("actionControls").textContent, /点击红方棋子开始选择/);
});

test("海龟汤展示非空题目标题、完整渲染历史问答与提示正文，并在用尽后禁用提示按钮", async () => {
  const active = {
    game_type: "turtle_soup", status: "active", outcome: null,
    moves: [["hint", "注意时间差"]],
    state: {
      puzzle: {title: "午夜敲门声", surface: "半夜门响了三声，开门后空无一人，他却长舒一口气。"},
      question_count: 1, discovered_fact_count: 1, key_fact_count: 3, hints_used: 1, hint_limit: 2,
      entries: [
        {kind: "question", prompt: "门外的人是熟人吗？", response: "是"},
        {kind: "hint", prompt: "提示", response: "注意时间差"},
      ],
    },
  };
  const afterHint = JSON.parse(JSON.stringify(active));
  afterHint.state.hints_used = 2;
  afterHint.state.entries.push({kind: "hint", prompt: "提示", response: "敲门其实是约定信号"});
  const {$, requests} = await render(active, {fetchAction: () => ({game: afterHint})});
  const headings = descendants($("gameBoard")).filter(el => el.tag === "h2");
  assert.equal(headings[0].textContent, "题面 · 午夜敲门声");
  assert.equal(headings[0].className, "");
  assert.match($("gameBoard").textContent, /门外的人是熟人吗/);
  assert.match($("gameBoard").textContent, /注意时间差/);
  assert.doesNotMatch($("gameHistory").textContent, /hint \| hint/);
  const hintButton = byText($("actionControls"), "给我一点提示");
  assert.equal(hintButton.disabled, false);
  click(hintButton);
  await flush();
  assert.equal(requests.filter(r => r.options.method === "POST").length, 1);
  assert.match($("gameBoard").textContent, /敲门其实是约定信号/);
  const exhausted = byText($("actionControls"), "已无更多提示");
  assert.equal(exhausted.disabled, true);
});

test("你画我猜作画方可见题词与可绘画布，支持落笔同步、撤销、清空和让陪伴者猜", async () => {
  const drawerGame = {
    game_type: "draw_guess", status: "active", outcome: null, moves: [],
    state: {
      viewer_role: "drawer", answer: "向日葵", guess_count: 0, max_guesses: 5,
      remaining_seconds: 120, revision: 0, strokes: [], guesses: [],
    },
  };
  const synced = JSON.parse(JSON.stringify(drawerGame));
  synced.state.revision = 1;
  synced.state.strokes = [{color: "#1c2420", width: 6, points: [[0.1, 0.2], [0.5, 0.5]]}];
  const {$, requests} = await render(drawerGame, {
    fetchAction: body => {
      if (body.action === "strokes") {
        synced.state.strokes = body.strokes;
        synced.state.revision += 1;
        return {game: JSON.parse(JSON.stringify(synced))};
      }
      return {game: JSON.parse(JSON.stringify(synced))};
    },
  });
  const headings = descendants($("gameBoard")).filter(el => el.tag === "h2");
  assert.equal(headings[0].textContent, "陪伴者的猜测");
  assert.equal(headings[0].className, "");
  assert.match($("gameBoard").textContent, /向日葵/);
  click(byText($("gameBoard"), "隐藏题词"));
  assert.doesNotMatch($("gameBoard").textContent, /向日葵/);
  click(byText($("gameBoard"), "显示题词"));
  assert.match($("gameBoard").textContent, /向日葵/);

  const canvas = descendants($("gameBoard")).find(el => el.tag === "canvas");
  assert.ok(canvas);
  assert.doesNotMatch(canvas.className, /readonly/);
  canvas.listeners.pointerdown(pointer(160, 220));
  canvas.listeners.pointermove(pointer(400, 400));
  canvas.listeners.pointerup(pointer(400, 400));
  await flush();
  const posts = requests.filter(r => r.options.method === "POST").map(r => JSON.parse(r.options.body));
  assert.equal(posts[0].action, "strokes");
  assert.equal(posts[0].strokes.length, 1);

  click(byText($("actionControls"), "让陪伴者猜"));
  await flush();
  const afterGuess = requests.filter(r => r.options.method === "POST").map(r => JSON.parse(r.options.body));
  assert.equal(afterGuess[1].action, "bot_guess");
  assert.match(afterGuess[1].image_data_url, /^data:image\/png;base64,/);

  click(byText($("actionControls"), "撤销一笔"));
  await flush();
  click(byText($("actionControls"), "清空画布"));
  await flush();
  const allActions = requests.filter(r => r.options.method === "POST").map(r => JSON.parse(r.options.body).action);
  assert.deepEqual(allActions, ["strokes", "bot_guess", "undo", "clear"]);
});

test("你画我猜猜测方不暴露题词，仅展示只读画布与猜词输入框", async () => {
  const guesserGame = {
    game_type: "draw_guess", status: "active", outcome: null, moves: [],
    state: {
      viewer_role: "guesser", guess_count: 0, max_guesses: 5, remaining_seconds: 90, revision: 1,
      strokes: [{color: "#d9534f", width: 6, points: [[0.2, 0.2], [0.8, 0.8]]}], guesses: [],
    },
  };
  const {$, requests} = await render(guesserGame);
  assert.match($("gameBoard").textContent, /你是猜测方/);
  assert.doesNotMatch($("gameBoard").textContent, /本题题词/);
  const canvas = descendants($("gameBoard")).find(el => el.tag === "canvas");
  assert.match(canvas.className, /readonly/);
  assert.equal(canvas.listeners.pointerdown, undefined);
  const input = descendants($("actionControls")).find(el => el.tag === "input");
  const form = descendants($("actionControls")).find(el => el.tag === "form");
  input.value = "向日葵";
  form.listeners.submit({preventDefault() {}});
  await flush();
  const post = requests.find(r => r.options.method === "POST");
  assert.deepEqual(JSON.parse(post.options.body), {action: "guess", guess: "向日葵"});
});

test("你画我猜画布同步失败时中止递归重试，不会触发无限请求风暴", async () => {
  const drawerGame = {
    game_type: "draw_guess", status: "active", outcome: null, moves: [],
    state: {
      viewer_role: "drawer", answer: "向日葵", guess_count: 0, max_guesses: 5,
      remaining_seconds: 120, revision: 0, strokes: [], guesses: [],
    },
  };
  let failSync = true;
  const {$, requests} = await render(drawerGame, {
    fetchAction: body => {
      if (body.action === "strokes" && failSync) throw new Error("network down");
      const next = JSON.parse(JSON.stringify(drawerGame));
      next.state.revision = 1;
      next.state.strokes = body.strokes || [];
      return {game: next};
    },
  });
  const canvas = descendants($("gameBoard")).find(el => el.tag === "canvas");
  canvas.listeners.pointerdown(pointer(160, 220));
  canvas.listeners.pointerup(pointer(320, 340));
  for (let i = 0; i < 10; i += 1) await flush();
  const failedPosts = requests.filter(r => r.options.method === "POST");
  assert.equal(failedPosts.length, 1);
  assert.match($("notice").textContent, /画布同步失败/);

  // 网络恢复后，继续落笔可正常同步
  failSync = false;
  const canvasAfter = descendants($("gameBoard")).find(el => el.tag === "canvas");
  canvasAfter.listeners.pointerdown(pointer(200, 240));
  canvasAfter.listeners.pointerup(pointer(360, 360));
  for (let i = 0; i < 6; i += 1) await flush();
  const allPosts = requests.filter(r => r.options.method === "POST").map(r => JSON.parse(r.options.body));
  assert.equal(allPosts.length, 2);
  assert.equal(allPosts[1].strokes.length, 2);
});

test("连续快速作画时上一笔同步返回不会卸载正在绘制的画布", async () => {
  const drawerGame = {
    game_type: "draw_guess", status: "active", outcome: null, moves: [],
    state: {
      viewer_role: "drawer", answer: "猫", guess_count: 0, max_guesses: 5,
      remaining_seconds: 120, revision: 0, strokes: [], guesses: [],
    },
  };
  let resolveFirst;
  let postCount = 0;
  const {$, requests} = await render(drawerGame, {
    fetchAction: body => {
      postCount += 1;
      const next = JSON.parse(JSON.stringify(drawerGame));
      next.state.revision = postCount;
      next.state.strokes = body.strokes;
      if (postCount === 1) return new Promise(resolve => { resolveFirst = () => resolve({game: next}); });
      return {game: next};
    },
  });
  const canvas = descendants($("gameBoard")).find(el => el.tag === "canvas");
  // 第 1 笔结束，触发同步（挂起中）
  canvas.listeners.pointerdown(pointer(150, 150));
  canvas.listeners.pointerup(pointer(250, 250));
  // 立即按下第 2 笔，尚未抬起
  canvas.listeners.pointerdown(pointer(300, 300));
  canvas.listeners.pointermove(pointer(350, 350));
  // 第 1 笔响应返回，必须保持当前 canvas DOM 不被卸载
  resolveFirst();
  await flush();
  assert.equal(descendants($("gameBoard")).find(el => el.tag === "canvas"), canvas);
  // 第 2 笔抬起，顺畅同步全部 2 笔
  canvas.listeners.pointerup(pointer(400, 400));
  for (let i = 0; i < 6; i += 1) await flush();
  const posts = requests.filter(r => r.options.method === "POST").map(r => JSON.parse(r.options.body));
  assert.equal(posts.length, 2);
  assert.equal(posts[1].strokes.length, 2);
});

test("猜词方输入框在倒计时轮询重绘后保留草稿、焦点与光标选区，提交成功后清空草稿", async () => {
  let seconds = 90;
  const makeState = () => ({
    game_id: "draw-1", game_type: "draw_guess", status: "active", outcome: null, moves: [],
    state: {
      viewer_role: "guesser", guess_count: 0, max_guesses: 5,
      remaining_seconds: seconds, revision: 1,
      strokes: [{color: "#1c2420", width: 6, points: [[0.2, 0.2], [0.8, 0.8]]}], guesses: [],
    },
  });
  const {$, poll, document} = await render(makeState(), {
    fetchState: () => ({game: makeState()}),
    fetchAction: body => {
      const next = makeState();
      next.state.guess_count = 1;
      next.state.guesses = [{number: 1, guess: body.guess, correct: false}];
      return {game: next};
    },
  });
  const input1 = descendants($("actionControls")).find(el => el.tag === "input");
  input1.focus();
  input1.value = "向日";
  input1.setSelectionRange(2, 2);
  input1.listeners.input();
  assert.equal(document.activeElement, input1);

  // 1.8s 轮询倒计时变化触发重绘
  seconds = 88;
  await poll();
  const input2 = descendants($("actionControls")).find(el => el.tag === "input");
  assert.equal(input2.value, "向日");
  assert.equal(document.activeElement, input2);
  assert.equal(input2.dataset.focusKey, "draw-guess-input");
  assert.equal(input2.selectionStart, 2);
  assert.equal(input2.selectionEnd, 2);

  // 提交猜测成功后清空草稿
  input2.value = "向日葵";
  input2.listeners.input();
  const form = descendants($("actionControls")).find(el => el.tag === "form");
  form.listeners.submit({preventDefault() {}});
  await flush();
  const input3 = descendants($("actionControls")).find(el => el.tag === "input");
  assert.equal(input3.value, "");
});
