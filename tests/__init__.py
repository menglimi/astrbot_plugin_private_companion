"""Test package bootstrap.

AstrBot resolves its runtime root from the current working directory when it is
not running as a packaged desktop application. Importing ``astrbot.core`` can
therefore create ``data/cmd_config.json`` and other runtime files inside this
repository. Keep those import side effects in the system temporary directory
during tests.
"""

import os
import tempfile
from pathlib import Path


_TEST_RUNTIME_ROOT = (
    Path(tempfile.gettempdir())
    / "astrbot_private_companion_tests"
    / str(os.getpid())
)
os.environ.setdefault("ASTRBOT_ROOT", str(_TEST_RUNTIME_ROOT))
