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
    current_parent_source_ids=None,
) -> frozenset[int]:
    """Return the indices of rendered image entries that stay inline.

    Each item in ``entries`` is a tuple ``(index, parent_source_id, byte_count,
    is_current_input)`` or ``(index, parent_source_id, byte_count,
    is_current_input, group_key)`` ordered chronologically from oldest to
    newest.  Entries sharing the same ``group_key`` (for example sampled frames
    of one ``PromptAnimation``) are kept or omitted atomically so a historical
    animation is never truncated to a partial tail of frames.

    - When ``policy.enabled`` is ``False``, every entry stays inline.
    - When ``policy.max_inline_images == 0``, no entry stays inline.
    - When ``policy.keep_latest_turn_images`` is ``True``:
      - All groups belonging to the active current turn (matching
        ``current_parent_source_id`` / ``current_parent_source_ids`` or marked
        ``is_current_input``; or, when no current parent IDs are provided,
        sharing the newest entry's ``parent_source_id``) are preserved inline
        so the model is never blinded to current-turn attachments, debounced
        merged messages, freshly generated images, or live screen captures
        (still bounded by intake limits and ``provider._estimate()`` fail-fast).
      - Older historical groups are admitted newest-first atomically only while
        both ``kept_count + group_count <= policy.max_inline_images`` and
        ``kept_bytes + group_bytes <= policy.max_inline_bytes``.
    - When ``policy.keep_latest_turn_images`` is ``False``, every group is
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

    current_parents: set[str] = set()
    if isinstance(current_parent_source_id, str) and current_parent_source_id:
        current_parents.add(current_parent_source_id)
    if current_parent_source_ids is not None:
        for identifier in current_parent_source_ids:
            if isinstance(identifier, str) and identifier:
                current_parents.add(identifier)

    # Collapse entries sharing a group_key into ordered atomic units.
    groups = []
    group_positions = {}
    for item in entries:
        idx = item[0]
        parent_id = item[1]
        size = max(0, int(item[2]))
        is_current = bool(item[3])
        group_key = item[4] if len(item) >= 5 else idx
        pos = group_positions.get(group_key)
        if pos is None:
            group_positions[group_key] = len(groups)
            groups.append({
                "indices": [idx],
                "parent_ids": {parent_id},
                "bytes": size,
                "is_current": is_current,
            })
        else:
            group = groups[pos]
            group["indices"].append(idx)
            group["parent_ids"].add(parent_id)
            group["bytes"] += size
            group["is_current"] = group["is_current"] or is_current

    latest_groups = []
    older_groups = []
    if active.keep_latest_turn_images:
        if current_parents:
            for group in groups:
                if group["is_current"] or bool(group["parent_ids"] & current_parents):
                    latest_groups.append(group)
                else:
                    older_groups.append(group)
        else:
            newest_parent = entries[-1][1]
            for group in groups:
                if group["is_current"] or newest_parent in group["parent_ids"]:
                    latest_groups.append(group)
                else:
                    older_groups.append(group)
    else:
        older_groups = list(groups)

    kept = set()
    kept_count = 0
    kept_bytes = 0

    for group in latest_groups:
        kept.update(group["indices"])
        kept_count += len(group["indices"])
        kept_bytes += group["bytes"]

    for group in reversed(older_groups):
        group_count = len(group["indices"])
        if kept_count >= active.max_inline_images:
            break
        if kept_count + group_count > active.max_inline_images:
            continue
        if kept_bytes + group["bytes"] > active.max_inline_bytes:
            continue
        kept.update(group["indices"])
        kept_count += group_count
        kept_bytes += group["bytes"]

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
