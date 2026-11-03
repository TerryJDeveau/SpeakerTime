"""Checks of the timer, the session IDs and the sessions' rules, with no Streamlit: ``python tools/verify_logic.py``.

Time is a clock stepped by hand, and whether a page is connected is a set the
checks fill, so every rule about hosts (the grace, a reload, a takeover) is
checked to the second. Prints each failure and a count; exits 1 on any.
"""

from __future__ import annotations

import contextlib
import http.server
import io
import json
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from speakertime import colours, ids  # noqa: E402
from speakertime.colours import BLACK, DARK, LIGHT, WHITE  # noqa: E402
from speakertime.sessions import (  # noqa: E402
    BUZZ_HOLD_S, EXPIRE_AFTER_S, HOST, HOST_GRACE_S, REMOTE_RETRY_S, SIZE_DEFAULT, SIZE_MAX, SIZE_MIN, VIEWER, Refused, Registry,
    Settings,
)
from speakertime.store import GIST_DESCRIPTION, GIST_FILE, GistStore  # noqa: E402
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


def colour_checks() -> None:
    """The timers' colours and the theme, and the Time Left Warning's colour (Terry, 2026-11-01, items 4, 9 and 10)."""
    both_white = {"elapsed": WHITE, "remaining": WHITE}
    check("black for one turns a white other black, the theme light",
          colours.choose(both_white, DARK, "elapsed", BLACK), ({"elapsed": BLACK, "remaining": BLACK}, LIGHT))
    check("white for one turns a black other white, the theme dark",
          colours.choose({"elapsed": BLACK, "remaining": BLACK}, LIGHT, "remaining", WHITE), (both_white, DARK))
    check("black beside a colour: the colour kept, the theme light",
          colours.choose({"elapsed": "yellow", "remaining": WHITE}, DARK, "remaining", BLACK),
          ({"elapsed": "yellow", "remaining": BLACK}, LIGHT))
    check("white beside a colour on white: the colour kept, the theme dark",
          colours.choose({"elapsed": "yellow", "remaining": BLACK}, LIGHT, "remaining", WHITE),
          ({"elapsed": "yellow", "remaining": WHITE}, DARK))
    check("a colour other than black or white: the theme kept",
          colours.choose({"elapsed": BLACK, "remaining": BLACK}, LIGHT, "elapsed", "green"),
          ({"elapsed": "green", "remaining": BLACK}, LIGHT))
    check("black chosen again where it is already: nothing changes",
          colours.choose({"elapsed": BLACK, "remaining": WHITE}, DARK, "elapsed", BLACK),
          ({"elapsed": BLACK, "remaining": WHITE}, DARK))
    check("red is not a choice", "red" in colours.CLOCK_COLOURS, False)
    for bad in [("elapsed", "red"), ("limit", WHITE)]:
        try:
            colours.choose(both_white, DARK, *bad)
            check(f"choose {bad!r} refused", "taken", "refused")
        except ValueError:
            check(f"choose {bad!r} refused", "refused", "refused")
    # The warning's colour: green beside white or black, else the complement of Remaining's.
    check("the complement turns the hue half round", (colours.complement("#ff0000"), colours.complement("#ffffff")), ("#00ffff", "#ffffff"))
    check("warning default beside white and black", (colours.warning_default(WHITE), colours.warning_default(BLACK)),
          (colours.WARNING_GREEN, colours.WARNING_GREEN))
    check("warning default beside orange: its complement", colours.warning_default("orange"),
          colours.complement(colours.CLOCK_COLOURS["orange"]))
    for timers in [both_white, {"elapsed": "yellow", "remaining": "orange"}, {"elapsed": "magenta", "remaining": "green"}]:
        choices = colours.warning_choices(timers)
        ruled_out = {colours.warning_default(timers["remaining"]), "#ff0000", "#ffffff", "#000000"} | {
            colours.CLOCK_COLOURS[name] for name in timers.values()}
        check(f"four warning choices, none ruled out, for {timers}", (len(choices), any(h in ruled_out for _, h in choices)), (4, False))
    check("the warning's colour: auto is the default", colours.warning_colour(colours.AUTO, both_white), colours.WARNING_GREEN)
    check("the warning's colour: a choice shown", colours.warning_colour("cyan", both_white), colours.WARNING_CANDIDATES["cyan"])
    check("the warning's colour: a choice no longer shown is the default",
          colours.warning_colour("yellow", {"elapsed": "yellow", "remaining": WHITE}), colours.WARNING_GREEN)

    # The session: the host's alone; what the pages are given to draw.
    reg = Registry(clock=Clock(), connected=lambda page: page in {"H", "V"})
    reg.create("PAINT", "H")
    reg.join("PAINT", "V", None)
    reg.set_colour("PAINT", "H", "elapsed", BLACK)
    look = reg.shown("PAINT").for_script()["look"]
    check("set_colour: by the rule; the look as drawn", (look["theme"], look["elapsed"], look["remaining"], look["names"]),
          (LIGHT, "#000000", "#000000", {"elapsed": BLACK, "remaining": BLACK}))
    for who, timer, colour, refusal in [("V", "elapsed", WHITE, Refused), ("H", "elapsed", "red", ValueError), ("H", "limit", WHITE, ValueError)]:
        try:
            reg.set_colour("PAINT", who, timer, colour)
            check(f"set_colour {who} {timer} {colour} refused", "set", "refused")
        except refusal:
            check(f"set_colour {who} {timer} {colour} refused", "refused", "refused")
    reg.control("PAINT", "H", "warning", 1.25)
    shown = reg.shown("PAINT")
    check("the warning to a tenth, in seconds for the script", (shown.warning, shown.for_script()["warning"]), (1.3, 78))
    reg.control("PAINT", "H", "warning", 0)
    check("a warning of 0: none for the script", reg.shown("PAINT").for_script()["warning"], None)
    reg.control("PAINT", "H", "warning", None)
    check("the warning blanked", reg.shown("PAINT").warning, None)
    buttons, chosen = reg.shown("PAINT").warning_buttons()
    check("the warning's buttons: A first and chosen, then four", ([name for name, _ in buttons][0], len(buttons), chosen), (colours.AUTO, 5, colours.AUTO))
    reg.set_warning_colour("PAINT", "H", "yellow")
    check("a warning colour chosen", reg.shown("PAINT").for_script()["look"]["warning"], colours.WARNING_CANDIDATES["yellow"])
    try:
        reg.set_warning_colour("PAINT", "H", "red")
        check("red refused for the warning", "set", "refused")
    except ValueError:
        check("red refused for the warning", "refused", "refused")
    reg.set_colour("PAINT", "H", "remaining", "yellow")
    shown = reg.shown("PAINT")
    check("Remaining turned the warning's colour: the warning back to A, its complement",
          (shown.warning_colour, shown.for_script()["look"]["warning"]), (colours.AUTO, colours.complement(colours.CLOCK_COLOURS["yellow"])))


