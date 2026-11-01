"""The timer itself: Elapsed Time, Time Remaining and the Time Limit, with no Streamlit in it.

Times are read from a monotonic clock (``time.monotonic``) that the caller
passes in, so a change of the server's wall clock never moves a timer, and a
test can step time by hand.

The rules (Terry, 2026-10-30):
- Start runs the timer; Stop pauses it, and Start then resumes it.
- Reset sets Elapsed Time back to zero; pressed while running, it is Stop
  followed by Reset, so the timer is left stopped.
- Time Remaining is the Time Limit minus Elapsed Time, and goes negative once
  the limit is passed. A blank Time Limit, 0 or a negative number of minutes
  means no limit, and Time Remaining is then always zero.
- The Time Limit is in minutes, to one decimal place (rounded to the nearest
  tenth), so it is always a whole number of seconds (a tenth is 6 s).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# A tenth of a minute, as Decimal's quantum for rounding the Time Limit.
_TENTH = Decimal("0.1")


def limit_minutes(value: float | int | str | None) -> float | None:
    """The Time Limit as entered, rounded to the nearest tenth of a minute; None for blank or for anything not a number.

    Rounded half up on the number as written (2.25 is 2.3), not on its
    binary value, which Python's ``round`` would take to 2.2.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        tenths = Decimal(text).quantize(_TENTH, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    if not tenths.is_finite():
        return None
    return float(tenths)


def limit_seconds(limit: float | None) -> int | None:
    """The Time Limit in whole seconds, or None where there is no limit (blank, 0 or negative)."""
    if limit is None or limit <= 0:
        return None
    return round(limit * 60)


def elapsed_shown(elapsed: float) -> int:
    """Elapsed Time as shown, in whole seconds: a stopwatch shows a second only once it has passed."""
    return max(0, math.floor(elapsed))


def remaining_shown(elapsed: float, limit: float | None) -> int:
    """Time Remaining as shown, in whole seconds: the limit minus Elapsed Time as shown, so the two always add up to the limit; 0 with no limit."""
    seconds = limit_seconds(limit)
    return 0 if seconds is None else seconds - elapsed_shown(elapsed)


def clock_text(seconds: int) -> str:
    """Whole seconds as a clock shows them: "4:05", "1:02:03", and a minus sign (U+2212) before a negative time.

    The page's script formats the live timers the same way (``web._HUB_JS``,
    ``clock``); this one is for the tests and anything Python shows.
    """
    sign = "−" if seconds < 0 else ""
    rest = abs(seconds)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{sign}{hours}:{minutes:02d}:{secs:02d}"
    return f"{sign}{minutes}:{secs:02d}"


@dataclass
class Timer:
    """One session's timer. Every method that changes it takes ``now``, the monotonic clock's reading."""

    banked: float = 0.0
    """Seconds run before the current run (all of them while stopped)."""
    started: float | None = None
    """The monotonic time the current run began; None while stopped."""
    limit: float | None = None
    """The Time Limit in minutes, to a tenth; None (blank), 0 or less mean no limit."""

    @property
    def running(self) -> bool:
        return self.started is not None

    def elapsed(self, now: float) -> float:
        """Elapsed Time in seconds at ``now``."""
        return self.banked + (now - self.started if self.started is not None else 0.0)

    def start(self, now: float) -> bool:
        """Start, or resume after a Stop. False (nothing done) if already running."""
        if self.started is not None:
            return False
        self.started = now
        return True

    def stop(self, now: float) -> bool:
        """Pause: the time run so far is kept. False (nothing done) if already stopped."""
        if self.started is None:
            return False
        self.banked += now - self.started
        self.started = None
        return True

    def reset(self, now: float) -> bool:
        """Stop, if running, and set Elapsed Time back to zero. False if there was nothing to reset."""
        changed = self.stop(now) or self.banked != 0.0
        self.banked = 0.0
        return changed

    def set_limit(self, value: float | int | str | None) -> bool:
        """Set the Time Limit from what was entered (``limit_minutes``). False if it is the same as before."""
        limit = limit_minutes(value)
        if limit == self.limit:
            return False
        self.limit = limit
        return True
