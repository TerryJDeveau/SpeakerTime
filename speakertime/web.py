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

Sizes and the way the times are shown (Terry, 2026-11-01)
---------------------------------------------------------
The host's slider beside each timer sets that timer's size on every panel and
pop-out of the session (``sessions.Registry.set_size``); it arrives with the
check-ins. Each panel and each pop-out has its own switch between H:MM:SS and
minutes to a tenth (``timer.CLOCK``, ``timer.MINUTES``), independent of every
other. A panel starts at the host's choice when it opens, and takes the
host's choice again whenever a host connects (whichever is the later); a
pop-out starts at its panel's choice when it opens.

What the browser keeps
----------------------
Two cookies and one note per tab:
- ``speakertime_recent``: the sessions this browser was in, most recent first;
- ``speakertime_hosts``: the host's token of each session hosted from it (the
  last ``_HOSTS_KEPT``);
- ``speakertime_browser``: a random id, so that the server can tell this
  browser's own windows from others' (``sessions.Registry.candidates``);
- the tab's own session, in its ``sessionStorage`` (``speakertime_tab``):
  kept through a reload of that tab, and by no other tab.
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
import json
import re
import urllib.parse
import uuid
from dataclasses import dataclass, field

import streamlit as st
from streamlit.runtime import Runtime
from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx

from . import ids
from .sessions import HOST, REGISTRY, SIZE_DEFAULT, SIZE_MAX, SIZE_MIN, SIZE_STEP, SIZED, VIEWER, Checked, Refused, Shown
from .shared import version_text
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
LEGACY_LAST_COOKIE = "speakertime_last"
"""Until 2026-11-01 the one session remembered; read once as the recent list, then removed."""
TAB_KEY = "speakertime_tab"
"""The tab's own session, in its sessionStorage."""

_BROWSER_ID = re.compile(r"[A-Za-z0-9]{8,64}")
"""A browser id as the page's script makes one (``_READER_JS``); anything else in the cookie is ignored."""

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
_TOKENS = "tokens"        # the host's token of each session this browser hosted: {sid: token}
_BROWSER = "browser"      # this browser's id (None if its script could not say)
_REJOIN = "rejoin"        # the sessions a new window could go back to, when it cannot tell which: [(sid, as host)]
_NOTICE = "notice"        # a message shown once: (kind, text), kind one of st's "success", "info", "warning", "error"
_DECLINED = "declined"    # the sessions whose "Assume Host Role?" this page answered No
_COOKIE_OP = "cookie_op"  # the browser's notes' last change, for the writer (_WRITER_JS)
_BEAT = "beat"            # the check-ins counted, so that each one reaches the page's script
_MARK = "mark"            # the page's own part of its id (_page)
_MODE = "mode"            # this panel's way of showing the times (timer.MODES); None before it has shown a session
_EPOCH = "epoch"          # the session's host_epoch when this panel last took the host's way
_CLOCK = "clock"          # the clock element: its triggers, "mode" (the switch) and "size" (a slider)

# The clock's look. A dark panel with light figures reads well from a lectern
# and in a small window beside the slides. Remaining turns red once the limit
# is passed (negative). The figures are sized by their column's width (cqw: a
# hundredth of it) times the host's size, and never wider than the column.
_CLOCK_COLOURS = {"panel": "#111418", "figures": "#f4f6f8", "labels": "#aab2bd", "over": "#ff5a4f", "slider": "#e2483d"}

_CHAR_EM = 0.62
"""The width of a character of the figures, in em, a little over a digit's (0.55 to 0.6 in the system fonts, tabular): the figures shrink to fit their column."""

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

_MODE_NAMES = {CLOCK: "H:MM:SS", MINUTES: "MMM.M"}
"""The switch's two buttons (Terry's names for the two ways)."""

