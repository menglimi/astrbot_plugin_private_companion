# 记忆契约 v0

> 导航：[设计总纲](./FRAMEWORK_DESIGN.md) / [主题目录](./FRAMEWORK_DESIGN_INDEX.md)。定位：记忆领域草案；维护对象语义，对外字段和转移依赖记忆接口与切片状态机。

本稿定义第一条纵向闭环使用的最小记忆协议。它只规定事实来源、作用域、生命周期和写入结果，不规定角色必须记住哪些中文词，也不把临时情绪直接写成长期人格。

持续性生活剧本不会新增一套记忆库。`StorySegment`、角色内心、场景描写和活动 tick 是可压缩的叙事投影；只有用户确认、可解释的活动边界、承诺边界、会话摘要或明确治理意图，才由 AI 提出 `MemoryProposal`。提议仍须经过 Memory Writer 的证据、归属、revision、retention 和撤回规则；故事写得连贯不等于事实已经持久化。

本文维护领域对象和生命周期；跨插件字段、能力 ID、作用域封套、版本与回执以[记忆提议、查询与外部注入契约](./MEMORY_PROPOSAL_QUERY_CONTRACT_V0.md)为准。下方字段是领域视图，调用时使用标准封套，不能另造精简 scope 或第二套接口。仍属于设计草案，尚未冻结 SDK。

## 1. 记忆的四类对象

### MemoryAtom

表示一条可被检索和修正的事实或偏好：

```text
id
scope
owner_ref              稳定归属引用，按已授权 namespace 解析
namespace
subject
predicate
object
qualifiers             区分不同作品、家庭/公司等限定条件
polarity
revision
status                 active / superseded / invalidated / expired
confidence
evidence_refs[]
valid_from
valid_to
retention              session / short / durable / protected
source_type
source_id
attribution            user / third_party / bot / unknown
representation         可供模型阅读的表达；不能替代结构化事实或证据
created_at
updated_at
```

`predicate` 和 `object` 允许开放语义。代码只检查结构完整性、长度、作用域和生命周期，不维护固定中文类别。

### MemoryProposal

由 AI 提交给 Memory Writer 的建议：

```text
operation               add / correct / retract / no_op
target_atom_id          correct/retract 必填
expected_revision      correct/retract 必填
namespace
visibility
purpose
subject
predicate
object
qualifiers
polarity
evidence_refs[]
confidence
valid_from
valid_to
retention
attribution
stability_estimate     稳定性短判断，不是思维链
defer_until_boundary   是否先进入会话级待处理队列
reason                  可审计的短理由，不是隐性思维链
```

Memory Writer 不读取 `reason` 来推断 operation；operation 必须来自结构化字段。

`scope`、`trace_id`、`idempotency_key` 和提供方 generation 放在标准封套。`reason/stability_estimate` 是内部评估材料，对外需要传递时使用已声明、受限且非必需的扩展字段；接收方不能按其文本推断操作。`types/extensions` 承载新领域的类型与属性，支持的必需语义通过 required_features 协商。

`proposal_state`、`pending_since` 和 `batch_id` 由 Writer 的提议/操作账本维护，不是 MemoryAtom 的事实状态或外部调用者可自行设置的提交证明。旧 `collected` 表示模型收集阶段的日志标签，兼容层按实际校验和提交进度映射，不能直接转换为 active。

### MemoryQuery

```text
namespace
visibility
query_intent            AI 生成的语义查询
subjects[]
types[]
time_range
at_time
allowed_retention[]
evidence_budget         max_items / max_chars / max_tokens
purpose                 例如 reply / planning / proactive；新用途可按命名空间协商
confidence_floor
include_pending
conflict_policy         current_only / include_history
cursor
extensions
```

查询结果只返回当前作用域和用途允许的记忆，并附带 `EvidenceRef`，不直接把整个历史库交给模型。

查询采用公共 RuntimeScope。freshness_weight 等排序参数由可替换策略描述符管理，不成为所有外部调用方必填字段；类型过滤属于请求语义，不能在提供方不支持时静默扩大范围。

### MemoryCorrection

用于用户明确纠正、撤回或限制记忆：

```text
target_atom_id / target_query
operation               correct / forget / restrict / restore
scope
expires_at
evidence_refs[]
```

`forget` 不是简单删文本：需要使当前事实失效，并保留最少的审计记录，防止旧版本重新被召回。

MemoryCorrection 表示用户治理意图，尚不是另一条已冻结外部接口。纠正/撤回按明确目标和 revision 转换到 proposal.submit；批量遗忘、物理擦除、restrict/restore 需独立治理能力和授权，未提供该能力时明确返回 unavailable，不以普通 retract 回执声称已经完成物理删除。

### MemoryContextPolicy（运行时扩展）

用于表达爱语三种上下文模式背后的工程权衡，但不把模式写成人格规则：

```text
mode                  semantic / rolling_summary / recent_only
budget_tokens
protected_tail        最近对话保护预算
summary_trigger       provider/预算/事件边界条件
cache_key             稳定摘要前缀的版本键
```

Runtime 根据模型能力、当前 token 预算和任务用途计算这些值；AI 负责摘要内容和记忆提案，不能通过提示词绕过预算或作用域限制。

