# 陪伴生态重建设计范围

> 导航：[设计总纲](./FRAMEWORK_DESIGN.md) / [主题目录](./FRAMEWORK_DESIGN_INDEX.md)。定位：范围设计草案；维护领域地图、owner 与建设批次。

> 状态：设计稿。本文把现有陪伴插件纳入同一套长期重建设计，不代表当前插件已经完成统一实现。

## 1. 设计目标

陪伴体系不是“一个主插件 + 若干功能菜单”，而是一组共享同一角色、关系和生活连续性的 AI 能力域。本文按当前优先级分批设计，暂缓领域保留生态位置，但不参与首期主链冻结。

长期目标是让 AI 负责理解、组合和表达：它可以记忆、创作、画图、感知现实、一起共处、参与直播，也可以主动选择暂时沉默。插件负责提供可靠的事实、能力和执行回执；代码不再通过大量自然语言关键词替 AI 进行语义裁决。

统一内核只提供：

- `RuntimeScope`、身份、人格和关系作用域；
- 上下文编排、模型调用和 PromptCompiler；
- 能力发现、授权、资源预算和任务取消；
- Session、事件、证据、版本和回执；
- 外部动作的幂等、投递和审计。

各领域插件负责自己的事实、算法、连接、数据库、媒体和领域状态。

## 2. 领域边界

| 领域 | 插件 | AI 可以负责的事情 | 插件必须负责的事实和能力 |
|---|---|---|---|
| 陪伴核心 | `astrbot_plugin_private_companion` | 角色表达、关系理解、生活连续性、主动动机、跨域编排 | Persona、关系、日历事实、主对话与主动候选、最终投递协调 |
| 记忆 | `astrbot_plugin_remember_you` | 判断什么值得记住、如何回忆、如何处理矛盾和不确定性 | 记忆原子、证据、ACL、召回、衰减、删除和导入导出 |
| 世界模拟 | `PersonaWorldModel` 能力提供方（可独立插件） | 模拟人格所属的生活世界、活动过程、场景和可供性 | WorldEntity/Relation、ActivityProcess、WorldEvent、EmbodimentState、checkpoint；不拥有用户长期记忆或现实设备事实 |
| 创作 | `astrbot_plugin_content_companion` | 立项、续写、审校、创作风格 | 作品、章节、版本、创作记忆、封面关联和本地任务状态；QQ 空间读取/发布暂缓 |
| 生图 | `astrbot_plugin_image_companion` | 场景构思、画面表达、参考图选择建议、提示词编排 | ImageTask、后端能力、参考图归属、媒体资源、生成回执和费用保护 |
| 现实触及 | `astrbot_plugin_reality_companion` | 判断何时需要现实感知、如何把设备数据解释成生活背景 | 摄像头/音频/位置/健康数据、设备授权、凭证、动作执行和撤销 |
| 实时共处 | `astrbot_plugin_together_companion` | 通话中的连续对话、共同观影评论、共读理解、自然收束 | 房间、参与者、媒体进度、临时上下文、断线恢复和票据 |
| 直播演出 | `astrbot_plugin_live_stream_companion` | 弹幕理解、主播表达、直播节奏、Live2D 情绪表演、开口或沉默 | 直播事件、平台连接、字幕、TTS、VTS/OBS 动作和投递通道 |
| 屏幕观察 | `screen_companion` | 判断工作上下文、进度、阻碍和是否需要陪伴 | 脱敏屏幕观察、采样预算、原始媒体留存和授权 |
| 游戏共处 | `game_companion` | 组队、对局解释、邀请、策略和互动 | 玩家身份、席位、游戏房间、引擎进程和销毁 |

`astrbot_plugin_bug_companion` 与 Content 中的 QQ 空间工作台暂缓，不纳入当前主链。游戏列入第二优先级，先做能力和 Session 契约，不阻塞第一优先级的记忆、现实触及和实时共处。Bug Companion 未来仍属于开发治理平面，不应改变普通陪伴的角色状态或主动行为。

世界模拟与记忆通过 `WorldEvent`、`MemoryProposal`、`MemoryQuery` 和 `memory.changed` 组合，不共享数据库，也不互相冒充 owner。世界模拟的高频状态留在自身 checkpoint；只有用户确认、活动边界或明确治理意图才进入长期记忆。

## 3. 统一 AI 交互模型

所有领域都围绕同一份调用上下文工作：

```text
RuntimeScope
  + PersonaSnapshot
  + RelationshipView
  + ConversationState
  + EvidenceRefs
  + CapabilityView
  + ResourceBudget
  + SessionView
```

领域插件不直接拼接 system prompt，而是提交带来源的 `ContextContribution`：