def remember_checks() -> None:
    """A session's settings kept once it has gone, and in the store between runs (Terry, 2026-11-01, item 11)."""
    store = Path(tempfile.mkdtemp()) / "remembered.json"
    clock = Clock()
    on = {"H"}
    reg = Registry(clock=clock, connected=lambda page: page in on, store=store)
    reg.create("KEEP", "H", None, MINUTES)
    reg.control("KEEP", "H", "limit", 12.5)
    reg.control("KEEP", "H", "warning", 2)
    reg.set_size("KEEP", "H", "remaining", 250)
    reg.set_colour("KEEP", "H", "remaining", "yellow")
    reg.set_warning_colour("KEEP", "H", "cyan")
    reg.control("KEEP", "H", "start")
    clock.step(30)
    # Everyone leaves; the session is removed EXPIRE_AFTER_S later (any create or join removes the expired).
    reg.leave("KEEP", "H")
    on.clear()
    clock.step(EXPIRE_AFTER_S + 60)
    reg.create("OTHER", "X")
    check("the session gone", reg.exists("KEEP"), False)
    on.add("H2")
    _, _, notice = reg.create("KEEP", "H2", None, CLOCK)
    shown = reg.shown("KEEP")
    check("created again: its settings back (its way of showing the times too), its timer at zero",
          (shown.limit, shown.warning, shown.sizes["remaining"], shown.colours["remaining"], shown.warning_colour, shown.host_mode,
           shown.elapsed, shown.running, notice is not None),
          (12.5, 2.0, 250, "yellow", "cyan", MINUTES, 0.0, False, True))
    # A new run of the app: the settings read from the store.
    again = Registry(clock=Clock(), store=store)
    again.create("KEEP", "P")
    check("a new run: the settings from the file", (again.shown("KEEP").limit, again.shown("KEEP").colours["remaining"]), (12.5, "yellow"))
    _, _, notice = again.create("FRESH", "P2")
    check("an ID never used: the defaults, no notice", (again.shown("FRESH").limit, notice), (None, None))
    check("a random ID is never one used before", again.create(None, "P3")[0] not in ("KEEP", "FRESH"), True)
    # A spoilt file: said so, and nothing remembered; spoilt values left at their defaults, a spoilt ID dropped.
    store.write_text("{not json", encoding="utf-8")
    said = io.StringIO()
    with contextlib.redirect_stderr(said):
        spoilt = Registry(clock=Clock(), store=store)
    check("a spoilt file: a warning, nothing remembered", ("Warning:" in said.getvalue(), spoilt.remembered("KEEP")), (True, None))
    store.write_text(json.dumps({"KEEP": {"limit": "lots", "sizes": {"elapsed": 9999}, "colours": {"remaining": "red"}, "theme": "pink"},
                                 "bad id!": {}}), encoding="utf-8")
    partial = Registry(clock=Clock(), store=store)
    check("spoilt values at their defaults, a spoilt ID dropped", (partial.remembered("KEEP"), partial.remembered("bad id!")), (Settings(), None))
    # v1.2's file (the table of sessions alone, sizes on a scale half as fine): read at twice its sizes, within bounds.
    store.write_text(json.dumps({"OLD": {"limit": 3.0, "sizes": {"elapsed": 100, "remaining": 300}}}), encoding="utf-8")
    old = Registry(clock=Clock(), store=store)
    check("v1.2's file: its sizes twice, no larger than v1.2 drew (400)", (old.remembered("OLD").limit, old.remembered("OLD").sizes),
          (3.0, {"elapsed": 200, "remaining": 400}))
    old.create("NEW", "P")
    written = json.loads(store.read_text(encoding="utf-8"))
    check("written in this version's form", (written.get("format"), sorted(written.get("sessions", {}))), (2, ["NEW", "OLD"]))


