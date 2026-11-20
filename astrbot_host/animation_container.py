"""Check bounded container completeness before trusting a decoder's end of frames."""

import zlib

from companion_v2.conversation.media.animation import MAX_DECODE_FRAMES
from companion_v2.execution.models import TaskError


def _invalid():
    raise TaskError("host_image_invalid", "animation container is incomplete or inconsistent")


def _bounded_count(count):
    if count < 1:
        _invalid()
    if count > MAX_DECODE_FRAMES:
        raise TaskError("host_animation_limit", "animation exceeds the decoded frame budget")
    return count


def _gif_frame_count(content):
    # Pillow treats EOF while seeking as the normal end even if a later image
    # block was truncated. Walk length-prefixed blocks, without decoding LZW,
    # and require the real trailer and every complete image data block.
    data = memoryview(content)
    cursor = 0

    def take(size):
        nonlocal cursor
        end = cursor + size
        if end > len(data):
            _invalid()
        part = data[cursor:end]
        cursor = end
        return part

    def subblocks():
        while True:
            size = take(1)[0]
            if not size:
                return
            take(size)

    header = take(13)
    if bytes(header[:6]) not in (b"GIF87a", b"GIF89a"):
        _invalid()
    if header[10] & 128:
        take(3 * 2 ** ((header[10] & 7) + 1))
    count = 0
    padding = 0
    while True:
        marker = take(1)[0]
        if marker == 0x00:
            # Some real-world encoders emit extra 0x00 block-terminator padding
            # between blocks or before the 0x3B trailer; Pillow skips these.
            padding += 1
            if padding > 64:
                _invalid()
            continue
        padding = 0
        if marker == 0x3B:
            return _bounded_count(count)
        if marker == 0x21:
            take(1)  # Extension label; every extension uses data sub-blocks.
            subblocks()
        elif marker == 0x2C:
            descriptor = take(9)
            if descriptor[8] & 128:
                take(3 * 2 ** ((descriptor[8] & 7) + 1))
            take(1)  # Minimum LZW code size; the decoder validates its meaning.
            subblocks()
            count = _bounded_count(count + 1)
        else:
            _invalid()


def _apng_frame_count(content):
    # Normal load validates pixels, but may accept a missing IEND or a damaged
    # final chunk. Check all lengths/CRCs and compare actual frame controls to
    # acTL rather than treating the declared count as evidence of completeness.
    data = memoryview(content)
    if bytes(data[:8]) != b"\x89PNG\r\n\x1a\n":
        _invalid()
    cursor, count, declared = 8, 0, None
    while cursor + 12 <= len(data):
        size = int.from_bytes(data[cursor:cursor + 4], "big")
        end = cursor + 12 + size
        if end > len(data):
            _invalid()
        kind = bytes(data[cursor + 4:cursor + 8])
        body = data[cursor + 8:end - 4]
        checksum = int.from_bytes(data[end - 4:end], "big")
        if zlib.crc32(data[cursor + 4:end - 4]) != checksum:
            _invalid()
        if kind == b"acTL":
            if declared is not None or size != 8:
                _invalid()
            declared = _bounded_count(int.from_bytes(body[:4], "big"))
        elif kind == b"fcTL":
            if declared is None or size != 26:
                _invalid()
            count = _bounded_count(count + 1)
        elif kind == b"IEND":
            if size != 0 or declared is None or count != declared:
                _invalid()
            return count
        cursor = end
    _invalid()


def verify_animation_container(content, mime):
    if mime == "image/gif":
        return _gif_frame_count(content)
    if mime == "image/png":
        return _apng_frame_count(content)
    # WebP's libwebp-backed open validates the RIFF payload and supplies a
    # constant-time frame count. There is no equivalent GIF.n_frames scan.
    return None
