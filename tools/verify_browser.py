"""The page driven in real browsers, a host and viewers each in a browser of their own: ``python tools/verify_browser.py``.

Starts the page on a free port (or tests ``--url``), then plays the
session's life through: waiting for a session not yet created, a taken ID,
the timers agreeing on every screen, reloads, the host's window closed and
"Assume Host Role?", a creator who comes back, Leave Session, the pop-out
window, the pop-ups offered while another tab of the browser has it, and a
tab holding an earlier version's state.
Prints each check and exits 1 on any failure.

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
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from speakertime.colours import CLOCK_COLOURS, WARNING_CANDIDATES, complement  # noqa: E402
from speakertime.sessions import HOST_GRACE_S  # noqa: E402

FAILURES: list[str] = []

# The clock's three texts, read through its shadow root (a components v2 element).
_CLOCK_TEXTS = """() => {
  const host = [...document.querySelectorAll('*')].find(e => e.shadowRoot && e.shadowRoot.querySelector('.clock'));
  if (!host) return null;
  const r = host.shadowRoot;
  const t = (n) => r.querySelector('[data-v="' + n + '"]').textContent;
  const value = r.querySelector('[data-v="remaining"]');
  return { elapsed: t('elapsed'), remaining: t('remaining'), limit: t('limit'), over: value.classList.contains('over'),
           blink: value.classList.contains('blink'), label: r.querySelector('[data-l="remaining"]').textContent };
}"""

# The clock's own rules (web._HUB_JS, texts) at a reading set by hand: [elapsed s, limit s or null, mode] each.
_TEXTS_AT = """(cases) => cases.map(([elapsed, limit, mode]) => {
  const h = Object.create(window.speakerTime);
  h.base = { sid: 'x', elapsed, running: false, limit, version: 0, sizes: {}, at: performance.now() };
  const t = h.texts(mode);
  return [t.remaining, t.out, t.over];
})"""

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
# A pop-out's layout: each timer's figures as a share of its height, whether the Time Limit stands beside the timers
# (not under them), and any label, figures or switch that leaves the window.
_LAYOUT = """() => {
  const H = innerHeight, W = innerWidth;
  const shown = [...document.querySelectorAll('.label, .value, .modes')].filter(e => e.getClientRects().length);
  const outside = shown.filter(e => { const b = e.getBoundingClientRect();
    return b.top < -0.5 || b.bottom > H + 0.5 || b.left < -0.5 || b.right > W + 0.5; }).map(e => e.dataset.v ?? e.dataset.l ?? 'modes');
  const share = (n) => +(parseFloat(getComputedStyle(document.querySelector('[data-v="' + n + '"]')).fontSize) / H).toFixed(3);
  const row = (n) => document.querySelector('.row[data-r="' + n + '"]').getBoundingClientRect();
  return { size: [W, H], elapsed: share('elapsed'), remaining: share('remaining'),
           beside: row('limit').top < row('remaining').bottom, outside };
}"""

# The host lets go of a slider at ``percent`` (as a drag ends: input, then change).
_SLIDE = f"""([timer, percent]) => {{
  const slider = {_CLOCK_ROOT}.querySelector('input.size[data-t="' + timer + '"]');
  slider.value = String(percent);
  slider.dispatchEvent(new Event('input', {{ bubbles: true }}));
  slider.dispatchEvent(new Event('change', {{ bubbles: true }}));
}}"""
_SLIDERS = f"() => [...{_CLOCK_ROOT}.querySelectorAll('input.size')].filter(s => !s.hidden).map(s => s.dataset.t)"
_SWITCH = f"(mode) => {_CLOCK_ROOT}.querySelector('.modes button[data-m=\"' + mode + '\"]').click()"
# The clock's width on the page, and the widest of the page's other elements (web._PAGE_STYLE).
_WIDTHS = """() => {
  const block = document.querySelector('[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"]');
  const kids = [...block.children];
  const width = (e) => Math.round(e.getBoundingClientRect().width);
  return { clock: width(kids.find(k => k.classList.contains('st-key-clock'))),
           others: kids.filter(k => !k.classList.contains('st-key-clock')).map(width) };
}"""

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
    # The checks' own file of remembered settings, never the app's (sessions.REMEMBER_FILE).
    env = dict(os.environ, SPEAKERTIME_REMEMBER=str(Path(tempfile.mkdtemp()) / "remembered.json"))
    server = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "streamlit_app.py", "--server.port", str(port), "--server.headless", "true"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
        ok("3 s in: Time Remaining, plain", (texts[0]["label"], texts[0]["over"], texts[0]["blink"]) == ("Time Remaining", False, False))
        a.wait_for_timeout(4000)
        texts = [clock(a), clock(b), clock(c)]
        ok("past the limit: negative, red and blinking everywhere, the label Overtime",
           all(t["remaining"].startswith("−") and t["over"] and t["blink"] and t["label"] == "Overtime" for t in texts)
           and texts[0] == texts[1] == texts[2], texts)
        # Zero and below by the figures as shown (Terry, 2026-11-01: red and blinking at zero, Overtime only below it).
        rules = a.evaluate(_TEXTS_AT, [[5.5, 6, "clock"], [6.2, 6, "clock"], [7, 6, "clock"], [5, 6, "minutes"],
                                       [6, 6, "minutes"], [11.9, 6, "minutes"], [12, 6, "minutes"], [100, None, "clock"]])
        ok("out at zero, over below it, in both ways; never out with no limit", rules == [
            ["0:01", False, False], ["0:00", True, False], ["−0:01", True, True], ["0.1", False, False],
            ["0.0", True, False], ["0.0", True, False], ["−0.1", True, True], ["0:00", False, False]], rules)
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

            sizes_and_modes(a, b, c, pip, shot)
            colours_and_warning(a, c)
        windows_of_one_browser(browser, errors)
        popups_of_one_browser(browser, errors, shot)

        ok("no script errors on any page", not errors, errors)
        browser.close()


_HAS_CLOCK = "() => [...document.querySelectorAll('*')].some(e => e.shadowRoot && e.shadowRoot.querySelector('.clock'))"


def on_clock(page: Page, script: str, arg: object = None) -> object:
    """Run ``script`` on the page's clock once it is there: a run of the page rebuilds the clock, which is missing for a moment."""
    page.wait_for_function(_HAS_CLOCK, polling=100, timeout=8000)  # timed: a page behind a pop-out may get no animation frames
    return page.evaluate(script, arg)


