"""V1-compatible message boundaries; reserved tokens never become chat text."""

import html
import re


LLM_SEGMENT_MARKER = "<<PRIVATE_COMPANION_SPLIT>>"
_FUZZY_MARKER_MAX_DISTANCE = 2
_TOKENS = re.compile(
    r"(?:&lt;|&#0*60;|&#x0*3c;){2}\s*PRIVATE_COMPANION_SPLIT\s*(?:&gt;|&#0*62;|&#x0*3e;){2}"
    r"|\\<\\<\s*PRIVATE\s*_\s*COMPANION\s*_\s*SPLIT\s*\\>\\>"
    r"|\{\{\s*split_marker\s*\}\}|<<\s*PRIVATE\s*_\s*COMPANION\s*_\s*SPLIT\s*>>",
    re.IGNORECASE,
)
# Models occasionally insert a line break/space inside the marker, omit one
# angle bracket, or make a one-character typo.  Keep the candidate deliberately
# narrow: it must still be an angle-bracket token and the normalized value must
# be within a very small edit distance of the reserved marker.
_FUZZY_CANDIDATE = re.compile(
    r"(?:<\s*){0,2}<(?P<body>[^<>]{1,96}?)(?:>(?:\s*>)?(?:\s*>)?)", re.DOTALL)
_MARKDOWN = re.compile(
    r"(?m)^ {0,3}(?:>|[-+*] |\d+[.)] |#{1,6} |\||<[a-zA-Z!/])|^(?: {4}|\t)\S"
    r"|`|[*_|]|!?\[[^\n]*\]\(|<[a-zA-Z!/][^>\n]*>|(?m:^ {0,3}(?:[-*_] *){3,}$)"
)
_QUOTED = re.compile(r" {0,3}>")


def _marker_distance(left, right, limit=_FUZZY_MARKER_MAX_DISTANCE):
    """Return a bounded Levenshtein distance for one short protocol token."""
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, 1):
        current = [row]
        for column, right_char in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (left_char != right_char),
            ))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _normalize_fuzzy_candidate(match):
    value = html.unescape(match.group(0)).replace("\\", "")
    return "".join(value.split()).casefold()


def _repair_fuzzy_tokens(text):
    """Canonicalize only near-miss angle-bracket split markers.

    Placeholder/escaped forms retain their existing cleanup-only behavior.  A
    canonical replacement also removes accidental newlines inside a marker so
    the normal line-oriented boundary handling can process it.
    """
    if not text or LLM_SEGMENT_MARKER.casefold() in text.casefold():
        # The fast path still needs to remove an accidental newline between the
        # marker's two halves, so only skip the scan when there is no whitespace
        # in the candidate at all.
        if "\n" not in text and "\r" not in text and "\t" not in text:
            return text

    canonical = LLM_SEGMENT_MARKER.casefold()
    exact = tuple(_TOKENS.finditer(text))

    def replace(match):
        # An exact token may contain an angle-bracket substring that also looks
        # like a fuzzy candidate (notably the escaped ``\\<\\<...`` form).  Do
        # not let the fuzzy pass consume part of that already-supported token.
        if any(match.start() < known.end() and known.start() < match.end()
               and match.span() != known.span() for known in exact):
            return match.group(0)
        normalized = _normalize_fuzzy_candidate(match)
        if _marker_distance(normalized, canonical) <= _FUZZY_MARKER_MAX_DISTANCE:
            return LLM_SEGMENT_MARKER
        return match.group(0)

    return _FUZZY_CANDIDATE.sub(replace, text)


def _standalone_marker(line):
    """Whether a line contains exactly one boundary-capable marker."""
    stripped = line.strip()
    if not stripped:
        return False
    matches = tuple(_FUZZY_CANDIDATE.finditer(stripped))
    if len(matches) == 1 and matches[0].span() == (0, len(stripped)):
        return _marker_distance(_normalize_fuzzy_candidate(matches[0]),
                                LLM_SEGMENT_MARKER.casefold()) <= _FUZZY_MARKER_MAX_DISTANCE
    return stripped == LLM_SEGMENT_MARKER


def _token_matches(text):
    """Yield exact and accepted fuzzy tokens in source-text coordinates."""
    exact = tuple(_TOKENS.finditer(text))
    fuzzy = []
    canonical = LLM_SEGMENT_MARKER.casefold()
    for match in _FUZZY_CANDIDATE.finditer(text):
        if _marker_distance(_normalize_fuzzy_candidate(match), canonical) > _FUZZY_MARKER_MAX_DISTANCE:
            continue
        if any(match.start() < known.end() and known.start() < match.end() for known in exact):
            continue
        fuzzy.append(match)
    return tuple(sorted((*exact, *fuzzy), key=lambda match: (match.start(), match.end())))


def _has_markdown(text):
    # Underscores inside the reserved protocol are not italic/emphasis syntax.
    return _MARKDOWN.search(clean_tokens(text)) is not None


