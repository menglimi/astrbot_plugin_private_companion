# 旧框架硬编码审计与 AI-first 重建设计

> 导航：[设计总纲](./FRAMEWORK_DESIGN.md) / [主题目录](./FRAMEWORK_DESIGN_INDEX.md)。定位：审计与设计来源；旧词表转为反例、提示词约束和结构化协议，非新词库规范。

> 状态：设计稿，仅用于新框架设计，不修改旧运行代码。
>
> 审计材料：`陪伴插件白名单词表整理.xlsx`。这不是新框架的词库，也不是需要迁移的配置；它是旧框架硬编码问题的审计清单和反例库。工作簿中有 522 条审计记录，短语总数约 9531；记录覆盖私聊、群聊、主动消息、日程、记忆、生图、TTS、日志和外部数据等多个边界。

## 1. 要解决的问题

旧框架把“一个词出现了”直接等同于“文本是什么”和“系统应该做什么”。同一组词经常被复制到多个函数里，同时扫描入站消息、模型生成正文、内部备注和外部网页文本，最后触发吞回复、删句、改写、延迟主动消息或删除记录。

新框架只把词条当作证据，不把词条当作动作。词条、作用范围、上下文条件、影响方式和过期时间都由数据配置；框架代码只实现通用匹配、归因和策略执行能力。

这不是把 522 行简单导入 YAML。表格中的词同时混合了放行、拦截、特征、协议标记、脱敏标记、状态迁移触发器和已经失效的旧代码。新框架首先要识别每行暴露出的旧设计问题，再决定采取“删除、改为 AI 提示、改为结构化协议、改为独立状态机、保留为安全不变量”中的哪一种处理；默认结果是移除词面限制，而不是保留词条。

## 0. 总原则：AI 主导，代码兜底

这次重建的目标不是建立一套更大的拦截词库，而是尽可能移除自然语言层的硬编码限制，让 AI 在完整上下文中理解和决策。

代码只固定四类不可避免的不变量：

- 平台权限、用户明确的隐私边界和凭据不可外泄；
- 工具调用、外部发送、写入和删除等副作用的授权与幂等；
- DTO/协议格式、超时、资源预算和故障恢复；
- 系统自身不能被用户文本或模型文本改写的基础设施约束。

除此之外，情绪、意图、语气、亲密度、是否追问、是否主动联系、如何修复表达和如何理解歧义，优先交给 AI。词条只作为提示材料、例子和弱证据，不得充当默认拦截器。

采用“模型先判断，结构化校验兜底，必要时轻量修复”的顺序：

```text
完整上下文
→ AI 生成意图/情绪/表达/主动计划
→ AI 自检事实、权限和声明
→ 代码校验 capability、协议和预算
→ 执行或返回模型修复
```

风格、情绪和质量类问题优先通过 `PromptCompiler` 的动态约束、反例和自检提示解决，不再用事后关键词删句。旧词表中的大多数条目应转成评测反例、提示词改进素材或直接退役，而不是逐条恢复为运行时限制。只有经过重新论证的少数安全/权限不变量，才允许进入新框架的结构化约束。

### 0.1 旧判定器的 AI-first 替代

| 旧类目 | 新框架的主判断者 | 代码保留的部分 |
|---|---|---|
| `_smart_silence_*`、`_rest_reply_boundary_score` | AI 从完整消息和分句中解析边界、收束、例外和持续时间 | 写入边界事件、执行已确认的静默状态 |
| `_proactive_text_is_intimate`、泛问候和“读空气”词表 | AI 根据关系、上下文、近期互动和动机判断表达压力 | 联系权限、预算、冷却和发送幂等 |
| `_framework_agent_meta_summary_leak`、`_response_review_meta_leak_reason` | AI 自检可见回复是否泄漏内部过程，并给出结构化检查结果 | 仅对明确的内部字段和协议标记执行兜底 |
| `_sanitize_action_boundaries`、媒体/图片词表 | AI 生成可验证的 speech act 和 claim | 用附件、工具回执和能力检查验证声明 |
| 日程、日记、情绪、群氛围关键词 | AI 生成带来源和置信度的事件或计划 | 保存枚举状态、时间、revision 和生命周期 |
| “消息已发送”“已挂起”等文本协议 | Provider/Tool/Delivery 结构化结果 | 旧文本仅作为低优先级兼容解析 |

