# -*- coding: utf-8 -*-
"""Shared helpers for the story_handoff facade parts.

The delayed host reference lets the split parts resolve names through the
``story_handoff`` facade at access time, preserving the monkeypatch semantics
that legacy tests rely on.
"""
from __future__ import annotations


class _StoryHandoffHostRef:
    """延迟引用宿主 story_handoff 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import story_handoff as _host_module

        return getattr(_host_module, name)


_story_handoff_host = _StoryHandoffHostRef()
