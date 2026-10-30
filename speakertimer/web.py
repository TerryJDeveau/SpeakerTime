"""The web page (Streamlit): ``streamlit run streamlit_app.py``.

Four screens, one at a time:
- start: the Session ID box, Create New Session and Connect to Session;
- pending: "Please wait, connection pending", with a spinner and Cancel, while
  the session connected to has not been created yet (the page joins it by
  itself once it is);
- host: the two timers and the Time Limit, with Start, Stop and Reset;
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

What the browser keeps (two cookies)
------------------------------------
``speakertimer_last`` holds the last session connected to, and
``speakertimer_hosts`` the host's token of each session hosted from this
browser (the last ``_HOSTS_KEPT``). A page opened again (a reload, the window
closed and opened) reconnects to its last session by itself, as its host if it
holds the session's token and no other page hosts it (Terry, 2026-10-30, for
a creator who comes back); Leave Session and Cancel forget the last session.
"""

from __future__ import annotations

import datetime
import json
import urllib.parse
import uuid

import streamlit as st
from streamlit.runtime import Runtime
from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx

from . import ids
from .sessions import HOST, REGISTRY, VIEWER, Checked, Refused, Shown
from .shared import COOKIE_READER_JS, version_text
from .timer import limit_minutes

PAGE_TITLE = "SpeakerTimer"

CHECK_IN_S = 1.0
"""How often a page checks in: the most a viewer's timers lag the host's Start or Stop, and how soon a session waited for is joined."""

_COOKIE_DAYS = 7
"""How long the cookies last: a session is removed 6 hours after the last page leaves it (``sessions.EXPIRE_AFTER_S``), so a week covers any return."""

_HOSTS_KEPT = 10
"""The most host tokens the browser keeps, the most recent: a token of a session long gone only takes room."""

LAST_COOKIE = "speakertimer_last"
HOSTS_COOKIE = "speakertimer_hosts"

# The screens.
START = "start"
PENDING = "pending"

# Session-state keys.
_HEARD = "heard"          # the cookies as the page's script sent them; absent until it has answered
_READER = "reader"        # the element that reads the cookies (shared.COOKIE_READER_JS)
_STARTED = "started"      # the visit's start is done: its last session reconnected, or not
_SCREEN = "screen"        # START, PENDING, HOST or VIEWER
_SID = "sid"              # the session waited for, hosted or viewed
_BOX = "box"              # the Session ID box
_LIMIT_BOX = "limit_box"  # the Time Limit box (minutes)
_TOKENS = "tokens"        # the host's token of each session this browser hosted: {sid: token}
_NOTICE = "notice"        # a message shown once: (kind, text), kind one of st's "success", "info", "warning", "error"
_DECLINED = "declined"    # the sessions whose "Assume Host Role?" this page answered No
_COOKIE_OP = "cookie_op"  # the cookies' last change, for the writer (_WRITER_JS)
_BEAT = "beat"            # the check-ins counted, so that each one reaches the page's script
_MARK = "mark"            # the page's own part of its id (_page)

# The clock's look. A dark panel with light figures reads well from a lectern
# and in a small window beside the slides. Remaining turns red once the limit
# is passed (negative). The figures are sized by the panel's width (cqw: a
# hundredth of it), so a narrow window still shows them whole.
_CLOCK_COLOURS = {"panel": "#111418", "figures": "#f4f6f8", "labels": "#aab2bd", "over": "#ff5a4f"}

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

