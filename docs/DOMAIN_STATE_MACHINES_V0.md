# 记忆、世界模拟、现实触及与实时共处领域状态机 v0

> 导航：[设计总纲](./FRAMEWORK_DESIGN.md) / [主题目录](./FRAMEWORK_DESIGN_INDEX.md)。定位：领域设计草案；维护现实/共处与组合规则，§7--§11 细化世界模拟首条切片。2026-09-08 更新，世界运行验收全部 `not_run`；记忆转移仍由记忆切片状态机详述。

本文承接[陪伴状态、事件与反馈闭环设计](./COMPANION_STATE_EVENT_FEEDBACK_DESIGN.md)，将记忆、世界模拟、现实触及和实时共处组织为可组合的状态机。状态机只约束生命周期和安全不变量，不规定 AI 必须使用的词汇或表达方式。

## 1. 记忆状态机

```text
提议：received → validated → accepted → persisted
      pending / quarantined 经重验后再提交；可 skipped / rejected / cancelled
事实：active → superseded / invalidated / expired
```

| 对象 / 状态 | 含义 | 可执行动作 |
| --- | --- | --- |
| 提议 `received / validated / accepted` | 收到、通过结构校验、被 Writer 接纳 | 继续校验授权、证据、版本与提交条件 |
| 提议 `pending / quarantined` | 等待边界或审查 | pending 可按授权用于当前会话；均不当作长期事实 |
| 提议 `persisted / skipped` | 已提交事实操作 / no_op | 查回执；不能用 skipped 声称记住了新事实 |
| 提议 `rejected / cancelled` | 拒绝或提交前取消 | 保留必要原因，不生成事实 |
| 事实 `active` | 当前有效记忆 | 可按用途召回和注入 |
| 事实 `superseded` | 被纠正后的新版本替代 | 仅在授权的历史/冲突解释中使用 |
| 事实 `invalidated` | 撤回或 owner 确认失效 | 传播至索引、摘要、投影和缓存 |
| 事实 `expired` | 超过有效期或保留期 | 不得进入当前召回 |

本节是领域组合摘要，详细状态以[记忆切片状态机](./MEMORY_VERTICAL_SLICE_STATE_MACHINE_V0.md)为准。早期 proposed/collected 是收集日志，不是新的外部枚举。权限撤销必须阻止输出并失效相关投影，不等于所有主体的事实都已撤回。

关键转移：

- `active → superseded` 必须与新版本在同一事务中完成；
- `active → invalidated/expired` 必须增加存储 revision，使旧召回缓存失效；
- `pending` 可以注入当前会话的 `ContextContribution`，但不能提升为 `active`；
- 对声明为单值的稳定事实键不能存在两个当前 `active` 版本；多值偏好和不同经历可以并存；
- 摘要失败只保留队列和原始证据，不把事件标成已总结。

召回分两步：先按作用域、用途、时间和权限产生候选，再为当前问题选择完整答案证据。缓存键至少包含 scope、query、用途、policy revision、memory revision 和摘要边界。

## 2. 现实触及状态机

```text
observed → verified → available → stale
              │           ├→ action_proposed → authorized
              └→ rejected │                      ├→ executing
                          └→ revoked              ├→ succeeded
                                                   ├→ failed
                                                   └→ unknown
```

- `observed`：设备或网关上报原始观测；
- `verified`：完成来源、作用域、时间和脱敏校验；
- `available`：在 TTL 内可供 AI 使用；
- `stale`：过期，只能作为历史背景；
- `action_proposed`：AI 认为可能需要动作；
- `authorized`：获得所需 capability、consent 和风险确认；
- `executing/succeeded/failed/unknown`：由适配器和 `OperationResult` 确认。

观测不能直接变成记忆或动作。动作未知时不可重试造成重复副作用，必须先查询、等待平台确认或进入人工处理队列。授权撤销会使未执行动作失效，已执行动作保留真实回执。

## 3. 实时共处状态机

```text
requested → invited → joined → active
                         │        ├→ paused → active
                         │        ├→ reconnecting → active
                         │        ├→ ending → ended
                         │        └→ failed
                         └→ declined / expired
```

`SessionRecord` 至少保存参与者、人格快照 revision、能力票据、媒体进度、临时上下文、generation 和终止原因。