_LABELS = {
    CLOCK: {"elapsed": "Elapsed Time", "remaining": "Time Remaining", "limit": "Time Limit"},
    MINUTES: {"elapsed": "Elapsed Time (min)", "remaining": "Time Remaining (min)", "limit": "Time Limit (min)"},
}

_MODES_HTML = '<div class="modes" role="group" aria-label="Show the times as">' + "".join(
    f'<button type="button" data-m="{mode}">{name}</button>' for mode, name in _MODE_NAMES.items()
) + "</div>"


def _row(name: str, slider: bool) -> str:
    """One row of the clock: its label and figures, and (on the host's panel) the slider for its size."""
    kind = "limit" if name == "limit" else "timer"
    size = (
        f'<input class="size" type="range" data-t="{name}" min="{SIZE_MIN}" max="{SIZE_MAX}" step="{SIZE_STEP}"'
        f' aria-label="Size of {_LABELS[CLOCK][name]} for everyone" hidden>'
        if slider else ""
    )
    return (
        f'<div class="row {kind}"><div class="cell"><span class="label" data-l="{name}"></span>'
        f'<span class="value" data-v="{name}"></span></div>{size}</div>'
    )


_PIP_HTML = "".join(_row(name, False) for name in ("elapsed", "remaining", "limit")) + _MODES_HTML

_PIP_CSS = f"""
  html, body {{ margin: 0; height: 100%; background: {_CLOCK_COLOURS["panel"]}; color: {_CLOCK_COLOURS["figures"]};
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
  body {{ display: flex; flex-direction: column; justify-content: center; gap: 1vh; overflow: hidden; }}
  .cell {{ display: flex; flex-direction: column; align-items: center; line-height: 1; }}
  .label {{ font-size: min(5vw, 6vh); color: {_CLOCK_COLOURS["labels"]}; margin-bottom: 0.6vh; }}
  .value {{ --scale: 1; --chars: 4; font-weight: 700; font-variant-numeric: tabular-nums; white-space: nowrap;
    font-size: min(calc(var(--scale) * min(19vw, 27vh)), calc(96vw / (var(--chars) * {_CHAR_EM}))); }}
  .limit .value {{ font-size: min(7vw, 9vh); font-weight: 600; }}
  .over {{ color: {_CLOCK_COLOURS["over"]}; }}
  .modes {{ align-self: center; display: inline-flex; border: 1px solid {_CLOCK_COLOURS["labels"]}; border-radius: 6px;
    overflow: hidden; margin-top: 0.5vh; }}
  .modes button {{ font: inherit; font-size: max(10px, min(3.4vw, 4.2vh)); color: {_CLOCK_COLOURS["labels"]};
    background: transparent; border: 0; padding: 0.3vh 1.6vw; cursor: pointer; }}
  .modes button.on {{ background: {_CLOCK_COLOURS["figures"]}; color: {_CLOCK_COLOURS["panel"]}; }}
"""

