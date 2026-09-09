# 扮演与主动内核长期重建蓝图

> 导航：[设计总纲](./FRAMEWORK_DESIGN.md) / [主题目录](./FRAMEWORK_DESIGN_INDEX.md)。定位：角色与主动领域设计；依赖公共状态及投递契约，局部路线按总纲安排。

本文是 [ARCHITECTURE_RESET.md](./ARCHITECTURE_RESET.md) 的长期设计补充。它描述后续重新搭建陪伴插件时的目标运行模型，不把现有 `main.py`、旧主动循环或迁移桥当作新系统的模块边界。

迁移阶段只负责保护存量数据和用户行为连续性。旧实现提供行为样本、回放输入和回归基线；新系统的 DTO、状态机、存储所有权和调用顺序以本文及架构基线为准。迁移完成后，旧字段、旧入口和兼容桥都应能够被删除。

主动偏好、接触成本、类型化反馈和分级授权的用户调研依据见 [PROACTIVE_PREFERENCE_SURVEY_REVIEW_20260907.md](./PROACTIVE_PREFERENCE_SURVEY_REVIEW_20260907.md)。本文的 `ContactBudget`、占用态和候选生命周期需要与该调研稿中的 `ProactivePreference` 组合，而不是退化为单一频率配置。

## 1. 重建目标

新版陪伴体系只保留一条用户可见的交互主链，同时支持两种触发方式：

```text
被动：用户消息 -> 作用域 -> 上下文 -> 扮演 -> 响应计划 -> 投递

主动：外部信号 -> 动机 -> 接触机会 -> 扮演 -> 响应计划 -> 投递
```

两条路径共享人格、关系、情绪、表达、模型资源、审核、投递、历史和诊断边界。主动不是一套旁路 LLM，扮演也不是一段可以直接调用发送 API 的提示词。

内核需要保证以下不变式：

1. 一次请求只有一个已解析的 `RuntimeScope` 和一个生效人格。
2. 生成只产生 `ResponsePlan`，不直接发送消息、写长期记忆或修改关系。
3. 所有外部动作经过 `DeliveryGateway`，并以 `DeliveryReceipt` 结算。
4. 模型可以解释证据、选择表达和提出候选，不能提升权限、证据等级、优先级或预算。
5. 领域插件只拥有自己的数据；跨插件协作使用版本化 DTO 和引用，不读取对方私有目录。
6. 任何异步结果都必须带输入 revision 和 provider generation，过期结果只能留下诊断记录。

## 2. 运行时分层

```text
AstrBot Adapter
        |
Companion Kernel
  Scope / Clock / Event / Policy / LLM / Prompt / Delivery / Lifecycle
        |
Roleplay Engine       Proactive Engine
        |                     |
Persona / Relation     Signal / Motive / Opportunity / Budget / Ledger
        |
Feature Services
  Memory / Content / Image / Reality / Screen / Together / Game / Live
        |
Platform / Provider / Device Adapters
```

### 2.1 Kernel 的职责

Kernel 只提供协调能力，不替领域插件保存业务副本：

| 内核服务 | 长期职责 |
| --- | --- |
| `ScopeResolver` | 从宿主事件解析安装、Bot、平台账号、会话、主体和人格；生成不可变 `RuntimeScope` |
| `Clock` | 提供带时区的当前时间、时间段和可复现测试时钟 |
| `EventJournal` | 接收带证据和 revision 的领域事件，支持幂等、重放和诊断 |
| `PolicyRegistry` | 加载版本化门槛、评分、合并、表达、重试和结算策略 |
| `ModelGateway` | 按任务类型、人格授权和资源预算调用 Provider；统一取消和失败语义 |
| `PromptCompiler` | 合并有来源的结构化 PromptSection，执行裁剪、排序、敏感边界和最终冻结 |
| `DeliveryGateway` | 执行消息、媒体和外部动作，维护提交、受理、部分成功和未知回执 |
| `TaskSupervisor` | 管理任务、连接、取消、热重载、generation 和资源预算 |
| `Diagnostics` | 保存 trace、证据、策略版本、拒绝原因、资源成本和回放入口 |

Kernel 不拥有作品、长期记忆、图片、设备、直播房间或游戏对局。它只保存调用所需的短期投影和有期限引用。

### 2.2 两个一等引擎

`RoleplayEngine` 和 `ProactiveEngine` 是核心内的稳定职责，不属于某个可选 Feature 插件。

- `RoleplayEngine` 负责“以当前人格怎样回应”。
- `ProactiveEngine` 负责“现在是否值得接触、接触什么以及何时接触”。
- `DeliveryGateway` 负责“是否真的执行了外部动作”。

这样，情绪不会越权成为消息，主动来源不会越权成为发送者，Feature 插件也不需要知道宿主发送细节。

## 3. 扮演内核

### 3.1 扮演输入

扮演只接受结构化的 `RoleplayRequest`。建议最小字段如下：