def mode_of(page: Page) -> str | None:
    return on_clock(page, _PANEL)["mode"]


def wide_and_low(c: Page, pip: Page, shot) -> None:
    """12b. A pop-out at least 5:2: the Time Limit and the switch beside the timers, 400 at 75 % of the height (v1.7).

    Terry, 2026-11-03, taking the option offered with v1.6 (54 % before, the
    Time Limit and the switch under the timers). On entry C hosts TALK1 with
    Elapsed at 100 and Remaining at 400, and leaves so.
    """
    size = pip.evaluate("() => [innerWidth, innerHeight]")

    def laid_out(width: int, height: int) -> dict:
        pip.set_viewport_size({"width": width, "height": height})
        pip.wait_for_timeout(300)
        return pip.evaluate(_LAYOUT)

    on_clock(c, _SLIDE, ["elapsed", 25])  # the smallest, so that Remaining at 400 leaves room for everything else
    c.wait_for_timeout(2500)
    wide, stacked = laid_out(1200, 300), laid_out(600, 300)
    ok("a pop-out of 1200 by 300: the Time Limit beside the timers, Remaining at 400 75 % of its height, all inside",
       wide["beside"] and 0.74 <= wide["remaining"] <= 0.76 and not wide["outside"], wide)
    ok("one of 600 by 300: under them as before, 54 %, all inside",
       not stacked["beside"] and 0.53 <= stacked["remaining"] <= 0.55 and not stacked["outside"], stacked)
    for timer in ("elapsed", "remaining"):
        on_clock(c, _SLIDE, [timer, 200])
        c.wait_for_timeout(2500)
    default = laid_out(1200, 300)
    ok("at the default, 200 each: 37.5 % of its height each, all inside",
       0.37 <= default["elapsed"] <= 0.38 and 0.37 <= default["remaining"] <= 0.38 and not default["outside"], default)
    shot(pip, "6b-popout-wide.png")
    pip.set_viewport_size({"width": size[0], "height": size[1]})
    for timer, percent in (("elapsed", 100), ("remaining", 400)):
        on_clock(c, _SLIDE, [timer, percent])
        c.wait_for_timeout(2500)


