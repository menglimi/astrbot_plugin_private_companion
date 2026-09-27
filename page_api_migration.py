# -*- coding: utf-8 -*-
from .page_api_migration_shared import (
    EXTENSION_MIGRATION_NOTICE_VERSION,
    PLUGIN_NAME,
    _MIGRATION_UNKNOWN_CONFIG_KEY,
    _MIGRATION_UNKNOWN_MAX_BYTES,
    _MIGRATION_UNKNOWN_MAX_FIELDS,
    _MIGRATION_UNKNOWN_NAMESPACES,
    _MIGRATION_UNKNOWN_SENSITIVE_NAME,
    logger,
)
from .page_api_migration_shared import logger
from .page_api_migration_part02 import PrivateCompanionPageApiMigrationPart02Mixin
from .page_api_migration_part01 import PrivateCompanionPageApiMigrationPart01Mixin
class PrivateCompanionPageApiMigrationMixin(PrivateCompanionPageApiMigrationPart01Mixin, PrivateCompanionPageApiMigrationPart02Mixin):
    """配置迁移 / 导入导出 / 备份 域（从 PrivateCompanionPageApi 拆出）。"""
