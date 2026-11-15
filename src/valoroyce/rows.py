"""Which captions fit on screen, and how they break into rows.

The caption strip has a fixed number of rows, so that it can never grow over
the middle of the screen, where the crosshair is. The newest caption is always
shown; older ones stay above it while there is room for them whole. A caption
too long for the strip shows its end, the part said last.

Text width is measured by the caller's `advance(text)`, so nothing here needs
a font or a window.
"""

from __future__ import annotations

from collections.abc import Callable

ELLIPSIS = "…"


def wrap(text: str, advance: Callable[[str], float], width: float) -> list[str]:
    """`text` broken into rows no wider than `width`. Breaks anywhere: Chinese
    and Japanese have no spaces to break at."""
    rows, row = [], ""
    for ch in text:
        if row and advance(row + ch) > width:
            rows.append(row.rstrip())
            row = "" if ch.isspace() else ch
        else:
            row += ch
    if row.strip():
        rows.append(row.rstrip())
    return rows


def newest(text: str, advance: Callable[[str], float], width: float, rows: int) -> list[str]:
    """`text` in at most `rows` rows. If it is longer: its end, after an ellipsis."""
    out = wrap(text, advance, width)
    if len(out) <= rows:
        return out
    # The longest end of the text that fits: start one row too early and
    # drop characters from the front until it does.
    start = max(0, len(text) - sum(len(row) + 1 for row in out[-(rows + 1):]))
    for start in range(start, len(text)):
        out = wrap(ELLIPSIS + text[start:].lstrip(), advance, width)
        if len(out) <= rows:
            return out
    return []


def arrange(texts: list[str], advance: Callable[[str], float], width: float,
            rows: int) -> list[list[str]]:
    """Rows for each caption, oldest first as given; an empty list for a
    caption that is not shown. At most `rows` rows in all."""
    out: list[list[str]] = [[] for _ in texts]
    left = rows
    for i in range(len(texts) - 1, -1, -1):
        if left <= 0:
            break
        if i == len(texts) - 1:
            fitted = newest(texts[i], advance, width, left)
        else:
            fitted = wrap(texts[i], advance, width)
            if len(fitted) > left:
                break  # no room for it whole: it and everything older leave
        out[i] = fitted
        left -= len(fitted)
    return out
