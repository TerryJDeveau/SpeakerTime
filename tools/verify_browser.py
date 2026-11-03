"""The page driven in real browsers, a host and viewers each in a browser of their own: ``python tools/verify_browser.py``.

Starts the page on a free port (or tests ``--url``), then plays the
session's life through: waiting for a session not yet created, a taken ID,
the timers agreeing on every screen, reloads, the host's window closed and
"Assume Host Role?", a creator who comes back, Leave Session, the pop-out
window. Prints each check and exits 1 on any failure.

Needs Playwright (``pip install playwright``) and a Chromium: ``--chrome``
uses the installed Google Chrome (no download, as on Buddug), ``--executable``
a browser of your choice; with neither, Playwright's own (``playwright
install chromium``). ``--shots DIR`` keeps a screenshot of each screen.
AppTest cannot do this: it runs none of the page's script, which draws the
clock and keeps the cookies.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from speakertime.sessions import HOST_GRACE_S  # noqa: E402

FAILURES: list[str] = []

# The clock's three texts, read through its shadow root (a components v2 element).
_CLOCK_TEXTS = """() => {
  const host = [...document.querySelectorAll('*')].find(e => e.shadowRoot && e.shadowRoot.querySelector('.clock'));
  if (!host) return null;
  const r = host.shadowRoot;
  const t = (n) => r.querySelector('[data-v="' + n + '"]').textContent;
  return { elapsed: t('elapsed'), remaining: t('remaining'), limit: t('limit'),
           over: r.querySelector('[data-v="remaining"]').classList.contains('over') };
}"""

_CLICK_POP = """() => [...document.querySelectorAll('*')].find(e => e.shadowRoot && e.shadowRoot.querySelector('.clock'))
  .shadowRoot.querySelector('.pop').click()"""

_POP_LABEL = """() => [...document.querySelectorAll('*')].find(e => e.shadowRoot && e.shadowRoot.querySelector('.clock'))
  .shadowRoot.querySelector('.pop').textContent"""

# Check-ins counted for 4 s, with or without the pop-out's kick every 250 ms; and whether the clock was rebuilt meanwhile.
_CLOCK_ROOT = "[...document.querySelectorAll('*')].find(e => e.shadowRoot && e.shadowRoot.querySelector('.clock')).shadowRoot"

# The figures' sizes in px and the texts, on a panel (``root`` the clock's shadow root) or a pop-out (its document).
_FIGURES = """(root) => {
  const size = (n) => parseFloat(getComputedStyle(root.querySelector('[data-v="' + n + '"]')).fontSize);
  const text = (n) => root.querySelector('[data-v="' + n + '"]').textContent;
  return { elapsedPx: size('elapsed'), remainingPx: size('remaining'), elapsed: text('elapsed'),
           remaining: text('remaining'), mode: root.querySelector('.modes button.on')?.dataset.m ?? null };
}"""
_PANEL = f"() => ({_FIGURES})({_CLOCK_ROOT})"
_POPOUT = f"() => ({_FIGURES})(document)"

# The host lets go of a slider at ``percent`` (as a drag ends: input, then change).
_SLIDE = f"""([timer, percent]) => {{
  const slider = {_CLOCK_ROOT}.querySelector('input.size[data-t="' + timer + '"]');
  slider.value = String(percent);
  slider.dispatchEvent(new Event('input', {{ bubbles: true }}));
  slider.dispatchEvent(new Event('change', {{ bubbles: true }}));
}}"""
_SLIDERS = f"() => [...{_CLOCK_ROOT}.querySelectorAll('input.size')].filter(s => !s.hidden).map(s => s.dataset.t)"
_SWITCH = f"(mode) => {_CLOCK_ROOT}.querySelector('.modes button[data-m=\"' + mode + '\"]').click()"

_COUNT_CHECK_INS = """async (kicks) => {
  const h = window.speakerTime;
  let n = 0;
  const real = h.update.bind(h);
  h.update = (r) => { n++; real(r); };
  const find = () => [...document.querySelectorAll('*')].find(e => e.shadowRoot && e.shadowRoot.querySelector('.clock'));
  find().shadowRoot.querySelector('.clock').dataset.mark = 'kept';
  const timer = kicks ? setInterval(() => h.kick(), 250) : null;
  await new Promise((r) => setTimeout(r, 4000));
  if (timer) clearInterval(timer);
  h.update = real;
  return { checkIns: n, clockKept: find().shadowRoot.querySelector('.clock').dataset.mark === 'kept' };
}"""


def ok(what: str, condition: bool, detail: object = None) -> None:
    print(("ok   " if condition else "FAIL ") + what + ("" if detail is None else "  " + json.dumps(detail, ensure_ascii=False)))
    if not condition:
        FAILURES.append(what)


def clock(page: Page) -> dict | None:
    return page.evaluate(_CLOCK_TEXTS)


def id_box(page: Page):
    return page.get_by_role("textbox", name="Session ID")


def limit_box(page: Page):
    return page.get_by_role("spinbutton", name="Time Limit (minutes)")


def button(page: Page, name: str):
    return page.get_by_role("button", name=name, exact=True)


def settled(read, expected: object, seconds: float = 3.0) -> object:
    """``read()`` once it gives ``expected``, or its last answer after ``seconds``: for a value the page fills in just after a screen appears."""
    end = time.monotonic() + seconds
    while True:
        value = read()
        if value == expected or time.monotonic() > end:
            return value
        time.sleep(0.1)


def main_text(page: Page) -> str:
    return page.locator('[data-testid="stMain"]').inner_text()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server() -> tuple[subprocess.Popen, str]:
    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "streamlit_app.py", "--server.port", str(port), "--server.headless", "true"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://localhost:{port}/"
    for _ in range(60):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.5)
    time.sleep(1.0)
    return server, url


def run(url: str, launch: dict, shots: Path | None) -> None:
    global profile_url
    profile_url = url
    def shot(page: Page, name: str) -> None:
        if shots is not None:
            page.screenshot(path=str(shots / name))

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        contexts = {name: browser.new_context(viewport=size) for name, size in
                    [("A", {"width": 800, "height": 900}), ("B", {"width": 420, "height": 800}), ("C", {"width": 420, "height": 800})]}
        errors: list[str] = []

        def open_page(name: str) -> Page:
            page = contexts[name].new_page()
            page.on("pageerror", lambda error: errors.append(f"{name}: {error}"))
            page.goto(url)
            page.locator('[data-testid="stApp"]').wait_for()
            page.wait_for_timeout(1500)
            return page

        # 1. C connects to a session not yet created: pending, a spinner, Cancel.
        c = open_page("C")
        shot(c, "1-start.png")
        id_box(c).fill("Talk 1")
        button(c, "Connect to Session").click()
        c.get_by_text("Please wait, connection pending").wait_for()
        ok("pending screen shown", True)
        spin = c.locator(".speakertime-spinner").evaluate("(e) => getComputedStyle(e).animationName")
        ok("its spinner turns", spin == "speakertime-spin", spin)
        shot(c, "2-pending.png")
        button(c, "Cancel").click()
        button(c, "Connect to Session").wait_for()
        ok("Cancel: the start screen, the ID as read in the box", id_box(c).input_value() == "TALK1", id_box(c).input_value())
        button(c, "Connect to Session").click()
        c.get_by_text("Please wait, connection pending").wait_for()

        # 2. A creates " talk 1 " (spaces and case ignored): C joins by itself.
        a = open_page("A")
        id_box(a).fill(" talk 1 ")
        button(a, "Create New Session").click()
        a.get_by_text("You are the host.").wait_for()
        began = time.monotonic()
        c.get_by_text("You are viewing it.").wait_for(timeout=5000)
        ok("the waiting page joined by itself", True, f"{(time.monotonic() - began) * 1000:.0f} ms")

        # 3. B: the ID taken, one too long, one with nothing usable; then a viewer.
        b = open_page("B")
        id_box(b).fill("TALK1")
        button(b, "Create New Session").click()
        b.get_by_text("already has a host").wait_for()
        ok("Create New Session refused for an ID with a host", True)
        id_box(b).fill("abcdefghij klmnopqrstu")
        button(b, "Connect to Session").click()
        b.get_by_text("at most 20 characters").wait_for()
        ok("21 characters refused", True)
        id_box(b).fill("$$$")
        button(b, "Connect to Session").click()
        b.get_by_text("can be part of a session ID").wait_for()
        ok("an ID of nothing usable refused", True)
        id_box(b).fill("talk1")
        button(b, "Connect to Session").click()
        b.get_by_text("You are viewing it.").wait_for()

        # 4. The Time Limit to a tenth; Start; every screen agrees, and goes red past the limit.
        limit_box(a).fill("0.1")
        limit_box(a).press("Enter")
        a.wait_for_timeout(800)
        button(a, "Start").click()
        a.wait_for_timeout(3300)
        texts = [clock(a), clock(b), clock(c)]
        ok("3 s in: all agree", texts[0]["elapsed"] == "0:03" and texts[0] == texts[1] == texts[2], texts)
        a.wait_for_timeout(4000)
        texts = [clock(a), clock(b), clock(c)]
        ok("past the limit: negative and red everywhere",
           all(t["remaining"].startswith("−") and t["over"] for t in texts) and texts[0] == texts[1] == texts[2], texts)
        shot(a, "3-host.png")
        shot(b, "4-viewer.png")
        limit_box(a).fill("2.25")
        limit_box(a).press("Enter")
        a.wait_for_timeout(800)
        rounded = settled(lambda: limit_box(a).input_value(), "2.3")
        ok("2.25 minutes kept as 2.3", rounded == "2.3", rounded)
        a.wait_for_timeout(1200)
        ok("a new limit reaches the viewers", clock(b)["limit"] == "2:18", clock(b))

        # 5. The host reloads: the host again by the cookie, with nothing typed; nobody saw a hostless moment.
        a.reload()
        a.get_by_text("You are the host.").wait_for(timeout=8000)
        ok("a reloaded host is the host again", True)
        restored = settled(lambda: limit_box(a).input_value(), "2.3")
        ok("its Time Limit box as it was", restored == "2.3", restored)
        a.wait_for_timeout(1500)
        texts = [clock(a), clock(b), clock(c)]
        ok("all agree after the reload", texts[0] == texts[1] == texts[2], texts)
        ok("no viewer was offered the host's role", "Assume Host Role" not in main_text(b))

        # 6. A viewer reloads: a viewer again by itself.
        c.reload()
        c.get_by_text("You are viewing it.").wait_for(timeout=8000)
        ok("a reloaded viewer reconnects", True)

        # 7. The host's window closes: after the grace, the viewers are offered the role; the timers run on.
        a.close()
        closed = time.monotonic()
        b.get_by_text("Assume Host Role?").wait_for(timeout=(HOST_GRACE_S + 10) * 1000)
        ok("the offer comes after the grace", time.monotonic() - closed >= HOST_GRACE_S - 1.5,
           f"{time.monotonic() - closed:.1f} s")
        before = clock(b)["elapsed"]
        b.wait_for_timeout(2100)
        ok("the timers run on without a host", clock(b)["elapsed"] != before, [before, clock(b)["elapsed"]])
        shot(b, "5-offer.png")
        button(b, "No").click()
        b.get_by_text("You answered No").wait_for()
        b.wait_for_timeout(2500)
        ok("the offer stays after No", "Assume Host Role?" in main_text(b))
        button(c, "Yes").click()
        c.get_by_text("You are the host.").wait_for(timeout=5000)
        ok("Yes: the host", True)
        b.wait_for_timeout(2500)
        ok("the offer goes once a host connects", "Assume Host Role?" not in main_text(b))

        # 8. The creator comes back while C hosts: a viewer (C's Yes made a new host token).
        a = open_page("A")
        a.get_by_text("You are viewing it.").wait_for(timeout=8000)
        ok("a creator who comes back while another hosts views", True)
        button(c, "Stop").click()
        a.wait_for_timeout(2200)
        first = [clock(a), clock(b), clock(c)]
        a.wait_for_timeout(2000)
        later = [clock(a), clock(b), clock(c)]
        ok("Stop: all agree and hold", first == later and later[0] == later[1] == later[2], later)
        button(c, "Reset").click()
        a.wait_for_timeout(2200)
        ok("Reset reaches the viewers", clock(a)["elapsed"] == "0:00", clock(a))

        # 9. Leave Session forgets: a reload then lands on the start screen.
        button(b, "Leave Session").click()
        button(b, "Connect to Session").wait_for()
        b.reload()
        button(b, "Connect to Session").wait_for(timeout=8000)
        b.wait_for_timeout(1500)
        ok("after Leave Session a reload does not reconnect", "Reconnected" not in main_text(b))

        # 10. The pop-out: a window of its own, ticking, following a Stop.
        button(c, "Start").click()
        c.wait_for_timeout(1200)
        try:
            with contexts["C"].expect_page(timeout=5000) as popped:
                c.evaluate(_CLICK_POP)
            pip = popped.value
        except Exception as error:  # noqa: BLE001 - reported as the check's failure
            pip = None
            ok("the pop-out opens", False, str(error))
        if pip is not None:
            ok("the pop-out opens", True)
            c.wait_for_timeout(1500)
            first = pip.locator('[data-v="elapsed"]').text_content()
            c.wait_for_timeout(2000)
            later = pip.locator('[data-v="elapsed"]').text_content()
            ok("the pop-out ticks", first != later, [first, later])
            shot(pip, "6-popout.png")
            button(c, "Stop").click()
            c.wait_for_timeout(1500)
            first = pip.locator('[data-v="elapsed"]').text_content()
            c.wait_for_timeout(1500)
            ok("the pop-out follows a Stop",
               first == pip.locator('[data-v="elapsed"]').text_content() == clock(c)["elapsed"], [first, clock(c)])
            ok("its button now closes it", c.evaluate(_POP_LABEL) == "Close the pop-out")

            # 11. The pop-out's kick brings check-ins (the page's own timer may be slowed while hidden),
            # rerunning the fragment only: the clock is not rebuilt. Stopped, every check-in still arrives.
            alone = c.evaluate(_COUNT_CHECK_INS, False)
            kicked = c.evaluate(_COUNT_CHECK_INS, True)
            ok("stopped, each check-in reaches the script (about 4 in 4 s)", 3 <= alone["checkIns"] <= 6 and alone["clockKept"], alone)
            ok("the kick brings more, the clock kept", kicked["checkIns"] > alone["checkIns"] + 3 and kicked["clockKept"], kicked)

            sizes_and_modes(a, b, c, pip)
        windows_of_one_browser(browser, errors)

        ok("no script errors on any page", not errors, errors)
        browser.close()


_HAS_CLOCK = "() => [...document.querySelectorAll('*')].some(e => e.shadowRoot && e.shadowRoot.querySelector('.clock'))"


def on_clock(page: Page, script: str, arg: object = None) -> object:
    """Run ``script`` on the page's clock once it is there: a run of the page rebuilds the clock, which is missing for a moment."""
    page.wait_for_function(_HAS_CLOCK, polling=100, timeout=8000)  # timed: a page behind a pop-out may get no animation frames
    return page.evaluate(script, arg)


