"""The web page (Streamlit): ``streamlit run streamlit_app.py``.

Four screens, one at a time:
- start: the Session ID box, Create New Session and Connect to Session (and,
  when a new window cannot tell which of several sessions to go back to, a
  Rejoin button for each);
- pending: "Please wait, connection pending", with a spinner and Cancel, while
  the session connected to has not been created yet (the page joins it by
  itself once it is);
- host: the two timers and the Time Limit, with Start, Stop and Reset, and a
  slider beside each timer setting its size for everyone;
- viewer: the two timers and the Time Limit, as the host runs them.

How the timers stay live, and the same on every screen
-------------------------------------------------------
The timer lives on the server (``sessions.REGISTRY``). Each page checks in
once a second from a fragment (``st.fragment(run_every=...)``, rerun by the
browser without the rest of the page). Each check-in gives the page's script
the timer as it is at that moment, by a script-only element in the fragment
(``_SYNC_JS``). The clock itself (``_CLOCK_JS``) is drawn in the browser, ten
times a second, from the last reading and the time passed since it, so the
seconds tick evenly however long a check-in takes. A reading that agrees with
the clock to within half a second is ignored, so the clock never steps back
or skips because one check-in was slower than the last; one that does not
(a Start, a Stop, a laptop woken from sleep) is taken at once.

The clock can be popped out into a window that stays on top of every other,
the slides included (``_HUB_JS``, ``openPip``): the browser's Document
Picture-in-Picture, in Chrome and Edge on a computer (version 116 on).
Chrome keeps one such window for the whole browser, every tab, window and
profile, a video's picture-in-picture included (measured 2026-11-03; Edge
keeps its own). So while another tab of the browser has it, a tab's button
offers a plain pop-up instead, which any number of tabs may have but which
other windows may cover; once the pop-out closes, the tabs without a pop-up
offer it again (Terry, 2026-11-03; ``_POP_LOCK``). A pop-up outlives its
tab: a tab reloaded takes it back, and one left without its tab says so
(``_GONE_MS``).

Sizes and the way the times are shown (Terry, 2026-11-01)
---------------------------------------------------------
The host's slider beside each timer sets that timer's size on every panel and
pop-out of the session (``sessions.Registry.set_size``); it arrives with the
check-ins. Each panel and each pop-out has its own switch between H:MM:SS and
minutes to a tenth (``timer.CLOCK``, ``timer.MINUTES``), independent of every
other. A panel starts at the host's choice when it opens, and takes the
host's choice again whenever a host connects (whichever is the later); a
pop-out starts at its panel's choice when it opens.

The host's Buzzer (Terry, 2026-11-03)
-------------------------------------
Only the host's panel has the button. While it is held, Time Remaining reads
"BUZZ" in red on every panel and pop-out, and a buzzer sounds on every page
of the session, the host's too (Terry, 2026-11-03, v1.13; viewers' alone until
then), except a page whose address says ``?silent`` (``silent_asked``); let go,
all is as before. It is sent through the check-in's element (``_SYNC_JS``),
whose trigger reruns the fragment alone: the clock's would make a whole run,
which can lay the clock out again under the finger. A browser lets a page
sound only once it has been clicked or a key pressed on it since it loaded,
so a viewer's page that reconnected by itself (a reload) says so under its
clock until it is clicked; the host's press of the button is itself a click.
A host's pop-out or pop-up has the Buzzer too, on a line with its switch and
Start, Stop and Reset (Terry, 2026-11-03, v1.15), so that a host can run the
session without leaving a maximized window on a single monitor (``_PIP_HTML``,
``openWindow``).

A link that connects by itself (Terry, 2026-11-03, v1.15)
---------------------------------------------------------
A page whose address says ``?session=ROOM5`` connects as Connect to Session
does, at the visit's start (``session_asked``). Leave Session and Cancel take it
out of the address, so that a reload does not go back. The host's Copy Link
(Terry, 2026-11-04, v1.16) puts that address on the clipboard, from the page's
script (``copyLink``), never with this page's ``?silent``.

A viewer's Day and Night (Terry, 2026-11-04, v1.16)
---------------------------------------------------
A toggle on a viewer's panel and pop-out or pop-up. Until the viewer presses the
one not shown it does nothing. Then the background is white or black whatever
the host chooses, the text with it, a timer's figures too where they are black or
white; the host's other colours still come through, and a change that would force
the other of night and day is ignored. The browser's alone (``hub``'s
``shownLook``), kept by session in a cookie: another session keeps the default,
and Leave Session forgets it.

Fitted to the window on connecting (Terry, 2026-11-03)
------------------------------------------------------
When a page enters a session (``_enter``: a new count in the clock's data),
its script scales it to the tab's window, down only (``_FIT_SHARE``, ``fit``).
The panel shrinks first and the text around it keeps its size, all of the page
inside the window. In a window too short for that the page's column shrinks as
well, though not so far that all of it stays inside: the text and controls stand
half as large again as that would leave them (``_FIT_TEXT``, Terry, 2026-11-04),
and the page scrolls to reach the rest. After that the sliders and the window
are the user's.

What the browser keeps
----------------------
Three cookies and three notes per tab:
- ``speakertime_recent``: the sessions this browser was in, most recent first;
- ``speakertime_hosts``: the host's token of each session hosted from it (the
  last ``_HOSTS_KEPT``);
- ``speakertime_browser``: a random id, so that the server can tell this
  browser's own windows from others' (``sessions.Registry.candidates``);
- the tab's own session, in its ``sessionStorage`` (``speakertime_tab``):
  kept through a reload of that tab, and by no other tab;
- the tab's own random id, in its ``sessionStorage`` too
  (``speakertime_tabid``), so that the server can tell the host's own tab,
  reloaded, from another tab of the same browser, which holds the same
  cookies (Terry, 2026-11-01: a host and a viewer in two tabs of one browser);
- the name of the tab's pop-up while one is open (``speakertime_popup``),
  so that the tab, reloaded, finds it again.
A tab reloaded goes back to its own session (as host if it holds the token
and no other page hosts it: Terry, 2026-10-30). A new window goes back by
itself only when that leaves no doubt: the one session it would host, or
the one session of the browser's recent ones still running and not open in
another of its windows; with several, it offers a Rejoin button for each.
Leave Session and Cancel forget the session. Measured 2026-11-01: with one
cookie for the last session alone, a window hosting ALPHA, reloaded while
another window of the same browser hosted BRAVO, went to BRAVO as a viewer.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import urllib.parse
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import streamlit as st
from streamlit.runtime import Runtime
from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx

from . import ids
from .colours import AUTO, BLACK, CLOCK_COLOURS, DARK, DEFAULT_COLOUR, LIGHT, WHITE, warning_colour
from .sessions import (
    BUZZ_HOLD_S, HOST, REGISTRY, SIZE_DEFAULT, SIZE_MAX, SIZE_MIN, SIZE_STEP, SIZED, VIEWER, Checked, Refused, Shown,
)
from .shared import version_text
from .store import TOKEN_NAME, GistStore
from .timer import CLOCK, MINUTES, MODES, limit_minutes

PAGE_TITLE = "SpeakerTime"

CHECK_IN_S = 1.0
"""How often a page checks in: the most a viewer's timers lag the host's Start or Stop, and how soon a session waited for is joined."""

_COOKIE_DAYS = 7
"""How long the cookies of sessions last: a session is removed 6 hours after the last page leaves it (``sessions.EXPIRE_AFTER_S``), so a week covers any return."""

_BROWSER_DAYS = 365
"""How long the browser's id lasts: it names the browser, not a session."""

_HOSTS_KEPT = 10
"""The most host tokens the browser keeps, the most recent: a token of a session long gone only takes room."""

_RECENT_KEPT = 10
"""The most sessions the browser remembers, the most recent."""

RECENT_COOKIE = "speakertime_recent"
HOSTS_COOKIE = "speakertime_hosts"
BROWSER_COOKIE = "speakertime_browser"
DAYNIGHT_COOKIE = "speakertime_daynight"
"""A viewer's Day or Night by session, read and written by the page's own script alone: the server has no part in it."""
_DAYNIGHT_KEPT = 20
"""The most sessions' Day or Night the browser keeps, the most recent."""
LEGACY_LAST_COOKIE = "speakertime_last"
"""Until 2026-11-01 the one session remembered; read once as the recent list, then removed."""
TAB_KEY = "speakertime_tab"
"""The tab's own session, in its sessionStorage."""
TAB_ID_KEY = "speakertime_tabid"
"""The tab's own random id, in its sessionStorage."""
POPUP_KEY = "speakertime_popup"
"""The name of the tab's pop-up while one is open, in its sessionStorage: a tab reloaded finds it by that name."""
UPDATED_KEY = "speakertime_updated"
"""Set in the tab's sessionStorage when new code closed its pop-out before reloading the tab (_SCRIPTS_KEY): its note says so."""

_BROWSER_ID = re.compile(r"[A-Za-z0-9]{8,64}")
"""A browser's or a tab's id as the page's script makes one (``_READER_JS``); anything else is ignored."""

# The screens.
START = "start"
PENDING = "pending"

# Session-state keys.
_HEARD = "heard"          # what the page's script sent at the visit's start (_READER_JS); absent until it has answered
_READER = "reader"        # the element that reads the cookies and the tab's note
_STARTED = "started"      # the visit's start is done: its session reconnected, or not
_SCREEN = "screen"        # START, PENDING, HOST or VIEWER
_SID = "sid"              # the session waited for, hosted or viewed
_BOX = "box"              # the Session ID box
_LIMIT_BOX = "limit_box"  # the Time Limit box (minutes)
_WARN_BOX = "warn_box"    # the Time Left Warning box (minutes)
_PICKER = "warn_colours"  # the warning's colour buttons: their trigger, "pick"
_PICKER_BOX = "warn_colours_box"  # their label and them
_START_BUTTON = "start"   # the host's Start button, which reads Restart once the timer has run
_COPY_BUTTON = "copy_link"  # the host's Copy Link button, found by the page's script by this key (Streamlit's class .st-key-copy_link)
_TOKENS = "tokens"        # the host's token of each session this browser hosted: {sid: token}
_BROWSER = "browser"      # this browser's id (None if its script could not say)
_TAB = "tab"              # this tab's id (None if its script could not say)
_REJOIN = "rejoin"        # the sessions a new window could go back to, when it cannot tell which: [(sid, as host)]
_NOTICE = "notice"        # a message shown once: (kind, text), kind one of st's "success", "info", "warning", "error"
_DECLINED = "declined"    # the sessions whose "Assume Host Role?" this page answered No
_COOKIE_OP = "cookie_op"  # the browser's notes' last change, for the writer (_WRITER_JS)
_BEAT = "beat"            # the check-ins counted, so that each one reaches the page's script
_MARK = "mark"            # the page's own part of its id (_page)
_MODE = "mode"            # this panel's way of showing the times (timer.MODES); None before it has shown a session
_EPOCH = "epoch"          # the session's host_epoch when this panel last took the host's way
_CLOCK = "clock"          # the clock element: its triggers, "mode" (the switch) and "size" (a slider)
_LOST = "lost"            # the check-in's script found the clock gone from the page: lay the page out again
_SYNC = "sync"            # the check-in's element: its triggers, "tick" (the pop-out's), "lost" and "buzz" (the host's Buzzer)
_ENTERED = "entered"      # the times this page has entered a session (_enter): a new count has the page's script fit the page to its window

# The clock's look. A dark panel with light figures reads well from a lectern
# and in a small window beside the slides; the host may turn it white with
# black text instead (``colours``: choosing black for a timer), and sets each
# timer's colour for everyone. Remaining turns red, and blinks, once it shows
# zero or less, and its label reads "Overtime" while it is negative (Terry,
# 2026-11-01); between the Time Left Warning and zero it is in the warning's
# colour. The figures are sized by a unit times the host's size, and the
# panel by its figures (v1.11; until then by its column's width). The colours are
# CSS variables on the panel (and the pop-out's body), set by the script from
# the session's look. The red differs by theme: on black #ff5a4f looked
# salmon, "not a deep enough red" (Terry, 2026-11-03); of five drawn on
# black he chose #ff2a2a. On white the darker #d1242f, which he found good.
_THEMES = {
    DARK: {"panel": "#000000", "text": "#ffffff", "line": "#6b7280", "over": "#ff2a2a"},
    LIGHT: {"panel": "#ffffff", "text": "#000000", "line": "#9ca3af", "over": "#d1242f"},
}
"""Each theme's background, other text, lines (buttons' borders) and out-of-time red (darker on white, to read)."""

_SLIDER_COLOUR = "#f28b82"
"""The size slider's filled (lower) part: a light red (Terry, 2026-11-01: lighter than the red it had, #e2483d)."""

_COLOUR_NAMES = {name: name.capitalize() for name in CLOCK_COLOURS}

_BLINK_S = 1.0
"""Remaining's figures and the Overtime label blink once a second: shown for half of it, faint for the other half."""

_CHAR_EM = 0.62
"""The width of a character of the figures, in em, a little over a digit's (0.55 to 0.6 in the system fonts, tabular): the figures shrink to fit their column."""

# The figures' size at 100 % of the host's slider (sessions.SIZE_MAX).
# v1.3 (Terry, 2026-11-03): a quarter of the largest that fit the page's
# centred column, so that 400 filled it: five characters ("12:34",
# "−0:05", "125.0": a talk of ten minutes or more shows five) at 0.62 em
# fill a column of 100 cqw at 32.3 cqw; a quarter of that is 8 cqw, about
# 56 px in that column. Measured first with four (10 cqw): a 12.5-minute
# limit's "12:30" stopped at 320. v1.6: the panel spans the browser window
# (main), and the unit stops at 56 px, so a size looks as it did in v1.3 on
# a computer while 800 doubles it where the panel is wide enough; figures
# never grow wider than their column, and on a phone the unit stays 8 cqw.
# v1.11 (Terry, 2026-11-03): the panel is as wide as its figures, not as
# its window, so the sliders and colour buttons stand just beside the
# larger timer; the figures keep the size the slider gives them, and a
# panel wider than the window runs past its edge, with a scroll bar under
# it. The unit is 56 px on a computer and 8 vw on a phone, where the
# figures still shrink to fit the width (_PHONE_PX). The labels, the Time
# Limit and the space between it and the warning are sized by the window
# (vw) as they were by the panel (cqw) before: the panel cannot be a
# container its own contents are sized by.
_PANEL_UNIT_PX = 56
_PANEL_UNIT = f"clamp(8px, 8vw, {_PANEL_UNIT_PX}px)"
_PANEL_PAD_PX = 8
"""The panel's padding at the left and at the right."""
_TOOLS_PX = 56
"""On the host's panel, the slider's column (30 px) and the colour buttons' (26 px) beside each timer; as much again at the left keeps the figures centred."""
_PANEL_MIN_PX = 480
"""The narrowest panel (or the page's width, if less): its row of buttons fits on one line."""
_PHONE_PX = 640
"""In a window narrower than this, the panel is no wider than the page and the figures shrink to fit it, as before v1.11: a phone's page is not scrolled sideways."""
_ROOM_TEXT = "00:00"
"""The figures keep room for this text however short they are, so the panel does not narrow when "10:00" becomes "9:59"."""