class _GitHub(http.server.ThreadingHTTPServer):
    """A stand-in for the part of GitHub's API the gist store uses: an account's gists, one gist, make one, change one.

    ``fail`` answers that many requests with 500 first; a request with
    another token than ``token`` gets 401; ``calls`` lists each (method, path).
    """

    token = "good-token"

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _GitHubHandler)
        self.gists: dict[str, dict] = {}
        self.fail = 0
        self.calls: list[tuple[str, str]] = []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def api(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def content(self) -> dict | None:
        """The table in the app's gist, read; None if there is no such gist."""
        for gist in self.gists.values():
            if gist["description"] == GIST_DESCRIPTION:
                return json.loads(gist["files"][GIST_FILE]["content"])
        return None


class _GitHubHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # quiet
        pass

    def _answer(self, code: int, body: object) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve(self, method: str) -> None:
        hub: _GitHub = self.server  # type: ignore[assignment]
        hub.calls.append((method, self.path))
        if self.headers.get("Authorization") != f"Bearer {hub.token}":
            return self._answer(401, {"message": "Bad credentials"})
        if hub.fail > 0:
            hub.fail -= 1
            return self._answer(500, {"message": "a failure, as asked"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")
        path, _, query = self.path.partition("?")
        if method == "GET" and path == "/gists":
            page = int(dict(p.split("=") for p in query.split("&")).get("page", "1"))
            listed = [{"id": i, "description": g["description"]} for i, g in hub.gists.items()]
            return self._answer(200, listed[(page - 1) * 100:page * 100])
        if method == "POST" and path == "/gists":
            gist_id = f"g{len(hub.gists) + 1}"
            hub.gists[gist_id] = {"description": body["description"], "public": body["public"], "files": body["files"]}
            return self._answer(201, {"id": gist_id})
        gist = hub.gists.get(path.removeprefix("/gists/"))
        if gist is None:
            return self._answer(404, {"message": "Not Found"})
        if method == "PATCH":
            gist["files"].update(body["files"])
        return self._answer(200, {"files": {name: {**f, "truncated": False} for name, f in gist["files"].items()}})

    def do_GET(self) -> None:
        self._serve("GET")

    def do_POST(self) -> None:
        self._serve("POST")

    def do_PATCH(self) -> None:
        self._serve("PATCH")


def remote_checks() -> None:
    """v1.9: the settings kept in a gist as well, so that a Reboot (a new machine, its file gone) keeps them (Terry, 2026-11-03)."""
    hub = _GitHub()
    folder = Path(tempfile.mkdtemp())

    def gist(token: str = _GitHub.token) -> GistStore:
        return GistStore(token, api=hub.api, write_after=0.1, retry_after=(0.2,))

    # The store alone: no gist yet; the first write makes a secret one; writes close together make one.
    alone = gist()
    check("no gist yet: nothing to read", alone.load(), None)
    alone.save('{"n": 1}')
    alone.flush(5)
    made = [g for g in hub.gists.values() if g["description"] == GIST_DESCRIPTION]
    check("the first write makes one gist, secret, holding the table",
          (len(made), made[0]["public"] if made else None, hub.content()), (1, False, {"n": 1}))
    before = alone.writes
    for n in (2, 3, 4):
        alone.save(json.dumps({"n": n}))
    alone.flush(5)
    check("three saves close together: one write, the latest", (alone.writes - before, hub.content()), (1, {"n": 4}))
    check("read back", json.loads(gist().load() or "null"), {"n": 4})
    hub.gists.clear()

    # A host sets a session up and leaves; the app is rebooted (a new machine: a new, empty file); the next host
    # creating that ID starts from its settings.
    first = Registry(clock=Clock(), store=folder / "first.json")
    first.use_remote(gist())
    check("the registry keeps the gist", first.has_remote(), True)
    first.create("TALK7", "H")
    first.control("TALK7", "H", "limit", 7.5)
    first.control("TALK7", "H", "warning", 1.5)
    first.set_colour("TALK7", "H", "remaining", "orange")
    first.leave("TALK7", "H")
    first.remote_written(5)
    check("the gist holds the session's settings", (hub.content() or {}).get("sessions", {}).get("TALK7", {}).get("limit"), 7.5)
    rebooted = Registry(clock=Clock(), store=folder / "rebooted.json")
    check("after a Reboot, before the gist is read: nothing", rebooted.remembered("TALK7"), None)
    rebooted.use_remote(gist())
    _, _, notice = rebooted.create("TALK7", "H2")
    shown = rebooted.shown("TALK7")
    check("after a Reboot: created again from the gist's settings, with the notice",
          (shown.limit, shown.warning, shown.colours["remaining"], notice is not None), (7.5, 1.5, "orange", True))
    check("and the new machine's file has them too", "TALK7" in json.loads((folder / "rebooted.json").read_text(encoding="utf-8"))["sessions"], True)

    # The two tables merged: a session in both takes the newer one's; one in either alone is kept.
    rebooted.remote_written(5)
    newer = {"format": 2, "written": time.time() + 1000,
             "sessions": {"TALK7": {"limit": 9.0}, "LOCAL": {"limit": 4.0}}}
    (folder / "newer.json").write_text(json.dumps(newer), encoding="utf-8")
    merged = Registry(clock=Clock(), store=folder / "newer.json")
    merged.use_remote(gist())
    merged.remote_written(5)
    gist_now = (hub.content() or {}).get("sessions", {})
    check("the file newer: its settings win, the gist is brought up to them",
          (merged.remembered("TALK7").limit, merged.remembered("LOCAL").limit, gist_now.get("TALK7", {}).get("limit"),
           gist_now.get("LOCAL", {}).get("limit")), (9.0, 4.0, 9.0, 4.0))
    hub.gists[next(iter(hub.gists))]["files"][GIST_FILE]["content"] = json.dumps(
        {"format": 2, "written": time.time() + 2000, "sessions": {"ONLY": {"limit": 2.0}}})
    older = Registry(clock=Clock(), store=folder / "newer.json")
    older.use_remote(gist())
    older.remote_written(5)
    check("the gist newer: a session in the file alone is kept, and the gist given it",
          (older.remembered("ONLY").limit, older.remembered("TALK7").limit, sorted((hub.content() or {}).get("sessions", {}))),
          (2.0, 9.0, ["LOCAL", "ONLY", "TALK7"]))

    # The gist cannot be read: said so, nothing written to it (it would replace what is kept there); tried again
    # REMOTE_RETRY_S later, not sooner.
    clock = Clock()
    blind = Registry(clock=clock, store=folder / "blind.json")
    hub.fail = 1
    said = io.StringIO()
    with contextlib.redirect_stderr(said):
        blind.use_remote(gist())
    check("the gist unreadable: a warning, not kept, nothing written",
          ("Warning:" in said.getvalue(), blind.has_remote(), hub.calls[-1][0]), (True, False, "GET"))
    calls = len(hub.calls)
    clock.step(REMOTE_RETRY_S - 1)
    blind.use_remote(gist())
    check("not tried again sooner than REMOTE_RETRY_S", (len(hub.calls), blind.has_remote()), (calls, False))
    clock.step(2)
    blind.use_remote(gist())
    check("tried again after it: kept, its settings read", (blind.has_remote(), blind.remembered("ONLY").limit), (True, 2.0))

    # A write that fails is tried again; the latest table arrives.
    hub.fail = 1
    with contextlib.redirect_stderr(said):
        blind.create("RETRY", "P")
        blind.control("RETRY", "P", "limit", 3.0)
        blind.remote_written(5)
    check("a failed write tried again: the latest table arrives",
          (hub.content() or {}).get("sessions", {}).get("RETRY", {}).get("limit"), 3.0)

    # A wrong token: said so, not kept; a gist whose table is spoilt: said so, kept, and given this machine's table.
    said = io.StringIO()
    with contextlib.redirect_stderr(said):
        wrong = Registry(clock=Clock(), store=folder / "wrong.json")
        wrong.use_remote(gist("bad-token"))
    check("a wrong token: a warning, not kept", ("401" in said.getvalue(), wrong.has_remote()), (True, False))
    hub.gists[next(iter(hub.gists))]["files"][GIST_FILE]["content"] = "{not json"
    said = io.StringIO()
    with contextlib.redirect_stderr(said):
        mended = Registry(clock=Clock(), store=folder / "newer.json")
        mended.use_remote(gist())
        mended.remote_written(5)
    check("a spoilt gist: a warning, kept, and given the file's table",
          ("Warning:" in said.getvalue(), mended.has_remote(), "LOCAL" in (hub.content() or {}).get("sessions", {})), (True, True, True))
    hub.shutdown()


def buzzer_checks() -> None:
    """v1.10: the host's Buzzer, held: every page shows it; let go, or no word from the host's page for BUZZ_HOLD_S, it stops (Terry, 2026-11-03)."""
    clock = Clock()
    on = {"H", "V"}
    reg = Registry(clock=clock, connected=lambda page: page in on)
    reg.create("BZ", "H")
    reg.join("BZ", "V", None)
    check("not buzzing at first", reg.shown("BZ").buzzing, False)
    before = reg.shown("BZ").version
    reg.buzz("BZ", "H", True)
    check("held: buzzing, for the script too, and every page told (a new version)",
          (reg.shown("BZ").buzzing, reg.shown("BZ").for_script()["buzz"], reg.shown("BZ").version > before), (True, True, True))
    try:
        reg.buzz("BZ", "V", True)
        refused = False
    except Refused:
        refused = True
    check("a viewer cannot buzz", refused, True)
    clock.step(BUZZ_HOLD_S - 1)
    reg.buzz("BZ", "H", True)  # the host's page, still holding it, says so again
    clock.step(BUZZ_HOLD_S - 1)
    check("said again while held: still buzzing", reg.shown("BZ").buzzing, True)
    reg.buzz("BZ", "H", False)
    check("let go: stopped", reg.shown("BZ").buzzing, False)
    reg.buzz("BZ", "H", True)
    clock.step(BUZZ_HOLD_S + 0.5)
    check("no word from the host's page for BUZZ_HOLD_S: stopped by itself", reg.shown("BZ").buzzing, False)
    reg.buzz("BZ", "H", True)
    reg.leave("BZ", "H")
    check("the host leaves while holding it: stopped", reg.shown("BZ").buzzing, False)
    check("never remembered with the settings", "buzz" in json.dumps(asdict(reg.remembered("BZ"))), False)


def main() -> int:
    timer_checks()
    id_checks()
    session_checks()
    display_checks()
    tab_checks()
    colour_checks()
    remember_checks()
    remote_checks()
    buzzer_checks()
    for failure in FAILURES:
        print("FAIL", failure)
    print(f"{COUNT - len(FAILURES)} of {COUNT} checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
