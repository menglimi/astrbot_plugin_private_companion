# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

from astrbot_plugin_private_companion.main import (
    _is_primary_plugin_instance,
    _plugin_instance_can_dispatch,
    _private_companion_runtime,
)


def _instance(module_name: str):
    cls = type("Plugin", (), {"__module__": module_name})
    return cls()


def test_primary_instance_wins_over_same_name_worktree():
    primary = _instance("data.plugins.astrbot_plugin_private_companion.main")
    worktree = _instance("data.plugins._worktree_pr215.main")
    for instance in (primary, worktree):
        instance._private_companion_instance_guard_enabled = True
        instance._private_companion_duplicate_instance = False

    previous = _private_companion_runtime.active_plugin
    try:
        _private_companion_runtime.active_plugin = primary
        assert _is_primary_plugin_instance(primary)
        assert not _is_primary_plugin_instance(worktree)
        assert _plugin_instance_can_dispatch(primary)
        assert not _plugin_instance_can_dispatch(worktree)
    finally:
        _private_companion_runtime.active_plugin = previous


def test_unmanaged_test_instance_is_not_suppressed():
    instance = SimpleNamespace()
    previous = _private_companion_runtime.active_plugin
    try:
        _private_companion_runtime.active_plugin = object()
        assert _plugin_instance_can_dispatch(instance)
    finally:
        _private_companion_runtime.active_plugin = previous
