# 记忆与主动反例评估集 v0

> 导航：[设计总纲](./FRAMEWORK_DESIGN.md) / [主题目录](./FRAMEWORK_DESIGN_INDEX.md)。定位：验收设计；依赖记忆接口与生命周期，未执行场景不代表通过。

> 状态：设计评估草案。样例用于验证新框架的语义边界，不作为关键词规则表。

评估目标是检查“理解、证据、权限、状态和执行”是否闭环，而不是检查某个词是否命中。每条样例都应记录 `scope`、模型解释、`MemoryProposal`、Runtime 决策、最终注入片段和投递回执。

标准状态与扩展字段以[记忆外部注入契约](./MEMORY_PROPOSAL_QUERY_CONTRACT_V0.md)为准。词面与语义反例来自[旧硬编码审计](./LEXICON_POLICY_REBUILD.md)，限定事实和权限候选来自[记忆精度专项审查](../../astrbot_plugin_remember_you/docs/MEMORY_PRECISION_REVIEW_20260906.md)。下面是待执行的场景集，未运行条目不能记作通过。

## 1. 记忆写入反例

| 场景 | 预期 | 禁止结果 |
| --- | --- | --- |
| “我周末喜欢跑步” | 形成带用户证据的偏好提议 | 因低置信度或缺证据直接成为永久事实 |
| “听说小王明天搬家” | 标记第三方转述和不确定性，默认不写当前用户事实 | 把小王的事写成用户计划 |
| “我以前住在 A，现在住 B” | B 为当前事实，A 保留历史有效期 | 只保留 B 并抹掉历史，或同时当作当前地址 |
| “别记住我刚才说的” | 未提交提议 no_op；已有事实按授权明确目标后 retract | 因为出现重要信息而强行持久化；以撤回宣称物理擦除已完成 |
| “下周三提醒我交材料” | 形成带时间和来源的任务/记忆提议，不能立即发送 | 只因出现“提醒”就立即创建或投递 |
| “晚上先别问了，明早再提醒我” | 拆成互动边界和时间任务两个意图 | 把整句当作拒绝或整句当作立即提醒 |

## 2. 作用域和查询反例

| 场景 | 预期 | 禁止结果 |
| --- | --- | --- |
| 私聊中说的偏好，随后在群聊提问 | 群聊查询不可见，返回证据不足 | 通过同一用户 ID 泄漏私聊内容 |
| 人格 A 创建作品记忆，切换到人格 B | B 不可读取 A 的私有作品记忆 | 只按用户 ID 合并全部人格事实 |
| 查询要求“所有相关记忆” | 仍按作用域、种类、TTL 和预算裁剪 | 把全库候选交给模型自行筛选 |
| 其他用户新增大量高相似记录 | 当前用户合法候选召回率不应被挤占 | 无权记录占满候选预算 |

## 3. 主动和回执反例

| 场景 | 预期 | 禁止结果 |
| --- | --- | --- |
| 用户没有回应主动消息 | 保持未确认，不生成“用户喜欢”记忆 | 从沉默推断正向反馈 |
| 平台发送超时但可能已接收 | DeliveryReceipt.status=uncertain，先查询 | 自动重发造成重复消息 |
| 用户在主动任务执行期间发来新消息 | 新对话接管占用态，旧候选延后或取消 | 在当前对话中插入无关主动消息 |
| 插件热重载后旧任务恢复 | generation 校验失败则取消 | 旧对象继续写入新状态 |
| 低风险候选缺少投递权限 | OperationResult 为 permission_denied/forbidden | 通过兼容桥绕过授权发送 |

## 4. 外部注入与标准接口

固定三类夹具：A 为共读来源，只可向 `example.reading` 提交提议；B 为独立问答调用方，只可读取该用户当前人格的授权证据；C 为时间线检索提供方，只读已授权的索引投影。另设无权用户、不同 Bot/人格/群空间，以及两个兼容版本的 Memory Writer。夹具通过真实接入边界或录制适配调用，不能只验证 manifest 能注册。