# v1.13 (Terry, 2026-11-03): on connecting to a session, as host or viewer, no part of the app is outside the browser
# tab's window, the text around the panel and the cookie note included; scaled down if need be, never up. Measured on
# v1.12: the host's page at the default sizes is 1026 px high, its panel 510 of them, in a window of 784. So the panel is
# scaled first (CSS zoom, which unlike a transform gives back the room it saves), and the text around it keeps its size.
# Only where that would leave the panel too little of the window (the rest of the host's page alone is about 430 px) is
# the page's whole column scaled as well (a viewer's page in a window of 500 by 400). The two scales are CSS variables on
# the document, so that a run of the page, which may rebuild the elements, leaves them; the column's rule (_PAGE_STYLE)
# holds only while the clock is on the page, so that the start screen stays at full size. After that the sliders and
# the window are the user's: a panel made wider than the window runs past its edge, with its scroll bar (v1.11).
_FIT_SHARE = 2 / 3
"""In a small window the panel keeps this share of the window's height, the whole column shrinking to leave it that (v1.16, Terry, 2026-11-04: twice the 1/3 it had)."""
_FIT_PANEL_PX = 250
"""... up to this height as drawn, so that a window of the usual size is left as it was: the panel alone shrinks there, the text around it keeping its size."""
_FIT_TEXT = 1.5
"""In a window where the column has to shrink, its text and controls stand this many times what would keep all of the page inside the window, up to their full size, and the page scrolls to reach the rest; the panel's size as drawn stays as it was (v1.17, Terry, 2026-11-04: at v1.16's sizes on a small laptop "a bit too small. They need to be about 50% larger, even if that means the page has to rely on a vertical scroll to reach the bottom.")."""
_FIT_LEAST = 0.2
"""The smallest the panel's scale goes, and, times ``_FIT_TEXT``, the column's (v1.16: twice the 0.1 it had): a window of almost no height shows the page unreadably small as it is, and browsers keep a smallest font."""
_FIT_GROW_MS = 3000
"""For this long after connecting the scales may rise again as well as fall: elements on their way out may have been measured."""
_FIT_SETTLED_MS = 1500
"""The fit ends once the scales have not changed for this long: late elements (the number boxes, a font) have come by then."""
_FIT_LONGEST_MS = 10000
"""The fit ends after this long whatever happens, should a line of text wrapping at one scale and not at the next keep it moving."""

# v1.11 (Terry, 2026-11-03): the pop-out and the pop-up show the panel's
# figures, Time Limit and switch at the panel's own sizes, scaled as a
# whole to fit the window (a scaled copy of what the host sees, so a window
# made larger or smaller by hand shows everything larger or smaller); when
# the sizes change, the window follows at the same scale, where the browser
# lets it. Chrome lets a script resize a pop-up at any time, and a pop-out
# only within about 5 s of a click or a key on the page (measured
# 2026-11-03, Chrome 156: "resizeBy() requires user activation in document
# picture-in-picture"), so the host's own pop-out follows its sliders and a
# viewer's does not: its window stays, and the copy is scaled to fit it.
# This replaces v1.7's column beside the timers in a wide, low pop-out.
_PIP_FILL = 0.99
"""The share of the pop-out's width or height the scaled copy fills."""

_DRAW_MS = 100
"""The clock is drawn every 100 ms: a second's change shows within a tenth of a second."""

_RESYNC_S = 0.5
"""A check-in's reading further than this from the clock's own is taken; nearer, it is ignored (jitter)."""

_KICK_AFTER_MS = 2500
"""With the clock popped out, a check-in is asked for from the pop-out's own timer after this long without one.

A page hidden behind other windows (the slides shown full screen) may have its
timers slowed by the browser to once a minute after five minutes (Chrome's
intensive throttling), the fragment's among them; the pop-out window is
shown, so its timers are not slowed.
"""

_BUZZER_LABEL = "Buzzer"
_BUZZ_TEXT = "BUZZ"
"""Time Remaining's figures while the host holds the Buzzer, in the out-of-time red, steady."""
_BUZZ_NOTE = "Click anywhere on this page once so that it can sound the buzzer."
"""Under a viewer's clock while its page may not sound yet (no click or key on it since it loaded)."""
_SILENT_SWITCH = "silent"
"""The word in a page's address that stops its buzzer sounding: ``?silent`` (Terry, 2026-11-03: "-silent or something like that", so ``?-silent`` too)."""
_SILENT_OFF = {"0", "false", "no", "off"}
"""A value of the switch that says not silent after all (``?silent=no``)."""
_SILENT_TEXT = "(silent)"
"""After the role on a page whose address says silent, so that its silence is not taken for a fault (Terry, 2026-11-03: this short)."""
_SESSION_SWITCH = "session"
"""The word in a page's address that connects it to a session by itself: ``?session=ROOM5`` (Terry, 2026-11-03)."""
_START = "Start"
_RESTART = "Restart"
"""The Start button's label once the timer has run (Elapsed not zero), running or stopped: Start goes on from there."""
_LEAVE_LABEL = "Leave Session"
_COPY_LABEL = "Copy Link"
_COPIED_LABEL = "Copied"
_NOT_COPIED_LABEL = "Could not copy"
_COPY_SHOWN_MS = 1500
"""What the Copy Link button says after a press, for this long."""
_POP_BUZZ = "Buzz"
"""The Buzzer's label on the host's line in a pop-out or pop-up (Terry, 2026-11-03)."""
_POP_CONTROLS = {"start": _START, "stop": "Stop", "reset": "Reset"}
"""The timer's buttons on the host's line in a pop-out or pop-up, by what each asks ``sessions.Registry.control`` to do."""

_BUZZ_RESEND_MS = round(BUZZ_HOLD_S * 1000 / 3)
"""While the Buzzer is held, the host's page says so again this often: the server stops it after BUZZ_HOLD_S without word."""

_BUZZ_CHECK_MS = 1500
"""A check-in this long after the Buzzer was last sent that still disagrees with the host's page has it sent again.

A send from the check-in's element can be lost when the element is rebuilt
(each check-in rebuilds it), as the pop-out's kick may be.
"""

_BUZZ_SOUND = {"hz": [110, 113], "lowpass": 1500, "gain": 0.25, "attack": 0.02, "release": 0.06}
"""The buzzer: two square waves 3 Hz apart, beating, through a low-pass filter (Hz), at a quarter of full volume,
rising and falling in ``attack`` and ``release`` seconds, so that it starts and stops without a click."""

_MODE_NAMES = {CLOCK: "H:MM:SS", MINUTES: "MMM.M"}
"""The switch's two buttons (Terry's names for the two ways)."""

_LABELS = {
    CLOCK: {"elapsed": "Elapsed Time", "remaining": "Time Remaining", "limit": "Time Limit", "overtime": "Overtime",
            "warning": "Time Left Warning"},
    MINUTES: {"elapsed": "Elapsed Time (min)", "remaining": "Time Remaining (min)", "limit": "Time Limit (min)",
              "overtime": "Overtime (min)", "warning": "Time Left Warning (min)"},
}
"""The labels in each way of showing the times; "overtime" is Remaining's while it is negative."""

_MODES_HTML = '<div class="modes" role="group" aria-label="Show the times as">' + "".join(
    f'<button type="button" data-m="{mode}">{name}</button>' for mode, name in _MODE_NAMES.items()
) + "</div>"
"""The switch, the same on a panel, a pop-out and a pop-up (Terry, 2026-11-04: of v1.15's shorter H:M:S and Min in the last two, "didn't improve anything")."""

_DAY, _NIGHT = "day", "night"
_DAYNIGHT_THEMES = {_DAY: LIGHT, _NIGHT: DARK}
_DAYNIGHT_HTML = '<div class="daynight" role="group" aria-label="Show the panel by day or by night" hidden>' + "".join(
    f'<button type="button" data-d="{choice}">{choice.capitalize()}</button>' for choice in _DAYNIGHT_THEMES
) + "</div>"
"""A viewer's Day/Night toggle (Terry, 2026-11-04), on its panel and pop-out or pop-up; a page's script keeps the choice (``hub``, shownLook)."""


def _cell(name: str, extra: str = "") -> str:
    return f'<div class="cell"{extra}><span class="label" data-l="{name}"></span><span class="value" data-v="{name}"></span></div>'


def _row(name: str, host: bool) -> str:
    """One row of the clock: its label and figures; on the host's panel, a timer's slider for its size and buttons for its colour.

    The Time Limit's row holds the Time Left Warning beside it, shown only
    while the host has set one (Terry, 2026-11-01).
    """
    if name == "limit":
        return f'<div class="row limit" data-r="limit"><div class="pair">{_cell("limit")}{_cell("warning", " data-w hidden")}</div></div>'
    tools = ""
    if host:
        swatches = "".join(
            f'<button type="button" data-c="{colour}" title="{_COLOUR_NAMES[colour]}" aria-label="{_COLOUR_NAMES[colour]}"'
            f' style="--swatch: {hex_}"></button>'
            for colour, hex_ in CLOCK_COLOURS.items()
        )
        tools = (
            f'<input class="size" type="range" data-t="{name}" min="{SIZE_MIN}" max="{SIZE_MAX}" step="{SIZE_STEP}"'
            f' aria-label="Size of {_LABELS[CLOCK][name]} for everyone" hidden>'
            f'<div class="swatches" data-t="{name}" role="group" aria-label="Colour of {_LABELS[CLOCK][name]} for everyone"'
            f' hidden>{swatches}</div>'
        )
    return f'<div class="row timer" data-r="{name}">{_cell(name)}{tools}</div>'


# The colours both the panel and the pop-out take from the session's look (set by the script, ``draw``).
_LOOK_CSS = f"""
  --panel: {_THEMES[DARK]["panel"]}; --text: {_THEMES[DARK]["text"]}; --line: {_THEMES[DARK]["line"]};
  --over: {_THEMES[DARK]["over"]}; --c-elapsed: {CLOCK_COLOURS[DEFAULT_COLOUR]}; --c-remaining: {CLOCK_COLOURS[DEFAULT_COLOUR]};
  --c-warning: {warning_colour(AUTO, dict.fromkeys(SIZED, DEFAULT_COLOUR))};
  background: var(--panel); color: var(--text);
"""

# Rules the panel and the pop-out share: the figures' colours, red, blinking, the warning's figures. The Time
# Limit and the Time Left Warning stand together in the middle, as the switch's two buttons do, two of their
# labels' heights apart (Terry, 2026-11-03: not spread across the width; each took half of it until v1.7).
_SHARED_CSS = f"""
.value[data-v="elapsed"] {{ color: var(--c-elapsed); }}
.value[data-v="remaining"] {{ color: var(--c-remaining); }}
.value[data-v="warning"], .value.warn {{ color: var(--c-warning); }}
.over {{ color: var(--over); }}
.value.over, .value.buzz {{ color: var(--over); }}
.blink {{ animation: speakertime-blink {_BLINK_S}s steps(1, end) infinite; }}
@keyframes speakertime-blink {{ 50% {{ opacity: 0.15; }} }}
.value {{ text-align: center; }}
.value::before {{ content: "{_ROOM_TEXT}"; display: block; height: 0; overflow: hidden; visibility: hidden; }}
.pair {{ display: flex; justify-content: center; }}
.pair .cell {{ flex: 0 1 auto; min-width: 0; }}
.pair .label {{ text-align: center; }}
.pair .cell[hidden] {{ display: none; }}
.modes button.on, .daynight button.on {{ background: var(--text); color: var(--panel); }}
.daynight[hidden] {{ display: none; }}
"""

_GONE_TEXT = "Its SpeakerTime tab has gone: close this window."
"""Shown in a pop-up instead of the figures once its tab has stopped drawing them (closed, or reloaded and not back)."""

# The timers, the Time Limit and a line under them at the panel's sizes on a computer (the stage), scaled as a whole to
# fit the window by the script (--z, hub.fitPip). The line holds the switch and, for the host alone (data-host), the
# Buzzer and the timer's buttons, so that a host can run the session from the window kept on top, without leaving a
# maximized Zoom window on a single monitor (Terry, 2026-11-03).
_PIP_HTML = (
    '<div class="stage">' + _row("elapsed", False) + _row("remaining", False) + _row("limit", False)
    + f'<div class="bar">{_MODES_HTML}{_DAYNIGHT_HTML}<button class="buzz" type="button" data-host hidden>{_POP_BUZZ}</button>'
    + "".join(f'<button type="button" data-a="{action}" data-host hidden>{label}</button>' for action, label in _POP_CONTROLS.items())
    + "</div></div>"
    + f'<div class="gone-note">{_GONE_TEXT}</div>'
)

_PIP_CSS = f"""
  html {{ height: 100%; }}
  body {{ {_LOOK_CSS} margin: 0; height: 100%; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    overflow: hidden; }}
  .stage {{ position: absolute; left: 50%; top: 50%; width: max-content; box-sizing: border-box;
    padding: 14px {_PANEL_PAD_PX}px 12px; transform-origin: 50% 50%; transform: translate(-50%, -50%) scale(var(--z, 1)); }}
  .row {{ margin-bottom: 10px; }}
  .cell {{ display: flex; flex-direction: column; align-items: center; line-height: 1; }}
  .label {{ font-size: 24px; margin-bottom: 4px; }}
  .value {{ --scale: 1; --chars: 4; font-weight: 700; font-variant-numeric: tabular-nums; white-space: nowrap;
    font-size: calc(var(--scale) * {_PANEL_UNIT_PX}px); }}
  .limit .value {{ font-size: 54px; font-weight: 600; }}
  .limit .label {{ font-size: 22px; }}
  .row[data-r="remaining"], .row[data-r="limit"] {{ margin-top: 20px; }}
  {_SHARED_CSS}
  .pair .cell + .cell {{ margin-left: 44px; }}
  .bar {{ display: flex; align-items: center; gap: 8px; width: max-content; margin: 4px auto 0; white-space: nowrap; }}
  .modes, .daynight {{ display: flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
  .modes button, .daynight button {{ font: inherit; font-size: 14px; color: var(--text); background: transparent; border: 0;
    padding: 4px 10px; cursor: pointer; }}
  .bar > button {{ font: inherit; font-size: 14px; color: var(--text); background: transparent;
    border: 1px solid var(--line); border-radius: 8px; padding: 4px 12px; cursor: pointer; }}
  .bar > button:disabled {{ opacity: 0.35; cursor: default; }}
  .bar > .buzz {{ border-color: var(--over); touch-action: none; user-select: none; -webkit-user-select: none; }}
  .bar > .buzz.on {{ background: var(--over); color: var(--panel); }}
  .bar > [data-a="start"]::before {{ content: "{_RESTART}"; display: block; height: 0; overflow: hidden; visibility: hidden; }}
  .gone-note {{ display: none; font-size: max(14px, min(5vw, 7vh)); text-align: center; padding: 0 5vw; }}
  body.gone {{ display: flex; flex-direction: column; justify-content: center; }}
  body.gone .stage {{ display: none; }}
  body.gone .gone-note {{ display: block; }}
"""

_GONE_MS = 3000
"""A pop-up whose tab has not drawn it for this long shows _GONE_TEXT instead of the figures.

A plain pop-up outlives its tab, its figures frozen where they were
(measured 2026-11-03, Chrome: the tab reloaded or closed, the pop-up stayed
open, its clock stopped), and a frozen clock looks like a live one. The
pop-out (Document Picture-in-Picture) closes with its tab, so needs none.
"""

_WATCH_MS = 500
"""How often a pop-up's own watchdog (_WATCH_JS) looks at when its tab last drew it."""

_SCRIPTS_KEY = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
"""This file's code, as the page's script knows it: a tab holding a note of the timer made by other code reloads.

Measured 2026-11-03 (v1.3 to v1.4, the Reboot after the merge; replayed
with two servers taking turns on one port): a tab left open while the
server restarts with other code keeps the earlier code's note of the timer
(``window.speakerTime``), the timer drawing its panel and its buttons'
listeners, none of which the new code can reach or stop, and the new code
ran on that note. The old timer kept setting the button's label by the old
rule while the new note under it went stale (Terry: "Another tab of this
browser has the on-top pop-out" under "Pop out"), and the old buttons took
no lock, so the other tabs never saw that tab's pop-out. A reload put it
right. So the note carries this key (``hub`` in _HUB_JS), and a note of
another key is retired: its timer stopped, its pop-out closed, the tab
reloaded once.
"""

