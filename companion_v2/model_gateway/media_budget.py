"""Inline historical image budget governance and conservative byte weighting."""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_MAX_INLINE_IMAGES = 4
DEFAULT_MAX_INLINE_BYTES = 6 * 1024 * 1024
DEFAULT_PER_IMAGE_BASE_ESTIMATE = 16384
DEFAULT_BYTES_PER_ESTIMATE_UNIT = 64


@dataclass(frozen=True)
class ImageHistoryBudgetPolicy:
    """Bounded budget for inline images forwarded on the model wire.

    Like ``CeilingRetryPolicy``, this policy is injected on ``LocalModelGateway``
    with safe defaults so pinned ``StagingConfiguration`` hashes remain
    unaffected while preventing historical high-resolution images from
    accumulating unboundedly across multimodal turns.

    When inline image count or cumulative raw bytes exceed the configured
    budget, images from the current turn (when ``keep_latest_turn_images`` is
    enabled) and the newest historical images within budget stay inline while
    older images degrade to compact metadata placeholders carrying a
    ``media_inspect`` hint.  Evidence handles and ``reply_images`` stay intact.
    """

    enabled: bool = True
    max_inline_images: int = DEFAULT_MAX_INLINE_IMAGES
    max_inline_bytes: int = DEFAULT_MAX_INLINE_BYTES
    keep_latest_turn_images: bool = True
    per_image_base_estimate: int = DEFAULT_PER_IMAGE_BASE_ESTIMATE
    bytes_per_estimate_unit: int = DEFAULT_BYTES_PER_ESTIMATE_UNIT

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("image history budget needs an explicit switch")
        if type(self.max_inline_images) is not int or not 0 <= self.max_inline_images <= 64:
            raise ValueError("max_inline_images must be between 0 and 64")
        if type(self.max_inline_bytes) is not int or not 0 <= self.max_inline_bytes <= 256 * 1024 * 1024:
            raise ValueError("max_inline_bytes must be a bounded nonnegative byte count")
        if type(self.keep_latest_turn_images) is not bool:
            raise ValueError("keep_latest_turn_images must be an explicit boolean")
        if type(self.per_image_base_estimate) is not int or not 0 <= self.per_image_base_estimate <= 1_000_000:
            raise ValueError("per_image_base_estimate must be bounded")
        if type(self.bytes_per_estimate_unit) is not int or not 1 <= self.bytes_per_estimate_unit <= 1_000_000:
            raise ValueError("bytes_per_estimate_unit must be a positive integer")


def rendered_image_bytes(image) -> int:
    """Return raw byte length for one rendered prompt image or video frame."""
    descriptor = getattr(image, "descriptor", None)
    byte_count = getattr(descriptor, "byte_count", None)
    if type(byte_count) is int and byte_count >= 0:
        return byte_count
    data = getattr(image, "data_base64", "")
    if isinstance(data, str) and data:
        padding = 2 if data.endswith("==") else 1 if data.endswith("=") else 0
        return max(0, (len(data) * 3) // 4 - padding)
    return 0


def select_inline_image_indices(
    entries,
    policy: ImageHistoryBudgetPolicy | None = None,
    *,
    current_parent_source_id: str | None = None,
) -> frozenset[int]:
    """Return the indices of rendered image entries that stay inline.

    Each item in ``entries`` is a tuple ``(index, parent_source_id, byte_count,
    is_current_input)`` ordered chronologically from oldest to newest.

    - When ``policy.enabled`` is ``False``, every entry stays inline.
    - When ``policy.max_inline_images == 0``, no entry stays inline.
    - When ``policy.keep_latest_turn_images`` is ``True``:
      - All entries belonging to the active current turn (matching
        ``current_parent_source_id`` or ``is_current_input``; or, when
        ``current_parent_source_id`` is absent, sharing the newest entry's
        ``parent_source_id``) are preserved inline so the model is never
        blinded to images attached in the current turn (already bounded at
        intake by ``MAX_INPUT_IMAGES`` / ``MAX_TURN_BYTES`` and still subject
        to ``provider._estimate()`` fail-fast checks).
      - Older historical entries are admitted newest-first only while both
        ``kept_count < policy.max_inline_images`` and
        ``kept_bytes + byte_count <= policy.max_inline_bytes``.
    - When ``policy.keep_latest_turn_images`` is ``False``, every entry is
      strictly governed newest-first by ``max_inline_images`` and
      ``max_inline_bytes``.
    """
    active = ImageHistoryBudgetPolicy() if policy is None else policy
    if not entries:
        return frozenset()
    if not active.enabled:
        return frozenset(item[0] for item in entries)
    if active.max_inline_images <= 0:
        return frozenset()

    latest_indices = []
    older_indices = []
    if active.keep_latest_turn_images:
        if current_parent_source_id is not None:
            for item in entries:
                if item[1] == current_parent_source_id or item[3]:
                    latest_indices.append(item)
                else:
                    older_indices.append(item)
        else:
            newest_parent = entries[-1][1]
            for item in entries:
                if item[1] == newest_parent or item[3]:
                    latest_indices.append(item)
                else:
                    older_indices.append(item)
    else:
        older_indices = list(entries)

    kept = set()
    kept_count = 0
    kept_bytes = 0

    for idx, _parent_id, size_bytes, _is_current in latest_indices:
        kept.add(idx)
        kept_count += 1
        kept_bytes += max(0, int(size_bytes))

    for idx, _parent_id, size_bytes, _is_current in reversed(older_indices):
        size = max(0, int(size_bytes))
        if kept_count >= active.max_inline_images:
            break
        if kept_bytes + size > active.max_inline_bytes:
            continue
        kept.add(idx)
        kept_count += 1
        kept_bytes += size

    return frozenset(kept)


def estimate_inline_image_weight(
    image_count: int,
    image_bytes: int = 0,
    policy: ImageHistoryBudgetPolicy | None = None,
) -> int:
    """Combine per-image baseline with actual inline byte weight."""
    active = ImageHistoryBudgetPolicy() if policy is None else policy
    count = max(0, int(image_count))
    total_bytes = max(0, int(image_bytes))
    if count == 0 and total_bytes == 0:
        return 0
    byte_weight = (
        (total_bytes + active.bytes_per_estimate_unit - 1) // active.bytes_per_estimate_unit
        if total_bytes > 0 else 0
    )
    return active.per_image_base_estimate * count + byte_weight