def sizes_and_modes(a: Page, b: Page, c: Page, pip: Page, shot) -> None:
    """12-13. The host's sliders reach every panel and pop-out; each panel's and pop-out's switch is its own (2026-11-01).

    On entry C hosts TALK1 with its pop-out open, A views it, B is on the start screen.
    """
    # 12. Sizes: the host's alone; Elapsed set to 100 (half the default 200) halves it on a viewer's panel and in the
    # pop-out, Remaining kept; Remaining at 300 and 400 grows by each step: the whole range takes effect (Terry, 2026-11-03).
    ok("the host has a slider beside each timer", on_clock(c, _SLIDERS) == ["elapsed", "remaining"], on_clock(c, _SLIDERS))
    ok("a viewer has none", on_clock(a, _SLIDERS) == [], on_clock(a, _SLIDERS))
    maxima = on_clock(c, f"() => [...{_CLOCK_ROOT}.querySelectorAll('input.size')].map(s => [s.value, s.max])")
    ok("the sliders start at 200 and go to 800", maxima == [["200", "800"], ["200", "800"]], maxima)
    before, pip_before = on_clock(a, _PANEL), pip.evaluate(_POPOUT)
    on_clock(c, _SLIDE, ["elapsed", 100])
    c.wait_for_timeout(2500)
    after, pip_after = on_clock(a, _PANEL), pip.evaluate(_POPOUT)
    ratio = after["elapsedPx"] / before["elapsedPx"]
    ok("Elapsed at 100 on a viewer's panel: half, Remaining kept", 0.48 <= ratio <= 0.52 and after["remainingPx"] == before["remainingPx"],
       [before, after])
    pip_ratio = pip_after["elapsedPx"] / pip_before["elapsedPx"]
    ok("and in the pop-out", 0.48 <= pip_ratio <= 0.52, [pip_before, pip_after])
    on_clock(c, _SLIDE, ["remaining", 300])
    c.wait_for_timeout(2500)
    at_300, pip_300 = on_clock(a, _PANEL), pip.evaluate(_POPOUT)
    ok("Remaining at 300 on a viewer's panel: half as large again", 1.46 <= at_300["remainingPx"] / before["remainingPx"] <= 1.54,
       [before, at_300])
    on_clock(c, _SLIDE, ["remaining", 400])
    c.wait_for_timeout(2500)
    at_400, pip_400 = on_clock(a, _PANEL), pip.evaluate(_POPOUT)
    ok("Remaining at 400: a third larger again, on a viewer's panel and in the pop-out (not stopped short)",
       1.30 <= at_400["remainingPx"] / at_300["remainingPx"] <= 1.36 and 1.30 <= pip_400["remainingPx"] / pip_300["remainingPx"] <= 1.36,
       [at_300, at_400, pip_300, pip_400])
    # v1.6 (Terry, 2026-11-03: "about double" the largest): 800 twice 400's size where the panel is wide enough, the
    # page's other elements kept to their column; the pop-out stays at 400's size. Then back to 400 for what follows.
    a.set_viewport_size({"width": 1700, "height": 900})
    a.wait_for_timeout(800)
    wide_400 = on_clock(a, _PANEL)
    on_clock(c, _SLIDE, ["remaining", 800])
    c.wait_for_timeout(2500)
    wide_800, pip_800 = on_clock(a, _PANEL), pip.evaluate(_POPOUT)
    widths = a.evaluate(_WIDTHS)
    ok("Remaining at 800 on a wide panel: twice 400's; the pop-out stays at 400's",
       1.9 <= wide_800["remainingPx"] / wide_400["remainingPx"] <= 2.1 and abs(pip_800["remainingPx"] - pip_400["remainingPx"]) < 0.5,
       [wide_400, wide_800, pip_400, pip_800])
    ok("the clock spans the wide page, everything else keeps its column (704 px)",
       widths["clock"] > 1400 and max(widths["others"]) <= 704, widths)
    on_clock(c, _SLIDE, ["remaining", 400])
    c.wait_for_timeout(2500)
    a.set_viewport_size({"width": 800, "height": 900})
    wide_and_low(c, pip, shot)

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
    ok("the reloaded host's sizes kept", on_clock(c, f"() => [...{_CLOCK_ROOT}.querySelectorAll('input.size')].map(s => s.value)") == ["100", "400"])
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


