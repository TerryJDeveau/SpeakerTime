"""The clocks' colours: the host's choices, the rule for black and white, and the Time Left Warning's colour, with no Streamlit in it.

Terry's rules (2026-11-01, items 4, 9 and 10):
- Beside each of the host's size sliders, a column of colour buttons sets
  that timer's figures' colour for everyone. White is the default; white and
  black are both choices; red is not one (Time Remaining turns red when its
  time is out, ``web``).
- Choosing black or white for a timer that was not already that colour also
  turns the other timer to it, if the other was the opposite one (white
  where black is chosen, black where white is). Choosing black makes the
  panel's background white and its other text black; choosing white makes
  the background black and the other text white: the panel's theme, LIGHT or
  DARK.
- The Time Left Warning's colour is, by default, green while Time Remaining
  is white or black, and otherwise the complement of Time Remaining's colour
  (its hue turned half way round the colour wheel, its saturation and
  lightness kept). The host may choose instead one of four others: not red,
  not the default, and not either timer's colour (``warning_choices``).

Colours are kept by name; ``web`` draws them by their hex values here.
"""

from __future__ import annotations

import colorsys

WHITE, BLACK = "white", "black"
DARK, LIGHT = "dark", "light"
"""The panel's themes: a black background with white text (the default), or a white one with black text."""

CLOCK_COLOURS: dict[str, str] = {
    WHITE: "#ffffff",
    BLACK: "#000000",
    "yellow": "#ffd60a",
    "orange": "#ff9f1c",
    "green": "#2ecc71",
    "blue": "#3a86ff",
    "magenta": "#ff4fd8",
}
"""A timer's colours, in the order of their buttons: white (the default) and black, then five that read on black (no red: it means time is out)."""

DEFAULT_COLOUR = WHITE

WARNING_GREEN = CLOCK_COLOURS["green"]
"""The warning's colour while Time Remaining is white or black."""

WARNING_CANDIDATES: dict[str, str] = {
    "yellow": CLOCK_COLOURS["yellow"],
    "orange": CLOCK_COLOURS["orange"],
    "cyan": "#22d3ee",
    "magenta": CLOCK_COLOURS["magenta"],
    "blue": CLOCK_COLOURS["blue"],
    "violet": "#9b5de5",
    "green": WARNING_GREEN,
}
"""The warning's other choices, in order: the first four not ruled out (``warning_choices``) are the buttons. Never white, black or red."""

AUTO = "auto"
"""The warning's colour by Terry's rule (green, or Time Remaining's complement): its first button, and the default."""

WARNING_CHOICES_SHOWN = 4


def complement(hex_colour: str) -> str:
    """The colour opposite ``hex_colour`` on the colour wheel: its hue turned 180 degrees, its lightness and saturation kept ("#rrggbb")."""
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    r, g, b = colorsys.hls_to_rgb((hue + 0.5) % 1.0, lightness, saturation)
    return "#" + "".join(f"{round(c * 255):02x}" for c in (r, g, b))


def choose(colours: dict[str, str], theme: str, timer: str, colour: str) -> tuple[dict[str, str], str]:
    """``colour`` chosen for ``timer``: the timers' colours and the theme after it, by Terry's rule for black and white.

    ``colours`` maps each timer to a name of CLOCK_COLOURS; it is not changed
    (a new dict is returned). Raises ValueError for a colour or timer not known.
    """
    if colour not in CLOCK_COLOURS:
        raise ValueError(f"no such colour: {colour!r}")
    if timer not in colours:
        raise ValueError(f"no such timer: {timer!r}")
    after = dict(colours)
    if after[timer] == colour:
        return after, theme
    after[timer] = colour
    if colour in (WHITE, BLACK):
        opposite = BLACK if colour == WHITE else WHITE
        for other in after:
            if other != timer and after[other] == opposite:
                after[other] = colour
        theme = LIGHT if colour == BLACK else DARK
    return after, theme


def warning_default(remaining: str) -> str:
    """The warning's colour by Terry's rule, for Time Remaining's colour ``remaining`` (a name): green for white or black, else its complement."""
    return WARNING_GREEN if remaining in (WHITE, BLACK) else complement(CLOCK_COLOURS[remaining])


def warning_choices(colours: dict[str, str]) -> list[tuple[str, str]]:
    """The warning's four other choices (name, hex), for the timers' colours ``colours``: not the default, and neither timer's colour."""
    taken = {warning_default(colours["remaining"])} | {CLOCK_COLOURS[name] for name in colours.values()}
    return [(name, hex_) for name, hex_ in WARNING_CANDIDATES.items() if hex_ not in taken][:WARNING_CHOICES_SHOWN]


def warning_colour(choice: str, colours: dict[str, str]) -> str:
    """The warning's colour (hex): ``choice`` where it is one of the choices for these timers' colours, else the default."""
    for name, hex_ in warning_choices(colours):
        if name == choice:
            return hex_
    return warning_default(colours["remaining"])