提示词应明确“理解后再行动”“保留用户原意”“发现不确定时询问或降低主动压力”，而不是把成千上万的词直接写成禁止项。模型生成后先做自检和局部修复，只有经过 capability 和权限校验的副作用才由代码执行。

## 2. 从审计表得到的优先级

| 维度 | 数量 | 设计含义 |
|---|---:|---|
| 审计记录 | 522 | 迁移对象必须保留来源、文件、行号、commit 和版本 |
| 短语总数 | 约 9531 | 不能继续在多个模块复制词表 |
| S1 吞掉整条 | 36 | 先处理整条丢失问题 |
| S2 改写替换 | 91 | 改写必须有明确字段和可解释原因 |
| S3 删句剥除 | 43 | 删除只能作用于结构化子句或专门的脱敏字段 |
| S4 回退作废 | 3 | 回退不能由自然语言短语决定 |
| S5 不入库 | 51 | 记忆过滤要有来源、字段和保存策略 |
| S6 仅日志 | 3 | 日志类规则不能反向改变用户行为 |
| S7 其它 | 295 | 先拆成明确的路由、分类、协议或诊断规则 |
| user-visible | 194 | 默认禁止仅凭子串吞掉用户可见文本 |
| internal | 317 | 优先改为结构化 DTO 和结果码 |
| log-only | 11 | 只能生成诊断证据，不参与决策 |
| high confidence | 240 | 可进入影子运行和人工复核队列 |
| medium confidence | 163 | 需要上下文条件，不能直接硬拦 |
| low confidence | 119 | 默认只观察和记录，不得产生破坏性效果 |

## 3. 四种数据对象

### 3.1 `LexiconEntry`

词条是可版本化的配置记录，至少包含：

- `entry_id`：稳定 ID，不能用短语本身作为主键；
- `forms`：原词、同义写法、繁简体或其他语言写法；
- `match_mode`：`exact`、`phrase`、`regex`、`semantic_hint`，默认使用最窄的模式；
- `semantic_class`：如 `user_boundary`、`conversation_close`、`quoted_content`、`provider_error`、`media_claim`、`intimacy_hint`；
- `applies_to`：入站、出站、内部字段、外部数据、日志，以及私聊/群聊/频道；
- `actor` 与 `target`：说话人、被指向的人和是否必须指向 Bot；
- `context_requirements`：引用、否定、转述、句首/句尾、长度、字段类型等条件；
- `effects`：只声明允许的证据、姿态、候选或存储影响；
- `confidence`、`priority`、`expires`、`enabled`；
- `positive_examples`、`negative_examples`、`source`、`review_status`。

词表可以由用户配置、默认包或迁移包提供。新增词条不应改动运行代码。

### 3.2 `TextObservation`

所有待分析文本先包装成带来源的观察对象，不再把多段文本拼接成一个字符串后全局扫描：

```text
observation_id
direction: inbound | outbound | internal | external
channel: private | group | qzone | other
actor_id / target_id
field_path: message.text | response.visible_text | audit.note | error.detail ...
structured_kind: user_message | model_reply | tool_result | provider_error | history_item ...
text
provenance: user | model | plugin | provider | quoted | imported
session_id / timestamp
```

`field_path` 和 `provenance` 是硬边界。比如用户消息中的“模型调用失败”和 Provider 返回的错误对象，不能共用同一个判定器。

### 3.3 `LexiconEvidence`

匹配器只能产生证据：命中的 entry、文本区间、匹配模式、归因结果、上下文和置信度。证据不会直接调用发送、删除或工具。

```text
matched_entry
span
speaker / target
context: direct | quoted | reported | negated | ambiguous
confidence
suggested_effects
rule_version
```

### 3.4 `PolicyDecision`

策略层消费证据和结构化状态，产生统一决定：

```text
decision: pass | modulate | defer | rewrite | suppress | reject | log_only
reason_code
scope
expiry
evidence_ids
replacement_plan / delivery_plan
```

所有决定都要能追溯到证据和规则版本。自然语言 `reason` 不能再作为成功、失败或内部指令的协议。

### 3.5 不能把所有记录都建成 `LexiconEntry`

工作簿中至少有八种不同的规则资产：

