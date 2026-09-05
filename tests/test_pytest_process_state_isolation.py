from __future__ import annotations

import os
import sys
import types


_LEAK_MODULE = "private_companion_test_order_leak"
_LEAK_ENV = "PRIVATE_COMPANION_TEST_ORDER_LEAK"


def test_01_process_state_polluter() -> None:
    """Model legacy tests that mutate process state without monkeypatch."""
    sys.modules[_LEAK_MODULE] = types.ModuleType(_LEAK_MODULE)
    os.environ[_LEAK_ENV] = "polluted"


def test_02_process_state_victim_starts_clean() -> None:
    """This test fails when run after the polluter without the autouse cleanup."""
    assert _LEAK_MODULE not in sys.modules
    assert _LEAK_ENV not in os.environ
