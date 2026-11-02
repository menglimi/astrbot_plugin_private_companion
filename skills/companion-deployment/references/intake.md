# Companion V2 intake checklist

Use this as a short interview, not a form dump. Ask only what the current installation and the user's goal require.

## 1. Start with read-only facts

Report what you already know and ask the user to correct it:

```text
AstrBot: <version>
Plugin: <version>
Current data root: <path>
Existing runtime DB: yes/no
Existing targets: <none / count>
Loaded chat providers: <ids>
Loaded TTS providers: <ids>
Current failure: <code or none>
```

Never include API keys, tokens, passwords, or private chat text.

## 2. Minimum questions

### Fresh install or upgrade?

- Fresh: no V2 database, no existing role bindings.
- Upgrade: preserve existing data; ask before migration or path changes.

### Who is the companion?

- Platform: `aiocqhttp`, `qq_official`, Telegram, etc.
- Bot account ID.
- Target user account ID.
- Persona ID and role name.

### Private only or multi-window?

- One private target.
- Multiple Bots/accounts.
- Group chat behaviour.
- Mobile/reality/live features needed?

### Models

Ask for one required model and optional task overrides:

```text
reply:
memory/reflection:
proactive:
vision:
image generation:
fallback:
```

Only offer Providers that are actually loaded.

### Voice

Choose one:

```text
AstrBot TTS Provider: <id>
MiMo Voice Clone fallback: yes/no
Text only: yes/no
STT: yes/no
```

Explain that MiMo requires the MiMo plugin to register `mimo_tts_speak`, and that AstrBot TTS is preferred.

### Proactive

Ask:

```text
Enable proactive contact: yes/no
Approximate frequency: ...
Quiet hours: ...
Daily limit: ...
```

If the user does not care, propose conservative defaults and say so.

### Data and backup

Ask:

```text
Keep existing data: yes/no
Fresh V2 data: yes/no
Migrate V1 data: yes/no
Backup before migration: yes/no
```

Migration and database replacement require a separate explicit confirmation.

## 3. Safe default profile

Use only when the user says “你决定” or has no preference. State the defaults before applying them.

```text
Private companion only
One Bot account, one target user, one persona
Reply model: first loaded chat Provider with tool support
Fallback: none
Voice: use the current session AstrBot TTS Provider if available; otherwise text only
STT: leave disabled unless requested
Proactive: disabled until the first manual private conversation and setup verification
Data: fresh V2 directory under the configured plugin_data root
Backup: yes, before any migration
```

Do not silently choose MiMo, a paid image model, or a high proactive frequency.

## 4. Confirmation template

```text
我准备这样配置：

- 部署形态：单私聊 / 多窗口 / 含群聊
- Bot：<id>
- 陪伴对象：<id>
- 角色/人格：<name/id>
- 对话模型：<provider/model>
- 复盘模型：<provider/model 或沿用对话>
- 主动消息：开启/关闭，频率 ...
- 语音：AstrBot TTS <id> / MiMo 回退 / 纯文字
- STT：开启/关闭
- 数据：保留现有 / 新建；是否迁移
- 是否需要重启：是/否

不会改动：现有数据库 / 其他人格 / 其他插件
确认后我再写入配置。
```

Wait for an explicit confirmation. If the user answers only part of the plan, ask only the missing decisions.

## 5. After confirmation

1. Back up the config and, if migration is involved, the relevant database.
2. Apply the smallest configuration change.
3. Reload the plugin or restart the backend only when required.
4. Verify `host/status`, target/role bindings, model task selection, one private reply, and voice/STT if enabled.
5. Report exactly what changed and what was not verified.
