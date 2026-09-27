# -*- coding: utf-8 -*-
"""Backward-compatible import alias for the canonical disclosure policy."""

try:
    from .agenda_disclosure_policy import *  # noqa: F401,F403
except ImportError:
    from agenda_disclosure_policy import *  # noqa: F401,F403
