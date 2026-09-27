# -*- coding: utf-8 -*-
"""EventDispatchResultProactivePart02Mixin。

由 tools/split_mixin_domain.py 从 event_dispatch_result_proactive.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 625 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 EventDispatchResultProactiveMixin）。
"""
from __future__ import annotations

from .event_dispatch_result_proactive_shared import (
    _SEGMENTED_GENERATED_PUNCTUATION_MARKER,
    _SEGMENTED_PROTECTED_FILE_SUFFIX_PATTERN,
    _SEGMENTED_PROTECTED_LITERAL_PATTERN,
    _expand_segmented_width_variant_words,
)
from .event_dispatch_result_proactive_shared import Any
from .event_dispatch_result_proactive_shared import _persona_value
from .event_dispatch_result_proactive_shared import _safe_int
from .event_dispatch_result_proactive_shared import _single_line
from .event_dispatch_result_proactive_shared import json
from .event_dispatch_result_proactive_shared import logger
from .event_dispatch_result_proactive_shared import protect_markdown_blocks
from .event_dispatch_result_proactive_shared import re
from .event_dispatch_result_proactive_shared import runtime_persona_setting



class EventDispatchResultProactivePart02Mixin:
    """EventDispatchResultProactivePart02Mixin（从 EventDispatchResultProactiveMixin 拆出）。"""


    def _split_proactive_text(
        self,
        text: str,
        *,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        disable_segmenting: bool = False,
        event: Any | None = None,
        umo: str = "",
        chat_type: str = "",
        max_segments_override: int | None = None,
        force_common_transforms: bool = False,
        common_transforms_only: bool = False,
    ) -> list[str]:
        # Media attachments are sent by the caller as separate components/messages.
        # Segment limits only apply to text, so image_path/extra_components are
        # intentionally ignored here and kept only for compatibility.
        normalized = str(text or "").strip()
        if not normalized:
            return []
        tts_normalizer = getattr(self, "_normalize_tts_tags", None)
        if callable(tts_normalizer) and re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", normalized, flags=re.IGNORECASE):
            try:
                normalized = str(tts_normalizer(normalized) or normalized).strip()
            except Exception:
                pass
        if disable_segmenting:
            return [normalized]
        checker = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        if callable(checker):
            if not checker("enable_segmented_proactive_reply"):
                return [normalized]
        elif not _persona_value(self, 'enable_segmented_proactive_reply', False):
            return [normalized]
        if (
            not force_common_transforms
            and not bool(_persona_value(self, "enable_segmented_plugin_rules", True))
        ):
            return [normalized]
        if event is not None or umo or chat_type:
            chat_scope_checker = getattr(self, "_segmented_chat_scope_allows", None)
            if callable(chat_scope_checker):
                resolved_chat_type = str(chat_type or "").strip().lower()
                if resolved_chat_type not in {"private", "group"}:
                    if event is not None:
                        resolver = getattr(self, "_segmented_chat_type_for_event", None)
                        resolved_chat_type = resolver(event) if callable(resolver) else "private"
                    else:
                        resolver = getattr(self, "_segmented_chat_type_for_umo", None)
                        resolved_chat_type = resolver(umo) if callable(resolver) else "private"
                if not chat_scope_checker(resolved_chat_type):
                    return [normalized]
        setting_getter = getattr(self, "_segmented_setting", None)

        def setting(name: str, default: Any) -> Any:
            if not callable(setting_getter):
                return getattr(self, f"segmented_proactive_{name}", default)
            return setting_getter(
                name,
                event=event,
                umo=umo,
                chat_type=chat_type,
                default=default,
            )

        markdown = protect_markdown_blocks(normalized)
        source_length = len(normalized)
        if markdown.active:
            normalized = markdown.protected_text
            if event is not None:
                try:
                    setattr(event, "_private_companion_segmented_markdown_detected", True)
                except Exception:
                    pass

        def restore_markdown(value: Any) -> str:
            return markdown.restore(value)

        def contains_markdown(value: Any) -> bool:
            return markdown.contains_token(value)

        replaced_text, replacement_count = self._apply_segmented_content_replacements(
            normalized,
            event=event,
            umo=umo,
            chat_type=chat_type,
        )
        if replacement_count > 0:
            candidate = replaced_text.strip()
            if candidate:
                normalized = candidate
            else:
                logger.warning("主动分段内容替换会清空整条文本，已保留原文")
        threshold = max(20, _safe_int(setting("threshold", 500), 500, 20, 1024))
        min_segment_chars = max(1, _safe_int(setting("min_segment_chars", 8), 8, 1, 40))
        max_segments = max(
            1,
            _safe_int(
                max_segments_override
                if max_segments_override is not None
                else setting("max_segments", 3),
                3,
                1,
                8,
            ),
        )
        if source_length > threshold and not common_transforms_only:
            return [restore_markdown(normalized)]

        cleanup_pattern: re.Pattern[str] | None = None
        cleanup_words: list[str] = []
        match_width_variants = bool(setting("match_width_variants", True))
        cleanup_enabled = bool(
            runtime_persona_setting(
                self,
                "enable_segmented_proactive_content_cleanup",
                False,
            )
        )
        split_mode = str(setting("split_mode", "regex") or "regex")
        if cleanup_enabled:
            if split_mode == "words":
                configured_words = setting("content_cleanup_words", [])
                if isinstance(configured_words, list):
                    cleanup_words = [str(item) for item in configured_words if str(item) != ""]
                raw_cleanup_rule = str(setting("content_cleanup_rule", '[\\n]') or "")
                parsed_words: list[Any] | None = None
                if not cleanup_words and raw_cleanup_rule:
                    try:
                        parsed = json.loads(raw_cleanup_rule)
                        if isinstance(parsed, list):
                            parsed_words = parsed
                    except Exception:
                        parsed_words = None
                    if parsed_words is None:
                        parsed_words = re.split(r"[,，、\n]+", raw_cleanup_rule)
                    cleanup_words = [str(item) for item in parsed_words if str(item) != ""]
                if match_width_variants:
                    cleanup_words = _expand_segmented_width_variant_words(cleanup_words)
            elif setting("content_cleanup_rule", '[\\n]'):
                try:
                    cleanup_pattern = re.compile(setting("content_cleanup_rule", '[\\n]'))
                except re.error as e:
                    logger.warning("主动分段内容清理正则无效,跳过清理: %s", e)

        def _protected_cleanup_chunks(value: str) -> list[tuple[str, bool]]:
            bracket_pairs = {
                "(": ")",
                "（": "）",
                "[": "]",
                "【": "】",
                "{": "}",
                "「": "」",
                "『": "』",
                "《": "》",
            }
            bracket_closers = {closer: opener for opener, closer in bracket_pairs.items()}
            quote_pairs = {"\"": "\"", "“": "”", "'": "'", "‘": "’"}
            chunks: list[tuple[str, bool]] = []
            current: list[str] = []
            protected = False
            bracket_stack: list[str] = []
            quote_close = ""
            text_value = str(value or "")
            last_pos = 0

            def flush() -> None:
                nonlocal current
                if current:
                    chunks.append(("".join(current), protected))
                    current = []

            def feed_plain(text_part: str) -> None:
                nonlocal protected, quote_close
                for char in text_part:
                    if not protected and char in bracket_pairs:
                        flush()
                        protected = True
                        bracket_stack.append(bracket_pairs[char])
                        current.append(char)
                        continue
                    if not protected and char in quote_pairs:
                        flush()
                        protected = True
                        quote_close = quote_pairs[char]
                        current.append(char)
                        continue

                    current.append(char)
                    if protected:
                        if quote_close:
                            if char == quote_close:
                                quote_close = ""
                                if not bracket_stack:
                                    flush()
                                    protected = False
                        elif char in bracket_pairs:
                            bracket_stack.append(bracket_pairs[char])
                        elif bracket_stack and char == bracket_stack[-1]:
                            bracket_stack.pop()
                            if not bracket_stack:
                                flush()
                                protected = False
                        elif char in bracket_closers and not bracket_stack:
                            flush()
                            protected = False

            for match in _SEGMENTED_PROTECTED_LITERAL_PATTERN.finditer(text_value):
                feed_plain(text_value[last_pos:match.start()])
                flush()
                chunks.append((match.group(0), True))
                last_pos = match.end()
            feed_plain(text_value[last_pos:])
            flush()
            return chunks

        def _protect_segmented_literals(value: str) -> tuple[str, dict[str, str]]:
            replacements: dict[str, str] = {}
            protected_parts: list[str] = []
            for chunk, protected in _protected_cleanup_chunks(str(value or "")):
                if not protected:
                    protected_parts.append(chunk)
                    continue
                token = f"PCSEGTOKEN{len(replacements)}X"
                replacements[token] = chunk
                protected_parts.append(token)
            return "".join(protected_parts), replacements

        def _restore_segmented_literals(value: str, replacements: dict[str, str]) -> str:
            restored = str(value or "")
            for token, original in replacements.items():
                restored = restored.replace(token, original)
            return restored

        protected_normalized, protected_literals = _protect_segmented_literals(normalized)

        def _split_words_outside_protected(value: str, words: list[str]) -> list[str]:
            sorted_words = sorted({str(word) for word in words if str(word) != ""}, key=len, reverse=True)
            if not sorted_words:
                return [str(value or "")]
            segments: list[str] = []
            current: list[str] = []
            url_pattern = re.compile(r"(?i)^(?:https?://|www\.)")

            def protected_starts_with_split_word(chunk: str) -> bool:
                stripped = str(chunk or "").lstrip()
                if _SEGMENTED_PROTECTED_FILE_SUFFIX_PATTERN.fullmatch(stripped):
                    return False
                return any(stripped.startswith(word) for word in sorted_words)

            def push_current() -> None:
                if current:
                    segments.append("".join(current))
                    current.clear()

            def feed_plain(chunk: str) -> None:
                index = 0
                text_chunk = str(chunk or "")
                cjk_char_pattern = re.compile(r"[\u3400-\u9fff\u3040-\u30ff]")

                def next_non_space_char(start: int) -> str:
                    pos = start
                    while pos < len(text_chunk) and text_chunk[pos].isspace():
                        pos += 1
                    return text_chunk[pos] if pos < len(text_chunk) else ""

                while index < len(text_chunk):
                    matched = ""
                    for word in sorted_words:
                        if text_chunk.startswith(word, index):
                            matched = word
                            break
                    if matched:
                        delimiter = matched
                        if matched == ".":
                            end = index + len(matched)
                            while end < len(text_chunk) and text_chunk[end] == ".":
                                delimiter += text_chunk[end]
                                end += 1
                            current.append(delimiter)
                            if delimiter == "." and end < len(text_chunk) and text_chunk[end].isdigit():
                                index = end
                                continue
                            push_current()
                            index = end
                            continue
                        if matched == ",":
                            end = index + 1
                            current.append(delimiter)
                            if end < len(text_chunk) and text_chunk[end].isdigit():
                                index = end
                                continue
                            push_current()
                            index = end
                            continue
                        if matched in {"…", "~", "～"}:
                            end = index + len(matched)
                            while end < len(text_chunk) and text_chunk.startswith(matched, end):
                                delimiter += matched
                                end += len(matched)
                            current.append(delimiter)
                            if matched in {"~", "～"} and next_non_space_char(end).isdigit():
                                index = end
                                continue
                            push_current()
                            index = end
                            continue
                        if matched and all(char in {"-", "－", "—"} for char in matched):
                            end = index + len(matched)
                            current.append(delimiter)
                            if next_non_space_char(end).isdigit():
                                index = end
                                continue
                            push_current()
                            index = end
                            continue
                        current.append(delimiter)
                        push_current()
                        index += len(matched)
                    else:
                        char = text_chunk[index]
                        if char.isspace():
                            previous = current[-1] if current else ""
                            following = next_non_space_char(index)
                            if previous and following and cjk_char_pattern.fullmatch(previous) and cjk_char_pattern.fullmatch(following):
                                current.append("，")
                                push_current()
                                index += 1
                                while index < len(text_chunk) and text_chunk[index].isspace():
                                    index += 1
                                continue
                        current.append(text_chunk[index])
                        index += 1

            for chunk, protected in _protected_cleanup_chunks(str(value or "")):
                if protected:
                    if current and protected_starts_with_split_word(chunk):
                        push_current()
                    current.append(chunk)
                    if url_pattern.match(chunk.strip()):
                        push_current()
                else:
                    feed_plain(chunk)
            push_current()
            return segments

        def _normalize_cjk_chat_spaces(value: str) -> str:
            cjk = r"\u3400-\u9fff\u3040-\u30ff"
            cjk_punct = r"，。！？；：、…~～"
            parts: list[str] = []
            for chunk, protected in _protected_cleanup_chunks(str(value or "")):
                if protected:
                    parts.append(chunk)
                    continue
                cleaned_chunk = re.sub(rf"(?<=[{cjk_punct}])\s+(?=[{cjk}])", "", chunk)
                cleaned_chunk = re.sub(rf"(?<=[{cjk}])\s+(?=[{cjk_punct}])", "", cleaned_chunk)
                cleaned_chunk = re.sub(rf"(?<=[{cjk_punct}])\s+(?=[{cjk_punct}])", "", cleaned_chunk)
                cleaned_chunk = re.sub(rf"(?<=[{cjk}])\s+(?=[{cjk}])", "，", cleaned_chunk)
                parts.append(cleaned_chunk)
            return "".join(parts).strip()

        def _clean_segment(segment: str) -> str:
            original = str(segment or "")
            cleaned_parts: list[str] = []
            cleanup_scope = str(setting("content_cleanup_scope", "all") or "all")

            def _strip_trailing_words(value: str, words: list[str]) -> str:
                stripped = str(value or "").rstrip()
                if not words:
                    return stripped
                sorted_words = sorted({str(word) for word in words if str(word) != ""}, key=len, reverse=True)
                changed = True
                while changed and stripped:
                    changed = False
                    for word in sorted_words:
                        if stripped.endswith(word):
                            stripped = stripped[: -len(word)].rstrip()
                            changed = True
                            break
                return stripped

            def _strip_trailing_pattern(value: str, pattern: re.Pattern[str]) -> str:
                stripped = str(value or "").rstrip()
                while stripped:
                    trailing_match = None
                    for match in pattern.finditer(stripped):
                        if match.end() == len(stripped) and match.start() != match.end():
                            trailing_match = match
                    if trailing_match is None:
                        break
                    stripped = stripped[: trailing_match.start()].rstrip()
                return stripped

            for chunk, protected in _protected_cleanup_chunks(original):
                if protected:
                    cleaned_parts.append(chunk)
                    continue
                cleaned_chunk = chunk
                if cleanup_words:
                    if cleanup_scope == "trailing":
                        cleaned_chunk = _strip_trailing_words(cleaned_chunk, cleanup_words)
                    else:
                        for word in cleanup_words:
                            cleaned_chunk = cleaned_chunk.replace(word, "")
                elif cleanup_pattern:
                    if cleanup_scope == "trailing":
                        cleaned_chunk = _strip_trailing_pattern(cleaned_chunk, cleanup_pattern)
                    else:
                        cleaned_chunk = cleanup_pattern.sub("", cleaned_chunk)
                cleaned_parts.append(cleaned_chunk)
            cleaned = "".join(cleaned_parts)
            cleaned = self._strip_leading_sentence_boundary_artifacts(cleaned)
            return _normalize_cjk_chat_spaces(cleaned)

        def _visible_len(value: str) -> int:
            visible = restore_markdown(value).replace(
                _SEGMENTED_GENERATED_PUNCTUATION_MARKER,
                "",
            )
            return len(re.sub(r"\s+", "", visible))

        def _generated_punctuation(value: str) -> str:
            return f"{_SEGMENTED_GENERATED_PUNCTUATION_MARKER}{value}"

        def _collapse_generated_repeated_punctuation(value: str) -> str:
            result = str(value or "")
            for punctuation in ("，", ",", "。", "！", "？", "!", "?", "…", "~", "～"):
                marked = _generated_punctuation(punctuation)
                result = re.sub(
                    rf"(?:{re.escape(marked)}){{2,}}",
                    lambda _match, token=marked: token,
                    result,
                )
            return result

        def _strip_generated_punctuation_markers(value: str) -> str:
            return str(value or "").replace(
                _SEGMENTED_GENERATED_PUNCTUATION_MARKER,
                "",
            )

        def _is_atomic_creative_excerpt(value: str) -> bool:
            stripped = str(value or "").strip()
            return bool(
                stripped.startswith("「")
                and stripped.endswith("」")
                and _visible_len(stripped[1:-1])
                >= min_segment_chars
            )

        def _expand_creative_excerpt_atoms(value: str) -> list[str]:
            expanded: list[str] = []
            current: list[str] = []

            def push_current() -> None:
                joined = "".join(current).strip()
                current.clear()
                if joined:
                    expanded.append(joined)

            for chunk, protected in _protected_cleanup_chunks(str(value or "")):
                if protected and _is_atomic_creative_excerpt(chunk):
                    push_current()
                    expanded.append(str(chunk).strip())
                else:
                    current.append(chunk)
            push_current()
            return expanded or [str(value or "")]

        if common_transforms_only:
            cleaned = _clean_segment(normalized)
            cleaned = restore_markdown(cleaned)
            return [cleaned] if cleaned else []

        def _is_soft_short_segment(value: str) -> bool:
            cleaned = _single_line(_strip_generated_punctuation_markers(value), 60)
            if not cleaned:
                return False
            body = re.sub(r"[。！？!?…~～,.，、\s]+$", "", cleaned)
            if _visible_len(cleaned) <= min_segment_chars:
                return True
            if re.search(r"(?:\.{2,}|…{1,}|~{2,}|～{2,})$", cleaned):
                return False
            return body in {
                "哈哈",
                "哈",
                "嗯",
                "唔",
                "诶",
                "欸",
                "啊",
                "呀",
                "我也觉得",
                "确实",
                "真的",
                "对吧",
                "不是",
                "那个",
                "还有",
            }

        def _join_segment_pair(left: str, right: str) -> str:
            left = str(left or "").strip()
            right = str(right or "").strip()
            if not left:
                return right
            if not right:
                return left
            if contains_markdown(left) or contains_markdown(right):
                return f"{left.rstrip()}\n\n{right.lstrip()}"
            if right.startswith(("（", "(")):
                return _normalize_cjk_chat_spaces(f"{left}{right}")
            visible_left = _strip_generated_punctuation_markers(left)
            if re.fullmatch(r"(?:…+|\.{2,})", visible_left):
                return _normalize_cjk_chat_spaces(f"{left}{right}")
            if re.search(r"[！？!?]$", left):
                return _normalize_cjk_chat_spaces(f"{left} {right}".strip())
            generated_comma = _generated_punctuation("，")
            softened = re.sub(r"[。…~～]+$", generated_comma, left)
            softened = re.sub(r"[!?！？]+$", generated_comma, softened)
            if not re.search(r"[，,、\s]$", softened):
                softened += generated_comma
            softened = _collapse_generated_repeated_punctuation(softened)
            return _normalize_cjk_chat_spaces(f"{softened}{right.lstrip()}")

        def _merge_segments(raw: list[str]) -> list[str]:
            segments = [str(item or "").strip() for item in raw if str(item or "").strip()]
            if len(segments) <= 1:
                return segments
            min_chars = min_segment_chars
            merged: list[str] = []
            index = 0
            while index < len(segments):
                current = segments[index]
                if _is_atomic_creative_excerpt(current):
                    merged.append(current)
                    index += 1
                    continue
                while index + 1 < len(segments) and (
                    _visible_len(current) < min_chars
                    or _is_soft_short_segment(current)
                    or (len(merged) >= max(0, max_segments - 1))
                ) and not _is_atomic_creative_excerpt(segments[index + 1]):
                    current = _join_segment_pair(current, segments[index + 1])
                    index += 1
                if (
                    merged
                    and not _is_atomic_creative_excerpt(merged[-1])
                    and (_visible_len(current) < min_chars or _is_soft_short_segment(current))
                ):
                    merged[-1] = _join_segment_pair(merged[-1], current)
                else:
                    merged.append(current)
                index += 1
            while len(merged) > max_segments:
                merge_index = next(
                    (
                        pos
                        for pos in range(len(merged) - 2, -1, -1)
                        if not _is_atomic_creative_excerpt(merged[pos])
                        and not _is_atomic_creative_excerpt(merged[pos + 1])
                    ),
                    -1,
                )
                if merge_index < 0:
                    break
                merged[merge_index] = _join_segment_pair(
                    merged[merge_index],
                    merged[merge_index + 1],
                )
                del merged[merge_index + 1]
            return [
                _strip_generated_punctuation_markers(item)
                for item in merged
            ]

        if split_mode == "words":
            configured_split_words = setting(
                "split_words",
                ['。', '？', '！', '~', '…', '“'],
            )
            split_words = [word for word in configured_split_words if word] if isinstance(configured_split_words, list) else []
            if match_width_variants:
                split_words = _expand_segmented_width_variant_words(split_words)
            if "\n" not in split_words:
                split_words.append("\n")
            if not split_words:
                return [restore_markdown(normalized)]
            raw_segments = _split_words_outside_protected(normalized, split_words)
            segments: list[str] = []
            for segment in raw_segments:
                content = segment[0] if isinstance(segment, tuple) else segment
                if not isinstance(content, str):
                    continue
                for atom in _expand_creative_excerpt_atoms(content):
                    cleaned = _clean_segment(atom)
                    if cleaned:
                        segments.append(cleaned)
            segments = _merge_segments(segments)
            segments = [restore_markdown(segment) for segment in segments]
            return segments if segments and (len(segments) > 1 or cleanup_enabled) else [restore_markdown(normalized)]

        try:
            raw_segments = re.findall(
                setting("regex", '.*?[。？！~…\\n]+|.+$') or r".*?[。？！~…\n]+|.+$",
                protected_normalized,
                re.DOTALL | re.MULTILINE,
            )
        except re.error as e:
            logger.warning("主动分段正则无效,使用默认规则: %s", e)
            raw_segments = re.findall(r".*?[。？！~…\n]+|.+$", protected_normalized, re.DOTALL | re.MULTILINE)

        segments = []
        for segment in raw_segments:
            content = segment[0] if isinstance(segment, tuple) else segment
            if not isinstance(content, str):
                continue
            restored = _restore_segmented_literals(content, protected_literals)
            for atom in _expand_creative_excerpt_atoms(restored):
                cleaned = _clean_segment(atom)
                if cleaned:
                    segments.append(cleaned)
        segments = _merge_segments(segments)
        segments = [restore_markdown(segment) for segment in segments]
        return segments if segments and (len(segments) > 1 or cleanup_enabled) else [restore_markdown(normalized)]
