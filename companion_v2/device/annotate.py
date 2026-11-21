"""Annotate one phone screenshot with a coordinate grid and clickable node boxes.

The vision model gets the same numbers the text observation uses: grid lines
every ``step`` pixels, green boxes around clickable nodes, and the node's index
from the UI dump drawn inside its box.  That makes "tap the box labelled [7]"
and "tap (540, 1160)" describe the same target, which is the point of sending an
image at all.

Only Pillow is used.  Labels are indices, not UI text, so no CJK font is needed.
"""

from __future__ import annotations

from io import BytesIO
from typing import Iterable, Sequence

from .adb import UiElement

DEFAULT_GRID = 100
LABEL_EVERY = 2
MAX_ANNOTATED_BYTES = 3_500_000
LEGEND_HEIGHT = 18


def annotate_screen(png: bytes, elements: Sequence[UiElement], *, grid: int = DEFAULT_GRID,
                    max_bytes: int = MAX_ANNOTATED_BYTES, boxes: bool = True) -> bytes:
    """Return a PNG with grid coordinates and clickable-node indices drawn on it.

    ``boxes=False`` keeps the grid but omits node outlines; the caller sets it
    when the screenshot and UI tree were captured from different frames, so a
    stale box cannot point at the wrong target.
    """
    from PIL import Image, ImageDraw, ImageFont

    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("annotate_screen needs the original PNG")
    if not 40 <= int(grid) <= 400:
        raise ValueError("grid spacing must be between 40 and 400 pixels")
    with Image.open(BytesIO(png)) as opened:
        base = opened.convert("RGBA")
    width, height = base.size
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default()
    line = (255, 64, 64, 110)
    label = (255, 220, 220, 235)
    shade = (0, 0, 0, 150)
    for x in range(grid, width, grid):
        draw.line(((x, 0), (x, height)), fill=line, width=1)
    for y in range(grid, height, grid):
        draw.line(((0, y), (width, y)), fill=line, width=1)
    for x in range(0, width, grid * LABEL_EVERY):
        text = str(x)
        draw.rectangle((x + 1, 19, x + 7 + 6 * len(text), 31), fill=shade)
        draw.text((x + 3, 20), text, fill=label, font=font)
    for y in range(grid * LABEL_EVERY, height, grid * LABEL_EVERY):
        text = str(y)
        draw.rectangle((1, y + 1, 7 + 6 * len(text), y + 13), fill=shade)
        draw.text((3, y + 2), text, fill=label, font=font)
    for element in elements:
        if not boxes or not element.clickable:
            continue
        left, top, right, bottom = element.bounds
        draw.rectangle((left, top, right, bottom), outline=(0, 230, 90, 235), width=3)
        tag = f"[{element.index}]"
        tag_width = 7 + 6 * len(tag)
        draw.rectangle((left, top, left + tag_width, top + 13), fill=(0, 90, 30, 220))
        draw.text((left + 2, top + 1), tag, fill=(220, 255, 220, 255), font=font)
    draw.rectangle((0, 0, width, LEGEND_HEIGHT), fill=(0, 0, 0, 170))
    legend = (f"grid={grid}px  green=clickable  [n]=node index  {width}x{height}" if boxes else
              f"grid={grid}px  node boxes hidden (unstable frame)  {width}x{height}")
    draw.text((4, 3), legend, fill=(235, 235, 235, 255), font=font)
    composed = Image.alpha_composite(base, overlay).convert("RGB")
    data = _encode(composed)
    if len(data) <= max_bytes:
        return data
    # A very busy screen can exceed the model image budget; one bounded downscale
    # preserves legibility better than silently dropping the image.
    scale = 0.7
    resized = composed.resize((max(1, int(width * scale)), max(1, int(height * scale))),
                              Image.LANCZOS)
    data = _encode(resized)
    if len(data) > max_bytes:
        raise ValueError("annotated screenshot exceeds the model image budget")
    return data


def _encode(image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, "PNG", optimize=True, compress_level=6)
    return buffer.getvalue()


def image_size(png: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(BytesIO(png)) as opened:
        return opened.size


def clickable(elements: Iterable[UiElement]) -> tuple[UiElement, ...]:
    return tuple(element for element in elements if element.clickable)