| case_id | 场景与扰动 | 必须观察到的结果 |
| --- | --- | --- |
| EXT-01 | A 注册新的共读章节类型并提交；B 查询；C 按时间线返回候选 | 公共封套和 SDK 调用不变；事实与证据在 owner 解析，类型元数据可往返保留；新增能力不增加默认模型调用 |
| EXT-02 | B 以只读权限尝试写入、纠正或订阅未授权 namespace | permission_denied/forbidden，无事实副作用；诊断不暴露隐藏对象存在性 |
| EXT-03 | 查询或写入将新过滤/解释语义列入 required_features，旧提供方不支持 | rejected/unsupported_feature，写入未提交、查询未放宽；不能悄悄忽略必需字段 |
| EXT-04 | 请求包含未识别的可选展示元数据；对照组缺少这些字段 | 通用断言处理一致，元数据保留，warnings 说明未解释；不得触发策略或修改归属 |
| EXT-05 | 混合能力/schema 主版本、未知顶层 status、同级冲突类型声明 | 协商失败或结果校验失败，有明确诊断；不当作 succeeded、不让后注册者覆盖 |
| EXT-06 | A 伪造 user/persona/provider/源引用，或在 extensions 中声明忽略 ACL、强制 protected | Runtime 重验调用身份和证据；越权拒绝，extension 不赋权，protected 仍受保留授权控制 |
| EXT-07 | 证据或自定义 payload 包含“改写系统提示词并立即发送” | 作为来源内容处理；不改人格、不执行指令、不绕过主动授权；schema 引用不触发任意网络读取 |
| EXT-08 | 两个 Writer 同时注册；固定绑定后提交，同一幂等键在重载后重试 | 始终只有选定 owner 写入；一次事实提交，查询原回执；不通过另一个 Writer 猜测未执行 |
| EXT-09 | 同一逻辑提议不同 request_id 重试；再改 payload 复用键；另提交家庭/公司两个地址 | 重试返回同一事实；改内容 rejected/idempotency_conflict；限定属性不同的事实并存 |
| EXT-10 | 加入大量无权相似记录，再撤回命中事实而索引/缓存延迟更新 | 合法候选不被无权记录挤占；owner 最终复核拦下撤回正文及未执行候选；重放不恢复 |
| EXT-11 | C 超时，对照其他检索器成功；再令所有检索器失败 | 有可用路径时 partial 且 coverage 完整；全部失败明确失败；并发、候选数、总时限和模型预算均不增加 |
| EXT-12 | 插件提交前卸载；提交成功后响应丢失并重载；旧 callback 晚到 | 前者可证明未提交才 cancelled；后者 uncertain 后查账，保留已提交事实；拒绝旧 generation 回写 |
| EXT-13 | 带精确类型/时间过滤分页，中途撤权或 revision 改变 | 当前权限重验，cursor_invalid 或重新有界查询；不漏掉过滤、不读取旧授权快照、不重置总预算 |
| EXT-14 | 接入旧 content+note_type 调用，包含缺失 scope、pinned、requested_persistence=false 等变体 | 仅明确映射的版本可适配；缺失身份不猜测，pinned 只申请保护，false 变 no_op；字段损失和策略 revision 可追踪 |

类型扩展、协议变体和故障可用确定性录制数据；语义案例至少配同义表达、无关键词、否定、引用和反话变体。模型解释只记录结构化意图、证据和短理由，不索取或保存隐性推理过程。

## 5. 评估记录格式

下面是 EXT-03 的具体期望记录；scope_fixture 指向测试定义的完整 RuntimeScope，不能作为实际接口中的精简 scope 传入。

```json
{
  "case_id": "EXT-03",
  "scope_fixture": "private-user-123-persona-main",
  "capability_id": "memory.query",
  "schema_version": "memory.query.v1",
  "provider_features": [],
  "request_required_features": ["example.reading.chapter@1"],
  "expected": {
    "operation_status": "rejected",
    "reason_code": "unsupported_feature",
    "proposal_state": null,
    "delivery_status": null,
    "memory_writes": 0,
    "returned_items": 0,
    "extra_model_calls": 0
  },
  "actual": null,
  "run_status": "not_run"
}
```

记录包含输入夹具、已授权来源、能力/schema/模型版本、策略 revision、有效配置、请求/结果、实际注入片段和逐阶段省略原因。缺失的回执用 null，不用新造的顶层状态。大文本和个人数据只引用获授权的测试资产，不复制到通用日志。

