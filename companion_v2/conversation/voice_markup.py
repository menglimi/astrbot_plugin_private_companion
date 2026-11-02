"""Keep speech performance controls out of display and intact across chunks."""

import re


VOICE_TAG_NAME_PATTERN = r"(?:p[\s._-]*c{1,2}[\s._-]*tts|tts)"
VOICE_PROTOCOL_LT = r"(?:<|＜|&lt;|&#0*60;|&#x0*3c;)"
VOICE_PROTOCOL_GT = r"(?:>|＞|&gt;|&#0*62;|&#x0*3e;)"


def canonical_voice_tag(name):
    """Normalize common separator and doubled-letter slips in voice tags."""
    normalized = re.sub(r"[\s._-]+", "", str(name).casefold())
    return "pc_tts" if normalized in {"pctts", "pcctts"} else normalized


_TAG_NAMES = ("whisper", "emphasis", "soft", "fast", "slow", "stress", "forceful",
              "french", "spanish", "german", "japanese", "chinese", "korean", "italian")
# Pause-family controls carry no spoken span: <pause> and [pause] both mark the
# delivery instead of the words, so they are dropped rather than balanced.
_PAUSE_NAMES = ("pause", "breath", "inhale", "exhale", "sigh", "silence")
_LT = VOICE_PROTOCOL_LT
_GT = VOICE_PROTOCOL_GT
# A control is "a known name, then up to the last closing angle on its line". The
# attribute tail is deliberately not parsed as XML, so a value holding either
# quote -- or an angle -- cannot defeat it. Three constraints keep that from
# eating prose: the name must end on a real boundary, so <pausex> stays text; the
# tail may span neither a newline nor another opening angle; and it stops before
# a further known control, so an unterminated control cannot swallow the next one.
_CONTROL_BOUNDARY = r"(?=[\s/>\uff1e&]|\Z)"
_KNOWN = "|".join(sorted(_TAG_NAMES + _PAUSE_NAMES, key=len, reverse=True))
_CONTROL_TAIL = rf"(?:(?!{_LT}\s*/?\s*(?:{_KNOWN}){_CONTROL_BOUNDARY})[^<\r\n])*"
_CONTROLS = re.compile(
    rf"{_LT}\s*(?P<close>/?)\s*(?P<tag>{'|'.join(_TAG_NAMES)}){_CONTROL_BOUNDARY}{_CONTROL_TAIL}{_GT}"
    rf"|{_LT}\s*(?P<pause>/?)\s*(?:{'|'.join(_PAUSE_NAMES)}){_CONTROL_BOUNDARY}{_CONTROL_TAIL}{_GT}"
    r"|(?P<cue>(?<!\[)\[[^\[\]\r\n]+\](?!\]|\s*\())", re.I)
# Body text outside a speech span may only lose controls that carry no wording:
# the pause family and a fixed set of brackets that mark delivery. A paired
# performance mark such as <soft>…</soft> stays verbatim there, because prose may
# legitimately mention it and only a speech span decides whether it is a mark.
# The list is deliberately finite: a bracketed word is ordinary prose unless it
# names a delivery, so ``[笑]``, ``第[一]章``, ``选项[A]`` and ``价格[￥100]``
# stay exactly as written.
_BODY_CUES = ("pause", "breath", "sigh", "silence", "inhale", "exhale",
              "停顿", "停顿一下", "稍作停顿", "换气", "吸气", "呼气", "叹息", "沉默",
              "轻声", "轻笑", "低语", "嘟囔", "小声", "温柔地", "轻柔地", "小声地")
# Longest first, so ``[小声地]`` cannot be cut short to a shorter cue that merely
# prefixes it. Alternation is first-match, so the order is load-bearing even
# though the closing bracket already forces a retry today.
_BODY_CUE_ALTERNATION = "|".join(sorted(_BODY_CUES, key=len, reverse=True))
_BODY_CONTROL = re.compile(
    rf"{_LT}\s*/?\s*(?:{'|'.join(_PAUSE_NAMES)}){_CONTROL_BOUNDARY}{_CONTROL_TAIL}{_GT}"
    rf"|(?<!\[)\[(?:{_BODY_CUE_ALTERNATION})\](?!\]|\s*\()", re.I)


def visible_speech(text):
    """Unwrap known delivery tags and directions in an authored speech span."""
    return _CONTROLS.sub("", text)


def strip_performance_controls(text):
    """Drop delivery-only marks from body text that no speech span claims.

    Narrower than ``visible_speech``: only the pause family in angle brackets and
    the fixed cue list in square brackets disappear. Paired performance marks
    such as ``<soft>``/``<whisper>``, plain prose brackets such as ``<3`` or
    ``[1](url)``, and citations such as ``数组[0]`` are left exactly as written.
    """
    return _BODY_CONTROL.sub("", text)


def slice_speech(text, start, end):
    """Slice by visible character offsets, balancing active delivery tags."""
    if end <= start:
        return ""
    output, active, pending = [], [], []
    position = cursor = 0
    begun = False

    def begin():
        nonlocal begun
        if not begun:
            output.extend(pending)
            output.extend(f"<{name}>" for name in active)
            begun = True

    def plain(run):
        nonlocal position
        left, right = max(start - position, 0), min(end - position, len(run))
        if right > left:
            begin()
            output.append(run[left:right])
        elif run.strip():
            pending.clear()
        position += len(run)

    for match in _CONTROLS.finditer(text):
        plain(text[cursor:match.start()])
        if position >= end:
            break
        tag = match.group("tag")
        closing = bool(match.group("close"))
        if start <= position:
            begin()
        if match.group("pause") is not None:
            # A pause mark is not a span, so it is written as an empty element:
            # it then crosses chunks paired while hiding no visible character.
            emitted = "</pause>" if closing else "<pause></pause>"
            if begun:
                output.append(emitted)
            else:
                pending.append(emitted)
        elif tag:
            tag = tag.lower()
            if closing:
                if tag in active:
                    # Well-formed input is nested; finish inner tags if a
                    # generated closing tag omitted them.
                    while active:
                        popped = active.pop()
                        if begun:
                            output.append(f"</{popped}>")
                        if popped == tag:
                            break
            else:
                active.append(tag)
                if begun:
                    output.append(f"<{tag}>")
        elif begun:
            output.append(match.group("cue"))
        else:
            pending.append(match.group("cue"))
        cursor = match.end()
    else:
        plain(text[cursor:])
    if begun:
        output.extend(f"</{tag}>" for tag in reversed(active))
    return "".join(output)
