# 提示词清单（2026-10-18）

本清单整理插件实际会发给模型的每一段提示词：位置、作用、以及它进入请求的前提。行号来自当前工作树，代码变动后会漂移；用每行给出的常量名或函数名重新定位。

## 0. 读法

- “提示词”指会进入模型请求的文本：system 指令、随材料附带的说明、工具描述、以及被当作材料注入的 JSON 里的指导语。用户可见的固定文案（拒绝回复、失败提示、唤醒提醒正文）也在末尾单列，因为它们容易被误认为提示词。
- “注入前提”写的是代码里真实存在的开关或分支。查法：对常量名 grep 引用点，读拼接处的条件。
- 通道顺序按生产实际执行顺序排；`execution_path=astrbot_core` 是本安装的实际回复链（`astrbot_host/core_reply_configuration.py:22`，运行库 `astrbot_host/core_reply_observations` 里同样记录）。

## 1. 组装通道

### 1.1 宿主核心回复链（生产主链）

AstrBot 先用人格与历史构造请求，插件在 `main.py` 的 `on_llm_request(priority=250000)` 里按顺序注入（`main.py:699-756`）：

1. `inject_core_reply_perception_policy` → system 追加固定策略块【陪伴感知与自然表达】（`core_reply_perception.py:61-77`）。
2. `inject_core_reply_prompt` → system 追加 V2 的**稳定段**（`core_reply.py:348-368`）；只追加 `stable=True` 的段。
3. `inject_core_reply_tools` → 工具目录与工具说明（失败只降级，不影响回复）。
4. `inject_core_reply_send_tool` → 发送工具。
5. `inject_core_reply_perception_context` → user 追加一个临时 part：`<companion_v2_context>{…}</companion_v2_context>`（`core_reply_perception.py:173-289`）。
6. `on_agent_begin`：媒体呈现收尾 `finalize_core_reply_media_prompt`（`core_reply_media_prompt.py:33-60`）与语音朗读准备。

人格本身由 AstrBot 构造，V2 不改写（`core_reply_configuration.py:27-28` 明确 `host_persona` 只读）。

### 1.2 V2 自有回复链（conversation.reply）

V2 自己执行回复批次时，由 `EvidencePromptCompiler.compile`（`companion_v2/conversation/foreground.py:478-504`）产出基础指令，再在 `ConversationMessages._request`（`companion_v2/conversation/messages.py:328-689`）里按材料逐段追加；媒体转述、流式分段（`conversation/streaming_reply.py`）复用同一份请求快照。

### 1.3 后台任务

每个后台任务有独立 system 提示与固定输出格式：日记/梦境、规划、反思、状态维护、自评、表达学习、识屏解读等，见第 6、7、9 节。

### 1.4 工具、输出协议与后处理

工具说明、JSON 契约、分段、语音标签属于“怎么输出”，见第 10 节；拒绝兜底与替换规则是后处理，不是提示词。

## 2. 宿主核心回复链：system 段

| 提示词 | 位置 | 作用 | 注入前提 |
| --- | --- | --- | --- |
| `BACKGROUND_INSTRUCTION` | `astrbot_host/core_reply_context.py:30` | 声明背景资料不是新发言/授权，身份沿用宿主人格 | 至少有一个背景段非空 |
| `MEMORY_INSTRUCTION` | `astrbot_host/core_reply_context.py:32` | 私聊记忆的归属与不确定性 | 本轮有私聊记忆材料 |
| `GROUP_INSTRUCTION` | `astrbot_host/core_reply_context.py:36` | 群聊发言者只代表本人、群记忆按账号归因 | 当前 scope 是群聊 |
| `SENT_CONTINUATION_INSTRUCTION` | `astrbot_host/core_reply_context.py:47` | 把已发出的主动消息放回对话顺序；承接旧话头只能引用真实出现过的原话 | 私聊且存在已确认投递的主动消息 |
| 表达偏好段 | `astrbot_host/core_reply_context.py:220-230` | 风格/日常表达偏好（`text_policy().instruction`） | 配置了 `style_prompt` 或开启日常表达 |
| 对话附加提示 | `astrbot_host/core_reply_context.py:232-248` | 页面保存的 `conversation.reply.prompt` | 该字段非空 |
| 角色档案 | `companion_v2/role_profile/task_reference.py` + `context.py:67` | 后台任务的角色参考；对话链不再注入 | 有角色档案且是后台任务 |
| 相识资料 | `astrbot_host/core_reply_context.py:250` + `companion_v2/relationships/profile.py:118` | 当前对象的相处资料 | 有该对象的档案材料 |
| 角色知识资料 | `astrbot_host/core_reply_context.py:253` + `companion_v2/conversation/messages.py:79` | 受控知识参考，不是事实证据 | 命中知识条目 |
| 本窗口成员资料 | `astrbot_host/core_reply_context.py:255` + `companion_v2/knowledge/members.py:30` | 群成员线索 | 群聊且命中成员线索 |
| 语音朗读指令 | `astrbot_host/core_reply.py:369-377` | 本轮语音回复的标签与朗读格式 | 本轮启用语音回复 |
| 【陪伴感知与自然表达】策略 | `astrbot_host/core_reply_perception.py:13` | 感知不是清单、角色状态是自己的、被问模型时的口径、图片理解边界 | 每次核心请求（有 prepared 即注入，幂等去重） |
| 呈现设置块 `<companion_reply_presentation>` | `astrbot_host/core_reply_media_prompt.py:52-60` | 语音标签与表情意图适用于本轮最终回复 | 启用语音或表情意图任一 |
| 分段说明 | `astrbot_host/core_reply_segmentation.py:24`（群）/`:30`（私聊） | 用 `{{split_marker}}` 标气泡边界 | 开启模型分段、`policy.llm_controlled` 为真、且本轮**不是**流式 |
| 工具使用规则 | `astrbot_host/core_reply_tools.py:34` | 陪伴工具的作用域、回执口径、证据句柄 | 本轮提供 V2 原生工具 |
| 作品创建与续写规则 | `astrbot_host/core_reply_tools.py:504` + `companion_v2/conversation/content/authoring_workflow.py:24` | 只有明确要求写作才用创作工具 | 本轮工具目录含创作工具 |

## 3. 宿主核心回复链：user 上下文

`<companion_v2_context>` 是临时 part，不写进历史（`core_reply_perception.py:278-289`），字段级前提如下：

| 字段 | 位置 | 内容与作用 | 注入前提 |
| --- | --- | --- | --- |
| `companion_background` | `core_reply_perception.py:205-207` | 不稳定段的合并文本（第 4 节表格里非 stable 的段） | 任一不稳定段非空 |
| `conversation` | `core_reply_perception.py:227-234` | `current_local_time` + `local_calendar_context`（日期/星期/时段/节日/农历） | `conversation.perception.calendar_enabled` 等开关，见第 8 节 |
| `expression_guidance` | `core_reply_perception.py:212-217` | 感知指令（`CURRENT_TIME_INSTRUCTION`、`CALENDAR_INSTRUCTION`、`ALMANAC_INSTRUCTION`）+ 管理员感知表达补充 | 同上；guidance 非空时也注入 |
| `environment` | `core_reply_perception.py:236-274` | 平台、本轮选择的 Provider/模型、声明的输入模态、是否带图/音/工具 | 三个 perception 开关任意为真 |
| `group_participation_hint` | `core_reply_perception.py:38-58` | 未被 @ 时的参与依据与闸门提示 | 群聊且本轮入站候选未被明确 @ |
| `reply_format_guidance` | `core_reply_perception.py:220-225` | 分段说明 | 同第 2 节分段前提 |
| `original_current_message` | `core_reply_perception.py:193-204` | 裁掉唤醒词前的原文 | 宿主把消息裁短且原文以裁剪后结尾 |

## 4. 宿主核心链的不稳定背景段

这些段默认进 `companion_background`；`stable=True` 的两个（表达偏好、对话附加提示）改走 system。角色档案不再进入对话 system，只作为后台任务的角色参考。