1. `IntentTrigger`：用户意图、命令、边界和一次性例外；
2. `OutputGuard`：出站声明、内部泄漏、舞台指示和可见内容完整性；
3. `SignalFeature`：情绪、群氛围、天气、食物、兴趣等弱信号；
4. `RoutingGate`：工具、模型、媒体、TTS 和参考图路由；
5. `StateTransitionTrigger`：静默、自动关闭、生日偏好、日程调整、重试和冷却；
6. `RetrievalStorageFilter`：记忆、表达学习、摘要和审计的入库策略；
7. `ProtocolFallbackParser`：没有结构化回执时的兼容解析；
8. `DataSanitizer`：凭据、日志、诊断和展示层脱敏。

其中 `LexiconEntry` 只保存词形和词义变体。跨句、跨记录、共现、时间窗、附件和 URL 域名判断由 `DetectorSpec` 表达；事实、权限和声明约束由 `ConstraintSpec` 表达；状态变化由 `TransitionSpec` 表达；工具与 Provider 状态由 `ProtocolSchema` 表达。这样可以避免把一整段旧 `if` 搬进配置文件。

每条迁移记录先变成 `RuleCard`，包含目标问题、输入字段、来源、方向、规则资产类型、判定信号、期望结果、禁止副作用、正反例、可达性和 owner。表中“用途、后果、误伤示例”有部分是 LLM 推断，必须标记 `evidence_source=code|test|llm_hypothesis`，不能直接当作事实。

### 3.6 证据图和多意图

一条消息可能同时包含多个意图和相反方向的信号，不能只返回一个分类。`IntentGraph` 的节点至少包含 `clause/span`、`actor`、`target`、`polarity`、`speech_act`、`modality`、`temporal_scope`、`durability` 和 `certainty`；边表示引用、转折、条件、否定范围和因果关系。

多个观察对象通过 `ObservationSet + JoinSpec` 组合，例如“当前消息 + 近期 Bot 回复”“候选 topic + motive + 真实附件”“外部正文 + URL 域名”“日记声明 + 未确认计划”。每个节点和边都要保留来源链、`source_event_id`、`quote_id`、`generation_id`、工具回执和时间窗。

## 4. 词条允许产生什么影响

影响不能只用一个 `effects` 字段表示，而要按领域和副作用拆分，并声明前置条件：

- `AffectEffect`：只改变情绪证据和短期姿态；
- `RoleplayEffect`：改变表达长度、提问预算、直接度和声明修复计划；
- `ProactiveEffect`：改变候选分数、压力、成本、冷却和联系机会；
- `RoutingEffect`：选择工具、模型、媒体或 TTS 路由；
- `StorageEffect`：控制字段级入库、保留和脱敏；
- `DeliveryEffect`：控制发送、延后、回退和幂等；
- `DiagnosticEffect`：只影响日志和审计展示。

`suppress`、`reject`、`delete` 等硬效果不是绝对禁止，但不能由普通词面单独触发。它们必须满足 `Preconditions`：结构化来源、权限、作用域、置信度、冲突处理、有效期和可审计原因码。明确的用户边界、凭据隐私和平台安全可以进入硬门；风格、亲密度、泛问候和抽象措辞应优先通过提示词约束和轻量修复处理。

## 5. 统一处理流水线

```text
ObservationSet
  → Normalizer + IntentGraph
  → DetectorGraph（词法 / 结构 / 语义 / 跨记录）
  → EvidenceGraph
  → 各领域 Reducer
       ├─ Boundary / Conversation
       ├─ Affect / Roleplay
       ├─ Proactive
       ├─ Media / Claim
       ├─ Memory / Storage
       └─ Operations / Protocol
  → 各领域 PolicyArbiter
  → StateTransition + OperationEffect
  → ResponsePlan / CandidateSet / StoragePlan / DeliveryPlan
```

### 5.1 先做结构判断，再做词条判断

先读取事件方向、字段类型、发送者、引用关系、真实媒体附件、工具回执和错误类型。只有在这些结构信息不足时，才使用词条作为补充证据。

### 5.2 上下文必须可以否决词条

以下情况默认降低置信度或忽略命中：

