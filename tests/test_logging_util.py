# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import Mock, patch

from astrbot_plugin_private_companion import logging_util


def test_module_logger_resolves_replaced_astrbot_logger_and_preserves_lazy_args():
    replacement = Mock()
    proxy = logging_util.get_module_logger(
        "astrbot_plugin_private_companion.qzone_feed"
    )

    with patch("astrbot.api.logger", replacement):
        proxy.info("loaded=%s", 3)

    replacement.info.assert_called_once_with("[空间动态] loaded=%s", 3)


def test_module_logger_exposes_standard_compatibility_methods():
    replacement = Mock()
    proxy = logging_util.get_module_logger("plugin.custom")

    with patch("astrbot.api.logger", replacement):
        proxy.warn("legacy")
        proxy.critical("fatal")
        proxy.log(25, "custom=%s", "level")
        replacement.isEnabledFor.return_value = True
        assert proxy.isEnabledFor(20) is True

    replacement.warning.assert_called_once_with("[custom] legacy")
    replacement.critical.assert_called_once_with("[custom] fatal")
    replacement.log.assert_called_once_with(25, "[custom] custom=%s", "level")