```text
request_id / trace_id / parent_trace_id
scope / persona_snapshot / persona_revision
target_subject / relationship_projection
affect_projection / motive_projection
continuity_snapshot / affordance_snapshot
temporal_decision_context
context_contributions[]
interaction_mode / intent_plan
allowed_tools / allowed_modalities
input_budget / output_budget / deadline
provider_generation / policy_versions
```

`interaction_mode` 至少区分 `passive_reply`、`proactive_contact`、`tool_followup`、`session_expression`。模式改变目的和预算，不改变人格身份。

### 3.2 人格、关系和状态的边界

| 对象 | 作用 | 是否长期持有 | 是否能直接改变正文 |
| --- | --- | --- | --- |
| `PersonaSnapshot` | Bot 是谁、基本设定、世界观、稳定表达原则 | 是，按人格 revision 版本化 | 提供最高层角色约束 |
| `RelationshipProjection` | 当前人格面对某个主体的关系、权限和关系能力 | 是，按人格与目标归属 | 限制距离、称呼和可用关系表达 |
| `PersonaWorldModel` | 角色的房间、物品、路线、设备、食物和其他生活环境 | 跨时间段持久保存，实体带来源、可见性、`reality_mode` 和 revision | 决定可用资源、场景连续性和动作可行性 |
| `EmbodimentState` / `ActivityProcess` | 当前身体状态、注意力和跨时间段活动过程，如学习、刷手机、洗澡和散步 | 状态按字段 TTL；活动可暂停、恢复并形成 `ActivityEpisode` | 影响当前可做什么、如何恢复以及表达负荷 |
| `HabitModel` | 时长、口味、路线、穿衣和节奏等带上下文的倾向分布 | 跨日积累，按证据和置信度更新 | 影响选择概率，不把角色锁成固定脚本 |
| `PersonaContinuityState` | 当前活动引用、未完成事项、进行中的目标、重复形成的偏好和近期经历引用 | 跨时间段，按字段 TTL 和 revision 管理 | 影响下一段活动、动机、表达和主动候选；不直接替代事实证据 |
| `AffectState` | 当前窗口或目标下的短期情绪、负荷和恢复阶段 | 短期 | 只能产生有限调制 |
| `MotiveState` | 当前更想推进的方向，如关心、修复、休息、探索 | 短期 | 只能影响意图和候选倾向 |
| `ExpressionPosture` | 温度、距离、直接度、长度、追问和模态偏好 | 每次响应 | 通过 PromptCompiler 影响表达 |
| `EvidenceRecord` | 外部资料、用户陈述、内部状态和执行回执的来源 | 按保留策略 | 约束可声明的事实 |

人格设定不能被用户输入、模型输出、情绪事件或主动候选直接改写。关系可以通过明确的关系事件和审核流程更新；情绪和动机只能在自己的 revision 内变化。

`ScheduleProjection` 和 `PromptSnapshot` 都是短期视图，不能承担角色生活的全部连续性。`PersonaContinuityState` 由 `ActivityEpisode`、日历承诺、目标、互动和动作回执投影而来，经过 owner、证据、TTL 和 revision 校验后持久化。它同时供活动选择、动机、表达和主动调度使用；模型只能消费经过裁剪的 `ContinuitySnapshot`，不能用一页新的日程覆盖它。

技能接入连续生活必须经过“观察/动作 -> 领域事件 -> `StateTransitionProposal` -> 状态提交 -> 新投影”的路径。只添加 prompt 片段、返回一次命令或写入一页日程的技能，不足以改变角色状态；技能也不能直接改人格核心、生成永久 system prompt 或发送消息。

### 3.2.1 持续剧本驱动的扮演

参考 HDSI 的持续写作式生活，`RoleplayEngine` 每次处理的不是孤立问题，而是“当前剧本在这个事件之后如何继续”。用户消息、角色活动检查点、关系变化、外部观察和共处动作先被归一为事件，再生成局部 `ContinuitySnapshot`；模型据此写出自然的角色反应、内心状态和下一步倾向。

```text
event ingress
  -> affected world / relationship / affect / motive projections
  -> bounded story context
  -> RoleplayDraft
  -> structured claims / proposals / ResponsePlan
```

`StorySegment` 只是一段可压缩的叙事投影，必须带 `source_event_refs`、输入 revision、`reality_mode` 和可见性。角色可以在故事中表现出疲惫、分心、想分享或不想回应，但这些表达不能直接创建长期事实；结构化的 `WorldEvent`、关系事件、MemoryProposal 和主动候选分别经过自己的 owner 与 Kernel 校验。

持续剧本按影响范围局部重建：一次用户消息通常只影响当前 session、目标关系、相关活动和少量情绪，不重新生成全天生活。事件没有达到检查点时保留 checkpoint，不开启新的模型调用；达到承诺、完成、纠正、撤回或值得分享的边界时才重新规划。这样保留 HDSI 的时间感和自主性，同时控制上下文、内存和模型成本。

