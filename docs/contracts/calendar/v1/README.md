# 日历与持续生活契约包

> `companion.calendar@1`，修订 `0.1.0`，状态 `review`。这是设计期机器契约，不是生产 SDK，也不修改旧日历数据库。

本包把持续剧本对日程的影响压缩为五个边界：`ActivityIntent` 表示角色软意图，`CalendarBoundary` 表示当前决策所需的有限时间窗口，`ActivityProcess` 保存可恢复的活动过程和检查点，`StoryProjection` 是按受众裁剪且会过期的叙事投影，`CalendarRequest/Result` 约束 observe/suggest/reserve/commit 与局部影响回执。

日历仍是事实与时间约束源。角色自主生成的活动默认 `reality_mode=simulated`，不会自动写入 `user_calendar`；`commit` 必须带授权或用户确认。`calendar-result.affected_refs` 只列受影响活动、过渡段、缓冲段和候选，禁止用一次变更重生成全天剧本。

对象复用公共 `RuntimeScope`、证据、revision 和时间格式。故事正文不是事实、记忆或现实观测；它只在 `valid_until` 内供当前受众表达。长活动只在互动、承诺边界、外部观测、主动评估或检查点到达时推进，离线恢复使用 checkpoint，不按错过的每个时刻补模型调用。

## 文件

| 文件 | 用途 |
| --- | --- |
| `schemas/activity-intent.schema.json` | 软活动意图 |
| `schemas/calendar-boundary.schema.json` | 有界 `TemporalDecisionContext` |
| `schemas/activity-process.schema.json` | 长活动、暂停/恢复与 checkpoint |
| `schemas/story-projection.schema.json` | 受众过滤的短期叙事 |
| `schemas/calendar-request.schema.json` | 日历控制请求 |
| `schemas/calendar-result.schema.json` | 日历回执与局部影响集 |
| `calendar.fixture.json` | CAL-01--04 设计回放，当前 `not_run` |

## 验收重点

机器格式通过不等于运行完成。隔离运行时还需证明：`user_calendar` 硬承诺不会被模拟活动覆盖；`suggest/reserve/commit` 的授权边界可审计；变更只触发局部重算；同一 checkpoint 幂等恢复；故事投影按 `session/user/global` 脱敏；工作集、投影、队列和模型调用不超过共享预算。

本包与[架构基线 §3.7](../../../ARCHITECTURE_RESET.md#37-日历和日程)、[优先领域状态机](../../../DOMAIN_STATE_MACHINES_V0.md)和[角色/主动蓝图](../../../ROLEPLAY_PROACTIVE_REBUILD.md)保持语义一致。它不新增第二套日历 API。
