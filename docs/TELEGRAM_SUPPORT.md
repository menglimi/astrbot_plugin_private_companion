# Telegram 平台支持

本插件（`astrbot_plugin_private_companion`，显示名「我会永远陪着你」）在 7.1.5 基础上增加了 **Telegram** 平台适配。本文说明支持范围、安装配置、已知限制和排查方式。

> 适配基线：AstrBot **4.29.1**（插件声明 `>= 4.27.2`；4.28.2 起 Telegram 适配器补齐了群名、论坛话题、入站音频/视频等能力，建议使用 4.28.2+）。平台名固定为 `telegram`，账号键为 AstrBot 里配置的**平台实例 id**（默认 `telegram`），不是 bot 用户名。

---

## 一、支持范围

| 能力 | Telegram | 说明 |
| --- | --- | --- |
| 私聊被动回复（AstrBot 主链） | ✅ | 文本、图片、语音、视频、文件、引用、贴纸（按图片处理） |
| 私聊连续输入防抖 / 收尾沉默 / 忙碌休息闸门 | ✅ | 与平台无关 |
| 主动联系（文字、图片、表情图、语音） | ✅ | 经 `Platform.send_by_session` 投递；语音自动转 OGG/OPUS |
| 群聊观察 / 参与 / 唤醒 | ✅ | 群白名单、@bot、引用、防抖、群记忆、群黑话 |
| 群 LLM 开关 / 群管理指令 | ✅ | `陪伴群 关闭LLM`、`开启LLM`、`LLM状态` |
| 绑定 / 开始使用向导 / 多账号 | ✅ | 账号 = 平台实例 id；同一 Telegram bot 多实例可分别配置 |
| 记忆、关系网、日程、日记、成长、生图、识屏、现实触及、游戏、直播、一起陪伴 | ✅ | 平台无关能力 |
| 引用回复（入站） | ✅ | Telegram 的 Reply 组件自带被引用消息链，无需历史接口 |
| 引用回复（出站，群聊） | ✅ | `Reply` → `reply_to_message_id` |
| TTS 语音 | ◐ | WAV 会经 FFmpeg 转 OGG/OPUS 后作为语音条发送；FFmpeg 不可用时降级为音频文件/纯文字 |
| @提及 | ◐ | 入站 @bot 正常；出站只对**当前发言人**和 bot 自己生成 @username，其他群成员不猜用户名 |
| 群名 / 昵称显示 | ◐ | 群标题、发言人 username 可显示；Telegram 无群成员列表接口，历史成员名需等其发言后补齐 |
| 跨窗口自然承接 | ✅ | 纯数据库逻辑 |
| 跨窗口递话 | ◐ | 私聊/群聊投递可用；按 QQ 号解析 @群友不可用（见下） |
| 流式输出 | ◐ | 私聊使用 Telegram `sendMessageDraft`；**群聊关闭流式**（AstrBot 群流式编辑路径在首段异常时会崩溃），改为分段发送 |
| 智能分段 | ✅ | 文本分段与平台无关；Telegram 单条上限 4096 字符由 AstrBot 自动切分 |

---

## 二、不支持 / 已降级的功能

以下功能在 Telegram 上**不可用**，插件会安全失败（不崩溃、不重放），面板与配置中不应期待它们生效：