## 2. 写入规则

### AI 负责

- 判断消息是否包含稳定事实、承诺、偏好、关系变化或可延续事件；
- 识别否定、引用、条件、时间和多意图；
- 给出证据编号、置信度、有效时间和保留建议；
- 选择新增、更新、失效或不写入。
- 判断提案应立即提交，还是先作为当前会话的 pending 提案提供给本轮上下文。

### Runtime 负责

- 验证证据确实存在且属于当前作用域；
- 验证跨私聊、群聊、公开目标的可见性；
- 处理同一事实键的版本、冲突和并发写入；
- 执行 TTL、撤回、保护和清理；
- 保证幂等、重试、死信和重载安全。
- 为待处理队列维护消费游标、批次和摘要边界；总结失败时保留队列并在下次任务重试。

### 不允许的写入

- 仅凭关键词命中写入；
- 仅凭 Bot 自己生成的情绪句子写入长期人格；
- 将一次性寒暄直接升级为稳定偏好；
- 将未确认的计划写成已完成事实；
- 将群聊中的他人信息归入当前用户档案；
- 将 `reason`、内部备注或模型思维链当作事实证据。

## 3. 最小状态转移

```text
提议主路径：received → validated → accepted → persisted
分支：pending / quarantined 经重验后再提交；no_op → skipped
提交前可终止为 rejected / cancelled

事实生命周期（与提议状态分开）：
add / correct 提议提交后产生 active 事实
active → superseded
active → invalidated
active → expired
```

对声明为单值的稳定事实键，同一 `owner + namespace + subject + predicate + qualifiers` 只允许一个当前 active 版本。多值偏好和不同经历可以并存；未知类型不自动合并或互相替换。纠正关闭明确目标的旧版本，与新版本在同一事务提交，历史版本仍可用于解释冲突和纠正来源。

上图只摘要对象边界；完整转移、恢复和未知提交以[切片状态机](./MEMORY_VERTICAL_SLICE_STATE_MACHINE_V0.md)为准。persisted 表示有事实副作用的提议已经提交，不表示所有操作都会新增 active 事实：retract 使目标 invalidated，no_op 返回 skipped。

`pending` 提议和旧 `collected` 日志都不能当作已确认的长期事实。混合上下文可以把获授权的 pending 提议作为 `session` 级 `ContextContribution` 注入当前请求，同时保持 active 记忆集合和稳定前缀不变。

摘要任务至少保存以下运行信息：

```text
last_summarized_event_id
summary_boundary
summary_version
policy_version
source_event_ids[]
added_atom_ids[]
batch_id
retry_count
lease / status
```

这组信息用于重启续跑、批次原子提交和防止同一消息被重复总结。

如果新提案时间更早、来源更弱或证据不足，Writer 可以保留当前版本并将提案标记为 `rejected`，不能静默覆盖。

## 4. 首条闭环的状态语义

```text
message_received
→ evidence_indexed
→ intent_interpreted
→ memory_proposed
→ memory_committed / memory_skipped
→ proactive_candidate
→ motive_decided
→ action_queued / action_abstained
→ delivery_submitted / delivery_accepted / delivery_delivered / delivery_uncertain / delivery_failed
→ feedback_waiting
→ feedback_observed / feedback_expired
```

以上是阶段日志标签，外部 status 使用公共 OperationResult 或 DeliveryReceipt。任意阶段失败都要留下结构化状态。`delivery_accepted` 只表示平台受理；`delivery_uncertain` 需对账，不能作为送达证据，`feedback_expired` 不得被解释成负面反馈。

## 5. 第一批验收例子

### 例一：边界与承诺并存

用户说：“今晚别一直问我，明早八点提醒我交材料。”

必须拆成两个语义对象：

- 一个有期限的互动边界；
- 一个有明确时间的提醒提案。

不能因为同一句里出现“别”就丢弃提醒，也不能因为出现“提醒”就跳过时间和来源确认。

### 例二：引用不归因

用户说：“他说‘我最喜欢熬夜’。”

这句话默认是关于第三方的引用，不能直接写成当前用户的偏好。

### 例三：纠正旧事实

旧记忆是“用户住在 A”。用户说“我已经搬到 B 了”。

系统新增 B 的当前事实，关闭 A 的 active 状态，并保留 A 作为历史版本；后续现实触及和主动行为只能使用 B。

### 例四：无反馈不猜测

主动发出一句关心后，用户数小时没有回应。

系统只能记录 `feedback_expired`，不能生成“用户喜欢这次关心”或“用户不想理我”的长期记忆。

## 6. 设计门槛

记忆切片进入实现前必须满足：

1. 作用域和可见性有明确测试；
2. 每个 active 事实都能追溯到证据；
3. 纠正、撤回和过期不会被旧缓存恢复；
4. 重试和重启不会重复写入或重复主动；
5. 未枚举的 `predicate/object` 可以保存；
6. 旧硬编码审计表中的否定、引用、反话、多意图案例不会误写入；
7. Memory、Affect、Proactive 三类写入职责彼此分开。
8. 外部来源、查询调用方和检索策略通过同一能力协议协商；增加类型不修改公共封套，也不增加默认模型调用数。
