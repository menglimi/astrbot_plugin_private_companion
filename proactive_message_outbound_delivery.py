# -*- coding: utf-8 -*-
from .proactive_message_outbound_delivery_shared import (
    _host_Image,
    _now_ts,
    _ProactiveSendOutcome,
    logger,
)
from .proactive_message_outbound_delivery_shared import logger
from .proactive_message_outbound_delivery_part04 import ProactiveMessageOutboundDeliveryPart04Mixin
from .proactive_message_outbound_delivery_part03 import ProactiveMessageOutboundDeliveryPart03Mixin
from .proactive_message_outbound_delivery_part02 import ProactiveMessageOutboundDeliveryPart02Mixin
from .proactive_message_outbound_delivery_part01 import ProactiveMessageOutboundDeliveryPart01Mixin
class ProactiveMessageOutboundDeliveryMixin(ProactiveMessageOutboundDeliveryPart01Mixin, ProactiveMessageOutboundDeliveryPart02Mixin, ProactiveMessageOutboundDeliveryPart03Mixin, ProactiveMessageOutboundDeliveryPart04Mixin):
    """outbound_delivery 域（从 ProactiveMessageMixin 拆出）。"""
