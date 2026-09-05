# 现有插件改造审计清单

状态：首轮静态审计 v0.1

审计日期：2026-09-05

范围：陪伴聚合体系十个插件的生产代码、manifest 和现有设计稿

原则：只记录可复核的代码事实；本轮不改变运行逻辑、不删除兼容路径。

## 1. 如何使用这份清单

设计稿中的架构目标不能直接等同于当前缺陷。每项审计先标记为以下一种状态：

| 状态 | 含义 | 当前允许的动作 |
| --- | --- | --- |
| `S0 static` | 从代码、配置或 manifest 即可确认入口和边界 | 立即盘点、标注、补测试，不改变协议 |
| `S1 observe` | 需要真实运行、回执或跨插件时序才能判断 | 先补统一审计字段，再影子运行 |
| `S2 contract` | 会改变公共调用、数据所有权或宿主边界 | 等 P0 契约冻结后分批迁移 |

审计输出至少包括：入口文件和函数、作用域、读写对象、外部副作用、失败/取消路径、当前 owner、证据链接、风险级别和下一步动作。静态扫描结果只说明“存在需要核对的路径”，不能单独证明一定会泄漏、重复发送或无法停止。

## 2. 现在可以立即做的审计

| 编号 | 对应设计主线 | 状态 | 首轮要核对什么 | 交付物 | 首批证据 |
| --- | --- | --- | --- | --- | --- |
| A-01 | 运行时身份与权限 | `S0 + S1` | 每个状态、缓存、任务、候选、记忆和页面请求的完整 scope；是否只用 `user_id`、`conversation_id` 或 `persona_id` | 作用域键清单；双 Bot/双人格/双会话隔离矩阵 | `private_companion/proactive_engine.py:362-470`；`remember_you/core/bridge.py:933-950`；`private_companion/bot_personal_contract.py:233` |
| A-02 | 对话主链与消息投递 | `S0` | Hook 阶段、priority、读写对象、是否注入/改写/停止事件、是否可能重复处理 | Hook 顺序图；冲突矩阵；唯一协调者候选 | `private_companion/main.py:1423`、`:8960`、`:9993`、`:11591`、`:18381-19110`；`remember_you/main.py:93-124`；`reality_companion/main.py:1270-1410`；`live_stream_companion/main.py:5926-6056` |
| A-03 | 对话主链与消息投递 | `S0 + S1` | 所有直接发送按命令回复、被动回复、主动消息、媒体、TTS、动作回执分类；是否经过发送前处理、幂等和投递回执 | 发送调用表；旁路清单；重复/不确定投递场景表 | `private_companion/main.py:8755-8850`、`:12008-12330`；`private_companion/proactive_message.py:17606-17685`；`screen_companion/main.py:2079-2084`、`:2905-2927`；`live_stream_companion/main.py:2068-2511` |
| A-04 | 控制面、诊断、迁移与运维 | `S0 + S1` | 每个 `asyncio.create_task` 的 owner、scope、名称、取消、超时、重试、异常回调和停止钩子 | 任务注册表；卸载后零遗留任务证明；任务数量和耗时基线 | `private_companion/main.py:8123-8264`；`screen_companion/main.py:970-1007`、`:2324-2603`；`reality_companion/main.py:521-579`；`live_stream_companion/main.py:391-405`、`:1327-1416` |
| A-05 | 控制面、诊断、迁移与运维 | `S0` | 跨插件发现是否走注册表、模块导入或 `sys.modules` 扫描；热重载后旧实例、旧 generation 和旧任务是否被撤销 | 依赖发现图；卸载/重载行为表；单一发现入口迁移清单 | `private_companion/main.py:516-520`；`private_companion/integration_status.py:543-555`、`:808-824`；`content_companion/main.py:549-563`；`image_companion/main.py:472-502`；`reality_companion/main.py:606-640`；`together_companion/main.py:1260-1655` |
| A-06 | 外部连接、Session 与安全 | `S0 + S1` | Web/API/WS 的鉴权、token 位置、票据过期、重放、CORS/Origin、资源上限、卸载撤销和隐私字段 | 路由和连接矩阵；凭证/票据生命周期表；未授权路径测试 | `screen_companion/core/remote_receiver.py:86-127`；`together_companion/server.py:96-104`、`:481-546`；`reality_companion/mihome_web_api.py:499-576`；`live_stream_companion/subtitle_server.py:152-170` |
| A-07 | 记忆、证据与知识权威 | `S0 + S1` | 注入模型的事实是否带来源、时间、新鲜度、可信度和敏感级别；模型推测是否被写成稳定记忆；跨会话/跨人格共享是否有授权 | 上下文贡献清单；记忆写入来源表；证据缺失和误记样本集 | `private_companion/agenda_disclosure_policy.py:376-487`；`private_companion/emotion_diagnostics.py:197-266`；`remember_you/core/service.py:921`；`remember_you/core/bridge.py:1160-1170` |
| A-08 | 模型、媒体与资源治理 | `S0 + S1` | 模型 Provider、Token、视觉/TTS、媒体生成、缓存和临时文件是否有预算、TTL、取消、清理和失败冷却 | 每次模型/媒体任务资源账本；内存、Token、网络和媒体耗时基线 | `private_companion/token_budget.py:477-699`；`private_companion/private_image.py:3658`、`:5151`；`image_companion/image_runtime.py:21284-21321`；`together_companion/main.py:3001-3051` |
| A-09 | 主动消息与动机 | `S0 + S1` | 念头/候选的来源、状态、延后次数、TTL、合并键、接触预算、未回应反馈和实际投递是否分开 | 候选生命周期表；延后/过期/合并/重复率；接触负担报表 | `private_companion/proactive_engine.py:320-520`、`:3370-3555`；`private_companion/proactive_message.py:15731-15970` |
| A-10 | 日历与双向时间控制 | `S0 + S1` | 日历事实、活动观测、软计划、可用窗口、安静时段和投影是否分层；Bot 回写是否走版本、授权、幂等和回滚 | 时间段事实表；重算影响范围；CalendarCommand 回写审计 | `private_companion/agenda_runtime.py:142-343`、`:661-969`；`private_companion/calendar_observer.py:162-370`；`private_companion/schedule_reconciler.py:170-560` |
| A-11 | 情绪、动机与角色表达 | `S1` | 情绪事件、状态投影、动机和表达姿态如何影响候选；是否直接改 prompt、频率或发送状态；状态是否可过期/撤回 | 新旧候选对照；情绪调制增量和 TTL；误判/恢复样本 | `private_companion/affect_modulation_contract.py:14-23`；`private_companion/emotion_diagnostics.py:93-266`；`private_companion/interaction_dynamics.py` |
| A-12 | 平台兼容与降级 | `S0 + S1` | 平台类型分支、消息组件能力、主动发送、引用/撤回/语音/图片失败降级；OneBot/QQ 官方身份是否混用 | 平台能力矩阵；每类组件的成功/降级/失败回执 | `private_companion/proactive_message.py:17606-17685`；`private_companion/proactive_chat_runtime_bridge.py:524-582`；各插件 `metadata.yaml` 的 `support_platforms` |

