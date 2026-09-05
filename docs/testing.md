# Testing

Run the suite with the Python environment bundled with AstrBot so imports exercise the
real framework rather than test doubles:

```powershell
$env:PYTHONPATH = 'H:\AstrBot\backend\app'
H:\AstrBot\backend\python\python.exe -m pytest
```

## Optional memory-companion integration contracts

A small set of cross-plugin contract tests loads source files from a **real** memory
companion checkout. The dependency is optional: when no checkout is installed those
test modules are reported as skipped during collection, while the rest of the suite is
still collected and run. No memory plugin is fabricated.

Point the tests at a checkout whose root contains `core/bridge.py` with either:

```powershell
$env:ASTRBOT_MEMORY_PLUGIN_ROOT = 'D:\src\astrbot_plugin_memory_companion'
H:\AstrBot\backend\python\python.exe -m pytest
```

or:

```powershell
H:\AstrBot\backend\python\python.exe -m pytest `
  --memory-plugin-root D:\src\astrbot_plugin_memory_companion
```

An explicitly configured path is authoritative. If it is invalid, integration tests
skip with a message naming the missing marker instead of silently selecting another
checkout. With a valid checkout, tests import and execute that checkout's actual
contract, bridge, coordination, manifest, and projection modules; failures remain real
integration failures.
