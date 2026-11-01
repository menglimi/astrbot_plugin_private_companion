"""One text and speech plan shared by generation and offline previews."""

from dataclasses import dataclass
from logging import getLogger

from .foreground import ClaimKind
from .text_processing import plan_leak_guard, strip_internal_expression_protocol
from .voice_reply import separate_spoken, speech_candidates, speech_decisions


logger = getLogger(__name__)


def observed_statements(draft):
    return tuple(claim.statement for claim in draft.claims if claim.kind is ClaimKind.OBSERVED)


def trim_blank_edges(text):
    """Remove empty edge lines without changing indentation or internal formatting."""
    lines = text.splitlines(keepends=True)
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return "".join(lines[start:end]).rstrip("\r\n")


@dataclass(frozen=True)
class PreparedReply:
    visible: str
    source_parts: tuple[str, ...]
    parts: tuple[str, ...]
    spans: tuple
    kept_indices: tuple[int, ...]

    def speech(self, policy):
        candidates = None
        if policy.uses_model_tags:
            originals = speech_candidates(self.visible, self.source_parts, self.spans, policy)
            candidates = tuple(originals[index] for index in self.kept_indices)
        return speech_decisions(self.visible, self.parts, self.spans, policy, candidates=candidates)


def prepare_reply(text, segmentation, presentation, voice=None, *, statements=(), split=None):
    raw_text = text
    text = strip_internal_expression_protocol(text)
    protocol_text = text
    # 上游可能把隐藏的计划写进正文；判定与原生发送出口共用同一个纯函数。
    text, plan_spans, plan_reasons = plan_leak_guard(text)
    if plan_spans:
        logger.debug("[CompanionV2][开发者追踪] 正文流水线 stage=plan_leak spans=%s reasons=%s raw=%r visible=%r",
                     plan_spans, plan_reasons, protocol_text, text)
        if not text.strip():
            raise ValueError("presentation removed all visible reply text")
    text, leaked = presentation.framework_guard(text)
    framework_text = text
    if leaked:
        logger.debug("[CompanionV2][开发者追踪] 正文流水线 stage=framework_guard leaked=true raw=%r protocol=%r visible=%r",
                     raw_text, protocol_text, text)
        return PreparedReply(text, (text,), (text,), (), (0,))
    replaced = presentation.replace_text(text, statements=statements)
    visible, spans = separate_spoken(replaced, voice)
    parts = segmentation.parts(visible)
    if split is not None and not segmentation.enabled:
        original = "".join(parts)
        parts = split(original)
        if (type(parts) is not tuple or not 1 <= len(parts) <= 64
                or any(type(part) is not str or not part.strip() for part in parts)
                or "".join(parts) != original):
            raise ValueError("custom segmentation must preserve the complete reply")
    # Authored speech stays literal after its wrapper is removed for display.
    cleaned = presentation.clean_parts(parts, statements=(*statements, *(speech for _, speech in spans)))
    # Presentation removes only empty edge lines; keep the source parts and
    # authored speech offsets intact, including indentation inside code.
    cleaned = tuple(trim_blank_edges(part) for part in cleaned)
    indices = tuple(index for index, part in enumerate(cleaned) if part.strip())
    if not indices:
        logger.debug("[CompanionV2][开发者追踪] 正文流水线 stage=empty_after_cleanup raw=%r protocol=%r framework=%r replaced=%r visible=%r spans=%r parts=%r cleaned=%r",
                     raw_text, protocol_text, framework_text, replaced, visible, spans, parts, cleaned)
        raise ValueError("presentation removed all visible reply text")
    logger.debug("[CompanionV2][开发者追踪] 正文流水线 raw=%r protocol=%r framework=%r replaced=%r visible=%r spans=%r parts=%r cleaned=%r final=%r",
                 raw_text, protocol_text, framework_text, replaced, visible, spans, parts, cleaned,
                 tuple(cleaned[index] for index in indices))
    return PreparedReply(visible, parts, tuple(cleaned[index] for index in indices), spans, indices)