| 段 | 位置 | 作用 | 前提 |
| --- | --- | --- | --- |
| 私聊感知（时间/日历） | `core_reply_context.py:258-290` | 见第 3 节 | 每次私聊 |
| 已发送主动消息 | `core_reply_context.py:294-306` | 承接本窗口自己发过的消息 | 私聊且有投递记录 |
| 本窗口主动灵感清单 | `core_reply_context.py:308-320` | 灵感清单历史，不是新指令 | 私聊且清单非空 |
| 关系档案 | `core_reply_context.py:321` | `RELATIONSHIP_INSTRUCTION`：主观关系理解 | 有已采纳的关系理解 |
| 当前私聊记忆 | `core_reply_context.py:322` | `MEMORY_INSTRUCTION` + 记忆材料 | 记忆服务可用且有材料 |
| 相处背景 | `core_reply_context.py:324` | `RELATIONSHIP_INSTRUCTION` | 有相处材料 |
| 角色生活 | `core_reply_context.py:326` | `LIFE_INSTRUCTION`：活动状态语义（planned/paused/cancelled） | 有生活记录材料 |
| 角色成长 | `core_reply_context.py:327` | `ROLE_GROWTH_INSTRUCTION`：技能与目标的定性进展 | 有成长材料 |
| 角色自我提醒 | `core_reply_context.py:329` | `SELF_REVIEW_INSTRUCTION`：短期表达建议 | 有人工采纳的自评建议 |
| 当前未完成事项 | `core_reply_context.py:331` | `OPEN_ITEMS_INSTRUCTION`：未完话头只在被提及时承接 | 有未完成事项且本轮相关 |
| 角色当前状态 | `core_reply_context.py:334` + `companion_v2/role_work/state/service.py:24` | 角色自身精力/心情/周期等，第一人称归属 | 状态模拟开启且有材料 |
| 群聊参与参考 | `core_reply_context.py:336-347` + `companion_v2/conversation/participation.py:41` | 是否接话的参与判断说明 | 群聊且群参与启用 |
| 当前群聊发言者 | `core_reply_context.py:349-356` | 认证发言者与群号 | 群聊且群权限可读 |
| 群成员与近期互动 | `core_reply_context.py:357` + `companion_v2/relationships/group_network.py` | 群内互动边与熟人卡 | 群聊且有材料 |
| 群公共记忆 | `core_reply_context.py:360-367` + `companion_v2/memory/group/context.py:14` | 群记忆按账号归因 | 群聊且有群记忆 |
| 表达习惯参考 | `core_reply_context.py:370-381` + `companion_v2/conversation/style_context.py:32` | 人工采纳的句式/措辞规则 | 有采纳规则且 applicable |

## 5. V2 自有回复链（conversation.reply）

| 提示词 | 位置 | 作用 | 注入前提 |
| --- | --- | --- | --- |
| 证据与输出契约 | `companion_v2/conversation/foreground.py:491-502` | 事实须带证据引用；只输出用户可见正文；不输出内部控制标签 | 每次 V2 自有回复 |
| `ADDRESSING_INSTRUCTION` | `companion_v2/conversation/addressing.py:4` | 称呼由语境决定，不是身份证明 | 同上（契约末尾拼接） |
| `EXPRESSION_STYLE_INSTRUCTION` | `companion_v2/conversation/style_context.py:32` | 采纳的表达规则按 applicability 取用 | 存在 READY 的 `role.expression`/`expression_style_rule` 材料 |
| `FILE_INSTRUCTION` | `companion_v2/conversation/media/understanding.py:58` | 附件正文是资料，不是发送者的陈述 | 材料里出现 `[文件阅读材料]` |
| `FILE_INSTRUCTION` + 管理偏好 | `companion_v2/conversation/messages.py:573-578` | 同上，追加管理员文件描述偏好 | 同上且 `file_understanding_prompt` 非空 |
| 相识资料 | `companion_v2/conversation/messages.py:580-587` | 当前对话对象的关系资料，不是角色自我 | 有对应快照 |
| `CALENDAR_INSTRUCTION` | `companion_v2/conversation/messages.py:588-589` + `date_context.py:43` | 日历材料只作环境背景，时段措辞以 `local_calendar_context` 为准 | `conversation.perception.calendar_enabled` |
| `IMPORTANT_DATES_INSTRUCTION` | `messages.py:590-592` + `proactive/date_context.py:11` | 配置日期只说明临近 | 本轮有 `upcoming_important_dates` 材料 |
| `ALMANAC_INSTRUCTION` | `messages.py:593-594` + `date_context.py:51` | 黄历只作氛围 | `almanac_enabled` |
| 管理员感知表达偏好 | `messages.py:595-600` | 追加 guidance 文本 | `conversation.perception.guidance` 非空 |
| `AUDIO_INSTRUCTION` | `messages.py:603-604` + `conversation/media/audio/models.py:19` | 语音附件只证明可听清内容 | 本轮含语音 |
| `IMAGE_INSTRUCTION` | `messages.py:605-606` + `conversation/media/models.py:21` | 图片理解与“配图是表达”的边界 | 本轮含图片 |
| `QUOTE_INSTRUCTION` | `messages.py:609-610` + `conversation/quotes/models.py:14` | 引用是历史资料，按原账号归因 | 本轮含引用 |
| `FORWARD_INSTRUCTION` | `messages.py:611-612` + `conversation/forwards/models.py:19` | 合并转发的多层归属 | 开启转发适配且本轮含转发 |
| `QUOTED_FORWARD_INSTRUCTION` | `messages.py:613-614` + `conversation/quotes/forward_models.py:14` | 引用里的转发同样不认证作者 | 同上且引用内含转发 |
| `RELATIONSHIP_INSTRUCTION` / `LIFE_INSTRUCTION` / `ROLE_GROWTH_INSTRUCTION` / `OPEN_ITEMS_INSTRUCTION` | `messages.py:615-622` | 与第 4 节同名段一致 | 对应材料为 READY |
| `STATE_INSTRUCTION` + 状态投影 | `messages.py:623-625` | 角色自身状态与表达底色 | 有状态材料 |
| `KNOWLEDGE_INSTRUCTION` / `MEMBER_CLUE_INSTRUCTION` | `messages.py:626-629` | 知识参考与成员线索 | 命中知识/成员线索 |
| `HISTORY_INSTRUCTION` | `messages.py:630-631` + `delivery/history.py:23` | 已发送背景的解读口径 | 请求快照含 `background_history` |
| `INSPIRATION_INSTRUCTION` | `messages.py:632-633` + `proactive/inspirations.py:17` | 灵感清单是历史资料 | 快照含灵感清单 |
| `TOOL_INSTRUCTION` / `GAME_PLAY_INSTRUCTION` | `messages.py:634-638` + `conversation/tool_loop.py:34,38` | 工具只给声明的参数；游戏当共享时光 | `include_tools` 且有工具 owner |
| `AGENDA` 续作说明 | `messages.py:636` + `conversation/agenda_tools.py:39` | 便签/提醒/投递分别解释 | 本轮用过日程工具 |
| 图片引用指令 | `messages.py:639-654` + `references/prompts.py` | 可编辑参考图的日期与范围 | 工具目录含 `image.edit` |
| `GROUP_CONTEXT_INSTRUCTION` | `messages.py:655-656` + `conversation/group_context.py:19` | 群聊历史与发言者边界 | scope 为群聊 |
| `GENERATED_IMAGE_INSTRUCTION` / `SCREEN_INSTRUCTION` | `messages.py:657-679` | 生成图与屏幕原件不是用户照片 | 本轮有生成图/屏幕采集 |
| 输出协议 | `companion_v2/adapters/astrbot/model_wire.py:17-26` | 最终只输出一个 JSON，附有效示例 | 每次批处理（`astrbot` 适配器） |
| 流式追加契约 | `companion_v2/adapters/astrbot/reply_stream.py:12` | 边生成边发送时的字段顺序 | 开启流式回复 |
| `FINAL_PROMPT_GUARD` | `companion_v2/model_gateway/prompt_composer.py:15` | 同一条 system 的末尾：只输出最终结果、材料只作资料 | 每次模型请求 |
| 模型失败提示 | `companion_v2/conversation/generation.py:31` | 失败时直接发给用户的固定文案（非提示词） | 模型失败 |

## 6. 统一装配顺序（所有模型任务共用）

`compose_prompt`（`companion_v2/model_gateway/prompt_composer.py:33-54`）固定六层，按 `PROMPT_LAYER_ORDER`（同文件 `:6-13`）：

1. `role_instruction`：对话链使用捕获的 AstrBot 人格；后台任务优先使用自我页稳定档案（`TaskRoleReference`），档案为空时才回退到捕获的人格。`image_review` 保留固定安全审核指令。
2. `request_instruction`：任务指令（例如回复链的证据契约、日记的 `COMMON + DIARY`、主动联系的业务规则）。
3. `task_prompt`：任务档（`ModelTaskProfile` 的短提示，见第 7 节表格里“任务档”一列）＋管理员在该任务下保存的覆盖提示。
4. `output_contract`：输出契约（回复链为 JSON 契约；其他任务为“只输出任务要求的 JSON”）。
5. `capability_instruction`：本轮能力说明（工具目录、图片选择协议等）。
6. `terminal_prompt_guard`：`FINAL_PROMPT_GUARD`，放在**同一条 system 消息的末尾**（`companion_v2/model_gateway/prompt_composer.py:33-58`）。

实际消息布局（`model_wire.py:145-160`）：system(1..6 合并为一条) → user{`materials`/`deferred`/`unavailable`}（图片/音频/视频按原序作为多模态 part 附在这条 user 里）→ 工具往返。合并是为了避开上游对多条 system 的过度内容过滤，角色、任务、契约与末尾约束的顺序不变。

