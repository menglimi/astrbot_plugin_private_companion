---
name: companion-deployment
description: Deploy, upgrade, and configure the AstrBot "我会永远陪着你 / Companion V2" plugin. Use when installing the plugin, fixing storage or startup errors, binding a Bot/account/role window, choosing chat or voice providers, completing the setup guide, or diagnosing role_bindings=0, provider unavailable, route ambiguity, TTS/STT, and model output-limit failures.
---

# Companion V2 deployment and configuration

Use this skill when you are an agent helping a user install, upgrade, or configure `astrbot_plugin_private_companion`.

## 0. Ask before changing anything

This skill is an interactive deployment assistant, not an unattended installer. Do not create the runtime database, write configuration, migrate data, install packages, reload the plugin, or restart AstrBot until the user has confirmed the plan.

You may do read-only discovery first:

- Read the AstrBot and plugin versions.
- Read the current plugin configuration with secrets redacted.
- Inspect whether `plugin_data/`, the runtime database, and existing targets already exist.
- Read backend logs for the current failure.
- List loaded chat/TTS/STT providers by ID and model only.

Then ask a compact set of questions. Do not dump a 30-item form. Group the decisions:

1. **Deployment shape**
   - Single private companion?
   - Multiple Bots/accounts or multiple private targets?
   - Group chat behaviour needed?
2. **Identity mapping**
   - Which platform, Bot account, user account, and persona?
   - Is this a fresh install or an upgrade with existing data?
3. **Models**
   - Which loaded chat model should handle replies?
   - Which model for memory/reflection, proactive contact, vision, and image generation?
   - Is a fallback Provider required?
4. **Voice**
   - Voice replies on/off?
   - AstrBot TTS Provider, MiMo Voice Clone fallback, or text only?
   - STT needed?
5. **Proactive behaviour**
   - Enable proactive contact?
   - Desired frequency, quiet hours, and daily limits?
6. **Data**
   - Fresh V2 data, or migrate from V1/6.x?
   - Where should plugin data live?
   - Is a backup required before migration?

If the user has no preference, propose safe defaults explicitly and ask for one final confirmation. Never silently choose a paid model, a TTS voice, or a destructive migration.

Before writing, show a short plan:

```text
Will change:
- storage.path: ...
- multi_host.targets: add ...
- persona/role: ...
- model.chat: ...
- voice: ...
Will not change:
- existing database
- existing personas
- other plugins
Requires restart: yes/no
```

Wait for an explicit “确认/可以/执行” before proceeding. If the user changes a choice, update the plan and confirm again.

Read [references/intake.md](references/intake.md) for the question template and default profile.

## First principles

- Treat the plugin code directory as read-only. Never create the V2 database, logs, or runtime files under `plugins/`.
- Back up both the plugin configuration and the V2 runtime database before structural changes.
- Do not edit the live SQLite database while AstrBot is running unless the user explicitly asks for a repair and accepts the risk.
- Never print API keys, dashboard passwords, room tickets, or private message contents.
- Prefer the Dashboard API/UI over direct SQL. Use direct SQL only for read-only diagnostics.
- After changing code or configuration, reload the plugin or restart the backend, then verify status before reporting success.

## 1. Identify the installation

Collect:

```text
AstrBot version
Plugin version
Plugin directory
AstrBot data directory
Plugin data directory
Runtime database path
```

Typical Docker/Linux layout:

```text
/AstrBot/data/plugins/astrbot_plugin_private_companion
/AstrBot/data/plugin_data/astrbot_plugin_private_companion
/AstrBot/data/config/astrbot_plugin_private_companion_config.json
```

Typical host layout:

```text
<data>/plugins/astrbot_plugin_private_companion
<data>/plugin_data/astrbot_plugin_private_companion
<data>/config/astrbot_plugin_private_companion_config.json
```

The runtime database normally lives under:

```text
<plugin_data>/companion-7/installations/<profile>/runtime/companion-v2.sqlite3
```

The supported AstrBot range begins at `4.27.2`; verify the installed release metadata instead of assuming.

## 2. Install or upgrade safely

1. Stop intake or stop AstrBot if the release notes require it.
2. Install the plugin zip through AstrBot's plugin installer, or replace the plugin directory with the release contents.
3. Keep the existing `data/` and `plugin_data/` directories.
4. Do not copy a V1 database into the V2 runtime directory. Use the plugin's explicit legacy migration flow.
5. Start/reload the backend and verify:

```text
GET /api/plug/astrbot_plugin_private_companion/host/status
```

Expect:

```json
{"state": "running", "accepting": true}
```

If the status is absent, read AstrBot backend logs for import errors before retrying. A plugin reload does not reload already-imported `companion_v2` modules; a backend restart is required for those code changes.

## 3. Fix `StoragePathError` first

V2 rejects a data root inside the plugin code directory.

Set `storage.path` to the **parent** of the dedicated data directory, for example:

```json
"storage": {
  "path": "/AstrBot/data/plugin_data"
}
```

Do not point it at:

```text
/AstrBot/data/plugins/astrbot_plugin_private_companion
```

Do not append the plugin name twice. Ensure the directory is writable by the AstrBot user, then restart the backend.

## 4. Bind the target window and role

The most common startup failure is not a model failure: it is a missing target/persona/role mapping.

Check `multi_host` in the plugin config:

- `targets`: one entry per private target; includes `platform_id`, `account_id`, `bot_id`, `participant_id`.
- `personas`: maps `bot_id` + `persona_id` to `role_id`.
- `group_targets`: only when group behaviour is needed.

Rules:

- A private target must point at the actual participant account, not the bot account.
- One role may serve several windows, but each window must have a deterministic `bot_id` and `role_id`.
- Do not leave a stale test target or a target whose account is no longer connected.
- If `role_bindings` stays at 0, verify the configured Bot, account, persona, and participant mapping before changing model settings.
- If the same person has several `conversation_routes` rows, V2 now collapses them by platform/bot/account/participant/role and prefers the newest non-`unopened-*` row. Do not run ad-hoc SQL deletion for `acquaintance_route_required`; check for genuinely different routes first.

## 5. Configure models by task, not one global model

Use the model page's task routes:

- Conversation reply
- Memory reflection / summary
- Proactive contact
- Image generation
- Vision / image review
- Other background tasks

Guidance:

- Prefer a loaded Provider with the required capability; text-only models cannot do vision.
- `model.max_output_tokens` is the default task budget, not the Provider's maximum capability. Do not use it to work around a Provider limit.
- If a task reports `model_output_limit`, raise the task's output budget within the product ceiling or choose a Provider with a larger declared ceiling.
- For structured background tasks, a Provider that only exposes `text_chat` is supported through the compatibility transport, but it does not get V2's tool loop.
- Anthropic-style Providers do not receive OpenAI `response_format`; V2 uses their own structured path. Do not reintroduce that field.
- GPT-6 / GPT-5 / o-series requests use `max_completion_tokens`; Responses APIs use `max_output_tokens`.

Never claim a Provider is supported merely because it appears in AstrBot. Confirm it is loaded and V2 exposes it in the model task picker.

## 6. Complete the setup guide

Follow the eight setup steps in order:

1. Management access
2. Target window binding
3. Runtime profile initialization
4. Persona / role profile
5. Model route
6. Voice and STT
7. Proactive contact settings
8. Verification

Do not skip the private-window conversation required by the proactive test. The test refuses to synthesize a source; send one normal private message to the bound target, wait for a V2 reply, then refresh and test.

## 7. Voice: AstrBot TTS first, MiMo bridge fallback

The preferred path is an enabled AstrBot TTS Provider selected for the current session.

If no AstrBot TTS Provider can synthesize, V2 can fall back to a registered MiMo Voice Clone tool:

```text
mimo_tts_speak
```

Requirements:

- The MiMo plugin is loaded.
- The tool is registered.
- The tool handler can resolve a plugin exposing `synthesize_text` or `text_to_speech`.

If neither path is available, the result is text-only; this is safer than sending audio to an arbitrary Provider.

For STT:

- Check AstrBot's session-level STT switch, not only the global switch.
- A missing Provider means no voice input; the room will not silently choose another Provider.
- Preserve an explicitly selected unavailable Provider ID rather than blanking it, so the UI can report the real cause.

## 8. Verify, then report

Minimum checks:

```text
host/status -> running / accepting=true
model task status -> expected Provider, no model_provider_unavailable
setup guide -> all required steps complete
one private chat -> V2 reply recorded
voice test -> record or an explicit text-only reason
```

Report:

- What was changed
- Where it was changed
- What was verified
- What remains unavailable and why
- Whether a backend restart is still required

If a fix requires code changes, keep them in the source tree and run the relevant tests; do not patch the installed runtime copy as the permanent solution.

## Common failure map

| Symptom | First check |
|---|---|
| `StoragePathError` / `data_root` | `storage.path` points into `plugins/` |
| `setup_binding_missing` | `multi_host.targets` or `personas` empty |
| `host_provider_unavailable` | Selected model is not loaded in AstrBot |
| `host_provider_changed` | Provider instance/config was replaced after startup |
| `role_bindings=0` | Bot/account/persona/participant mapping is wrong or probe found no window |
| `acquaintance_route_required` | Same person has genuinely different Bot/account/role routes |
| `model_output_limit` | Task budget exceeds declared Provider ceiling |
| `model_fallback_duplicate` | Fallback equals primary; clear fallback or choose another Provider |
| `tool_calls must be followed by tool messages` | History repair did not run before the Provider request |
| `provider_request_failed` | Inspect Provider/protocol error; do not assume the role data is corrupt |
| TTS unavailable | AstrBot TTS session switch, then MiMo tool bridge |
| STT unavailable | Session STT switch or explicit STT Provider |