```text
source_plugin
source_event_id
scope
content_type: fact | observation | memory | media | task | suggestion
content
confidence
valid_from / expires_at
authority
redaction
```

模型在统一 PromptCompiler 中理解这些贡献，生成结构化但可扩展的 AI 产物：

- `IntentGraph`：分句、意图、目标、时态、引用、否定和条件；
- `AffectEvent`：来源、目标、极性、强度、可靠性和有效期；
- `MotivePlan`：想完成什么、针对谁、压力和成本；
- `ResponsePlan`：可见表达、提问、媒体和语音形式；
- `ActionPlan`：需要什么能力、是否需要确认、预期副作用；
- `ClaimSet`：模型准备声明的事实、媒体、时间和工具结果；
- `MemoryProposal`：带证据、耐久度和敏感级别的记忆建议。

代码只校验 schema、scope、capability、权限、预算和回执，不依据普通自然语言关键词重做一遍 AI 判断。

## 4. 统一事件和能力平面

跨插件交互使用版本化对象：

| 对象 | 用途 |
|---|---|
| `CompanionEvent` | 用户消息、环境观测、作品进度、直播事件、房间变化和设备事件 |
| `CapabilityDescriptor` | 插件提供的能力、版本、作用域、权限、预算和缺失时行为 |
| `TaskEnvelope` | 异步模型/媒体/导入/同步任务，含 request、trace、generation 和取消语义 |
| `ActionRequest/ActionResult` | 设备、媒体、转述、房间、平台动作及结构化结果 |
| `SessionRecord` | 通话、观影、共读、游戏、直播和移动端会话的生命周期 |
| `EvidenceRef` | 可追溯事实、媒体、消息、工具回执和人工审批引用 |
| `DeliveryReceipt` | 已接受、已发送、已送达、未知、失败和取消，不使用自然语言回执猜状态 |
| `DiagnosticReport` | 只读诊断、证据、耗时、权限、降级原因和版本 |

能力注册不等于获得主动发送、记忆写入、设备控制或外部发布权。每次调用都重新检查当前 scope、授权和 capability。

## 5. 共享主链和领域主链

### 5.1 普通对话

```text
用户消息 / 当前生活事件
→ Core 解析 scope 与 IntentGraph
→ World/Relation/Affect 处理受影响的剧本状态
→ Memory 提供可见记忆和证据
→ Reality/Screen/Calendar 提供当前观察
→ Content/Image/Together 提供按需能力
→ AI 基于局部 ContinuitySnapshot 生成 ResponsePlan 和 ClaimSet
→ ClaimValidator 校验事实与媒体声明
→ DeliveryGateway 投递
→ 各领域按授权提交记忆、情绪、关系或进度事件
```

### 5.2 主动陪伴

```text
领域事实/事件/剧本检查点
→ World owner 提供可见的生活状态投影
→ AI 解释为 MotivePlan
→ CandidateSet 生成多种行为方案
→ Core 合并同一目标和时间窗内的机会
→ AI 选择表达或沉默
→ 代码只校验权限、预算、冷却、能力和投递幂等
```

直播、房间和设备内的实时反馈属于各自 Session，不逐帧进入普通主动额度；只有面向聊天窗口的新接触才进入统一主动调度。

### 5.3 共同活动

Together 创建 Session 后，可以按授权组合 Memory、Content、Image、Reality、Screen 和 Live 的能力。活动中的原始音频、视频帧、屏幕和位置默认只留在短期会话范围；结束时由 AI 提出一条共同经历摘要，Memory 再决定是否长期保存。

## 6. AI 自由度与代码边界

### 6.1 默认交给 AI 的内容

- 同一句话的多意图和隐含意图；
- 玩笑、引用、否定、转折和语气；
- 情绪目标、关系变化和表达距离；
- 是否追问、是否主动、主动的动机和方式；
- 创作、画面、直播和共同活动中的内容选择；
- 模型生成内容的自检、修复和降级表达。

### 6.2 必须由代码保证的内容

- 凭据、私密媒体、精确位置和健康数据不越权；
- 外部发送、设备动作、付费模型、发布和删除必须有 capability 与授权；
- 任务取消、版本 fencing、幂等和重试不依赖模型措辞；
- 真实附件、工具回执和事实证据决定声明是否可成立；
- Provider、工具、平台和房间状态使用结构化协议；
- 资源预算和连接生命周期不能被模型无限扩大。

普通风格词、亲密词、问候词、情绪词、生活词和领域词不能直接成为 `drop`、`rewrite`、`defer` 或 `block` 条件。需要行为调整时，优先回到 PromptCompiler 和 AI 自检。

## 7. 数据所有权和联动方式

数据按领域单一写入者管理：