多用户共用剧本使用 `global` 世界投影、`user` 偏好投影和 `session` 对话投影的组合。某用户的事件可以改变角色的公共情绪或关系倾向，但其他用户只能得到当前受众允许的脱敏表达；他们不能读取原始私聊事件或自动获得修改活动的权限。跨会话恢复只恢复有授权的活动 checkpoint，不恢复旧窗口的消息、投递句柄和临时占用态。

### 3.2.1.1 Global 模式下的统一入口

global 连续生活不等于把所有频道放进同一个上下文。所有私聊、群聊和外部事件先进入统一归一化入口，得到同一 `EventInterpretation` 和 `RoleDecisionSnapshot`；频道差异只在 `SceneGate`、受众裁剪和表达姿态中体现：

```text
native event
  -> NormalizedInteractionEvent
  -> GlobalContinuityState + UserRelationshipState + SessionSceneState
  -> AudienceExpressionPolicy
  -> DecisionGate
  -> ResponsePlan / silence
```

群聊的读空气、唤醒和防刷屏属于 `SceneGate`，结果是继续、延后、吸收或沉默，不得直接跳过关系、情绪和人格连续性。私聊和群聊共同读取同一个 persona/global owner；用户关系按 canonical subject 单独读取，群公共信息按群 session 保留。这样同一用户从群聊进入私聊时，不会被当成两个身份；同一人格在群聊表达更克制时，也只是受众姿态变化，不是切换成另一人格。

PromptCompiler 固定使用 `global -> user -> session` 的有界顺序，并在每层标记 visibility、purpose、revision、TTL 和 evidence_refs。私聊正文、专属关系摘要、群成员观察和第三方信息不能跨层自动升级；只有 Memory/World owner 接受带证据的提议后，才生成可被其他场景读取的 user 或 global 投影。宿主原生历史作为一个可去重的贡献源参与编排，不由群聊路径清空并重建第二套历史。

### 3.2.1.2 GlobalActorRuntime：一部手机，多扇窗口

`global` 模式的运行时模型是“一个角色拿着一部手机，同时面对多个聊天窗口”，而不是为每个窗口克隆一个 Bot。核心只存在一个 `GlobalActorRuntime`：

```text
GlobalActorRuntime {
  actor_id / persona_id / global_revision
  continuity_state_ref       # 公共生活、身体/注意力、当前活动和全局情绪投影
  active_window_ref          # 当前注意力，不是人格或状态分区
  pending_window_refs[]      # 有界的待处理窗口引用
}

ConversationWindow {
  window_ref / conversation_ref / session_id
  audience / target_subject_ref
  window_revision / occupancy / last_event_ref
  context_checkpoint_ref     # 冷窗口只保留有期限引用
}
```

窗口切换只更新注意力和读取视图，不创建新的 `actor_id`、`persona_id`、AffectState 或 Memory owner，也不清空上一窗口的状态。窗口 A 的私聊事件、窗口 B 的群消息和角色自己的活动检查点进入同一个有序事件账本；全局状态变更按 `global_revision` 串行提交，窗口上下文继续按各自 `window_revision` 隔离。模型可以并行生成只读草稿，但提交前必须重新检查全局 revision；过期草稿只能重编译或丢弃，不能覆盖较新的窗口。

一次窗口切换的最小流程是：

```text
select window
  -> read latest GlobalActorSnapshot
  -> read this window's SessionSceneState
  -> read authorized UserRelationshipState / audience projection
  -> compile one ResponsePlan
```

切换窗口不会让角色“忘记刚才在另一边做什么”。如果 A 窗口的消息改变了角色公共情绪或当前活动，B 窗口读取的是新的 global revision；如果变化只属于 A 的私聊关系或 session 话题，B 只能看到经过 audience policy 允许的脱敏结果。群聊的公开克制是表达姿态，不是另一种人格。

为控制资源，`GlobalActorRuntime` 只保存有界状态和引用，不保存所有窗口的完整 transcript。活跃窗口保留近期事件，冷窗口转为 checkpoint；待处理窗口按 owner、优先级、TTL 和接触预算合并。全局状态提交失败时沿用上一有效 revision，单个窗口失败不能阻塞其他窗口；没有可安全表达的内容时，统一返回 `silence` 或延后。

### 3.2.1.3 HDSI 试验运行与效果验证

HDSI 试验不是把旧提示词整体替换掉，而是用同一条规范化输入同时驱动旧路径和 HDSI 旁路。旁路拥有独立的连续状态、事件账本和快照版本；旧路径仍是默认生产 owner，直到试验达到退出门槛。

```text
NormalizedInteractionEvent
  -> LegacyRequest -------------------------------> LegacyResponse
  -> HDSI Trial Runtime -> TrialDecisionSnapshot -> TrialResponsePlan
                                      \-----------> TrialObservation
```

