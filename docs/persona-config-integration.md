# Persona Configuration Integration

`persona_config.py` is a pure configuration layer. It does not read or write
persona profile files, mutate `PrivateCompanionPlugin` attributes, acquire
locks, or invalidate runtime caches. The caller owns those responsibilities.

## Runtime reads

Build the manifest once at plugin startup:

```python
from .persona_config import load_scope_manifest, resolve_persona_setting

manifest = load_scope_manifest()
value = resolve_persona_setting(
    "quiet_hours",
    active_profile.get("persona_settings"),
    self.config,
    manifest=manifest,
)
```

An absent key follows the primary configuration. Presence wins even for
`False`, `0`, `[]`, and `""`. Common keys always read the primary
configuration; identity keys never fall back to the primary configuration.
Do not temporarily assign resolved values to shared `self.<setting>` fields.

Safety entries expose `safety_merge` in the manifest. Capability switches use
`primary_and_persona`; consent and guard switches use `primary_or_persona`.
The resolver applies this policy so a persona may tighten, but never relax,
the primary safety boundary.

## Profile creation and updates

Use `create_persona_settings(mode=..., bot_name=...)` for new profiles:

- `follow_primary`: sparse settings containing only the new identity name.
- `defaults`: a complete persona-only fresh-install default snapshot.
- `copy`: raw-copy allowed overrides from `source_settings` without resolving
  missing keys; common, identity, and unknown keys are filtered.

Use `detach_persona_settings` to materialize the target's current effective
persona settings. Preserve the target identity values and write the result as
one atomic profile update. A UI/API layer should preview this result and use a
revision or compare-and-swap check before applying it.

## Migration

Call `migrate_persona_profile` on a deep copy of each profile before saving.
Profiles without `persona_settings` remain sparse; the function does not fill
historical missing keys. For a future schema version, pass
`new_keys_by_version={version: [keys...]}` to write only newly introduced
persona keys with their manifest defaults. Invalid non-object settings raise
`PersonaSettingsTypeError`; keep the original file and expose a repair state.

The function preserves all life-data fields and adds
`persona_settings_schema_version` and `persona_settings_revision`. Resolve a
legacy missing `bot_name` outside this module when possible, then pass the
AstrBot display name as `legacy_bot_name`; otherwise pass the stable persona
ID as `persona_id`.

## Persistence boundary

The caller should validate submitted keys against the manifest, reject common
keys, hold the existing profile data lock, write a temporary JSON file, and
atomically replace the profile. On a write failure restore the in-memory
snapshot and leave the original file intact. `persona_config.py` deliberately
does not perform those side effects.
