"""Bounded post references: the only handles a like or reply may act on.

A scan hands the model opaque tokens instead of platform identifiers, so a model
cannot like or reply to a post that was never read in this conversation, and a
token from another conversation or role never resolves here. References are
deliberately in-memory: a restart invalidates them, which is the safe direction
(the model must scan again rather than act on a stale handle).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from threading import RLock

from companion_v2.execution.models import TaskError, digest


LIKE = "like"
COMMENT = "comment"


@dataclass(frozen=True)
class QzonePhoto:
    url: str
    width: int = 0
    height: int = 0
    identities: tuple[str, ...] = ()

    def summary(self):
        return {"url": self.url, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class QzonePost:
    """One scanned post with the original tickets a later action must resend."""

    post_id: str
    author_uin: str
    author_name: str
    excerpt: str
    unikey: str = ""
    curkey: str = ""
    appid: str = ""
    typeid: str = ""
    abstime: str = ""
    fid: str = ""
    created_at: str = ""
    liked: bool = False
    own: bool = False
    images: tuple[QzonePhoto, ...] = ()

    def __post_init__(self):
        if type(self.post_id) is not str or not self.post_id.strip():
            raise ValueError("a scanned post requires its platform identity")
        if type(self.author_uin) is not str or not self.author_uin.strip():
            raise ValueError("a scanned post requires its author")


@dataclass(frozen=True)
class QzonePostReference:
    """A scanned post plus the lease that makes it actionable for a while."""

    post: QzonePost
    token: str
    owner_key: str
    scope_key: str
    issued_at: datetime
    expires_at: datetime
    used: tuple[str, ...] = ()

    def used_for(self, kind):
        return kind in self.used

    def summary(self):
        """The model-facing and page-facing view: no session tickets."""
        return {"post_ref": self.token, "author": self.post.author_name, "excerpt": self.post.excerpt,
                "at": self.post.created_at or self.issued_at.isoformat(), "liked": self.post.liked,
                "own_post": self.post.own, "images": [photo.summary() for photo in self.post.images]}


class QzoneReferenceStore:
    """One bounded, per-runtime lease table with an explicit clock."""

    def __init__(self, *, ttl_minutes, capacity):
        if type(ttl_minutes) is not int or ttl_minutes < 1:
            raise ValueError("reference TTL must be a positive integer")
        if type(capacity) is not int or capacity < 1:
            raise ValueError("reference capacity must be a positive integer")
        self.ttl = timedelta(minutes=ttl_minutes)
        self.capacity = capacity
        self._items: dict[str, QzonePostReference] = {}
        self._lock = RLock()

    def issue(self, posts, *, owner_key, scope_key, at):
        """Register scanned posts and return them with their lease filled in."""
        issued = []
        with self._lock:
            self._forget(at)
            for post in posts:
                token = digest(["qzone-post", owner_key, scope_key, post.post_id, post.unikey,
                                at.isoformat()])
                reference = QzonePostReference(post=post, token=token, owner_key=owner_key,
                    scope_key=scope_key, issued_at=at, expires_at=at + self.ttl, used=())
                self._items[token] = reference
                issued.append(reference)
            self._trim()
        return tuple(issued)

    def resolve(self, token, *, owner_key, scope_key, at, kind=None):
        """Resolve one token, refusing another conversation's or an expired handle."""
        if type(token) is not str or not token.strip() or len(token) > 200:
            raise TaskError("qzone_post_ref_invalid", "a scanned post reference is required")
        with self._lock:
            self._forget(at)
            reference = self._items.get(token.strip())
            if reference is None:
                raise TaskError("qzone_post_ref_stale", "scan the Zone again; this post reference is no longer valid")
            if reference.owner_key != owner_key or reference.scope_key != scope_key:
                raise TaskError("qzone_post_ref_stale", "this post reference belongs to another conversation")
            if kind is not None and reference.used_for(kind):
                raise TaskError("qzone_post_already_done", "this post already received that action")
            return reference

    def mark_used(self, reference, kind):
        """Record one performed action so a repeated call cannot repeat it."""
        with self._lock:
            current = self._items.get(reference.token)
            if current is not None and kind not in current.used:
                self._items[reference.token] = replace(current, used=(*current.used, kind))

    def mark_liked(self, reference):
        with self._lock:
            current = self._items.get(reference.token)
            if current is not None:
                self._items[reference.token] = replace(current, post=replace(current.post, liked=True))

    def clear(self, *, owner_key=None, scope_key=None):
        with self._lock:
            if owner_key is None and scope_key is None:
                self._items.clear()
                return
            for token, reference in tuple(self._items.items()):
                if ((owner_key is None or reference.owner_key == owner_key)
                        and (scope_key is None or reference.scope_key == scope_key)):
                    self._items.pop(token, None)

    def _forget(self, at):
        for token, reference in tuple(self._items.items()):
            if reference.expires_at <= at:
                self._items.pop(token, None)

    def _trim(self):
        # Oldest first: a long conversation may scan many times, and the lease is
        # only a convenience for the next action, never a durable record.
        overflow = len(self._items) - self.capacity
        if overflow <= 0:
            return
        ordered = sorted(self._items.values(), key=lambda item: (item.issued_at, item.token))
        for reference in ordered[:overflow]:
            self._items.pop(reference.token, None)


__all__ = ["COMMENT", "LIKE", "QzonePost", "QzonePostReference", "QzoneReferenceStore"]