_HUB_JS = f"""
const DRAW_MS = {_DRAW_MS};
const RESYNC_S = {_RESYNC_S};
const KICK_AFTER_MS = {_KICK_AFTER_MS};

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

const PIP_CSS = `
  html, body {{ margin: 0; height: 100%; background: {_CLOCK_COLOURS["panel"]}; color: {_CLOCK_COLOURS["figures"]};
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
  body {{ display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 1vh; }}
  .row {{ display: flex; flex-direction: column; align-items: center; line-height: 1; }}
  .label {{ font-size: min(5vw, 6vh); color: {_CLOCK_COLOURS["labels"]}; margin-bottom: 0.6vh; }}
  .value {{ font-size: min(19vw, 27vh); font-weight: 700; font-variant-numeric: tabular-nums; }}
  .limit .value {{ font-size: min(7vw, 9vh); font-weight: 600; }}
  .over {{ color: {_CLOCK_COLOURS["over"]}; }}
`;
const ROWS_HTML = '<div class="row"><span class="label">Elapsed Time</span><span class="value" data-v="elapsed"></span></div>'
  + '<div class="row"><span class="label">Time Remaining</span><span class="value" data-v="remaining"></span></div>'
  + '<div class="row limit"><span class="label">Time Limit</span><span class="value" data-v="limit"></span></div>';

// The page's one note of the timer, kept on the window so that every element
// that is rebuilt (each of the page's runs) finds the one before's.
const hub = () => (window.speakerTimer ??= {{
  base: null,      // the last reading taken: {{sid, elapsed, running, limit, version, at}}, at from performance.now()
  lastSync: 0,     // when a check-in last arrived
  lastKick: 0,     // when the pop-out last asked for one
  kick: null,      // asks for a check-in (the latest sync element's trigger)
  pip: null,       // the pop-out window, while open

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
    this.base = {{ sid: reading.sid, elapsed: reading.elapsed, running: reading.running,
                  limit: reading.limit, version: reading.version, at }};
    this.lastSync = at;
  }},

  // The three texts now, as timer.elapsed_shown and timer.remaining_shown.
  texts() {{
    const b = this.base;
    if (b === null) return null;
    const elapsed = b.elapsed + (b.running ? (performance.now() - b.at) / 1000 : 0);
    const shown = Math.max(0, Math.floor(elapsed));
    const remaining = b.limit === null ? 0 : b.limit - shown;
    return {{
      elapsed: clock(shown), remaining: clock(remaining), over: remaining < 0,
      limit: b.limit === null ? "none" : clock(b.limit),
    }};
  }},

  draw(root) {{
    const t = this.texts();
    if (t === null) return;
    const set = (name, text) => {{
      const el = root.querySelector('[data-v="' + name + '"]');
      if (el !== null && el.textContent !== text) el.textContent = text;
      return el;
    }};
    set("elapsed", t.elapsed);
    const remaining = set("remaining", t.remaining);
    if (remaining !== null) remaining.classList.toggle("over", t.over);
    set("limit", t.limit);
  }},

  // The pop-out: drawn and timed from its own window, which stays on top.
  openPip(win) {{
    const h = this;
    win.document.title = "SpeakerTimer";
    const style = win.document.createElement("style");
    style.textContent = PIP_CSS;
    win.document.head.appendChild(style);
    win.document.body.innerHTML = ROWS_HTML;
    const tick = () => {{
      h.draw(win.document);
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
    '<div class="row"><span class="label">Elapsed Time</span><span class="value" data-v="elapsed"></span></div>'
    '<div class="row"><span class="label">Time Remaining</span><span class="value" data-v="remaining"></span></div>'
    '<div class="row limit"><span class="label">Time Limit</span><span class="value" data-v="limit"></span></div>'
    '<div class="tools"><button class="pop" type="button" hidden></button></div>'
    '<div class="note" hidden></div>'
    "</div>"
)

_CLOCK_CSS = f"""
.clock {{
  container-type: inline-size;
  background: {_CLOCK_COLOURS["panel"]}; color: {_CLOCK_COLOURS["figures"]};
  border-radius: 14px; padding: 14px 12px 12px;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}}
