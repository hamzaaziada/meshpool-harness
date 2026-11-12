"""Text measurement with real font metrics (Carlito == Calibri metrics)."""
from functools import lru_cache
from PIL import ImageFont

from . import theme

SAFETY = 0.94  # PowerPoint wraps slightly earlier than raw advance widths; keep a margin


@lru_cache(maxsize=256)
def _font(style: str, size_pt: float):
    # load at 10x so sub-point rounding does not distort widths; 1pt == 1px at 72 dpi
    return ImageFont.truetype(theme.FONT_FILES[style], int(round(size_pt * 10)))


def text_width_in(text: str, size_pt: float, style: str = "regular") -> float:
    return _font(style, size_pt).getlength(text) / 10 / 72


def wrap(text: str, width_in: float, size_pt: float, style: str = "regular") -> list[str]:
    usable = width_in * SAFETY
    lines, cur = [], ""
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if text_width_in(trial, size_pt, style) <= usable or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def block_height_in(paragraphs: list[tuple[str, float, str, float]], width_in: float) -> float:
    """paragraphs: (text, size_pt, style, space_before_pt). Returns total height in inches."""
    h = 0.0
    for text, size, style, space_before in paragraphs:
        n = len(wrap(text, width_in, size, style))
        h += (n * size * theme.LINE_SPACING * 1.2 + space_before) / 72
    return h


def fit_size(paragraphs_at, width_in: float, height_in: float, pref: float, minimum: float, step: float = 1.0):
    """paragraphs_at(size) -> paragraph list. Returns the largest size that fits, or None if even min overflows."""
    size = pref
    while size >= minimum - 1e-6:
        if block_height_in(paragraphs_at(size), width_in) <= height_in:
            return size
        size -= step
    return None