试验运行时至少保存以下有界对象：

```text
HDSITrialBinding {
  binding_id / mode / scope_selector / actor_id / persona_id
  binding_revision / generation / effective_from / expires_at
}

HDSITrialInput {
  trial_id / trace_id / normalized_event_ref
  input_digest / clock_id / random_seed
  global_revision / window_revision / policy_versions
}

HDSITrialObservation {
  trial_id / legacy_result_ref / hdsi_result_ref
  decision_diff / privacy_check / continuity_check
  latency_ms / model_calls / prompt_bytes / state_bytes / peak_inflight
  outcome / fallback_reason / created_at
}
```

`mode` 分为四种运行状态：

| 模式 | HDSI 权限 | 用户看到的结果 |
| --- | --- | --- |
| `legacy` | 不运行 | 旧框架回复 |
| `shadow` | 读取同一输入并计算，不写生产状态、不投递 | 旧框架回复；记录可解释差异 |
| `active` | 生成 HDSI `ResponsePlan`，提交前经过统一安全、权限和投递校验 | HDSI 回复；失败时回退同一输入的旧结果 |
| `rollback` | 停止新副作用，保留诊断和旁路状态只读 | 旧框架回复；不删除 HDSI 试验数据 |

`shadow` 必须复用同一时钟、输入版本、策略版本和可复现随机种子；模型调用可以抽样，但抽样率、成本和遗漏样本数要记录。比较不能只看文本相似度，至少包括：

1. 角色连续性：当前活动、未完成事项、情绪方向和人格核心是否一致；
2. 受众边界：私聊专属事实、群成员观察、第三方信息是否泄漏；
3. 决策质量：回答、澄清、分享、沉默、延后和主动候选的意图是否合理；
4. 事实质量：时间、关系、记忆证据、现实观测和模拟内容是否分层；
5. 交互效果：首句延迟、回复完成率、用户追问/纠正、同主题吸收率和无关打扰率；
6. 资源成本：辅助模型调用、输入/输出字节、旁路状态大小、队列长度、峰值在途任务和进程 RSS。

`active` 的回退必须绑定到同一个 `trial_id + input_digest`：HDSI 生成失败、状态 revision 过期、权限/隐私检查失败或投递不确定时，取消未提交的 HDSI 副作用并使用旧路径结果；已经提交的外部投递只保留原回执，不重复发送。回退只影响当前绑定，不暂停其他 actor、窗口或 legacy 用户。

试验状态采用单一绑定和 generation fencing。配置或人格绑定变化后，旧 generation 的 HDSI 草稿不能写回全局状态；窗口可以继续读取上一有效 checkpoint，但必须以新 binding revision 重新编译。冷窗口只保留事件引用和有限摘要，完整 transcript 由历史 owner 管理；试验观察默认保存哈希、指标和最小脱敏片段，避免旁路复制整套聊天记录。

退出门槛分为硬门槛和软指标。任一硬门槛失败立即对受影响绑定执行 `rollback`：跨窗口隐私泄漏、重复外部投递、旧 generation 写回、人格串线、无法解释的权限放宽或资源超出安装预算。软指标至少需要与 legacy 基线相比不降低回复完成率和同主题吸收率，不增加无关打扰率与纠正率，P95 延迟和峰值内存处于预先记录的预算内；未达到时只能调整策略资产或提示词，不扩大 active 范围。

最小验证矩阵覆盖：同一 `global` actor 的私聊/群聊交替、两个用户同时发言、窗口冷却后恢复、两个 persona 隔离、session/user/global 三种共享模式、模型超时、配置切换、进程重启、事件重复/乱序/修订、群聊隐私裁剪以及 Memory/World provider 暂不可用。每个样本同时保存旧结果、HDSI 结果或失败原因、输入/状态 revision、资源指标和人工复核结论；没有这些记录只能算提示词试用，不能算 HDSI 效果验证。

### 3.2.2 角色决策闭环

持续剧本要真正影响行为，必须经过一条可回放的中间链，而不是把“角色现在的心情”直接拼进提示词。每次事件只在受影响的作用域和领域内局部重算：

```text
WorldEvent / UserMessage / CalendarChange / MemoryEvidence / ActionReceipt
  -> EventInterpretation (evidence + uncertainty)
  -> AffectEvent + RelationshipEvent
  -> AffectState + RelationshipProjection
  -> MotiveProjection (what the character wants to do)
  -> DecisionGate (time, occupancy, permission, budget)
  -> ResponsePlan / InteractionOpportunity / silence
  -> DeliveryReceipt / UserFeedback
  -> new feedback events
```

Kernel 保存一次 `RoleDecisionSnapshot` 的输入和输出 revision，供普通回复、主动候选和诊断复用：

