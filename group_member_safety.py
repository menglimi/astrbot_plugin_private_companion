# -*- coding: utf-8 -*-
from .group_member_safety_shared import (
    logger,
)
from .group_member_safety_shared import logger
from .group_member_safety_part02 import GroupMemberSafetyPart02Mixin
from .group_member_safety_part01 import GroupMemberSafetyPart01Mixin
class GroupMemberSafetyMixin(GroupMemberSafetyPart01Mixin, GroupMemberSafetyPart02Mixin):
    _GROUP_MEMBER_SAFETY_CATEGORIES = {
        "harassment": "持续骚扰",
        "sexual_harassment": "性骚扰",
        "threat": "威胁恐吓",
        "manipulation": "恶意操控",
        "repeated_attack": "重复攻击",
        "other": "其他恶意行为",
    }
    _GROUP_MEMBER_SAFETY_REPEATED_CATEGORIES = {
        "harassment",
        "sexual_harassment",
        "manipulation",
        "repeated_attack",
    }