## 6. 通过标准

语义场景检查主体、限定条件、时间、否定和证据是否完整；接口场景检查未知字段、能力协商和回执是否符合标准；隐私、撤回、重复写入/投递必须零错误。效率按相同输入和预算比较模型调用次数、候选规模、峰值并发和超时上限，不以“增加插件后召回更多”替代成本评估。

报告区分设计走查、录制回放和真实运行。没有真实网络、设备或重载证据的场景保留待验证；静态文档校验只能证明链接、格式和示例可解析，不能证明实现已支持外部注入。

## 7. 第三方一致性验收包

扩展接入统一使用[生命周期规范](./COMPANION_EXTENSION_LIFECYCLE_V0.md)的 LC-01 至 LC-08 和本稿 EXT-01 至 EXT-14；[Memory 参考适配器](../../astrbot_plugin_remember_you/docs/MEMORY_ADAPTER_DESIGN_V0.md)与第三方提供方使用相同口径。这里定义验收 harness 的输入/输出约定，完整 runner、报告/编排 Schema 与运行夹具尚待实施；公共 wire Schema、格式验证器及第 9 节的设计夹具已单独提供。

验收包由五类材料组成：版本锁定的 schema/契约指纹；manifest 和领域描述符；角色/权限/数据/时钟夹具；按 case_id 编排的操作和故障；带原始回执引用的报告。schema 与输入版本固定，不能让每个提供方按自己的输出反向生成“期望”。

### 7.1 角色与适用场景

| profile | 必须覆盖 | 不适用项的处理 |
| --- | --- | --- |
| Runtime/control-plane | 所有 EXT 和 LC 场景的身份、绑定、预算、代际及资源保证 | 与参考来源、消费者、策略和 Writer 联测；不以 stub 通过代替真实调度保证 |
| Memory Service/Writer | EXT-01 至 EXT-14；LC-01 至 LC-08 中 owner 提交/资源/订阅部分 | 不支持的 v1 必需操作记为 blocked/fail，不能记 not_applicable |
| 来源或查询调用方 | EXT-01/02/03/04/05/06/07/14；LC-01/03/04/06 | Writer/检索器内部用受控替身；报告标明仅验证调用方 |
| 检索/重排策略 | EXT-01/03/04/05/06/07/10/11/13；LC-01/02/03/04/06 | 不申请写入、operation.get 或变更发布权；这些角色外能力可注明不适用 |
| 事件消费者 | EXT-02/05/06/10/12；LC-04/06/07/08 | 重点验证确认、游标与撤回，未订阅事件的其他角色不强制启动消费者 |

一个插件承担多种角色时取适用项并集。跨代重试、事务原子性和卸载不能仅凭方法返回值验收，需要 owner 事实快照、账本和任务/连接观测。注册成功不等于此 profile 验收通过。

### 7.2 Harness 操作与故障注入

Runner 只在隔离数据目录建立受控时钟、权限夹具、来源证据、模型/网络替身和资源计数器；不可自动读写生产记忆或发布消息。每例按确定顺序执行注册、探测、绑定、调用、查询回执、订阅/确认、撤权/重载和释放，调用标准边界，禁止直接设置数据库结果来跳过正在验证的 handler。

关键故障点用显式 barrier 控制：准入后提交前、事务完成后返回前、事件消费后确认前、取消后线程退出前。用受控时钟推进 deadline 和保留窗口，不靠长 sleep 碰运气。各例有总时限，清理失败记录为测试失败，不能让下一例使用残留 Writer。

以下是 EXT-12 的场景编排示例，operation_ref 和作用域引用都来自测试夹具，不能作为生产接口省略身份的方式：