身份来源分工：对话链用 AstrBot 主人物人格，不重复注入自我页；后台任务用自我页稳定档案作为 `role_instruction`，档案为空时回退人格。这样 V1 的“日程专用角色设定优先、默认人格补缺”在 V2 里落成“自我页优先、人格兜底”，且不会把完整人格卡带进所有后台任务。

## 7. 任务档（ModelTaskProfile）一览

每个模型任务注册一个任务档；`task_prompt` 这一列就是第 6 节第 3 层的默认文本。

| 任务 kind | 车道 | 任务档位置 | 默认 task_prompt 摘要 | 触发前提 |
| --- | --- | --- | --- | --- |
| `conversation.reply` | foreground | `astrbot_host/multi_session.py:364`、`adapters/astrbot/staging.py:273` | 页面“对话附加提示”（`conversation.reply.prompt`） | AstrBot 核心链每次回复 |
| `conversation.poke_reply` | background | `conversation/pokes.py:28` | 依据角色身份回应戳一戳 | 开启戳一戳模型回复 |
| `proactive.compose` | background | `proactive/service.py:67` | 保持人格，判断是否主动联系 | 主动联系机会开放 |
| `proactive.judge` | background | `proactive/service.py:90` | 发送前人格自检 | 开启 LLM 人格判定 |
| `role.journal.diary` / `dream` / `derive` / `select` | background | `role_work/journal/writing.py:40` | 按本次私人书写任务生成 | 日记日程/派生/选材任务被调度 |
| `role.self_review` | background | `role_work/journal/self_review_service.py:48` | 依据行为证据提出复盘候选 | 自评任务被调度 |
| `role.growth.progress` | background | `role_work/growth/progress.py:41` | 依据完成回执提出进展 | 成长进展任务被调度 |
| `role.expression.learn` | background | `expression/style_service.py:48` | 提取待审阅表达候选 | 表达学习任务被调度 |
| `role.expression.review` / `.contact_review` | background / foreground | `expression/review.py:38` | 检查表达学习是否造成生硬 | 存在已采纳表达规则且开启复核 |
| `role.state.maintain` | background | `role_work/state/maintenance.py:97` | 维护自己的生活状态 | 状态维护/周期巡检任务 |
| `role.reading.consume` | background | `role_work/reader.py:25` | 阅读本次提供的原文片段 | 角色阅读活动被调度 |
| `calendar.plan` | background | `agenda/planning.py:34` | 沿人格安排自己的生活 | 日程规划任务/用户干预 |
| `memory.reflect` | background | `memory/reflection.py:37` | 按原人格整理已授权资料 | 记忆整理日程 |
| `screen.interpretation` / `screen.peek` | background | `screen/interpretation.py:80`、`screen/peek.py` | 谨慎解读授权屏幕 | `screen.interpretation_enabled` 且采集成功 |
| `content.authoring.draft` | background | `conversation/content/authoring_tasks.py:16` | 作品创建/续写 | 用户明确要求写作并使用工具 |
| `conversation.image_narration` | background | `conversation/media/image_narration.py:55` | 逐图转述已授权图片 | 图片理解开启且本轮有图 |
| `conversation.video_narration` | background | `conversation/media/video/narration.py:64` | 直接理解原视频并转述 | 视频理解开启且本轮有视频 |
| `conversation.context_image_caption` | background | `conversation/media/context_caption.py:32` | 群历史原图短摘要 | 群历史图片需要转述 |
| `image_review`（`conversation.image_review`） | background | `conversation/media/review_service.py:41` | 群聊成图安全分类 | `group_nsfw_image_review_mode` 非 off |

## 8. 输出协议与后处理（不是提示词，但决定最终文本）

| 项 | 位置 | 作用 | 适用前提 |
| --- | --- | --- | --- |
| `REPLY_CONTRACT` / `REPLY_CONTRACT_EXAMPLE` | `companion_v2/adapters/astrbot/model_wire.py:17-26` | 只输出一个 JSON，附有效示例 | `output_format=conversation` |
| 可选动作契约 | `model_wire.py:129-138` | 声明本轮可选 `action`（文字/表情图/仅语音/戳一戳/沉默）与其字段约束 | 回复链；动作集合由 `messages.py:680-688` 决定（`voice_enabled`、`reply_images`、`reply_pokes`） |
| 每轮末尾 JSON 约束 | `model_wire.py:143-151` | 再次强调 action/text/claims 的关系与证据句柄 | `output_format=conversation` |
| `REPLY_IMAGE_INSTRUCTION` | `companion_v2/conversation/media/reply.py:17`（`model_wire.py:126-128` 拼接） | 图片选择协议（`sendable_image` 句柄） | `reply_images` 为真 |
| `STREAM_REPLY_INSTRUCTION` | `companion_v2/adapters/astrbot/reply_stream.py:12` | 边生成边发送时的字段顺序 | `streaming_mode="incremental"` 且 `output_format=conversation` |
| 模型分段协议 `DEFAULT_PROMPT` | `companion_v2/conversation/segmentation.py:150` | 用 `{{split_marker}}` 标气泡边界 | `SegmentationPolicy.enabled` 且 `llm_controlled`，且自定义分段提示为空 |
| `FRAMEWORK_GUARD_REPLACEMENT` | `companion_v2/conversation/text_processing.py:138` | 输出命中“框架/系统提示”类泄漏时的替换文案 | `framework_error_guard_enabled` 且命中堆栈特征 |
| `REALITY_PROMISE_GUARD_REPLACEMENT` | `text_processing.py:139` | 把未核实的外部操作说成已完成时的替换文案 | 仅群聊且 `enable_group_reality_promise_guard`，且正则命中非保护片段 |
| `DRAMA_DIRECTION` | `astrbot_host/fish_audio.py:12` | Drama 3 的方括号表演指令与局部语气标签 | 语音策略存在、Provider 为 fishaudio、模型为 `drama-3` 且生成模式为 model_tag/adaptive |
| 拒绝检测词表 | `companion_v2/model_gateway/refusal.py:8-19` | 命中后走 `fallback_on_refusal` 兜底 | 配置 `refusal_terms` / 默认词表 |
| 替换规则 | `companion_v2/model_gateway/replacement_rules.py` | 按配置的时窗、来源对输出做定向替换 | 配置了 `replacement_rules` |
| 表达复核 | `companion_v2/expression/review.py:27` | 学到的句式是否生硬/泄漏内部规则，必要时小幅改写 | `role.expression.review` 任务开启且本轮有采纳规则 |
| `MODEL_FAILURE_NOTICE` | `companion_v2/conversation/generation.py:31` | 模型失败时直接发给用户的固定文案 | 模型失败 |
| 陌生人拒绝文案 | `companion_v2/relationships/private_access.py:44` | 无对话权限时的固定回复 | 未授权对话 |

## 9. 主动联系（proactive.compose）

### 9.1 系统指令的拼接顺序

`request_for` 把下列文本拼成**一个** instruction 字符串（`companion_v2/proactive/prompts.py:384-408`），顺序即下表：