| 功能 | 状态 | 原因 / 表现 |
| --- | --- | --- |
| 戳一戳（拍一拍） | ❌ 不支持 | Telegram 无 poke API，也没有入站戳一戳通知；`interaction.poke` 工具在 Telegram 上不再提供给模型；主动消息中的戳一戳意图被忽略 |
| 撤回感知 / 撤回沉默 / 撤回记录 | ❌ 不支持 | AstrBot Telegram 适配器不产生撤回通知事件；相关开关在 Telegram 上不会触发 |
| 合并转发（入站/出站） | ❌ 不支持 | Telegram 适配器不产生 `Forward` 组件；被转发的消息会以普通文本/媒体到达，丢失“转发”归属；出站 `Forward/Node` 被适配器静默丢弃 |
| QQ 表情（Face） | ❌ 不支持 | Telegram 无 QQ 表情段；出站 Face 被丢弃（文字保留），入站不存在 |
| QQ 空间（Qzone） | ❌ 不支持 | QQ 专属传输与 Cookie；Telegram 下功能返回不可用 |
| 好友申请 / 陌生人通知 | ❌ 不支持 | Telegram 没有好友申请事件；未绑定的私聊会落到 AstrBot 主链，由 AstrBot 自身处理 |
| 原生 emoji 表情回应 | ❌ 未使用 | 插件的「表情回应」是**图库表情图片**（按图片发送），不是 Telegram reaction；Telegram reaction API 未被调用 |
| 跨窗口递话按 QQ 号 @群友 | ❌ 不支持 | 需要 OneBot 群成员列表；Telegram 无成员列表接口，按 QQ 号解析会明确失败 |
| 旧版资料迁入（V1 QQ 数据） | n/a | V1 数据是 QQ 数据；Telegram 安装没有可迁移的旧数据 |
| 群管理员身份判定 | ◐ | AstrBot 的 `event.role=admin` 只来自全局 `admins_id` 配置，不读取 Telegram 群管理员；群管理指令按“安装核心管理员”判定 |

其他已知平台差异：

- **回复 bot 的消息不会唤醒群聊机器人**：AstrBot 用 `Reply.sender_id`（数字用户 id）对比 `self_id`（bot 用户名），二者永不相等。群聊里请使用 **@bot** 或 `/` 唤醒词。
- **群隐私模式**：Telegram bot 默认只能收到 @它、回复它、命令和 Service 消息；要在群里观察普通聊天，需要关闭 BotFather 的 privacy mode 或把 bot 设为群管理员。
- **论坛话题群**：话题群的会话 id 为 `chat_id#thread_id`，群目标需要按话题分别配置；话题名可能显示为 `群标题-话题名`。
- **单条文本 4096 字符**：AstrBot 自动切分；插件分段设置仍然生效。
- **媒体下载 20 MB 上限**：Telegram Bot API `getFile` 限制；超过上限的媒体无法进入理解流程。
- **媒体下载走代理**：Telegram 媒体下载使用 AstrBot 的 `http_proxy` 环境（与 AstrBot 一致），并且只允许下载 `telegram_file_base_url` 配置里的主机。自建本地 Bot API Server 时，请把 `telegram_file_base_url` 配成该服务地址，插件会按该主机放行。
- **媒体 URL 含 bot token**：入站媒体定位符是带 token 的 Telegram 下载 URL；插件只把它们当临时输入，不写入提示词、日志或持久化元数据。
- **贴纸/动图**：静态贴纸按图片理解；`.tgs`/`.webm` 动图贴纸使用静态缩略图；Telegram 的 `animation`（GIF）消息 AstrBot 适配器不处理，可能不会产生事件。
- **不支持的消息类型**（位置、联系人、投票、骰子、Service 消息等）：AstrBot 适配器会产生空内容事件，插件按无文本处理并跳过。
- **B站直播 TTS**：直播自动回应的语音已按目标会话平台转换；Telegram 会话下同样走 OGG/OPUS 转换，其他平台保持原样。
- **群聊流式输出**：插件对 Telegram 群聊关闭 AstrBot 流式编辑（AstrBot 4.29.1 的群流式路径在首段失败时会抛 `UnboundLocalError`），改为普通分段发送；私聊仍使用 Telegram 安全的 `sendMessageDraft`。

---

## 三、安装与配置

1. **在 AstrBot 中添加 Telegram 平台**
   - WebUI → 平台配置 → 新增 Telegram 适配器，填入 BotFather 给的 `telegram_token`。
   - 平台实例 `id` 默认 `telegram`；如配置多个 bot，请给每个实例不同的 id（如 `telegram-main`、`telegram-sub`）。插件用这个 id 作为账号键。
   - 如需代理，在 AstrBot 全局 `http_proxy` 中配置；插件媒体下载会复用它。
