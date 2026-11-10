"""The seam between the Zone domain and whatever performs the HTTP call.

The domain decides *what* to send and *what the answer means*; the host owns the
session, the credentials and the delivery semantics. ``delivery_unknown`` is the
important field: a timed-out POST may already have reached QQ, so it must never
be replayed, while a failed GET is simply retried.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from companion_v2.qzone.protocol import QzoneCredential, QzoneRequest


@dataclass(frozen=True)
class TransportOutcome:
    """One bounded answer; the caller decides what to do with it."""

    status: int
    text: str = ""
    error: str = ""
    retryable: bool = False
    delivery_unknown: bool = False

    @property
    def ok(self):
        return not self.error and 200 <= int(self.status or 0) < 400


class QzoneTransport(Protocol):
    async def credential(self, *, refresh=False) -> QzoneCredential:
        """Return the current session, acquiring or refreshing it when needed."""

    async def send(self, request: QzoneRequest, *, timeout: float) -> TransportOutcome:
        """Perform exactly one request; never retry, never invent partial success."""

    async def qzonetoken(self, credential: QzoneCredential) -> str:
        """Best-effort H5 token; the upload response remains authoritative."""


__all__ = ["QzoneTransport", "TransportOutcome"]