| 提示词 | 位置 | 作用 | 注入前提 |
| --- | --- | --- | --- |
| `INSTRUCTION` | `proactive/prompts.py:74` | 主动联系的业务总则：可选话题、时间措辞、事实边界、JSON 输出 | 每次主动联系 |
| `RHYTHM_INSTRUCTION` | `proactive/prompts.py:88` | 送达节奏只是软线索，不是配额目标 | 有 `rhythm` 快照且非管理员测试 |
| 主动表达模板 | `proactive/prompt_templates.py:6`，页面字段 `proactive.contact.expression_prompt` | 把动机写成自然消息、开头/收尾习惯 | 每次（默认模板，可被页面覆盖） |
| `OFFICIAL_ALERT_INSTRUCTION` | `proactive/prompts.py:183` | 官方预警的适用范围核对与引用 | 本轮来源含官方预警 |
| `PURPOSE_INSTRUCTIONS[purpose]` | `proactive/prompts.py:100-116` | 十五个联系目的各自的表达方向 | 每次（取当前目的） |
| `ORIGIN_INSTRUCTIONS[...]` | `proactive/prompts.py:117-137` | 自动来源（新闻/天气/余额/日历/活动/日记/屏幕等）的事实边界 | 由头或来源命中对应 key；官方预警与部分新由头没有对应条目，见 14 节 |
| 新闻分享模板 | `proactive/prompt_templates.py:9`，字段 `news_sharing_prompt` | 新闻当“值得分享的一段话”，不改写成早报 | 目的为新闻或来源含资讯 |
| `GREETING_INSTRUCTION` | `proactive/prompts.py:96` | 问候窗口可作由头，不套早安/午安 | 目的为问候或存在问候事件 |
| `INSOMNIA_GREETING_INSTRUCTION` | `proactive/prompts.py:99` | 夜间低压力：不报时、不报日期和星期 | 问候事件 kind 为 `insomnia_night`（追加在上一行之后） |
| `REACTION_INSTRUCTION` | `proactive/prompts.py:149` | 表情检索意图 | 本轮可附表情图片 |
| `VOICE_INSTRUCTION` | `proactive/prompts.py:158` | 主动语音的截取与语气要求 | 主动语音开启且冷却允许 |
| `POKE_INSTRUCTION` | `proactive/prompts.py:167` | 戳一戳作为提醒方式 | 本轮允许且支持戳一戳 |
| `MEMORY_INSTRUCTION` | `proactive/prompts.py:172` | 记忆材料与正文一致、不是新输入 | 有封存记忆材料 |
| `EXPRESSION_STYLE_INSTRUCTION` | `conversation/style_context.py:32` | 采纳的表达规则 | 有采纳规则材料 |
| `DIALOGUE_INSTRUCTION` | `conversation/private_history.py:20` | 近期私聊按轮理解 | 有 `recent_private_dialogue` |
| `LENGTH_INSTRUCTION` / `NEWS_LENGTH_INSTRUCTION` | `proactive/prompt_templates.py:12,14` | 按强度档位控制字数（`{chars}` 占位） | 有长度档位；新闻分享用新闻版 |
| `CONTEXT_INSTRUCTION` | `delivery/history.py:23` | 已发送背景按轮、按原时间理解 | 每次 |
| `DEDUP_INSTRUCTION` + 窗口/锚点参数 | `proactive/prompts.py:139` | 只按 `dedup_candidate`/`last_confirmed_reply` 判重 | 去重启用且该目的/来源命中 `dedup_policies` |
| 管理员测试说明（三档） | `proactive/prompts.py:352-374` | 合成演练 / 真实投递链路 / 默认测试，各自的事实边界 | `administrator_test` 为真，按 `diagnostic_origin` 分支 |
| `DIAGNOSTIC_PRESENTATION_INSTRUCTION` | `proactive/prompts.py:178` | 测试标识只在管理端呈现 | 管理员测试 |
| 图片验收说明 | `proactive/prompts.py:378-383` | 主动图片验收的写法与失败口径 | 管理员指定 `diagnostic_image_intent` |
| 主动风格补充 | 页面字段 `proactive.contact.style_prompt` | 只影响表达，不改事实 | 该字段非空 |

### 9.2 材料（user 侧 JSON）

| 材料 | 位置 | 作用 | 前提 |
| --- | --- | --- | --- |
| `contact_intent` | `proactive/prompts.py:285-301` | 目的、由头、时间标签（`local_now`/`local_date`/`local_time`/`weekday`/`is_weekend`/`period`/`timezone`）、`recent_timing`、`rhythm` | 每次主动联系 |
| `communication_state` | `proactive/prompts.py:312-314` | 上次用户发言后已送出的联系数、未解决联系数 | 非管理员测试且有交互快照 |
| 来源证据（`contact_evidence`） | `proactive/prompts.py:302-311` | 新闻/天气/余额/日历/日记/活动等原始记录，按 owner 归一（去掉 `_receipt`） | 本轮有对应来源记录 |
| 记忆、表达规则 | `proactive/prompts.py:318-323` | READY 的记忆与表达材料 | 服务接线且材料 READY |
| `recent_private_dialogue` | `proactive/prompts.py:324-326` | 最近一轮私聊（同轮分段合并） | 有近期对话 |
| `dedup_candidate` / `confirmed_background_message` | `proactive/prompts.py:327-331` | 已送达的主动消息与回复，用于判重与承接 | 有投递历史；去重启用时窗口内标 `dedup_candidate` |
| `last_confirmed_reply` | `proactive/prompts.py:332-335` | 最近一条已确认回复 | 提供 `last_reply` 且去重规则命中 |

### 9.3 由头与材料文本（不是 system，但会引导输出）

| 文本 | 位置 | 作用 | 前提 |
| --- | --- | --- | --- |
| 日问候由头 | `proactive/greetings.py:176` | 本地日期 + 早/午/晚窗口，窗口只提供时机 | `enable_daily_greetings`、落在 `greeting_*_window`、purpose 未禁用 |
| 夜间由头 | `proactive/greetings.py:171` | 夜间低压力问候，不报时、不报日期和星期（可附睡眠线索） | `allow_insomnia_night_message`、落在夜间切片、按配置可能要求睡眠线索 |
| 日期由头 | `proactive/date_events.py:104` | 今天/临近的配置标签，声明不证明个人相关 | `date_checkin_enabled`、到点 45 分钟内、当日有条目 |
| 饭点由头 | `proactive/meal_events.py:59` | 当前餐次只作轻话题 | `meal_checkin_enabled`、落在午餐/晚餐窗口 |
| 日记由头 | `proactive/journal_events.py:194` | 新日记/梦境可作近况，梦不是现实 | 有保存/恢复回执且未过期、life 未禁用 |
| 约定由头 | `proactive/calendar_events.py:187` | 人类已接受的约定即将开始 | 记录为 accepted commitment 且落在提前/滞后窗口 |
| 天气由头 | `proactive/weather_events.py:139` | 角色所在地一次观测，不代对方所在地 | `weather_checkin_enabled`、到点 45 分钟内、观测未过期 |
| 天气变化由头 | `proactive/weather_change_events.py:155` | 两次观测明显不同，只在相关时提 | `weather_change_enabled`、样本间隔达标、显著变化且过冷却 |
| 官方预警由头 | `proactive/official_alert_events.py:174` | 只基于预警原文提示风险 | 私聊 + 开关 + 有目标辖区 + 未过期、严重度过滤后仍有候选 |
| 余额由头 | `proactive/balance_events.py:220` | 服务经费偏低，适合时才提、不催充值 | `automatic_enabled` + `balance_awareness_enabled` + 冷却/升级逻辑产出事件 |
| 资讯由头 | `proactive/news_events.py:688` | 挑一个有出处的亮点分享 | 资讯分享开启、抓取到未重复条目 |
| 识屏由头 | `proactive/screen_events.py:101` | 已授权的一次观察，可说成“之前看到” | `screen_glance_enabled` + 自动联系 + 未超限额/冷却 |
| 活动由头 | `proactive/events.py:187` | 阅读完成可分享片段；睡眠结束可看近况 | 有 role.life 完成回执且在时效内 |
| 关系（slot/source）由头 | `proactive/relationship_events.py:67-70` | 关系窗口只表示表达方向 | 候选/来源命中且对应 purpose 未禁用 |
| 节奏由头（chat/burst/followup/未完话头） | `proactive/scheduling.py:519`、`:491`、`:883`、`:961` | 低压力开场、同阵第 N 条、隔一段再轻接、未完话头承接 | 分别由窗口、`enable_proactive_burst`、`contact_followup_after_minutes`、agenda memo 未被认领决定 |
| 诊断/合成由头 | `proactive/scheduling.py:658`、`:770` | 管理员测试的开场说明；合成演练的“本轮情境” | `administrator_test`（合成需 route_id 为 `synthetic_event`） |
| 来源材料的 `meaning` 字段（9 处） | `weather_events.py:27`、`weather_change_events.py:26`、`official_alert_events.py:23`、`balance_events.py:22`、`news_events.py:28`、`events.py:79,88`、`journal_events.py:90-92`、`calendar_events.py:84`、`screen_events.py:87` | 在来源 JSON 里声明该材料的事实上限 | 对应来源记录被作为 `contact_evidence` 注入时 |
| 新闻上下文说明 | `proactive/news_context.py:28`、`:35` | 公开资讯只支持写明内容；无来源时如实说明 | 回复路径有/无可用资讯（不是主动联系路径） |
| 灵感清单说明 | `proactive/inspirations.py:17` | 清单是历史资料，不是新指令 | 回复路径快照含灵感清单（不是主动联系路径） |
| 重要日期材料说明 | `proactive/date_context.py:11` | 标签只说明临近 | 回复路径注入 `upcoming_important_dates`（不是主动联系路径） |

### 9.4 发送前自检与表达复核（两条独立请求）

| 提示词 | 位置 | 作用 | 前提 |
| --- | --- | --- | --- |
| `INSTRUCTION` | `proactive/persona_judge.py:35` | 用当前人格判定这条消息是否可发 | 开启 LLM 人格判定、目的不在豁免名单、缓存未命中、判定档已注册 |
| `OUTPUT_FORMAT` | `proactive/persona_judge.py:32` | 只输出放行/拦下的 JSON | 同上 |
| 判定材料 `proactive_draft` | `proactive/persona_judge.py:75-79` | 把 purpose、正文、时间、置信度、note 交给判定模型 | 同上 |
| 表达复核 `PROMPT` | `expression/review.py:27` | 学到的句式是否生硬/泄漏内部规则，必要时小幅改写 | 有正文 + 非管理员测试 + 双重 `style_review_enabled` + 有采纳规则 + 复核档已注册 |
| 复核材料 `expression_review_input` | `expression/review.py:134-139` | 正文、受保护语句与被检查规则 | 同上 |

