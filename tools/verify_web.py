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
    check("the buttons the page's style colours have their keys (v1.19)",
          (a.button(key=web._START_BUTTON).label, a.button(key=web._STOP_BUTTON).label, a.button(key=web._LEAVE_BUTTON).label), ("Start", "Stop", "Leave Session"))

    # The Time Limit, rounded; Start; then Stop is the button to press, Reset too.
    a.number_input(key=web._LIMIT_BOX).set_value(2.25).run()
    check("limit rounded in the box", a.number_input(key=web._LIMIT_BOX).value, 2.3)
    check("limit kept in the session", REGISTRY.shown(sid).limit, 2.3)
    a.number_input(key=web._WARN_BOX).set_value(0.55).run()
    check("warning rounded in its box, kept in the session", (a.number_input(key=web._WARN_BOX).value, REGISTRY.shown(sid).warning), (0.6, 0.6))
    # The boxes take the session's values at every laying out, not only on entering (v1.18).
    REGISTRY._sessions[sid].timer.limit = 3.0
    REGISTRY._sessions[sid].warning = 1.0
    a.run()
    check("each box takes the session's value at every laying out",
          (a.number_input(key=web._LIMIT_BOX).value, a.number_input(key=web._WARN_BOX).value), (3.0, 1.0))
    a.number_input(key=web._LIMIT_BOX).set_value(None).run()
    a.number_input(key=web._WARN_BOX).set_value(None).run()
    shown = REGISTRY.shown(sid)
    check("each box cleared stays blank, and the session has none",
          (a.number_input(key=web._LIMIT_BOX).value, a.number_input(key=web._WARN_BOX).value, shown.limit, shown.warning), (None, None, None, None))
    REGISTRY._sessions[sid].timer.limit = 2.3
    REGISTRY._sessions[sid].warning = 0.6
    a.run()
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
    check("a page counts each session entered, for its fit to its window: joined, then the host", b.session_state[web._ENTERED], 2)
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

    # The switch in a page's address that stops its buzzer sounding (v1.13, Terry, 2026-11-03).
    for params, expected in (({}, False), ({"silent": ""}, True), ({"-silent": ""}, True), ({"--SILENT": "1"}, True), ({"silent": "yes"}, True),
                             ({"silent": "no"}, False), ({"silent": "0"}, False), ({"silent": "False"}, False), ({"sound": ""}, False),
                             # A second ? or a ; for the & (Terry, 2026-11-05), as they arrive: only & divides for a browser.
                             ({"session": "ROOM5?silent"}, True), ({"silent?session": "ROOM5"}, True), ({"session": "ROOM5;silent"}, True),
                             ({"session": "ROOM5?-silent"}, True), ({"session": "ROOM5?silent=no"}, False), ({"session": "ROOM5"}, False)):
        check(f"silent_asked({params})", web.silent_asked(params), expected)
    s = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=30)
    s.query_params["silent"] = "1"  # AppTest loses a blank value after its first run; Streamlit keeps one (verify_browser: ?silent)
    s.session_state[web._HEARD] = sent(browser="browserS1")
    s.run()
    check("the start screen says nothing of it", web._SILENT_TEXT in texts(s), False)
    s.text_input(key=web._BOX).input("quiet").run()
    button(s, "Create New Session").click().run()
    check("a page whose address says silent says so", ("You are the host." in texts(s), web._SILENT_TEXT in texts(s)), (True, True))
    check("a page whose address does not, does not", web._SILENT_TEXT in texts(r), False)

    # A link naming a session (v1.15, Terry, 2026-11-03): as Connect to Session, with nothing typed.
    for params, expected in (({}, None), ({"session": "ROOM5"}, "ROOM5"), ({"-Session": "room 5"}, "room 5"), ({"session": "  "}, None), ({"sound": "x"}, None),
                             ({"session": "ROOM5?silent"}, "ROOM5"), ({"silent?session": "ROOM5"}, "ROOM5"), ({"?session": "ROOM5"}, "ROOM5"),
                             ({"session": "ROOM5;silent"}, "ROOM5"), ({"session": "?silent"}, None)):
        check(f"session_asked({params})", web.session_asked(params), expected)

    def linked(address: dict, heard: dict) -> AppTest:
        at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=30)
        for name, value in address.items():
            at.query_params[name] = value
        at.session_state[web._HEARD] = heard
        return at.run()

    l1 = linked({"session": "second", "silent": "1"}, sent(browser="browserK1"))
    check("a link to a running session: viewing it, nothing typed, silent too",
          (l1.session_state[web._SID], "You are viewing it." in texts(l1), web._SILENT_TEXT in texts(l1)), ("SECOND", True, True))
    button(l1, "Leave Session").click().run()
    check("Leave Session takes the session out of the address, the rest kept",
          ("session" in l1.query_params, "silent" in l1.query_params, l1.session_state[web._SCREEN]), (False, True, "start"))
    # silent=1: AppTest loses a blank value after its first run (above); verify_browser has ?session=ID?silent itself.
    l7 = linked({"session": "second?silent=1"}, sent(browser="browserK7"))
    check("a second ? for the &: that session, not one called SECONDSILENT1, and silent",
          (l7.session_state[web._SID], "You are viewing it." in texts(l7), web._SILENT_TEXT in texts(l7)), ("SECOND", True, True))
    button(l7, "Leave Session").click().run()
    check("Leave Session then: the session out of the address, the switch kept",
          ("session" in l7.query_params, l7.query_params.get("silent")), (False, ["1"]))
    l2 = linked({"session": "not yet"}, sent(browser="browserK2"))
    check("a link to a session not yet created: waiting for it", "Please wait, connection pending" in texts(l2), True)
    button(l2, "Cancel").click().run()
    check("Cancel takes it out of the address too", ("session" in l2.query_params, l2.text_input(key=web._BOX).value), (False, "NOTYET"))
    l3 = linked({"session": "%%%"}, sent(browser="browserK3"))
    check("a link with nothing usable: refused, on the start screen", any("can be part of a session ID" in x.value for x in l3.error), True)
    l4 = linked({"session": "x" * 100}, sent(browser="browserK4"))
    check("a link too long: refused, on the start screen", any("at most 20" in x.value for x in l4.error), True)
    l5 = linked({"session": "second"}, sent(tab="SECOND", browser="browserK5"))
    check("a reload of a tab already in the link's session: reconnected", "Reconnected to session SECOND" in texts(l5), True)
    l6 = linked({"session": "second"}, sent(tab=sid, browser="browserK6"))
    check("a link to another session than the tab's: the link's", l6.session_state[web._SID], "SECOND")

    # The host's row and the Warning Colour (Terry, 2026-11-04). AppTest has no layout, so verify_browser has where they stand.
    h1 = page(sent(browser="browserL1"))
    h1.text_input(key=web._BOX).set_value("row check").run()
    button(h1, "Create New Session").click().run()
    check("the host's buttons, in order: Copy Link and Leave Session after Reset",
          [b_.label for b_ in h1.button], ["Start", "Stop", "Reset", "Copy Link", "Leave Session"])
    label = next((m for m in h1.markdown if web._WARNING_COLOUR_LABEL in m.value), None)
    check("the label reads Warning Colour, with its help", (label is not None, bool(label and label.proto.help)), (True, True))
    check("the Time Left Warning's help leaves the buttons to it", "first button" in web._WARNING_HELP, False)
    button(h1, "Copy Link").click().run()
    check("Copy Link's run changes nothing on the server", ("You are the host." in texts(h1), h1.session_state[web._SID]), (True, "ROWCHECK"))
    v1 = linked({"session": "rowcheck"}, sent(browser="browserL2"))
    check("a viewer: Leave Session and no Copy Link", [b_.label for b_ in v1.button], ["Leave Session"])

    for at_ in (a, b, b2, c, d, e, legacy, other, f, g, w, hst, t, r, s, l1, l2, l3, l4, l5, l6, h1, v1):
        check("no exception", [x.value for x in at_.exception], [])

    for failure in FAILURES:
        print("FAIL", failure)
    print(f"{COUNT - len(FAILURES)} of {COUNT} checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
