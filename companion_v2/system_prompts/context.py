"""Read the effective system prompt overrides for one conversation."""

from __future__ import annotations

import logging

from .catalog import FIELD_DEFAULTS
from .configuration import (
    GROUP, RoleSystemPromptConfigurationOwner, configuration_fields, target_for,
)

logger = logging.getLogger(__name__)


class SystemPromptContext:
    def __init__(self, runtime):
        self.runtime = runtime
        self.owner = RoleSystemPromptConfigurationOwner(runtime)
        self.fields = configuration_fields()

    def read(self, context):
        snapshot = self.owner.read(target_for(context), self.fields)
        prefix = GROUP + "."
        return {
            field.field_id.removeprefix(prefix): snapshot.fields[field.field_id].value
            for field in self.fields
        }

    def overrides(self, context):
        """Only non-default values; an empty value means "use the built-in"."""
        try:
            values = self.read(context)
        except Exception as error:  # a broken override must never block a reply
            logger.warning("[CompanionV2] 系统提示词读取降级 error_type=%s", type(error).__name__)
            return {}
        return {
            key: value.strip()
            for key, value in values.items()
            if isinstance(value, str) and value.strip() and value.strip() != FIELD_DEFAULTS.get(key, "")
        }
