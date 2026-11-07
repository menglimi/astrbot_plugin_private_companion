"""Care: the character's small, stable concerns and the impulses they produce.

This package deliberately separates *what the character cares about* from
*when something happens*.  A concern is not a cron entry; it is a standing
attachment that only becomes an impulse when a cue makes it salient.  Phase 1
observes impulses without acting on them.
"""

from .engine import ACTIONS, CONCERNS, appraise, normalize_settings
from .models import Concern, DeviceObservation, Impulse

__all__ = ["ACTIONS", "CONCERNS", "appraise", "normalize_settings", "Concern",
           "DeviceObservation", "Impulse"]
