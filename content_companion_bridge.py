# -*- coding: utf-8 -*-
from .content_companion_bridge_shared import (
    _CONTENT_API_FAMILY,
    _CONTENT_API_UNSET,
    _CONTENT_API_VERSION,
    _CONTENT_DESCRIPTOR_FIELDS,
    _CONTENT_EXTRACT_FIELDS,
    _CONTENT_HANDOFF_CAPABILITY,
    _CONTENT_MODEL_EXECUTION_TOKEN_LIMIT,
    _CONTENT_MODEL_PROMPT_CHAR_LIMIT,
    _CONTENT_MODEL_REQUEST_TOKEN_LIMIT,
    _CONTENT_MODEL_ROLE_OUTPUT_LIMITS,
    _CONTENT_OPERATION_MODEL_CALL_LIMITS,
    _CONTENT_PLUGIN_ID,
    _CONTENT_PROGRESS_PROJECT_FIELDS,
    _CONTENT_PROGRESS_PROJECT_LIMITS,
    _CONTENT_REQUIRED_CAPABILITIES,
    _CONTENT_SERVICES_VERSION,
    _CONTENT_SHARE_FIELDS,
    _CONTENT_STORY_OWNER_ID,
    _CONTENT_TASK_VERSION,
    _CONTENT_VERSION_FIELDS,
    _ContentStoryModelBudget,
    _raise_story_write_fence,
)
# 门面保留拆分前定义在宿主里的 story 相关名字：tests 通过
# patch("astrbot_plugin_private_companion.content_companion_bridge.story_authority_controller")
# 注入替身；源码断言还要求 resolve/call_enforced_story_target 出现在本文件。
# 域 mixin 经 _shared 的宿主代理按调用时解析，保证 patch 生效。
from .content_companion_bridge_shared import (
    StoryAuthorityError,
    call_enforced_story_target,
    resolve_enforced_story_target,
    story_authority_controller,
)
from .content_companion_bridge_part02 import ContentCompanionBridgePart02Mixin
from .content_companion_bridge_part01 import ContentCompanionBridgePart01Mixin
class ContentCompanionBridgeMixin(ContentCompanionBridgePart01Mixin, ContentCompanionBridgePart02Mixin):
    pass