- 文本属于引用、转述、代码块、JSON、日志或 Provider 原始回执；
- 命中词位于否定范围内；
- 词条要求指向 Bot，但当前句子指向第三方；
- 命中发生在内部字段，而规则只适用于用户可见文本；
- 当前规则没有匹配到规定的句式、长度或字段类型。

上下文不只是几个枚举值。需要支持否定和转折范围、嵌套引用、多人说话人、条件和假设、时态、代码/JSON/日志、群聊目标和分句之间的关系。匹配器应返回 `true | false | ambiguous`，而不是把低置信度强行压成命中或未命中。

### 5.3 冲突处理

冲突不能靠一个全局排序解决，而要按影响维度分别合并。每个领域定义 `deny / allow / modify` 的冲突集、同一消息多子句合并、显式撤销、一次性例外、去重和互斥规则。停止打扰、隐私和安全边界可以优先于亲密、问候和主动候选，但只在相同 scope 和 effect 维度内覆盖。

例如“我不想收到主动消息，但这次提醒我”应形成一个持久 opt-out 和一个一次性例外；“晚安，明早八点叫我”应同时产生会话收束和定时任务意图；“哈哈，别这样”应同时保留玩笑和边界两个不确定信号。

### 5.4 状态、声明和副作用

状态变化必须写成事件和 revision，不能在文本扫描函数里直接修改队列、记忆、预算或缓存。临时边界、会话静默、六小时反馈、三十天去重、持久偏好、重试退避和历史清理使用不同的 `TransitionSpec`。

角色扮演需要 `ClaimLedger`：模型声明“我拍了张照片”“刚看到你说”“已经发过去”等内容时，必须与真实附件、工具回执、时间和发送记录核对。无法验证时只修复声明或换候选，不用事后删掉整段回复。

主动候选拆为 `source_signal`、`motive`、`rationale`、`content`。动作可执行性来自结构化 action、权限和 receipt，不能从模型写的理由文本反推。一个状态提问只影响同一 target、主题和候选类型，不能清空整个候选池。

## 6. 对审计表中主要问题的替代方案

### 6.1 用户说“别问了”“晚安”导致回复被吞

把它识别为 `user_boundary` 或 `conversation_close` 事件，写入当前会话和当前关系的短期状态：

- 扮演：减少提问，保持简短；
- 主动：降低候选或进入配置的静默期；
- 情绪：增加负担/防备，但按半衰期恢复；
- 回复：仍由 `ResponsePlan` 决定是否需要一句确认。

词条不能直接清空整条回复。`“别回复我唱功问题，但请帮我看代码”` 应由范围解析分成两个子句，边界只作用于唱功话题。

### 6.2 模型正常提到“工具调用失败”“没有返回值”被当成内部错误

错误判断改为结构化 `ProviderResult`、`ToolResult` 和 `ErrorEnvelope`。只有来自指定 Provider/工具字段、带错误类型和调用 ID 的对象才可进入错误分支。

用户分享的标题、网页正文、模型可见回复和主动正文中的相同文字，只产生普通文本证据，不得清空字段。

### 6.3 “看到你说”“发你看图”“睡前”“床”等普通表达被改写

媒体声明、亲密程度和最近消息承接分开建模。词条只能产生 `media_claim_hint` 或 `intimacy_hint`，还要结合真实附件、关系范围、动作类型和当前表达姿态；不能因一个生活词直接删句或把主动候选判为越界。

### 6.4 日程、日记和状态被自然语言词误判

计划和状态使用枚举、来源、时间和证据字段。`activity`、`mood`、`message_seed` 的自由文本只用于展示和提示，不能反过来改变计划类型或事实状态。需要分类时保存 `classification` 与 `classification_confidence`，不再从 `note` 或 `summary` 反推。

### 6.5 跨模块用“消息已发送”“已挂起”判断成功

所有工具、转述、定时任务和外部渠道返回统一的 `OperationResult`：

```text
operation_id
status: accepted | scheduled | sent | failed | cancelled
recipient
channel
error_code / error_detail
receipt
```

用户可见文案由结果码渲染。文案变化不会破坏状态机。

### 6.6 记忆、审计和日志被词条误删

存储层接收 `StoragePlan`，明确 `store`、`exclude_field`、`redact_secret` 和 `retention`。普通词条只能标注“可能是内部文本”，不能删除记录；低置信度项只进入诊断，不进入清理流程。

