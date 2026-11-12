"""Design tokens for meshpool-slides. All layout decisions live here, not in model output."""
from pptx.dml.color import RGBColor

SLIDE_W, SLIDE_H = 13.333, 7.5          # inches, 16:9
MARGIN_X = 0.6
TITLE_TOP = 0.45
TITLE_H = 1.1
BODY_TOP = 1.75
FOOTER_TOP = 7.0
FOOTER_H = 0.3
BODY_BOTTOM = 6.85
BODY_W = SLIDE_W - 2 * MARGIN_X
GUTTER = 0.4

FONT = "Calibri"                        # ships with PowerPoint on Mac/Windows
FONT_FILES = {                          # metric-compatible files used for measuring
    "regular": "/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf",
    "bold": "/usr/share/fonts/truetype/crosextra/Carlito-Bold.ttf",
    "italic": "/usr/share/fonts/truetype/crosextra/Carlito-Italic.ttf",
}

# type scale (pt): (preferred, minimum)
TITLE_PT = (28, 20)
COVER_TITLE_PT = (40, 28)
SUBTITLE_PT = (18, 14)
BODY_PT = (18, 14)
SUB_BULLET_PT = (15, 12)
SMALL_PT = (12, 10)
FOOTER_PT = 9
KPI_VALUE_PT = (44, 16)
QUOTE_PT = (30, 20)
LINE_SPACING = 1.15


def rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h.lstrip("#"))


INK = "1B2333"          # primary text
MUTED = "5B6577"        # secondary text
FAINT = "69717F"        # footer
RULE = "D9DEE7"         # hairlines
BG = "FFFFFF"
PANEL = "F3F5F9"        # light panels
ACCENT = "2557D6"       # primary accent
ACCENT_DARK = "16307A"  # cover background
POS = "157347"
NEG = "C2413A"
# chart series order: accent first, then muted complements that stay distinguishable
SERIES = ["2557D6", "8FA8E8", "1B2333", "F2A541", "4FB3BF", "B5BCC9"]
