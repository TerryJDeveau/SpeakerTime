"""The page's screens and buttons through Streamlit's AppTest, with no browser: ``python tools/verify_web.py``.

AppTest runs none of the page's script, so the clock's figures, the cookies
and the once-a-second check-ins are ``verify_browser.py``'s to check. Here:
what each screen lays out, the messages, which buttons are greyed, and what
the shared sessions hold after each click, for two pages at once.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# The checks' own file of remembered settings, never the app's (sessions.REMEMBER_FILE), holding one
# session of an earlier run of the app (item 11).
REMEMBERED = Path(tempfile.mkdtemp()) / "remembered.json"
REMEMBERED.write_text(json.dumps({"LASTWEEK": {"limit": 7.5, "warning": 1.0, "colours": {"elapsed": "black", "remaining": "black"},
                                               "theme": "light", "host_mode": "minutes"}}), encoding="utf-8")
os.environ["SPEAKERTIME_REMEMBER"] = str(REMEMBERED)

from streamlit.testing.v1 import AppTest  # noqa: E402

from speakertime import web  # noqa: E402
from speakertime.sessions import REGISTRY  # noqa: E402

# AppTest logs "missing ScriptRunContext" at each new page; a filter stays where a level is reset (ratesplot).
logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").addFilter(
    lambda record: "missing ScriptRunContext" not in record.getMessage()
)

FAILURES: list[str] = []
COUNT = 0


def check(what: str, got: object, expected: object) -> None:
    global COUNT
    COUNT += 1
    if got != expected:
        FAILURES.append(f"{what}: got {got!r}, expected {expected!r}")


def sent(cookies: dict | None = None, tab: str = "", browser: str = "", tabid: str = "") -> dict:
    """What the page's reader would send (``web._READER_JS``): cookies by name (values as kept: JSON encoded), the tab's session, the browser's and the tab's ids."""
    encoded = {name: value if isinstance(value, str) else urllib.parse.quote(json.dumps(value))
               for name, value in (cookies or {}).items()}
    return {"cookies": encoded, "tab": tab, "browser": browser, "tabid": tabid}


def page(heard: dict | None = None) -> AppTest:
    """A new page, its reader answered (AppTest runs no script) with ``heard`` (``sent``); None leaves it unanswered."""
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=30)
    if heard is not None:
        at.session_state[web._HEARD] = heard
    at.run()
    return at


def button(at: AppTest, label: str):
    return next(b for b in at.button if b.label == label)


def texts(at: AppTest) -> str:
    return " | ".join([m.value for m in at.markdown] + [c.value for c in at.caption]
                      + [e.value for e in at.error] + [w.value for w in at.warning] + [i.value for i in at.info]
                      + [s.value for s in at.success])


def main() -> int:
    # The first run waits for the cookies' reader, laying out nothing else.
    at = page()
    check("first run: only the reader", ([b.label for b in at.button], "Starting" in texts(at)), ([], True))

    # The start screen.
    a = page(sent())
    check("start screen buttons", [b.label for b in a.button], ["Create New Session", "Connect to Session"])
    check("Session ID box", [t.label for t in a.text_input], ["Session ID"])

    # Connect with a blank box: a message; Create with a blank box: a random six-digit ID, the host screen.
    button(a, "Connect to Session").click().run()
    check("blank connect refused", any("Enter the ID" in e.value for e in a.error), True)
    button(a, "Create New Session").click().run()
    sid = a.session_state[web._SID]
    check("random ID", (len(sid), sid.isdigit()), (6, True))
    check("host screen", "You are the host." in texts(a), True)
    check("the ID shown in the box's place", a.session_state[web._BOX], sid)
    labels = {b.label: b.disabled for b in a.button}
    check("stopped and at zero: Start only", (labels["Start"], labels["Stop"], labels["Reset"]), (False, True, True))

    # The Time Limit, rounded; Start; then Stop is the button to press, Reset too.
    a.number_input(key=web._LIMIT_BOX).set_value(2.25).run()
    check("limit rounded in the box", a.number_input(key=web._LIMIT_BOX).value, 2.3)
    check("limit kept in the session", REGISTRY.shown(sid).limit, 2.3)
    a.number_input(key=web._WARN_BOX).set_value(0.55).run()
    check("warning rounded in its box, kept in the session", (a.number_input(key=web._WARN_BOX).value, REGISTRY.shown(sid).warning), (0.6, 0.6))
    button(a, "Start").click().run()
    labels = {b.label: b.disabled for b in a.button}
    check("running: Stop and Reset, Start greyed and reading Restart (v1.11)",
          ("Start" in labels, labels["Restart"], labels["Stop"], labels["Reset"]), (False, True, False, False))
    check("running in the session", REGISTRY.shown(sid).running, True)
    # v1.11 (Terry, 2026-11-03): Restart once the timer has run, greyed or not; Start again after Reset.
    button(a, "Stop").click().run()
    labels = {b.label: b.disabled for b in a.button}
    check("stopped part way: Restart and Reset", ("Start" in labels, labels["Restart"], labels["Stop"], labels["Reset"]),
          (False, False, True, False))
    button(a, "Reset").click().run()
    labels = {b.label: b.disabled for b in a.button}
    check("reset: Start again", ("Restart" in labels, labels["Start"], labels["Stop"], labels["Reset"]), (False, False, True, True))
    button(a, "Start").click().run()
    check("started again: running", (REGISTRY.shown(sid).running, "Restart" in {b.label for b in a.button}), (True, True))

    # A second page: the ID taken; a viewer of it with the case and spaces changed.
    b = page(sent())
    b.text_input(key=web._BOX).input(f" {sid[:3]} {sid[3:]} ").run()
    button(b, "Create New Session").click().run()
    check("taken ID refused", any("already has a host" in e.value for e in b.error), True)
    button(b, "Connect to Session").click().run()
    check("viewer screen", "You are viewing it." in texts(b), True)
    check("a viewer has no Start", [x.label for x in b.button if x.label in ("Start", "Stop", "Reset")], [])

    # A session not yet created: pending, and Cancel puts the ID back.
    b2 = page(sent())
    b2.text_input(key=web._BOX).input("later on").run()
    button(b2, "Connect to Session").click().run()
    check("pending", "Please wait, connection pending" in texts(b2), True)
    check("pending: Cancel only", [x.label for x in b2.button], ["Cancel"])
    button(b2, "Cancel").click().run()
    check("cancel: the box as read", b2.text_input(key=web._BOX).value, "LATERON")

    # Characters left out, and too long.
    b2.text_input(key=web._BOX).input("%%%").run()
    button(b2, "Connect to Session").click().run()
    check("nothing usable refused", any("can be part of a session ID" in e.value for e in b2.error), True)
    b2.text_input(key=web._BOX).input("x" * 21).run()
    button(b2, "Connect to Session").click().run()
    check("too long refused", any("at most 20" in e.value for e in b2.error), True)

    # The host leaves: the session runs on; the viewer's page lays out the offer at its next run.
    button(a, "Leave Session").click().run()
    check("left: the start screen, the ID in the box", (a.session_state[web._SCREEN], a.text_input(key=web._BOX).value), ("start", sid))
    check("the session runs on", REGISTRY.shown(sid).running, True)
    b.run()
    check("offer to the viewer", "Assume Host Role?" in texts(b), True)
    button(b, "No").click().run()
    check("No: the offer stays", ("Assume Host Role?" in texts(b), "You answered No" in texts(b)), (True, True))
    button(b, "Yes").click().run()
    check("Yes: the host", "You are the host." in texts(b), True)
    check("its limit box from the session", b.number_input(key=web._LIMIT_BOX).value, 2.3)
    check("its warning box from the session", b.number_input(key=web._WARN_BOX).value, 0.6)

    # What the browser keeps. A new window whose one recent session is running goes back to it by itself.
    c = page(sent({web.RECENT_COOKIE: [sid, "GONE"]}, browser="browserC1"))
    check("new window: back to its one recent session", "Reconnected to session" in texts(c) and "You are viewing it." in texts(c), True)
    # Its last session not running: its ID in the box.
    d = page(sent({web.RECENT_COOKIE: ["GONE"]}, browser="browserD1"))
    check("last session not running", (d.text_input(key=web._BOX).value, "is not running now" in texts(d)), ("GONE", True))
    # The one cookie of before 2026-11-01 is read as the recent list.
    legacy = page(sent({web.LEGACY_LAST_COOKIE: sid}, browser="browserL1"))
    check("the old cookie still reconnects", "Reconnected to session" in texts(legacy), True)
    # Several running: a Rejoin button for each, nothing joined by itself.
    other = page(sent())
    other.text_input(key=web._BOX).input("second").run()
    button(other, "Create New Session").click().run()
    f = page(sent({web.RECENT_COOKIE: ["SECOND", sid]}, browser="browserF1"))
    check("several: Rejoin buttons, on the start screen",
          ([x.label for x in f.button if x.label.startswith("Rejoin")], "Create New Session" in [x.label for x in f.button]),
          (["Rejoin SECOND", f"Rejoin {sid}"], True))
    button(f, "Rejoin SECOND").click().run()
    check("Rejoin: viewing it", ("You are viewing it." in texts(f), f.session_state[web._SID]), (True, "SECOND"))
    # A reloaded tab goes back to its own session only: here SECOND, though the recent list puts the other first.
    g = page(sent({web.RECENT_COOKIE: [sid, "SECOND"]}, tab="SECOND", browser="browserG1"))
    check("reloaded tab: its own session", (g.session_state[web._SID], "Reconnected" in texts(g)), ("SECOND", True))
    # A reloaded tab whose session is not running: waiting for it, as a viewer would; with its host token, the ID in the box.
    w = page(sent(tab="LATER2", browser="browserW1"))
    check("reloaded viewer's tab, session gone: waiting", "Please wait, connection pending" in texts(w), True)
    hst = page(sent({web.HOSTS_COOKIE: {"LATER3": "token"}}, tab="LATER3", browser="browserH1"))
    check("reloaded host's tab, session gone: its ID in the box",
          (hst.text_input(key=web._BOX).value, "Create New Session starts it again" in texts(hst)), ("LATER3", True))
    # Spoilt notes: the start screen.
    e = page(sent({web.RECENT_COOKIE: "%7Bspoilt", web.HOSTS_COOKIE: "not json"}, tab="$$$", browser="no good!", tabid="x y"))
    check("spoilt notes: the start screen", e.session_state[web._SCREEN] if web._SCREEN in e.session_state else "start", "start")
    check("a spoilt browser id is not used", e.session_state[web._BROWSER], None)
    check("a spoilt tab id is not used", e.session_state[web._TAB], None)
    t = page(sent(browser="browserT1", tabid="tabT1abcd"))
    check("the tab's id kept", t.session_state[web._TAB], "tabT1abcd")

    # A session of an earlier run of the app, created again: its settings back, and said so (Terry, 2026-11-01, item 11).
    r = page(sent(browser="browserR1"))
    r.text_input(key=web._BOX).input("lastweek").run()
    button(r, "Create New Session").click().run()
    shown = REGISTRY.shown("LASTWEEK")
    check("an ID used in an earlier run: its settings back",
          (r.number_input(key=web._LIMIT_BOX).value, r.number_input(key=web._WARN_BOX).value, shown.theme, shown.host_mode),
          (7.5, 1.0, "light", "minutes"))
    check("and said so", "starts from its settings of last time" in texts(r), True)
    check("the file holds the sessions of this run too",
          sorted(json.loads(REMEMBERED.read_text(encoding="utf-8"))["sessions"])[:2], [sid, "LASTWEEK"])

    for at_ in (a, b, b2, c, d, e, legacy, other, f, g, w, hst, t, r):
        check("no exception", [x.value for x in at_.exception], [])

    for failure in FAILURES:
        print("FAIL", failure)
    print(f"{COUNT - len(FAILURES)} of {COUNT} checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
