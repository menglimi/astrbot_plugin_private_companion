# -*- coding: utf-8 -*-
from .proactive_message_external_share_shared import (
    _now_ts,
)
from .proactive_message_external_share_shared import _now_ts
from .proactive_message_external_share_part03 import ProactiveMessageExternalSharePart03Mixin
from .proactive_message_external_share_part02 import ProactiveMessageExternalSharePart02Mixin
from .proactive_message_external_share_part01 import ProactiveMessageExternalSharePart01Mixin
class ProactiveMessageExternalShareMixin(ProactiveMessageExternalSharePart01Mixin, ProactiveMessageExternalSharePart02Mixin, ProactiveMessageExternalSharePart03Mixin):
    """external_share 域（从 ProactiveMessageMixin 拆出）。"""