## 7. 与情绪、扮演和主动的衔接

情绪、扮演、主动、媒体、记忆和运营不是一条共用的情绪链。它们共享证据和来源，但由各自的 reducer 与 policy arbiter 消费：

```text
ObservationSet
  → EvidenceGraph
  → AffectReducer → AffectState / MotiveState
  → BoundaryReducer → BoundaryIntent / Transition
  → RoleplayReducer → ResponsePlan / ClaimLedger
  → ProactiveReducer → CandidateSet / InteractionOpportunity
  → Media/Memory/Operations Reducer
```

例如 `“我现在不想聊天”`：

1. 词条产生“用户边界”证据；
2. 情绪状态增加负担和脆弱度；
3. 动机转向修复、少打扰；
4. 扮演姿态降低提问和长度；
5. 主动系统生成“暂不联系”的候选状态；
6. 联系预算和时间策略决定何时恢复。

词条不能直接让 Bot 变冷淡、永久沉默或立即发送亲密话术。

## 8. 迁移方案

### 阶段 A：登记审计问题

将工作簿每行登记为旧框架问题，保留 `file`、`line`、`method`、`commit`、`version`、严重度、置信度、后果和误伤示例。先生成 `RuleCard`，记录“应该删除、交给 AI、改为结构化协议、改为状态机或保留为安全不变量”的处理结论；原始短语只进入反例和回放语料，不进入新框架运行时词库。

### 阶段 B：按规则资产和观察对象拆分

至少拆为：

- 用户入站文本；
- Bot/模型出站可见文本；
- 模型内部字段；
- 工具和 Provider 结构化结果；
- 记忆和审计存储字段；
- 外部网页、卡片、新闻和媒体元数据；
- 日志和诊断字段。

一条规则只能绑定明确的对象和方向，不能跨对象复用。

跨对象联合规则不能强行拆成多个独立词条，而应迁移为 `DetectorSpec + JoinSpec`，明确共现条件、时间窗口、参与字段和失败策略。

### 阶段 C：影子运行

新规则只记录证据和拟议决定，与旧行为并行比较。优先观察 36 条 S1、194 条 user-visible 和 119 条 low confidence 记录，统计吞回复、错误改写、误删和漏识别边界。S1 也包含中低置信度记录，不能仅按 high confidence 自动启用。

### 阶段 D：先替换协议，再替换词表

先落地 `OperationResult`、`ProviderResult`、`StoragePlan`、`ResponsePlan` 等结构化接口，消除“从自由文本猜状态”。再启用词条的表达调节和候选调节，最后才评估少量有明确范围的硬门。

### 阶段 E：删除旧判定器

当新管线完成一轮语料回放并达到发布门槛后，逐个移除重复词表、自然语言成功文案匹配和跨模块字符串协议。旧函数只保留迁移回放所需的行为记录。

## 9. 发布门槛

- 任意用户可见文本不能仅因一个普通子串被整条吞掉；
- 低置信度的 `ambiguous` 结果必须保留原文，并只能降低分数、请求澄清或进入观察队列；
- 每个启用 entry 都有作用对象、范围、置信度、过期策略和正反例；
- lexical 规则不得绕过 Preconditions 直接触发 `suppress`、`reject` 或删除存储；
- 所有跨模块状态判断使用结构化结果码；
- 每个决定可追溯到 `evidence_id` 和 `rule_version`；
- 规则支持热加载、版本回滚和单条禁用；
- 522 条审计记录及误伤示例可以自动回放；
- 回放覆盖多意图、否定、转折、引用、群聊多人、外部文本、模型拒答和真实附件缺失；
- 词条更新不需要修改角色扮演、主动或投递代码。

## 10. 后续设计任务

1. 把工作簿 522 行逐条映射到 `semantic_class`、`observation_scope` 和 `effect`；
2. 标记哪些条目其实是协议、枚举、权限或结构化错误码，而不是词条；
3. 为 S1/S2 user-visible 条目补齐最小反例集；
4. 设计词库管理页：预览命中、查看证据、启停、回滚和导出；
5. 为主动和角色扮演共用 `LexiconEvidence`，但使用各自的 `PolicyArbiter`；
6. 把用户反馈中的“误判”直接转成新反例和规则修订，不再新增散落的硬编码。