## 3. 首轮静态基线

下面是排除 `tests`、`benchmarks`、`scripts`、桌面端依赖目录后的文本命中统计。它们用于决定审计优先级，不是缺陷数量；同一函数可能命中多个模式。

| 插件 | Python 文件 | Hook 装饰器 | 发送相关命中 | `create_task` | Web/WS 命中 | 跨插件发现命中 | 身份字段命中 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `private_companion` | 215 | 66 | 90 | 32 | 18 | 52 | 3063 |
| `remember_you` | 58 | 2 | 0 | 3 | 2 | 7 | 613 |
| `content_companion` | 4 | 0 | 0 | 1 | 0 | 5 | 4 |
| `image_companion` | 31 | 0 | 4 | 2 | 0 | 7 | 209 |
| `reality_companion` | 14 | 5 | 1 | 9 | 5 | 9 | 277 |
| `screen_companion` | 14 | 2 | 38 | 21 | 62 | 0 | 13 |
| `together_companion` | 8 | 0 | 5 | 5 | 31 | 8 | 122 |
| `game_companion` | 16 | 1 | 0 | 5 | 1 | 2 | 17 |
| `live_stream_companion` | 25 | 3 | 13 | 35 | 118 | 1 | 131 |
| `bug_companion` | 7 | 0 | 1 | 6 | 2 | 0 | 15 |

