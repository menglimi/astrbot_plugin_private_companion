from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


__all__ = ["PhotoReferenceFeedback", "analyze_photo_reference_feedback"]


@dataclass(frozen=True)
class PhotoReferenceFeedback:
    regenerate_requested: bool
    issues: tuple[str, ...]
    confidence: float
    source: str


def analyze_photo_reference_feedback(text: Any) -> PhotoReferenceFeedback:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    if not normalized:
        return PhotoReferenceFeedback(False, (), 0.0, "none")
    patterns = (
        (
            "face_mismatch",
            r"脸(?:完全|很|也)?不(?:像|对)|人(?:物)?不(?:像|对)|长相不对|身份不对"
            r"|face\s+(?:doesn't|does not)\s+(?:match|look right)|wrong\s+(?:face|identity)",
        ),
        (
            "outfit_mismatch",
            r"衣服(?:也)?不对|服装(?:也)?不对|穿错(?:了|衣服)?|没(?:有)?换(?:衣服|服装)|还是(?:原来|之前|上一张)的(?:衣服|服装)"
            r"|wrong\s+(?:outfit|clothes)|outfit\s+(?:doesn't|does not)\s+match",
        ),
        (
            "scene_not_changed",
            r"场景(?:根本|还是)?没(?:有)?换|背景(?:根本|还是)?没(?:有)?换|地方(?:根本|还是)?没(?:有)?换|还是(?:原来|之前|上一张)的(?:场景|背景|地方)"
            r"|scene\s+(?:didn't|did not)\s+change|same\s+(?:scene|background)",
        ),
        (
            "pose_mismatch",
            r"姿势不对|动作不对|没(?:有)?换(?:姿势|动作)|wrong\s+pose|pose\s+(?:doesn't|does not)\s+match",
        ),
        (
            "style_mismatch",
            r"画风不对|风格不对|没(?:有)?换(?:画风|风格)|wrong\s+style|style\s+(?:doesn't|does not)\s+match",
        ),
    )
    issues = tuple(
        issue for issue, pattern in patterns if re.search(pattern, normalized, flags=re.I)
    )
    regenerate = bool(
        re.search(
            r"重新(?:生成|画|做|来)(?:一张|一下)?|重(?:生成|画|做)(?:一张|一下)?|再生成(?:一张)?"
            r"|regenerate|generate\s+again|try\s+again|redo\s+(?:it|the\s+image)",
            normalized,
            flags=re.I,
        )
    )
    if not issues and not regenerate:
        return PhotoReferenceFeedback(False, (), 0.0, "none")
    return PhotoReferenceFeedback(regenerate, issues, 0.98, "rule")