### 9.5 service 层追加的三类指令

`service.py:691-696` 在 `request_for` 之后再拼一次 instruction：

| 指令 | 位置 | 作用 | 前提 |
| --- | --- | --- | --- |
| 模型分段协议 | `conversation/segmentation.py:150`（`{{split_marker}}`） | 让模型用标记划分气泡 | 分段 `enabled` 且 `llm_controlled`（默认关闭，故默认不注入） |
| 回复级风格与拟人块 | `conversation/text_processing.py:253-265` | `conversation.presentation.style_prompt`；`GUIDED_PROMPT`/自定义风格正文；日常表达块 | 字段非空 / `prompt_style_mode` 为 guided 或 constraint / 开启日常表达 |
| 新闻排版 | `proactive/prompts.py:191-207` | 单条、模型分段、规则分段三种排版要求 | 目的为新闻或来源含资讯 |


## 10. 角色生活、成长、规划、识屏与创作

装配位置同第 6 节：人格层→system#1，业务层→system#2（下表“system”即此层），任务档一句话与 `model.<kind>.prompt` 覆盖→同一条 body，材料→user JSON。

### 10.1 日记、梦境与私人书写

| 名称 | 位置 | 作用 | 注入前提 |
| --- | --- | --- | --- |
| `COMMON` | `role_work/journal/prompts.py:11` | 私人书写共同规则：材料是资料，旧日记不是多份证据 | 日记/梦境/选材任务；派生任务不注入 |
| `DIARY` | `role_work/journal/prompts.py:19` | 日记写法与 JSON 契约（body/form/recall/afterglow） | kind=DIARY，`role.life.diary_enabled` 日程或管理端动作 |
| `DREAM` | `role_work/journal/prompts.py:24` | 梦境残留写法与 recall/afterglow 契约 | kind=DREAM，睡眠回执后触发或管理端动作 |
| `dream_instruction` | `role_work/journal/prompts.py:59` | 把梦境题材/混搭/亲密/余韵配置转成写作补充 | kind=DREAM 且对应配置非默认；默认 `auto` 下不注入 |
| 日记风格补充 | `role_work/journal/writing.py:375` | 风格参考句 | `role.life.diary_style_prompt` 非空（日记与梦境追加） |
| `DERIVE` | `role_work/journal/prompts.py:30` | 从已保存日记/梦境提取衍生材料 | 父记录已保存且 `role.life.derive_enabled`（管理端绕过） |
| `SELECT` + 上限句 | `role_work/journal/selection.py:12`、`:55` | 选材任务只给 id、不改写材料 | 候选超上限（约 64 条 / 48000 字节） |

### 10.2 状态、周期、心情与作息

| 名称 | 位置 | 作用 | 注入前提 |
| --- | --- | --- | --- |
| `INSTRUCTION` | `role_work/state/service.py:24` | 状态模拟的主语、回执与隐私边界（状态维护时作为前缀） | `role.state.enabled` 且维护到期/被干预 |
| 状态维护输出协议 | `role_work/state/maintenance.py:35` | 维护任务的字段与回执边界 | 同上 |
| 技能安排提示 / 表达偏好 / 关闭字段说明 / 干预后缀 | `role_work/state/maintenance.py:65`、`:101`、`:118`、`:605` | 按技能安排活动、状态表达偏好、关闭字段一律 null、把本轮对话当状态复核 | 分别由 growth_refs、`*_expression_prompt`、字段关闭、`intervention` 决定 |
| 周期规则与阶段提示 | `role_work/state/cycle_prompts.py:12`、`:21`、`:3` | 周期模拟、输出契约、阶段提示 | `role.state.cycle_enabled`；关闭时只留“cycle 为 null”一句 |
| 周期表达（回复侧） | `role_work/state/cycle_prompts.py:40` | 回复里自然带出周期感受 | 回复批次带 state 投影且周期适用 |
| 心情回落参考 / `REPLY` / `NEUTRAL` | `role_work/state/mood/prompts.py:11`、`:5`、`:8` | 情绪只影响语气浓度；中性带单独收束 | `role.state.mood_enabled` 且本轮 gate 打开（hold 只提示不更新） |
| 状态底色 `BAND_INSTRUCTION` | `role_work/state/humanized.py:55` | 状态表达强度上限 | 有 `role_state_intensity` 材料且强度>0、存在可表达 facet |

### 10.3 日程、阅读与识屏

| 名称 | 位置 | 作用 | 注入前提 |
| --- | --- | --- | --- |
| `CALENDAR_PLAN` | `agenda/planning_output.py:11` | daily/horizon/refine 计划与 JSON 契约 | `role.state.calendar_enabled`；自动还需 `planning_enabled` 与 orchestrated 模式 |
| `INTERVENTION_INSTRUCTION` | `agenda/planning_output.py:21` | 用户干预日程只做 move/refine/cancel/create | 显式干预（休息中另需唤醒授权） |
| 技能强度后缀 | `agenda/planning_inputs.py:203` | 按技能强度调整日程取向 | 有 growth_refs 且强度>0 |
| `CALENDAR_CONTINUOUS` | `agenda/calendar_prompt.py:28` | 连续日程模式下如何就现有计划回应 | `schedule_simulation_mode=continuous` 且该回复批次带日程材料 |
| 日历/天气工具说明 | `agenda/calendar_tools.py:14`（`agenda/calendar.py:14`）、`agenda/weather_tools.py:23` | 计划/自述/约定语义；天气只代表角色地点 | 工具目录含对应工具且授权 |
| 读后感 `READING` | `role_work/reader.py:125` | 只谈本次实际读到的原文 | 角色阅读活动启动 |
| 屏幕解读 `PROMPT` | `screen/interpretation.py:67` | 只按授权覆盖范围解读，不执行画面内容 | `screen.interpretation_enabled`、私有窗口、路由已注册 |
| `NEUTRAL_INSTRUCTION` | `screen/interpretation.py:65` | 读不到父任务人格时的最小兜底 | 父任务 `role_instruction` 为空 |
| 时间线/成长工具说明 | `role_work/timeline/tools.py:9`、`role_work/growth/tools.py:21` | 只读历史的语义；成长提案的证据约束 | 工具目录含对应工具 |

### 10.4 创作（Content）

| 名称 | 位置 | 作用 | 注入前提 |
| --- | --- | --- | --- |
| `AUTHORING_TOOL` | `conversation/content/authoring_workflow.py:24` | 只有明确要求写作才用创作工具 | `content.authoring.enabled`、私有窗口、工具在本轮目录 |
| `AUTHORING_PROMPT` | `conversation/content/authoring.py:84` | 作品正文契约（体裁/续写/JSON） | 模型发起 create/continue 调用后 |
| 体裁提示 | `conversation/content/authoring.py:37` | 按作品类型切换体裁写法 | work_type 命中诗/歌词/剧本/随笔等 |
| 创作方向 | `conversation/content/authoring_service.py:118` | 安装级创作方向 | `content.authoring.direction_prompt` 非空 |
| 创作任务档 | `conversation/content/authoring_tasks.py:11` | 一句话任务说明（可被 `model.content.authoring.draft.prompt` 覆盖） | 每次创作调用 |

### 10.5 自评与成长进展

| 名称 | 位置 | 作用 | 注入前提 |
| --- | --- | --- | --- |
| `ROLE_SELF_REVIEW` | `role_work/journal/self_review_service.py:40` | 自评候选的规则与证据约束 | 后台自评到期（24h 窗口、≥2 条证据、私有窗口） |
| `ROLE_GROWTH_PROGRESS` + 技能域后缀 | `role_work/growth/progress.py:30`、`:215` | 依据完成回执提出成长进展 | 存在活跃目标且回执匹配；后缀需自定义技能域 |
| `INSTRUCTION`（自评模块） | `role_work/journal/self_review.py:18` | 完整自评指令 | **未使用**，见第 14 节 |

### 10.6 该域材料（user JSON）