_HUB_JS = f"""
const DRAW_MS = {_DRAW_MS};
const RESYNC_S = {_RESYNC_S};
const KICK_AFTER_MS = {_KICK_AFTER_MS};
const CLOCK = {json.dumps(CLOCK)};
const MINUTES = {json.dumps(MINUTES)};
const SIZE_DEFAULT = {SIZE_DEFAULT};
const LABELS = {json.dumps(_LABELS)};
const PIP_HTML = {json.dumps(_PIP_HTML)};
const PIP_CSS = {json.dumps(_PIP_CSS)};

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
// that is rebuilt (each of the page's runs) finds the one before's.
const hub = () => (window.speakerTime ??= {{
  base: null,       // the last reading taken: {{sid, elapsed, running, limit, version, sizes, at}}, at from performance.now()
  lastSync: 0,      // when a check-in last arrived
  lastKick: 0,      // when the pop-out last asked for one
  kick: null,       // asks for a check-in (the latest sync element's trigger)
  pip: null,        // the pop-out window, while open
  pageMode: CLOCK,  // the panel's way of showing the times (the page's own switch)
  pipMode: CLOCK,   // the pop-out's (its own switch; the panel's when it opened)
  preview: {{}},     // the host's slider while dragged, before the size comes back from the server

  // A check-in's reading (or the clock's own, as it is laid out).
  update(reading) {{
    const at = performance.now();
    const b = this.base;
    if (b !== null && b.sid === reading.sid && b.version === reading.version) {{
      const predicted = b.elapsed + (b.running ? (at - b.at) / 1000 : 0);
      if (Math.abs(predicted - reading.elapsed) <= RESYNC_S) {{
        this.lastSync = at;
        return;
      }}
    }}
    this.base = {{ sid: reading.sid, elapsed: reading.elapsed, running: reading.running, limit: reading.limit,
                  version: reading.version, sizes: reading.sizes ?? {{}}, at }};
    this.lastSync = at;
  }},

  // The three texts now, as timer.elapsed_shown and remaining_shown (CLOCK),
  // or elapsed_tenths and remaining_tenths (MINUTES). The limit is a whole
  // number of tenths of a minute (6 s each), so the two still add up to it.
  texts(mode) {{
    const b = this.base;
    if (b === null) return null;
    const elapsed = b.elapsed + (b.running ? (performance.now() - b.at) / 1000 : 0);
    const shown = Math.max(0, Math.floor(elapsed));
    if (mode === MINUTES) {{
      const tenths = Math.floor(shown / 6);
      const limit = b.limit === null ? null : Math.round(b.limit / 6);
      const remaining = limit === null ? 0 : limit - tenths;
      return {{ elapsed: minutes(tenths), remaining: minutes(remaining), over: remaining < 0,
               limit: limit === null ? "none" : minutes(limit) }};
    }}
    const remaining = b.limit === null ? 0 : b.limit - shown;
    return {{ elapsed: clock(shown), remaining: clock(remaining), over: remaining < 0,
             limit: b.limit === null ? "none" : clock(b.limit) }};
  }},

  // Draw the texts, labels and sizes into a panel or the pop-out.
  draw(root, mode) {{
    const t = this.texts(mode);
    if (t === null) return;
    for (const name of ["elapsed", "remaining", "limit"]) {{
      const value = root.querySelector('[data-v="' + name + '"]');
      const label = root.querySelector('[data-l="' + name + '"]');
      if (label !== null && label.textContent !== LABELS[mode][name]) label.textContent = LABELS[mode][name];
      if (value === null) continue;
      if (value.textContent !== t[name]) {{
        value.textContent = t[name];
        value.style.setProperty("--chars", String(Math.max(4, t[name].length)));
      }}
      if (name !== "limit") {{
        const scale = String((this.preview[name] ?? this.base.sizes[name] ?? SIZE_DEFAULT) / 100);
        if (value.style.getPropertyValue("--scale") !== scale) value.style.setProperty("--scale", scale);
      }}
      if (name === "remaining") value.classList.toggle("over", t.over);
    }}
    for (const button of root.querySelectorAll(".modes button")) button.classList.toggle("on", button.dataset.m === mode);
  }},

  // The pop-out: drawn and timed from its own window, which stays on top, with
  // its own switch, starting at the panel's choice.
  openPip(win) {{
    const h = this;
    h.pipMode = h.pageMode;
    win.document.title = "SpeakerTime";
    const style = win.document.createElement("style");
    style.textContent = PIP_CSS;
    win.document.head.appendChild(style);
    win.document.body.innerHTML = PIP_HTML;
    for (const button of win.document.querySelectorAll(".modes button")) {{
      button.addEventListener("click", () => {{
        h.pipMode = button.dataset.m;
        h.draw(win.document, h.pipMode);
      }});
    }}
    const tick = () => {{
      h.draw(win.document, h.pipMode);
      const now = performance.now();
      if (h.kick !== null && now - h.lastSync > KICK_AFTER_MS && now - h.lastKick > KICK_AFTER_MS) {{
        h.lastKick = now;
        try {{ h.kick(); }} catch (error) {{}}  // the element that gave it is gone: the next one gives another
      }}
    }};
    tick();
    win.setInterval(tick, DRAW_MS);
    win.addEventListener("pagehide", () => {{ if (h.pip === win) h.pip = null; }});
    h.pip = win;
  }},
}});
"""