.row {{ display: flex; flex-direction: column; align-items: center; line-height: 1; margin-bottom: 10px; }}
.label {{ font-size: clamp(14px, 4.2cqw, 24px); color: {_CLOCK_COLOURS["labels"]}; margin-bottom: 4px; }}
.value {{ font-size: clamp(44px, 19cqw, 150px); font-weight: 700; font-variant-numeric: tabular-nums; }}
.limit .value {{ font-size: clamp(22px, 7cqw, 54px); font-weight: 600; }}
.over {{ color: {_CLOCK_COLOURS["over"]}; }}
.tools {{ display: flex; justify-content: center; margin-top: 4px; }}
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
  const {{ data, parentElement }} = component;
  const h = hub();
  h.update(data);
  const root = parentElement.querySelector(".clock");
  const pop = root.querySelector(".pop");
  const note = root.querySelector(".note");
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
        h.openPip(await api.requestWindow({{ width: 340, height: 260 }}));
      }} catch (error) {{
        note.textContent = "The pop-out could not be opened: " + error.message;
        note.hidden = false;
      }}
    }});
  }}
  const draw = () => {{
    h.draw(root);
    const label = h.pip !== null ? POP_CLOSE : POP_OPEN;
    if (pop.textContent !== label) pop.textContent = label;
  }};
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

# The cookies' change (``_COOKIE_OP``): laid out on every run at the page's
# foot, applied once by each page (the number ``n`` noted on the window), so
# a run that changes nothing writes nothing, and two windows of one browser
# do not undo each other's cookie at every run. ``last`` is the session to
# reconnect to (null: none); ``host``, when given, adds one session's token
# to those kept, without touching the others' (another window's).
_WRITER_JS = f"""
const LAST = {json.dumps(LAST_COOKIE)};
const HOSTS = {json.dumps(HOSTS_COOKIE)};
const KEPT = {_HOSTS_KEPT};

export default function (component) {{
  const {{ data }} = component;
  const done = (window.speakerTimerCookies ??= {{ n: 0 }});
  if (data.n === 0 || data.n === done.n) return;
  done.n = data.n;
  try {{
    const read = (name) => {{
      const kept = document.cookie.split("; ").find((pair) => pair.startsWith(name + "="));
      return kept === undefined ? null : kept.slice(name.length + 1);
    }};
    const write = (name, value) => {{
      document.cookie = value === null
        ? name + "=; max-age=0; path=/; SameSite=Lax"
        : name + "=" + value + "; max-age=" + data.seconds + "; path=/; SameSite=Lax"
          + (location.protocol === "https:" ? "; Secure" : "");
    }};
    write(LAST, data.last === null ? null : encodeURIComponent(data.last));
    if (data.host !== null) {{
      let hosts = {{}};
      try {{
        hosts = JSON.parse(decodeURIComponent(read(HOSTS) ?? ""));
      }} catch (error) {{
        hosts = {{}};
      }}
      if (typeof hosts !== "object" || hosts === null || Array.isArray(hosts)) hosts = {{}};
      const [sid, token] = data.host;
      delete hosts[sid];  // and added again, as the most recent
      hosts[sid] = token;
      const sids = Object.keys(hosts);
      while (sids.length > KEPT) delete hosts[sids.shift()];
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
.speakertimer-wait { display: flex; align-items: center; gap: 0.7em; font-size: 1.25rem; margin: 0.5rem 0 0.25rem; }
.speakertimer-spinner {
  width: 1.3em; height: 1.3em; flex: none; border-radius: 50%;
  border: 0.22em solid rgba(128, 128, 128, 0.3); border-top-color: #e2483d;
  animation: speakertimer-spin 0.9s linear infinite;
}
@keyframes speakertimer-spin { to { transform: rotate(360deg); } }
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
    "This page keeps two small cookies in your browser: the last session you connected to and, for a session you hosted, "
    "its host key, so that a reload or a return reconnects you. Leave Session forgets the session."
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


def _notify(kind: str, text: str) -> None:
    st.session_state[_NOTICE] = (kind, text)


def _cookie_change(last: str | None, host: tuple[str, str] | None = None) -> None:
    """Have the writer (``_WRITER_JS``) set the last session (None: forget it) and, if given, keep a session's host token."""
    before = st.session_state.get(_COOKIE_OP, {"n": 0})
    st.session_state[_COOKIE_OP] = {"n": before["n"] + 1, "last": last, "host": list(host) if host else None}