| 材料 | 位置 | 作用 | 前提 |
| --- | --- | --- | --- |
| `writing_date` | `role_work/journal/prompts.py:83` | 目标日期/时区/种类 | 日记/梦境书写必带 |
| `writing_materials` | `role_work/journal/prompts.py:89` | 当日素材（id/kind/text/origin） | 非派生书写 |
| `recent_journal` / `parent_journal` | `prompts.py:90` / `:86` | 最近 8 篇旧书写 / 被提取原文 | 有历史 / 仅 DERIVE |
| `material_candidates` | `role_work/journal/selection.py:51` | 选材候选清单 | 仅 SELECT |
| `maintenance_basis` | `role_work/state/maintenance.py:587` | 时间、上一轮状态、周期、日历、活动、睡眠回执 | 每次状态维护 |
| `reported_conversation` | `role_work/state/maintenance.py:352` | 本窗口对角色说的话（≤4096 字） | 有有效输入来源 |
| `planning_material` / `calendar_context` / `current_weather` / `planning_preferences` | `agenda/planning_inputs.py:238`、`:22/:53`、`:246`、`:240` | 规划材料、当日节日、角色地点天气、管理员计划偏好 | 计划任务；天气需开关与取数成功 |
| `role_self_review_evidence` | `role_work/journal/self_review_service.py:177` | 复盘窗口与证据目录 | ≥2 条证据 |
| `completed_receipt_and_growth` | `role_work/growth/progress.py:211` | 完成回执 + 活跃目标 | 有匹配回执与目标 |
| `original_reading_span` / `screen_interpretation_original` | `role_work/reader.py:122`、`screen/interpretation.py:355` | 只按本次原文/覆盖范围理解 | 对应任务 |
| 材料语义前缀 | `role_work/journal/material_producer.py:285,291`、`reading_materials.py:58,64`、`screen/journal_materials.py:75`、`journal/triggers.py:93` | 给材料加“创作原文/收存未读/本次已读/屏幕观察/睡眠回执”限定 | 材料进入日记收集时 |

## 11. 记忆、关系、表达与媒体材料

这些常量既作为回复链的材料说明，也出现在独立小任务里；门控写在“注入前提”。

| 名称 | 位置 | 作用 | 注入前提 |
| --- | --- | --- | --- |
| `MEMORY_INSTRUCTION` | `memory/prompts.py:5` | 记忆是带来源与条件的资料，当前表达优先 | 本轮有记忆材料 |
| `REFLECTION_INSTRUCTION` | `memory/prompts.py:16` | 反思任务：有来源地更新认识，允许不修改 | `memory.reflect` 任务被调度 |
| 群记忆口径 | `memory/group/context.py:14` | 群记忆按原账号归因，不是全群共识 | 有群记忆材料 |
| 群内词义口径 / 候选抽取 | `memory/group/slang.py:15`、`:22` | 释义是推断；从本群发言抽候选词 | 群内词义功能启用 |
| `MEMBER_CLUE_INSTRUCTION` | `knowledge/members.py:30` | 成员线索只按本轮认证账号匹配 | 命中成员线索 |
| 表达学习 | `expression/learning.py:130`、`expression/style_service.py:41` | 只观察可借鉴的句式与写法，不推断身份 | `role.expression.learn` 任务被调度 |
| 表达复核 | `expression/review.py:27` | 学到的句式是否生硬或被照抄，必要时小幅改写 | 有采纳规则 + 双重 `style_review_enabled` |
| 表情呈现意图 | `expression/reply_intent.py:16` | 完成正文后判断是否配图 | 回复启用角色图库表达 |
| 表情选图与检索意图 | `expression/reactions.py:23`、`:26` | 按交流动作选图；检索描述写法 | 工具目录含表情检索 |
| 图片转述 `PROMPT` | `conversation/media/image_narration.py:40` | 逐图转述，供回复模型理解 | `image_understanding_*_enabled` 且本轮有图 |
| 视频转述 `PROMPT` | `conversation/media/video/narration.py:37` | 直接理解原视频并转述 | `video_understanding_enabled` 且 scope 允许 |
| 群历史图摘要 `PROMPT` | `conversation/media/context_caption.py:22` | 为群历史原图生成短摘要 | 图片理解开启且群历史有原图 |
| 媒体材料口径（图/音/视频帧/引用/转发/文件） | `media/models.py:21`、`media/audio/models.py:19`、`media/video/models.py:26`、`quotes/models.py:14`、`forwards/models.py:19`、`quotes/forward_models.py:14`、`media/understanding.py:58`、`:64` | 图片是表达、语音只证明可听清、转发不认证作者、文件正文不是陈述 | 对应材料出现在本轮请求里 |
| 阅读偏好参考 | `library/preferences.py:21` | 口味参考，不是本轮要求 | 有阅读偏好材料 |



## 12. 原生拓展与宿主侧

这一域大多是扩展自己的固定 system 提示，与主对话人格并行；除特别注明外都要求对应扩展已启用。

### 12.1 图片生成与审核

| 名称 | 位置 | 作用 | 注入前提 |
| --- | --- | --- | --- |
| image 工具字段说明 | `astrbot_host/native_extensions/image_native/adapter.py:56` | 说明 `prompt`/`negative_prompt` 由模型写画面描述与负向词 | image 扩展启用且本轮工具目录开放生图 |
| 生图提示词编排器 | `image_native/comfyui_prompts.py:164` | 把要求重写并拆分进 ComfyUI 工作流各文本槽 | 需 `comfyui` 后端且传入模型调用；**仓库内无调用方**，当前不注入 |
| 工作流映射分析 prompt | `image_native/comfyui_service.py:133` | 推断哪些节点是动态文本/图片/尺寸 | `ComfyUIService.analyze` 路径，同样无调用方 |
| 群图安全审核 `_INSTRUCTION`+`_RULES`+`_BOUNDARIES` | `companion_v2/conversation/media/review.py:68`、`:80` | 判定群聊成图能否公开，输出固定标签 | 群聊成图待发 + 审核模式非 off + 有视觉 Provider |
| 群图审核补充规则 | `conversation/image_review/group_nsfw_image_review_custom_prompt` | 管理员补充规则 | 该字段非空 |

图片没有“人格+风格+场景”拼好的固定常量：画面描述由对话模型写进工具参数，风格/质量由工作流固定槽保留。

### 12.2 直播（live）

| 名称 | 位置 | 作用 | 注入前提 |
| --- | --- | --- | --- |
| `bili_live_auto_reply_system_prompt` | `live/model_methods.py:318` | B 站自动回应的直播身份与简洁语气 | `bili_live_auto_reply_enabled` 且原生路径失败回退到直接 LLM |
| 框架式自动回应 prompt | `live/model_methods.py:378` | 把弹幕、身份、工具禁令交给主 Agent | `bili_live_auto_reply_mode=native` 且回退路径失败 |
| `twitch_auto_reply_system_prompt` | `live/twitch_mixin.py:382` | Twitch 自动回应的身份与语言规则 | `twitch_auto_reply_enabled` + 已绑定回应会话 |
| 身份说明 | `live/settings_methods.py:163-164` | 区分主播本人/助理，禁止冒充与替主播承诺 | 进入自动回应时按 `bili_live_reply_identity_mode` 取一条 |
| 直播回复经验守卫 | `live/model_methods.py:695` | 贴当前弹幕、别像老熟人重逢、不解释插件 | 本批有事件（礼物/SC 另有致谢分支） |
| 直播记忆/关系/延续上下文 | `live/model_methods.py:799`、`:621`、`:758` | 可承接的黑话与高光；已关联观众的称呼与风格；同一观众本场前文 | 分别由 `live_memory_*`、`live_relationship_context_enabled` 且成员已认证、本场事件多于本批决定 |
| Live2D 标签块 | `live/host_events.py:61` | `<l2d:标签>` 的格式、上限与白名单 | `autonomous_l2d_enabled` 且配置了标签 |
| Soullink 表演块 | `live/soullink_mixin.py:875` | `<soullink>{…}` 情绪意图 JSON 与 VAD 规则 | `soullink_enabled` 且 `soullink_prompt_intent_enabled` |
| 直播间实时信息 | `live/host_events.py:49` | 最近弹幕作可引用不可伪造的实时材料 | `bili_live_inject_enabled` + 已连接 + 事件类型命中 |
| 当前 V2 角色人设 | `live/native_context.py:78` | 注入已验证人格，禁止带私聊记忆 | 回应目标可解析为已验证 V2 私聊目标 |
| VTS 授权入口 | `live/activity_methods.py:1667` | 防模型编造授权步骤 | 消息恰为 `/vts_auth` |

### 12.3 一起（together）