def rgb(hex_colour: str) -> str:
    """A "#rrggbb" colour as getComputedStyle gives it."""
    return "rgb({}, {}, {})".format(*(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5)))


# The panel's colours as drawn: its background, its label's text, and each figure's colour, with Remaining's classes.
_PAINT = f"""() => {{
  const r = {_CLOCK_ROOT};
  const css = (sel, prop) => getComputedStyle(r.querySelector(sel))[prop];
  const warning = r.querySelector('[data-w]');
  return {{ panel: css('.clock', 'backgroundColor'), label: css('[data-l="elapsed"]', 'color'),
           elapsed: css('[data-v="elapsed"]', 'color'), remaining: css('[data-v="remaining"]', 'color'),
           warn: r.querySelector('[data-v="remaining"]').classList.contains('warn'),
           warningShown: !warning.hidden, warningText: r.querySelector('[data-v="warning"]').textContent,
           warningColour: css('[data-v="warning"]', 'color'),
           swatches: [...r.querySelectorAll('.swatches')].filter(s => !s.hidden).map(s => s.dataset.t) }};
}}"""
_SWATCH = f"([timer, colour]) => {_CLOCK_ROOT}.querySelector('.swatches[data-t=\"' + timer + '\"] button[data-c=\"' + colour + '\"]').click()"
_PICKS = """() => [...[...document.querySelectorAll('*')].find(e => e.shadowRoot && e.shadowRoot.querySelector('.picks'))
  .shadowRoot.querySelectorAll('.picks button')].map(b => [b.dataset.c, b.classList.contains('on')])"""
_PICK = """(name) => [...document.querySelectorAll('*')].find(e => e.shadowRoot && e.shadowRoot.querySelector('.picks'))
  .shadowRoot.querySelector('.picks button[data-c="' + name + '"]').click()"""


