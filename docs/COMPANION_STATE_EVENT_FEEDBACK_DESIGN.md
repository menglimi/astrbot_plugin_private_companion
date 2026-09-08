# 陪伴状态、事件与反馈闭环设计

> 导航：[设计总纲](./FRAMEWORK_DESIGN.md) / [主题目录](./FRAMEWORK_DESIGN_INDEX.md)。定位：公共语义草案；维护共同闭环，详细转移依赖专题状态机。

> 状态：设计稿。本文是记忆、现实触及、实时共处、扮演和主动行为的共同上游模型，不规定具体插件实现或固定词表。

## 1. 总体闭环

```text
Evidence（消息/观察/工具回执）
  → Interpretation（Intent/Affect/Motive）
  → State（会话/关系/连续生活/记忆）
  → StoryProjection（本轮叙事视图，不是新的事实库）
  → Plan（回复/主动/设备/媒体）
  → Execution（工具与投递）
  → Feedback（结果/用户反应/过期）
  → 下一轮 Evidence
```

事件描述发生了什么；AI 解释事件意味着什么；状态保存可延续的结果；计划决定是否表达或行动；运行时执行并记录真实回执。任一层不能伪造下一层的结果。

## 2. 五类状态

| 状态层 | 内容 | 生命周期 | 权威来源 |
| --- | --- | --- | --- |
| `SceneState` | 当前会话、群聊、房间、正在进行的活动和占用态 | 秒至会话结束 | Core / Together |
| `GlobalActorState` | 同一人格在多个窗口中的公共生活、当前活动、注意力和全局 revision | 跨窗口，按事件提交 | Core / World |
| `ConversationWindowState` | 单个私聊/群聊窗口的受众、话题、占用、近期上下文和 checkpoint | 秒至窗口结束或冷却 | Core |
| `AffectState` | 面向目标的情绪、表达姿态、强度、持续性和可靠性 | 短期，可衰减 | Core 的 Affect 服务 |
| `ContinuityState` | 未完成事项、承诺、角色自我生活、物品/地点/活动进度 | 跨会话，带有效期 | Core / 领域服务 |
| `MemoryState` | 经证据确认的事实、偏好、关系和经历 | 按 retention 保存 | Memory |
| `ObservationState` | 设备、屏幕、位置、媒体进度等外部观测 | 由 TTL 决定 | Reality / Screen / Together |

情绪不是记忆，观测不是事实，候选不是行动，表达不是回执。跨层转换必须通过带证据的结构化事件或提案完成。

## 3. 事件类型与数据流

统一事件的语义至少覆盖：`event_id`、`scope`、共享归属、`source`、`observed_at`、`expires_at`、`evidence_refs`、`causation_id` 和 `revision`。共享归属包括 `sharing_mode=session|user|global`、Kernel 解析的 `sharing_anchor` 和 `sharing_policy_revision`，不能由模型或插件自行拼接。这是共同语义，不是向所有已有事件 Schema 添加顶层属性；具体 wire 由事件所属契约定义，Memory 请求/召回使用命名空间扩展，见[共享契约包](./contracts/sharing/v1/README.md)。订阅与变更事件的共享关联仍需专题定型和运行验证。事件按以下类型流动：

```text
ObservationEvent   外部或对话中观察到的内容
StoryEvent         用户消息、活动检查点和角色生活边界进入持续剧本
InterpretationEvent AI 对意图、情绪、目标和不确定性的解释
StateChangeEvent   经 owner 接受的状态变化
PlanEvent          回复、主动、设备或媒体计划
ExecutionEvent     工具执行和投递过程
FeedbackEvent      用户反应、结果确认、未知或反馈过期
```

模型输出只能产生 `InterpretationEvent` 和候选 `PlanEvent`；`StateChangeEvent`、`ExecutionEvent` 和成功 `FeedbackEvent` 必须由对应 owner 或真实适配器确认。

持续剧本是跨领域的编排视图，不是第六个权威数据 owner。`StoryEvent` 只引用 World、Relationship、Affect、Memory 和 Session 的来源事件；`StoryProjection/StorySegment` 必须带输入 revision、`source_event_refs`、`reality_mode`、可见性和字节预算。叙事可以让角色表现出疲惫、分心、想分享或暂时沉默，但不能单凭自然语言创建长期事实、现实观测、用户认可或外部动作结果。

## 4. AI 与 Runtime 的边界

AI 负责：多意图、否定、引用、条件、时态、情绪归因、关系含义、主动动机、表达方式和是否等待。Runtime 负责：作用域、权限、证据存在性、TTL、预算、能力、幂等、取消、版本、投递和持久化安全。

Runtime 不扫描模型理由或普通自然语言来推断动作。需要硬门时，输入必须是结构化字段，例如 `ConsentGrant`、`BoundaryIntent`、`ActionRequest`、`MemoryProposal` 或 `DeliveryReceipt`。

## 5. 三个优先领域如何联动

### 5.1 记忆

对话或活动先产生证据和临时上下文；AI 提交 `MemoryProposal`；Memory 校验归属、冲突、有效期和保留策略后，才进入 `MemoryState`。当前问题召回时同时返回答案所需证据和来源，不把候选命中等同于可回答。

### 5.2 现实触及