- `requested/invited` 只表示协商，不代表参与者已经加入；
- `active` 期间的音频、视频、屏幕和位置默认限于会话 scope；
- `paused` 保留进度但停止实时动作；
- `reconnecting` 期间旧代任务不得继续发送或写回；
- `ending` 负责收束、释放票据和生成可选经历摘要；
- `ended` 后只有经授权的摘要/记忆提案可以继续处理。

## 4. 三个状态机的组合规则

```text
Reality Observation
  → Scene/Affect update
  → MotivePlan
  → Proactive candidate

Together Session
  → temporary ContextContribution
  → activity events
  → ending summary proposal
  → Memory Writer decision
```

1. 现实观测影响场景和动机，但不绕过 Memory Writer；
2. 共处会话可以消费记忆和现实观测，但不能扩大两者的权限范围；
3. 记忆纠正或撤回必须通知仍在运行的 Session 和主动候选；
4. 会话结束、观测过期或人格 revision 变化时，相关候选重新评估；
5. 任一领域缺失时，其状态为 `unavailable/degraded`，其他领域继续运行，不伪造缺失结果。

## 5. 设计验收场景

- 用户说“我已经搬到 B”：旧地址进入 `superseded`，所有当前召回、现实动作和主动候选使用 B。
- 设备上报“刚回家”但 TTL 已过：观测进入 `stale`，不得继续触发欢迎动作。
- 共处中途断线：Session 进入 `reconnecting`，旧 generation 的音频和摘要不能重复写入。
- 用户撤回一条活动记忆：Memory 失效传播到摘要、召回缓存和仍在运行的会话投影。
- 设备动作超时：结果为 `unknown`，系统不直接重发，直到获得查询或确认结果。

## 6. 世界模拟与记忆的组合边界

世界模拟作为 `PersonaWorldModel` 的能力提供方，可以独立成插件，也可以由陪伴核心托管；它不建立第二个长期记忆库。世界 owner 保存人格内部的实体、关系、`ActivityProcess`、`EmbodimentState` 和 checkpoint；Memory owner 保存带证据、可治理、可跨时间召回的事实；现实触及 owner 保存外部观测和动作回执。

两者通过统一 DTO 组合：世界侧发布带 `world_event_id`、`world_owner_ref`、`world_revision`、`reality_mode` 和 `source_refs` 的 `WorldEvent`，需要长期保存时转成标准 `MemoryProposal`；Memory 通过 `MemoryQuery` 向世界侧提供按用途裁剪的事实/经历投影。`world_revision`、`process_revision`、`memory_revision` 和 `policy_revision` 分开维护，不能互相冒充。

内部模拟活动、每次进度 tick、场景描写和模型猜测只更新世界 checkpoint；用户确认、活动完成/中断、会话结束摘要和明确纠正才进入 Memory 提议。`simulated`、`derived` 和 `observed` 必须保留来源差异，角色模拟不能证明用户现实活动。Memory 的纠正/撤回可以使世界投影和主动候选失效，但不直接结束现实设备动作。

