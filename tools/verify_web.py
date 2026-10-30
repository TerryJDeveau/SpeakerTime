"""The page's screens and buttons through Streamlit's AppTest, with no browser: ``python tools/verify_web.py``.

AppTest runs none of the page's script, so the clock's figures, the cookies
and the once-a-second check-ins are ``verify_browser.py``'s to check. Here:
what each screen lays out, the messages, which buttons are greyed, and what
the shared sessions hold after each click, for two pages at once.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from speakertimer import web  # noqa: E402
from speakertimer.sessions import REGISTRY  # noqa: E402

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


def page(cookies: dict | None = None) -> AppTest:
    """A new page, its cookies' reader answered (AppTest runs no script): ``cookies`` as the script would send them."""
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=30)
    if cookies is not None:
        at.session_state[web._HEARD] = cookies
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
    a = page({})
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
    button(a, "Start").click().run()
    labels = {b.label: b.disabled for b in a.button}
    check("running: Stop and Reset", (labels["Start"], labels["Stop"], labels["Reset"]), (True, False, False))
    check("running in the session", REGISTRY.shown(sid).running, True)

    # A second page: the ID taken; a viewer of it with the case and spaces changed.
    b = page({})
    b.text_input(key=web._BOX).input(f" {sid[:3]} {sid[3:]} ").run()
    button(b, "Create New Session").click().run()
    check("taken ID refused", any("already has a host" in e.value for e in b.error), True)
    button(b, "Connect to Session").click().run()
    check("viewer screen", "You are viewing it." in texts(b), True)
    check("a viewer has no Start", [x.label for x in b.button if x.label in ("Start", "Stop", "Reset")], [])

    # A session not yet created: pending, and Cancel puts the ID back.
    b2 = page({})
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

    # The cookies: a page whose last session is running reconnects by itself; one whose is not has its ID in the box.
    c = page({web.LAST_COOKIE: sid})
    check("reconnected by the cookie", "Reconnected to session" in texts(c) and "You are viewing it." in texts(c), True)
    d = page({web.LAST_COOKIE: "GONE"})
    check("last session not running", (d.text_input(key=web._BOX).value, "is not running now" in texts(d)), ("GONE", True))
    e = page({web.LAST_COOKIE: "%7Bspoilt", web.HOSTS_COOKIE: "not json"})
    check("spoilt cookies: the start screen", e.session_state[web._SCREEN] if web._SCREEN in e.session_state else "start", "start")

    for at_ in (a, b, b2, c, d, e):
        check("no exception", [x.value for x in at_.exception], [])

    for failure in FAILURES:
        print("FAIL", failure)
    print(f"{COUNT - len(FAILURES)} of {COUNT} checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
