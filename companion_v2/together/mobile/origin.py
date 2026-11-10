"""Which execution path is opening a Together room right now.

The mobile gateway already prepares room access through its own proxy
(``_ensure_mobile_room_access(via_mobile_gateway=True)``), but the native
provider used to call the same preparation again without that flag and tried to
start a public tunnel. On hosts without a reachable public URL the second call
failed and every phone-side room creation ended in
``together_public_access_unavailable``.

The bridge sets this marker for the duration of exactly one provider
invocation; the provider reads it when it prepares access.
"""

from contextvars import ContextVar


MOBILE_ROOM_CALL: ContextVar[bool] = ContextVar("mobile_together_room_call", default=False)


__all__ = ["MOBILE_ROOM_CALL"]