_CLOCK_HTML = (
    '<div class="clock">'
    + "".join(_row(name, name in SIZED) for name in ("elapsed", "remaining", "limit"))
    + f'<div class="tools">{_MODES_HTML}<button class="pop" type="button" hidden></button></div>'
    + '<div class="note" hidden></div>'
    + "</div>"
)

_CLOCK_CSS = f"""
.clock {{
  background: {_CLOCK_COLOURS["panel"]}; color: {_CLOCK_COLOURS["figures"]};
  border-radius: 14px; padding: 14px 8px 12px;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}}
.row {{ display: grid; grid-template-columns: minmax(0, 1fr); margin-bottom: 10px; }}
.clock.host .row {{ grid-template-columns: 30px minmax(0, 1fr) 30px; }}
.cell {{ grid-column: 1; container-type: inline-size; min-width: 0;
  display: flex; flex-direction: column; align-items: center; line-height: 1; }}
.clock.host .cell {{ grid-column: 2; }}
.size {{ grid-column: 3; justify-self: center; align-self: stretch; margin: 0; width: 22px; min-height: 64px;
  writing-mode: vertical-lr; direction: rtl; accent-color: {_CLOCK_COLOURS["slider"]}; cursor: ns-resize; }}
.size[hidden] {{ display: none; }}
.label {{ font-size: clamp(14px, 4.6cqw, 24px); color: {_CLOCK_COLOURS["labels"]}; margin-bottom: 4px; }}
.value {{ --scale: 1; --chars: 4; font-weight: 700; font-variant-numeric: tabular-nums; white-space: nowrap;
  font-size: min(calc(var(--scale) * clamp(40px, 21cqw, 150px)), calc(100cqw / (var(--chars) * {_CHAR_EM}))); }}
.limit .value {{ font-size: clamp(22px, 7.6cqw, 54px); font-weight: 600; }}
.over {{ color: {_CLOCK_COLOURS["over"]}; }}
.tools {{ display: flex; justify-content: center; align-items: center; flex-wrap: wrap; gap: 8px 12px; margin-top: 4px; }}
.modes {{ display: inline-flex; border: 1px solid {_CLOCK_COLOURS["labels"]}; border-radius: 8px; overflow: hidden; }}
.modes button {{ font: inherit; font-size: 14px; color: {_CLOCK_COLOURS["labels"]}; background: transparent; border: 0;
  padding: 4px 10px; cursor: pointer; }}
.modes button.on {{ background: {_CLOCK_COLOURS["figures"]}; color: {_CLOCK_COLOURS["panel"]}; }}
.pop {{
  font: inherit; font-size: 14px; color: {_CLOCK_COLOURS["figures"]}; background: transparent;
  border: 1px solid {_CLOCK_COLOURS["labels"]}; border-radius: 8px; padding: 4px 12px; cursor: pointer;
}}
.pop:hover {{ border-color: {_CLOCK_COLOURS["figures"]}; }}
.note {{ font-size: 13px; color: {_CLOCK_COLOURS["labels"]}; text-align: center; margin-top: 6px; }}
"""

_POP_OPEN = "Pop out (stays on top)"
_POP_CLOSE = "Close the pop-out"

