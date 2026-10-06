"""Page classification domain engine (Task 2.3).

Implements the MECE Decision Table for classifying document pages into:
- 'blank': zero characters, negligible image (< 5% of page canvas).
- 'scanned': raster scan (>= 70% image area ratio), with has_text_layer recorded.
- 'image_heavy': graphic diagrams, photos (5% - 70% image area ratio).
- 'digital_text': standard selectable digital text (< 30% image ratio, > 0 characters).
"""

from __future__ import annotations

from app.ports.types import PageType


def classify_page(char_count: int, image_area_ratio: float) -> tuple[PageType, bool]:
    """Classify a single page into exactly one PageType and determine if text layer exists.

    Args:
        char_count: Total extractable digital characters on page.
        image_area_ratio: Ratio of page area covered by raster graphics (0.0 to 1.0).

    Returns:
        tuple[PageType, bool]: (page_type, has_text_layer)
    """
    if char_count == 0:
        if image_area_ratio < 0.05:
            return PageType.BLANK, False
        if image_area_ratio < 0.70:
            return PageType.IMAGE_HEAVY, False
        return PageType.SCANNED, False

    # char_count > 0
    if image_area_ratio >= 0.70:
        return PageType.SCANNED, True
    if image_area_ratio >= 0.30:
        return PageType.IMAGE_HEAVY, True
    return PageType.DIGITAL_TEXT, True