```text
RoleDecisionSnapshot {
  decision_id / trace_id / scope
  source_event_refs[]
  world_revision / calendar_revision / affect_revision
  relationship_revision / motive_revision / conversation_revision
  motives[]                    # 最多 3--5 个活跃方向
  blocked_reasons[]            # 日历、占用、权限、预算或证据不足
  expression_posture
  response_intent              # answer / clarify / repair / share / rest / boundary
  candidate_refs[]             # 只引用候选，不携带发送状态
  expires_at
}
```

各层职责固定如下：

1. `EventInterpretation` 只解释证据和不确定性。转述、讽刺、对象不明或过期来源必须降低置信度，不能直接改变关系或情绪。
2. `AffectStateProjector` 与 `RelationshipProjector` 使用同一输入事件但分别提交自己的 revision。重复事件按 `event_id/dedupe_key` 收敛，修订或撤回使旧贡献失效后重算。
3. `MotiveProjector` 将状态、日历边界、当前活动、关系能力、开放事项和用户近期反馈合成为有限方向。动机可以被阻塞，但不能被伪装成已经执行的任务或用户需求。
4. `DecisionGate` 是唯一的行为准入点，检查 `ConversationOccupancy`、`user/persona/global` 受众、授权、安静窗口、接触预算、平台能力和来源有效期。模型不能跳过它创建消息或现实动作。
5. `ResponsePlanner` 根据获准意图生成 `ResponsePlan`；`ExpressionPosture` 只调节距离、温度、直接度、长度、追问和模态，不新增事实、关系承诺或日历提交。
6. `DeliveryReceipt` 和用户反馈才是闭环终点。未发送、超时、取消、吸收和用户纠正分别生成不同反馈事件，不能把模型草稿当作已经发生的互动。

普通回复和主动行为的差异只在入口和准入结果：普通回复已有用户事件和开放线程，优先生成一个 `ResponsePlan`；主动行为从 `MotiveProjection` 生成候选，必须再次通过占用态、预算和投递回执。两者不能各自维护一套情绪、关系或消息状态。

性能边界：同一 `decision_id` 内最多一次语义复核模型调用，常规状态投影使用本地策略；没有新事件或检查点时不重建快照。快照只保存引用、短摘要和 TTL，不保存完整剧本、聊天全文或媒体；候选落选后合并、延后或过期，不进入无限队列。投影失败时沿用上一份有效 revision 和中性姿态，主回复不被情绪链阻塞。

### 3.3 扮演流水线

```text
RoleplayRequest
  -> Scope / permission / revision check
  -> PersonaSnapshot resolve
  -> ContextContribution normalize
  -> IntentPlan validate
  -> Affect + Relationship -> ExpressionPosture
  -> PromptCompiler
  -> ModelGateway
  -> Claim / tool / safety validation
  -> ResponsePlan
```

PromptCompiler 将输入分为几个有明确权威的 lane：

1. `identity`：稳定人格和不可越过的角色边界。
2. `relationship`：当前目标、关系能力、称呼和距离。
3. `world`：当前活动所需的地点、物品、身体状态、习惯倾向和可用 affordance；不注入完整世界数据库。
4. `scene`：时间、地点、活动和当前会话场景。
5. `memory`：经过作用域、隐私和新鲜度检查的记忆投影。
6. `affect`：短期情绪和表达姿态的摘要，不暴露内部分数。
7. `intent`：本轮目的、连续性锚点、可用工具和禁止断言。
8. `safety`：平台、安全、隐私、确认和输出格式约束。

每个片段都带 `source`、`evidence_refs`、`revision`、`max_age`、`priority` 和 `visibility`。编译器负责去重、裁剪、冲突报告和最终冻结；领域函数不能直接修改宿主 `system_prompt`。

### 3.4 扮演输出

模型输出先解析为 `RoleplayDraft`，再由规则和必要的复核模型生成 `ResponsePlan`：

```text
ResponsePlan {
  plan_id
  scope
  visible_parts[]
  expression_posture
  tool_intents[]
  memory_proposals[]
  affect_events[]
  forbidden_claims_check
  delivery_owner
  history_owner
  input_revision
  provider_generation
}
```

`visible_parts` 可以包含文字、媒体引用和平台允许的组件；`tool_intents` 只表示建议动作，实际调用仍需经过权限和投递层。`memory_proposals` 只表示写入建议，Memory Service 负责接受、拒绝、修订或撤回。

扮演失败时依次使用：已有安全缓存、低成本中性姿态、简短事实回复、明确说明暂时不可用。不能用未经证实的“内心戏”、身体状态或共同经历填补失败。

## 4. 主动内核

### 4.1 来源只产生信号

Feature 插件、日历、用户互动和系统生命周期只能产生 `Signal`，不能直接生成面向用户的消息。

```text
Signal {
  signal_id / signal_type / source_id
  scope / target_subject
  occurred_at / observed_at
  valid_from / valid_until
  payload_ref / evidence_refs
  confidence / sensitivity
  dedupe_key / revision / trace_id
}
```