# The watchdog, run in the pop-up's own window: the tab's timers and functions
# stop with the tab, so it cannot be one of them. A round of its own that
# comes late (the pop-up hidden behind other windows, its timers slowed with
# the tab's drawing) judges nothing, so a pop-up brought to the front after a
# while does not show the notice for a moment before the next draw.
_WATCH_JS = f"""(() => {{
  if (window.speakerTimeWatch) return;
  window.speakerTimeWatch = true;
  let last = Date.now();
  setInterval(() => {{
    const now = Date.now();
    const late = now - last > 3 * {_WATCH_MS};
    last = now;
    if (!late) document.body.classList.toggle("gone", now - (window.speakerTimeBeat || 0) > {_GONE_MS});
  }}, {_WATCH_MS});
}})();"""

_RELOAD_AFTER_MS = 1000
"""A tab retiring an earlier version's note reloads this long after: a run under way finishes its cookies first.

Measured 2026-11-03: reloading at once, in the run that laid out a session
just created, lost the run's cookies (the new host's token not yet written),
and the creator came back a viewer.
"""

# Shared by every script that may meet a note of the timer made by other code
# (_SCRIPTS_KEY): the cookie reader, which runs first at each visit, so open
# tabs reload as soon as a restarted server answers, and the clock and the
# check-in. That note is retired: its timer stopped, its pop-out kept on top
# closed (its drawing stops with the old code, and on Community Cloud that
# pop-out belongs to the top page, which a reload of the app's frame leaves
# open), and the tab reloaded once, so that the new code starts afresh; until
# then a stub, and every script does nothing. A pop-up is left open: the tab,
# reloaded, takes it back (adopt).
_RETIRE_JS = f"""
const SCRIPTS_KEY = {json.dumps(_SCRIPTS_KEY)};
const UPDATED_KEY = {json.dumps(UPDATED_KEY)};

const reloading = () => {{
  const old = window.speakerTime;
  if (old === undefined) return false;
  if (old.key === SCRIPTS_KEY) return old.reloading === true;
  window.speakerTime = {{ key: SCRIPTS_KEY, reloading: true }};
  try {{ clearInterval(old.panelTimer); }} catch (error) {{}}
  try {{
    if (old.pip && !old.popup && !old.pip.closed) {{
      old.pip.close();
      sessionStorage.setItem(UPDATED_KEY, "1");
    }}
  }} catch (error) {{}}
  setTimeout(() => location.reload(), {_RELOAD_AFTER_MS});
  return true;
}};
"""

_HUB_JS = _RETIRE_JS + f"""
const DRAW_MS = {_DRAW_MS};
const RESYNC_S = {_RESYNC_S};
const KICK_AFTER_MS = {_KICK_AFTER_MS};
const CLOCK = {json.dumps(CLOCK)};
const MINUTES = {json.dumps(MINUTES)};
const SIZE_DEFAULT = {SIZE_DEFAULT};
const LABELS = {json.dumps(_LABELS)};
const THEMES = {json.dumps(_THEMES)};
const DARK = {json.dumps(DARK)};
const LIGHT = {json.dumps(LIGHT)};
const COLOURS = {json.dumps(CLOCK_COLOURS)};
const WHITE = {json.dumps(WHITE)};
const BLACK = {json.dumps(BLACK)};
const DAY = {json.dumps(_DAY)};
const NIGHT = {json.dumps(_NIGHT)};
const DAYNIGHT_COOKIE = {json.dumps(DAYNIGHT_COOKIE)};
const DAYNIGHT_KEPT = {_DAYNIGHT_KEPT};
const COOKIE_SECONDS = {_COOKIE_DAYS * 86400};
const PIP_HTML = {json.dumps(_PIP_HTML)};
const PIP_CSS = {json.dumps(_PIP_CSS)};
const PIP_FILL = {_PIP_FILL};
const WATCH_JS = {json.dumps(_WATCH_JS)};
const POPUP_KEY = {json.dumps(POPUP_KEY)};
const BUZZ_TEXT = {json.dumps(_BUZZ_TEXT)};
const BUZZ = {json.dumps(_BUZZ_SOUND)};
const START = {json.dumps(_START)};
const RESTART = {json.dumps(_RESTART)};

// Whole seconds as a clock shows them: "4:05", "1:02:03", a minus sign before
// a negative time. As timer.clock_text.
const clock = (seconds) => {{
  const sign = seconds < 0 ? "\\u2212" : "";
  let rest = Math.abs(seconds);
  const hours = Math.floor(rest / 3600);
  rest -= hours * 3600;
  const minutes = Math.floor(rest / 60);
  const secs = rest - minutes * 60;
  const pad = (n) => String(n).padStart(2, "0");
  return sign + (hours ? hours + ":" + pad(minutes) : String(minutes)) + ":" + pad(secs);
}};

// Tenths of a minute as MINUTES shows them: "0.0", "12.5", a minus sign before
// a negative time. As timer.minutes_text.
const minutes = (tenths) => {{
  const sign = tenths < 0 ? "\\u2212" : "";
  const rest = Math.abs(tenths);
  return sign + Math.floor(rest / 10) + "." + (rest % 10);
}};

// The page's one note of the timer, kept on the window so that every element
// that is rebuilt (each of the page's runs) finds the one before's. Callers
// ask reloading() first (_RETIRE_JS).
const hub = () => (window.speakerTime ??= {{
  key: SCRIPTS_KEY, // the code that made it
  base: null,      // the last reading taken: {{sid, elapsed, running, limit, version, sizes, at}}, at from performance.now()
  lastSync: 0,      // when a check-in last arrived
  lastKick: 0,      // when the pop-out last asked for one
  kick: null,       // asks for a check-in (the latest sync element's trigger)
  clock: null,      // the panel's clock (the .clock element) as last laid out
  panelTimer: null, // the page's one timer drawing it (_CLOCK_JS)
  send: null,       // the clock element's trigger, as the latest run gave it
  missing: 0,       // check-ins running that found it gone from the page (_SYNC_JS)
  pip: null,        // the pop-out window, or the pop-up, while open
  popup: false,     // pip is a plain pop-up, not the pop-out kept on top
  opening: false,   // a pop-out asked for and not yet given
  pipOpenedAt: -Infinity,  // when the pop-out last opened (performance.now())
  elsewhere: null,  // the session of the pop-out another tab of this browser holds ("" if it cannot say), or null
  locksAt: -Infinity,      // when that was last looked at (performance.now())
  adopted: false,   // this page has looked for its pop-up from before a reload
  pageMode: CLOCK,  // the panel's way of showing the times (the page's own switch)
  pipMode: CLOCK,   // the pop-out's (its own switch; the panel's when it opened)
  preview: {{}},     // the host's slider while dragged, before the size comes back from the server
  host: null,       // whether this page hosts the session (the clock's data); null until the clock has said
  silent: false,    // this page's address says it makes no sound (the clock's data, silent_asked)
  entered: null,    // the clock's data's count of sessions entered, as last seen
  fitting: null,    // while the page is being fitted to its window: fitStart's (_CLOCK_JS, fit)
  fitPage: 1,       // the page's column's scale
  fitPanel: 1,      // the panel's own scale, within that
  listening: false, // the page's own listeners for the Buzzer are added (_CLOCK_JS, listen)
  buzzHeld: false,  // the host's Buzzer is held on this page
  buzzBy: null,     // held by "pointer" or "key"
  buzzWin: null,    // the window it is held in: the page's, or its pop-out's or pop-up's
  keepBuzz: null,   // says again that it is held (_CLOCK_JS, listen): the pop-out's timer runs while the page behind it is slowed
  buzzWant: null,   // what this page last sent of it: true held, false let go; null for nothing
  buzzSentAt: -Infinity,  // when it last sent it (performance.now())
  buzzSend: null,   // sends it (the latest sync element's trigger)
  audio: null,      // the page's AudioContext, once it may sound (wake)
  tone: null,       // the buzzer while it sounds: {{ gain, oscillators }}
  dayNight: null,   // a viewer's Day/Night for the session: {{ sid, choice, shown, seen }} (dayNightOf)

  // A check-in's reading (or the clock's own, as it is laid out). A Buzzer
  // that stopped by itself changes no version, so its state is taken from
  // every reading.
  update(reading) {{
    const at = performance.now();
    const b = this.base;
    if (b !== null && b.sid === reading.sid && b.version === reading.version) {{
      const predicted = b.elapsed + (b.running ? (at - b.at) / 1000 : 0);
      if (Math.abs(predicted - reading.elapsed) <= RESYNC_S) {{
        b.buzz = reading.buzz === true;
        this.lastSync = at;
        this.buzzer(b.buzz);
        return;
      }}
    }}
    this.base = {{ sid: reading.sid, elapsed: reading.elapsed, running: reading.running, limit: reading.limit,
                  warning: reading.warning ?? null, version: reading.version, sizes: reading.sizes ?? {{}},
                  look: reading.look ?? null, buzz: reading.buzz === true, at }};
    this.lastSync = at;
    this.buzzer(this.base.buzz);
  }},

  // The three texts now, as timer.elapsed_shown and remaining_shown (CLOCK),
  // or elapsed_tenths and remaining_tenths (MINUTES). The limit is a whole
  // number of tenths of a minute (6 s each), so the two still add up to it.
  // "out": Remaining shows zero or less, with a limit set (red, blinking);
  // "over": it shows less than zero (its label "Overtime" too). Both go by
  // the figures as shown, so MMM.M is out from "0.0" (Terry, 2026-11-01:
  // "when the display is zero"). "warn": it shows no more than the Time Left
  // Warning, and more than zero (the warning's colour); "warning" the
  // warning's own figures (null for none). "buzz": the host's Buzzer is
  // held, on the host's own page at once, elsewhere as the server says.
  texts(mode) {{
    const b = this.base;
    if (b === null) return null;
    const buzz = this.buzzHeld || b.buzz === true;
    const elapsed = b.elapsed + (b.running ? (performance.now() - b.at) / 1000 : 0);
    const shown = Math.max(0, Math.floor(elapsed));
    if (mode === MINUTES) {{
      const tenths = Math.floor(shown / 6);
      const limit = b.limit === null ? null : Math.round(b.limit / 6);
      const warning = b.warning === null ? null : Math.round(b.warning / 6);
      const remaining = limit === null ? 0 : limit - tenths;
      return {{ elapsed: minutes(tenths), remaining: minutes(remaining), over: remaining < 0,
               out: limit !== null && remaining <= 0, limit: limit === null ? "none" : minutes(limit),
               warn: limit !== null && warning !== null && remaining > 0 && remaining <= warning,
               warning: warning === null ? null : minutes(warning), buzz }};
    }}
    const remaining = b.limit === null ? 0 : b.limit - shown;
    return {{ elapsed: clock(shown), remaining: clock(remaining), over: remaining < 0,
             out: b.limit !== null && remaining <= 0, limit: b.limit === null ? "none" : clock(b.limit),
             warn: b.limit !== null && b.warning !== null && remaining > 0 && remaining <= b.warning,
             warning: b.warning === null ? null : clock(b.warning), buzz }};
  }},

  // The viewers' Day or Night by session, from their cookie (the latest last; web._DAYNIGHT_KEPT at most).
  dayNightKept() {{
    try {{
      const pair = document.cookie.split("; ").find((one) => one.startsWith(DAYNIGHT_COOKIE + "="));
      const kept = pair === undefined ? null : JSON.parse(decodeURIComponent(pair.slice(DAYNIGHT_COOKIE.length + 1)));
      return typeof kept === "object" && kept !== null && !Array.isArray(kept) ? kept : {{}};
    }} catch (error) {{
      return {{}};
    }}
  }},

  // A viewer's Day/Night for session ``sid``. ``choice``: null until pressed for this session, and the session's look is
  // shown as it is. ``shown``: each timer's colour (a name) as shown under the choice; ``seen``: the host's, as last seen.
  dayNightOf(sid) {{
    if (this.dayNight === null || this.dayNight.sid !== sid) {{
      const choice = this.dayNightKept()[sid];
      this.dayNight = {{ sid, choice: choice === DAY || choice === NIGHT ? choice : null, shown: {{}}, seen: {{}} }};
    }}
    return this.dayNight;
  }},

  // The look this page shows (Terry, 2026-11-04). The session's, unless a viewer has pressed Day or Night for it: then
  // the background is white or black whatever the host has chosen, and the text with it, a timer's figures too where
  // they are black or white. The host's other colours still come through; a change to the black or white that would
  // force the other of night and day is ignored, the timer staying as it was.
  shownLook() {{
    const look = this.base.look;
    if (look === null || this.host !== false) return look;
    const d = this.dayNightOf(this.base.sid);
    if (d.choice === null) return look;
    const theme = d.choice === DAY ? LIGHT : DARK;
    const own = theme === LIGHT ? BLACK : WHITE, other = theme === LIGHT ? WHITE : BLACK;
    const names = {{}};
    for (const timer of ["elapsed", "remaining"]) {{
      const name = look.names[timer];
      if (d.seen[timer] !== name) {{
        const first = d.seen[timer] === undefined;
        d.seen[timer] = name;
        if (first || name !== other) d.shown[timer] = name;
      }}
      if (d.shown[timer] === other) d.shown[timer] = own;
      names[timer] = d.shown[timer];
    }}
    return {{ ...look, theme, names, elapsed: COLOURS[names.elapsed], remaining: COLOURS[names.remaining] }};
  }},

  // A viewer pressing Day or Night, on its panel or pop-out. The one already shown does nothing, so the session's look
  // goes on deciding until the other is pressed. Kept for this session alone (Leave Session forgets it, _WRITER_JS).
  setDayNight(choice) {{
    if (this.base === null || this.host !== false) return;
    if (this.shownLook()?.theme === (choice === DAY ? LIGHT : DARK)) return;
    const sid = this.base.sid;
    this.dayNightOf(sid).choice = choice;
    try {{
      const kept = this.dayNightKept();
      delete kept[sid];  // and added again, as the most recent
      kept[sid] = choice;
      const sids = Object.keys(kept);
      while (sids.length > DAYNIGHT_KEPT) delete kept[sids.shift()];
      document.cookie = DAYNIGHT_COOKIE + "=" + encodeURIComponent(JSON.stringify(kept)) + "; max-age=" + COOKIE_SECONDS
        + "; path=/; SameSite=Lax" + (location.protocol === "https:" ? "; Secure" : "");
    }} catch (error) {{}}  // no cookies for this page: the choice lasts this visit
  }},

  // The look on a panel (the .clock element) or the pop-out (its body): the theme's colours and the timers'.
  paint(box) {{
    const look = this.shownLook();
    if (look === null) return;
    const key = JSON.stringify(look);
    if (box.dataset.look === key) return;
    box.dataset.look = key;
    const theme = THEMES[look.theme] ?? THEMES[DARK];
    for (const [name, value] of Object.entries(theme)) box.style.setProperty("--" + name, value);
    box.style.setProperty("--c-elapsed", look.elapsed);
    box.style.setProperty("--c-remaining", look.remaining);
    box.style.setProperty("--c-warning", look.warning);
    box.classList.toggle("light", look.theme === LIGHT);
    for (const group of box.querySelectorAll(".swatches")) {{
      for (const button of group.querySelectorAll("button")) {{
        button.classList.toggle("on", button.dataset.c === look.names[group.dataset.t]);
      }}
    }}
  }},

  // Draw the texts, labels, sizes and colours into a panel (root: the .clock element) or the pop-out (its document).
  draw(root, mode) {{
    const t = this.texts(mode);
    if (t === null) return;
    this.paint(root.body ?? root);
    const warningCell = root.querySelector("[data-w]");
    if (warningCell !== null) warningCell.hidden = t.warning === null;
    for (const name of ["elapsed", "remaining", "limit", "warning"]) {{
      if (name === "warning" && t.warning === null) continue;
      const value = root.querySelector('[data-v="' + name + '"]');
      const label = root.querySelector('[data-l="' + name + '"]');
      if (label !== null) {{
        const overtime = name === "remaining" && t.over;
        const text = LABELS[mode][overtime ? "overtime" : name];
        if (label.textContent !== text) label.textContent = text;
        label.classList.toggle("over", overtime);
        label.classList.toggle("blink", overtime);
      }}
      if (value === null) continue;
      // BUZZ in place of Remaining's figures, sized as five of them at the least: its letters are wider than digits.
      const buzz = name === "remaining" && t.buzz;
      const text = buzz ? BUZZ_TEXT : t[name];
      if (value.textContent !== text) {{
        value.textContent = text;
        value.style.setProperty("--chars", String(Math.max(buzz ? 5 : 4, t[name].length)));
      }}
      if (name === "elapsed" || name === "remaining") {{
        const scale = String((this.preview[name] ?? this.base.sizes[name] ?? SIZE_DEFAULT) / 100);
        if (value.style.getPropertyValue("--scale") !== scale) value.style.setProperty("--scale", scale);
      }}
      if (name === "remaining") {{
        value.classList.toggle("over", t.out && !buzz);
        value.classList.toggle("blink", t.out && !buzz);
        value.classList.toggle("warn", t.warn && !buzz);
        value.classList.toggle("buzz", buzz);
      }}
    }}
    for (const button of root.querySelectorAll(".modes button")) button.classList.toggle("on", button.dataset.m === mode);
    const group = root.querySelector(".daynight");
    if (group !== null) {{
      if (group.hidden === (this.host === false)) group.hidden = this.host !== false;
      const on = this.shownLook()?.theme === LIGHT ? DAY : NIGHT;
      for (const button of group.querySelectorAll("button")) button.classList.toggle("on", button.dataset.d === on);
    }}
    this.buzzer(t.buzz);
  }},

  // Whether this page may sound: clicked, or a key pressed on it, since it
  // loaded (the browsers' rule for sound); where the browser cannot say,
  // whether its sound is running.
  mayPlay() {{
    const activation = navigator.userActivation;
    return activation ? activation.hasBeenActive : this.audio !== null && this.audio.state === "running";
  }},

  // The page's sound: made, or woken, on a click or a key (_CLOCK_JS, listen),
  // or once the page may sound. None on a silent page, or before the clock has
  // said which page this is.
  wake() {{
    if (this.host === null || this.silent) return;
    try {{
      this.audio ??= new AudioContext();
      if (this.audio.state === "suspended") this.audio.resume().catch(() => {{}});
    }} catch (error) {{}}  // no sound in this browser
  }},

  // The buzzer on this page, sounding while on: called by every check-in and
  // every draw, the pop-out's included, so that a page hidden behind the
  // slides, its own timers slowed, still starts and stops in time. The host's
  // page goes by its own button, not the server's word, which comes later: it
  // starts and stops with the press, and a check-in cannot break it up.
  buzzer(on) {{
    if (this.host === null || this.silent) on = false;
    else {{
      if (this.host) on = this.buzzHeld;
      if (this.audio === null && this.mayPlay()) this.wake();
    }}
    const ctx = this.audio;
    if (on && this.tone === null && ctx !== null && ctx.state === "running") {{
      const now = ctx.currentTime;
      const gain = ctx.createGain();
      gain.gain.setValueAtTime(0, now);
      gain.gain.linearRampToValueAtTime(BUZZ.gain, now + BUZZ.attack);
      const filter = ctx.createBiquadFilter();
      filter.type = "lowpass";
      filter.frequency.value = BUZZ.lowpass;
      filter.connect(gain);
      gain.connect(ctx.destination);
      const oscillators = BUZZ.hz.map((hz) => {{
        const oscillator = ctx.createOscillator();
        oscillator.type = "square";
        oscillator.frequency.value = hz;
        oscillator.connect(filter);
        oscillator.start(now);
        return oscillator;
      }});
      this.tone = {{ gain, oscillators }};
    }} else if (!on && this.tone !== null) {{
      const {{ gain, oscillators }} = this.tone;
      this.tone = null;
      const now = ctx.currentTime;
      gain.gain.cancelScheduledValues(now);
      gain.gain.setValueAtTime(gain.gain.value, now);
      gain.gain.linearRampToValueAtTime(0, now + BUZZ.release);
      for (const oscillator of oscillators) oscillator.stop(now + BUZZ.release + 0.01);
    }}
  }},

  // The pop-out's copy of the panel scaled to fit its window (v1.11). Its
  // zoom (pipZoom) follows the window, sized by hand or not; when the copy's
  // own size changes (a slider, longer figures, the switch, the warning),
  // the window is resized to show it at the zoom it had, which Chrome allows
  // a pop-up always and a pop-out only just after a click or a key (else it
  // throws, and the copy is scaled to the window it has).
  fitPip(win, doc) {{
    const stage = doc.querySelector(".stage");
    if (stage === null || doc.body.classList.contains("gone")) return;
    const natW = stage.offsetWidth, natH = stage.offsetHeight;
    const W = win.innerWidth, H = win.innerHeight;
    if (natW === 0 || natH === 0 || W === 0 || H === 0) return;
    const fit = PIP_FILL * Math.min(W / natW, H / natH);
    const key = natW + "x" + natH;
    if (key !== this.pipNat) {{
      const zoom = this.pipZoom ?? fit;
      this.pipNat = key;
      const dw = Math.ceil(natW * zoom / PIP_FILL) - W, dh = Math.ceil(natH * zoom / PIP_FILL) - H;
      this.pipWant = [W + dw, H + dh];
      if (Math.abs(dw) > 1 || Math.abs(dh) > 1) {{
        try {{
          win.resizeBy(dw, dh);
          this.pipRefused = false;
        }} catch (error) {{
          this.pipRefused = true;  // a pop-out with no click or key just before
        }}
      }}
    }} else {{
      this.pipZoom = fit;
    }}
    const z = String(+fit.toFixed(4));
    if (stage.style.getPropertyValue("--z") !== z) stage.style.setProperty("--z", z);
  }},

  // The host's line in a pop-out or pop-up: shown on the host's page alone (a
  // viewer made host gains it), its buttons as the page's are: greyed where
  // they would do nothing, Start reading Restart once the timer has run.
  drawBar(doc) {{
    const host = this.host === true, b = this.base;
    for (const button of doc.querySelectorAll("[data-host]")) {{
      if (button.hidden === host) button.hidden = !host;
    }}
    if (!host || b === null) return;
    const start = doc.querySelector('[data-a="start"]');
    const label = b.running || b.elapsed > 0 ? RESTART : START;
    if (start.textContent !== label) start.textContent = label;
    start.disabled = b.running;
    doc.querySelector('[data-a="stop"]').disabled = !b.running;
    doc.querySelector('[data-a="reset"]').disabled = !b.running && b.elapsed === 0;
    doc.querySelector(".buzz").classList.toggle("on", this.buzzHeld);
  }},

  // The pop-out (popup false), or a plain pop-up (true): drawn and timed from
  // its own window, with its own switch, starting at the panel's choice. The
  // pop-out stays on top; a pop-up does not, and outlives its tab, so it has
  // a watchdog of its own (_WATCH_JS), and a tab reloaded takes it back
  // (adopt): laid out again here, its old style replaced. The host's line is
  // wired by the clock's script, which holds the Buzzer's code (openWindow).
  openPip(win, popup) {{
    const h = this;
    h.pipMode = h.pageMode;
    h.pipNat = null;
    h.pipZoom = null;
    h.pipWant = null;
    h.pipRefused = false;
    const doc = win.document;
    doc.title = "SpeakerTime";
    doc.head.querySelector("style[data-speakertime]")?.remove();
    const style = doc.createElement("style");
    style.dataset.speakertime = "1";
    style.textContent = PIP_CSS;
    doc.head.appendChild(style);
    doc.body.innerHTML = PIP_HTML;
    for (const button of doc.querySelectorAll(".modes button")) {{
      button.addEventListener("click", () => {{
        h.pipMode = button.dataset.m;
        h.draw(doc, h.pipMode);
        h.fitPip(win, doc);
      }});
    }}
    for (const button of doc.querySelectorAll(".daynight button")) {{
      button.addEventListener("click", () => {{
        h.setDayNight(button.dataset.d);
        h.draw(doc, h.pipMode);
      }});
    }}
    const tick = () => {{
      win.speakerTimeBeat = Date.now();
      doc.body.classList.remove("gone");
      h.draw(doc, h.pipMode);
      h.drawBar(doc);
      h.fitPip(win, doc);
      h.keepBuzz?.();
      const now = performance.now();
      if (h.kick !== null && now - h.lastSync > KICK_AFTER_MS && now - h.lastKick > KICK_AFTER_MS) {{
        h.lastKick = now;
        try {{ h.kick(); }} catch (error) {{}}  // the element that gave it is gone: the next one gives another
      }}
    }};
    tick();
    win.setInterval(tick, DRAW_MS);
    if (popup) {{
      const watch = doc.createElement("script");  // made in the pop-up's document, so run by its window
      watch.textContent = WATCH_JS;
      doc.head.appendChild(watch);
    }}
    win.addEventListener("pagehide", () => {{ if (h.pip === win) h.closed(); }});
    h.pip = win;
    h.popup = popup;
    h.pipOpenedAt = performance.now();
  }},

  // The pop-out or pop-up has closed.
  closed() {{
    this.pip = null;
    this.popup = false;
    try {{ sessionStorage.removeItem(POPUP_KEY); }} catch (error) {{}}
  }},
}});
"""