def protected_ranges(text, *, explicit=False):
    """Conservatively keep Markdown paragraphs and variable-width fences atomic.

    A whole paragraph is retained for lists, quotes, tables and inline markup,
    including lazy continuation lines. No Markdown parser or placeholder tokens
    are inserted into the user's reply.

    A bare explicit boundary line may sit inside one Markdown block. Its own line
    is never protected, but the block it splits is offered as one range up to that
    line and a second range after it, with no gap. A gap here would leave the
    position between the two ranges unprotected, and a delimiter on that edge --
    exactly the newline that ends an indented continuation line -- would split a
    block the reader asked to keep atomic.

    Whether a bare boundary line is stray content inside such a block is decided
    in ``_inside_markdown_block``; this function only reports the ranges, so the
    boundary line stays out of them and can be removed without moving text.
    """
    ranges = []
    fence = None
    start = offset = paragraph = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        marker_line = False
        # An explicit boundary can separate prose from a Markdown block in the
        # same paragraph. Inside a fence or a Markdown block it is stray content.
        # V1's rule, kept verbatim: a marker that occupies its own line is a
        # boundary unless it is inside a code fence or the line is quoted. An
        # indented marker is still a boundary -- indentation belongs to the list
        # item being written, and V1 never treated it as a reason to ignore the
        # marker. Lists, tables and inline markup are therefore splittable, while
        # fences and quotes keep the marker as literal text.
        if (explicit and fence is None and not _QUOTED.match(body)
                and _standalone_marker(body)):
            marker_line = True
            # The block the boundary splits is kept as one range up to the marker
            # line's start; the continuation after it is picked up by the tail
            # check, so the two ranges meet without a gap.
            if _has_markdown(text[paragraph:offset]):
                ranges.append((paragraph, offset))
            paragraph = offset + len(line)
        if fence:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(fence[1]) + r",}[ \t]*", body):
                ranges.append((start, offset + len(line)))
                fence = None
        else:
            opening = re.match(r" {0,3}(`{3,}|~{3,})(.*)$", body)
            if opening and not (opening[1][0] == "`" and "`" in opening[2]):
                fence = (opening[1][0], len(opening[1]))
                start = offset
        if marker_line:
            # The marker line keeps its own protocol status: it is never inside a
            # protected range, and it never opens a paragraph of its own.
            offset += len(line)
            continue
        if not body.strip():
            if _has_markdown(text[paragraph:offset]):
                ranges.append((paragraph, offset))
            paragraph = offset + len(line)
        offset += len(line)
    if fence:
        ranges.append((start, len(text)))
    if _has_markdown(text[paragraph:]):
        ranges.append((paragraph, len(text)))
    return tuple(ranges)


def clean_tokens(text):
    # As in V1, an inline reserved token separates words instead of joining them.
    matches = _token_matches(text)
    if not matches:
        return text
    chunks, offset = [], 0
    for match in matches:
        chunks.append(text[offset:match.start()])
        chunks.append(" " if (match.start() and match.end() < len(text)
                               and not text[match.start() - 1].isspace()
                               and not text[match.end()].isspace()) else "")
        offset = match.end()
    chunks.append(text[offset:])
    return "".join(chunks)


def control_token_ranges(text):
    return tuple(match.span() for match in _token_matches(text))


def _shrink_after_marker(ranges, marker_start, marker_end):
    """Rebase protected ranges on text whose marker was just removed.

    ``protected_ranges`` measures the text it was given, marker included. Once the
    marker is deleted every later offset moves left, so a range that started after
    it must shift and a range that spanned it must stop where the marker began.
    Leaving the measured offsets in place would compare positions against the
    wrong characters and silently unprotect a block.
    """
    removed = marker_end - marker_start
    adjusted = []
    for start, end in ranges:
        if start >= marker_end:
            adjusted.append((start - removed, end - removed))
        elif end > marker_start:
            adjusted.append((start, marker_start))
        else:
            adjusted.append((start, end))
    return tuple(adjusted)


def controlled_parts(text, *, split_inline=False):
    """Return cleaned parts and whether V1's explicit boundary was present.

    Recover a marker attached to one side of its line, as V1 does. When
    ``split_inline`` is enabled for model-controlled replies, a recognized
    marker attached to both sides is treated as the same boundary with omitted
    line breaks. Quoted, fenced, escaped and placeholder forms remain
    cleanup-only. Retain all ordinary text, including whitespace, and never
    emit an empty part.
    """
    text = _repair_fuzzy_tokens(text)
    protected = protected_ranges(text, explicit=True)
    parts, current = [], []
    offset = 0
    for line in text.splitlines(keepends=True):
        is_protected = any(start <= offset < end for start, end in protected)
        if not is_protected and line.count(LLM_SEGMENT_MARKER) == 1:
            left, right = line.split(LLM_SEGMENT_MARKER)
            if split_inline or not (left.strip() and right.strip()):
                start = offset + len(left)
                protected = _shrink_after_marker(protected, start, start + len(LLM_SEGMENT_MARKER))
                # V1 trims the marker line's own indentation off both sides, so the
                # removed marker never leaves trailing spaces in the sent message.
                current.append(left.rstrip() if left.strip() else "")
                if left.strip() and not right.strip():
                    current.append(right)
                body = "".join(current)
                if body.strip():
                    parts.append(body)
                    current = []
                # The marker-only line is protocol, including its newline.
                if right.strip():
                    current.append(right.lstrip() if left.strip() else clean_tokens(right))
                offset += len(line)
                continue
        current.append(clean_tokens(line))
        offset += len(line)
    tail = "".join(current)
    if tail.strip():
        parts.append(tail)
    elif parts:
        parts[-1] += tail
    return tuple(parts), len(parts) > 1
