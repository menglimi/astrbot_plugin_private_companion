#!/usr/bin/env python3
"""阻塞时长对比：原生轮次/预算读取在事件循环上占用了多久。

为什么值得一个脚本：这轮问题的现象是「页面请求超时」，定位它靠的是看门狗日志里那一行
``Event loop stalled 30.2 s``。日志能说明「循环被挡住了」，说不清「挡住它的那次读取本来
应该多快」。这个脚本把两件事量成数字：

1. **一次读取本身多久**（ms）；
2. **它在事件循环线程上执行时循环被挡住多久**（ms）——用一条只做 ``sleep(2ms)`` 的心跳
   协程测相邻心跳的最大间隔；心跳间隔就是循环不可用的时间。

覆盖本次修复的三条读路径：

* ``latest_scope``：每个活跃窗口一次，原来是七字段 json_extract 的全表扫描；
* ``context_rows(kind='private')``：``presence`` 每个 drive pass 一次；
* ``reclaim_expired`` 的候选扫描：每 60 秒一次，原来在事件循环上同步跑。

用法：

    python tools/bench_storage_blocking.py                       # 合成库，25000 轮次
    python tools/bench_storage_blocking.py --turns 8000 --contexts 120
    python tools/bench_storage_blocking.py --database <companion-v2.sqlite3>

``--database`` 先用 SQLite backup API 把库复制到工作目录再测：脚本会建、删索引，绝不能
拿到生产库上直接跑。复制走 backup API，WAL 里未 checkpoint 的内容也会带上。
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import random
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from companion_v2.conversation.native_turns import (NativeConversationTurns, context_rows)
from companion_v2.execution.models import TaskContext, encode, stamp
from companion_v2.foundation.refs import (AudienceRef, ScopeKind, ScopeRef, SubjectKind,
                                          SubjectRef)
from companion_v2.identity_policy.models import (AuthenticatedHostPrincipal, ResolvedIdentity,
                                                 RoleBinding)
from companion_v2.storage.native_conversation_schema import OPEN_TURN_STATES, STATEMENTS
from companion_v2.storage.runtime import StorageRuntime

INSTALLATION_ID = "bench-installation"
BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)
NEW_INDEXES = ("conversation_native_scope_idx", "conversation_native_open_turn_idx")

# ``BudgetLedger.reclaim_expired`` 发出的那条候选查询（companion_v2/model_gateway/
# budget.py）。原样抄在这里，测的就是线上形状。
RECLAIM_SELECT = """SELECT c.call_id, c.state, c.started_at, c.finished_at,
        t.kind AS kind, r.created_at AS requested_at
    FROM model_calls c
    JOIN execution_tasks t ON t.task_id = c.task_id
    JOIN model_requests r ON r.task_id = c.task_id
    WHERE c.actual_tokens IS NULL
        AND COALESCE(c.receipt_ref, '') NOT LIKE ?
        AND COALESCE(c.finished_at, c.started_at, r.created_at) <= ?
    ORDER BY COALESCE(c.finished_at, c.started_at, r.created_at)
    LIMIT ?"""

SCOPE_SELECT = """SELECT * FROM conversation_native_turns
    WHERE json_extract(context_json, '$.audience.scope.installation_id') = ?
      AND json_extract(context_json, '$.audience.scope.platform') = ?
      AND json_extract(context_json, '$.audience.scope.kind') = ?
      AND json_extract(context_json, '$.audience.scope.native_scope_id') = ?
      AND json_extract(context_json, '$.audience.scope.bot_id') = ?
      AND json_extract(context_json, '$.audience.scope.account_id') = ?
      AND json_extract(context_json, '$.audience.scope.session_id') = ?
    ORDER BY observed_at DESC, rowid DESC LIMIT 1"""


def context_for(index: int, kind: ScopeKind) -> TaskContext:
    scope = ScopeRef(INSTALLATION_ID, "onebot", kind, f"native-{index:05d}", "bot-a", "login-a",
                     f"conversation-{index:05d}", f"session-{index:05d}")
    subject = SubjectRef(INSTALLATION_ID, SubjectKind.HUMAN, f"user-{index % 37:03d}")
    return TaskContext(
        ResolvedIdentity(
            AuthenticatedHostPrincipal(INSTALLATION_ID, "host-a", "onebot", "native-account-a",
                                       "bot-a", "login-a"),
            subject, f"binding-{index:05d}", 2, 7),
        RoleBinding(scope, SubjectRef(INSTALLATION_ID, SubjectKind.BOT, "role-a"), 1),
        AudienceRef(scope, (subject,)))


def build_store(workdir: Path, turns: int, contexts: int, model_calls: int) -> Path:
    root = workdir / "synthetic"
    if root.exists():
        shutil.rmtree(root)
    runtime = StorageRuntime.open(data_root=root, installation_id=INSTALLATION_ID)
    NativeConversationTurns(runtime)  # the optional tables a real installation creates
    documents = [context_for(index, ScopeKind.GROUP if index % 3 else ScopeKind.PRIVATE)
                 for index in range(contexts)]
    turns_rows = []
    last_index_of_context = {}
    for index in range(turns):
        context = documents[index % contexts]
        observed = BASE + timedelta(minutes=index // contexts, microseconds=index)
        turns_rows.append((f"turn-{index:07d}", context.key, encode(context), "instruction",
                           f"observation-{index:07d}", stamp(observed), "completed",
                           stamp(observed), None))
        last_index_of_context[index % contexts] = index
    # presence only acts on windows whose *newest* turn is still open, so the open
    # turns have to be the last one of their context; a random open row would be
    # superseded and correctly filtered out.
    open_turns = set(list(last_index_of_context.values())[::20])
    turns_rows = [row[:6] + ("running",) + row[7:] if index in open_turns else row
                  for index, row in enumerate(turns_rows)]
    calls = []
    rng = random.Random(20261108)
    for index in range(model_calls):
        observed = BASE + timedelta(minutes=index)
        calls.append((f"call-{index:07d}", f"task-{index:07d}", f"attempt-{index:07d}", 1,
                      "generated", "{}", 1000, 1000, observed, rng.random() * 900))
    with runtime.transaction() as connection:
        connection.executemany("""INSERT INTO conversation_native_turns
            (turn_id, context_key, context_json, role_instruction, input_observation_id,
             observed_at, generation_state, phase_at, last_sent_at)
            VALUES (?,?,?,?,?,?,?,?,?)""", turns_rows)
        if calls:
            connection.executemany("""INSERT INTO execution_tasks
                (task_id, origin_key, kind, lane, dedupe_key, request_hash, revision, state,
                 generation, attempt_no, attempt_id, next_run_at, deadline_at, created_at,
                 updated_at, record_json)
                VALUES (?, 'bench', 'conversation.reply', 'foreground', ?, 'hash', 1, 'succeeded',
                        1, 1, ?, ?, ?, ?, ?, '{}')""",
                [(task, task, attempt, stamp(observed), stamp(observed), stamp(observed),
                  stamp(observed))
                 for _call, task, attempt, _position, _state, _provider, _tokens, _cost,
                 observed, _latency in calls])
            connection.executemany("""INSERT INTO model_requests
                (task_id, request_hash, plan_json, created_at) VALUES (?, 'hash', '{}', ?)""",
                [(task, stamp(observed))
                 for _call, task, _attempt, _position, _state, _provider, _tokens, _cost,
                 observed, _latency in calls])
            connection.executemany("""INSERT INTO execution_attempts
                (attempt_id, task_id, attempt_no, generation, lane, kind, origin_key, scope_key,
                 slot_held, lease_until, record_json)
                VALUES (?, ?, 1, 1, 'foreground', 'conversation.reply', 'bench', ?, 0, ?, '{}')""",
                [(attempt, task, attempt, stamp(observed))
                 for _call, task, attempt, _position, _state, _provider, _tokens, _cost,
                 observed, _latency in calls])
            connection.executemany("""INSERT INTO model_calls
                (call_id, task_id, attempt_id, position, state, provider_json, reserved_tokens,
                 reserved_cost, actual_tokens, actual_cost, receipt_ref, started_at,
                 finished_at, latency_ms)
                VALUES (?,?,?,?,?,?,?,?,NULL,NULL,NULL,?,NULL,?)""",
                [(call, task, attempt, position, state, provider, tokens, cost,
                  stamp(observed), latency)
                 for call, task, attempt, position, state, provider, tokens, cost, observed,
                 latency in calls])
    database = runtime.database_path
    runtime.close()
    checkpoint(database)
    return database


def checkpoint(database: Path) -> None:
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


def copy_store(source: Path, workdir: Path) -> Path:
    """Copy through the backup API: WAL content that is not checkpointed comes along."""

    # StorageRuntime always opens ``<data_root>/companion-v2.sqlite3``, so the copy
    # has to carry that name or the runtime would open a fresh empty store beside it.
    target = workdir / "copy" / StorageRuntime.DATABASE_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    reader = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    writer = sqlite3.connect(target)
    try:
        reader.backup(writer)
    finally:
        writer.close()
        reader.close()
    return target


def timed(connection, sql, parameters=(), *, repeat=3):
    best = None
    rows = ()
    for _ in range(repeat):
        started = time.perf_counter()
        rows = connection.execute(sql, parameters).fetchall()
        elapsed = (time.perf_counter() - started) * 1000
        best = elapsed if best is None else min(best, elapsed)
    return best, rows


async def measure(call, *, threaded: bool = False, repeat: int = 1):
    """Return (mean ms per call, longest loop gap in ms while it ran).

    The longest gap is the number a stall watchdog would log: it is how long the
    loop could not run anything else. The ticker yields with ``sleep(0)`` instead of
    a timer on purpose — a timer on Windows is quantized at the ~15.6 ms system tick,
    which would floor every measurement at one tick and hide exactly the difference
    this script exists to show. Spinning costs one core for the ~100 ms window, which
    is acceptable for a benchmark and not for shipped code.
    """

    gaps: list[float] = []
    state = {"recording": False, "stop": False}

    async def ticker():
        last = time.perf_counter()
        while not state["stop"]:
            await asyncio.sleep(0)
            now = time.perf_counter()
            if state["recording"]:
                gaps.append(now - last)
            last = now

    task = asyncio.create_task(ticker(), name="bench:ticker")
    await asyncio.sleep(0.05)  # let the ticker settle before the window opens
    gaps.clear()
    state["recording"] = True
    started = time.perf_counter()
    for _ in range(repeat):
        if threaded:
            await asyncio.to_thread(call)
        else:
            call()
    elapsed = (time.perf_counter() - started) * 1000 / repeat
    await asyncio.sleep(0.005)  # one more tick, so the last gap is recorded
    state["recording"] = False
    state["stop"] = True
    await asyncio.gather(task, return_exceptions=True)
    return elapsed, (max(gaps) * 1000 if gaps else 0.0)


def on_loop(call, *, repeat: int = 1, attempts: int = 3):
    """Best of three: the lowest call time and the lowest longest-gap.

    A single run can lose to one scheduler hiccup on a busy machine; the minimum of
    three is the cost of the read itself rather than of the moment it was measured.
    """

    results = [asyncio.run(measure(call, repeat=repeat)) for _ in range(attempts)]
    return min(result[0] for result in results), min(result[1] for result in results)


def on_thread(call, *, attempts: int = 3):
    results = [asyncio.run(measure(call, threaded=True)) for _ in range(attempts)]
    return min(result[0] for result in results), min(result[1] for result in results)


def new_index_statements():
    return {name: statement for statement in STATEMENTS for name in NEW_INDEXES
            if statement.startswith(f"CREATE INDEX IF NOT EXISTS {name} ")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, help="复制一份既有库再测（不会改原库）")
    parser.add_argument("--turns", type=int, default=25000, help="合成库的轮次行数")
    parser.add_argument("--contexts", type=int, default=250, help="合成库的会话数")
    parser.add_argument("--model-calls", type=int, default=20000, help="合成库的模型调用行数")
    parser.add_argument("--workdir", type=Path, help="工作目录（默认系统临时目录）")
    options = parser.parse_args()

    stamps = new_index_statements()
    if len(stamps) != len(NEW_INDEXES):
        print(f"警告：schema 模块里缺少 {sorted(set(NEW_INDEXES) - set(stamps))}，"
              "本机代码可能早于本次修复")

    workdir = options.workdir or Path(tempfile.mkdtemp(prefix="companion-bench-"))
    workdir.mkdir(parents=True, exist_ok=True)
    if options.database is not None:
        database = copy_store(options.database.expanduser().resolve(), workdir)
        print(f"复制 {options.database} -> {database}")
    else:
        print(f"合成 {options.turns} 轮次 / {options.contexts} 会话 / "
              f"{options.model_calls} 模型调用 ...")
        database = build_store(workdir, options.turns, options.contexts, options.model_calls)

    # check_same_thread=False mirrors StorageRuntime: the shipped runtime opens its
    # connection that way and serialises statements on one RLock, which is what makes
    # a maintenance worker thread possible in the first place.
    connection = sqlite3.connect(database, isolation_level=None, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    installation = connection.execute(
        "SELECT value FROM runtime_metadata WHERE key = 'installation_id'").fetchone()
    if installation is None:
        print("这个库没有 runtime_metadata.installation_id，不是 V2 存储")
        return 1
    print(f"工作库：{database}（{database.stat().st_size / 1e6:.1f} MB）")
    print("轮次行数：", connection.execute(
        "SELECT COUNT(*) FROM conversation_native_turns").fetchone()[0],
        "模型调用行数：", connection.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0])

    runtime = StorageRuntime.open(data_root=database.parent, installation_id=installation[0])
    try:
        # The cheap answer must be the same answer: open_only filters the outer scan
        # before the latest-per-context probe, and that is only correct if the two
        # spellings agree.
        every_turn = context_rows(runtime, kind="private")
        open_only = context_rows(runtime, kind="private", open_only=True)
        expected = tuple(row for row in every_turn
                         if row["generation_state"] in OPEN_TURN_STATES)
        print(f"一致性：open_only {len(open_only)} 行，与「先取最新再按状态过滤」"
              f"{'一致' if open_only == expected else '不一致'}")

        sample = next(iter(every_turn), None) or next(iter(context_rows(runtime)), None)
        if sample is None:
            print("库里没有轮次行，无法测量 scope 探测")
            return 0
        scope_document = json.loads(sample["context_json"])["audience"]["scope"]
        scope_parameters = tuple(scope_document[key] for key in (
            "installation_id", "platform", "kind", "native_scope_id", "bot_id", "account_id",
            "session_id"))
        reclaim_parameters = ("released:%", stamp(BASE + timedelta(days=400)), 1000)

        print("\n== 修复之后（新增索引在库上）==")
        for statement in stamps.values():
            connection.execute(statement)
        after_scope_ms, after_scope_gap = on_loop(
            lambda: runtime.read_one(SCOPE_SELECT, scope_parameters))
        after_presence_ms, after_presence_gap = on_loop(
            lambda: context_rows(runtime, kind="private", open_only=True))
        scope_row_after = connection.execute(SCOPE_SELECT, scope_parameters).fetchone()

        print("\n== 修复之前（去掉这两个索引）==")
        for name in NEW_INDEXES:
            connection.execute(f'DROP INDEX IF EXISTS "{name}"')
        before_scope_ms, before_scope_gap = on_loop(
            lambda: runtime.read_one(SCOPE_SELECT, scope_parameters))
        before_presence_ms, before_presence_gap = on_loop(
            lambda: context_rows(runtime, kind="private"))
        scope_row_before = connection.execute(SCOPE_SELECT, scope_parameters).fetchone()

        reclaim_sync_ms, reclaim_sync_gap = on_loop(
            lambda: connection.execute(RECLAIM_SELECT, reclaim_parameters).fetchall())
        reclaim_thread_ms, reclaim_thread_gap = on_thread(
            lambda: connection.execute(RECLAIM_SELECT, reclaim_parameters).fetchall())

        print("\n== 结论（同一份数据，单次调用）==")
        print(f"{'读取路径':34s} {'循环占用 前':>13s} {'循环占用 后':>13s}")
        print(f"{'latest_scope 探测':34s} {before_scope_gap:10.2f} ms "
              f"{after_scope_gap:10.2f} ms   ({before_scope_ms:.2f} -> {after_scope_ms:.2f} ms)")
        print(f"{'presence 每 pass 读取':34s} {before_presence_gap:10.2f} ms "
              f"{after_presence_gap:10.2f} ms   ({before_presence_ms:.2f} -> "
              f"{after_presence_ms:.2f} ms)")
        print(f"{'reclaim 扫描（循环线程 -> 工作线程）':34s} {reclaim_sync_gap:10.2f} ms "
              f"{reclaim_thread_gap:10.2f} ms   ({reclaim_sync_ms:.2f} -> "
              f"{reclaim_thread_ms:.2f} ms)")
        print(f"\nscope 探测结果一致："
              f"{bool(scope_row_before) and tuple(scope_row_before) == tuple(scope_row_after)}")
        return 0
    finally:
        runtime.close()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
