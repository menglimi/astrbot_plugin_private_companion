(() => {
  "use strict";

  const token = decodeURIComponent(window.location.pathname.split("/")[2] || "");
  const $ = (id) => document.getElementById(id);
  const labels = {
    tictactoe: "井字棋", gomoku: "五子棋", xiangqi: "中国象棋", pig_dice: "掷骰竞分",
    blackjack: "二十一点", turtle_soup: "海龟汤", draw_guess: "你画我猜",
  };
  const stateLabel = {active: "对局进行中", finished: "本局结束", closed: "已关闭"};
  let game = null;
  let timer = 0;
  let busy = false;
  let selected = null;
  let selectedPiece = null;
  let dragState = null;
  let dragClickGuardUntil = 0;
  let requestSequence = 0;
  let loading = false;
  let renderedSignature = "";
  const DRAW_COLORS = [
    {hex: "#1c2420", label: "墨黑"},
    {hex: "#d9483b", label: "朱红"},
    {hex: "#2d7dd2", label: "钴蓝"},
    {hex: "#2a9d63", label: "松绿"},
    {hex: "#e69f28", label: "暖黄"},
    {hex: "#845ec2", label: "绛紫"},
    {hex: "#ffffff", label: "橡皮"},
  ];
  const DRAW_WIDTHS = [
    {size: 3, label: "细"},
    {size: 6, label: "中"},
    {size: 12, label: "粗"},
  ];
  const drawState = {
    color: "#1c2420",
    width: 6,
    wordHidden: false,
    strokes: [],
    revision: -1,
    activeStroke: null,
    syncing: false,
    dirty: false,
    canvas: null,
    guessDraft: "",
    gameId: "",
  };
  const gomoku = window.CompanionGomokuRoom.create({node, $, getGame: () => game,
    isBusy: () => busy, getSelectedPiece: () => selectedPiece,
    clearSelectedPiece: () => { selectedPiece = null; },
    setSelectedPiece: source => { selectedPiece = source; },
    sendAction, render, setNotice, refreshControls: () => renderControls(game.state || {})});

  function node(tag, className = "", text = "") {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== "") element.textContent = text;
    return element;
  }

  function iconButton(icon, label, action, className = "") {
    const button = node("button", `control-button ${className}`.trim());
    button.type = "button";
    button.innerHTML = `<i data-lucide="${icon}" aria-hidden="true"></i><span>${label}</span>`;
    button.disabled = busy || game?.status !== "active";
    button.addEventListener("click", () => sendAction(action));
    return button;
  }

  function paintIcons() {
    if (window.lucide?.createIcons) window.lucide.createIcons();
  }

  function setNotice(message, error = false) {
    $("notice").textContent = message || "";
    $("notice").classList.toggle("error", error);
  }

  function turnIsHuman(state) {
    if (game?.game_type === "xiangqi") return state.turn === state.human_side;
    if (["tictactoe", "gomoku"].includes(game?.game_type)) {
      return state.turn === (state.human_mark ?? state.human_color);
    }
    return state.turn === "human" || !state.turn;
  }

  function boardSize(kind) {
    return kind === "gomoku" ? 15 : 3;
  }

  function clamp(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, value));
  }

  function pointerTarget(event, stage, size) {
    const rect = stage.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.left + rect.width
        || event.clientY < rect.top || event.clientY > rect.top + rect.height) return null;
    const boardX = clamp((event.clientX - rect.left) / rect.width, 0, .999999);
    const boardY = clamp((event.clientY - rect.top) / rect.height, 0, .999999);
    const column = Math.min(size - 1, Math.floor(boardX * size));
    const row = Math.min(size - 1, Math.floor(boardY * size));
    if (game.game_type === "gomoku") return {row, column, x: .5, y: .5};
    return {
      row,
      column,
      x: Number(clamp(boardX * size - column, .02, .98).toFixed(4)),
      y: Number(clamp(boardY * size - row, .02, .98).toFixed(4)),
    };
  }

  function cellTarget(event, cell, row, column) {
    const rect = cell.getBoundingClientRect();
    return {
      row,
      column,
      x: Number(clamp((event.clientX - rect.left) / rect.width, .02, .98).toFixed(4)),
      y: Number(clamp((event.clientY - rect.top) / rect.height, .02, .98).toFixed(4)),
    };
  }

  function pieceSource(piece) {
    return {kind: "board", piece_id: piece.piece_id, color: piece.color};
  }

  function selectPiece(source) {
    if (busy || game?.status !== "active" || Date.now() < dragClickGuardUntil) return;
    selectedPiece = source;
    setNotice(source.kind === "tray" ? "已取子，点击棋盘落点" : "已拿起棋子，点击新的落点");
    render();
  }

  function pieceVisual(element, color, kind, human) {
    element.classList.add(color === human ? "piece-player" : "piece-bot");
    if (kind === "gomoku") {
      element.appendChild(node("span", `stone ${color === 1 ? "stone-black" : "stone-white"}`));
    } else {
      element.textContent = color === human ? "×" : "○";
    }
  }

  function beginPiecePointer(event, source, element) {
    if (busy || game?.status !== "active") return;
    const stage = $("gameBoard").querySelector(".gomoku-intersections")
      || $("gameBoard").querySelector(".board-stage");
    if (!stage) return;
    event.preventDefault();
    const ghost = node("div", "drag-ghost");
    pieceVisual(ghost, source.color, game.game_type, game.state.human_mark ?? game.state.human_color);
    stage.appendChild(ghost);
    const stageRect = stage.getBoundingClientRect();
    ghost.style.left = `${clamp(event.clientX - stageRect.left, 0, stageRect.width)}px`;
    ghost.style.top = `${clamp(event.clientY - stageRect.top, 0, stageRect.height)}px`;
    const startX = event.clientX;
    const startY = event.clientY;
    dragState = {source, stage, ghost, pointerId: event.pointerId,
      startX, startY, moved: false};
    const move = (moveEvent) => {
      if (!dragState || moveEvent.pointerId !== dragState.pointerId) return;
      const distance = Math.hypot(moveEvent.clientX - startX, moveEvent.clientY - startY);
      if (distance > 5) dragState.moved = true;
      const rect = stage.getBoundingClientRect();
      ghost.style.left = `${clamp(moveEvent.clientX - rect.left, 0, rect.width)}px`;
      ghost.style.top = `${clamp(moveEvent.clientY - rect.top, 0, rect.height)}px`;
    };
    const finish = (finishEvent) => {
      if (!dragState || finishEvent.pointerId !== dragState.pointerId) return;
      const current = dragState;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", cancel);
      current.ghost.remove();
      dragState = null;
      if (!current.moved) {
        selectPiece(source);
        return;
      }
      dragClickGuardUntil = Date.now() + 260;
      const target = pointerTarget(finishEvent, current.stage, boardSize(game.game_type));
      if (!target) { setNotice("已取消拖动，棋子未移动"); return; }
      sendAction({action: "interact", source,
        target,
        interaction: "drag"});
    };
    const cancel = (cancelEvent) => {
      if (!dragState || cancelEvent.pointerId !== dragState.pointerId) return;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", cancel);
      dragState.ghost.remove();
      dragState = null;
    };
    window.addEventListener("pointermove", move, {passive: false});
    window.addEventListener("pointerup", finish, {passive: false});
    window.addEventListener("pointercancel", cancel, {passive: false});
  }

  function boardPiece(piece, state, kind, size) {
    const human = state.human_mark ?? state.human_color;
    const element = node("button", "floating-piece");
    element.type = "button";
    element.dataset.pieceId = piece.piece_id;
    element.setAttribute("aria-label", `${piece.color === human ? "你的" : "Bot 的"}棋子，可拖动`);
    element.style.left = `${((piece.column + piece.x) / size) * 100}%`;
    element.style.top = `${((piece.row + piece.y) / size) * 100}%`;
    pieceVisual(element, piece.color, kind, human);
    if (selectedPiece?.kind === "board" && selectedPiece.piece_id === piece.piece_id) {
      element.classList.add("selected");
    }
    element.addEventListener("pointerdown", (event) => beginPiecePointer(event, pieceSource(piece), element));
    element.addEventListener("click", (event) => {
      event.stopPropagation();
      if (Date.now() < dragClickGuardUntil) return;
      selectPiece(pieceSource(piece));
    });
    return element;
  }

  function renderGridBoard(board, kind, state) {
    const size = boardSize(kind);
    const stage = node("div", `board-stage ${kind === "gomoku" ? "gomoku-stage" : "tictactoe-stage"}`);
    stage.style.setProperty("--board-size", size);
    const grid = node("div", `grid-board board-grid ${kind === "gomoku" ? "gomoku-board" : "tictactoe-board"}`);
    const human = state.human_mark ?? state.human_color;
    const bot = state.bot_mark ?? state.bot_color;
    grid.style.gridTemplateColumns = `repeat(${board.length || 3}, minmax(0, 1fr))`;
    board.forEach((row, rowIndex) => row.forEach((cell, columnIndex) => {
      const button = node("button", "board-cell");
      button.type = "button";
      button.setAttribute("aria-label", `第 ${rowIndex + 1} 行，第 ${columnIndex + 1} 列`);
      const empty = cell === 0 || cell === null || cell === "" || cell === ".";
      if (!empty && kind === "gomoku") {
        button.classList.add("logical-occupied");
      } else if (!empty) {
        button.classList.add("logical-occupied");
      }
      const canMove = !busy && game.status === "active" && empty
        && (selectedPiece || turnIsHuman(state));
      button.disabled = !canMove;
      button.addEventListener("click", (event) => {
        if (!empty) return;
        const target = cellTarget(event, button, rowIndex, columnIndex);
        if (selectedPiece) {
          const source = selectedPiece;
          selectedPiece = null;
          sendAction({action: "interact", source, target, interaction: "click"});
        } else if (turnIsHuman(state)) {
          sendAction({action: "move", ...target});
        }
      });
      grid.appendChild(button);
    }));
    stage.appendChild(grid);
    const layer = node("div", "piece-layer");
    (Array.isArray(state.pieces) ? state.pieces : []).forEach((piece) => {
      layer.appendChild(boardPiece(piece, state, kind, size));
    });
    stage.appendChild(layer);
    $("gameBoard").appendChild(stage);
  }

  function renderPieceTray(state) {
    if (game.game_type === "gomoku") { gomoku.renderTray(state); return; }
    const tray = $("pieceTray");
    tray.replaceChildren();
    const isBoardGame = ["tictactoe", "gomoku"].includes(game.game_type);
    tray.hidden = !isBoardGame || game.status !== "active";
    if (!isBoardGame || game.status !== "active") return;
    tray.append(node("div", "piece-tray-heading", "棋盒"));
    const human = state.human_mark ?? state.human_color;
    [[human, "你的棋"], [state.bot_mark ?? state.bot_color, "Bot 的棋"]].forEach(([color, label]) => {
      const piece = node("button", "tray-piece");
      piece.type = "button";
      piece.setAttribute("aria-label", `${label}，点击或拖动到棋盘`);
      piece.title = `${label} · 点击后选择落点`;
      pieceVisual(piece, color, game.game_type, human);
      if (selectedPiece?.kind === "tray" && selectedPiece.color === color) piece.classList.add("selected");
      const source = {kind: "tray", color};
      piece.addEventListener("pointerdown", (event) => beginPiecePointer(event, source, piece));
      piece.addEventListener("click", (event) => {
        event.stopPropagation();
        if (Date.now() < dragClickGuardUntil) return;
        selectPiece(source);
      });
      tray.appendChild(piece);
    });
    tray.append(node("span", "piece-tray-hint", selectedPiece ? "选择棋盘落点" : "双方棋子均可拿起"));
  }

  const pieces = {
    r: "車", n: "馬", b: "象", a: "士", k: "將", c: "砲", p: "卒",
    R: "車", N: "馬", B: "相", A: "仕", K: "帥", C: "炮", P: "兵",
  };

  function renderXiangqi(state) {
    const board = node("div", "xiangqi-board");
    const legal = Array.isArray(state.legal_moves) ? state.legal_moves : [];
    const active = game.status === "active" && turnIsHuman(state) && !busy;
    const flipped = state.human_side === "black";
    const rowIndices = flipped ? [9, 8, 7, 6, 5, 4, 3, 2, 1, 0] : [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];
    const colIndices = flipped ? [8, 7, 6, 5, 4, 3, 2, 1, 0] : [0, 1, 2, 3, 4, 5, 6, 7, 8];
    const legalTargets = selected
      ? new Set(legal.filter((item) => item[0] === selected[0] && item[1] === selected[1])
          .map((item) => `${item[2]},${item[3]}`))
      : null;
    rowIndices.forEach((rowIndex) => colIndices.forEach((columnIndex) => {
      const piece = state.board?.[rowIndex]?.[columnIndex] || ".";
      const button = node("button", "xiangqi-cell");
      button.type = "button";
      button.textContent = pieces[piece] || "";
      if (piece && piece !== ".") button.classList.add(piece === piece.toUpperCase() ? "piece-red" : "piece-black");
      button.setAttribute("aria-label", `${rowIndex + 1} 行 ${columnIndex + 1} 列${pieces[piece] ? ` ${pieces[piece]}` : " 空位"}`);
      const isSelected = selected?.[0] === rowIndex && selected?.[1] === columnIndex;
      if (isSelected) button.classList.add("selected");
      if (legalTargets?.has(`${rowIndex},${columnIndex}`)) button.classList.add("legal-target");
      button.disabled = !active;
      button.addEventListener("click", () => {
        if (!selected) {
          if (legal.some((move) => move[0] === rowIndex && move[1] === columnIndex)) {
            selected = [rowIndex, columnIndex];
            render();
          }
          return;
        }
        const move = legal.find((item) => item[0] === selected[0] && item[1] === selected[1]
          && item[2] === rowIndex && item[3] === columnIndex);
        if (move) {
          selected = null;
          sendAction({action: "move", from_row: move[0], from_column: move[1], to_row: move[2], to_column: move[3]});
        } else if (legal.some((item) => item[0] === rowIndex && item[1] === columnIndex)) {
          selected = [rowIndex, columnIndex];
          render();
        } else {
          selected = null;
          render();
        }
      });
      board.appendChild(button);
    }));
    $("gameBoard").appendChild(board);
  }

  function scoreItem(label, value, type = "") {
    const item = node("div", `score-item ${type}`.trim());
    item.append(node("small", "", label), node("strong", "", String(value)));
    return item;
  }

  function renderSummary(state) {
    const summary = $("gameSummary");
    summary.replaceChildren();
    if (game.game_type === "tictactoe" || game.game_type === "gomoku") {
      const pair = node("div", "score-pair");
      const isGomoku = game.game_type === "gomoku";
      pair.append(scoreItem("你", isGomoku ? (state.human_color === 1 ? "黑棋" : "白棋") : "×", "player"));
      pair.append(scoreItem("陪伴者", isGomoku ? (state.bot_color === 1 ? "黑棋" : "白棋") : "○", "bot"));
      const phase = game.status !== "active" ? stateLabel[game.status]
        : (isGomoku ? gomoku.phase() : (turnIsHuman(state) ? "轮到你了" : "对方思考中"));
      summary.append(pair, node("p", "summary-line", `已落子 ${state.move_count || 0} 手 · ${phase}`));
      if (isGomoku && state.last_move) summary.append(node("p", "summary-line", `最后一手：第 ${state.last_move[0] + 1} 行，第 ${state.last_move[1] + 1} 列`));
    } else if (game.game_type === "pig_dice") {
      const pair = node("div", "score-pair");
      pair.append(scoreItem("你", state.human_score, "player"), scoreItem("陪伴者", state.bot_score, "bot"));
      summary.append(pair, node("p", "summary-line", `当前回合 ${state.turn_total} 分 · ${state.target_score} 分获胜`));
    } else if (game.game_type === "blackjack") {
      const hand = state.hands?.["1"];
      const pair = node("div", "score-pair");
      pair.append(scoreItem("你的点数", hand?.value ?? 0, "player"), scoreItem("庄家点数", state.dealer_total ?? 0, "bot"));
      summary.append(pair, node("p", "summary-line", state.dealer_hidden ? "庄家底牌暂时盖住" : (state.phase === "dealer_turn" ? "庄家正在补牌" : "无下注单人对局")));
    } else if (game.game_type === "xiangqi") {
      const pair = node("div", "score-pair");
      pair.append(scoreItem("你执", state.human_side === "red" ? "红" : "黑", "player"), scoreItem("已走", state.move_count || 0, "bot"));
      summary.append(pair, node("p", "summary-line", game.status === "active"
        ? (turnIsHuman(state) ? "轮到你落子" : "对方正在应手") : stateLabel[game.status]));
    } else if (game.game_type === "turtle_soup") {
      const pair = node("div", "score-pair");
      pair.append(scoreItem("已提问", state.question_count || 0, "player"), scoreItem("已发现", `${state.discovered_fact_count || 0}/${state.key_fact_count || 0}`, "bot"));
      summary.append(pair, node("p", "summary-line", `提示 ${state.hints_used || 0} / ${state.hint_limit || 0}`));
    } else if (game.game_type === "draw_guess") {
      const pair = node("div", "score-pair");
      pair.append(scoreItem("已猜", state.guess_count || 0, "player"), scoreItem("还可猜", Math.max(0, (state.max_guesses || 0) - (state.guess_count || 0)), "bot"));
      const roleText = state.viewer_role === "guesser" ? "猜测方" : "作画方";
      summary.append(pair, node("p", "summary-line", `${roleText} · 剩余 ${state.remaining_seconds ?? 0} 秒`));
    }
    if (game.status === "finished") {
      const isPuzzle = ["turtle_soup", "draw_guess"].includes(game.game_type);
      const result = game.game_type === "draw_guess"
        ? (game.outcome === "player" ? "合作猜中了" : (state.timed_out ? "作画超时，本轮未猜中" : "本轮未猜中"))
        : isPuzzle ? (game.outcome === "player" ? "你解答成功了" : "本局未解答成功")
          : ({player: "你赢了", bot: "陪伴者赢了", draw: "本局平局"}[game.outcome] || "本局结束");
      summary.append(node("p", "summary-line game-outcome", result));
    }
  }

  function cardMarkup(card) {
    const suits = {spades: "♠", clubs: "♣", hearts: "♥", diamonds: "♦"};
    const suit = suits[card.suit] || card.suit || "";
    const isRed = ["hearts", "diamonds", "♥", "♦"].includes(card.suit) || ["♥", "♦"].includes(suit);
    const element = node("div", `playing-card ${isRed ? "red" : ""}`.trim());
    element.append(node("strong", "", String(card.rank)), node("small", "", suit));
    return element;
  }

  function renderBlackjack(state) {
    const table = node("div", "blackjack-table");
    const dealer = node("div", "hand-zone");
    dealer.append(node("span", "hand-label", `庄家${state.dealer_hidden ? " · 暗牌" : ` · ${state.dealer_total} 点`}`));
    const dealerCards = node("div", "cards");
    (state.dealer_cards || []).forEach((card) => dealerCards.append(cardMarkup(card)));
    if (state.dealer_hidden) dealerCards.append(node("div", "playing-card", "?"));
    dealer.append(dealerCards);
    const player = node("div", "hand-zone");
    const hand = state.hands?.["1"];
    player.append(node("span", "hand-label", `你 · ${hand?.value ?? 0} 点`));
    const playerCards = node("div", "cards");
    (hand?.cards || []).forEach((card) => playerCards.append(cardMarkup(card)));
    player.append(playerCards);
    table.append(dealer, player);
    $("gameBoard").appendChild(table);
  }

  function paintDrawCanvas(canvas) {
    if (!canvas || typeof canvas.getContext !== "function") return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const width = canvas.width || 640;
    const height = canvas.height || 420;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#fffdf8";
    ctx.fillRect(0, 0, width, height);
    drawState.strokes.forEach((stroke) => {
      const points = Array.isArray(stroke?.points) ? stroke.points : [];
      if (!points.length) return;
      ctx.beginPath();
      ctx.strokeStyle = stroke.color || "#1c2420";
      ctx.lineWidth = Number(stroke.width || 6);
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      points.forEach(([x, y], index) => {
        const px = Number(x) * width;
        const py = Number(y) * height;
        if (index === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      if (points.length === 1) {
        ctx.lineTo(Number(points[0][0]) * width + 0.1, Number(points[0][1]) * height + 0.1);
      }
      ctx.stroke();
    });
  }

  function syncLocalDrawStrokes(state) {
    const serverRevision = Number(state.revision ?? 0);
    if (!drawState.activeStroke && !drawState.syncing && !drawState.dirty && serverRevision >= drawState.revision) {
      drawState.strokes = Array.isArray(state.strokes) ? state.strokes.map((stroke) => ({
        color: stroke.color || "#1c2420",
        width: Number(stroke.width || 6),
        points: Array.isArray(stroke.points) ? stroke.points.map((point) => [Number(point[0]), Number(point[1])]) : [],
      })) : [];
      drawState.revision = serverRevision;
    }
  }

  function drawCanvasPoint(event, canvas) {
    const rect = canvas.getBoundingClientRect();
    const width = rect.width || 1;
    const height = rect.height || 1;
    return [
      Number(clamp((event.clientX - rect.left) / width, 0, 1).toFixed(5)),
      Number(clamp((event.clientY - rect.top) / height, 0, 1).toFixed(5)),
    ];
  }

  async function flushDrawStrokes() {
    if (!drawState.dirty || drawState.syncing || game?.status !== "active") return true;
    drawState.syncing = true;
    drawState.dirty = false;
    let succeeded = false;
    const snapshot = drawState.strokes.map((stroke) => ({
      color: stroke.color,
      width: stroke.width,
      points: stroke.points.map((point) => [...point]),
    }));
    try {
      const result = await fetchJSON(`/api/native/${encodeURIComponent(token)}/action`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({action: "strokes", strokes: snapshot}),
      });
      game = result.game;
      drawState.revision = Number(game.state?.revision ?? drawState.revision);
      setNotice("");
      succeeded = true;
      return true;
    } catch (error) {
      drawState.dirty = true;
      setNotice(errorMessage(error, "画布同步失败，请稍后重试"), true);
      return false;
    } finally {
      drawState.syncing = false;
      if (drawState.activeStroke) {
        // 正在连续绘制新一笔时禁止重建画布，等待 pointerup 结束后统一同步与重绘。
      } else if (succeeded && drawState.dirty && game?.status === "active") {
        void flushDrawStrokes();
      } else {
        render({force: true});
      }
    }
  }

  async function submitBotDrawGuess() {
    if (busy || game?.status !== "active") return;
    if (!drawState.strokes.length) {
      setNotice("请先在画布上画几笔，再让陪伴者猜", true);
      return;
    }
    if (drawState.activeStroke) {
      drawState.activeStroke = null;
      drawState.dirty = true;
    }
    if (drawState.dirty && !(await flushDrawStrokes())) return;
    let imageDataUrl = "";
    if (drawState.canvas && typeof drawState.canvas.toDataURL === "function") {
      imageDataUrl = drawState.canvas.toDataURL("image/png");
    }
    if (!imageDataUrl) {
      setNotice("当前环境不支持导出画布图像", true);
      return;
    }
    await sendAction({action: "bot_guess", image_data_url: imageDataUrl});
  }

  function renderTurtleSoup(state) {
    const layout = node("div", "soup-layout");
    const surface = node("article", "soup-surface");
    const titleText = state.puzzle?.title ? `题面 · ${state.puzzle.title}` : "题面";
    surface.append(node("h2", "", titleText), node("p", "", state.puzzle?.surface || "题面暂不可用"));
    if (state.puzzle?.solution) {
      const solutionBox = node("div", "soup-solution");
      solutionBox.append(node("h3", "", "汤底揭晓"), node("p", "", state.puzzle.solution));
      surface.appendChild(solutionBox);
    }
    layout.appendChild(surface);

    const entriesPanel = node("section", "soup-entries");
    entriesPanel.appendChild(node("h2", "", "提示与推理记录"));
    const entries = Array.isArray(state.entries) ? state.entries : [];
    if (!entries.length) {
      entriesPanel.appendChild(node("p", "summary-line", "还没有提示或提问记录；可点击下方获取提示，或在聊天中向陪伴者提问。"));
    } else {
      const list = node("ol", "soup-entry-list");
      const kindLabels = {hint: "提示", question: "提问", answer: "完整推理", reverse: "反猜回合"};
      entries.forEach((entry, index) => {
        const item = node("li", `soup-entry ${entry.kind === "hint" ? "is-hint" : ""}`.trim());
        const head = node("div", "soup-entry-head");
        head.append(node("span", "soup-badge", kindLabels[entry.kind] || "记录"));
        head.append(node("span", entry.kind === "question" ? "soup-verdict" : "",
          entry.kind === "question" ? (entry.response || "") : `#${index + 1}`));
        item.appendChild(head);
        if (entry.kind === "hint") {
          item.appendChild(node("p", "soup-entry-response", entry.response || "提示暂不可用"));
        } else {
          if (entry.prompt) item.appendChild(node("p", "soup-entry-prompt", entry.prompt));
          if (entry.response && entry.kind !== "question") {
            item.appendChild(node("p", "soup-entry-response", entry.response));
          }
        }
        list.appendChild(item);
      });
      entriesPanel.appendChild(list);
    }
    layout.appendChild(entriesPanel);
    $("gameBoard").appendChild(layout);
  }

  function renderDrawGuess(state) {
    syncLocalDrawStrokes(state);
    const isGuesser = state.viewer_role === "guesser";
    const editable = !isGuesser && game.status === "active" && !busy;
    const layout = node("div", "draw-layout");

    const promptCard = node("section", "draw-prompt-card");
    const promptMain = node("div", "draw-prompt-main");
    if (game.status !== "active" && state.answer) {
      promptMain.append(
        node("span", "eyebrow", "本轮答案"),
        node("div", "draw-prompt-word", state.answer),
        node("p", "draw-prompt-note", state.solved ? "合作猜中，太棒了！" : (state.timed_out ? "作画倒计时结束。" : "本轮已结束。")),
      );
      promptCard.appendChild(promptMain);
    } else if (!isGuesser) {
      const wordText = state.answer ? (drawState.wordHidden ? "已隐藏（点击右侧显示）" : state.answer) : "题词加载中";
      promptMain.append(
        node("span", "eyebrow", "本题题词（仅作画方可见）"),
        node("div", "draw-prompt-word", wordText),
        node("p", "draw-prompt-note", "陪伴者看不到题词，请作画后点击「让陪伴者猜」或在聊天中发送画作。"),
      );
      promptCard.appendChild(promptMain);
      const toggle = node("button", "control-button", drawState.wordHidden ? "显示题词" : "隐藏题词");
      toggle.type = "button";
      toggle.dataset.drawToggleWord = "1";
      toggle.addEventListener("click", () => {
        drawState.wordHidden = !drawState.wordHidden;
        render({force: true});
      });
      promptCard.appendChild(toggle);
    } else {
      promptMain.append(
        node("span", "eyebrow", "你是猜测方"),
        node("p", "draw-prompt-note", "观察画布上的作品，在下方输入你的猜测；本轮结束前不会显示答案。"),
      );
      promptCard.appendChild(promptMain);
    }
    layout.appendChild(promptCard);

    if (!isGuesser && game.status === "active") {
      const toolbar = node("div", "draw-toolbar");
      const swatches = node("div", "draw-swatches");
      DRAW_COLORS.forEach(({hex, label}) => {
        const swatch = node("button", "draw-swatch");
        swatch.type = "button";
        swatch.style.backgroundColor = hex;
        swatch.setAttribute("aria-label", label);
        swatch.setAttribute("aria-pressed", String(drawState.color === hex));
        swatch.dataset.drawColor = hex;
        swatch.disabled = !editable;
        swatch.addEventListener("click", () => {
          drawState.color = hex;
          render({force: true});
        });
        swatches.appendChild(swatch);
      });
      const widths = node("div", "draw-widths");
      DRAW_WIDTHS.forEach(({size, label}) => {
        const btn = node("button", "draw-width-btn", label);
        btn.type = "button";
        btn.setAttribute("aria-pressed", String(drawState.width === size));
        btn.dataset.drawWidth = String(size);
        btn.disabled = !editable;
        btn.addEventListener("click", () => {
          drawState.width = size;
          render({force: true});
        });
        widths.appendChild(btn);
      });
      toolbar.append(swatches, widths);
      layout.appendChild(toolbar);
    }

    const canvasShell = node("div", "draw-canvas-shell");
    const canvas = node("canvas", `draw-canvas ${editable ? "" : "readonly"}`.trim());
    canvas.width = 640;
    canvas.height = 420;
    canvas.setAttribute("aria-label", editable ? "你画我猜画布，可拖动绘制" : "你画我猜画布（只读）");
    drawState.canvas = canvas;
    if (editable) {
      canvas.addEventListener("pointerdown", (event) => {
        if (busy || game?.status !== "active") return;
        event.preventDefault?.();
        canvas.setPointerCapture?.(event.pointerId);
        drawState.activeStroke = {
          color: drawState.color,
          width: drawState.width,
          points: [drawCanvasPoint(event, canvas)],
        };
        drawState.strokes.push(drawState.activeStroke);
        drawState.dirty = true;
        paintDrawCanvas(canvas);
      });
      canvas.addEventListener("pointermove", (event) => {
        if (!drawState.activeStroke) return;
        event.preventDefault?.();
        drawState.activeStroke.points.push(drawCanvasPoint(event, canvas));
        paintDrawCanvas(canvas);
      });
      const finishStroke = (event) => {
        if (!drawState.activeStroke) return;
        event.preventDefault?.();
        canvas.releasePointerCapture?.(event.pointerId);
        drawState.activeStroke = null;
        drawState.dirty = true;
        paintDrawCanvas(canvas);
        void flushDrawStrokes();
      };
      canvas.addEventListener("pointerup", finishStroke);
      canvas.addEventListener("pointercancel", finishStroke);
    }
    paintDrawCanvas(canvas);
    canvasShell.appendChild(canvas);
    layout.appendChild(canvasShell);

    const guesses = Array.isArray(state.guesses) ? state.guesses : [];
    const guessBox = node("section", "draw-guesses");
    guessBox.appendChild(node("h2", "", isGuesser ? "猜测记录" : "陪伴者的猜测"));
    if (!guesses.length) {
      guessBox.appendChild(node("p", "summary-line", isGuesser ? "还没有猜测" : "陪伴者还没有猜，画好后可点击下方「让陪伴者猜」"));
    } else {
      const list = node("ul", "draw-guess-list");
      guesses.forEach((item, index) => {
        const chip = node("li", `draw-guess-chip ${item.correct ? "correct" : ""}`.trim(),
          `第 ${item.number || index + 1} 次：${item.guess}${item.correct ? " · 猜中" : " · 未命中"}`);
        list.appendChild(chip);
      });
      guessBox.appendChild(list);
    }
    layout.appendChild(guessBox);
    $("gameBoard").appendChild(layout);
  }

  function renderBoard(state) {
    const host = $("gameBoard");
    host.replaceChildren();
    host.setAttribute("aria-busy", "false");
    if (game.game_type === "gomoku") {
      gomoku.renderBoard(state);
    } else if (game.game_type === "tictactoe") {
      renderGridBoard(state.board || [], game.game_type, state);
    } else if (game.game_type === "xiangqi") {
      renderXiangqi(state);
    } else if (game.game_type === "pig_dice") {
      const dice = node("div", "dice-layout");
      const face = state.last_roll?.final ?? state.last_roll ?? "·";
      dice.append(node("div", "die", String(face)), node("p", "summary-line", `本回合 ${state.turn_total || 0} 分 · ${state.turn === "human" ? "轮到你" : "对方掷骰中"}`));
      host.appendChild(dice);
    } else if (game.game_type === "blackjack") {
      renderBlackjack(state);
    } else if (game.game_type === "turtle_soup") {
      renderTurtleSoup(state);
    } else if (game.game_type === "draw_guess") {
      renderDrawGuess(state);
    }
    renderSummary(state);
  }

  function renderControls(state) {
    const controls = $("actionControls");
    controls.replaceChildren();
    if (game.status === "active") {
      if (game.game_type === "gomoku") {
        gomoku.renderControls(controls, state);
      } else if (game.game_type === "pig_dice") {
        controls.append(iconButton("dices", "桌面掷骰", {action: "roll"}, "primary"));
        controls.append(iconButton("glass-water", "推骰盅", {action: "roll", method: "cup"}));
        controls.append(iconButton("save", "存下分数", {action: "hold"}));
      } else if (game.game_type === "blackjack") {
        controls.append(iconButton("plus", "再要一张", {action: "hit"}, "primary"));
        controls.append(iconButton("hand", "停牌", {action: "stand"}));
      } else if (game.game_type === "turtle_soup") {
        const exhausted = (state.hint_limit || 0) > 0 && (state.hints_used || 0) >= (state.hint_limit || 0);
        const hintBtn = iconButton("lightbulb", exhausted ? "已无更多提示" : "给我一点提示", {action: "hint"});
        if (exhausted) hintBtn.disabled = true;
        controls.append(hintBtn);
        controls.append(iconButton("flag", "揭晓汤底", {action: "give_up"}, "warm"));
      } else if (game.game_type === "draw_guess") {
        if (state.viewer_role === "guesser") {
          const form = node("form", "guess-form");
          const input = node("input");
          input.name = "guess";
          input.maxLength = 80;
          input.autocomplete = "off";
          input.placeholder = "输入你的猜测";
          input.setAttribute("aria-label", "输入你的猜测");
          input.dataset.focusKey = "draw-guess-input";
          input.value = drawState.guessDraft;
          input.addEventListener("input", () => {
            drawState.guessDraft = input.value;
          });
          const button = node("button", "control-button primary");
          button.type = "submit";
          button.innerHTML = '<i data-lucide="send" aria-hidden="true"></i><span>提交猜测</span>';
          button.disabled = busy;
          form.append(input, button);
          form.addEventListener("submit", (event) => {
            event.preventDefault();
            drawState.guessDraft = input.value;
            const guess = input.value.trim();
            if (guess) sendAction({action: "guess", guess});
          });
          controls.appendChild(form);
        } else {
          const hasStrokes = drawState.strokes.length > 0;
          const undoBtn = iconButton("undo-2", "撤销一笔", {action: "undo"});
          undoBtn.disabled = busy || drawState.syncing || !hasStrokes;
          const clearBtn = iconButton("trash-2", "清空画布", {action: "clear"});
          clearBtn.disabled = busy || drawState.syncing || !hasStrokes;
          const guessBtn = node("button", "control-button primary");
          guessBtn.type = "button";
          guessBtn.innerHTML = '<i data-lucide="sparkles" aria-hidden="true"></i><span>让陪伴者猜</span>';
          guessBtn.disabled = busy || drawState.syncing || !hasStrokes;
          guessBtn.addEventListener("click", () => { void submitBotDrawGuess(); });
          controls.append(undoBtn, clearBtn, guessBtn);
        }
        controls.append(iconButton("flag", "放弃本局", {action: "give_up"}, "warm"));
      } else if (game.game_type === "xiangqi") {
        const sideLabel = state.human_side === "black" ? "黑方" : "红方";
        const hint = !turnIsHuman(state) ? "对方思考中，请稍候"
          : (selected ? "选择要移动到的位置" : `点击${sideLabel}棋子开始选择`);
        controls.append(node("p", "summary-line", hint));
      }
    }
    if (game.status === "active") controls.append(iconButton("x", "结束对局", {action: "close"}, "warm"));
    if (game.game_type === "turtle_soup" && game.status === "active") {
      controls.append(node("span", "summary-line", "提问与推理继续在聊天中进行"));
    }
    paintIcons();
  }

  function renderHistory() {
    const list = $("gameHistory");
    list.replaceChildren();
    if (game.game_type === "turtle_soup" && Array.isArray(game.state?.entries)) {
      const entries = game.state.entries;
      $("actionCount").textContent = String(entries.length);
      const labelsByKind = {hint: "提示", question: "提问", answer: "推理", reverse: "反猜"};
      entries.slice(-8).reverse().forEach((entry) => {
        const actor = labelsByKind[entry.kind] || "进展";
        const detail = entry.kind === "hint"
          ? (entry.response || "已揭示提示")
          : entry.kind === "question"
            ? `${entry.prompt || ""} → ${entry.response || ""}`
            : (entry.response || entry.prompt || "进展");
        const item = node("li");
        item.append(node("span", "", actor), node("span", "", detail));
        list.appendChild(item);
      });
      if (!entries.length) list.append(node("li", "", "等待第一步"));
      return;
    }
    const events = game.events || game.moves || [];
    $("actionCount").textContent = String(events.length);
    events.slice(-8).reverse().forEach((event, index) => {
      let action = "落子";
      let actor = `第 ${events.length - index} 手`;
      if (Array.isArray(event)) {
        if (["tictactoe", "gomoku"].includes(game.game_type)) {
          actor = event[2] === (game.state.human_mark ?? game.state.human_color) ? "你" : "陪伴者";
          action = `第 ${event[0] + 1} 行，第 ${event[1] + 1} 列`;
        } else {
          actor = event[0] === "human" ? "你" : "陪伴者";
          action = event[1] === "cup_roll" ? `骰盅 ${event[2]?.[0]} → ${event[2]?.[1]}`
            : event[1] === "roll" ? `掷出 ${event[2]}` : event[1] === "hold" ? "存分" : String(event[1]);
        }
      } else if (event && typeof event === "object") {
        actor = event.guess ? (game.state?.viewer_role === "guesser" ? "你" : "陪伴者") : (event.action || "进展");
        action = event.guess ? `${event.guess}${event.correct ? " · 命中" : " · 未命中"}`
          : (event.question || event.answer || event.action || "进展");
      } else if (typeof event === "string") {
        action = event;
      }
      const item = node("li");
      if (game.game_type === "gomoku") {
        item.appendChild(node("small", "history-step", String(events.length - index)));
        item.classList.add("gomoku-history-item");
      }
      item.append(node("span", "", actor), node("span", "", action));
      list.appendChild(item);
    });
    if (!events.length) list.append(node("li", "", "等待第一步"));
  }

  function render({force = true} = {}) {
    if (!game) return;
    if (game.game_type === "draw_guess") {
      const currentId = String(game.game_id || "");
      if ((drawState.gameId && currentId && drawState.gameId !== currentId) || game.status !== "active") {
        drawState.guessDraft = "";
      }
      drawState.gameId = currentId;
    }
    const signature = JSON.stringify([game, busy, selected, game.game_type === "gomoku" ? null : selectedPiece]);
    if (!force && signature === renderedSignature) return;
    renderedSignature = signature;
    const activeEl = document.activeElement;
    const focusKey = activeEl?.dataset?.focusKey;
    const selectionStart = typeof activeEl?.selectionStart === "number" ? activeEl.selectionStart : null;
    const selectionEnd = typeof activeEl?.selectionEnd === "number" ? activeEl.selectionEnd : null;
    const label = labels[game.game_type] || "V2 原生对局";
    $("gameTitle").textContent = label;
    $("gameCode").textContent = game.game_type === "gomoku" ? "15 × 15 · 黑棋先行 · 无禁手" : "一起玩 · 游戏房间";
    $("roundState").textContent = stateLabel[game.status] || game.status;
    if (game.game_type === "gomoku") {
      gomoku.renderToolbar(game.state || {});
      $("roundState").textContent = game.status === "active" ? gomoku.phase() : (stateLabel[game.status] || game.status);
    } else { $("boardToolbar").hidden = true; }
    renderBoard(game.state || {});
    renderPieceTray(game.state || {});
    renderControls(game.state || {});
    renderHistory();
    if (focusKey) {
      const restored = document.querySelector?.(`[data-focus-key="${focusKey}"]`);
      if (restored) {
        restored.focus?.({preventScroll: true});
        if (selectionStart !== null && typeof restored.setSelectionRange === "function") {
          const length = String(restored.value || "").length;
          try {
            restored.setSelectionRange(Math.min(selectionStart, length), Math.min(selectionEnd ?? selectionStart, length));
          } catch (_) {}
        }
      }
    }
  }

  async function fetchJSON(url, options = {}) {
    const response = await fetch(url, {cache: "no-store", ...options});
    const result = await response.json();
    if (!response.ok || result?.status === "error") throw new Error(result?.message || "请求失败");
    return result.data ?? result;
  }

  const errors = {
    game_illegal_move: "这一步未落下，棋盘已同步，请重新选择空位",
    game_target_occupied: "这个交叉点已有棋子，请选择空位",
    game_web_link_expired: "对局链接已失效，请在聊天中重新打开房间",
    game_not_found: "这局已不可用，请在聊天中打开当前房间",
    game_not_active: "本局已结束，棋盘已同步",
    game_not_found_in_scope: "这局已不可用，请在聊天中打开当前房间",
    game_drawer_only: "只有作画方可以操作画布",
    game_guesser_only: "只有猜测方可以提交猜词",
    game_drawing_empty: "请先在画布上画几笔，再让陪伴者猜",
    game_vision_unavailable: "当前未配置视觉模型，可将画作发到聊天让陪伴者猜",
    invalid_game_arguments: "操作参数未被接受，请同步棋盘后重试",
  };
  const errorMessage = (error, fallback) => errors[error.message]
    || (/[\u3400-\u9fff]/.test(error.message || "") ? error.message : fallback);

  async function load({quiet = true, recovery = false} = {}) {
    if (loading || dragState || drawState.activeStroke || drawState.syncing || drawState.dirty
        || gomoku.isInteracting() || (busy && !recovery)) return;
    loading = true;
    const sequence = ++requestSequence;
    try {
      const result = await fetchJSON(`/api/native/${encodeURIComponent(token)}/state`);
      if (sequence !== requestSequence || dragState || drawState.activeStroke
          || drawState.syncing || drawState.dirty || gomoku.isInteracting()) return;
      game = result.game;
      $("connectionBadge").dataset.state = "online";
      $("connectionBadge").querySelector("span").textContent = "实时同步";
      render({force: false});
      if (!quiet) setNotice("");
    } catch (error) {
      if (sequence !== requestSequence) return;
      if (!quiet || !game) setNotice(errorMessage(error, "对局暂时无法读取"), true);
      $("connectionBadge").dataset.state = "error";
      $("connectionBadge").querySelector("span").textContent = "连接中断";
    } finally {
      loading = false;
    }
  }

  async function sendAction(payload) {
    if (busy || !token) return;
    gomoku.clearPending();
    if (payload?.action === "interact") selectedPiece = null;
    busy = true;
    requestSequence += 1;
    render();
    try {
      const result = await fetchJSON(`/api/native/${encodeURIComponent(token)}/action`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      game = result.game;
      if (game?.game_type === "draw_guess") {
        if (["undo", "clear"].includes(payload?.action)) {
          drawState.dirty = false;
          drawState.revision = -1;
        }
        if (payload?.action === "guess") {
          drawState.guessDraft = "";
        }
      }
      setNotice("");
    } catch (error) {
      setNotice(errorMessage(error, "这一步未收到确认，请以同步后的棋盘为准"), true);
      await load({quiet: true, recovery: true});
    } finally {
      busy = false;
      render();
    }
  }

  if (!token) {
    setNotice("对局链接无效", true);
  } else {
    load({quiet: false});
    timer = window.setInterval(() => load(), 1800);
  }
  window.addEventListener("pagehide", () => window.clearInterval(timer));
})();