_CLOCK_HTML = (
    '<div class="scroll"><div class="clock">'
    + "".join(_row(name, name in SIZED) for name in ("elapsed", "remaining", "limit"))
    + f'<div class="tools">{_MODES_HTML}{_DAYNIGHT_HTML}<button class="pop" type="button" hidden></button>'
    + f'<button class="buzzer" type="button" hidden>{_BUZZER_LABEL}</button></div>'
    + '<div class="why" hidden></div>'
    + '<div class="note" hidden></div>'
    + f'<div class="buzz-note" hidden>{_BUZZ_NOTE}</div>'
    + "</div></div>"
)

# The panel is as wide as its widest row (max-content), the figures' rows deciding it: the row of buttons and the
# notes wrap within it (contain: inline-size). The host's panel is one grid whose timer rows share its columns
# (subgrid): the figures' column is as wide as the larger timer's figures, the sliders and colour buttons of both
# timers stand in one column just beside it, and any width to spare (the panel's narrowest, a wider Time Limit row)
# goes to the two outer columns, so they stay beside it and the figures in the middle (v1.12: in v1.11 the spare width
# went into the figures' column, 36 px between the figures and the sliders at the default size).
_CLOCK_CSS = f"""
.scroll {{ overflow-x: auto; overflow-y: hidden; }}
.clock {{
  {_LOOK_CSS}
  zoom: var(--speakertime-panel, 1);
  border-radius: 14px; padding: 14px {_PANEL_PAD_PX}px 12px; box-shadow: inset 0 0 0 1px var(--line);
  box-sizing: border-box; width: max-content; min-width: min(100%, {_PANEL_MIN_PX}px); margin: 0 auto;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}}
.clock.host {{ display: grid; grid-template-columns: minmax(0, 1fr) {_TOOLS_PX}px max-content 30px 26px minmax(0, 1fr); }}
.clock.host > * {{ grid-column: 1 / -1; }}
.row {{ display: grid; grid-template-columns: minmax(0, 1fr); margin-bottom: 10px; }}
.clock.host .row.timer {{ grid-template-columns: subgrid; }}
.cell {{ grid-column: 1; min-width: 0;
  display: flex; flex-direction: column; align-items: center; line-height: 1; }}
.pair {{ grid-column: 1; min-width: 0; }}
.clock.host .row.timer > .cell {{ grid-column: 3; }}
.size {{ grid-column: 4; justify-self: center; align-self: stretch; margin: 0; width: 22px; min-height: 64px;
  writing-mode: vertical-lr; direction: rtl; accent-color: {_SLIDER_COLOUR}; cursor: ns-resize; }}
.size[hidden], .swatches[hidden] {{ display: none; }}
.swatches {{ grid-column: 5; align-self: center; display: flex; flex-direction: column; align-items: center; gap: 5px; }}
.swatches button {{ width: 17px; height: 17px; padding: 0; border-radius: 50%; cursor: pointer;
  background: var(--swatch); border: 1px solid var(--line); }}
.swatches button.on {{ outline: 2px solid var(--text); outline-offset: 2px; }}
.label {{ font-size: clamp(14px, 4.2vw, 24px); margin-bottom: 4px; }}
.value {{ --scale: 1; --chars: 4; font-weight: 700; font-variant-numeric: tabular-nums; white-space: nowrap;
  font-size: calc(var(--scale) * {_PANEL_UNIT}); }}
@media (max-width: {_PHONE_PX - 0.02}px) {{
  .clock {{ --room: calc(100vw - 2rem - {2 * _PANEL_PAD_PX}px); max-width: 100%; }}
  .clock.host {{ --room: calc(100vw - 2rem - {2 * _PANEL_PAD_PX + 2 * _TOOLS_PX}px); }}
  .value {{ font-size: min(calc(var(--scale) * {_PANEL_UNIT}), calc(var(--room) / (var(--chars) * {_CHAR_EM}))); }}
}}
.limit .value {{ font-size: clamp(22px, 7vw, 54px); font-weight: 600; }}
.limit .label {{ font-size: clamp(14px, 3.4vw, 22px); }}
.pair .cell + .cell {{ margin-left: clamp(28px, 6.8vw, 44px); }}
.row[data-r="remaining"], .row[data-r="limit"] {{ margin-top: 20px; }}
{_SHARED_CSS}
.tools {{ display: flex; justify-content: center; align-items: center; flex-wrap: wrap; gap: 8px 12px; margin-top: 4px;
  contain: inline-size; }}
.modes, .daynight {{ display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
.modes button, .daynight button {{ font: inherit; font-size: 14px; color: var(--text); background: transparent; border: 0;
  padding: 4px 10px; cursor: pointer; }}
.pop, .buzzer {{
  font: inherit; font-size: 14px; color: var(--text); background: transparent;
  border: 1px solid var(--line); border-radius: 8px; padding: 4px 12px; cursor: pointer;
}}
.pop:hover {{ border-color: var(--text); }}
.buzzer {{ border-color: var(--over); touch-action: none; user-select: none; -webkit-user-select: none; }}
.buzzer.on {{ background: var(--over); color: var(--panel); }}
.note, .why, .buzz-note {{ font-size: 13px; text-align: center; margin-top: 6px; contain: inline-size; }}
"""

_POP_OPEN = "Pop out (stays on top)"
_POP_CLOSE = "Close the pop-out"
_POPUP_OPEN = "Pop-up (may get obscured)"
_POPUP_CLOSE = "Close the pop-up"
_ELSEWHERE = "Another tab of this browser has the on-top pop-out{of}. The browser allows one at a time."
"""Under the button while it offers a pop-up; {of} is " (session ID)", or nothing when the other tab's session is not known."""
_POPUP_BLOCKED = "The pop-up was blocked: allow pop-ups for this site in the browser."
_UPDATED_TEXT = "SpeakerTime was updated, which closed the pop-out: press the button to open it again."