| 名称 | 位置 | 作用 | 注入前提 |
| --- | --- | --- | --- |
| `BASE_REALTIME_PROMPT` | `together/common.py:70` | 实时共处的基础说话规则、禁输出语音标签 | 房间内任一轮对话 |
| 通话接通/房间上下文 | `together/common.py:79`、`:83` | 说明通话已接通，禁假装听到麦克风 | `mode=call`（work/watch 接通时也加 79） |
| 摄像头单帧说明 | `together/common.py:81` | 单帧只作可见线索、不推断身份 | 通话 + 本轮带帧 + 有视觉 Provider |
| 挂断/静默判断 | `together/common.py:87`、`:93`、`:97`、`:99` | 是否自然挂断；静默时是否开口或结束 | `call_active` + `model_hangup_enabled` + 静默到 `call_idle_seconds`（三选一由 `call_proactive_enabled` 决定） |
| 双语语音块 | `together/common.py:86` | 要求 `<pc_tts>` 外语语音块 + 可见正文 | 通话 + `speech.direct_multilingual_tts` + TTS 语种非中文 |
| 客户端时间 | `together/common.py:84`、`:85` | 用浏览器上报时间，缺失时禁猜 | `mode=call`（15 分钟内有上报用 84） |
| `STT_CORRECTION_PROMPT` | `together/common.py:74` | 只纠明显转写错误，返回 JSON | `speech.stt_correction_enabled` 且转写可疑 |
| 工作协同/共看/共读 | `together/common.py:88`、`:92`、`:102`、`:111` | 推进目标、证据口径；共看的不剧透与不逐帧；共读的已读边界 | `mode=work` / `watch` / `novel` |
| 观影开麦判断与知识卡 | `together/common.py:106`、`:109` | 是否值得评论；生成观前背景卡 | `watch.auto_comment` / `watch.prepare_knowledge` |
| 观影记忆 | `together/common.py:110` | 把已播片段整理成临时共同记忆 | 新事件≥6 或到刷新间隔或影片结束（**写入开关恒为假**，见 14 节） |
| 房间共同参与者 | `together/model_methods.py:456` | 区分成员发言、禁跨成员授权 | 房间存在 guest 成员 |
| 帧转述 | `together/activity_methods.py:243-244` | 把画面转客观描述再交给聊天模型 | 有视觉 Provider 且本轮带帧 |
| 触发说明 | `together/media_methods.py:339` | 本次开口的触发原因 | watch 帧触发 |

### 12.4 游戏（game）

| 名称 | 位置 | 作用 | 注入前提 |
| --- | --- | --- | --- |
| `_generate_persona_text` system | `game/model_methods.py:104` | 以人格口吻对游戏事件说话 | 房间存在且能取到人格/背景 |
| 网页房间对话 system | `game/rooms_methods.py:469` | 只接当前发言、不声称外部发送 | 玩家在游戏网页房间发言 |
| 你画我猜猜图 | `game/activity_methods.py:258` | 只按画面线索给一个答案 | 猜图房间 + 画布 + 视觉 Provider |
| 海龟汤分类 | `game/activity_methods.py:308` | 判断是继续海龟汤还是转普通聊天 | 海龟汤房间内被调用 |
| 海龟汤出题/校验/裁判 | `companion_v2/games/turtle_soup_ai.py:28`、`:57`、`:76`、`:100`、`:130` | 出题、自洽校验、提问判定、终局、玩家出题 | 对应回合 |
| 悔棋/再来一局 | `game/activity_methods.py:330`、`:405` | 决定是否接受并回一句 | 玩家点击对应按钮 |

### 12.5 现实（reality）

| 名称 | 位置 | 作用 | 注入前提 |
| --- | --- | --- | --- |
| 提醒任务指令 | `reality/host_events.py:76` | 用原人格表达提醒、只调一次专用工具 | Cron 现实提醒事件可执行（取消/结束用 `:72`） |
| 管理工具说明 | `reality/host_events.py:100` | 只按本轮明确请求操作现实设置 | 私聊 + 命中管理请求且已挂工具 |
| 摄像头观察指令 | `reality/host_events.py:110` | 先取实际观察再回应，不猜画面 | 明确请求摄像头且已有工具 |
| 单帧视觉理解 | `reality/reality_touch_camera.py:796` | 输出场景摘要、目的回答、可见证据、不确定项 | 取到帧 + 配置 `vision_provider_id` 且支持图片 |
| 叫醒话术 / 反馈分类 | `reality/wakeup_alarm.py:1115`、`:1318` | 生成起床话术；把回应判为 awake/snooze/stop/other | 闹钟到时；规则未命中且文本≤120 字 |
| 最近私聊连续性 | `reality/host_context.py:113` | 提供称呼与语气连续性，旧话不是新请求 | 上下文生成时且有近 8 条历史 |
| 手机地点 thought | `reality/host_delivery.py:139` | 作主动联系意图材料，判断是否自然提起 | 已配对手机上报地点 + 快照授权（端到端是否常触发未确认） |

### 12.6 人格工坊、空间与其它

| 名称 | 位置 | 作用 | 注入前提 |
| --- | --- | --- | --- |
| 人格标准化 | `astrbot_host/persona_workshop_api.py:922` | 把来源人格/问卷整理成可审核草稿 | 管理台请求且模型可用 |
| 场景试答 | `persona_workshop_api.py:946` | 生成贴合/差异/对照三种口吻 | 管理台请求且草稿已审核 |
| 风格归纳 | `persona_workshop_api.py:958` | 由试答归纳对话风格规则 | 管理台请求 |
| `CONVERSATION_GUIDANCE` | `companion_v2/qzone/topic_prompt.py:32` | 发布主题/场景目录与置信度口径 | 工具目录含 `qzone.post.publish`（扩展启用 + 授权 + 会话同意 + 凭据可用） |
| 空间发布工具说明 | `qzone/capability.py:116` | 先定主题场景再写正文 | 同上 |
| 分段 `NATIVE_PROMPT`/`PRIVATE_PROMPT` | `astrbot_host/core_reply_segmentation.py:24`、`:30` | 宿主回复的气泡边界协议 | 分段 `enabled`+`llm_controlled` 且非流式 |
| 表情呈现意图 | `astrbot_host/core_reply_reaction_delivery.py:39`（正文 `expression/reply_intent.py:16`） | 要求附表情呈现意图标签 | `reaction_available` 为真 |
| 呈现设置块 | `astrbot_host/core_reply_media_prompt.py:52` | 覆盖最终 system 的语音/表情格式 | 有语音策略或表情呈现 |

## 13. 可配置提示词字段

字段分三处决定：`_conf_schema.json`（安装级默认值）、各领域 `FieldDefinition`（角色/关系/窗口覆盖）、`prompt_sections`（页面可编辑标记，只影响 UI）。

### 13.1 回复与表达

| 字段 | 默认 | 作用 | 覆盖层级 | 消费点 |
| --- | --- | --- | --- | --- |
| `conversation.reply.prompt`（model.conversation.reply.prompt） | 空/任务提示 | 对话任务补充提示，追加进核心 system | 安装/角色/窗口 | `core_reply_context.py:232-248`（宿主）、`model_wire.py:140` |
| `conversation.presentation.style_prompt` | 空 | 表达风格补充 | 角色+窗口 | `text_processing.py:253`、`core_reply_context.py:224-228` |
| `conversation.presentation.prompt_style_mode` | `free` | free 不注入 / guided 注入 `GUIDED_PROMPT` / constraint 注入自定义正文 | 角色+窗口 | `text_processing.py:256-262` |
| `conversation.presentation.enable_humanized_expression` | false | 日常表达块开关 | 角色+窗口 | `text_processing.py:262-265` |
| `conversation.presentation.humanized_expression_prompt` | `DEFAULT_PROMPT` 全文 | 日常表达正文（constraint 档复用为风格正文） | 角色+窗口 | `humanized_expression.py:73,78` |
| `conversation.presentation.prompt` | 空→`DEFAULT_PROMPT` | 模型分段协议 | 角色+窗口 | `segmentation.py:214-215` |
| `conversation.presentation.enabled` / `llm_controlled` / `streaming_mode` | true / false / buffered | 分段开关、是否让模型决定、是否流式 | 角色+窗口 | `segmentation.py:208-213`、`model_wire.py:122` |
| `conversation.presentation.file_understanding_prompt` | 空 | 文件说明方式偏好 | 角色+窗口 | `messages.py:574-577` |
| `conversation.presentation.video_understanding_prompt` | 空 | 视频转述偏好 | 角色+窗口 | `media/video/narration.py:96,187` |
| `conversation.presentation.private_image_vision_custom_prompt` | 空 | 私聊看图补充要求 | 角色+窗口 | `media/image_narration.py:142,173,244` |
| `conversation.presentation.voice_extra_prompt` | 空 | 语音选句/朗读补充 | 角色+窗口 | `voice_reply.py:192`、`generation.py:260` |
| `conversation.presentation.voice_primary_mention_prompt` | 空 | 被 @ 时的定向语音提示 | 角色+窗口 | `multi_session.py:818-823` |
| `conversation.image_review.group_nsfw_image_review_custom_prompt` | 空 | 群图审核补充规则 | 角色+窗口 | `media/review.py:139-142` |
| `conversation.perception.guidance` | 空 | 管理员感知表达偏好 | 角色+窗口 | `messages.py:598-600`、`core_reply_perception.py:213-217` |
| `conversation.group_participation.group_behavior_guidance` | 空 | 群互动习惯学习偏好 | 安装/角色/窗口 | `behavior/service.py:127-129` |

### 13.2 主动联系