设备只上报带时效的 `ObservationEvent`。AI 可以把“刚回家”解释为欢迎机会，但不能把它直接写成永久事实或直接执行设备动作。动作必须经过授权、风险检查和 `OperationResult`，离线/未知不能写成成功。

### 5.3 实时共处

Together 建立 `SessionRecord`，在会话内持有临时上下文、媒体进度和参与者权限。实时内容不逐帧进入普通记忆或主动额度；结束时由 AI 提出经历摘要，再由 Memory 决定是否保存。断线恢复使用 session revision，旧代结果不能回写。

## 6. 扮演与主动的统一关系

扮演是 `ResponsePlan` 的表达层，主动是 `MotivePlan` 驱动的接触候选。二者共享同一 `PersonaSnapshot`、`RelationshipView`、`SceneState` 和可用证据；主动候选必须经过当前对话占用态、边界、预算、冷却和投递幂等检查。

角色可以选择沉默、等待、延后或转换为会话内表达。沉默、延后和失败都要记录原因，但不能生成未经证实的用户态度记忆。

## 7. 反馈闭环

```text
计划/候选 -> Runtime 授权与调度 -> 投递请求
DeliveryReceipt: pending / submitted / accepted / delivered / partial / uncertain / failed / cancelled
用户反馈: 按关联引用单独记录确认、纠正、拒绝或反馈窗口到期
```

上述三行属于不同对象，不是一条共享状态枚举。queued 是内部任务阶段；早期 sent/unknown 日志须根据真实证据映射为 accepted/delivered/uncertain，不能直接证明用户收到。详细投递转移见[切片状态机](./MEMORY_VERTICAL_SLICE_STATE_MACHINE_V0.md)，公共结果枚举见[注入协议](./COMPANION_INJECTION_PROTOCOL.md)。

反馈窗口到期仍无回应时只记录 `feedback_expired`；不能解释为喜欢或讨厌，也不能改写投递回执。用户的新消息可以吸收、取消或改写相关候选，但不能误伤无关承诺。纠正和撤回必须传播到状态投影、召回、摘要和缓存。

## 8. 设计验收例子

- “我今天好累”：影响当前 `AffectState` 和表达姿态，默认不写长期偏好，也不自动产生主动任务。
- “晚安，明早八点提醒我”：分别形成会话收束、带时间的任务/记忆提案和主动状态，不能被一个否定词整体丢弃。
- 现实观测“用户刚回家”：作为短期观察供 AI 选择欢迎或等待，过期后不再驱动行动。
- 一起看完电影：会话内保留进度和评论；结束后形成可审查的经历提案，不自动保存全部原文。
- 主动消息发送超时且可能已提交：回执进入 `uncertain`，查询或等待确认后再决定重试，不能直接重复发送。

### 8.1 角色决策闭环回放

以下场景用于验证同一事件经过世界、情绪、关系、动机、回复和主动边界后仍保持可解释、可恢复和有界。它们是设计验收输入，不表示已经完成运行验证：

1. **活动被用户打断。** `WorldEvent(activity_paused)` 更新活动 checkpoint；`AffectState` 只产生短暂负荷，`MotiveProjection` 保留 `connect` 与 `resume`，当前回复吸收同主题内容；不生成旁路主动消息。
2. **硬承诺冲突。** `user_calendar` 的 confirmed commitment 阻塞角色的软 `share` 动机，`DecisionGate` 返回 `blocked(calendar_conflict)`；可以提出延后或合并候选，不能自动移动用户承诺。
3. **跨用户可见性。** 用户 A 的私聊事件可以影响 global-safe 的低敏公共情绪投影，但用户 B 只能获得脱敏 `StoryProjection`；原始证据、关系细节和 session 占用态不得进入 B 的快照。
4. **语义不确定。** 讽刺、转述或对象不明的消息只产生低置信度解释，关系和情绪保持上一有效投影；必要时生成澄清意图，不用关键词直接扣分或触发修复消息。
5. **用户纠正与撤回。** 纠正事件使相关 `AffectEvent`、`RelationshipEvent`、`MotiveProjection`、故事投影和主动候选按来源 revision 同时失效；旧投递回执不被改写，已提交的外部动作仍走对账。
6. **模型不可用或重复事件。** 语义复核超时使用上一有效状态和中性 `ExpressionPosture`；相同 `event_id` 重放不重复累加情绪、不重复创建候选，主回复仍可完成。

## 9. 后续设计顺序

本模型的状态边界、事件字段、owner、共享组合和优先级已经形成专题草案；日历/日程的 HDSI 适配见[架构基线 §3.7.1.3.1](./ARCHITECTURE_RESET.md#37131-持续剧本对日历的适配)，世界模拟的持续剧本见[世界切片](./DOMAIN_STATE_MACHINES_V0.md#7-世界模拟纵向切片学习被打断与跨会话恢复)。当前全局排期只看[设计总纲 §10--§12](./FRAMEWORK_DESIGN.md#10-当前进度与统一路线)。

本专题下一项是运行[角色决策契约包](./contracts/decision/v1/README.md)的跨域回放：日历硬承诺、角色软活动、活动 checkpoint、StoryProjection、用户打断和主动候选在同一时间线上互不越权，并验证 `RoleDecisionSnapshot` 的 revision、占用态、预算和降级行为。旧的“先冻结全部状态、再分别补三套状态机”的列表保留为历史推进记录，不构成新的前置条件。