_POP_LOCK = "speakertime-popout"
"""The lock (Web Locks) a tab holds while its pop-out is open, so other tabs of the browser offer a pop-up instead.

Terry (2026-11-03): Chrome keeps one pop-out at a time for the whole
browser, so a tab finding one open labels its button for a plain pop-up,
not kept on top; once the pop-out closes, the tabs without a pop-up offer
the pop-out again, until one of them takes it. The lock is asked for
before the pop-out, and only if free, so the first press wins (measured
2026-11-03: two tabs pressed together, one had it). The browser frees a
lock when its tab goes, however it goes, so no tab can leave it held
(measured: a tab closed with no pagehide). Only this browser profile's
tabs of this site see it: another profile, an Incognito window, or another
site's picture-in-picture still closes the pop-out unseen.
"""
_POP_LOCK_OF = "speakertime-popout-of "
"""Held with _POP_LOCK, its name ending in the session's ID, for the other tabs' note."""
_LOCKS_MS = 1000
"""How often a tab looks at the locks held: a closed pop-out is offered again within about this long."""
_POPUP_FEATURES = "popup,width=340,height=280"

_LOST_AFTER = 2
"""Check-ins running that find the clock gone from the page before the page is asked for again (Streamlit's whole run).

Measured 2026-11-03 (Chrome on Buddug, verify_browser, the same with
v1.1's code): a whole run of the host's page, as a slider let go makes,
sometimes left the clock out of the page for good, everything else laid
out and nothing in the console, and the check-ins (a fragment's runs) never
lay it out again. Two, not one: the clock is missing for a moment while a
whole run lays it out again, and that is not a loss.
"""

_POP_GUARD_MS = 1000
"""A click on the pop-out's button this soon after the pop-out opened is ignored: a double click would close it at once.

Terry (2026-11-01) saw a pop-out "appear then immediately disappear". Two
causes are possible and both are dealt with: a double click (this), and a
second pop-out opened from another tab or window of the same browser, which
Chrome allows one of at a time, closing the first (measured 2026-11-03):
another tab of this browser now offers a pop-up instead (_POP_LOCK).
"""

_CLOCK_JS = (
    _HUB_JS
    + f"""
const POP_OPEN = {json.dumps(_POP_OPEN)};
const POP_CLOSE = {json.dumps(_POP_CLOSE)};
const POPUP_OPEN = {json.dumps(_POPUP_OPEN)};
const POPUP_CLOSE = {json.dumps(_POPUP_CLOSE)};
const ELSEWHERE = {json.dumps(_ELSEWHERE)};
const POPUP_BLOCKED = {json.dumps(_POPUP_BLOCKED)};
const UPDATED_TEXT = {json.dumps(_UPDATED_TEXT)};
const POP_GUARD_MS = {_POP_GUARD_MS};
const POP_LOCK = {json.dumps(_POP_LOCK)};
const POP_LOCK_OF = {json.dumps(_POP_LOCK_OF)};
const LOCKS_MS = {_LOCKS_MS};
const POPUP_FEATURES = {json.dumps(_POPUP_FEATURES)};
const PIP_SIZE = {{ width: 340, height: 280 }};
const BUZZ_RESEND_MS = {_BUZZ_RESEND_MS};
const BUZZ_CHECK_MS = {_BUZZ_CHECK_MS};
const SESSION_SWITCH = {json.dumps(_SESSION_SWITCH)};
const COPY_BUTTON = {json.dumps(f".st-key-{_COPY_BUTTON} button")};
const COPY_LABEL = {json.dumps(_COPY_LABEL)};
const COPIED_LABEL = {json.dumps(_COPIED_LABEL)};
const NOT_COPIED_LABEL = {json.dumps(_NOT_COPIED_LABEL)};
const COPY_SHOWN_MS = {_COPY_SHOWN_MS};
const FIT_SHARE = {_FIT_SHARE};
const FIT_PANEL_PX = {_FIT_PANEL_PX};
const FIT_TEXT = {_FIT_TEXT};
const FIT_LEAST = {_FIT_LEAST};
const FIT_GROW_MS = {_FIT_GROW_MS};
const FIT_SETTLED_MS = {_FIT_SETTLED_MS};
const FIT_LONGEST_MS = {_FIT_LONGEST_MS};

// The Document Picture-in-Picture API of the top window: a pop-out may be
// opened only from a top-level page (Chrome: "only allowed from a top-level
// browsing context", measured 2026-10-30), and on Streamlit Community Cloud
// the app runs in a frame of the same origin as the top page; a click in the
// frame lets the top page open it. None in other browsers, or if the top
// page is another site's.
const pipApi = () => {{
  try {{
    return window.top.documentPictureInPicture ?? null;
  }} catch (error) {{
    return null;
  }}
}};

// Whether another tab of this browser holds the pop-out (POP_LOCK), looked at
// every LOCKS_MS: h.elsewhere, its session ("" if it cannot say), or null.
const lookElsewhere = (h) => {{
  const now = performance.now();
  if (!navigator.locks || now - h.locksAt < LOCKS_MS) return;
  h.locksAt = now;
  navigator.locks.query().then((state) => {{
    const names = state.held.map((lock) => lock.name);
    if (!names.includes(POP_LOCK) || (h.pip !== null && !h.popup)) {{
      h.elsewhere = null;
      return;
    }}
    const of = names.find((name) => name.startsWith(POP_LOCK_OF));
    h.elsewhere = of === undefined ? "" : of.slice(POP_LOCK_OF.length);
  }}, () => {{}});
}};

// The scales that fit the page to its window (_FIT_SHARE, _FIT_TEXT), worked out from
// the page as it stands at the scales it has: the panel as it would be unscaled,
// and the rest of the column at the column's scale alone. The column's padding
// is outside its scale, so that the title stays clear of Streamlit's top bar.
// A panel still wider than its place sets the most the two may come to.
// Null while the page cannot say.
const fitted = (h, root) => {{
  const main = document.querySelector('[data-testid="stMain"]');
  const outer = document.querySelector('[data-testid="stMainBlockContainer"]');
  const column = outer?.querySelector(':scope > [data-testid="stVerticalBlock"]');
  if (!main || !column) return null;
  const padding = getComputedStyle(outer);
  const room = main.clientHeight - parseFloat(padding.paddingTop) - parseFloat(padding.paddingBottom) - 1;
  const drawn = root.getBoundingClientRect().height;
  if (room <= 0 || drawn <= 0) return null;
  // The scales in effect, not those asked for: while the column's rule does not hold yet (_PAGE_STYLE), or its elements
  // are still arriving, dividing by the scale asked for makes the rest of the page look larger the smaller the scale,
  // and the page falls to its floor in a few draws.
  const zoomed = (element) => parseFloat(getComputedStyle(element).zoom) || 1;
  const pageNow = zoomed(column), panelNow = zoomed(root);
  const panel = drawn / (pageNow * panelNow);
  const others = (column.getBoundingClientRect().height - drawn) / pageNow;
  const scroll = root.parentElement;
  if (scroll.scrollWidth > scroll.clientWidth) {{
    h.fitting.widest = Math.min(h.fitting.widest, pageNow * panelNow * scroll.clientWidth / scroll.scrollWidth);
  }}
  const most = h.fitting.most;  // the highest the column's scale may go (fit)
  let page = most;
  let scale = (room - others * page) / (page * panel);
  const least = Math.min(FIT_SHARE * room, FIT_PANEL_PX, panel);  // the panel's height as drawn, in a small window
  if (scale * page < 1 && scale * page * panel < least) {{
    // The panel at its least, and the text and controls around it FIT_TEXT times what would keep all of the page inside
    // the window: the page scrolls to reach the rest.
    page = Math.min(most, FIT_TEXT * Math.max(FIT_LEAST, (room - least) / others));
    scale = least / (page * panel);
  }}
  const down = (value) => Math.floor(value * 1000) / 1000;
  // The column's scale reaches the panel inside it, so the panel's own may pass 1 where the column's is below it: what
  // limits the panel is its size as drawn, never above its full size (widest starts at 1) or wider than its place.
  return {{ page: down(page), panel: down(Math.max(FIT_LEAST, Math.min(scale, h.fitting.widest / page))) }};
}};

// A fit's state, begun now: when it began and last changed; how wide the panel may be; the most the column's scale may
// go, and whether it has risen yet.
const fitStart = (now) => ({{ at: now, changed: now, widest: 1, most: 1, rose: false }});

// Fits the page to its window after it connects to a session, as its elements
// arrive: run at every draw until the scales settle, and not again until the
// next session entered, so the window and the sliders are the user's after
// that. Measured only between runs of the page: during one, the last screen's
// elements may still be there (Streamlit's own mark, kept for its tests;
// should it go, the scales would only move about a little more at the start).
const fit = (h, root) => {{
  const f = h.fitting;
  if (f === null) return;
  const now = performance.now();
  if (now - f.at > FIT_LONGEST_MS) {{
    h.fitting = null;
    return;
  }}
  if (document.querySelector('[data-testid="stApp"]')?.getAttribute("data-test-script-state") === "running") return;
  const want = fitted(h, root);
  if (want === null) {{
    // No window yet (Community Cloud keeps the app's frame hidden, at no size, while the page loads): the fit's time
    // starts when there is one.
    f.at = f.changed = now;
    return;
  }}
  // The column's scale changes how its text wraps, so a scale's measure says little about a higher one: at phone widths the
  // host's page measured at 0.565 as if 1 would fit, and at 1 as needing 0.565, flipping at every draw until the fit's
  // time ran out, wherever that left it (2026-11-04, 66 to 304 px past the window). So a rise that did not fit sets the
  // most the column's scale may go; at that scale the panel takes the room there is. After FIT_GROW_MS it only falls.
  const falling = want.page < h.fitPage - 0.001, rising = want.page > h.fitPage + 0.01;
  if (falling && f.rose) f.most = want.page;
  const late = now - f.at >= FIT_GROW_MS;
  if (late) f.most = Math.min(f.most, falling ? want.page : h.fitPage);
  if (falling || want.panel < h.fitPanel - 0.001 || (!late && (rising || want.panel > h.fitPanel + 0.01))) {{
    if (rising) f.rose = true;
    h.fitPage = want.page;
    h.fitPanel = want.panel;
    document.documentElement.style.setProperty("--speakertime-page", String(want.page));
    document.documentElement.style.setProperty("--speakertime-panel", String(want.panel));
    f.changed = now;
  }} else if (now - f.changed > FIT_SETTLED_MS) {{
    // Settled at the floor: fitted once more, what was measured while the page arrived not having been the page.
    // Seen once, 2026-11-04: a page left at the floor that a reload fitted at 1 and 0.609.
    h.fitting = !f.looked && h.fitPage <= FIT_TEXT * FIT_LEAST + 0.001 ? {{ ...fitStart(now), looked: true }} : null;
  }}
}};

// The panel as it is now (h.clock, the element last laid out): drawn every
// DRAW_MS by the page's one timer, with the pop button's label and note.
const drawPanel = (h) => {{
  const root = h.clock;
  if (root === null || !root.isConnected) return;
  h.draw(root, h.pageMode);
  fit(h, root);
  lookElsewhere(h);
  if (h.pip !== null && h.pip.closed) h.closed();  // its pagehide missed
  const pop = root.querySelector(".pop");
  const label = h.pip !== null ? (h.popup ? POPUP_CLOSE : POP_CLOSE) : h.elsewhere !== null ? POPUP_OPEN : POP_OPEN;
  if (pop.textContent !== label) pop.textContent = label;
  const why = root.querySelector(".why");
  const text = pop.hidden || label !== POPUP_OPEN ? ""
    : ELSEWHERE.replace("{{of}}", h.elsewhere ? " (session " + h.elsewhere + ")" : "");
  if (why.textContent !== text) why.textContent = text;
  why.hidden = text === "";
  keepBuzz(h);
  root.querySelector(".buzzer").classList.toggle("on", h.buzzHeld);
  const deaf = root.querySelector(".buzz-note");
  const hidden = h.host !== false || h.silent || h.mayPlay();
  if (deaf.hidden !== hidden) deaf.hidden = hidden;
}};

// Copy Link (Terry, 2026-11-04): the address that connects to this session by
// itself (session_asked), with none of this page's own switches, so never
// ?silent. The page's address without its query, the top window's: on Community
// Cloud the app runs in a frame of the same origin. The clipboard's API where
// the browser allows it, else the older way, both within the click's activation.
const copyLink = async (h, button) => {{
  if (h.base === null) return;
  let place = location;
  try {{ place = window.top.location; }} catch (error) {{}}
  const link = place.origin + place.pathname + "?" + SESSION_SWITCH + "=" + encodeURIComponent(h.base.sid);
  let copied = false;
  try {{
    await navigator.clipboard.writeText(link);
    copied = true;
  }} catch (error) {{
    const box = document.createElement("textarea");
    box.value = link;
    box.style.position = "fixed";
    box.style.opacity = "0";
    document.body.appendChild(box);
    box.select();
    try {{ copied = document.execCommand("copy"); }} catch (older) {{}}
    box.remove();
  }}
  // Said on the button, its width kept: only the text node changes, which Streamlit's next run leaves alone or lays out afresh.
  const text = button.querySelector("p")?.firstChild;
  if (!text) return;
  clearTimeout(h.copyTimer);
  button.style.minWidth = button.offsetWidth + "px";
  text.nodeValue = copied ? COPIED_LABEL : NOT_COPIED_LABEL;
  h.copyTimer = setTimeout(() => {{
    text.nodeValue = COPY_LABEL;
    button.style.minWidth = "";
  }}, COPY_SHOWN_MS);
}};

// The host's Buzzer to the server, as this page holds it now, through the
// check-in's element (h.buzzSend, from _SYNC_JS).
const sendBuzz = (h) => {{
  h.buzzSentAt = performance.now();
  try {{ h.buzzSend?.(h.buzzWant); }} catch (error) {{}}  // the element that gave it is gone: sent again (keepBuzz)
}};

// The Buzzer held (by "pointer" or "key", in window ``win``) or let go (null):
// shown on this panel at once, and sent.
const hold = (h, by, win = null) => {{
  const on = by !== null;
  if (on === h.buzzHeld) return;
  h.buzzHeld = on;
  h.buzzBy = by;
  h.buzzWin = win;
  h.buzzWant = on;
  sendBuzz(h);
  drawPanel(h);
  if (h.pip !== null) h.drawBar(h.pip.document);
}};

// While it is held, said again every BUZZ_RESEND_MS (the server stops it
// after BUZZ_HOLD_S without word); held or let go, sent again while a
// check-in come since, BUZZ_CHECK_MS after the send, still disagrees.
const keepBuzz = (h) => {{
  if (h.buzzWant === null || h.base === null) return;
  const since = performance.now() - h.buzzSentAt;
  const disagrees = h.lastSync > h.buzzSentAt && (h.base.buzz === true) !== h.buzzWant;
  if ((h.buzzHeld && since > BUZZ_RESEND_MS) || (disagrees && since > BUZZ_CHECK_MS)) sendBuzz(h);
}};

// A window's own listeners, added once for the page and once for each pop-out
// or pop-up: the Buzzer let go wherever the pointer is when its button comes up
// (or a pointerup missed: a move with no button down), its key let go, or the
// window left; and a click or a key anywhere on it lets the page sound. Only
// the window holding it lets go: pressing a button in a pop-out takes the focus
// from the page behind it, whose blur would otherwise end the hold at once.
const listen = (h, win) => {{
  const letGo = () => {{ if (h.buzzWin === win) hold(h, null); }};
  const isKey = (event) => event.key === " " || event.key === "Enter";
  win.addEventListener("pointerup", letGo);
  win.addEventListener("pointercancel", letGo);
  win.addEventListener("blur", letGo);
  win.addEventListener("pagehide", letGo);
  win.addEventListener("pointermove", (event) => {{ if (h.buzzBy === "pointer" && (event.buttons & 1) === 0) letGo(); }});
  win.addEventListener("keyup", (event) => {{ if (h.buzzBy === "key" && isKey(event)) letGo(); }});
  win.document.addEventListener("visibilitychange", () => {{ if (win.document.hidden) letGo(); }});
  win.addEventListener("pointerdown", () => h.wake(), true);
  win.addEventListener("keydown", () => h.wake(), true);
}};

// A Buzzer's button, in the page or a pop-out: held while it is down, or Space
// or Enter on it; let go by its window's listeners (listen).
const wireBuzzer = (h, buzzer, win) => {{
  buzzer.addEventListener("pointerdown", (event) => {{
    if (event.button !== 0) return;
    event.preventDefault();
    try {{ buzzer.setPointerCapture(event.pointerId); }} catch (error) {{}}
    hold(h, "pointer", win);
  }});
  buzzer.addEventListener("keydown", (event) => {{
    if (event.key !== " " && event.key !== "Enter") return;
    event.preventDefault();
    if (!event.repeat) hold(h, "key", win);
  }});
  buzzer.addEventListener("contextmenu", (event) => event.preventDefault());  // a long press on a phone
}};

// A pop-out or pop-up opened, or a pop-up taken back, with the host's line wired.
// The timer's buttons go through the clock's trigger, as a whole run: the page's
// own Start, Stop and Reset are laid out by it, and must agree with these.
const openWindow = (h, win, popup) => {{
  h.openPip(win, popup);
  const doc = win.document;
  for (const button of doc.querySelectorAll("[data-a]")) {{
    button.addEventListener("click", () => h.send?.("control", {{ action: button.dataset.a, n: Date.now() }}));
  }}
  wireBuzzer(h, doc.querySelector(".buzz"), win);
  listen(h, win);
}};

// The pop-out, if no other tab of this browser holds it: the lock is asked
// for first, and only if free, so the first of several presses wins. True
// once open, false if another tab holds it. Both locks are held until the
// pop-out closes (its pagehide), or the browser frees them with the tab.
const claim = (h, api) => new Promise((resolve, reject) => {{
  // Gives {{ closed }}, not the promise itself, which an await would wait out.
  const open = async () => {{
    const win = await api.requestWindow(PIP_SIZE);
    const closed = new Promise((done) => win.addEventListener("pagehide", done));
    openWindow(h, win, false);
    return {{ closed }};
  }};
  if (!navigator.locks) {{
    open().then(() => resolve(true), reject);
    return;
  }}
  navigator.locks.request(POP_LOCK, {{ ifAvailable: true }}, async (lock) => {{
    if (lock === null) {{
      resolve(false);
      return;
    }}
    let pip;
    try {{
      pip = await open();
    }} catch (error) {{
      reject(error);
      return;
    }}
    resolve(true);
    await navigator.locks.request(POP_LOCK_OF + (h.base?.sid ?? ""), () => pip.closed);
  }}).catch(reject);
}});

// A plain pop-up, not kept on top: any number of tabs may have one. Its name
// is kept in the tab's sessionStorage, for adopt.
const openPopup = (h, note) => {{
  const name = "speakertime_popup_" + Math.random().toString(36).slice(2, 10);
  const win = window.top.open("", name, POPUP_FEATURES);
  if (win === null) {{
    note.textContent = POPUP_BLOCKED;
    note.hidden = false;
    return;
  }}
  try {{ sessionStorage.setItem(POPUP_KEY, name); }} catch (error) {{}}
  openWindow(h, win, true);
}};

// A tab reloaded takes back the pop-up it had: a plain window outlives its
// tab, frozen, and is found again by its name with no click needed
// (measured 2026-11-03, Chrome). If it was closed meanwhile, the name opens
// a new, empty window instead (where pop-ups are allowed): closed at once.
const adopt = (h) => {{
  h.adopted = true;
  let name = null;
  try {{ name = sessionStorage.getItem(POPUP_KEY); }} catch (error) {{}}
  if (name === null || h.pip !== null) return;
  let win = null;
  try {{ win = window.top.open("", name, POPUP_FEATURES); }} catch (error) {{}}
  if (win !== null && win.document.querySelector(".gone-note") !== null) {{
    openWindow(h, win, true);
    return;
  }}
  if (win !== null) win.close();
  h.closed();
}};

// Called again with each run's data (Streamlit 1.64 components v2), on the
// same element or a new one, and the cleanup a call returns is not called
// between calls: measured 2026-11-03, a timer started at each call made 210
// draws a second after 20 runs. So each call takes the run's data, the
// buttons' listeners are added once per element (sending through h.send,
// the latest call's), and the page has one timer (h.panelTimer).
export default function (component) {{
  const {{ data, parentElement, setTriggerValue }} = component;
  if (reloading()) return;
  const h = hub();
  h.update(data);
  h.pageMode = data.mode;
  h.preview = {{}};  // the sizes laid out with this run are the server's
  h.send = setTriggerValue;
  const root = parentElement.querySelector(".clock");
  h.clock = root;
  h.host = data.host;
  h.silent = data.silent === true;
  if (h.entered !== data.entered) {{
    h.entered = data.entered;
    h.fitting = fitStart(performance.now());
    // A page that may scroll (_FIT_TEXT) starts at its top, not where the start screen was left to reach its buttons.
    document.querySelector('[data-testid="stMain"]')?.scrollTo(0, 0);
    h.dayNight = null;  // read again: Leave Session may have forgotten it
  }}
  root.classList.toggle("host", data.host);
  root.querySelector(".buzzer").hidden = !data.host;
  if (!data.host) {{
    h.buzzHeld = false;
    h.buzzBy = null;
    h.buzzWin = null;
    h.buzzWant = null;
  }}
  if (!h.listening) {{
    h.listening = true;
    h.keepBuzz = () => keepBuzz(h);
    listen(h, window);
    // On the document, once: Streamlit lays the button out afresh on its runs. Before Streamlit's own handler, whose run may rebuild it.
    document.addEventListener("click", (event) => {{
      const button = event.target.closest?.(COPY_BUTTON);
      if (button) copyLink(h, button);
    }}, true);
  }}
  if (data.host) {{
    for (const slider of root.querySelectorAll("input.size")) {{
      slider.value = String(data.sizes[slider.dataset.t]);
      slider.hidden = false;
    }}
    for (const group of root.querySelectorAll(".swatches")) group.hidden = false;
  }}
  h.panelTimer ??= setInterval(() => drawPanel(h), DRAW_MS);
  if (root.dataset.wired !== "1") {{
    root.dataset.wired = "1";
    wire(h, root);
  }}
  if (!h.adopted) adopt(h);
  try {{
    if (sessionStorage.getItem(UPDATED_KEY) !== null) {{
      sessionStorage.removeItem(UPDATED_KEY);
      const note = root.querySelector(".note");
      note.textContent = UPDATED_TEXT;
      note.hidden = false;
    }}
  }} catch (error) {{}}
  drawPanel(h);
}}

function wire(h, root) {{
  const note = root.querySelector(".note");
  // The host's sliders: shown at once while dragged, sent when let go.
  for (const slider of root.querySelectorAll("input.size")) {{
    const name = slider.dataset.t;
    slider.addEventListener("input", () => {{
      h.fitting = null;  // the host has taken over the size
      h.preview[name] = Number(slider.value);
      drawPanel(h);
    }});
    slider.addEventListener("change", () => {{
      h.preview[name] = Number(slider.value);
      h.send("size", {{ timer: name, percent: Number(slider.value) }});
    }});
  }}
  // The colour buttons beside each slider: the server applies Terry's rule for black and white.
  for (const group of root.querySelectorAll(".swatches")) {{
    for (const button of group.querySelectorAll("button")) {{
      button.addEventListener("click", () => {{
        if (button.classList.contains("on")) return;
        h.send("colour", {{ timer: group.dataset.t, colour: button.dataset.c }});
      }});
    }}
  }}

  wireBuzzer(h, root.querySelector(".buzzer"), window);

  // This panel's own switch: shown at once, and told to the page (the host's is every page's default).
  for (const button of root.querySelectorAll(".modes button")) {{
    button.addEventListener("click", () => {{
      if (h.pageMode === button.dataset.m) return;
      h.pageMode = button.dataset.m;
      drawPanel(h);
      h.send("mode", button.dataset.m);
    }});
  }}

  // A viewer's Day/Night: the browser's alone, so shown at once, with no run of the page.
  for (const button of root.querySelectorAll(".daynight button")) {{
    button.addEventListener("click", () => {{
      h.setDayNight(button.dataset.d);
      drawPanel(h);
    }});
  }}

  const pop = root.querySelector(".pop");
  const api = pipApi();
  if (api !== null) {{
    pop.hidden = false;
    pop.addEventListener("click", async () => {{
      // A second click while the pop-out is coming, or just after it came (a
      // double click), would close it again at once: ignored.
      if (h.opening || performance.now() - h.pipOpenedAt < POP_GUARD_MS) return;
      note.hidden = true;
      if (h.pip !== null) {{
        h.pip.close();
        return;
      }}
      h.opening = true;
      try {{
        // The pop-out if no other tab holds it, whatever the label said a moment ago; else a pop-up.
        if (!(await claim(h, api))) openPopup(h, note);
      }} catch (error) {{
        note.textContent = "The pop-out could not be opened: " + error.message;
        note.hidden = false;
      }} finally {{
        h.opening = false;
      }}
    }});
  }}
}}
"""
)