世界模拟读取记忆必须经过作用域、purpose、ACL、revision 和预算；不得把完整世界模型或记忆库注入 Prompt。世界事件与 `memory.changed` 各自保留 event_id，通过 `caused_by` 和 revision 去重，禁止事件回调互相无限写入。具体字段、事务边界和 WMS-01--08 场景见[记忆状态机 §6.10](./MEMORY_VERTICAL_SLICE_STATE_MACHINE_V0.md#610-与世界模拟能力的组合边界)。

## 7. 世界模拟纵向切片：学习被打断与跨会话恢复

本切片从用户体验反推最小领域边界：角色正在学习，用户发来消息，角色能暂时放下手上的事；换一个窗口后仍知道自己的学习进度，但不把旧窗口的私密话题带过去；偏好被纠正后，后续安排和表达随之调整。学习的内容、回应方式和是否继续由 AI 结合上下文判断，框架维护真实来源、可恢复状态与执行边界。

### 7.1 场景归属

采用一个显式授权的混合配置演示三种模式：角色的公共书房和自主学习过程属于 `global`，用户允许跨聊天使用的阅读偏好属于 `user`，本轮话题、打断原因和对话占用属于 `session`。这是本切片配置，不是所有部署必须采用的默认值。整套体系选择 session 或 user 预设时，领域策略可将相应活动绑定到该模式，执行同一状态机。

| 状态 | 归属和权威 owner | 换会话后的行为 |
| --- | --- | --- |
| 书房、公共书架等角色内部资源 | 配置的 global 分区，World owner | 获授权的入口读取同一版本；不能据此声称用户现实房间有这些物品 |
| 角色自主学习过程 P | 创建时固定的 global anchor，World owner | checkpoint 延续；不为每个窗口复制一个 P |
| 针对用户的共读过程 | 依授权绑定 user 或 session，领域 owner | 不因书籍来自公共书架就变成公共活动 |
| 用户阅读偏好 | user anchor，Memory owner | 同平台用户私聊转群聊时可查询；只有当前受众可见的内容进入 Prompt 和输出 |
| 临时困扰、群话题、谁打断了角色 | session/source 权限分区 | 新 session 不继承；不会附带到公共学习状态 |
| 关系投影和主动候选 | 对应主体与目标会话，陪伴领域 owner | 新目标重新取上下文和授权；公共世界事件不广播给所有用户 |

模式不表示任意读者有修改权。其他用户可以看到获授权的公共进度，但暂停、恢复、取消要核验世界控制授权；两个用户同时发消息时，共享进程只有一个权威状态，各会话回复仍独立。公共对象和对外事件不携带私聊正文、用户标识或可遍历的私有证据引用；owner 在源权限分区持有受限因果凭据，公开视图只保留允许公开的角色活动变化。

session 模式的 P 只可在原实际 session 的合法恢复中延续。新建 session 不借“恢复”读取旧 P；如需跨 session 延续，创建时选择 user/global 归属，或另走有证据、有授权的数据迁移。改共享预设不能改名 anchor 后复用旧 checkpoint。

### 7.2 最小对象与协议接法

下表是领域字段决策，尚未发布新的 world wire Schema。复用[架构基线 §3.7](./ARCHITECTURE_RESET.md)已有 ActivityProcess，不在公共 DTO 中增加一套任务状态。共享字段按[共享契约包](./contracts/sharing/v1/README.md)放置；已有世界 patch 夹具只验证 phase/progress 的演示形状，不代表下表已机器化。

| 对象 | 本切片必须保存或返回 | 责任 |
| --- | --- | --- |
| `ActivityProcess` | `process_id`、World owner、共享绑定、`process_revision`、`phase`、progress、reality_mode、来源、开始时间、累计有效时长、上次推进时间、next checkpoint、resume policy、checkpoint_ref | World owner 持久化进度；不持有平台 event、网络连接或整段对话 |
| 恢复 checkpoint | process/owner/anchor、格式版本、过程 revision、阶段与累计有效时长、有限资源引用、依赖 revision、下一边界、算法版本；使用随机推进时含可恢复随机状态 | 可序列化的数据；不保存旧 generation、绑定句柄、原生定时器或发送授权 |
| 依赖投影 | Memory owner/atom/ref 与 revision、用途、受众指纹、共享策略版本、有效期、源权限和消费对象引用 | World/Planner 只持有有限投影；不复制 Memory 事实库，不展开到公共分区 |
| `AffordanceSnapshot` | 当前活动、必要实体/限制、来源及 reality_mode、各依赖水位、有效期、截断/降级标志 | 按当前用途和受众裁剪；只读查询不隐式提交新的活动阶段 |
| `WorldEvent` / `ActivityEpisode` | 各自稳定 ID、process/owner 引用、world/process revision、来源、reality_mode、有效时间、因果和可见性 | 已提交变化和活动边界的输出；投递成功、真实学习成果和 Memory 已提交需各自的证据 |

`world.model.query` 返回有界只读快照；`world.state.transition` 接受标准 StateTransitionProposal 加已注册领域 patch；`world.activity.advance` 是会改变 checkpoint 的 local 操作，需幂等与版本校验。`world.event.publish` 只允许发布或重发 owner 已提交的事件，重发保留 event_id；不能让外部扩展用 publish 伪造一次世界提交。各能力的精确请求/回执 Schema 留作本切片下一份机器产物。

`process_revision` 是单个活动的竞争版本，`world_revision` 是世界 owner 的变更水位。针对 P 的 StateTransitionProposal 将 `target_state=P`、`expected_revision=当前 process_revision`；涉及资源时再校验 patch 声明的实体 revision，不要求所有无关活动抢同一个 world revision。无法在一个 owner 事务内提交的跨资源变更按公共操作回执拆分，不虚构跨 owner 原子事务。

`ActivityProcess` 不是 `TaskEnvelope` 或 `SessionRecord`：前者表示领域活动，任务表示一次推进/持久执行的调度，会话表示连接与交互生命周期。控制面的 `session.resume` 恢复原领域会话，不能用它将新聊天 session 冒充旧 session，或直接将学习过程置为 running。

## 8. 暂停、推进和恢复的决策

沿用共享 patch 夹具中的 `running / paused / completed`，补充领域草案中的 `cancelled / failed` 终态；这里没有把后二者写入现有夹具 Schema。running 是活动过程状态，不与 Session 的 active 混用。

```text
创建提议 --owner 提交--> running --暂停提交--> paused
                          ^                   |
                          +----恢复提交-------+
running --完成边界提交--> completed
running / paused --取消提交--> cancelled
running / paused --owner 确认不可恢复--> failed
```

提议尚未提交不构成活动状态。`unavailable` 是能力状态；响应未知、依赖过期或冲突是操作/校验结果，不应直接把过程改成 failed。blocked 原因可放在受限 checkpoint/投影状态中，不将临时服务故障当作活动永久失败。

| 触发 | 领域处理 | 可观察的结果 |
| --- | --- | --- |
| 用户在学习中发来消息 | AI 根据 interruptibility 与语义选择暂停或保持背景；Kernel 优先安排当前回复，World owner 校验提议 | 暂停成功后才将状态作为“已暂停”注入；失败时仍可正常回应，不虚构提交 |
| 暂停 | 先按已有推进策略计入暂停前的有效时长，提交 checkpoint/新 process revision 和边界事件 | 暂停期间 progress 不随墙钟增长；重复请求返回原效果 |
| 恢复 | 新运行绑定核验 owner/anchor、最新 checkpoint、资源、依赖版本、授权和恢复条件 | 只有一次 CAS 提交成功；新 session 重建自己的上下文，旧投递目标不恢复 |
| 定时边界或对话触发推进 | 从上次提交水位按有效时长计算；按预算跳到必要边界，保存一次有界变化 | 不为离线的每分钟补 LLM 调用或逐 tick 事件；重复唤醒无重复效果 |
| Memory 纠正、撤回或撤权 | 先失效受影响投影/候选；若活动确实依赖失效内容，再按当前授权提出暂停或重规划 | 不撤销已发生的角色经历，不重发或自动撤销现实动作 |
| 完成 | 同一 owner 提交终态、最终 checkpoint、稳定 Episode 引用和待派发事件 | 终态不能恢复成 running；再做一次学习需新 process_id |

进程内耗时可用单调时钟计算；持久化使用明确 UTC 时间、累计有效时长和时间策略。重启后 paused 不补进度；running 仅在声明允许离线推进时计算有界增量。时钟回退、跨度超限或条件无法确认时不倒退 revision，也不推断已经完成，转为待重验。随机推进复用 checkpoint 中的算法版本和随机状态，同一操作重试不重新抽样。

一次提交将 checkpoint、process revision、必要资源变化、幂等结果引用和 outbox 纳入 World owner 的本地事务边界。响应丢失沿原 owner/能力/幂等键查账；不会换进程 ID 或改 anchor 重做。事件重投使用原 event_id，Memory 消费方独立幂等；跨 World 与 Memory 没有分布式原子提交保证。完成摘要失败可以重试同一 Episode 的提议，不能把学习终态回滚为 running。

多个暂停来源分别持有有期限的受限原因引用，恢复前检查仍生效的阻碍。一个会话结束只释放它的引用，不能覆盖另一个会话或承诺的暂停条件。引用集合按 owner 聚合有界；达到额度时合并或延后新任务，不建无限暂停栈。读取 global 进度不会自动获取这些来源详情。

“角色模拟学习已完成”只证明模拟活动走到了完成边界。声称掌握真实知识、读完外部材料或完成工具产物，需要相应来源或产物回执；时长和模型旁白不能代替结果。自然表达通过 Prompt 的来源提示和自检约束，不用固定台词或关键词扫描判断真伪。

## 9. 一条完整的设计轨迹

以下版本号是走查示意，所有步骤 `run_status=not_run`，没有伪造运行回执。学习进度只属于角色的模拟世界。

| 步骤 | 输入与状态变化 | 记忆、上下文和输出 |
| --- | --- | --- |
| 1. 创建 | 角色按已授权生活策略在公共书房开始 P，global anchor G，process revision 1，running | 公共资源来自 G；用户偏好通过 user anchor U 单独查询，私有投影不写进 G |
| 2. 被打断 | 用户在私聊 session A 发来消息；暂停操作以 P 的当前 revision 提交，保存 40% 进度 | A 的占用态优先；打断原因留在 A 的源权限分区，公共视图只说明角色暂停了学习 |
| 3. 转到群聊 | 同一平台、账号和用户进入新 session B | B 的 session anchor 与 A 不同；可读取 U 中获当前群受众授权的阅读偏好与 G 的公共进度，不能读取 A 的临时困扰 |
| 4. 另一成员出现 | 群内另一用户也查询公共书房 | 仍读 G，但使用自己的 user anchor；不能继承 U 的偏好或取得 P 的控制授权 |
| 5. 跨会话恢复 | A 的暂停引用已结束，其他阻碍已清除；World owner 通过当前授权恢复 P | 延续 40% checkpoint；B 不继承 A 的对话状态、发送票据或私密证据 |
| 6. 偏好纠正 | 用户将原偏好纠正为新偏好，Memory Writer 提交 atom 新 revision | U 的旧投影和依赖候选失效；重取授权证据后才重新安排面向该用户的内容，不改写公共书房设定 |
| 7. 撤回与竞态 | 用户撤回相关事实，或在候选生成后改变共享策略 | 未发送结果在输出边界重验，失败则丢弃/重编译；已经提交的外部效果仍按原回执查账 |
| 8. 活动完成 | P 终态提交一次，形成 Episode E | AI 可提出带 simulated 来源的 MemoryProposal；Writer 回执未 persisted 前不声称长期记忆已写入。分享只是候选，不广播 |
| 9. 更换宿主 | 将获授权 checkpoint/逻辑映射导入独立应用或其他 Bot 宿主 | 保留 P、G、有效 revision 和来源，重签运行绑定；无映射的用户状态不可见，旧句柄和发送目标不能复用 |

记忆依赖的失效不能只等待异步通知。消费方还须在推进提交、Prompt 编译和输出前校验当前依赖状态、授权及水位；订阅落后且无法确认时省略相关内容或延后操作。来源权限与共享策略分开保存，向群聊脱敏后的投影也不能缓存成面向所有受众的版本。

## 10. 资源、扩展与迁移约束

世界能力可以由原生或第三方提供，声明相同输入/输出 Schema、patch 类型、owner、算法版本、权限和资源需求。新增活动类型放在命名空间 payload 中，复用上述生命周期；未知必需类型拒绝或降级为只读，不导入扩展 Python 对象作为 checkpoint。独立服务、宿主插件和嵌入应用使用同一逻辑记录，通过装配替换时钟、调度、存储和模型端口。

| 资源 | 有界策略 | 后续测量 |
| --- | --- | --- |
| 非活跃角色/进程 | checkpoint 留在 owner 存储；仅加载活跃工作集，以存储索引分页取得将到期活动 | 活跃/休眠进程数、加载字节、冷启动延迟；不能把全量进程常驻 |
| 事件与调度 | 单个 owner 的到期索引和共享有界队列；同 process 的重复唤醒合并 | 队列深度、最老等待时间、重复事件折叠率、取消后的残留 |
| 上下文查询 | 实体数量、边/关系、源读取页大小、UTF-8 字节、模型 token 及总子查询额度共同限制 | 取数前预算、反序列化/转换临时对象、最终 Prompt 字节；禁止全量读取后截断 |
| 公共缓存 | 同 revision 的不可变公共资源可共享；用户/会话投影按 anchor、purpose、audience、策略/权限/依赖 revision 分区 | 总缓存字节、命中率、淘汰；缓存不能包含可变用户上下文 |
| 依赖和失效 | 反向依赖索引放 owner 存储，分页失效，水位先阻止旧投影输出 | 撤回至停止输出的延迟、失效积压、游标恢复，不全量扫描内存对象图 |
| 历史与摘要 | checkpoint 折叠高频过程；边界 Episode、幂等与 outbox 分别按治理/对账保留策略回收 | 存储增长、重同步量、最长保留；未确认 outbox 不能先删后标成功 |
| LLM 与媒体 | 复用本轮意图结果，普通推进可零模型调用；媒体只传引用，按需生成 | 模型调用/费用、媒体驻留、首响应耗时，不每角色每分钟开循环 |

工作集预算满足 `公共驻留 + 各 owner 活跃状态 + 各受众投影缓存 + 在途取数/解码/序列化 + 队列与恢复暂存 <= Runtime 分配的世界能力总预算`，该预算还受整套框架总上限约束。子插件、多个模式和多个恢复任务不能各拿一份总额度。字节计数要与进程 RSS/可用内存观测对照，不能用 JSON 输出长度证明堆内存峰值。

预算不足时优先淘汰可重建投影、折叠重复事件、降低非必要推进频率和暂停新后台工作；保留当前回复及失效/撤权处理所需额度。不通过扩大共享范围、跳过来源校验或丢弃未对账效果降载。具体默认数值待目标部署测量后确定，本轮只有可执行的测量要求，没有性能通过结论。

PortableSnapshot 只包含已授权逻辑状态、版本化 checkpoint、必要依赖引用及墓碑，不含运行租约、凭据、宿主事件或网络连接。公共 G 在另一入口仍需显式装配和授权；U 需要平台身份映射，原始 ID 相同不代表同一用户；session 状态仅随原会话的显式恢复迁移，不注入任意新 session。

## 11. 世界切片验收与设计退出条件

以下 WS-01--WS-12 是本切片的新设计验收条目，与已有 WMS（记忆联动）和 SHR（共享模式）互相引用，不替代其编号。全部 `run_status=not_run`、`actual=null`；共享 JSON 校验器不会执行此表。

| case_id | 输入/故障 | 运行时断言与所需证据 | 关联 |
| --- | --- | --- | --- |
| WS-01 | 学习中收到消息，暂停请求重复到达 | 优先处理回复；同键只提交一次 checkpoint/revision，暂停期间进度不增长；保留操作记录 | WMS-03 |
| WS-02 | 同一用户从私聊 A 进入新群聊 B | U 相同、session anchor 不同；群授权偏好可用，A 的私密内容不进入 B 的 Prompt；核对实际上下文与访问记录 | SHR-01/02/06 |
| WS-03 | 另一成员或另一平台使用相同原始 ID | U 不被合并；有授权的公共 G 仍可读；记录可信身份/anchor 解析结果 | SHR-03/04/05 |
| WS-04 | 两个窗口同时暂停/恢复 P | 只有当前 process revision 的有效提交成功；一个来源释放不覆盖其他阻碍；记录 CAS 与幂等结果 | WMS-03 |
| WS-05 | 进程重启、宿主迁移或墙钟回退 | 从获授权 checkpoint 恢复，paused 不补进度，无逐分钟补跑、无旧句柄执行；记录时间和调度决策 | SHR-11，WMS-07 |
| WS-06 | memory.correct 后旧投影仍在缓存 | 依赖旧 atom revision 的快照/候选失效，输出只用新授权证据；保留提交与输出关联 | WMS-02，SHR-09 |
| WS-07 | memory.retract/撤权/策略切换与输出并发 | 输出边界挡住旧依赖，积压通知不构成继续使用的许可；不重发已提交外部动作 | WMS-05，SHR-08/09 |
| WS-08 | 提交后响应丢失，事件重复或乱序 | 查原操作得到原效果；完成 Episode 一份，MemoryProposal 幂等；消费无 World/Memory 循环 | WMS-06 |
| WS-09 | 模拟学习完成，同时现实观测缺失或相矛盾 | 模拟、derived、user_stated 与 observed 保持来源差异；无回执不声称真实产物或用户行为 | WMS-01/04 |
| WS-10 | 大量休眠活动、多会话查询、并发恢复 | 输入取数和总工作集有界，撤回处理仍获调度；记录峰值 RSS、暂存、队列和释放残留 | WMS-08，SHR-10 |
| WS-11 | session 预设改为 user/global，或第三方声明未知活动类型 | 不移动历史 anchor、不借恢复越界；必需 Schema 未协商则拒绝，不加载未知对象 | SHR-12 |
| WS-12 | Memory/world provider 暂时不可用，完成摘要失败 | 可用领域继续回复；过期依赖不用，已提交 Episode 不回滚；恢复后按原 owner 对账 | WMS-03/05 |

本轮设计已给出归属、状态转移、操作边界、端到端轨迹、资源策略和验收输入。下一份世界产物只需将这些决策固化为最小 `ActivityProcess/checkpoint/WorldEvent` 与能力请求/回执 Schema、反例和 WS 夹具；不以继续扩写房间、衣柜、饮食或技能大全为前置条件。届时完成格式审查即可转入角色决策闭环的设计，真实 Writer、订阅和资源运行验收继续保留为后续建设任务。统一顺序只在总纲 §10 维护。