| 字段 | 默认 | 作用 | 覆盖层级 | 消费点 |
| --- | --- | --- | --- | --- |
| `proactive.contact.expression_prompt` | `EXPRESSION_INSTRUCTION` | 主动表达风格 | 安装+角色+关系+窗口 | `prompts.py:386` |
| `proactive.contact.news_sharing_prompt` | `NEWS_SHARING_INSTRUCTION` | 新闻分享模板 | 同上 | `prompts.py:351-353,390` |
| `proactive.contact.length_prompt` | `LENGTH_INSTRUCTION` | 非新闻字数模板（必须含 `{chars}`） | 同上 | `prompts.py:355-356,398` |
| `proactive.contact.news_length_prompt` | `NEWS_LENGTH_INSTRUCTION` | 新闻字数模板 | 同上 | `prompts.py:356,398` |
| `proactive.contact.style_prompt` | 空 | 主动风格补充（末尾） | 同上 | `prompts.py:406-407` |
| `proactive.contact.note` | 空 | 偏好说明，进 `contact_intent.preference_note` | 同上 | `prompts.py:287`、`persona_judge.py:79` |
| `model.proactive.compose.prompt` | 任务提示 | 主动任务补充 | 安装/角色/窗口 | `prompts.py` 任务档层 |

### 13.3 任务档提示（`model.<kind>.prompt`）

| 字段 | 默认 | 覆盖层级 | 消费点 |
| --- | --- | --- | --- |
| `model.conversation.reply.prompt` | 任务提示 | 安装/角色/窗口 | `routing.py:212` → `model_wire.py:140-141` |
| `model.role.journal.diary/dream/derive/select.prompt` | “保持原人格，按本次私人书写任务及素材语义生成。” | 同上 | `journal/writing.py:39-42` |
| `model.memory.reflect.prompt` | “按原人格整理已授权资料…” | 同上 | `memory/reflection.py:36-38` |
| `model.calendar.plan.prompt` | “沿原人格安排自己的生活…” | 同上 | `agenda/planning.py:33-36` |
| `model.content.authoring.draft.prompt` | 创作任务说明 | 同上 | `content/authoring_tasks.py:11` |

### 13.4 角色生活与状态

| 字段 | 默认 | 作用 | 覆盖层级 | 消费点 |
| --- | --- | --- | --- | --- |
| `role.state.planning_prompt` | 空 | 日程规划偏好（作为材料文本） | 角色+窗口 | `agenda/planning_inputs.py:92,240` |
| `role.state.energy_expression_prompt` / `hunger_…` / `health_…` | 空 | 状态表达倾向 | 角色+窗口 | `role_work/state/maintenance.py:101-115` |
| `role.state.cycle_expression_prompt` | 空 | 周期表达偏好（随材料下发） | 角色+窗口 | `role_work/state/cycle.py:151,189` |
| `role.state.humanized_state_intensity` | 50 | 状态表达强度（0 不注入） | 角色+窗口 | `state_context.py:264-267` → `humanized.py:149-167` |
| `role.life.diary_style_prompt` | 空 | 日记风格补充 | 角色+窗口 | `journal/writing.py:375` → `journal/prompts.py:94-95` |
| `role.life.dream_theme_candidates` | 空 | 梦境题材参考 | 角色+窗口 | `journal/prompts.py:62-65` |

### 13.5 没有消费点或存在歧义

| 字段/项 | 现象 |
| --- | --- |
| `qzone.topic_prompt_guidance` | 只做长度校验，没有提示拼接；`topic_prompt.request_for` 的 guidance 参数也无生产调用 |
| `conversation.humanized` 组 | 组名与 owner 常量存在但未注册，页面按该组找不到字段（真实字段在 `conversation.presentation`） |
| `humanized_expression.instruction()/block()` | 全仓无调用者，实际走 `HumanizedExpressionPolicy` |
| `conversation.presentation.rules_enabled` | 兼容字段：读时强制 True，规则补段无法关闭 |
| `conversation.brief` | 不存在该组；brief 只是 decision 层的 `reply_style` 取值 |
| `role.state.expression_prompt` | 不存在统一字段，实际是 energy/hunger/health 三项 + cycle |
| `role.journal.*` 提示词字段 | 不存在该组；只有 `model.role.journal.*.prompt` 与 `role.life.diary_style_prompt` |

## 14. 可疑与未使用

| 名称 | 位置 | 现象 |
| --- | --- | --- |
| `INSTRUCTION`（自评模块） | `role_work/journal/self_review.py:18` | 完整自评指令只被测试引用；生产注入的是 `self_review_service.PROMPT:40` |
| `BACKLOG_INSTRUCTION` / `backlog_prompt` | `role_work/life/rest_gate.py:115`、`:566` | 休息期积压补看的提示词有封装函数，但生产链路无调用者（只有测试调用） |
| `SCREEN_INSTRUCTION` | `screen/pipeline.py:26` | 挂在 `ScreenProcessingRequest.instruction` 上；解读任务实际用 `screen/interpretation.py:67` 的 `PROMPT`，是否进入模型请求未确认 |
| `dream_afterglow_mode` 分支 | `role_work/journal/prompts.py:70-76` | 默认值 `auto` 不等于轻/标准/明显，默认配置下该段永不注入 |
| `role.journal.*` | `role_work/life/configuration.py:15` | 配置组实际是 `role.life.*`；按 `role.journal.*` 查字段会查不到 |
| 人格工坊（数据层） | `companion_v2/persona_craft/` | 只有数据与校验、没有模型调用，因此没有提示词可盘 |
| 日记 `writing_date` | `role_work/journal/prompts.py:83` | 只给日期与时区，没有星期；日记若要点出星期仍需模型自己算（与 2.2.0 修好的主动消息同类风险） |
| 周期阶段提示拼接 | `role_work/state/maintenance.py:596` | `cycle_enabled` 时把所有阶段提示一次拼入，而不是只取当前阶段，是否有意未确认 |
| `expression/learning.py:130 INSTRUCTION` | `expression/learning.py:130` | 生产无引用（仅测试）；实际生效的是 `style_service.learning_instruction`（`PROMPT` + `FORM_INSTRUCTION` + `learning.mode_instruction`） |
| `VIDEO_INSTRUCTION`（同名两份） | `conversation/media/understanding.py:64` | 无引用；真正注入的是 `media/video/narration.py:43` 的同名常量，易混 |
| `media/video/models.py:26 INSTRUCTION` | `conversation/media/video/models.py:26` | 无引用，该模块只用于帧与预算校验 |
| `SELF_REVIEW_INSTRUCTION` | `conversation/messages.py:57` | 只 import 未拼接：V2 自生成回复会带自评材料却没有对应指引，只有宿主背景段有 |
| `SHARED_EXPERIENCE_MEMORY_PROMPT` | `astrbot_host/native_extensions/together/common.py:115` | 唯一入口 `record_shared_experiences` 硬编码为 False 且无配置项 → 条件恒假 |
| ComfyUI 两段生图提示 | `image_native/comfyui_prompts.py:164`、`comfyui_service.py:133` | `generate_image`/`analyze` 无调用方，永不注入 |
| live 记忆/上下文兼容函数 | `live/model_methods.py:697`、`:811`、`media_methods.py:84,108` | 保留兼容名或无调用方，不注入 |
| `qzone` 主题 `INSTRUCTION`/`request_for` | `qzone/topic_prompt.py:47`、`:64` | 无生产调用方，主题判定实际靠工具参数 + `CONVERSATION_GUIDANCE` |
| together `_memory_context` | `together/model_methods.py:525` | 恒返回空串，`conversation.enable_memory_context` 实际无效果 |
| `prompt_sections()` 的 condition 文本 | `proactive/prompts.py:210-257` | 只是 Dashboard 文案目录，不参与门控，容易被误读成真实条件 |
| `ORIGIN_INSTRUCTIONS` 覆盖空洞 | `proactive/prompts.py:117-137` | `official_alert_event`、`burst_event`、`followup_event`、`open_loop_event`、`synthetic_event`、`reality_location_event` 无对应来源规则段 |
| `CONTEXT_INSTRUCTION` | `proactive/prompts.py:399` | 无历史时也注入（制度文本，不是 bug，但门控不是“有历史”） |
| `delivery/history.py:31 INSTRUCTION` | `delivery/history.py:31` | 带“不要重复”的那句只在回复路径使用；主动避重靠 DEDUP_INSTRUCTION |

## 15. 维护方法

行号会漂移，重新生成清单的做法：

1. 用 AST 扫全仓模块级/类级大写字面量，只保留值里含中日韩字符的常量，得到“候选清单 + 行号”。
2. 对每个常量 grep 引用点，读拼接处的条件，得到“注入前提”。只看常量定义无法判断是否真的注入。
3. 装配顺序以 `model_gateway/prompt_composer.py` 的 `PROMPT_LAYER_ORDER` 为准；宿主核心链以 `main.py` 的 `on_llm_request` 注入顺序为准；主动联系以 `proactive/prompts.py:384-408` 的拼接表达式为准。
4. 判定“是否注入”时优先看生产调用者：只有测试引用的常量视为未接入。