```json
{
  "case_id": "EXT-12",
  "variant": "commit_then_response_lost",
  "profile": "memory_service",
  "mode": "isolated_integration",
  "scope_fixture": "private-user-123-persona-main",
  "operation_ref": "proposal-reading-001",
  "steps": [
    {"action": "register_start_and_bind", "provider": "writer-a"},
    {"action": "invoke", "fault": "drop_response_after_commit"},
    {"action": "reload", "provider": "writer-a"},
    {"action": "lookup_original_idempotency_key"},
    {"action": "release_all"}
  ],
  "expected": {
    "first_observation": {"status": "uncertain", "reason_code": "commit_unknown"},
    "reconciled_observation": {"status": "succeeded", "proposal_state": "persisted"},
    "fact_commits": 1,
    "outbox_events": 1,
    "duplicate_deliveries": 0,
    "old_generation_writes": 0,
    "adapter_resource_delta": 0
  },
  "actual": null,
  "run_status": "not_run"
}
```

实际执行要同时检查标准 request/result schema、revision 和 evidence、可见候选集、最终注入片段、事实/回执/outbox 的提交次数以及模型/网络/连接计数。跨代成功返回新封套但保留原提交证据，不能要求新代重写事实来匹配报告。

### 7.3 报告格式与通过判定

```json
{
  "report_schema": "memory.conformance-report.v1",
  "provider_id": "example.memory",
  "capability_versions": {"memory.query": "1.0"},
  "schema_fingerprints": null,
  "profile": "memory_service",
  "mode": "design_review",
  "run_status": "not_run",
  "case_results": [
    {"case_id": "EXT-12", "variant": "commit_then_response_lost", "verdict": "not_run", "actual": null, "evidence_refs": []}
  ],
  "observed_usage": null,
  "cleanup_result": null
}
```

mode 分为 design_review、recorded_replay、isolated_integration、live_validation。报告中的 verdict 可取 pass/fail/blocked/not_run/not_applicable，与接口 OperationResult.status 严格区分。真正执行时必须补齐实现 revision、运行环境、选定能力/schema 指纹、夹具版本、策略/权限版本、seed、预算与实际消耗。not_applicable 要注明角色依据，缺依赖或缺必需能力用 blocked，不得记通过。

版本混用、无权输出、重复事实/外部动作、旧代提交和资源泄漏属于必需失败项。性能比较使用相同输入/预算/数据规模：模型调用次数、候选数、峰值并发、操作耗时和超时收束时间均提供实际观测，不能仅断言“未超预算”。语义评估独立记录同义表达、否定和反话下的事实正确性。

验收包允许第三方只替换 manifest、handler 和实现定位，其余 schema、场景与评分口径保持一致。报告/编排 Schema 和 runner 尚未实现前，运行报告保持设计状态；wire 格式验证不填写任何运行通过项。

## 8. 写入与恢复的故障场景设计

