# 角色决策契约包

> `companion.decision@1`，修订 `0.1.0`，状态 `review`。这是设计期机器契约，不是生产 SDK。

本包把持续剧本的行为链固定为：证据解释 -> `AffectEvent` / `RelationshipEvent` -> 状态投影 -> `RoleDecisionSnapshot` -> 回复或主动预览。它不拥有情绪、关系或消息事实；各领域仍由自己的 owner 提交 revision，决策快照只保存有界引用和短期结果。

`proactive-preview` 是第二阶段的观察模式产物：`mode=observe`、`risk_class=low` 且 `delivery_allowed=false`，只能比较 readiness、阻塞原因和建议窗口，不创建投递请求。`ShadowRun` 进一步记录旧策略/新策略差异、延迟和资源指标，并将 `delivery_requests`、`action_requests`、`memory_writes`、`calendar_commits` 固定为 0。真实主动行为仍必须经过统一 `PolicyArbiter`、占用态、授权、接触预算和 `DeliveryGateway`。

`ShadowAdapterBinding` 是平台无关的接入绑定：只声明 adapter 身份、generation、输入/输出 Schema、作用域和并发上限，并将 `delivery_permission` 固定为 `none`。AstrBot、独立服务或一站式应用只需把原生事件转换成 `RoleDecisionSnapshot`，再接收 `ShadowRun`；平台对象、凭据、消息句柄和数据库连接不能进入绑定或决策 DTO。

新增的 `NormalizedInteractionEvent` 是原生事件进入决策层前的只读边界。AstrBot adapter 只能输出稳定 `event_id`、已解析的 `RuntimeScope`、`session/user/global` 共享模式、脱敏摘要或 `ContentRef`、证据引用、可见性、`reality_mode` 和 `adapter_generation`；完整聊天正文、平台对象、凭据和消息句柄必须留在 adapter 内。决策层接收该输入后才生成 `RoleDecisionSnapshot`。`recordings/astrbot-message-recording.json` 是不含原文的 AstrBot 脱敏录制样例，`cases/normalized-input-cases.json` 固定了稳定 ID 缺失、身份未映射、群私聊误归因、私密正文泄漏到 global、旧代绑定和过期事件六个拒绝条件。

`decision.fixture.json` 同时覆盖三阶段目标：跨域时间线、低风险主动预览、纠正/撤回和跨用户裁剪。`scripts/replay_decision_fixture.py` 已在纯内存、无副作用模式下通过 9 项检查；`scripts/replay_normalized_input.py` 另对 AstrBot 脱敏录制和六个归一化反例通过 8 项检查。两个 fixture 的真实运行状态仍保持 `not_run`；隔离结果不代表真实平台授权、资源峰值或投递已验证。

`cases/cross-host-comparison.json` 和 `scripts/replay_cross_host_comparison.py` 使用同一规范事件比较 AstrBot、独立服务和嵌入式 shell。回放要求逻辑 owner、`user_shared` 可见性、证据、内容哈希和共享模式一致，允许差异仅限宿主、运行实例、平台账号、adapter generation 和 session 引用；当前 9 项隔离检查通过，真实跨平台身份绑定仍为 `not_run`。

`cases/capability-degradation-budget.json` 和 `scripts/replay_capability_degradation.py` 固定三套 shell 的可用 feature、降级原因和共享资源预算。能力缺失只能产生声明式降级（如视觉引用模式或无模型模式），不能偷偷扩大候选、模型调用、快照字节或并发；当前 11 项资源/降级隔离检查通过。

`cases/global-multi-window-continuity.json` 和 `scripts/replay_global_multi_window.py` 固定“一个角色同时面对多个窗口”的运行模型。三个窗口共享同一 `actor_id`、`persona_id`、全局活动和 revision，切换只改变注意力；每个窗口仍保留独立 session 和受众边界。当前 13 项多窗口连续性隔离检查通过。

`cases/persona-window-matrix.json` 和 `scripts/replay_persona_window_matrix.py` 补充区分“同一人格的受众投影”与“真正的多人格运行时”：同一 `persona_id` 的群聊/私聊共享角色连续性，不同 `persona_id` 必须拥有独立 `actor_id` 和领域 owner；人格绑定变化会使旧 revision 草稿失效。当前 8 项矩阵检查通过。