## 4. 已确认的首批审计事实

### 4.1 Hook 已形成多条隐式顺序

`private_companion` 的生产代码中同时存在模型请求、模型响应、Agent 开始/结束和发送前装饰 Hook。发送前装饰 Hook 从 `main.py:8960` 延伸到 `main.py:12350`，模型请求 Hook 又集中在 `main.py:18083-19110`。外部插件也直接注册模型和发送相关 Hook：记忆插件在 `main.py:93-124`，现实插件在 `main.py:1270-1410`，游戏插件在 `main.py:893-910`，直播插件在 `main.py:5926-6056`。这些入口可以立即静态排序和标注，但只有运行时 trace 才能确认真实执行顺序、是否重复注入以及是否在同一会话生效。

### 4.2 发送出口还没有成为全局唯一出口

私有核心已有较完整的 Proactive Chat 局部桥接：`proactive_chat_runtime_bridge.py:448-507` 记录 attempt、物理发送成功数和状态结算，`proactive_chat_runtime_bridge.py:524-582` 负责平台发送确认；普通主动链在 `proactive_message.py:17606-17685` 同时尝试精确平台、核心发送和降级路径。与此同时，屏幕、直播、游戏、图片和 AT 转发仍有自己的 `event.send`、`context.send_message` 或 `send_by_session` 调用。第一步应逐处判断它们属于命令回复还是业务投递，并记录是否拥有 `dedupe_key`、`DeliveryReceipt` 和 `InteractionLedger` 结算。

### 4.3 任务管理已经有局部收口，但跨插件不一致

私有核心对启动任务和生命周期任务有登记、done callback、取消和超时处理（`main.py:8157-8264`）。游戏、共处、现实和直播也有 terminate/stop 逻辑，但仍能看到直接创建的延迟关闭、网络心跳、媒体、设备和自动回复任务。屏幕插件在初始化阶段直接加入多个 `background_tasks`（`main.py:970-1000`），直播插件有多组平台、字幕、VTS、Soullink 和 TTS 任务。需要先做任务清单和卸载测试，不能只因为某个插件有 `terminate()` 就认定所有子任务已收口。

### 4.4 发现机制存在注册表与模块扫描并行路径

私有核心保留模块级运行时单例（`main.py:516-520`），集成状态还会读取 `sys.modules`（`integration_status.py:543-555`、`:808-824`）。内容、图片、现实和共处插件也分别组合固定模块名、`get_registered_star` 和 `sys.modules` 扫描。新的 `external_bridge_resolver.py` 已经提供注册表优先、生命周期检查和有界缓存，可作为迁移基准；首轮先确认每个调用点是否可能持有旧实例和旧 generation。

### 4.5 作用域仍有明显的历史单键结构

主动候选池在 `proactive_engine.py:320-470` 以全局数据段和 `user_id` 分组，计划候选也从用户字典的 `planned_candidate_id` 读取。记忆插件的兼容桥在 `core/bridge.py:933-950` 明确保留“只要求 `user_id`”的旧接口；主动记录格式中也保留 `proactive:{user_id}:...`（`core/bot_personal_contract.py:233`）。这些不是立即删除的理由，但足以把多 Bot、多人格、多个会话的隔离矩阵列为 P0 审计。

### 4.6 Web/API 和实时连接由各插件分别暴露