`occurred_at` 是事情发生时间，`observed_at` 是系统看到时间。信号过期后只能用于诊断，不能继续产生新的接触机会。原始健康数据、精确位置、聊天全文和媒体不进入主动队列，只传摘要或资源引用。

### 4.2 动机与候选

主动系统把“为什么想联系”和“具体说什么”分开：

```text
Signal
  -> MotiveProjection
  -> CandidateProposal[]
  -> CandidateSet normalize / dedupe / merge
  -> PolicyArbiter
  -> InteractionOpportunity
```

`MotiveProjection` 表示 `care`、`repair`、`connect`、`assist`、`share`、`rest`、`boundary` 等有限方向，带目标、驱动力、持续性、可行性、侵入成本、证据和有效期。它不是待发送文本。

主动偏好不再压缩成一个“主动等级”或每日次数。Kernel 维护按作用域版本化的 `ProactivePreference` 投影，字段是可扩展的自然语言偏好结果，枚举仅用于校验和统计：

```text
ProactivePreference {
  scope / revision / source_refs
  contact_style                 # 接触风格，不等于频率
  daily_budget / topic_cooldown # 用户可调整的软预算与同主题成本
  type_preferences{}            # 关系、生活、工具等类型的偏好
  time_windows[] / quiet_windows[]
  interrupt_policy              # 当前占用下是否允许打断
  nonresponse_policy{}          # 按意图类型的未回应处理
  late_delivery_policy{}        # 错过时机后的合并、延后、放弃
  format_preferences[]          # 独立消息、合并、下次提起、摘要、仅记录
  data_grants[] / action_grants[]
  feedback_rules[] / expires_at
}
```

模型可以从自然语言反馈提出 `PreferenceMutation`，但不能直接修改偏好、提高预算或绕过有效期。Runtime 负责作用域、冲突、授权和 TTL；同一用户对关系关心、Bot 生活、工具提醒可以有不同的未回应和投递形式。偏好缺失时采用低压力的上下文推断，并把这次选择作为可解释的候选依据，而不是写入新的固定规则。

`CandidateProposal` 至少包含：

```text
candidate_id / source_signal_ids / motive_ids
purpose / value_class / expected_response
preferred_window / expires_at
merge_group / semantic_key / supersedes_key
preferred_modality / interruption_cost
evidence_refs / policy_versions / resource_budget
```

同一信号可以产生多个候选，但一个 `CandidateSet` 在一个接触窗口内最多产生一个机会。未胜出的候选可以合并、延后、抑制或过期，不能各自排队等待发送。

### 4.3 机会准入和预算

`PolicyArbiter` 是主动准入和排序的唯一责任点，执行顺序固定：

1. 作用域、权限、事实有效期和人格 revision。
2. 安静时段、当前活动、平台能力和明确免打扰。
3. 主动类型的承诺、时效和确认要求。
4. `ContactBudget` 的每日额度、最小间隔、类型配额和未回应减速。
5. 相关性、连续性、新颖性、延迟损失和打扰成本的软评分。
6. 形式选择、扮演生成和投递计划。

候选预算与接触预算分开：前者限制比较多少方案，后者限制用户实际收到多少接触。情绪、关系和模型都不能突破用户预算。没有合适机会时，`silence` 是成功结果。

主动车道建议固定为：

| 车道 | 内容 | 默认行为 |
| --- | --- | --- |
| `alert` | 安全、用户明确承诺和高时效事件 | 可按授权抢占低车道 |
| `normal` | 跟进、关系关心、目标进度和外部内容 | 受时间段和接触负担控制 |
| `ambient` | 早安、角色生活分享、轻量邀请 | 默认可合并、延后或安静结束 |

### 4.4 主动调度

所有主动来源由 `TaskSupervisor` 统一管理。Feature 插件不创建自己的永久轮询器，也不直接维护发送定时器。

- `push`：外部事件发生时上报信号。
- `pull`：Kernel 在时间段或机会评估时按需查询。
- `derived`：Kernel 从已有事件和状态推导，不伪装成外部事实。

调度器按“事件唤醒 + 有界 tick”工作：事件到达时立即评估高时效类型；普通和环境类型按时间段批量评估；没有信号时只允许低成本的角色内部 `ThoughtSeed`，且必须受候选预算、接触预算和 TTL 限制。

主动内核不在零点、启动或每次刷新时生成全天活动大纲。它只请求当前段、下一转折和受事件影响的局部窗口；全天视图如果被用户要求，只能从事实和倾向现场渲染，不能把未确认条目变成经历、触发器或媒体任务。旧 `daily_plan` 在迁移期只能作为兼容读取快照。

#### 4.4.1 对话占用态闸门

