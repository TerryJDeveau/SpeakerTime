"""Checks of the timer, the session IDs and the sessions' rules, with no Streamlit: ``python tools/verify_logic.py``.

Time is a clock stepped by hand, and whether a page is connected is a set the
checks fill, so every rule about hosts (the grace, a reload, a takeover) is
checked to the second. Prints each failure and a count; exits 1 on any.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from speakertime import ids  # noqa: E402
from speakertime.sessions import (  # noqa: E402
    EXPIRE_AFTER_S, HOST, HOST_GRACE_S, SIZE_DEFAULT, SIZE_MAX, SIZE_MIN, VIEWER, Refused, Registry,
)
from speakertime.timer import (  # noqa: E402
    CLOCK, MINUTES, Timer, clock_text, elapsed_shown, elapsed_tenths, limit_minutes, limit_seconds, minutes_text,
    remaining_shown, remaining_tenths,
)

FAILURES: list[str] = []
COUNT = 0


def check(what: str, got: object, expected: object) -> None:
    global COUNT
    COUNT += 1
    if got != expected:
        FAILURES.append(f"{what}: got {got!r}, expected {expected!r}")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def step(self, seconds: float) -> None:
        self.now += seconds


def timer_checks() -> None:
    # The Time Limit: to the nearest tenth, half up on the number as written.
    for typed, expected in [
        (None, None), ("", None), ("  ", None), ("abc", None), ("nan", None), ("inf", None),
        (5, 5.0), ("5", 5.0), (2.25, 2.3), ("2.25", 2.3), (2.24, 2.2), (0.05, 0.1), (0.04, 0.0),
        (-1, -1.0), (-2.25, -2.3), (0, 0.0), (1.15, 1.2),
    ]:
        check(f"limit_minutes({typed!r})", limit_minutes(typed), expected)
    for limit, expected in [(None, None), (0.0, None), (-3.0, None), (0.1, 6), (2.5, 150), (5.0, 300), (90.0, 5400)]:
        check(f"limit_seconds({limit!r})", limit_seconds(limit), expected)
    # As shown: Elapsed a whole second once it has passed; Remaining the limit minus it; 0 with no limit.
    check("elapsed_shown(0.99)", elapsed_shown(0.99), 0)
    check("elapsed_shown(1.0)", elapsed_shown(1.0), 1)
    check("remaining_shown(0.5, 5)", remaining_shown(0.5, 5.0), 300)
    check("remaining_shown(1.5, 5)", remaining_shown(1.5, 5.0), 299)
    check("remaining_shown(301.2, 5)", remaining_shown(301.2, 5.0), -1)
    check("remaining_shown(100, None)", remaining_shown(100.0, None), 0)
    check("remaining_shown(100, 0)", remaining_shown(100.0, 0.0), 0)
    check("remaining_shown(100, -2)", remaining_shown(100.0, -2.0), 0)
    for seconds, text in [(0, "0:00"), (5, "0:05"), (65, "1:05"), (600, "10:00"), (3723, "1:02:03"),
                          (-1, "−0:01"), (-75, "−1:15"), (-3600, "−1:00:00")]:
        check(f"clock_text({seconds})", clock_text(seconds), text)

    # MINUTES: tenths of a minute, a tenth once it has passed; Remaining the limit's tenths minus Elapsed's, adding up to it.
    for elapsed, tenths in [(0.0, 0), (5.99, 0), (6.0, 1), (59.9, 9), (60.0, 10), (754.0, 125), (7505.0, 1250)]:
        check(f"elapsed_tenths({elapsed})", elapsed_tenths(elapsed), tenths)
    check("remaining_tenths(5, 12.5)", remaining_tenths(5.0, 12.5), 125)
    check("remaining_tenths(6, 12.5)", remaining_tenths(6.0, 12.5), 124)
    check("remaining_tenths(756, 12.5)", remaining_tenths(756.0, 12.5), -1)
    check("remaining_tenths(100, None)", remaining_tenths(100.0, None), 0)
    check("remaining_tenths(100, -1)", remaining_tenths(100.0, -1.0), 0)
    for elapsed in (0.0, 7.0, 359.0, 761.5):
        check(f"tenths add up to the limit at {elapsed}", elapsed_tenths(elapsed) + remaining_tenths(elapsed, 12.5), 125)
    for tenths, text in [(0, "0.0"), (1, "0.1"), (125, "12.5"), (1250, "125.0"), (-1, "\u22120.1"), (-25, "\u22122.5")]:
        check(f"minutes_text({tenths})", minutes_text(tenths), text)

    # Start, Stop (a pause), Start (a resume), Reset; Reset while running is Stop then Reset.
    t = Timer()
    check("new timer running", t.running, False)
    check("start", t.start(10.0), True)
    check("start again does nothing", t.start(11.0), False)
    check("elapsed while running", t.elapsed(14.0), 4.0)
    check("stop", t.stop(14.0), True)
    check("stop again does nothing", t.stop(15.0), False)
    check("elapsed while stopped", t.elapsed(100.0), 4.0)
    t.start(100.0)
    check("resumed", t.elapsed(103.5), 7.5)
    check("reset while running", t.reset(103.5), True)
    check("reset leaves it stopped", t.running, False)
    check("reset to zero", t.elapsed(200.0), 0.0)
    check("reset again does nothing", t.reset(200.0), False)
    check("set_limit", t.set_limit("2.25"), True)
    check("limit kept to a tenth", t.limit, 2.3)
    check("set_limit the same", t.set_limit(2.3), False)


def id_checks() -> None:
    for typed, expected in [
        ("room 5b", "ROOM5B"), ("Room\t#5", "ROOM#5"), ("  abc  ", "ABC"), ("Café", "CAFE"), ("straße", "STRASSE"),
        ("ＡＢＣ１", "ABC1"), ("a-b_c.d#e@f+g&h", "A-B_C.D#E@F+G&H"), ("x$y%z;w,v'u\"t<s>r\\q/p", "XYZWVUTSRQP"),
        ("", ""), ("!!!", ""),
    ]:
        check(f"normalized({typed!r})", ids.normalized(typed), expected)
    check("20 characters allowed", ids.problem("A" * 20), None)
    check("21 refused", ids.problem("A" * 21) is not None, True)
    seen = {"123456"}
    sid = ids.random_id(lambda c: c in seen)
    check("random id has six digits", (len(sid), sid.isdigit(), sid[0] != "0"), (6, True, True))


def session_checks() -> None:
    clock = Clock()
    connected: set[str] = set()
    reg = Registry(clock=clock, connected=lambda page: page in connected)

    # A random ID for a blank box.
    connected.add("A")
    sid, token, notice = reg.create(None, "A")
    check("random session id", (len(sid), sid.isdigit(), notice), (6, True, None))

    # A chosen ID; another page creating it while A hosts it is refused.
    sid, token_a, notice = reg.create("TALK1", "A")
    check("create TALK1", (sid, notice), ("TALK1", None))
    connected.add("B")
    try:
        reg.create("TALK1", "B")
        check("second creator refused", "not refused", "refused")
    except Refused as refusal:
        check("second creator refused", "choose another" in str(refusal), True)

    # Connect to a session not yet created: None (the page waits).
    check("join missing", reg.join("NOPE", "B", None), None)

    # B views TALK1; the host's controls are refused to it.
    checked = reg.join("TALK1", "B", None)
    check("B views", checked.role, VIEWER)
    check("host connected, seen by B", checked.shown.hosted, True)
    try:
        reg.control("TALK1", "B", "start")
        check("viewer cannot start", "started", "refused")
    except Refused:
        check("viewer cannot start", "refused", "refused")

    # The host runs the timer; the viewer sees the same.
    reg.control("TALK1", "A", "limit", 0.1)
    reg.control("TALK1", "A", "start")
    clock.step(4.0)
    check("viewer sees elapsed", reg.check_in("TALK1", "B", None).shown.elapsed, 4.0)
    check("host counts its viewer", reg.check_in("TALK1", "A", token_a).shown.viewers, 1)
    reg.control("TALK1", "A", "stop")
    clock.step(10.0)
    shown = reg.shown("TALK1")
    check("stopped: elapsed kept", (shown.elapsed, shown.running), (4.0, False))
    reg.control("TALK1", "A", "start")
    clock.step(3.0)
    check("resumed", reg.shown("TALK1").elapsed, 7.0)

    # A's page closes. Within the grace it is still the host; after it, the
    # session runs on without one, and B is offered the role. The grace counts
    # from the host's last check-in (once a second while its page is open).
    reg.check_in("TALK1", "A", token_a)
    connected.discard("A")
    clock.step(HOST_GRACE_S - 1)
    check("host within grace", reg.check_in("TALK1", "B", None).shown.hosted, True)
    clock.step(2)
    checked = reg.check_in("TALK1", "B", None)
    check("hostless after grace", (checked.role, checked.shown.hosted, checked.shown.running), (VIEWER, False, True))

    # A comes back (a new page, A2) with its token while nobody hosts: the host again, the timer as it was.
    connected.add("A2")
    checked = reg.join("TALK1", "A2", token_a)
    check("returning creator hosts", (checked.role, checked.token), (HOST, token_a))
    check("timer ran on meanwhile", reg.shown("TALK1").elapsed, 7.0 + HOST_GRACE_S + 1)

    # A reload of the host's page: the old page closed a moment ago, the new one brings the token: host at once.
    connected.discard("A2")
    connected.add("A3")
    clock.step(1.0)
    check("reloaded host hosts at once", reg.join("TALK1", "A3", token_a).role, HOST)
    # A second window of the same browser while the host's page is connected: a viewer.
    connected.add("A4")
    check("second window with the token views", reg.join("TALK1", "A4", token_a).role, VIEWER)

    # The host leaves by Leave Session: hostless at once; B says Yes and gets a new token.
    reg.leave("TALK1", "A3")
    connected.discard("A3")
    connected.discard("A4")
    reg.leave("TALK1", "A4")
    check("hostless after leave", reg.check_in("TALK1", "B", None).shown.hosted, False)
    token_b = reg.assume_host("TALK1", "B")
    check("B hosts, new token", (reg.check_in("TALK1", "B", None).role, token_b != token_a), (HOST, True))
    # Another viewer saying Yes after B: refused.
    connected.add("C")
    reg.join("TALK1", "C", None)
    try:
        reg.assume_host("TALK1", "C")
        check("second Yes refused", "accepted", "refused")
    except Refused:
        check("second Yes refused", "refused", "refused")
    # A, back with its old token while B hosts: a viewer; and still one after B goes (its token is old).
    connected.add("A5")
    check("old token while B hosts: viewer", reg.join("TALK1", "A5", token_a).role, VIEWER)
    connected.discard("B")
    clock.step(HOST_GRACE_S + 1)
    check("old token after B left: viewer", reg.check_in("TALK1", "A5", token_a).role, VIEWER)

    # Create New Session with the ID of a session running without a host: taken over, timers running on.
    elapsed_before = reg.shown("TALK1").elapsed
    connected.add("D")
    sid, token_d, notice = reg.create("TALK1", "D")
    check("create on a hostless session takes it over", (sid, notice is not None, token_d not in (token_a, token_b)), ("TALK1", True, True))
    check("its timer as it was", reg.shown("TALK1").elapsed, elapsed_before)

    # Expiry: nobody connected, nobody heard from, for EXPIRE_AFTER_S.
    connected.clear()
    clock.step(EXPIRE_AFTER_S + 60)
    reg.create("OTHER", "E")  # any create or join removes the expired
    check("expired session removed", reg.exists("TALK1"), False)

    # Where connections cannot be told: the heartbeat alone.
    clock2 = Clock()
    blind = Registry(clock=clock2)
    _, tok, _ = blind.create("X", "P")
    clock2.step(HOST_GRACE_S - 1)
    check("blind: reload within grace views", blind.join("X", "P2", tok).role, VIEWER)
    clock2.step(2)
    check("blind: the token's holder hosts after the grace", blind.check_in("X", "P2", tok).role, HOST)


def display_checks() -> None:
    """The host's sizes, the host's way of showing the times and when pages take it, and a browser's own pages (2026-11-01)."""
    clock = Clock()
    connected: set[str] = set()
    reg = Registry(clock=clock, connected=lambda page: page in connected)

    # Sizes: the host's alone, taken to the step and kept within the bounds; each change raises the version.
    connected.update({"H", "V"})
    _, token_h, _ = reg.create("SIZES", "H", "browser-h")
    reg.join("SIZES", "V", None, "browser-v")
    check("default sizes", reg.shown("SIZES").sizes, {"elapsed": SIZE_DEFAULT, "remaining": SIZE_DEFAULT})
    version = reg.shown("SIZES").version
    reg.set_size("SIZES", "H", "elapsed", 52)
    check("size to the step", reg.shown("SIZES").sizes["elapsed"], 50)
    check("a size raises the version", reg.shown("SIZES").version, version + 1)
    reg.set_size("SIZES", "H", "remaining", 9999)
    reg.set_size("SIZES", "H", "elapsed", -3)
    check("sizes kept within bounds", reg.shown("SIZES").sizes, {"elapsed": SIZE_MIN, "remaining": SIZE_MAX})
    check("sizes reach the viewers", reg.check_in("SIZES", "V", None).shown.for_script()["sizes"], {"elapsed": SIZE_MIN, "remaining": SIZE_MAX})
    for bad in [("limit", 50), ("elapsed", "big"), ("elapsed", float("nan")), ("elapsed", None)]:
        try:
            reg.set_size("SIZES", "H", *bad)
            check(f"bad size {bad!r} refused", "taken", "refused")
        except ValueError:
            check(f"bad size {bad!r} refused", "refused", "refused")
    try:
        reg.set_size("SIZES", "V", "elapsed", 100)
        check("a viewer cannot size", "sized", "refused")
    except Refused:
        check("a viewer cannot size", "refused", "refused")

    # The host's way of showing the times: the default as a page opens; again for everyone when a host connects.
    check("new session's mode", reg.shown("SIZES").host_mode, CLOCK)
    reg.set_host_mode("SIZES", "H", MINUTES)
    reg.set_host_mode("SIZES", "V", CLOCK)  # a viewer's choice is its own: ignored here
    shown = reg.shown("SIZES")
    check("host's mode kept, epoch unchanged", (shown.host_mode, shown.host_epoch), (MINUTES, 1))
    connected.add("V2")
    check("a page opening is given the host's mode", reg.join("SIZES", "V2", None).shown.host_mode, MINUTES)
    # The host reloads (a new page with the token, its panel at CLOCK by then): a host connects, the epoch rises.
    connected.discard("H")
    connected.add("H2")
    checked = reg.join("SIZES", "H2", token_h, "browser-h", CLOCK)
    check("reloaded host: host, its mode the host's", (checked.role, checked.shown.host_mode, checked.shown.host_epoch), (HOST, CLOCK, 2))
    # A page that has shown no session yet (mode None) keeps the session's host mode when it becomes host.
    reg.set_host_mode("SIZES", "H2", MINUTES)
    reg.leave("SIZES", "H2")
    connected.discard("H2")
    reg.assume_host("SIZES", "V", None)
    shown = reg.shown("SIZES")
    check("assumed with no mode: the host's mode stays", (shown.host_mode, shown.host_epoch), (MINUTES, 3))
    reg.leave("SIZES", "V")
    reg.assume_host("SIZES", "V2", CLOCK)
    check("assumed with a mode: it becomes the host's", (reg.shown("SIZES").host_mode, reg.shown("SIZES").host_epoch), (CLOCK, 4))
    check("set_host_mode ignores a mode not in MODES", reg.set_host_mode("SIZES", "V2", "nonsense") or reg.shown("SIZES").host_mode, CLOCK)

    # A browser's own pages: what a new window of that browser could go back to.
    clock2 = Clock()
    on: set[str] = set()
    reg2 = Registry(clock=clock2, connected=lambda page: page in on)
    on.update({"W1", "W2"})
    _, tok_a, _ = reg2.create("ALPHA", "W1", "me")
    _, tok_b, _ = reg2.create("BRAVO", "W2", "me")
    on.add("X")
    _, tok_c, _ = reg2.create("CHARLIE", "X", "someone-else")
    tokens = {"ALPHA": tok_a, "BRAVO": tok_b}
    recent = ["BRAVO", "ALPHA", "CHARLIE", "GONE"]  # GONE is not running: never a candidate
    check("both open in this browser: none", reg2.candidates(recent, "W3", "me", tokens), [("CHARLIE", False)])
    check("another browser sees them all", reg2.candidates(recent, "Y", "other", {}), [("BRAVO", False), ("ALPHA", False), ("CHARLIE", False)])
    on.discard("W1")  # ALPHA's window closed: its host's page gone
    check("ALPHA's window closed: it would host ALPHA", reg2.candidates(recent, "W3", "me", tokens), [("ALPHA", True), ("CHARLIE", False)])
    on.discard("W2")
    check("both closed: both to host", reg2.candidates(recent, "W3", "me", tokens), [("BRAVO", True), ("ALPHA", True), ("CHARLIE", False)])
    check("no browser id: nothing is open here", [sid for sid, _ in reg2.candidates(["CHARLIE"], "W3", None, {})], ["CHARLIE"])