def _enter(sid: str, checked: Checked) -> None:
    """Show session ``sid`` in the role ``checked`` gives, and remember it in the browser."""
    state = st.session_state
    state[_SID] = sid
    state[_SCREEN] = checked.role
    state[_BOX] = sid
    host = None
    if checked.role == HOST:
        token = checked.token or _tokens().get(sid)
        if checked.token:
            _tokens()[sid] = checked.token
        host = (sid, token) if token else None
        state[_LIMIT_BOX] = checked.shown.limit
    _cookie_change(sid, host)


# -- the cookies, read at the visit's start ---------------------------------------

def _cookies_read() -> None:
    """The page's script has sent the cookies: kept for the visit's start."""
    sent = (st.session_state.get(_READER) or {}).get("cookies")
    st.session_state[_HEARD] = sent if isinstance(sent, dict) else {}


def heard_cookies(sent: object) -> tuple[str | None, dict[str, str]]:
    """The last session and the host tokens in the cookies as the script sent them; (None, {}) for none, or for a cookie spoilt.

    Each ID is read again as the box reads one, so a cookie altered by hand
    cannot name a session no box could.
    """
    if not isinstance(sent, dict):
        return None, {}
    last_text = sent.get(LAST_COOKIE)
    last = ids.normalized(urllib.parse.unquote(last_text)) if isinstance(last_text, str) else ""
    hosts: dict[str, str] = {}
    hosts_text = sent.get(HOSTS_COOKIE)
    if isinstance(hosts_text, str) and hosts_text:
        try:
            kept = json.loads(urllib.parse.unquote(hosts_text))
        except ValueError:
            kept = {}
        if isinstance(kept, dict):
            for sid, token in kept.items():
                if isinstance(sid, str) and isinstance(token, str) and ids.normalized(sid) == sid and sid:
                    hosts[sid] = token
    return (last or None) if not ids.problem(last) else None, hosts


def _start_visit() -> None:
    """The visit's first run after the cookies were read: reconnect to the last session, if it is running."""
    state = st.session_state
    state[_STARTED] = True
    last, hosts = heard_cookies(state.get(_HEARD))
    _tokens().update(hosts)
    if last is None:
        return
    checked = REGISTRY.join(last, _page(), _tokens().get(last))
    if checked is None:
        state[_BOX] = last
        _notify("info", f"Your last session, {last}, is not running now.")
        return
    _enter(last, checked)
    as_what = "its host" if checked.role == HOST else "a viewer"
    _notify("success", f"Reconnected to session {last}, as {as_what}.")


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
        sid, token, notice = REGISTRY.create(sid or None, _page())
    except Refused as refusal:
        _notify("error", str(refusal))
        return
    _tokens()[sid] = token
    st.session_state[_DECLINED] = set()
    checked = REGISTRY.join(sid, _page(), token)
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
    checked = REGISTRY.join(sid, _page(), _tokens().get(sid))
    if checked is None:
        st.session_state[_SID] = sid
        st.session_state[_SCREEN] = PENDING
        _cookie_change(sid)
        return
    _enter(sid, checked)


def _cancel() -> None:
    # The box is put back: Streamlit forgets a widget's value while it is not laid out (the pending screen has none).
    st.session_state[_BOX] = st.session_state.get(_SID) or ""
    st.session_state[_SCREEN] = START
    _cookie_change(None)