日历的 `availability` 只说明用户可能接受联系，不能作为当前插话许可。Kernel 另外维护按会话和目标隔离的 `ConversationOccupancy`：`turn_open`（本轮尚未完成）、`engaged_thread`（连续双向交流）、`closing`（收束宽限）、`idle`、`quiet` 和 `unknown`。主动机会在生成、批准和提交前都读取它，并携带 `conversation_revision`。

在 `turn_open` 中，普通候选只合并到当前回复或暂存；在 `engaged_thread` 中，同主题候选可以合并，无关的角色生活分享、邀请和媒体延后到收束或空闲；`quiet` 抑制普通候选但不影响用户主动请求。安全事件、用户明确请求和已授权的高时效承诺按独立权限和证据策略处理。角色内部的吃饭、散步或拍照事件只更新 `persona_internal`，不会因为图片已经生成就旁路投递。

天气信号也只影响受影响的时间窗：它可以让一段户外活动产生室内替代或延后候选，但不能生成整天大纲、创建现实承诺或覆盖 `daily_plan`。模型只接收局部环境摘要和候选窗口，不能把天气预测扩写成角色已经安排好的一整天。

### 4.5 主动生成和投递

获准的 `InteractionOpportunity` 转换为 `IntentPlan`，再调用同一个 `RoleplayEngine`：

```text
InteractionOpportunity
  -> IntentPlan
  -> RoleplayRequest(interaction_mode=proactive_contact)
  -> ResponsePlan
  -> DeliveryPlan
  -> DeliveryGateway
  -> DeliveryReceipt
```

`IntentPlan` 至少包含 `purpose`、`emotional_stance`、`continuity_anchor`、`evidence_refs`、`forbidden_claims`、`preferred_modality` 和 `conversation_load`。主动模型不能凭空增加事实、紧急程度或关系承诺。

### 4.6 主动机会生命周期

```text
created
  -> eligible
  -> approved
  -> generating
  -> delivery_pending
  -> submitted
  -> accepted / delivered / partial / uncertain / failed

eligible -> merged / deferred / suppressed / expired
```

`InteractionLedger` 记录自然互动、主动接触、用户回应、吸收、取消、忽略、接触负担和外部回执。用户新消息优先吸收同主题机会；人格切换、权限撤销和 generation 变化使尚未提交的机会失效；已经提交的动作进入对账，不能假装被取消。

## 5. 两条路径共用的响应主链

新版不分别维护“普通回复发送器”和“主动发送器”。两者都必须产生 `ResponsePlan`，并经过相同的 review、delivery、history 和 feedback 阶段。

```text
ingest
  -> scope
  -> context
  -> intent
  -> roleplay
  -> tool / media plan
  -> review
  -> delivery
  -> history
  -> memory / affect feedback
```

每一阶段声明：输入 revision、输出 DTO、超时、取消行为、资源预算、失败回退和所有者。阶段之间只传递不可变 DTO，不能通过共享对象隐式改变后续决策。

### 5.1 被动回复

被动回复由宿主事件进入 `ConversationTurn`：

1. `ScopeResolver` 确认 Bot、人格、主体和会话。
2. `ConversationOccupancy` 锁定本轮的 `active_thread_id`、主题指纹和 revision，并吸收同主题候选。
3. `ContextCollector` 并行读取记忆、场景、关系和可选 Feature 上下文。
4. `IntentResolver` 判断回答、工具、澄清、安全或自然结束。
5. `RoleplayEngine` 生成 `ResponsePlan`，提示词明确禁止夹带未请求的无关生活更新和媒体。
6. `DeliveryGateway` 由宿主或核心按唯一 `delivery_owner` 发送。
7. 回执产生记忆建议、情绪事件和主动反馈；延后候选在对话收束后重新评估，不会在同一因果链中自动重复触达。

### 5.2 主动接触

主动接触以 `Signal` 或用户明确指令开始：

1. `SignalNormalizer` 校验证据、作用域、新鲜度和去重。
2. `MotiveProjector` 得到有限的动机投影。
3. `OpportunityAggregator` 合并不同来源的候选。
4. `ConversationOccupancy` 判断当前线程是否占用接触，并吸收同主题机会或将无关机会标记 `deferred(conversation_busy)`。
5. `PolicyArbiter` 处理硬门槛、接触预算、打扰成本和软评分。
6. `RoleplayEngine` 以主动模式生成同人格响应；媒体仅在机会获准且与当前线程相容时生成。
7. `DeliveryGateway` 在提交前再次校验 conversation revision，发送并等待平台回执。
8. `InteractionLedger` 记录结果，用户后续消息负责吸收、回应或取消相关机会。

## 6. 长期数据所有权

持久化对象按照稳定归属保存，不把临时会话或绑定 revision 拼进长期实体主键：

```text
installation_id
  + bot_id
  + persona_id
  + target_namespace / target_id
  + feature_namespace
```