def tab_checks() -> None:
    """A host and a viewer in two tabs of one browser, both holding the host's token (Terry, 2026-11-01, item 3)."""
    clock = Clock()
    on: set[str] = set()
    reg = Registry(clock=clock, connected=lambda page: page in on)
    on.update({"H1", "V1"})
    _, token, _ = reg.create("TABS", "H1", "browser", None, "tab-h")
    check("the viewer's tab, with the browser's token: a viewer", reg.join("TABS", "V1", token, "browser", None, "tab-v").role, VIEWER)
    # The host's tab reloads: its old page closes, and the viewer's tab checks in before the new page joins.
    on.discard("H1")
    clock.step(1.0)
    check("the host's tab reloading: the viewer's tab stays a viewer",
          reg.check_in("TABS", "V1", token, "browser", None, "tab-v").role, VIEWER)
    on.add("H2")
    check("the host's tab, reloaded, hosts", reg.join("TABS", "H2", token, "browser", None, "tab-h").role, HOST)
    # Reloaded again, joining while its old page still seems connected: a viewer, then the host at its check-in.
    on.add("H3")
    check("joining before its old page is seen closed: a viewer", reg.join("TABS", "H3", token, "browser", None, "tab-h").role, VIEWER)
    on.discard("H2")
    check("then the host at its check-in", reg.check_in("TABS", "H3", token, "browser", None, "tab-h").role, HOST)
    # The host's tab closed for good: the viewer's tab is offered the role after the grace, like any viewer.
    on.discard("H3")
    clock.step(HOST_GRACE_S + 1)
    checked = reg.check_in("TABS", "V1", token, "browser", None, "tab-v")
    check("the host's tab closed: the viewer's tab offered the role, not given it", (checked.role, checked.shown.hosted), (VIEWER, False))
    # A window of the browser coming to the session with the token (the creator back): the host.
    on.add("N1")
    check("a new window joining with the token hosts", reg.join("TABS", "N1", token, "browser", None, "tab-n").role, HOST)


def main() -> int:
    timer_checks()
    id_checks()
    session_checks()
    display_checks()
    tab_checks()
    for failure in FAILURES:
        print("FAIL", failure)
    print(f"{COUNT - len(FAILURES)} of {COUNT} checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
