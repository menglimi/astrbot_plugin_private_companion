# -*- coding: utf-8 -*-
"""Expose this checkout under its canonical plugin package name."""
from __future__ import annotations

import importlib.machinery
import os
import sys
import types
from pathlib import Path


PACKAGE_NAME = "astrbot_plugin_private_companion"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]


# Load the real package before legacy unit tests get a chance to install
# ``setdefault`` stubs into the shared full-suite process.  The explicit CI
# stub mode remains self-contained for artifact-import smoke tests.
if os.environ.get("ASTRBOT_CI_STUBS") != "1":
    try:
        import astrbot  # noqa: F401
    except ImportError:
        pass


if PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(PACKAGE_NAME)
    package.__file__ = str(PLUGIN_ROOT / "__init__.py")
    package.__package__ = PACKAGE_NAME
    package.__path__ = [str(PLUGIN_ROOT)]
    spec = importlib.machinery.ModuleSpec(PACKAGE_NAME, loader=None, is_package=True)
    spec.submodule_search_locations = [str(PLUGIN_ROOT)]
    package.__spec__ = spec
    sys.modules[PACKAGE_NAME] = package


if os.environ.get("ASTRBOT_CI_STUBS") == "1" and "astrbot" not in sys.modules:
    class _Dummy:
        def __call__(self, *_args, **_kwargs):
            return self

        def __getattr__(self, _name):
            return self

        def __iter__(self):
            return iter(())

        def __bool__(self):
            return False


    class _Logger:
        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None


    def _module(name: str, *, package: bool = False) -> types.ModuleType:
        value = types.ModuleType(name)
        if package:
            value.__path__ = []
        sys.modules[name] = value
        return value


    astrbot = _module("astrbot", package=True)
    api = _module("astrbot.api", package=True)
    event = _module("astrbot.api.event")
    core = _module("astrbot.core", package=True)
    utils = _module("astrbot.core.utils", package=True)
    paths = _module("astrbot.core.utils.astrbot_path")
    api.logger = _Logger()
    event.MessageChain = _Dummy
    event.AstrMessageEvent = _Dummy
    event.filter = _Dummy()
    paths.get_astrbot_data_path = lambda: Path(".")
    astrbot.api = api
    core.utils = utils

    quart = _module("quart")
    quart.request = _Dummy()
    quart.send_file = _Dummy()