屏幕远程接收器在 `core/remote_receiver.py:86-127` 自建 WebSocket，并在未配置 token 时允许可访问端口的客户端推送；共处服务在 `server.py:96-104` 暴露房间、媒体、上传和 WebSocket 路由，再用 `ticket`/`resume` 查询参数建立房间（`server.py:481-512`）。现实、直播、记忆、游戏和私有核心还分别注册页面 API 或设备服务。应先完成路由、票据、凭证、Origin、资源上限和卸载撤销的矩阵，再决定哪些能力搬到主控制面。

### 4.7 日历、主动和情绪已有可复用实现，适合先做影子审计

日历侧已有候选抽取、合并、生命周期、修订和重算函数（`agenda_runtime.py:142-343`、`calendar_observer.py:162-370`、`schedule_reconciler.py:170-560`）；主动侧已有候选池收缩、TTL、重复物化熔断和延后状态（`proactive_engine.py:509-520`、`:3370-3555`）；情绪侧已有事件/状态投影和诊断摘要（`emotion_diagnostics.py:93-266`）。这些实现可以先输出标准审计事件，比较旧策略与设计稿中的候选、时间段和表达姿态，不宜在缺少行为基线时整体替换。

## 5. 先加观测的统一字段

在主动、日历、情绪、模型、媒体和连接的运行审计中，至少记录以下脱敏字段：

```text
trace_id
scope_key
provider_id
capability_version
task_id
candidate_id
opportunity_id
action_id
delivery_id
policy_version
started_at
finished_at
status
reason_code
resource_usage
```

原始消息、精确位置、健康数据、屏幕内容和媒体正文不应因为审计被复制到 Kernel。审计事件只保留摘要、引用和脱敏后的状态。没有 `DeliveryReceipt` 时记录为 `submitted` 或 `uncertain`，不能直接记为 `delivered`。

## 6. 可以直接修正的局部问题

这些动作不需要等待完整 Kernel，可以在兼容层内完成：

1. 为现有 Hook 和发送点补充来源、阶段、scope、trace 和幂等键日志，不改变调用结果；
2. 将新的 `external_bridge_resolver` 用作新增跨插件调用的唯一发现入口，旧调用保留兼容回退并记录命中路径；
3. 给现有后台任务补齐稳定名称、owner、创建时间和取消原因，优先把裸 `asyncio.create_task` 纳入插件已有的任务集合；
4. 给页面和 WebSocket 状态接口统一返回 `installed/enabled/available/degraded/stopped` 及最近错误，不在主插件复制扩展凭证和原始数据；
5. 为日历候选、主动候选和情绪投影增加 `source_refs`、`valid_until`、`revision` 和 `trace_id` 的兼容字段，先双写审计，不改变旧决策；
6. 先建立多 Bot/多人格/多会话隔离测试夹具，发现串线时优先修复键构造和上下文传递，暂不删除旧数据。

## 7. 暂缓事项

以下工作需要等 `RuntimeScope`、`ConversationPipeline`、`DeliveryGateway`、`Evidence/Memory` 和迁移错误码冻结后再做：

- 全量替换外部插件公共 API；
- 把所有模型、图片、音频、视频和设备动作重写到新 Gateway；
- 删除旧的主动循环、兼容桥和页面路由；
- 把各插件领域数据库合并到主插件；
- 大规模修改 AstrBot 主事件模型或提交宿主 PR；
- 在没有影子数据和回滚版本时切换真实主动投递。

## 8. 首轮执行顺序

```text
Hook/发送/任务/发现静态盘点
  -> 多 Bot/多人格/多会话隔离夹具
  -> 统一审计字段和回执
  -> 主动、日历、情绪影子运行
  -> 低风险被动回复 + 一个低风险主动类型共用投递出口
  -> 按插件批次迁移并保留兼容回退
```

首轮完成条件：每个生产发送点和后台任务都有 owner、scope、失败/取消路径；每个跨插件发现点能说明注册表或兼容回退来源；候选、时间段和情绪投影可以追溯到证据和策略版本；隔离矩阵没有发现跨 Bot、人格或会话串线。满足这些条件后，才进入 P0 主链样板和数据迁移。