def _leave() -> None:
    sid = st.session_state.get(_SID)
    if sid:
        REGISTRY.leave(sid, _page())
        st.session_state[_BOX] = sid
        _notify("info", f"You left session {sid}.")
    st.session_state[_SCREEN] = START
    _cookie_change(None)


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


def _assume_host() -> None:
    sid = st.session_state.get(_SID)
    try:
        token = REGISTRY.assume_host(sid, _page())
    except Refused as refusal:
        _notify("error", str(refusal))
        return
    _tokens()[sid] = token
    checked = REGISTRY.join(sid, _page(), token)
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


def _pending_screen(sid: str) -> None:
    st.markdown(
        '<div class="speakertimer-wait"><span class="speakertimer-spinner"></span>'
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
    checked = REGISTRY.join(sid, _page(), _tokens().get(sid))
    if checked is not None:
        _enter(sid, checked)
        _notify("success", f"Connected to session {sid}.")
        st.rerun(scope="app")


def _session_screen(sid: str, checked: Checked) -> None:
    role = checked.role
    who = "You are the host." if role == HOST else "You are viewing it."
    st.markdown(f"Session ID: **{_markdown_text(sid)}**   {who}")
    st.fragment(_live, run_every=datetime.timedelta(seconds=CHECK_IN_S))(sid, role)
    clock = st.components.v2.component("speakertimer_clock", html=_CLOCK_HTML, css=_CLOCK_CSS, js=_CLOCK_JS)
    clock(key="clock", data={"sid": sid, **checked.shown.for_script()})
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
    check-in. On its own runs, a change of role or a session ended lays the
    whole page out again.
    """
    state = st.session_state
    if state.get(_SCREEN) != role or state.get(_SID) != sid:
        # A button in this fragment (Yes) has changed the screen: its click
        # reran the fragment alone, so the page is laid out again here.
        st.rerun(scope="app")
    checked = REGISTRY.check_in(sid, _page(), _tokens().get(sid))
    if checked is None:
        state[_SCREEN] = START
        state[_BOX] = sid
        _notify("warning", f"Session {sid} has ended.")
        st.rerun(scope="app")
    if checked.role != role:
        _enter(sid, checked)
        if checked.role == HOST:
            _notify("success", f"You are the host of session {sid} again.")
        else:
            _notify("warning", f"Someone else became the host of session {sid} while you were away. You are now viewing it.")
        st.rerun(scope="app")
    # "beat" differs at every check-in: an element whose data is the same as
    # the last run's is not run again in the browser (measured: a stopped
    # timer's check-ins reached the script only with it), and the pop-out
    # must see every check-in arrive to know when to ask for one.
    state[_BEAT] = state.get(_BEAT, 0) + 1
    sync = st.components.v2.component("speakertimer_sync", js=_SYNC_JS)
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
    writer = st.components.v2.component("speakertimer_cookies", js=_WRITER_JS)
    op = st.session_state.get(_COOKIE_OP, {"n": 0, "last": None, "host": None})
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
        reader = st.components.v2.component("speakertimer_reader", js=COOKIE_READER_JS)
        reader(key=_READER, data={"names": [LAST_COOKIE, HOSTS_COOKIE]}, on_cookies_change=_cookies_read)
        st.caption("Starting …")
        return
    if not state.get(_STARTED):
        _start_visit()

    screen = state.get(_SCREEN, START)
    sid = state.get(_SID)
    checked = None
    if screen in (HOST, VIEWER):
        checked = REGISTRY.check_in(sid, _page(), _tokens().get(sid))
        if checked is None:
            state[_SCREEN] = screen = START
            state[_BOX] = sid
            _notify("warning", f"Session {sid} has ended.")
        elif checked.role != screen:
            _enter(sid, checked)
            screen = checked.role

    _show_notice()
    if screen == PENDING:
        _pending_screen(sid)
    elif checked is not None:
        _session_screen(sid, checked)
    else:
        _start_screen()
    _write_cookies()
    st.caption(_COOKIE_NOTE)