_CLOCK_JS = (
    _HUB_JS
    + f"""
const POP_OPEN = {json.dumps(_POP_OPEN)};
const POP_CLOSE = {json.dumps(_POP_CLOSE)};

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

export default function (component) {{
  const {{ data, parentElement, setTriggerValue }} = component;
  const h = hub();
  h.update(data);
  h.pageMode = data.mode;
  h.preview = {{}};  // the sizes laid out with this element are the server's
  const root = parentElement.querySelector(".clock");
  root.classList.toggle("host", data.host);
  const pop = root.querySelector(".pop");
  const note = root.querySelector(".note");
  const draw = () => {{
    h.draw(root, h.pageMode);
    const label = h.pip !== null ? POP_CLOSE : POP_OPEN;
    if (pop.textContent !== label) pop.textContent = label;
  }};

  // The host's sliders: shown at once while dragged, sent when let go.
  if (data.host) {{
    for (const slider of root.querySelectorAll("input.size")) {{
      const name = slider.dataset.t;
      slider.value = String(data.sizes[name]);
      slider.hidden = false;
      slider.addEventListener("input", () => {{
        h.preview[name] = Number(slider.value);
        draw();
      }});
      slider.addEventListener("change", () => {{
        h.preview[name] = Number(slider.value);
        setTriggerValue("size", {{ timer: name, percent: Number(slider.value) }});
      }});
    }}
  }}

  // This panel's own switch: shown at once, and told to the page (the host's is every page's default).
  for (const button of root.querySelectorAll(".modes button")) {{
    button.addEventListener("click", () => {{
      if (h.pageMode === button.dataset.m) return;
      h.pageMode = button.dataset.m;
      draw();
      setTriggerValue("mode", button.dataset.m);
    }});
  }}

  const api = pipApi();
  if (api !== null) {{
    pop.hidden = false;
    pop.addEventListener("click", async () => {{
      note.hidden = true;
      if (h.pip !== null) {{
        h.pip.close();
        return;
      }}
      try {{
        h.openPip(await api.requestWindow({{ width: 340, height: 280 }}));
      }} catch (error) {{
        note.textContent = "The pop-out could not be opened: " + error.message;
        note.hidden = false;
      }}
    }});
  }}
  draw();
  const timer = setInterval(draw, DRAW_MS);
  return () => clearInterval(timer);
}}
"""
)

# The check-in's reading for the clock, from inside the fragment (rebuilt at
# each check-in). It also leaves the pop-out a way to ask for a check-in.
_SYNC_JS = (
    _HUB_JS
    + """
export default function (component) {
  const { data, setTriggerValue } = component;
  const h = hub();
  h.update(data);
  h.kick = () => setTriggerValue("tick", Date.now());
}
"""
)

