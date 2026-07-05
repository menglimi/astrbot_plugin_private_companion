# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from dataclasses import dataclass

NUMERIC_FID_MIN_LENGTH = 12

LATEST_ALIASES = {"", "0", "1", "latest", "newest", "最新", "最新一条", "最近", "第一条", "第1条"}
LAST_ALIASES = {"-1", "last", "最后", "最后一条", "末条"}


@dataclass(slots=True)
class QzonePostSelection:
    target_id: str = ""
    pos: int = 0
    limit: int = 1
    selector: str = "latest"
    fid: str = ""
    explicit_target: bool = False
    explicit_selector: bool = False

    @property
    def is_last(self) -> bool:
        return self.selector == "last"


def _looks_like_fid(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if re.fullmatch(rf"\d{{{NUMERIC_FID_MIN_LENGTH},}}", text):
        return True
    if _parse_index(text) is not None:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_.:-]{6,}", text))


def _parse_index(value: str) -> int | None:
    text = str(value or "").strip()
    lowered = text.lower()
    if text in LATEST_ALIASES or lowered in LATEST_ALIASES:
        return 0
    if text in LAST_ALIASES or lowered in LAST_ALIASES:
        return -1
    normalized = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    match = re.fullmatch(r"第\s*(\d+)\s*(?:条|條)?", normalized)
    if not match:
        match = re.fullmatch(r"(\d+)\s*(?:条|條)?", normalized)
    if not match:
        return None
    return max(0, int(match.group(1)) - 1)


def parse_qzone_post_selection(
    *,
    user_id: str = "",
    selector: str = "",
    pos: int = 0,
    fid: str = "",
) -> QzonePostSelection:
    target_id = str(user_id or "").strip()
    raw = str(selector or "").strip()
    explicit_target = bool(target_id)
    explicit_selector = bool(raw or fid)

    at_match = re.search(r"\[CQ:at,qq=(\d+)[^\]]*\]|@(\d{5,})", raw)
    if at_match:
        target_id = at_match.group(1) or at_match.group(2) or target_id
        explicit_target = True
        raw = re.sub(r"\[CQ:at,qq=\d+[^\]]*\]|@\d{5,}", " ", raw, count=1).strip()

    tokens = raw.split()
    if not target_id and tokens and re.fullmatch(r"\d{5,}", tokens[0]) and not _looks_like_fid(tokens[0]):
        target_id = tokens.pop(0)
        explicit_target = True

    explicit_fid = str(fid or "").strip()
    if explicit_fid:
        return QzonePostSelection(target_id=target_id, fid=explicit_fid, selector="fid", explicit_target=explicit_target, explicit_selector=True)

    if tokens and _looks_like_fid(tokens[0]):
        return QzonePostSelection(target_id=target_id, fid=tokens[0], selector="fid", explicit_target=explicit_target, explicit_selector=True)

    parsed = _parse_index(tokens[0]) if tokens else _parse_index(raw)
    if parsed is None:
        parsed = max(0, int(pos or 0))
        explicit_selector = explicit_selector or parsed > 0
    if parsed < 0:
        return QzonePostSelection(target_id=target_id, pos=0, limit=10, selector="last", explicit_target=explicit_target, explicit_selector=True)
    return QzonePostSelection(target_id=target_id, pos=parsed, limit=1, selector="index" if parsed > 0 else "latest", explicit_target=explicit_target, explicit_selector=explicit_selector)