2. **安装/更新本插件**并重载。`metadata.yaml` 已声明支持 `telegram`。
3. **打开「陪伴小屋 → 开始使用」**
   - 第 1 步会列出已连接的 Telegram 账号（账号 id = 平台实例 id）和私聊窗口；
   - 选择核心管理员窗口后，在 Telegram 私聊里发送绑定口令：`我会永远陪着你`（可带人格名，如 `我会永远陪着你 晚星`），再用 `companion_confirm` 确认；
   - 按向导完成模型、人格、关系、主动联系、日程、群聊配置。
4. **群聊**：在「账号与会话」里勾选允许接入的 Telegram 群（或使用通配 `*`）。群 id 为负数（超级群，如 `-1001234567890`），论坛话题为 `-1001234567890#42`。
5. **TTS 语音**：Telegram 语音条只接受 OGG/OPUS、MP3、M4A。插件会用 FFmpeg 把 AstrBot TTS 的 WAV 转成 OGG/OPUS；请确保 AstrBot 的 `data/tools/bin/ffmpeg` 或系统 PATH 中有 `ffmpeg`。没有 FFmpeg 时：被动回复降级为音频文件（document），主动消息降级为纯文字。

---

## 四、实现要点（维护者）

- 平台接入点：
  - `companion_v2/adapters/astrbot/native.py`：平台白名单、账号键（平台实例 id）、`telegram-user:` 主体前缀、事件 client 解析、媒体准备钩子、Telegram 引用 locator；
  - `companion_v2/adapters/astrbot/telegram.py`：纯值助手（`event.client` 回退、引用冻结、bot 用户名）；
  - `astrbot_host/telegram_media.py`：入站媒体下载（代理感知、主机白名单、20 MB 上限、AstrBot 临时目录）、出站语音转码、@username 解析；
  - `companion_v2/adapters/astrbot/platform_sender.py`：`TelegramVoiceRecord`（绕过 AstrBot 对 `Record` 的强制 WAV 转码）、Face 丢弃、At 用户名；
  - `astrbot_host/recalls.py`：Telegram 消息路由认证（群 LLM 指令、拒绝回执、日程/重置指令）；
  - `companion_v2/adapters/astrbot/presence.py`：`TelegramPresencePort`（typing → `send_chat_action`）。
- 安全边界：Telegram 媒体 URL 含 bot token，只作为临时 locator 使用；下载只允许配置的 file host；临时文件写入 AstrBot temp 目录并按事件清理。
- 语音边界：AstrBot 4.29.1 的 Telegram 适配器会把所有出站 `Record` 重新编码成 WAV 再 `send_voice`，而 Telegram 拒绝 WAV 语音条。插件用 `TelegramVoiceRecord` 子类覆盖 `convert_to_file_path()` 返回已转好的 OGG/MP3/M4A 路径，从而保留语音条体验。

---

## 五、排查

| 现象 | 优先检查 |
| --- | --- |
| 私聊不回复 | 平台实例是否已连接；绑定是否完成（`陪伴小屋 → 开始使用` 第 1 步）；`event_account_id` 是否等于平台实例 id |
| 群聊不回复 | 群是否在「账号与会话」里勾选；是否 @bot 或使用 `/` 唤醒；BotFather privacy mode；群目标 id 是否含论坛话题后缀 |
| 图片/文件/视频理解失败 | 媒体下载是否走通代理；`telegram_file_base_url` 主机是否与实际下载地址一致；文件是否超过 20 MB |
| 语音没有声音条 | 是否安装 FFmpeg；日志中是否有 `Telegram voice conversion` 警告；被动回复会降级为音频文件 |
| 群聊回复偶发失败 | 已默认关闭 Telegram 群聊流式；若仍异常，查看 AstrBot 日志中的 `send_streaming` 相关错误 |
| 群管理指令无效 | 操作者是否为安装核心管理员；Telegram 群管理员身份不等同于插件核心管理员 |