def mode_of(page: Page) -> str | None:
    return on_clock(page, _PANEL)["mode"]


def sizes_and_modes(a: Page, b: Page, c: Page, pip: Page) -> None:
    """12-13. The host's sliders reach every panel and pop-out; each panel's and pop-out's switch is its own (2026-11-01).

    On entry C hosts TALK1 with its pop-out open, A views it, B is on the start screen.
    """
    # 12. Sizes: the host's alone; Elapsed set to 50 % halves it on a viewer's panel and in the pop-out, Remaining kept.
    ok("the host has a slider beside each timer", on_clock(c, _SLIDERS) == ["elapsed", "remaining"], on_clock(c, _SLIDERS))
    ok("a viewer has none", on_clock(a, _SLIDERS) == [], on_clock(a, _SLIDERS))
    before, pip_before = on_clock(a, _PANEL), pip.evaluate(_POPOUT)
    on_clock(c, _SLIDE, ["elapsed", 50])
    c.wait_for_timeout(2500)
    after, pip_after = on_clock(a, _PANEL), pip.evaluate(_POPOUT)
    ratio = after["elapsedPx"] / before["elapsedPx"]
    ok("Elapsed at 50 % on a viewer's panel, Remaining kept", 0.48 <= ratio <= 0.52 and after["remainingPx"] == before["remainingPx"],
       [before, after])
    pip_ratio = pip_after["elapsedPx"] / pip_before["elapsedPx"]
    ok("and in the pop-out", 0.48 <= pip_ratio <= 0.52, [pip_before, pip_after])
    on_clock(c, _SLIDE, ["remaining", 150])
    c.wait_for_timeout(2500)
    ok("Remaining at 150 % on a viewer's panel",
       1.3 <= on_clock(a, _PANEL)["remainingPx"] / before["remainingPx"] <= 1.52, [before, on_clock(a, _PANEL)])

    # 13. Ways of showing the times. The host's switch changes its own panel only, and not its pop-out.
    ok("all start at H:MM:SS", (mode_of(c), mode_of(a), pip.evaluate(_POPOUT)["mode"]) == ("clock", "clock", "clock"))
    on_clock(c, _SWITCH, "minutes")
    c.wait_for_timeout(2200)
    figures = on_clock(c, _PANEL)
    ok("the host's panel in minutes", figures["mode"] == "minutes" and "." in figures["elapsed"] and ":" not in figures["remaining"], figures)
    ok("a viewer's panel and the pop-out keep theirs", (mode_of(a), pip.evaluate(_POPOUT)["mode"]) == ("clock", "clock"))
    # A page that opens takes the host's.
    id_box(b).fill("TALK1")
    button(b, "Connect to Session").click()
    b.get_by_text("You are viewing it.").wait_for()
    ok("a page opening takes the host's way", settled(lambda: mode_of(b), "minutes") == "minutes", mode_of(b))
    # A viewer's switch is its own; the host changing its own later changes nobody else's.
    on_clock(a, _SWITCH, "minutes")
    on_clock(c, _SWITCH, "clock")
    c.wait_for_timeout(2200)
    ok("viewers keep their own when the host switches", (mode_of(a), mode_of(b), mode_of(c)) == ("minutes", "minutes", "clock"),
       [mode_of(a), mode_of(b), mode_of(c)])
    # The pop-out's switch is its own, both ways.
    pip.locator('.modes button[data-m="minutes"]').click()
    c.wait_for_timeout(500)
    ok("the pop-out's switch leaves its panel alone", (pip.evaluate(_POPOUT)["mode"], mode_of(c)) == ("minutes", "clock"))
    ok("the pop-out in minutes", "." in pip.evaluate(_POPOUT)["elapsed"], pip.evaluate(_POPOUT))
    # A host connecting (here the host's page reloaded, its panel at H:MM:SS): every panel takes the host's way again.
    c.reload()
    c.get_by_text("You are the host.").wait_for(timeout=8000)
    after_reload = [settled(lambda: mode_of(p_), "clock", 4.0) for p_ in (a, b)]
    ok("a host connecting: every viewer takes the host's way", after_reload == ["clock", "clock"], after_reload)
    ok("the reloaded host's sizes kept", on_clock(c, f"() => [...{_CLOCK_ROOT}.querySelectorAll('input.size')].map(s => s.value)") == ["50", "150"])
    # A viewer's pop-out starts at its panel's way.
    on_clock(a, _SWITCH, "minutes")
    a.wait_for_timeout(1200)
    with a.context.expect_page(timeout=5000) as popped:
        on_clock(a, _CLICK_POP)
    viewer_pip = popped.value
    a.wait_for_timeout(800)
    ok("a pop-out starts at its panel's way", viewer_pip.evaluate(_POPOUT)["mode"] == "minutes", viewer_pip.evaluate(_POPOUT))
    on_clock(a, _SWITCH, "clock")
    a.wait_for_timeout(1500)
    ok("then keeps its own", (viewer_pip.evaluate(_POPOUT)["mode"], mode_of(a)) == ("minutes", "clock"))