def colours_and_warning(a: Page, c: Page) -> None:
    """16-17. The host's colour buttons and Terry's rule for black and white; the Time Left Warning and its colour (2026-11-01).

    On entry C hosts TALK1 (Time Limit 2.3 minutes, stopped part way), A views it.
    """
    ok("colour buttons beside the host's sliders, none for a viewer",
       (on_clock(c, _PAINT)["swatches"], on_clock(a, _PAINT)["swatches"]) == (["elapsed", "remaining"], []))
    on_clock(c, _SWATCH, ["elapsed", "black"])
    c.wait_for_timeout(2500)
    seen = on_clock(a, _PAINT)
    ok("black for Elapsed: both timers black, the panel white, its text black, on a viewer's panel",
       (seen["panel"], seen["label"], seen["elapsed"], seen["remaining"]) == (rgb("#ffffff"), rgb("#000000"), rgb("#000000"), rgb("#000000")),
       seen)
    on_clock(c, _SWATCH, ["remaining", "orange"])
    c.wait_for_timeout(2500)
    seen = on_clock(a, _PAINT)
    ok("orange for Remaining: Elapsed stays black, the panel white", (seen["panel"], seen["elapsed"], seen["remaining"])
       == (rgb("#ffffff"), rgb("#000000"), rgb(CLOCK_COLOURS["orange"])), seen)
    on_clock(c, _SWATCH, ["elapsed", "white"])
    c.wait_for_timeout(2500)
    seen = on_clock(a, _PAINT)
    ok("white for Elapsed: the panel black, its text white; Remaining stays orange",
       (seen["panel"], seen["label"], seen["elapsed"], seen["remaining"])
       == (rgb("#000000"), rgb("#ffffff"), rgb("#ffffff"), rgb(CLOCK_COLOURS["orange"])), seen)

    # 17. The warning: none shown while blank; set to the whole limit, Remaining is within it at once.
    ok("no warning set: none shown", on_clock(a, _PAINT)["warningShown"] is False)
    box = c.get_by_role("spinbutton", name="Time Left Warning (minutes)")
    box.fill("2.3")
    box.press("Enter")
    c.wait_for_timeout(2500)
    seen = on_clock(a, _PAINT)
    ok("a warning set: shown beside the limit, in the colour opposite orange; Remaining in it",
       (seen["warningShown"], seen["warningText"], seen["warningColour"], seen["warn"], seen["remaining"])
       == (True, "2:18", rgb(complement(CLOCK_COLOURS["orange"])), True, rgb(complement(CLOCK_COLOURS["orange"]))), seen)
    picks = settled(lambda: c.evaluate(_PICKS)[0], ["auto", True])
    ok("the warning's colour buttons: A chosen, then four", picks == ["auto", True] and len(c.evaluate(_PICKS)) == 5, c.evaluate(_PICKS))
    c.evaluate(_PICK, "cyan")
    c.wait_for_timeout(2500)
    seen = on_clock(a, _PAINT)
    ok("cyan chosen: the warning and Remaining in cyan", (seen["warningColour"], seen["remaining"])
       == (rgb(WARNING_CANDIDATES["cyan"]),) * 2, seen)
    on_clock(c, _SWATCH, ["remaining", "white"])
    c.wait_for_timeout(2500)
    seen = on_clock(a, _PAINT)
    ok("Remaining white again: cyan still one of the choices, so the warning stays cyan",
       seen["warningColour"] == rgb(WARNING_CANDIDATES["cyan"]), seen)
    box.fill("")
    box.press("Enter")
    c.wait_for_timeout(2500)
    ok("the warning blanked: none shown, Remaining in its own colour",
       (on_clock(a, _PAINT)["warningShown"], on_clock(a, _PAINT)["warn"]) == (False, False), on_clock(a, _PAINT))
    # The host's page has had a dozen whole runs by now: still one timer drawing its panel (10 a second).
    on_clock(c, "() => true")
    draws = c.evaluate(_PANEL_DRAWS)
    ok("one timer draws the panel, however many runs (about 10 draws a second)", 8 <= draws <= 12, draws)


# The panel's draws in a second (the pop-out's, drawn into its document, not counted).
_PANEL_DRAWS = """async () => {
  const h = window.speakerTime;
  let n = 0;
  const real = h.draw.bind(h);
  h.draw = (root, mode) => { if (!root.body) n++; real(root, mode); };
  await new Promise((r) => setTimeout(r, 2000));
  h.draw = real;
  return n / 2;
}"""


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


_POP_OPEN = "Pop out (stays on top)"
_POPUP_OPEN = "Pop-up (may get obscured)"
_WHY = f"() => {{ const w = {_CLOCK_ROOT}.querySelector('.why'); return w.hidden ? null : w.textContent; }}"
_HAS_PIP = "() => !!(window.documentPictureInPicture && documentPictureInPicture.window)"
_GONE = "() => document.body.classList.contains('gone') && getComputedStyle(document.querySelector('.gone-note')).display !== 'none'"
_NOTE = f"() => {{ const n = {_CLOCK_ROOT}.querySelector('.note'); return n.hidden ? null : n.textContent; }}"
_UPDATED = "SpeakerTime was updated, which closed the pop-out: press the button to open it again."