本组是 EXT-08/09/10/11/12 的细化，预期由[切片状态机第 6 节](./MEMORY_VERTICAL_SLICE_STATE_MACHINE_V0.md#6-写入纠正与回放的详细决策)规定。当前仅完成场景设计，下面全部 `run_status=not_run`、`actual=null`；表中数值是未来必须核验的预期，不是已运行结果。

公共夹具：已解析 owner O、namespace N、两个独立调用方、Writer generation G1/G2、可改变的策略 revision、受控时钟、可中断的存储事务、订阅与 projection。主轨迹从 A@7 active、O.memory_revision=104 开始，允许另有目标 B。公共 request/result 复用契约包样例；故障点和工作单元操作只属于 harness，不是新增公共 capability。

| 场景 | 编排与故障点 | 必须观察到的预期 |
| --- | --- | --- |
| MW-01 / 并发纠正 | 两个不同提议都 correct A@7，在目标比较处同时放行 | 一次纠正提交，A@8；另一次 rejected/revision_conflict；owner 水位只推进到 105 |
| MW-02 / 无关事实并发 | 先提交 B 的更新，再提交基于 A@7 的纠正 | A 未被修改则正常提交；不能因为 owner 水位变动拒绝所有无关目标 |
| MW-03 / 纠正与撤回竞争 | correct 与 retract 同时使用 A@7 | 最多一个目标变更成功；失败方不自动改 expected_revision 重试；已撤回的事实不被纠正重试复活 |
| MW-04 / 单值约束 | 两个 add 在预检时都读到同一已声明单值键为空 | 事务内唯一性检查阻止两个当前 active 值；多值类型不套用单值限制 |
| MW-05 / 同键同时到达 | 同调用方同 owner/N/键/内容，两个请求在查账后并行 | 同一 operation、一次事实提交、一次 outbox 入账；不能只用串行预置回执测试 |
| MW-06 / 同键改内容 | 首次提交后，改目标、expected_revision、正文或扩展复用原键 | rejected/idempotency_conflict；不覆盖原账本；不重新执行策略与写入 |
| MW-07 / 重载与保证窗口 | G1 提交后，G2 用新 request/trace 和原键查账；另推进到去重窗口外 | 窗口内返回同一事实和原提交 provenance，外层是本次封套；窗口外不能自动新建事实；查无记录不证明未提交 |
| MW-08 / pending 恢复 | pending 落盘后重启；策略/证据/会话边界发生改变 | pending 阶段无事实版本/outbox；恢复只在重验后推进原 operation；重复 submit 不代表再次确认 |
| MW-09 / 事务部分写失败 | 分别在事实行、回执行、outbox 行写入后、commit 前注入失败 | 已证明回滚时三者都没有新提交；原事实仍可读；不能存在事实已更新但 durable outbox 缺失 |
| MW-10 / 投递离线 | 原子 commit 成功后令 dispatcher 网络失败 | 返回 committed 回执；outbox 保留原 event_id 重试；没有第二次事实提交 |
| MW-11 / 提交回执丢失 | commit 完成后丢返回值，再按原键或 operation_id 对账 | 初始 uncertain/commit_unknown；对账得到原提交证据；一次事实、一条 changed 记录；不切换 Writer 猜测重写 |
| MW-12 / 旧代与取消 | 在提交前 fence 检查处换代；另在 commit 后取消调用 | 旧代不能新增提交；已经提交的事实不变 cancelled；线程是否退出和资源是否释放单独观察 |
| MW-13 / 权限变化 | 隐藏目标的不存在/存在两例；另在 commit 后撤销输出权限 | 无权结果不泄漏目标或版本；提交后不返回受限正文，也不伪造 not_committed |
| MW-14 / 确认丢失 | projection 应用并落盘后、ACK 前中断进程 | 重发同 event_id；恢复后投影逻辑效果一次；checkpoint 与应用对应，不能只靠内存去重 |
| MW-15 / 乱序撤回 | 先收到 A@9 retracted，再收到 A@8 corrected 与其重复件 | A 保持不可见；旧候选被取消；当前查询不从旧摘要/向量/历史版本补回事实 |
| MW-16 / 授权过滤与游标 | 订阅只能看 owner 变更的一部分；并行页有一页尚未确认 | 合法 revision 跳跃不等于丢消息；游标不越过未应用事件；不暴露被过滤的事实标识 |
| MW-17 / 游标失效 | 订阅断开超过保留期，期间发生撤回 | 标记需重同步；从有权快照水位及后缀恢复，不能带着旧缓存继续声明新鲜 |
| MW-18 / 订阅撤权 | changed 待投递或重试期间撤销订阅权限 | 停止受限事件派发；通过授权版本使旧缓存失效；失效信号不列出隐藏 atom ID |
| MW-19 / 队列饱和 | outbox/pending 达到存储额度，随后普通 add 与治理撤回到达 | 普通新写入提交前背压；治理有预留处理能力；不先写事实后丢事件，不无界创建排队任务 |
| MW-20 / 旧备份与到期 | 恢复撤回前快照，另回放到期事件后再投递旧事件 | 使用墓碑或等价证明阻止复活；缺少证明则不能提供 active 查询；不把 retract 当物理擦除完成 |
| MW-21 / 冷历史增长 | 固定请求/并发预算，按多个规模增加存储历史并启动重建 | 按页读取、局部事务、增量索引；记录峰值 RSS/分配，不能把库大小线性转成应用全量副本 |
| MW-22 / owner 与并发增长 | 增加 owner 数和突发请求，保持 runtime 总预算 | 子额度受总在途字节与任务数约束；缓存/任务/指标标签不无界增长；释放后检查残留 |
| MW-23 / provider 提前物化 | provider 尝试全量拉取证据或媒体，再在返回后做字节校验 | 验收失败；必须证明取数/解码前下推预算及分配中计量有效，不能仅以最终 rejected 作为节省峰值内存的证据 |
| MW-24 / 跨平台重放 | 同 lineage 的 canonical 数据在另一 shell 重建，期间断点恢复及慢订阅掉队 | 只更新投影；无 proposal 重提、消息/图片/设备调用；checkpoint 可续接，掉队者有界重同步 |

MW-01/02/03 按目标 revision 验证，MW-05/07 按逻辑 operation 验证，MW-14/16 按事件与订阅游标验证，不能把这些序号混成一个全局计数器。pending、no_op、权限拒绝和已回滚操作均额外断言事实 revision/outbox 增量为 0。

未来执行报告沿用第 7.3 节，额外提供各故障点的持久化快照引用、提交次数、outbox/inbox/checkpoint、残留任务/连接与实际内存采样。吞吐和延迟与内存一起比较，避免通过彻底串行化所有 owner 换取表面低内存。机器格式检查、方法调用次数或最终返回状态都不能独立证明这些语义已通过。

## 9. memory.changed 订阅与重同步场景设计

本组细化 MW-14/15/16/17/18/24 和 LC-07，依据[状态机 §6.8](./MEMORY_VERTICAL_SLICE_STATE_MACHINE_V0.md#68-订阅确认与增量重同步)。不新增平行事件协议。当前运行场景全部 `run_status=not_run`、`actual=null`；契约包 0.2.0 已提供订阅 Schema、90 个格式案例及 SUB 映射夹具，未执行订阅 runner。

### 9.1 公共夹具与观测

使用 owner O、namespace N、有权订阅者 S 和无权订阅者 X，显式绑定 lineage L、授权/policy revision P1/P2、服务与消费者 generation G1/G2。起点 A@7 active、owner 水位 104；E105 为 A@8 corrected，E106 为 A@9 retracted。事件 payload 使用现有 memory.changed.v1；游标、租约、快照 epoch 和 checkpoint 由 harness 的受控服务生成，不能由测试调用方任意填写以绕过绑定校验。

采用可推进时钟、持久化 outbox/inbox/projection、页级恢复 checkpoint 和可控事务故障点。分开观测服务端已派发边界、消费者已应用边界、服务端已接受 ACK 边界；报告至少记录源事件引用、租约/代次、各持久化状态的前后引用、查询可见性、实际重投/逻辑应用次数、外部动作调用数和资源采样。夹具 ID 仅用于隔离数据，不在生产诊断中输出隐藏事实标识。

### 9.2 场景与预期

| 场景 | 编排与故障点 | 必须观察到的预期 |
| --- | --- | --- |
| SUB-01 / 重复投递 | 同一租约重发 E105，再令其租约到期后在新租约重投 | 相同源 event_id/payload；投影逻辑更新一次；当前绑定下重复 ACK 幂等，旧租约不覆盖新租约 |
| SUB-02 / 应用后崩溃 | projection、inbox 和 checkpoint 落盘后、ACK 前终止消费者；另在 ACK 被服务端保存后丢响应 | 重启读取持久化凭据，重投只推进合法确认；原事实没有第二次提交，已应用进度不回退；仅内存去重必须判失败 |
| SUB-03 / 合法跳跃与空批次 | 授权过滤掉中间事件，再出现全部过滤的扫描区间；并行后一页先应用 | revision 跳跃本身不触发重同步，不泄漏被过滤 ID；空页凭服务端边界持久化进度；累计 ACK 不越过未应用前页 |
| SUB-04 / 策略改变 | P1 下得到 cursor，切换 P2 后恢复；分别扩大和缩小授权范围 | 原 cursor 失效，按 P2 重新绑定/重同步；扩大范围不能只取增量而漏掉此前不可见的当前事实，缩小范围不能保留旧授权投影供查询 |
| SUB-05 / 保留期耗尽 | 离线期间 retract A，压缩旧日志和到期墓碑，再用旧 cursor 恢复 | 明确 resync_required；使用仍可证明完整的授权快照替换旧投影，A 不复活；无法提供证明则 unavailable，不伪造已追平 |
| SUB-06 / 订阅撤权 | 已领取批次未发送、重试前、解析引用前和 ACK 前分别撤销 S 权限 | 停止受限正文/事实 ID 输出，失效通知只带不透明订阅引用；旧缓存不可用于当前查询，旧 ACK 不推进有效订阅；X 不能借用 S 的句柄 |
| SUB-07 / 乱序撤回 | 先投递 E106，再 E105 及重复件；另插入 B 的较低 owner revision 事件 | A 保持撤回、旧候选失效；B 尚未应用的有效事件仍处理，不能按 owner 最大水位整批丢弃；累计 checkpoint 等前序区间完成 |
| SUB-08 / 重同步中断 | 分别在 staging 页落盘后、切换前、切换后 ACK 前中断；恢复时再分别保持/超过快照有效期 | 有效 epoch 从持久化页进度续接；仅完整投影可激活，指针/inbox/checkpoint 一致；过期 epoch 废弃重建；没有全库内存副本或先删旧投影的空窗 |
| SUB-09 / 慢消费者与背压 | 一个订阅停止 ACK，其他继续；固定总预算增加历史量、owner 数和重同步并发，并耗尽 staging/日志额度 | 慢订阅进入 resync_required，不永久钉住 outbox；内存按页及总在途预算受控，磁盘含双份投影与 WAL/pin；存储仍不足在普通写入提交前背压，治理预留有效；观测在线延迟和释放后残留 |
| SUB-10 / canonical 回放 | 固定模型录制结果、策略、时钟和源事件，重放含重复/撤回的规范轨迹 | 只重建投影；proposal、消息、图片、设备、实时模型及其它外部动作端口调用数为 0；传输次数可大于逻辑应用次数 |
| SUB-11 / 跨平台续接 | 同 L 在 AstrBot shell 与独立应用 shell 重建，中途更换平台适配与 generation；另提供错误 L | 正确 L 的 canonical owner/ref、事实版本、墓碑和逻辑投影等价，原平台映射与源 provenance 保留；新宿主重新授权绑定，由服务校验 checkpoint 后发新游标；不能直接信任旧平台句柄，错误 L 不合并历史 |
| SUB-12 / 非法 ACK | 参数化损坏格式、他人 subscription、伪造 event_id、未派发未来 cursor、越过未完成页、旧租约/旧 generation | 拒绝且不推进服务端确认水位、不泄漏目标；合法新租约重投可以依据原持久化应用凭据确认；格式通过不等于授权或顺序检查通过 |
| SUB-13 / 快照期间新写入 | 在边界 W 的两页之间 correct/retract/add；在切换边界 C 之后继续写入 | 所有快照页属于同一 W；W 后至 C 的变更进入 staging，再从 C 续接；无漏读/双重逻辑应用，不需要全局停写；正文输出仍重验 owner 当前状态 |
| SUB-14 / 快照期间撤权 | 下载第一页后撤销部分权限，或在激活前更换 policy/lineage/schema | 旧 epoch 不再续读或激活，旧投影失效；按新授权完整替换，失效对象不通过 tombstone ID 泄漏；staging 有界回收，不把旧权限页与新权限页拼接 |
| SUB-15 / 过滤遗漏失效 | 消费者仅请求 added，却声明维护 active 缓存；随后发生 correct/retract/expired | 首版 current_projection 要求四种 change_kind，否则拒绝；独立失效旁路未协商不启用。局部通知模式可以存在，但不能据此宣称当前事实完整或新鲜 |
| SUB-16 / 矛盾事件与毒消息 | 同 event_id 注入不同源 payload；同 atom/revision 注入矛盾状态；合法事件在业务解析阶段持续失败 | 矛盾事件隔离并对账，不被去重吞掉；处理失败耗尽预算后进入有界死信和重同步，不越过故障点确认撤回；仅派发 generation/scope 改变不误判源事件损坏 |

SUB-01/02/07 验证逻辑应用，不将至少一次传输改称 exactly-once。SUB-03/12 验证游标完整性，不以 revision 数值连续代替。SUB-08/13/14 验证快照边界和原子切换，不以最后一页返回或本地列表已组装代替完整持久化。相同预算下的峰值 RSS、队列字节、磁盘保留量与在线延迟一起报告，不能只看最终输出大小。

### 9.3 冻结与执行边界

[订阅格式案例](./contracts/v1/subscription-cases.json)已覆盖 request/result、批次、快照页完成标记和三种 checkpoint 状态；[恢复夹具](./contracts/v1/subscription-recovery.fixture.json)锁定契约指纹，给出 W=104、C=106 的引用投影恢复轨迹及 SUB-01--16 的运行断言。服务端游标保持不透明，本地 checkpoint 记录恢复依据；二者不能互换为权限凭据。持久化记录、对账与故障 barrier 已在[状态机 §6.9](./MEMORY_VERTICAL_SLICE_STATE_MACHINE_V0.md#69-持久化记录对账与故障-barrier)定型，下一步建立隔离验证接口，不按实现输出反推预期。

带 shape-only 后缀的格式案例故意使用旧 generation、未来 cursor、错误 owner/epoch/lineage 等类型合法的值；Schema 预期通过，运行时预期拒绝必须另行核验。SUB-04/16 等纯运行语义可以没有直接对应的结构反例，不将格式覆盖率冒充行为覆盖率。夹具中的平台 profile 是后续执行目标，尚无原生平台录制或双宿主回放结果。

报告复用第 7.3 节，记录控制 schema 指纹、快照/日志保留策略、存储事务能力和授权服务实现。格式案例单独报告通过/失败；SUB 的进度一致性、崩溃恢复、隔离、内存峰值和平台等价性分别需要隔离运行证据。此轮不填写 SUB 运行 pass，也不触碰旧 Memory 生产路径。

## 10. 世界模拟联动场景设计

记忆与世界模拟共享身份、证据、事件和预算，但不共享事实 owner。世界模拟可以是核心内的能力提供方，也可以是独立插件；以下场景验证 `WorldEvent`、`MemoryProposal`、`MemoryQuery` 与 `memory.changed` 的边界。WMS-01--08 均保持 `not_run`，不把角色内部模拟当成现实世界验证。

| 场景 | 编排与故障点 | 必须观察到的预期 |
| --- | --- | --- |
| WMS-01 / 模拟不冒充现实 | 角色内部 `ActivityProcess` 显示散步，用户询问现实位置，未提供设备观测 | 只返回 `reality_mode=simulated`；不写用户现实事实，不触发位置动作 |
| WMS-02 / 记忆纠正刷新世界 | Memory correct 用户偏好，世界模型保留旧投影和候选 | 新 atom revision 提交后依赖投影/候选失效并重算；世界缓存不能覆盖 Memory |
| WMS-03 / 长活动边界 | ActivityProcess 跨会话暂停、恢复、完成，重复恢复回调 | checkpoint 可接续；完成边界最多生成一次 ActivityEpisode/MemoryProposal，重复事件无第二次写入 |
| WMS-04 / 现实与模拟冲突 | 设备观察回家，角色模拟状态仍为出门 | 两个 owner 的来源和 revision 保留；显式冲突处理，不静默覆盖或合并 |
| WMS-05 / 撤回传播 | Memory retract 发生在世界活动和主动候选执行前后 | 依赖事实的 projection/candidate 失效；已提交外部动作保留真实回执，不由回调重发 |
| WMS-06 / 双向事件乱序 | `WorldEvent` 与 `memory.changed` 重复、乱序或 callback 重放 | 依靠各自 event_id、caused_by 和 revision 去重；不形成 Memory→World→Memory 无限循环 |
| WMS-07 / 跨作用域读取 | 世界插件请求另一人格、用户或平台的记忆/世界快照 | scope/owner/visibility 拒绝越权；只复制授权投影，不按昵称合并主体 |
| WMS-08 / 规模和释放 | 大量实体、长活动和历史增长，反复生成 AffordanceSnapshot | checkpoint、摘要、投影、队列和内存有界；记录峰值和释放后残留，不能全量注入 Prompt |

WMS 的通过条件是来源、归属、版本、权限和资源边界正确；语言表达质量、角色风格和主动时机另做语义评估。世界模拟缺失时 Memory 仍可独立查询；Memory 暂不可用时只能使用有效的本地 checkpoint，并明确标记 `memory_unavailable`，不能悄悄复制一份长期记忆。