# The check-in's reading for the clock, from inside the fragment (rebuilt at
# each check-in). It also leaves the pop-out a way to ask for a check-in, and
# asks for the whole page again when the clock has gone from it (_LOST_AFTER).
_SYNC_JS = (
    _HUB_JS
    + f"""
const LOST_AFTER = {_LOST_AFTER};

export default function (component) {{
  const {{ data, setTriggerValue }} = component;
  if (reloading()) return;
  const h = hub();
  h.update(data);
  h.kick = () => setTriggerValue("tick", Date.now());
  h.buzzSend = (on) => setTriggerValue("buzz", {{ on, n: Date.now() }});
  h.missing = h.clock !== null && h.clock.isConnected ? 0 : h.missing + 1;
  if (h.missing >= LOST_AFTER) {{
    h.missing = 0;
    setTriggerValue("lost", Date.now());
  }}
}}
"""
)

# What the browser keeps, read at the start of a visit (the method of
# ratesplot's reader, batch 24.6: through the page's own script, since on
# Streamlit Community Cloud the proxy filters cookies from st.context.cookies).
# The cookies by name ("" for one not set), the tab's own session from its
# sessionStorage ("" for none), and the browser's and the tab's ids, each made
# and kept if there was none. Sent once; the page starts from it on the run
# that follows.
_READER_JS = _RETIRE_JS + """
const randomId = () => (typeof crypto !== "undefined" && crypto.randomUUID
  ? crypto.randomUUID().replace(/-/g, "")
  : Math.random().toString(36).slice(2) + Date.now().toString(36)).slice(0, 24);

export default function (component) {
  if (reloading()) return;  // an earlier version's note: the tab reloads, and reads afresh
  const { data, setTriggerValue } = component;
  const cookies = {};
  for (const name of data.names) {
    let value = "";
    try {
      const kept = document.cookie.split("; ").find((pair) => pair.startsWith(name + "="));
      if (kept !== undefined) value = kept.slice(name.length + 1);
    } catch (error) {
      value = "";
    }
    cookies[name] = value;
  }
  let tab = "";
  try {
    tab = sessionStorage.getItem(data.tab) ?? "";
  } catch (error) {
    tab = "";
  }
  let browser = cookies[data.browser] || "";
  if (!/^[A-Za-z0-9]{8,64}$/.test(browser)) {
    browser = randomId();
    try {
      document.cookie = data.browser + "=" + browser + "; max-age=" + data.seconds + "; path=/; SameSite=Lax"
        + (location.protocol === "https:" ? "; Secure" : "");
    } catch (error) {}  // no cookies for this page: the id lasts this visit
  }
  let tabid = "";
  try {
    tabid = sessionStorage.getItem(data.tabid) ?? "";
    if (!/^[A-Za-z0-9]{8,64}$/.test(tabid)) {
      tabid = randomId();
      sessionStorage.setItem(data.tabid, tabid);
    }
  } catch (error) {
    tabid = "";  // no storage for this page: the server cannot tell its tab
  }
  setTriggerValue("cookies", { cookies, tab, browser, tabid });
}
"""

# A change of what the browser keeps (``_COOKIE_OP``): laid out on every run
# at the page's foot, applied once by each page (the number ``n`` noted on the
# window), so a run that changes nothing writes nothing, and two windows of
# one browser do not undo each other's notes at every run. ``tab`` is the
# tab's session (null: none); ``first`` a session put first in the recent
# list, ``drop`` one taken out of it; ``host``, when given, adds one session's
# token to those kept. Neither list is replaced, only changed, so another
# window's sessions stay in them.
_WRITER_JS = f"""
const RECENT = {json.dumps(RECENT_COOKIE)};
const HOSTS = {json.dumps(HOSTS_COOKIE)};
const LEGACY = {json.dumps(LEGACY_LAST_COOKIE)};
const DAYNIGHT = {json.dumps(DAYNIGHT_COOKIE)};
const TAB = {json.dumps(TAB_KEY)};
const RECENT_KEPT = {_RECENT_KEPT};
const HOSTS_KEPT = {_HOSTS_KEPT};

export default function (component) {{
  const {{ data }} = component;
  const done = (window.speakerTimeCookies ??= {{ n: 0 }});
  if (data.n === 0 || data.n === done.n) return;
  done.n = data.n;
  try {{
    if (data.tab === null) sessionStorage.removeItem(TAB);
    else sessionStorage.setItem(TAB, data.tab);
  }} catch (error) {{}}  // no storage for this page: a reload starts afresh
  try {{
    const read = (name) => {{
      const kept = document.cookie.split("; ").find((pair) => pair.startsWith(name + "="));
      return kept === undefined ? null : kept.slice(name.length + 1);
    }};
    const readJson = (name) => {{
      try {{
        return JSON.parse(decodeURIComponent(read(name) ?? ""));
      }} catch (error) {{
        return null;
      }}
    }};
    const write = (name, value) => {{
      document.cookie = value === null
        ? name + "=; max-age=0; path=/; SameSite=Lax"
        : name + "=" + value + "; max-age=" + data.seconds + "; path=/; SameSite=Lax"
          + (location.protocol === "https:" ? "; Secure" : "");
    }};
    let recent = readJson(RECENT);
    if (!Array.isArray(recent)) recent = [];
    recent = recent.filter((sid) => typeof sid === "string" && sid !== data.first && sid !== data.drop);
    if (data.first !== null) recent.unshift(data.first);
    recent = recent.slice(0, RECENT_KEPT);
    write(RECENT, recent.length ? encodeURIComponent(JSON.stringify(recent)) : null);
    if (read(LEGACY) !== null) write(LEGACY, null);
    const dayNight = readJson(DAYNIGHT);
    if (data.drop !== null && dayNight !== null && typeof dayNight === "object" && data.drop in dayNight) {{
      delete dayNight[data.drop];  // the session forgotten: its Day or Night too
      write(DAYNIGHT, Object.keys(dayNight).length ? encodeURIComponent(JSON.stringify(dayNight)) : null);
    }}
    if (data.host !== null) {{
      let hosts = readJson(HOSTS);
      if (typeof hosts !== "object" || hosts === null || Array.isArray(hosts)) hosts = {{}};
      const [sid, token] = data.host;
      delete hosts[sid];  // and added again, as the most recent
      hosts[sid] = token;
      const sids = Object.keys(hosts);
      while (sids.length > HOSTS_KEPT) delete hosts[sids.shift()];
      write(HOSTS, encodeURIComponent(JSON.stringify(hosts)));
    }}
  }} catch (error) {{}}  // the browser keeps no cookies for this page: nothing to write
}}
"""

