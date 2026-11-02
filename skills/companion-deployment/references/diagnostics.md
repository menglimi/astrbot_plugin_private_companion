# Companion V2 diagnostics reference

Use these checks in order. Prefer read-only calls. Do not print secrets.

## Host status

```text
GET /api/plug/astrbot_plugin_private_companion/host/status
```

Look for:

```json
{"state": "running", "accepting": true, "failure_code": null}
```

If the plugin route is missing, check AstrBot plugin logs and whether the plugin imported successfully.

## Target and role catalog

```text
GET /api/plug/astrbot_plugin_private_companion/page/targets
```

Confirm:

- `multi_host.targets` includes the intended private target.
- `participant_id` is the user, not the bot account.
- Each role has the expected `role_id` and `persona_api`.
- No stale test target remains.

## Model task status

```text
GET /api/plug/astrbot_plugin_private_companion/host/targets/<target>/roles/<role>/model
```

Confirm the task's selected Provider appears in `providers` and the task has no `model_provider_unavailable` failure.

## Storage layout

Read-only check of the resolved data root:

```text
<plugin_data>/companion-7/installations/<profile>/runtime/companion-v2.sqlite3
```

The plugin code directory must not contain:

```text
runtime/companion-v2.sqlite3
```

## Read-only SQLite diagnostics

Use a read-only connection and close it immediately. Never run `UPDATE` or `DELETE` while AstrBot is running.

```sql
SELECT COUNT(*) AS role_bindings FROM role_bindings;
SELECT context_key, observed_at, generation_state
FROM conversation_native_turns ORDER BY observed_at DESC LIMIT 5;
SELECT operation_id, state FROM operations
WHERE owner = 'role.journal.trigger' ORDER BY created_at DESC LIMIT 10;
```

If `role_bindings` is 0, fix target/persona mapping; do not modify model settings.

## Log signatures

- `provider_request_failed`: Provider/protocol request failed; inspect the preceding `error_type`, `task_error_code`, and status.
- `host_provider_changed`: Provider instance/config was replaced; restart or reinitialize.
- `host_provider_unavailable`: selected Provider is not loaded.
- `model_output_limit`: task budget exceeds the Provider ceiling.
- `model_fallback_duplicate`: fallback Provider equals primary.
- `acquaintance_route_required`: genuinely different Bot/account/role routes remain.
- `stt_unavailable`: no session STT and no explicit STT Provider.
- `laya_gpu_unavailable`: local Laya cannot use CUDA; use remote Workers AI mode.
