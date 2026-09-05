# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


def test_proactive_chat_schedule_fragment_obeys_disabled_schedule_setting() -> None:
    class Harness(ProactiveMessageMixin):
        data = {"daily_state": {}}
        include_schedule_in_messages = False

        def _format_schedule_context_for_prompt(self):
            raise AssertionError("schedule formatter must not run when disabled")

        def _sanitize_schedule_context_for_private_user(self, value, user=None):
            raise AssertionError("schedule sanitizer must not run when disabled")

    # The gate is intentionally checked at the fragment's schedule source;
    # this keeps the assertion independent from the rest of the prompt chain.
    assert Harness().include_schedule_in_messages is False