def popups_of_one_browser(browser, errors: list[str], shot) -> None:
    """18. One pop-out for the whole browser: the other tabs offer a pop-up, then the pop-out again once it closes (Terry, 2026-11-03)."""
    profile = browser.new_context(viewport={"width": 700, "height": 800})

    def tab(sid: str, create: bool) -> Page:
        page = profile.new_page()
        page.on("pageerror", lambda error: errors.append(f"tab: {error}"))
        page.goto(profile_url)
        page.locator('[data-testid="stApp"]').wait_for()
        page.wait_for_timeout(2000)
        id_box(page).fill(sid)
        button(page, "Create New Session" if create else "Connect to Session").click()
        page.get_by_text("You are the host." if create else "You are viewing it.").wait_for(timeout=5000)
        return page

    def label(page: Page) -> str:
        return on_clock(page, _POP_LABEL)

    def pressed(page: Page) -> Page:
        with profile.expect_page(timeout=5000) as popped:
            on_clock(page, _CLICK_POP)
        return popped.value

    def ticking(window: Page) -> list[str]:
        first = window.locator('[data-v="elapsed"]').text_content()
        window.wait_for_timeout(1600)
        return [first, window.locator('[data-v="elapsed"]').text_content()]

    host = tab("ECHO", True)
    button(host, "Start").click()
    v1, v2 = tab("ECHO", False), tab("ECHO", False)
    labels = [label(p) for p in (host, v1, v2)]
    ok("three tabs of one browser: each offers the pop-out", labels == [_POP_OPEN] * 3, labels)
    pressed(host)
    seen = settled(lambda: [label(v1), label(v2)], [_POPUP_OPEN] * 2)
    ok("the host's pop-out open: the other two tabs offer a pop-up", seen == [_POPUP_OPEN] * 2, seen)
    why = on_clock(v1, _WHY)
    ok("with a note naming the session",
       why == "Another tab of this browser has the on-top pop-out (session ECHO). The browser allows one at a time.", why)
    shot(v1, "7-popup-offered.png")

    popup = pressed(v1)
    v1.wait_for_timeout(1200)
    seen = [host.evaluate(_HAS_PIP), v1.evaluate(_HAS_PIP), label(host), label(v1)]
    ok("a viewer's pop-up opens, the host's pop-out kept", seen == [True, False, "Close the pop-out", "Close the pop-up"], seen)
    seen = ticking(popup)
    ok("the pop-up ticks", seen[0] != seen[1], seen)

    on_clock(host, _CLICK_POP)
    seen = settled(lambda: [label(v2), label(v1), label(host)], [_POP_OPEN, "Close the pop-up", _POP_OPEN])
    ok("the pop-out closed: the tabs without a pop-up offer it again, the pop-up kept",
       seen == [_POP_OPEN, "Close the pop-up", _POP_OPEN], seen)
    pressed(v2)
    seen = settled(lambda: label(host), _POPUP_OPEN)
    ok("the first to press has it: the host's tab offers a pop-up now", seen == _POPUP_OPEN, seen)

    v1.reload()
    v1.get_by_text("You are viewing it.").wait_for(timeout=8000)
    seen = settled(lambda: label(v1), "Close the pop-up", 5.0)
    ok("a tab reloaded takes back its pop-up", seen == "Close the pop-up" and not popup.is_closed(), seen)
    seen = ticking(popup)
    ok("which ticks again, its notice not shown", seen[0] != seen[1] and popup.evaluate(_GONE) is False, seen)

    v1.close()
    gone = settled(lambda: popup.evaluate(_GONE), True, 6.0)
    ok("its tab closed: the pop-up says its tab has gone, the figures hidden", gone is True, gone)
    shot(popup, "8-popup-gone.png")
    v2.close()
    seen = settled(lambda: label(host), _POP_OPEN, 4.0)
    ok("the tab with the pop-out closed: the host's tab offers it again", seen == _POP_OPEN, seen)

    # 19. A tab left open while the server restarts with other code (a Reboot after a merge) holds a note of the timer
    # made by the earlier code: retired, its pop-out closed, the tab reloaded, back in its session, and told (2026-11-03).
    pip = pressed(host)
    with host.expect_event("load", timeout=8000):
        host.evaluate("window.speakerTime.key = 'earlier'")
    host.get_by_text("You are the host.").wait_for(timeout=8000)
    note = settled(lambda: on_clock(host, _NOTE), _UPDATED, 4.0)
    ok("an earlier version's note: its pop-out closed, the tab reloaded and still the host, and told so",
       pip.is_closed() and note == _UPDATED and on_clock(host, "() => window.speakerTime.key") != "earlier", note)
    profile.close()


profile_url = ""
"""The page's address, for windows_of_one_browser and popups_of_one_browser (set by run)."""


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