# The page's own styles: less space above the first line (a small window
# beside the slides), room below the last for the badges Community Cloud lays
# over the window's lower right corner (46 px high, measured 2026-11-03: at 2rem
# they covered the end of the cookie note on a page fitted to a narrow window),
# and the pending screen's spinner.
_PAGE_STYLE = """
<style>
[data-testid="stMainBlockContainer"] { padding-top: 3.2rem; padding-bottom: 3.2rem; }
/* The page is wide so that the clock can span the window (Terry, 2026-11-03: twice the largest figures); everything
   else keeps the centred column it had (44rem, 704 px), each element where it stood in it. */
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] > :not(.st-key-clock) {
  max-width: 44rem; margin-left: max(0px, calc((100% - 44rem) / 2));
}
/* The page's column as fitted to its window on connecting (_FIT_SHARE), only while the clock is on the page. */
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"]:has(> .st-key-clock) { zoom: var(--speakertime-page, 1); }
.speakertime-wait { display: flex; align-items: center; gap: 0.7em; font-size: 1.25rem; margin: 0.5rem 0 0.25rem; }
.speakertime-spinner {
  width: 1.3em; height: 1.3em; flex: none; border-radius: 50%;
  border: 0.22em solid rgba(128, 128, 128, 0.3); border-top-color: #e2483d;
  animation: speakertime-spin 0.9s linear infinite;
}
@keyframes speakertime-spin { to { transform: rotate(360deg); } }
</style>
"""

_ID_HELP = (
    f"Letters, numbers and the characters {' '.join(ids.ALLOWED_SPECIALS)}, at most {ids.MAX_LENGTH}. "
    "Spaces, tabs and other characters are ignored, and so is case: \"room 5b\" is ROOM5B. "
    "Leave it blank and Create New Session chooses a random number."
)
_LIMIT_HELP = (
    "Minutes, to one decimal place (2.5 is two and a half minutes; more decimals are rounded to the nearest tenth). "
    "Time Remaining is the Time Limit minus Elapsed Time, and goes negative once the limit is passed. "
    "Blank, 0 or a negative number: no limit, and Time Remaining stays at zero."
)
_WARNING_HELP = (
    "Minutes, to one decimal place, or blank for none. Shown beside the Time Limit on every panel and pop-out. "
    "While Time Remaining is this many minutes or fewer, and more than zero, it takes the warning's colour."
)
_WARNING_COLOUR_LABEL = "Warning Colour"
_WARNING_COLOUR_HELP = (
    "The colour Time Remaining takes while it is within the Time Left Warning, on every panel and pop-out. "
    "The first button, A, is green (or, when Time Remaining is in a colour, the colour opposite it); the others are four more."
)

# The warning's colour buttons (``Shown.warning_buttons``), beside its box on the host's page: laid out again at each run.
_PICKER_HTML = '<div class="picks" role="group" aria-label="Colour of the Time Left Warning"></div>'
_PICKER_CSS = """
.picks { display: flex; align-items: center; gap: 7px; height: 2.5rem; }
.picks button { width: 22px; height: 22px; padding: 0; border-radius: 50%; cursor: pointer; background: var(--swatch);
  border: 1px solid #9ca3af; font: 600 11px system-ui, sans-serif; color: #000; }
.picks button.on { outline: 2px solid #31333f; outline-offset: 2px; }
"""
_PICKER_JS = """
export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const row = parentElement.querySelector(".picks");
  row.replaceChildren();
  for (const [name, hex] of data.buttons) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.c = name;
    button.title = name === data.auto ? "A: green, or the colour opposite Time Remaining's" : name[0].toUpperCase() + name.slice(1);
    button.setAttribute("aria-label", button.title);
    button.style.setProperty("--swatch", hex);
    button.classList.toggle("on", name === data.chosen);
    if (name === data.auto) button.textContent = "A";
    button.addEventListener("click", () => { if (name !== data.chosen) setTriggerValue("pick", name); });
    row.appendChild(button);
  }
}
"""

_COOKIE_NOTE = (
    "This page keeps small cookies in your browser: the sessions you were in, the host key of a session you hosted, "
    "your Day or Night for a session, and a random id for this browser, so that a reload or a return reconnects you. "
    "Leave Session forgets the session."
)


# -- the page and the server ---------------------------------------------------

def _page() -> str:
    """This page's id: its Streamlit session's (the one Streamlit's runtime knows it by), "/", and a mark made for the visit.

    The mark is kept in the session's state, so a page that Streamlit
    reconnects keeps its id. It is there because AppTest gives every page
    the same session id ("test session id"), and two test pages must be two.
    """
    ctx = get_script_run_ctx()
    session = ctx.session_id if ctx is not None else "no-session"
    mark = st.session_state.setdefault(_MARK, uuid.uuid4().hex[:12])
    return f"{session}/{mark}"


def _connected(page: str) -> bool | None:
    """Whether page ``page``'s connection is open: Streamlit's runtime knows (a closed tab is told at once); None where there is no runtime to ask."""
    if not Runtime.exists():
        return None
    return Runtime.instance().is_active_session(page.split("/", 1)[0])


def _tokens() -> dict[str, str]:
    return st.session_state.setdefault(_TOKENS, {})


def _browser() -> str | None:
    return st.session_state.get(_BROWSER)


def _tab() -> str | None:
    return st.session_state.get(_TAB)


def _mode() -> str | None:
    return st.session_state.get(_MODE)


def _is_switch(name: str, switch: str) -> bool:
    """Whether a name in a page's address is the switch ``switch``, whatever its case and however many dashes before it."""
    return name.lstrip("-").casefold() == switch


def silent_asked(params: Mapping[str, str]) -> bool:
    """Whether a page's address (its query parameters, ``params``) says it makes no sound: the switch by itself or with any value but a no."""
    return any(_is_switch(name, _SILENT_SWITCH) and value.casefold() not in _SILENT_OFF for name, value in params.items())


def session_asked(params: Mapping[str, str]) -> str | None:
    """The session a page's address (its query parameters, ``params``) connects it to, as written; None for none, or a blank one."""
    return next((value for name, value in params.items() if _is_switch(name, _SESSION_SWITCH) and value.strip()), None)


def _forget_link() -> None:
    """Take the session out of the page's address, the rest of it kept: the page has left it, so a reload is not to go back."""
    for name in list(st.query_params):
        if _is_switch(name, _SESSION_SWITCH):
            del st.query_params[name]


def _notify(kind: str, text: str) -> None:
    st.session_state[_NOTICE] = (kind, text)


def _cookie_change(tab: str | None, first: str | None = None, drop: str | None = None,
                   host: tuple[str, str] | None = None) -> None:
    """Have the writer (``_WRITER_JS``) set the tab's session (None: none), put ``first`` first among the recent sessions, take ``drop`` out of them, and keep a host's token."""
    before = st.session_state.get(_COOKIE_OP, {"n": 0})
    st.session_state[_COOKIE_OP] = {
        "n": before["n"] + 1, "tab": tab, "first": first, "drop": drop, "host": list(host) if host else None,
    }


def _take_host_mode(shown: Shown) -> None:
    """This panel takes the host's way of showing the times (it opens, or a host has connected)."""
    st.session_state[_MODE] = shown.host_mode
    st.session_state[_EPOCH] = shown.host_epoch


def _enter(sid: str, checked: Checked) -> None:
    """Show session ``sid`` in the role ``checked`` gives, and remember it in the browser."""
    state = st.session_state
    state[_SID] = sid
    state[_SCREEN] = checked.role
    state[_BOX] = sid
    state[_ENTERED] = state.get(_ENTERED, 0) + 1
    state.pop(_REJOIN, None)
    _take_host_mode(checked.shown)
    host = None
    if checked.role == HOST:
        token = checked.token or _tokens().get(sid)
        if checked.token:
            _tokens()[sid] = checked.token
        host = (sid, token) if token else None
        state[_LIMIT_BOX] = checked.shown.limit
        state[_WARN_BOX] = checked.shown.warning
    _cookie_change(sid, first=sid, host=host)


def _join(sid: str) -> Checked | None:
    return REGISTRY.join(sid, _page(), _tokens().get(sid), _browser(), _mode(), _tab())


def _check_in(sid: str) -> Checked | None:
    return REGISTRY.check_in(sid, _page(), _tokens().get(sid), _browser(), _mode(), _tab())


# -- what the browser keeps, read at the visit's start ------------------------------

@dataclass
class Heard:
    """What the page's script sent at the visit's start, read and checked."""

    tab: str | None = None
    """The tab's own session (a reload of a tab that was in one)."""
    recent: list[str] = field(default_factory=list)
    """The browser's recent sessions, most recent first."""
    hosts: dict[str, str] = field(default_factory=dict)
    """The host token of each session hosted from this browser."""
    browser: str | None = None
    """The browser's id."""
    tabid: str | None = None
    """The tab's id."""


def _cookies_read() -> None:
    """The page's script has sent what the browser keeps: kept for the visit's start."""
    sent = (st.session_state.get(_READER) or {}).get("cookies")
    st.session_state[_HEARD] = sent if isinstance(sent, dict) else {}


def _session_id(value: object) -> str | None:
    """An ID as kept by the browser, read again as the box reads one, so a note altered by hand cannot name a session no box could."""
    if not isinstance(value, str):
        return None
    sid = ids.normalized(value)
    return sid if sid and not ids.problem(sid) else None


def _json_cookie(text: object) -> object:
    if not isinstance(text, str) or not text:
        return None
    try:
        return json.loads(urllib.parse.unquote(text))
    except ValueError:
        return None


def heard_cookies(sent: object) -> Heard:
    """What the browser keeps, from what its script sent (``_READER_JS``); nothing for anything spoilt."""
    heard = Heard()
    if not isinstance(sent, dict):
        return heard
    cookies = sent.get("cookies") if isinstance(sent.get("cookies"), dict) else {}
    heard.tab = _session_id(sent.get("tab"))
    recent = _json_cookie(cookies.get(RECENT_COOKIE))
    if isinstance(recent, list):
        for value in recent:
            sid = _session_id(value)
            if sid is not None and sid not in heard.recent:
                heard.recent.append(sid)
    legacy = cookies.get(LEGACY_LAST_COOKIE)
    if not heard.recent and isinstance(legacy, str) and legacy:
        sid = _session_id(urllib.parse.unquote(legacy))
        heard.recent = [sid] if sid else []
    hosts = _json_cookie(cookies.get(HOSTS_COOKIE))
    if isinstance(hosts, dict):
        for value, token in hosts.items():
            sid = _session_id(value)
            if sid == value and isinstance(token, str):
                heard.hosts[sid] = token
    browser = sent.get("browser")
    heard.browser = browser if isinstance(browser, str) and _BROWSER_ID.fullmatch(browser) else None
    tabid = sent.get("tabid")
    heard.tabid = tabid if isinstance(tabid, str) and _BROWSER_ID.fullmatch(tabid) else None
    return heard


def _reconnected(sid: str, checked: Checked) -> None:
    _enter(sid, checked)
    as_what = "its host" if checked.role == HOST else "a viewer"
    _notify("success", f"Reconnected to session {sid}, as {as_what}.")


def _start_visit() -> None:
    """The visit's first run after the browser's notes were read: go back to the tab's session, or to the browser's where that leaves no doubt."""
    state = st.session_state
    state[_STARTED] = True
    heard = heard_cookies(state.get(_HEARD))
    _tokens().update(heard.hosts)
    state[_BROWSER] = heard.browser
    state[_TAB] = heard.tabid
    # A link naming a session connects as Connect to Session does: joining it, or waiting for it to be created, as
    # host only if this browser holds its token and no other page hosts it. A reload of a tab already in that session
    # goes on below, and says so.
    link = session_asked(st.query_params)
    if link is not None and ids.normalized(link) != heard.tab:
        state[_BOX] = link[:ids.MAX_TYPED]  # as much as the box takes: a longer ID is refused, as it would be if typed
        _connect()
        return
    if heard.tab is not None:
        # A reload of a tab that was in a session: that session, and no other.
        checked = _join(heard.tab)
        if checked is not None:
            _reconnected(heard.tab, checked)
        elif heard.tab in _tokens():
            state[_BOX] = heard.tab
            _notify("info", f"Session {heard.tab} is not running now. Create New Session starts it again.")
        else:
            state[_SID] = heard.tab
            state[_SCREEN] = PENDING
        return
    # A new window: the browser's recent sessions not open in another of its windows.
    found = REGISTRY.candidates(heard.recent, _page(), heard.browser, _tokens())
    hosting = [sid for sid, as_host in found if as_host]
    pick = hosting[0] if len(hosting) == 1 else found[0][0] if len(found) == 1 else None
    if pick is not None:
        checked = _join(pick)
        if checked is not None:
            _reconnected(pick, checked)
    elif found:
        state[_REJOIN] = found
    elif heard.recent and not REGISTRY.exists(heard.recent[0]):
        state[_BOX] = heard.recent[0]
        _notify("info", f"Your last session, {heard.recent[0]}, is not running now.")


# -- the buttons ------------------------------------------------------------------

def _typed_id() -> tuple[str, str | None]:
    """The ID in the box as read (``ids.normalized``), written back into the box, and why it cannot be used (None if it can)."""
    typed = st.session_state.get(_BOX) or ""
    sid = ids.normalized(typed)
    st.session_state[_BOX] = sid
    if typed.strip() and not sid:
        return sid, f"Nothing in “{typed.strip()}” can be part of a session ID. {_ID_HELP}"
    return sid, ids.problem(sid)


def _create() -> None:
    sid, problem = _typed_id()
    if problem:
        _notify("error", problem)
        return
    try:
        sid, token, notice = REGISTRY.create(sid or None, _page(), _browser(), _mode(), _tab())
    except Refused as refusal:
        _notify("error", str(refusal))
        return
    _tokens()[sid] = token
    st.session_state[_DECLINED] = set()
    checked = _join(sid)
    if checked is not None:
        _enter(sid, checked)
    if notice:
        _notify("info", notice)


def _connect() -> None:
    sid, problem = _typed_id()
    if not sid and not problem:
        problem = "Enter the ID of the session to connect to."
    if problem:
        _notify("error", problem)
        return
    st.session_state[_DECLINED] = set()
    checked = _join(sid)
    if checked is None:
        st.session_state[_SID] = sid
        st.session_state[_SCREEN] = PENDING
        _cookie_change(sid, first=sid)
        return
    _enter(sid, checked)


def _rejoin(sid: str) -> None:
    st.session_state[_BOX] = sid
    _connect()


def _cancel() -> None:
    # The box is put back: Streamlit forgets a widget's value while it is not laid out (the pending screen has none).
    sid = st.session_state.get(_SID) or ""
    st.session_state[_BOX] = sid
    st.session_state[_SCREEN] = START
    _forget_link()
    _cookie_change(None, drop=sid or None)


def _leave() -> None:
    sid = st.session_state.get(_SID)
    if sid:
        REGISTRY.leave(sid, _page())
        st.session_state[_BOX] = sid
        _notify("info", f"You left session {sid}.")
    st.session_state[_SCREEN] = START
    _forget_link()
    _cookie_change(None, drop=sid)