```text
Core: identity / persona / relationship / calendar / proactive
Memory: memory records / indexes / ACL / retention
Content: projects / chapters / creative versions
Image: image tasks / assets / references
Reality: credentials / devices / observations
Together: rooms / sessions / transient activity context
Live: stream sessions / platform events / performance outputs
Screen: observation samples / activity context
Game（第二优先级）: game rooms / players / match state
Bug（暂缓）: evidence / review / tasks / approved knowledge
```

跨域只传引用、事件、建议和回执，不互相读 `data_dir`，不复制另一插件的可变状态。长期记忆、情绪、日程、作品进度和设备观测之间通过 `EvidenceRef` 与事件投影联动。

## 8. 缺失、降级和重载

- 缺少 Memory：普通对话和角色表达继续运行，长期记忆能力显示 unavailable；
- 缺少 Content：已有对话和其他领域继续运行，不生成创作能力假象；
- 缺少 Image：文本、创作和直播继续运行，图片计划返回明确 unavailable；
- 缺少 Reality/Screen：不伪造设备或屏幕事实，AI 使用中性背景；
- 缺少 Together：普通聊天和主动不受影响；
- 缺少 Live：普通人格和记忆不受影响，直播能力单独停止；
- 缺少 Game：不影响文字陪伴和其他实时共处能力，游戏能力单独显示 unavailable；
- 插件重载或 generation 改变：旧任务不能回写新实例，Session 和任务按各自 owner 恢复或结束。

降级应该减少可用事实和动作，不应通过增加关键词限制来“保护”主链。

## 9. 设计和建设顺序

以下是领域优先级与横切责任，不是五个依次执行的阶段；全局切片和完成条件以[设计总纲](./FRAMEWORK_DESIGN.md)为准。设备、房间等先参与边界建模，详细接口随领域切片定型，不阻塞记忆只读验证。

1. **第一优先级：记忆、现实触及、实时共处**：先冻结身份、证据、设备、房间、Session、权限和 AI 上下文契约；
2. **第二优先级：生图、创作、直播、屏幕、游戏**：在第一优先级稳定后接入媒体、作品、直播演出、屏幕观察和游戏 Session；
3. **AI 上下文平面**：统一 `RuntimeScope`、`ContextContribution`、`EvidenceRef` 和 PromptCompiler，贯穿两批领域；
4. **治理和外围能力**：QQ 空间与 Bug Companion 暂不进入当前重建主线，未来按独立能力接入；
5. **逐步退役旧限制**：以审计表中的误伤案例作为回放集，删除词面守卫、自然语言协议和跨插件旁路。

## 10. 共同验收场景

- 用户说“晚安，明早八点叫我”：对话收束、定时任务和主动状态同时成立；
- 用户说“救命，我代码报错了”：现实情绪和技术求助可以并存，不被一个词吞掉；Bug 治理暂不进入本批次；
- 用户请求“和我一起看这部小说”：Together、Content、Memory 按已读范围协作，不提前泄露剧情；
- 用户说“画一张我们在海边的照片”：Image 负责生成，Core 负责关系与投递，ClaimValidator 不允许伪造已发送；
- 手机上报“刚走完路”：Reality 提供带时效的观测，AI 决定是否关心，不能自动变成永久健康事实；
- 直播弹幕和私聊同时到达：Live Session 与私聊会话分别处理，不互相污染主动额度和记忆；
- 插件缺失或重载：主链继续运行，具体能力明确降级，旧任务不能越过 generation 回写。

本文与 [ARCHITECTURE_RESET.md](./ARCHITECTURE_RESET.md)、[ROLEPLAY_PROACTIVE_REBUILD.md](./ROLEPLAY_PROACTIVE_REBUILD.md) 和 [LEXICON_POLICY_REBUILD.md](./LEXICON_POLICY_REBUILD.md) 共同构成新陪伴体系的设计范围。

各领域共同遵循 [COMPANION_STATE_EVENT_FEEDBACK_DESIGN.md](./COMPANION_STATE_EVENT_FEEDBACK_DESIGN.md) 定义的状态、事件和反馈闭环，再进入各自的领域契约；避免记忆、现实触及和实时共处分别定义一套互不兼容的“当前状态”和“主动触发”。

功能范围还必须与 [FUNCTIONAL_COVERAGE_AND_PARITY.md](./FUNCTIONAL_COVERAGE_AND_PARITY.md) 对齐。该清单把旧插件的用户可见能力、后台治理和失败恢复单独登记为 `CapabilityCoverage`，防止“公共契约已完成”被误认为“功能已经齐全”。暂缓领域仍需保留能力描述和缺失状态；有意删除的能力必须给出替代路径和用户可见影响。