| 对象 | 权威所有者 | Kernel 保存的内容 |
| --- | --- | --- |
| 人格源设定 | AstrBot Persona Manager | 有版本的 `PersonaSnapshot` 引用和 companion overlay |
| 关系事实 | Private Companion | 关系投影、授权和 revision |
| 长期记忆 | Memory Service | 查询投影、引用、撤回游标 |
| 作品/章节 | Content Service | 作品引用和当前版本摘要 |
| 媒体资源 | Image Service | `MediaAssetRef`、用途和过期时间 |
| 主动信号 | 信号生产方 | 可重放信号或摘要引用 |
| 动机/机会 | Proactive Engine | 生命周期、证据、策略版本和预算结算 |
| 消息历史 | 宿主或唯一历史所有者 | `DeliveryReceipt` 和受理消息 ID |

`session_id` 用于当前请求、缓存和取消；它不能决定人格生活、关系、记忆或作品的长期归属。

## 7. 重建顺序

以下 A--F 是角色与主动子系统的局部工作包；其开工顺序服从[总纲](./FRAMEWORK_DESIGN.md)的契约、记忆、主动与后续领域阶段，不另设全局路线。

长期重建按垂直能力推进，而不是按旧文件搬运：

### A. 契约和回放基线

冻结 `RuntimeScope`、`PersonaSnapshot`、`ContextContribution`、`RoleplayRequest`、`ResponsePlan`、`Signal`、`InteractionOpportunity`、`DeliveryPlan` 和 `DeliveryReceipt`。用旧系统录制的输入建立脱敏回放集，只验证行为差异，不把旧字段变成新 API。

### B. 被动扮演切片

先只支持一个私聊文本回复：作用域解析、人格快照、关系投影、最小上下文、PromptCompiler、RoleplayEngine 和宿主投递。完成多人格、多会话、人格切换、模型超时和敏感输出的验证后，再加入记忆、媒体和工具。

### C. 低风险主动切片

复用同一个 RoleplayEngine，先实现一个环境无关、无外部副作用的主动类型，例如“用户明确预约的跟进”或“被当前回复吸收的延续”。验证候选取消、接触预算、标准 Agent Pipeline、分段回执和用户新消息吸收。

首轮运行采用 `shadow` 模式：旧策略和新 `RoleDecisionSnapshot` 并行计算，新路径只产生 `ProactivePreview` 与 `ShadowRun`，不创建 `DeliveryPlan`、不写记忆、不提交日历、不执行设备或媒体动作。比较项至少包括候选数量、排序变化、阻塞原因、建议窗口、首句延迟、语义模型调用、快照字节和峰值在途预览数；差异必须能追溯到来源事件、策略版本和决策 revision。

影子运行的退出门槛是行为门槛而非“新候选更多”：同主题候选吸收率不下降、无关打扰候选不增加、硬承诺冲突全部被阻塞、纠正后旧候选失效、模型不可用时主链仍能稳定回复，且资源指标在安装/Bot/人格/会话预算内。未达到门槛时只调整 `PolicyAsset` 或提示词约束，不开放真实投递；达到门槛后仍按行为类型逐步灰度，保留旧策略只读回退和可审计快照。

### D. 信号和机会扩展

按 `calendar -> user_interaction -> memory_relationship -> goal_project -> extension_content -> external_observation` 的顺序接入来源。每接入一种来源，都必须提供信号 schema、证据要求、失效策略和回放样例。

### E. 外部动作和多模态

在文本主动链稳定后再接 Image、Reality、Together、Game 和 Live。媒体、设备、房间和外设均以 `ActionRequest` 或 `MediaAssetRef` 进入 DeliveryGateway，不在主动评分阶段执行副作用。

### F. 退役兼容层

当新路径已经覆盖一类行为并通过回放、真实平台、热重载和恢复演练后，删除该类旧入口。迁移桥只保留到对应领域切片完成，不能成为永久的第二套调度器。

## 8. 设计验收

每个新切片至少回答这些问题：

1. 同一人格跨会话是否连续，不同人格和 Bot 是否绝不串线？
2. 角色设定、关系事实、情绪状态、动机和模型推测能否在回放中分辨？
3. 被动回复和主动消息是否使用同一个扮演与投递主链？
4. 新消息能否吸收或取消相关主动机会，而不误伤无关承诺？
5. 连续聊天期间角色内部产生的活动或图片，是否只形成延后候选而不会无关插话？
6. `PromptSnapshot` 过期后，`PersonaContinuityState` 是否仍能恢复未完成事项和已验证偏好？
7. 长活动中断后能否按进度恢复，物品、衣物、路线和习惯是否保持世界状态一致？
8. 发送未知、分段部分成功、热重载和 Provider 变化能否恢复而不重复触达？
9. 缺少 Memory、Image 或外部信号插件时，核心是否只降级对应能力？
10. 每个拒绝、延后、静默、失败和降级是否都有证据、策略版本和可诊断原因？

在这些条件满足前，旧实现只能作为兼容行为基线，不能反向决定新版的领域边界。