def _ended(sid: str) -> None:
    """Session ``sid`` has gone (removed after hours with nobody in it, or the app restarted): back to the start, its ID in the box."""
    st.session_state[_SCREEN] = START
    st.session_state[_BOX] = sid
    _notify("warning", f"Session {sid} has ended.")
    _cookie_change(None, drop=sid)


def _control(action: str, value: float | None = None) -> None:
    sid = st.session_state.get(_SID)
    try:
        REGISTRY.control(sid, _page(), action, value)
    except Refused as refusal:
        _notify("error", str(refusal))


def _control_sent() -> None:
    """The host has pressed Start, Stop or Reset on its pop-out's or pop-up's line: as the page's own buttons do."""
    sent = _clock_sent("control")
    action = sent.get("action") if isinstance(sent, dict) else None
    if action in _POP_CONTROLS:
        _control(action)


def _limit_changed() -> None:
    limit = limit_minutes(st.session_state.get(_LIMIT_BOX))
    st.session_state[_LIMIT_BOX] = limit  # shown as it is used: to a tenth
    _control("limit", limit)


def _clock_sent(name: str) -> object:
    return (st.session_state.get(_CLOCK) or {}).get(name)


def _mode_changed() -> None:
    """This panel's switch: its own way of showing the times; the host's is also every page's default from now on."""
    mode = _clock_sent("mode")
    if mode not in MODES:
        return  # not one of the switch's two values: the switch did not send it
    st.session_state[_MODE] = mode
    if st.session_state.get(_SCREEN) == HOST:
        REGISTRY.set_host_mode(st.session_state.get(_SID), _page(), mode)


def _size_changed() -> None:
    """The host has let go of a slider: that timer's size, for every panel and pop-out."""
    sent = _clock_sent("size")
    try:
        if not isinstance(sent, dict):
            raise ValueError(f"not a size: {sent!r}")
        REGISTRY.set_size(st.session_state.get(_SID), _page(), sent.get("timer"), sent.get("percent"))
    except Refused as refusal:
        _notify("error", str(refusal))
    except ValueError as problem:
        _notify("error", f"The size was not changed: {problem}.")


def _colour_changed() -> None:
    """The host has pressed a colour button beside a slider: that timer's colour, for every panel and pop-out."""
    sent = _clock_sent("colour")
    try:
        if not isinstance(sent, dict):
            raise ValueError(f"not a colour: {sent!r}")
        REGISTRY.set_colour(st.session_state.get(_SID), _page(), sent.get("timer"), sent.get("colour"))
    except Refused as refusal:
        _notify("error", str(refusal))
    except ValueError as problem:
        _notify("error", f"The colour was not changed: {problem}.")


def _warning_changed() -> None:
    warning = limit_minutes(st.session_state.get(_WARN_BOX))
    st.session_state[_WARN_BOX] = warning  # shown as it is used: to a tenth
    _control("warning", warning)


def _warning_colour_picked() -> None:
    """The host has pressed one of the warning's colour buttons."""
    choice = (st.session_state.get(_PICKER) or {}).get("pick")
    try:
        REGISTRY.set_warning_colour(st.session_state.get(_SID), _page(), choice)
    except Refused as refusal:
        _notify("error", str(refusal))
    except ValueError as problem:
        _notify("error", f"The warning's colour was not changed: {problem}.")


def _assume_host() -> None:
    sid = st.session_state.get(_SID)
    try:
        token = REGISTRY.assume_host(sid, _page(), _mode(), _tab())
    except Refused as refusal:
        _notify("error", str(refusal))
        return
    _tokens()[sid] = token
    checked = _join(sid)
    if checked is not None:
        _enter(sid, checked)
        _notify("success", f"You are now the host of session {sid}.")


def _decline_host() -> None:
    st.session_state.setdefault(_DECLINED, set()).add(st.session_state.get(_SID))


def _ticked() -> None:
    """The pop-out asked for a check-in: the fragment's run is the check-in."""


def _buzz_sent() -> None:
    """The host's Buzzer held or let go (sent through the check-in's element, ``_SYNC_JS``): for every page of the session."""
    sent = (st.session_state.get(_SYNC) or {}).get("buzz")
    if not isinstance(sent, dict):
        return
    try:
        REGISTRY.buzz(st.session_state.get(_SID), _page(), sent.get("on") is True)
    except Refused:
        pass  # no longer the host: the check-in that follows says so


def _clock_lost() -> None:
    """The check-in's script has found the clock gone from the page (``_LOST_AFTER``): the fragment's run lays the whole page out again."""
    st.session_state[_LOST] = True


# -- the screens ------------------------------------------------------------------

def _start_screen() -> None:
    st.text_input(
        "Session ID", key=_BOX, max_chars=ids.MAX_TYPED, help=_ID_HELP,
        placeholder="Blank: Create New Session chooses one",
    )
    row = st.container(horizontal=True)
    row.button("Create New Session", on_click=_create, type="primary")
    row.button("Connect to Session", on_click=_connect)
    rejoin = st.session_state.get(_REJOIN)
    if rejoin:
        st.caption("Sessions of yours still running, and not open in another window of this browser:")
        row = st.container(horizontal=True)
        for sid, as_host in rejoin:
            row.button(f"Rejoin {sid}" + (" as host" if as_host else ""), key=f"rejoin:{sid}", on_click=_rejoin, args=(sid,))


def _pending_screen(sid: str) -> None:
    st.markdown(
        '<div class="speakertime-wait"><span class="speakertime-spinner"></span>'
        "<span>Please wait, connection pending</span></div>",
        unsafe_allow_html=True,
    )
    st.caption(f"Waiting for session {sid} to be created. This page connects to it by itself as soon as it is.")
    st.button("Cancel", on_click=_cancel)
    st.fragment(_wait_for, run_every=datetime.timedelta(seconds=CHECK_IN_S))(sid)


def _wait_for(sid: str) -> None:
    """The pending screen's check, once a second: join the session once it has been created."""
    if st.session_state.get(_SCREEN) != PENDING or st.session_state.get(_SID) != sid:
        st.rerun(scope="app")
    checked = _join(sid)
    if checked is not None:
        _enter(sid, checked)
        _notify("success", f"Connected to session {sid}.")
        st.rerun(scope="app")


def _session_screen(sid: str, checked: Checked) -> None:
    role = checked.role
    who = "You are the host." if role == HOST else "You are viewing it."
    silent = silent_asked(st.query_params)
    st.markdown(f"Session ID: **{_markdown_text(sid)}**   {who}" + (f" {_SILENT_TEXT}" if silent else ""))
    st.fragment(_live, run_every=datetime.timedelta(seconds=CHECK_IN_S))(sid, role)
    clock = st.components.v2.component("speakertime_clock", html=_CLOCK_HTML, css=_CLOCK_CSS, js=_CLOCK_JS)
    data = {"sid": sid, **checked.shown.for_script(), "mode": _mode() or checked.shown.host_mode, "host": role == HOST,
            "silent": silent, "entered": st.session_state.get(_ENTERED, 0)}
    clock(key=_CLOCK, data=data, on_mode_change=_mode_changed, on_size_change=_size_changed, on_colour_change=_colour_changed,
          on_control_change=_control_sent)
    if role == HOST:
        _host_controls(checked.shown)
    else:
        st.button(_LEAVE_LABEL, on_click=_leave)


def _host_controls(shown: Shown) -> None:
    """The Time Limit, and to its right the Time Left Warning and its colour buttons (Terry, 2026-11-01); Start, Stop and Reset, with Copy Link and Leave Session at the row's right (Terry, 2026-11-04)."""
    st.session_state.setdefault(_LIMIT_BOX, shown.limit)
    st.session_state.setdefault(_WARN_BOX, shown.warning)
    row = st.container(horizontal=True, vertical_alignment="bottom", wrap=True)
    row.number_input(
        "Time Limit (minutes)", key=_LIMIT_BOX, min_value=None, step=1.0, format="%.1f",
        help=_LIMIT_HELP, placeholder="Blank: no limit", on_change=_limit_changed, width=200,
    )
    row.number_input(
        "Time Left Warning (minutes)", key=_WARN_BOX, min_value=None, step=1.0, format="%.1f",
        help=_WARNING_HELP, placeholder="Blank: none", on_change=_warning_changed, width=220,
    )
    buttons, chosen = shown.warning_buttons()
    picker = st.components.v2.component("speakertime_warn_colours", html=_PICKER_HTML, css=_PICKER_CSS, js=_PICKER_JS)
    # The label is Streamlit's own, for its "?" like the boxes' beside it (Terry, 2026-11-04); at their labels' size (:small).
    with row.container(gap=None, key=_PICKER_BOX):
        st.markdown(f":small[{_WARNING_COLOUR_LABEL}]", help=_WARNING_COLOUR_HELP)
        picker(key=_PICKER, data={"buttons": buttons, "chosen": chosen, "auto": AUTO}, on_pick_change=_warning_colour_picked)
    row = st.container(horizontal=True)
    # A button that would do nothing is greyed (ratesplot's rule: grey what has no effect). Start reads Restart once the
    # timer has run, greyed or not (Terry, 2026-11-03), and Start again after Reset.
    start = _RESTART if shown.running or shown.elapsed > 0 else _START
    row.button(start, key=_START_BUTTON, on_click=_control, args=("start",), disabled=shown.running, type="primary")
    row.button("Stop", on_click=_control, args=("stop",), disabled=not shown.running)
    row.button("Reset", on_click=_control, args=("reset",), disabled=not shown.running and shown.elapsed == 0)
    row.space("stretch")
    # Copy Link's work is in the browser, where the click may reach the clipboard (_CLOCK_JS, copyLink); its run does nothing.
    row.button(_COPY_LABEL, key=_COPY_BUTTON)
    row.button(_LEAVE_LABEL, on_click=_leave)


def _live(sid: str, role: str) -> None:
    """The session's check-in, once a second (a fragment): the clock's reading, the host's role offered or lost, the viewers counted.

    Laid out with the page as well, where it agrees with the page's own
    check-in. On its own runs, a change of role, a host newly connected (the
    panel takes the host's way of showing the times) or a session ended lays
    the whole page out again.
    """
    state = st.session_state
    if state.get(_SCREEN) != role or state.get(_SID) != sid or state.pop(_LOST, False):
        # A button in this fragment (Yes) has changed the screen, or the clock
        # has gone from the page: the click reran the fragment alone, so the
        # page is laid out again here.
        st.rerun(scope="app")
    checked = _check_in(sid)
    if checked is None:
        _ended(sid)
        st.rerun(scope="app")
    if checked.role != role:
        _enter(sid, checked)
        if checked.role == HOST:
            _notify("success", f"You are the host of session {sid} again.")
        else:
            _notify("warning", f"Someone else became the host of session {sid} while you were away. You are now viewing it.")
        st.rerun(scope="app")
    if checked.shown.host_epoch != state.get(_EPOCH):
        _take_host_mode(checked.shown)
        st.rerun(scope="app")
    # "beat" differs at every check-in: an element whose data is the same as
    # the last run's is not run again in the browser (measured: a stopped
    # timer's check-ins reached the script only with it), and the pop-out
    # must see every check-in arrive to know when to ask for one.
    state[_BEAT] = state.get(_BEAT, 0) + 1
    sync = st.components.v2.component("speakertime_sync", js=_SYNC_JS)
    sync(key=_SYNC, data={"sid": sid, "beat": state[_BEAT], **checked.shown.for_script()}, on_tick_change=_ticked,
         on_lost_change=_clock_lost, on_buzz_change=_buzz_sent)
    shown = checked.shown
    if role == HOST:
        viewers = "1 viewer" if shown.viewers == 1 else f"{shown.viewers} viewers"
        st.caption(f"{viewers} connected.")
    elif shown.hosted:
        st.caption("The host is connected.")
    else:
        declined = sid in state.get(_DECLINED, set())
        st.warning(
            "This session has no host connected. Its timers keep running. **Assume Host Role?**"
            + ("  \nYou answered No; you can still say Yes until a host connects." if declined else ""),
        )
        row = st.container(horizontal=True)
        row.button("Yes", key="assume_yes", on_click=_assume_host, type="primary")
        row.button("No", key="assume_no", on_click=_decline_host, disabled=declined)


def _markdown_text(text: str) -> str:
    """``text`` shown as it is in Markdown: an ID may hold "#", "_", "&", "+" and ".", which Markdown can take for marks."""
    return "".join("\\" + c if c in "\\`*_{}[]()#+-.!&<>|$~" else c for c in text)


def _show_notice() -> None:
    notice = st.session_state.pop(_NOTICE, None)
    if notice is not None:
        kind, text = notice
        getattr(st, kind)(text)


def _write_cookies() -> None:
    writer = st.components.v2.component("speakertime_cookies", js=_WRITER_JS)
    op = st.session_state.get(_COOKIE_OP, {"n": 0, "tab": None, "first": None, "drop": None, "host": None})
    writer(key="cookies", data={**op, "seconds": _COOKIE_DAYS * 86400})


def _use_remote() -> None:
    """Keep the remembered settings in a gist as well, once the app has a token for it (v1.9, ``store``).

    The token is the secret TOKEN_NAME (the app's settings on Community
    Cloud), or the environment variable of that name; with neither, the
    settings are kept in the file alone.
    """
    if REGISTRY.has_remote():
        return
    token = os.environ.get(TOKEN_NAME)
    if not token:
        try:
            token = st.secrets.get(TOKEN_NAME)
        except Exception:  # noqa: BLE001 - no secrets at all (locally, the checks): Streamlit raises its own error
            token = None
    if isinstance(token, str) and token.strip():
        REGISTRY.use_remote(GistStore(token.strip()))


def main() -> None:
    """The page: one run of it (Streamlit runs it again after every change)."""
    st.set_page_config(page_title=PAGE_TITLE, page_icon="⏱️", layout="wide")  # the clock spans the window (_PAGE_STYLE)
    st.html(_PAGE_STYLE)
    REGISTRY.set_connected(_connected)
    _use_remote()
    state = st.session_state
    title = st.container(horizontal=True, vertical_alignment="bottom")
    title.markdown(f"### ⏱️ {PAGE_TITLE}")
    title.caption(version_text())

    if _HEARD not in state:
        # Components are registered on every run: registration belongs to
        # Streamlit's runtime, and AppTest starts one per test (ratesplot).
        reader = st.components.v2.component("speakertime_reader", js=_READER_JS)
        reader(
            key=_READER,
            data={"names": [RECENT_COOKIE, HOSTS_COOKIE, BROWSER_COOKIE, LEGACY_LAST_COOKIE], "tab": TAB_KEY,
                  "tabid": TAB_ID_KEY, "browser": BROWSER_COOKIE, "seconds": _BROWSER_DAYS * 86400},
            on_cookies_change=_cookies_read,
        )
        st.caption("Starting …")
        return
    if not state.get(_STARTED):
        _start_visit()

    screen = state.get(_SCREEN, START)
    sid = state.get(_SID)
    checked = None
    if screen in (HOST, VIEWER):
        checked = _check_in(sid)
        if checked is None:
            _ended(sid)
            screen = START
        elif checked.role != screen:
            _enter(sid, checked)
            screen = checked.role
        elif checked.shown.host_epoch != state.get(_EPOCH):
            _take_host_mode(checked.shown)

    _show_notice()
    if screen == PENDING:
        _pending_screen(sid)
    elif checked is not None:
        _session_screen(sid, checked)
    else:
        _start_screen()
    _write_cookies()
    st.caption(_COOKIE_NOTE)