# What the browser keeps, read at the start of a visit (the method of
# ratesplot's reader, batch 24.6: through the page's own script, since on
# Streamlit Community Cloud the proxy filters cookies from st.context.cookies).
# The cookies by name ("" for one not set), the tab's own session from its
# sessionStorage ("" for none), and the browser's id, made and kept if the
# browser had none. Sent once; the page starts from it on the run that follows.
_READER_JS = """
export default function (component) {
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
    const random = typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID().replace(/-/g, "")
      : Math.random().toString(36).slice(2) + Date.now().toString(36);
    browser = random.slice(0, 24);
    try {
      document.cookie = data.browser + "=" + browser + "; max-age=" + data.seconds + "; path=/; SameSite=Lax"
        + (location.protocol === "https:" ? "; Secure" : "");
    } catch (error) {}  // no cookies for this page: the id lasts this visit
  }
  setTriggerValue("cookies", { cookies, tab, browser });
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
# beside the slides), and the pending screen's spinner.
_PAGE_STYLE = """
<style>
[data-testid="stMainBlockContainer"] { padding-top: 3.2rem; padding-bottom: 2rem; }
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
_COOKIE_NOTE = (
    "This page keeps small cookies in your browser: the sessions you were in, the host key of a session you hosted, "
    "and a random id for this browser, so that a reload or a return reconnects you. Leave Session forgets the session."
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


def _mode() -> str | None:
    return st.session_state.get(_MODE)


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
    state.pop(_REJOIN, None)
    _take_host_mode(checked.shown)
    host = None
    if checked.role == HOST:
        token = checked.token or _tokens().get(sid)
        if checked.token:
            _tokens()[sid] = checked.token
        host = (sid, token) if token else None
        state[_LIMIT_BOX] = checked.shown.limit
    _cookie_change(sid, first=sid, host=host)


def _join(sid: str) -> Checked | None:
    return REGISTRY.join(sid, _page(), _tokens().get(sid), _browser(), _mode())


def _check_in(sid: str) -> Checked | None:
    return REGISTRY.check_in(sid, _page(), _tokens().get(sid), _browser(), _mode())


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
        sid, token, notice = REGISTRY.create(sid or None, _page(), _browser(), _mode())
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
    _cookie_change(None, drop=sid or None)


def _leave() -> None:
    sid = st.session_state.get(_SID)
    if sid:
        REGISTRY.leave(sid, _page())
        st.session_state[_BOX] = sid
        _notify("info", f"You left session {sid}.")
    st.session_state[_SCREEN] = START
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


def _assume_host() -> None:
    sid = st.session_state.get(_SID)
    try:
        token = REGISTRY.assume_host(sid, _page(), _mode())
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
    st.markdown(f"Session ID: **{_markdown_text(sid)}**   {who}")
    st.fragment(_live, run_every=datetime.timedelta(seconds=CHECK_IN_S))(sid, role)
    clock = st.components.v2.component("speakertime_clock", html=_CLOCK_HTML, css=_CLOCK_CSS, js=_CLOCK_JS)
    data = {"sid": sid, **checked.shown.for_script(), "mode": _mode() or checked.shown.host_mode, "host": role == HOST}
    clock(key=_CLOCK, data=data, on_mode_change=_mode_changed, on_size_change=_size_changed)
    if role == HOST:
        _host_controls(checked.shown)
    st.button("Leave Session", on_click=_leave)


def _host_controls(shown: Shown) -> None:
    st.session_state.setdefault(_LIMIT_BOX, shown.limit)
    st.number_input(
        "Time Limit (minutes)", key=_LIMIT_BOX, min_value=None, step=1.0, format="%.1f",
        help=_LIMIT_HELP, placeholder="Blank: no limit", on_change=_limit_changed,
    )
    row = st.container(horizontal=True)
    # A button that would do nothing is greyed (ratesplot's rule: grey what has no effect).
    row.button("Start", on_click=_control, args=("start",), disabled=shown.running, type="primary")
    row.button("Stop", on_click=_control, args=("stop",), disabled=not shown.running)
    row.button("Reset", on_click=_control, args=("reset",), disabled=not shown.running and shown.elapsed == 0)


def _live(sid: str, role: str) -> None:
    """The session's check-in, once a second (a fragment): the clock's reading, the host's role offered or lost, the viewers counted.

    Laid out with the page as well, where it agrees with the page's own
    check-in. On its own runs, a change of role, a host newly connected (the
    panel takes the host's way of showing the times) or a session ended lays
    the whole page out again.
    """
    state = st.session_state
    if state.get(_SCREEN) != role or state.get(_SID) != sid:
        # A button in this fragment (Yes) has changed the screen: its click
        # reran the fragment alone, so the page is laid out again here.
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
    sync(key="sync", data={"sid": sid, "beat": state[_BEAT], **checked.shown.for_script()}, on_tick_change=_ticked)
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


def main() -> None:
    """The page: one run of it (Streamlit runs it again after every change)."""
    st.set_page_config(page_title=PAGE_TITLE, page_icon="⏱️", layout="centered")
    st.html(_PAGE_STYLE)
    REGISTRY.set_connected(_connected)
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
                  "browser": BROWSER_COOKIE, "seconds": _BROWSER_DAYS * 86400},
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
