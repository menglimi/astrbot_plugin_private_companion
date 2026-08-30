# -*- coding: utf-8 -*-
"""Compatibility export for older integrations importing PrivateReadingMixin."""
from __future__ import annotations

from .reading_archive import ReadingArchiveMixin


PrivateReadingMixin = ReadingArchiveMixin

__all__ = ["PrivateReadingMixin"]