def windows_of_one_browser(browser, errors: list[str]) -> None:
    """14. Several sessions in one browser (its windows share the cookies): each window goes back to its own (2026-11-01)."""
    profile = browser.new_context(viewport={"width": 700, "height": 800})

    def window() -> Page:
        page = profile.new_page()
        page.on("pageerror", lambda error: errors.append(f"window: {error}"))
        page.goto(profile_url)
        page.locator('[data-testid="stApp"]').wait_for()
        page.wait_for_timeout(2000)
        return page

    def where(page: Page) -> str:
        line = next((line for line in main_text(page).splitlines() if line.startswith("Session ID:")), "start screen")
        return " ".join(line.split())

    w1 = window()
    id_box(w1).fill("ALPHA")
    button(w1, "Create New Session").click()
    w1.get_by_text("You are the host.").wait_for()
    w2 = window()
    ok("a second window opens on the start screen", where(w2) == "start screen", where(w2))
    id_box(w2).fill("BRAVO")
    button(w2, "Create New Session").click()
    w2.get_by_text("You are the host.").wait_for()
    w3 = window()
    ok("a third: the start screen, no Rejoin (both open here)",
       where(w3) == "start screen" and "Rejoin" not in main_text(w3), where(w3))
    w3.close()
    for page, sid in ((w1, "ALPHA"), (w2, "BRAVO")):
        page.reload()
        page.get_by_text("You are the host.").wait_for(timeout=8000)
        ok(f"reloaded, the window hosting {sid} hosts {sid} again", where(page) == f"Session ID: {sid} You are the host.", where(page))
    w1.close()
    w4 = window()
    ok("ALPHA's window closed, a new one: the host of ALPHA by itself", where(w4) == "Session ID: ALPHA You are the host.", where(w4))
    w2.close()
    w4.close()
    w5 = window()
    labels = sorted(b.text_content().strip() for b in w5.get_by_role("button").all() if (b.text_content() or "").startswith("Rejoin"))
    ok("both closed, a new window: a Rejoin button for each", where(w5) == "start screen" and labels == ["Rejoin ALPHA as host", "Rejoin BRAVO as host"],
       [where(w5), labels])
    button(w5, "Rejoin BRAVO as host").click()
    w5.get_by_text("You are the host.").wait_for(timeout=5000)
    ok("Rejoin: the host of BRAVO", where(w5) == "Session ID: BRAVO You are the host.", where(w5))
    profile.close()

    # 15. A host and a viewer of one session in two tabs of one browser (Terry, 2026-11-01, item 3):
    # the host's tab reloaded stays the host, though the viewer's tab holds the same cookies.
    profile = browser.new_context(viewport={"width": 700, "height": 800})
    host = window()
    id_box(host).fill("DELTA")
    button(host, "Create New Session").click()
    host.get_by_text("You are the host.").wait_for()
    viewer = window()
    id_box(viewer).fill("DELTA")
    button(viewer, "Connect to Session").click()
    viewer.get_by_text("You are viewing it.").wait_for(timeout=5000)
    for round_ in (1, 2):
        host.reload()
        host.get_by_text("You are the host.").wait_for(timeout=8000)
        host.wait_for_timeout(2500)
        ok(f"two tabs, the host's reloaded ({round_}): host and viewer as they were",
           (where(host), where(viewer)) == ("Session ID: DELTA You are the host.", "Session ID: DELTA You are viewing it."),
           [where(host), where(viewer)])
    profile.close()


profile_url = ""
"""The page's address, for windows_of_one_browser (set by run)."""


def main() -> int:
    # The details print the figures' minus sign (U+2212), which Windows' console code page (cp1252) cannot encode.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", help="test this running page instead of starting one")
    parser.add_argument("--chrome", action="store_true", help="use the installed Google Chrome")
    parser.add_argument("--executable", help="the browser to use")
    parser.add_argument("--headed", action="store_true", help="show the browsers")
    parser.add_argument("--shots", type=Path, help="keep a screenshot of each screen in this folder")
    args = parser.parse_args()
    launch: dict = {"headless": not args.headed}
    if args.chrome:
        launch["channel"] = "chrome"
    if args.executable:
        launch["executable_path"] = args.executable
    if args.shots:
        args.shots.mkdir(parents=True, exist_ok=True)
    server = None
    url = args.url
    if url is None:
        server, url = start_server()
    try:
        run(url, launch, args.shots)
    finally:
        if server is not None:
            server.terminate()
            server.wait(timeout=10)
    print(f"FAILED: {'; '.join(FAILURES)}" if FAILURES else "All passed.")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
