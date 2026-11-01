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
from speakertime.sessions import EXPIRE_AFTER_S, HOST, HOST_GRACE_S, VIEWER, Refused, Registry  # noqa: E402
from speakertime.timer import (  # noqa: E402
    Timer, clock_text, elapsed_shown, limit_minutes, limit_seconds, remaining_shown,
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


def main() -> int:
    timer_checks()
    id_checks()
    session_checks()
    for failure in FAILURES:
        print("FAIL", failure)
    print(f"{COUNT - len(FAILURES)} of {COUNT} checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
