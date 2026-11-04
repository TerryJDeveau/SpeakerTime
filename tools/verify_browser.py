"""The page driven in real browsers, a host and viewers each in a browser of their own: ``python tools/verify_browser.py``.

Starts the page on a free port (or tests ``--url``), then plays the
session's life through: waiting for a session not yet created, a taken ID,
the timers agreeing on every screen, reloads, the host's window closed and
"Assume Host Role?", a creator who comes back, Leave Session, the pop-out
window, the pop-ups offered while another tab of the browser has it, a
tab holding an earlier version's state, the host's Buzzer, the page fitted
to its window on connecting, the host's line in a pop-out or pop-up, a
link that connects by itself, a viewer's Day and Night, the host's row with Copy
Link, the frame's minimum in a small window, a viewer's Yes with check-ins
waiting, and a box cleared.
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
import math
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
from speakertime.sessions import BUZZ_HOLD_S, HOST_GRACE_S  # noqa: E402

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
# A pop-out's scaled copy of the panel (v1.11): its zoom and its size at the panel's sizes, each figure's size as shown
# (its own times the zoom), whether all of it is inside the window, and how much of the window it fills.
_COPY = """() => {
  const stage = document.querySelector('.stage');
  const zoom = parseFloat(stage.style.getPropertyValue('--z') || '1');
  const px = (n) => parseFloat(getComputedStyle(document.querySelector('[data-v="' + n + '"]')).fontSize) * zoom;
  const W = innerWidth, H = innerHeight, b = stage.getBoundingClientRect();
  return { size: [W, H], zoom, natural: [stage.offsetWidth, stage.offsetHeight],
           elapsedPx: px('elapsed'), remainingPx: px('remaining'), limitPx: px('limit'),
           inside: b.left >= -0.5 && b.top >= -0.5 && b.right <= W + 0.5 && b.bottom <= H + 0.5,
           filled: +Math.max(b.width / W, b.height / H).toFixed(3) };
}"""
# The size the opener's script last asked its pop-out's window to take, and whether the browser refused it.
_WANT_EXPRESSION = "[window.speakerTime.pipWant, window.speakerTime.pipRefused]"
_WANT = f"() => {_WANT_EXPRESSION}"
_INNER = "() => ({ width: innerWidth, height: innerHeight })"

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

# The panel against its figures (v1.11): its width and Remaining's figures', how far off centre it stands in its
# element and Remaining's figures stand in it, how far the host's sliders stand from the timers (and their left
# edges), and whether it runs past the window (a scroll bar), its left edge then at its element's.
_HUG = f"""() => {{
  const r = {_CLOCK_ROOT};
  const panel = r.querySelector('.clock').getBoundingClientRect(), outer = r.querySelector('.scroll').getBoundingClientRect();
  const value = r.querySelector('[data-v="remaining"]').getBoundingClientRect();
  const sliders = [...r.querySelectorAll('input.size')].filter(s => !s.hidden).map(s => s.getBoundingClientRect());
  const scroll = r.querySelector('.scroll');
  // The timers' right edge: their figures', or their labels' where a label is the wider ("Time Remaining" at 100).
  const right = Math.max(...['elapsed', 'remaining'].flatMap(n => ['[data-v="' + n + '"]', '[data-l="' + n + '"]'])
    .map(s => r.querySelector(s).getBoundingClientRect().right));
  return {{ panel: Math.round(panel.width), remaining: Math.round(value.width),
           off: Math.round((panel.left - outer.left) - (outer.right - panel.right)),
           centre: Math.round((value.left + value.right) / 2 - (panel.left + panel.right) / 2),
           gap: sliders.length ? Math.round(sliders[0].left - right) : null, lefts: sliders.map(s => Math.round(s.left)),
           scrolls: scroll.scrollWidth > scroll.clientWidth, left: Math.round(panel.left - outer.left) }};
}}"""

# The Time Limit and the Time Left Warning: the space between them, their limit label's height (its size: its line
# height is 1), and how far off the middle of their row the two stand together, on a panel (``root`` the clock's shadow
# root) or a pop-out (its document), as drawn (in a pop-out, scaled).
_GROUPED = """(root) => {
  const cell = (n) => root.querySelector('[data-v="' + n + '"]').closest('.cell').getBoundingClientRect();
  const l = cell('limit'), w = cell('warning'), p = root.querySelector('.pair').getBoundingClientRect();
  return { gap: +(w.left - l.right).toFixed(1), label: +root.querySelector('[data-l="limit"]').getBoundingClientRect().height.toFixed(1),
           off: +((l.left + w.right) / 2 - (p.left + p.right) / 2).toFixed(1) };
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
        # Tall enough that these pages keep their full size: the fit to a window is fitted_to_the_window's to check.
        contexts = {name: browser.new_context(viewport=size) for name, size in
                    [("A", {"width": 800, "height": 1500}), ("B", {"width": 420, "height": 1500}), ("C", {"width": 420, "height": 1500})]}
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

            a_pip = sizes_and_modes(a, b, c, pip, shot)
            viewer_popout_kept(a, c, a_pip)
            colours_and_warning(a, c, a_pip)
        windows_of_one_browser(browser, errors)
        popups_of_one_browser(browser, errors, shot)
        buzzer_held(browser, errors, shot)
        buzzer_silent(browser, errors)
        fitted_to_the_window(browser, errors, shot)
        host_line_in_popout(browser, errors, shot)
        join_link(browser, errors)
        day_and_night(browser, errors, shot)
        host_row_and_link(browser, errors, shot)
        frame_minimum_and_recovery(browser, errors)
        yes_with_check_ins_waiting(browser, errors)
        box_cleared(browser, errors)

        ok("no script errors on any page", not errors, errors)
        browser.close()


_HAS_CLOCK = "() => [...document.querySelectorAll('*')].some(e => e.shadowRoot && e.shadowRoot.querySelector('.clock'))"


def on_clock(page: Page, script: str, arg: object = None) -> object:
    """Run ``script`` on the page's clock once it is there: a run of the page rebuilds the clock, which is missing for a moment."""
    page.wait_for_function(_HAS_CLOCK, polling=100, timeout=8000)  # timed: a page behind a pop-out may get no animation frames
    return page.evaluate(script, arg)


def mode_of(page: Page) -> str | None:
    return on_clock(page, _PANEL)["mode"]


def scaled_copy(c: Page, pip: Page, shot) -> None:
    """12b. The pop-out is the host's panel scaled as a whole to fit its window, and its window follows the sizes (v1.11).

    Terry, 2026-11-03: the pop-out resized "if necessary to accommodate a
    larger clock size", and resized by hand, the size shown scaled "so that
    the pop-up looks like a scaled-up or scaled-down version of what the host
    is seeing" (replacing v1.7's column beside the timers). Headless Chrome
    keeps a window's inner size whatever a script asks (measured), so the
    window's following is read from what the script asked for, and the
    window then given that size by hand. On entry C hosts TALK1 with Elapsed
    at 100 and Remaining at 400, ``pip`` its own pop-out; leaves so.
    """
    size = pip.evaluate(_INNER)

    def laid_out(width: int, height: int) -> dict:
        pip.set_viewport_size({"width": width, "height": height})
        pip.wait_for_timeout(400)
        return pip.evaluate(_COPY)

    # The panel's sizes on a computer (web._PIP_CSS): a unit of 56 px per 100 of the slider, the Time Limit 54 px.
    natural = {"elapsedPx": 56 * 100 / 100, "remainingPx": 56 * 400 / 100, "limitPx": 54}
    small, large = laid_out(600, 400), laid_out(1200, 800)
    uniform = [round(s[k] / s["zoom"] / px, 3) for s in (small, large) for k, px in natural.items()]
    ok("the pop-out a copy of the panel: Elapsed (100), Remaining (400) and the Time Limit at the panel's sizes times one"
       " zoom, all inside, filling it", all(0.99 <= u <= 1.01 for u in uniform) and small["inside"] and large["inside"]
       and 0.98 <= small["filled"] <= 1 and 0.98 <= large["filled"] <= 1, [uniform, small, large])
    ok("its window made twice as large by hand: everything twice as large", 1.98 <= large["zoom"] / small["zoom"] <= 2.02,
       [small["zoom"], large["zoom"]])
    wide = laid_out(1600, 300)
    ok("a wide, low window: the copy fits its height, all inside", wide["inside"] and abs(wide["filled"] - 0.99) <= 0.01, wide)
    shot(pip, "6b-popout-wide.png")
    before = laid_out(600, 400)
    on_clock(c, _SLIDE, ["remaining", 200])  # the host's own page, just clicked: Chrome lets its pop-out be resized
    c.wait_for_timeout(2500)
    want, refused = c.evaluate(_WANT)
    shrunk = pip.evaluate(_COPY)
    expected = [math.ceil(n * before["zoom"] / 0.99) for n in shrunk["natural"]]
    ok("Remaining at 200: the host's pop-out asked to take the copy's new size at the zoom it had",
       refused is False and all(abs(w - e) <= 2 for w, e in zip(want, expected)), [want, expected, before, shrunk])
    after = laid_out(*want)
    ok("given it: the same zoom, Remaining half its size there, Elapsed and the Time Limit unchanged",
       abs(after["zoom"] / before["zoom"] - 1) <= 0.01 and 0.49 <= after["remainingPx"] / before["remainingPx"] <= 0.51
       and abs(after["elapsedPx"] / before["elapsedPx"] - 1) <= 0.01 and abs(after["limitPx"] / before["limitPx"] - 1) <= 0.01,
       [before, after])
    on_clock(c, _SLIDE, ["remaining", 400])
    c.wait_for_timeout(2500)
    pip.set_viewport_size(size)


def viewer_popout_kept(a: Page, c: Page, a_pip: Page) -> None:
    """12c. A viewer's pop-out keeps its window when the host's sizes change, its copy scaled to fit it (v1.11).

    Chrome lets a script resize a pop-out only within about 5 s of a click or
    a key on its page (measured 2026-11-03: "resizeBy() requires user
    activation in document picture-in-picture"). Playwright's own
    evaluations count as clicks, so A's page is read here through CDP alone.
    On entry C hosts TALK1 with Elapsed at 100 and Remaining at 400, A views
    it with its pop-out (``a_pip``) open; leaves so.
    """
    read = quiet(a)
    size = a_pip.evaluate(_INNER)
    a.wait_for_timeout(6000)  # no activation left on A's page or its pop-out
    on_clock(c, _SLIDE, ["remaining", 200])
    c.wait_for_timeout(2500)
    want, refused = read(_WANT_EXPRESSION)
    copy = a_pip.evaluate(_COPY)
    ok("a viewer's pop-out, the host's sizes changed: Chrome refuses to resize it, so its copy fills the window it has",
       refused is True and copy["size"] == [size["width"], size["height"]] and copy["inside"] and abs(copy["filled"] - 0.99) <= 0.01,
       [want, refused, copy])
    on_clock(c, _SLIDE, ["remaining", 400])
    c.wait_for_timeout(2500)


def sizes_and_modes(a: Page, b: Page, c: Page, pip: Page, shot) -> Page:
    """12-13. The host's sliders reach every panel and pop-out; each panel's and pop-out's switch is its own (2026-11-01).

    On entry C hosts TALK1 with its pop-out open, A views it, B is on the start screen. Gives A's pop-out, left open.
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
    # v1.6 (Terry, 2026-11-03: "about double" the largest): 800 twice 400's size, the page's other elements kept to
    # their column; since v1.11 in the pop-out's copy too (its window then follows, or the copy is scaled to fit it).
    a.set_viewport_size({"width": 1700, "height": 900})
    a.wait_for_timeout(800)
    wide_400 = on_clock(a, _PANEL)
    on_clock(c, _SLIDE, ["remaining", 800])
    c.wait_for_timeout(2500)
    wide_800, pip_800 = on_clock(a, _PANEL), pip.evaluate(_POPOUT)
    widths = a.evaluate(_WIDTHS)
    ok("Remaining at 800: twice 400's, on a viewer's panel and in the pop-out's copy",
       1.9 <= wide_800["remainingPx"] / wide_400["remainingPx"] <= 2.1 and 1.9 <= pip_800["remainingPx"] / pip_400["remainingPx"] <= 2.1,
       [wide_400, wide_800, pip_400, pip_800])
    ok("the clock's element spans the wide page, everything else keeps its column (704 px)",
       widths["clock"] > 1400 and max(widths["others"]) <= 704, widths)
    # v1.11 (Terry, 2026-11-03): the panel as wide as its figures need, not as the window, centred; the host's sliders
    # in one column just beside the larger timer's figures (Remaining, here); and a panel wider than the window runs
    # past its edge, a scroll bar under it.
    for page, who, tools in ((a, "a viewer's", 0), (c, "the host's", 112)):
        size = page.viewport_size
        page.set_viewport_size({"width": 1700, "height": 900})
        page.wait_for_timeout(800)
        seen = on_clock(page, _HUG)
        ok(f"{who} panel at 800 in a window of 1700 px: as wide as Remaining's figures with its padding"
           + (" and the sliders' columns" if tools else "") + ", centred",
           abs(seen["panel"] - (seen["remaining"] + 16 + tools)) <= 1 and abs(seen["off"]) <= 1 and not seen["scrolls"], seen)
        if tools:
            ok("the host's sliders in one column, just beside Remaining's figures",
               len(set(seen["lefts"])) == 1 and 0 <= seen["gap"] <= 8, seen)
        page.set_viewport_size({"width": 1100, "height": 900})
        page.wait_for_timeout(800)
        seen = on_clock(page, _HUG)
        ok(f"{who} panel at 800 in a window of 1100 px: wider than it, a scroll bar, its left edge at the page's",
           seen["scrolls"] and seen["left"] == 0 and abs(seen["panel"] - (seen["remaining"] + 16 + tools)) <= 1, seen)
        page.set_viewport_size(size)
    # v1.12: with both timers small the host's panel is wider than they need (480 px on a computer, its row of buttons
    # on one line; the page's width on a phone): the width to spare goes to its outer edges, so the sliders still stand
    # just beside the figures, and the figures in the middle (seen live in v1.11: 36 px between them at the default).
    size = c.viewport_size
    on_clock(c, _SLIDE, ["remaining", 100])
    c.wait_for_timeout(2500)
    for width in (1700, size["width"]):
        c.set_viewport_size({"width": width, "height": 900})
        c.wait_for_timeout(800)
        seen = on_clock(c, _HUG)
        ok(f"the host's panel, both timers at 100, in a window of {width} px: "
           + ("480 px, " if width >= 640 else "the page's width, ")
           + "the sliders just beside the timers (here their labels, wider than the figures), the figures in the middle",
           (seen["panel"] == 480 if width >= 640 else not seen["scrolls"]) and 0 <= seen["gap"] <= 8 and abs(seen["centre"]) <= 1, seen)
    c.set_viewport_size(size)
    on_clock(c, _SLIDE, ["remaining", 400])
    c.wait_for_timeout(2500)
    a.set_viewport_size({"width": 800, "height": 900})
    scaled_copy(c, pip, shot)

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
    return viewer_pip


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


def colours_and_warning(a: Page, c: Page, a_pip: Page) -> None:
    """16-17. The host's colour buttons and Terry's rule for black and white; the Time Left Warning and its colour (2026-11-01).

    On entry C hosts TALK1 (Time Limit 2.3 minutes, stopped part way), A views it, its pop-out (a_pip) open.
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
    # v1.8 (Terry, 2026-11-03): the two stand together in the middle, as the switch's buttons do, two of their labels'
    # heights apart, on a panel and in a pop-out (each took half the width until v1.7).
    grouped = [on_clock(a, f"() => ({_GROUPED})({_CLOCK_ROOT})"), a_pip.evaluate(f"() => ({_GROUPED})(document)")]
    ok("the Time Limit and the warning together in the middle, on a viewer's panel and in its pop-out",
       all(abs(g["gap"] - 2 * g["label"]) <= 1 and abs(g["off"]) <= 1 for g in grouped), grouped)
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


# The Buzzer as a page shows it: Remaining's text, colour and classes, whether the button is shown, the note under
# a viewer's clock (null while hidden), and the page's sound. An expression, for Playwright and for quiet() alike.
_BUZZ_SEEN = f"""(() => {{
  const r = {_CLOCK_ROOT};
  const v = r.querySelector('[data-v="remaining"]'), note = r.querySelector('.buzz-note'), h = window.speakerTime;
  return {{ remaining: v.textContent, colour: getComputedStyle(v).color, blink: v.classList.contains('blink'),
           over: v.classList.contains('over'), button: !r.querySelector('.buzzer').hidden,
           note: note.hidden ? null : note.textContent, audio: h.audio ? h.audio.state : null, sounding: h.tone !== null }};
}})()"""
_BUZZ_NOTE = "Click anywhere on this page once so that it can sound the buzzer."
# The host's page sends nothing more of its Buzzer: each check-in sets the sender again, so it is pinned to none.
_MUTE = "Object.defineProperty(window.speakerTime, 'buzzSend', { get: () => null, set: () => {}, configurable: true })"


def quiet(page: Page):
    """A reader of ``page`` that does not count as the user's gesture there.

    Playwright runs its own scripts as gestures (userGesture), which a browser
    takes for a click on the page, after which the page may sound: the note
    the Buzzer's check looks for would go at the first look. Chrome's own
    protocol, asked directly, runs a script as no gesture.
    """
    session = page.context.new_cdp_session(page)

    def read(expression: str) -> object:
        answer = session.send("Runtime.evaluate", {
            "expression": f"(() => {{ try {{ return {expression}; }} catch (error) {{ return null; }} }})()", "returnByValue": True})
        return answer.get("result", {}).get("value")
    return read


def buzzer_held(browser, errors: list[str], shot) -> None:
    """20. The host's Buzzer (Terry, 2026-11-03): held, BUZZ in red everywhere and a buzzer on every page (v1.13: the host's too); let go, the time again."""
    pages = []
    for create in (True, False):
        page = browser.new_context(viewport={"width": 700, "height": 800}).new_page()
        page.on("pageerror", lambda error: errors.append(f"buzzer: {error}"))
        page.goto(profile_url)
        page.locator('[data-testid="stApp"]').wait_for()
        page.wait_for_timeout(2000)
        id_box(page).fill("FOXTROT")
        button(page, "Create New Session" if create else "Connect to Session").click()
        page.get_by_text("You are the host." if create else "You are viewing it.").wait_for(timeout=5000)
        pages.append(page)
    host, viewer = pages
    limit_box(host).fill("5")
    limit_box(host).press("Enter")
    host.wait_for_timeout(800)
    button(host, "Start").click()
    ok("the Buzzer on the host's panel alone", (on_clock(host, _BUZZ_SEEN)["button"], on_clock(viewer, _BUZZ_SEEN)["button"]) == (True, False))

    # A viewer's page reconnected by itself (a reload) may not sound until clicked: it says so, and a click ends it.
    read = quiet(viewer)
    viewer.reload()
    settled(lambda: read("document.body.innerText.includes('You are viewing it.')"), True, 8.0)
    seen = settled(lambda: (read(_BUZZ_SEEN) or {}).get("note"), _BUZZ_NOTE, 5.0)
    ok("a viewer's page reconnected by itself: the note to click it", seen == _BUZZ_NOTE, seen)
    shot(viewer, "9-buzzer-note.png")
    viewer.mouse.click(12, 400)  # the page's margin: a click on nothing
    seen = settled(lambda: [(read(_BUZZ_SEEN) or {}).get(k) for k in ("note", "audio")], [None, "running"], 3.0)
    ok("clicked: the note gone, its sound ready", seen == [None, "running"], seen)
    with viewer.context.expect_page(timeout=5000) as popped:
        on_clock(viewer, _CLICK_POP)
    pip = popped.value
    viewer.wait_for_timeout(1000)

    def everywhere() -> list[str]:
        return [on_clock(host, _BUZZ_SEEN)["remaining"], read(_BUZZ_SEEN)["remaining"], pip.locator('[data-v="remaining"]').text_content()]

    # Held by the mouse: at once on the host's panel, within 2.5 s on the viewer's and its pop-out, and it sounds there.
    box = host.locator(".buzzer").bounding_box()
    host.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    host.mouse.down()
    began = time.monotonic()
    seen = on_clock(host, _BUZZ_SEEN)
    ok("held: BUZZ on the host's panel at once, in steady red",
       (seen["remaining"], seen["colour"], seen["blink"], seen["over"]) == ("BUZZ", rgb("#ff2a2a"), False, False), seen)
    seen = settled(lambda: [on_clock(host, _BUZZ_SEEN)[k] for k in ("audio", "sounding")], ["running", True], 1.0)
    ok("and the buzzer sounds on the host's page at once", seen == ["running", True], seen)
    host.evaluate("window.speakerTime.tone.mark = 1")  # the same sound later on: check-ins have not broken it up
    seen = settled(everywhere, ["BUZZ"] * 3, 2.5)
    ok("and on a viewer's panel and its pop-out within 2.5 s", seen == ["BUZZ"] * 3, [seen, f"{(time.monotonic() - began) * 1000:.0f} ms"])
    seen = read(_BUZZ_SEEN)
    ok("the viewer's buzzer sounds, its BUZZ in red too", (seen["audio"], seen["sounding"], seen["colour"]) == ("running", True, rgb("#ff2a2a")), seen)
    shot(host, "10-buzzer-held.png")
    shot(pip, "11-popout-buzz.png")
    host.wait_for_timeout((BUZZ_HOLD_S + 2) * 1000 - (time.monotonic() - began) * 1000)
    seen = everywhere()
    ok(f"held {BUZZ_HOLD_S + 2:.0f} s: still BUZZ everywhere (the host's page says so again)", seen == ["BUZZ"] * 3, seen)
    ok("and the host's page's sound one unbroken sound", host.evaluate("window.speakerTime.tone?.mark === 1"))
    host.mouse.up()
    ok("let go: the host's page's sound stops at once", on_clock(host, _BUZZ_SEEN)["sounding"] is False)
    seen = settled(lambda: [t != "BUZZ" for t in everywhere()] + [read(_BUZZ_SEEN)["sounding"]], [True, True, True, False], 2.5)
    ok("let go: the time again everywhere within 2.5 s, the sound stopped", seen == [True, True, True, False], [everywhere(), seen])

    # Held by Space on the button.
    host.locator(".buzzer").focus()
    host.keyboard.down("Space")
    seen = settled(lambda: everywhere()[1:], ["BUZZ"] * 2, 2.5)
    ok("held by Space: BUZZ at the viewer's", seen == ["BUZZ"] * 2, seen)
    host.keyboard.up("Space")
    seen = settled(lambda: [t != "BUZZ" for t in everywhere()], [True] * 3, 2.5)
    ok("Space let go: the time again", seen == [True] * 3, everywhere())

    # The host's page can say nothing more while held (as with its connection lost): the server stops the Buzzer by
    # itself, which changes no version, so the viewer takes it from a check-in.
    host.mouse.down()
    settled(lambda: read(_BUZZ_SEEN)["remaining"], "BUZZ", 2.5)
    host.evaluate(_MUTE)
    muted = time.monotonic()
    seen = settled(lambda: read(_BUZZ_SEEN)["remaining"] != "BUZZ", True, BUZZ_HOLD_S + 3)
    took = time.monotonic() - muted
    ok(f"held, the host's page silenced: stopped by itself about {BUZZ_HOLD_S:.0f} s after its last word",
       seen is True and took > BUZZ_HOLD_S - 5 and read(_BUZZ_SEEN)["sounding"] is False, f"{took:.1f} s")
    host.mouse.up()
    host.evaluate("delete window.speakerTime.buzzSend")
    # The host's page closed with the button down: let go at once (the page is hidden as it goes).
    host.wait_for_timeout(1500)
    host.mouse.down()
    settled(lambda: read(_BUZZ_SEEN)["remaining"], "BUZZ", 2.5)
    host.close()
    closed = time.monotonic()
    seen = settled(lambda: read(_BUZZ_SEEN)["remaining"] != "BUZZ", True, 2.5)
    ok("the host's page closed while held: let go within 2.5 s", seen is True, f"{time.monotonic() - closed:.1f} s")
    viewer.context.close()


_SILENT_TEXT = "(silent)"


def buzzer_silent(browser, errors: list[str]) -> None:
    """20b. A page whose address says silent makes no sound, host or viewer, and says so; BUZZ still shows, and the other pages sound (Terry, 2026-11-03)."""
    def opened(address: str, create: bool) -> Page:
        page = browser.new_context(viewport={"width": 700, "height": 800}).new_page()
        page.on("pageerror", lambda error: errors.append(f"silent: {error}"))
        page.goto(profile_url + address)
        page.locator('[data-testid="stApp"]').wait_for()
        page.wait_for_timeout(2000)
        id_box(page).fill("GOLF")
        button(page, "Create New Session" if create else "Connect to Session").click()
        page.get_by_text("You are the host." if create else "You are viewing it.").wait_for(timeout=5000)
        return page

    host, hushed, plain = opened("?silent", True), opened("?-silent", False), opened("", False)
    ok("a silent page says so, the others do not",
       [_SILENT_TEXT in main_text(p) for p in (host, hushed, plain)] == [True, True, False])
    host.wait_for_timeout(1000)  # a page may sound from its next draw

    def pages(key: str) -> list[object]:
        return [on_clock(p, _BUZZ_SEEN)[key] for p in (host, hushed, plain)]

    box = host.locator(".buzzer").bounding_box()
    host.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    host.mouse.down()
    seen = settled(lambda: pages("remaining"), ["BUZZ"] * 3, 2.5)
    ok("held on a silent host's page: BUZZ on every page", seen == ["BUZZ"] * 3, seen)
    seen = settled(lambda: pages("sounding"), [False, False, True], 1.0)
    ok("no sound on the two silent pages, nor any sound made ready; the other page sounds",
       seen == [False, False, True] and pages("audio")[:2] == [None, None], [seen, pages("audio")])
    host.mouse.up()
    seen = settled(lambda: pages("sounding")[2], False, 2.5)
    ok("let go: the other page's sound stops", seen is False, seen)
    # A silent viewer's page reconnected by itself has no sound to ask a click for: no note.
    read = quiet(hushed)
    hushed.reload()
    settled(lambda: read("document.body.innerText.includes('You are viewing it.')"), True, 8.0)
    hushed.wait_for_timeout(1500)
    seen = read(_BUZZ_SEEN)
    ok("a silent viewer's page reconnected: no note to click it, and still silent after a reload",
       (seen or {}).get("note") is None and _SILENT_TEXT in read("document.body.innerText"), seen)
    for page in (host, hushed, plain):
        page.context.close()


_BADGES_PX = 46
"""How high Community Cloud's badges stand over the window's lower right corner (measured 2026-11-03): nothing locally shows them, so the fit is held clear of that height."""

# How a page has been fitted to its window (v1.13): its two scales, whether the fit has ended, whether every element of
# the page's column stands inside the window, above the badges, and nothing has a scroll bar, the panel's size as drawn.
# For a page that scrolls (v1.17): the frame in view and clear of the top bar, nothing sideways, how far it is scrolled,
# the end of the page above the badges when scrolled there, how many times the room the text has it takes (web._FIT_TEXT),
# and whether the title's text is clear of the bar.
_FIT_SEEN = f"""() => {{
  const main = document.querySelector('[data-testid="stMain"]'), h = window.speakerTime;
  const column = document.querySelector('[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"]');
  const kids = [...column.children].map(k => k.getBoundingClientRect());
  const r = {_CLOCK_ROOT}, scroll = r.querySelector('.scroll'), panel = r.querySelector('.clock').getBoundingClientRect();
  const padding = getComputedStyle(document.querySelector('[data-testid="stMainBlockContainer"]'));
  const room = main.clientHeight - parseFloat(padding.paddingTop) - parseFloat(padding.paddingBottom) - 1;
  const bar = document.querySelector('[data-testid="stHeader"]').getBoundingClientRect(), title = document.createRange();
  title.selectNodeContents(column.querySelector('h3'));
  return {{ page: h.fitPage, panel: h.fitPanel, ended: h.fitting === null,
           inside: Math.min(...kids.map(k => k.top)) >= 0 && Math.max(...kids.map(k => k.bottom)) <= innerHeight - {_BADGES_PX}
             && Math.max(...kids.map(k => k.right)) <= innerWidth,
           bars: main.scrollHeight > main.clientHeight || main.scrollWidth > main.clientWidth || scroll.scrollWidth > scroll.clientWidth,
           frame: panel.top >= bar.bottom && panel.bottom <= innerHeight,
           sideways: main.scrollWidth > main.clientWidth || scroll.scrollWidth > scroll.clientWidth,
           top: main.scrollTop,
           reached: Math.max(...kids.map(k => k.bottom)) - (main.scrollHeight - main.clientHeight - main.scrollTop) <= innerHeight - {_BADGES_PX},
           text: +((column.getBoundingClientRect().height - panel.height) / (room - panel.height)).toFixed(3),
           titleClear: title.getBoundingClientRect().top >= bar.bottom - 3,  // the text's box stands a few pixels above its letters
           size: [Math.round(panel.width), Math.round(panel.height)], window: [innerWidth, innerHeight] }};
}}"""
_COLUMN_ZOOM = """() => getComputedStyle(document.querySelector(
  '[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"]')).zoom"""
_COOKIE_TEXT = "This page keeps small cookies"


def scrolled(seen: dict) -> bool:
    """A page in a small window (web._FIT_TEXT): settled, at its top, the frame in view, nothing sideways, and a scroll reaching the end above Community Cloud's badges."""
    return seen["ended"] and seen["top"] == 0 and seen["frame"] and not seen["sideways"] and seen["reached"]


def fitted_to_the_window(browser, errors: list[str], shot) -> None:
    """21. On connecting, the app is fitted to the tab's window, scaled down if need be, never up (Terry, 2026-11-03); in a small window the frame is in view and a scroll reaches the rest (Terry, 2026-11-04)."""
    def fit_of(page: Page) -> dict:
        page.wait_for_function(f"() => {{ {_HAS_FIT}; }}", polling=100, timeout=15000)
        return on_clock(page, _FIT_SEEN)

    def connected(sid: str, create: bool, width: int, height: int) -> Page:
        page = browser.new_context(viewport={"width": width, "height": height}).new_page()
        page.on("pageerror", lambda error: errors.append(f"fit: {error}"))
        page.goto(profile_url)
        page.locator('[data-testid="stApp"]').wait_for()
        page.wait_for_timeout(1500)
        id_box(page).fill(sid)
        button(page, "Create New Session" if create else "Connect to Session").click()
        page.get_by_text("You are the host." if create else "You are viewing it.").wait_for(timeout=8000)
        return page

    def all_shown(seen: dict) -> bool:
        return seen["ended"] and seen["inside"] and not seen["bars"]

    # In a window of the usual size the panel alone is scaled, the text around it keeping its size.
    host = connected("INDIA", True, 1545, 784)
    seen = fit_of(host)
    ok("the host's page in a window of 1545 by 784: all of it inside, no scroll bar, the panel alone scaled",
       all_shown(seen) and seen["page"] == 1 and seen["panel"] < 1, seen)
    note = host.get_by_text(_COOKIE_TEXT).bounding_box()
    ok("the cookie note inside the window too, above Community Cloud's badges", note is not None and note["y"] + note["height"] <= 784 - _BADGES_PX, note)
    shot(host, "12-fit-host.png")
    viewer = connected("INDIA", False, 1545, 784)
    seen = fit_of(viewer)
    ok("a viewer's page the same, its panel larger for having less around it",
       all_shown(seen) and seen["page"] == 1 and on_clock(host, _FIT_SEEN)["panel"] < seen["panel"] < 1, seen)
    shot(viewer, "13-fit-viewer.png")

    # Room to spare: never scaled up.
    roomy = connected("INDIA", False, 1545, 1300)
    seen = fit_of(roomy)
    ok("a window with room to spare: its full size, not larger", all_shown(seen) and (seen["page"], seen["panel"]) == (1, 1), seen)

    # Windows too short for the panel alone to make room: the page's column is scaled too, and a scroll reaches the rest.
    for sid, create, width, height, who, fits in (("INDIA", False, 500, 400, "a viewer's page", False), ("JULIET", True, 500, 400, "the host's page", False),
                                                  ("INDIA", False, 390, 750, "a viewer's phone", True), ("KILO", True, 390, 750, "the host's phone", False)):
        page = connected(sid, create, width, height)
        seen = fit_of(page)
        if fits:
            ok(f"{who} in a window of {width} by {height}: all of it inside, no scroll bar", all_shown(seen), seen)
        else:
            ok(f"{who} in a window of {width} by {height}: at its top, the frame in view, nothing sideways, a scroll reaching the end above the badges", scrolled(seen), seen)
        if (width, create) == (500, True):
            ok("too short for the panel alone: the page's column scaled too", seen["page"] < 1, seen)
            shot(page, "14-fit-small.png")
        if (width, create) == (500, False):
            small_viewer = page
        else:
            page.context.close()

    # The host's sizes at the largest: a page connecting now is scaled to their width as well.
    before = on_clock(host, _FIT_SEEN)
    for timer in ("elapsed", "remaining"):
        on_clock(host, _SLIDE, [timer, 800])
        host.wait_for_timeout(1500)
    late = connected("INDIA", False, 1100, 700)
    seen = fit_of(late)
    ok("a page connecting with the sizes at 800: all of it inside, no scroll bar", all_shown(seen) and seen["panel"] < before["panel"], seen)
    late.context.close()

    # After that the sliders and the window are the user's: the scales stay, and a panel made too wide has its scroll bar.
    seen = on_clock(host, _FIT_SEEN)
    ok("the host's sliders moved after connecting: its scale unchanged, the panel larger",
       (seen["page"], seen["panel"]) == (before["page"], before["panel"]) and seen["size"][0] > before["size"][0], [before, seen])
    held = on_clock(viewer, _FIT_SEEN)
    viewer.set_viewport_size({"width": 1000, "height": 500})
    viewer.wait_for_timeout(800)
    seen = on_clock(viewer, _FIT_SEEN)
    ok("a window made smaller after connecting: its scale unchanged", (seen["page"], seen["panel"]) == (held["page"], held["panel"]), [held, seen])

    # Connecting again fits again: a reload, a session entered after leaving, a viewer becoming the host.
    host.reload()
    host.get_by_text("You are the host.").wait_for(timeout=8000)
    seen = fit_of(host)
    # The note saying it has reconnected takes it a little past the window, where a column's scale of 0.92 would have kept it inside (web._FIT_TEXT).
    ok("the host's page reloaded with the sizes at 800: fitted again, its text at full size, a scroll reaching the rest", scrolled(seen) and seen["page"] == 1 and seen["panel"] < before["panel"], seen)
    roomy.set_viewport_size({"width": 1000, "height": 500})
    button(roomy, "Leave Session").click()
    button(roomy, "Connect to Session").wait_for()
    ok("Leave Session: the start screen at its full size", roomy.evaluate(_COLUMN_ZOOM) == "1", roomy.evaluate(_COLUMN_ZOOM))
    entered = roomy.evaluate("window.speakerTime.entered")
    button(roomy, "Connect to Session").click()
    roomy.get_by_text("You are viewing it.").wait_for(timeout=8000)
    roomy.wait_for_function("(before) => window.speakerTime.entered !== before", arg=entered, polling=100, timeout=8000)
    seen = fit_of(roomy)
    ok("connecting again in a smaller window: fitted to it, at its top though the start screen was scrolled to reach its button", scrolled(seen) and seen["page"] * seen["panel"] < 1, seen)
    host.close()
    button(small_viewer, "Yes").wait_for(timeout=(HOST_GRACE_S + 10) * 1000)
    entered = small_viewer.evaluate("window.speakerTime.entered")
    button(small_viewer, "Yes").click()
    small_viewer.get_by_text("You are the host.").wait_for(timeout=5000)
    # The role's text comes before the clock's data: until that arrives, the last fit is what there is to read.
    small_viewer.wait_for_function("(before) => window.speakerTime.entered !== before", arg=entered, polling=100, timeout=8000)
    seen = fit_of(small_viewer)
    ok("a viewer becoming the host: fitted again, with the host's controls", scrolled(seen), seen)
    for page in (viewer, roomy, small_viewer):
        page.context.close()


_HAS_FIT = f"const h = window.speakerTime; return h && h.fitting === null && h.entered !== null && ({_HAS_CLOCK})()"

# The line under a pop-out's or pop-up's timers: the switch's labels, each button's text and whether it is greyed (null
# while hidden), whether the Buzzer shows held, whether all of it stands on one row, and whether it is inside the window.
_LINE_SEEN = """() => {
  const q = (s) => document.querySelector(s);
  const state = (s) => q(s).hidden ? null : [q(s).textContent, q(s).disabled];
  const boxes = [...document.querySelectorAll('.modes, .bar > button')].filter(e => !e.hidden).map(e => e.getBoundingClientRect());
  const middles = boxes.map(b => (b.top + b.bottom) / 2), bar = q('.bar').getBoundingClientRect();
  return { modes: [...document.querySelectorAll('.modes button')].map(b => b.textContent), buzz: state('.buzz'),
           start: state('[data-a="start"]'), stop: state('[data-a="stop"]'), reset: state('[data-a="reset"]'),
           daynight: !q('.daynight').hidden,
           held: q('.buzz').classList.contains('on'), oneRow: Math.max(...middles) - Math.min(...middles) <= 2,
           inside: bar.left >= -0.5 && bar.top >= -0.5 && bar.right <= innerWidth + 0.5 && bar.bottom <= innerHeight + 0.5 };
}"""
_FULL_MODES = ["H:MM:SS", "MMM.M"]


def host_line_in_popout(browser, errors: list[str], shot) -> None:
    """22. The host's line in a pop-out or pop-up: its switch, Buzz, Start or Restart, Stop and Reset (Terry, 2026-11-03: a single monitor, Zoom maximized)."""
    def connected(context, sid: str, create: bool) -> Page:
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(f"line: {error}"))
        page.goto(profile_url)
        page.locator('[data-testid="stApp"]').wait_for()
        page.wait_for_timeout(1500)
        id_box(page).fill(sid)
        button(page, "Create New Session" if create else "Connect to Session").click()
        page.get_by_text("You are the host." if create else "You are viewing it.").wait_for(timeout=8000)
        page.wait_for_timeout(800)
        return page

    def popped_from(page: Page) -> Page:
        with page.context.expect_page(timeout=5000) as popped:
            on_clock(page, _CLICK_POP)
        popped.value.on("pageerror", lambda error: errors.append(f"line, its window: {error}"))
        page.wait_for_timeout(1000)
        return popped.value

    size = {"width": 800, "height": 1300}
    host_context = browser.new_context(viewport=size)
    host, viewer = connected(host_context, "LIMA", True), connected(browser.new_context(viewport=size), "LIMA", False)
    # One pop-out at a time, whatever the browser's contexts (web._POP_LOCK): the viewer's first, closed for the host's.
    viewer_pip = popped_from(viewer)
    seen = viewer_pip.evaluate(_LINE_SEEN)
    ok("a viewer's pop-out: the switch and Day/Night, no host's buttons", (seen["modes"], seen["buzz"], seen["start"], seen["stop"], seen["reset"], seen["daynight"])
       == (_FULL_MODES, None, None, None, None, True), seen)
    viewer_pip.close()
    viewer.wait_for_timeout(1500)
    pip = popped_from(host)
    seen = pip.evaluate(_LINE_SEEN)
    ok("the host's pop-out: one line of H:MM:SS|MMM.M, Buzz, Start, Stop and Reset, no Day/Night, inside its window, Stop and Reset greyed",
       (seen["modes"], seen["buzz"], seen["start"], seen["stop"], seen["reset"], seen["daynight"], seen["oneRow"], seen["inside"])
       == (_FULL_MODES, ["Buzz", False], ["Start", False], ["Stop", True], ["Reset", True], False, True, True), seen)
    shot(pip, "15-popout-line.png")

    def line(key: str) -> object:
        return pip.evaluate(_LINE_SEEN)[key]

    # The timer's buttons: each as the page's own, the page's agreeing, the viewer following.
    pip.locator('[data-a="start"]').click()
    seen = settled(lambda: [line("start"), line("stop"), line("reset")], [["Restart", True], ["Stop", False], ["Reset", False]], 3.0)
    ok("Start in the pop-out: running, Start reading Restart and greyed, Stop and Reset live", seen == [["Restart", True], ["Stop", False], ["Reset", False]], seen)
    ok("and the page's own buttons agree", settled(lambda: button(host, "Restart").is_disabled(), True, 3.0) is True)
    viewer.wait_for_timeout(2200)
    ok("and the viewer's timers run", clock(viewer)["elapsed"] not in ("0:00", None), clock(viewer))
    pip.locator('[data-a="stop"]').click()
    seen = settled(lambda: [line("start"), line("stop")], [["Restart", False], ["Stop", True]], 3.0)
    ok("Stop in the pop-out: stopped, Restart live, Stop greyed", seen == [["Restart", False], ["Stop", True]], seen)
    pip.locator('[data-a="reset"]').click()
    seen = settled(lambda: [line("start"), line("reset")], [["Start", False], ["Reset", True]], 3.0)
    ok("Reset in the pop-out: Start again, Reset greyed, zero everywhere",
       seen == [["Start", False], ["Reset", True]] and settled(lambda: clock(viewer)["elapsed"], "0:00", 3.0) == "0:00" and clock(host)["elapsed"] == "0:00", seen)
    seen = pip.locator('[data-a="start"]').bounding_box()["width"]
    pip.locator('[data-a="start"]').click()
    settled(lambda: line("start"), ["Restart", True], 3.0)
    ok("Start's button no wider as Restart: the window does not jump", abs(pip.locator('[data-a="start"]').bounding_box()["width"] - seen) <= 1,
       [seen, pip.locator('[data-a="start"]').bounding_box()["width"]])
    pip.locator('[data-a="stop"]').click()
    settled(lambda: line("stop"), ["Stop", True], 3.0)

    def everywhere() -> list[str]:
        return [clock(host)["remaining"], pip.locator('[data-v="remaining"]').text_content(), clock(viewer)["remaining"]]

    def sounding(page: Page) -> bool:
        return on_clock(page, _BUZZ_SEEN)["sounding"]

    # The Buzzer: held in the pop-out, the page behind it having lost the focus to it.
    box = pip.locator(".buzz").bounding_box()
    pip.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    pip.mouse.down()
    ok("Buzz held in the pop-out: shown held, BUZZ everywhere within 2.5 s", settled(everywhere, ["BUZZ"] * 3, 2.5) == ["BUZZ"] * 3 and line("held") is True, everywhere())
    seen = settled(lambda: [sounding(host), sounding(viewer)], [True, True], 1.0)
    ok("the buzzer sounds on the host's page and the viewer's", seen == [True, True], seen)
    host.evaluate("window.dispatchEvent(new Event('blur'))")
    host.wait_for_timeout(300)
    ok("the page behind losing the focus does not let go: its pop-out holds it", line("held") is True and sounding(host) is True)
    began = time.monotonic()
    pip.wait_for_timeout((BUZZ_HOLD_S + 2) * 1000)
    ok(f"held {BUZZ_HOLD_S + 2:.0f} s in the pop-out: still BUZZ everywhere (said again from its own timer)", everywhere() == ["BUZZ"] * 3, [everywhere(), f"{time.monotonic() - began:.1f} s"])
    pip.mouse.up()
    seen = [line("held"), sounding(host)]
    ok("let go in the pop-out: the button shows it, and the host's page's sound stops at once", seen == [False, False], seen)
    seen = settled(lambda: [t != "BUZZ" for t in everywhere()] + [sounding(viewer)], [True, True, True, False], 2.5)
    ok("and the time again everywhere within 2.5 s, the viewer's sound stopped", seen == [True, True, True, False], [everywhere(), seen])
    pip.locator(".buzz").focus()
    pip.keyboard.down("Space")
    ok("held by Space in the pop-out: BUZZ at the viewer's", settled(lambda: clock(viewer)["remaining"], "BUZZ", 2.5) == "BUZZ", clock(viewer))
    pip.keyboard.up("Space")
    ok("Space let go: the time again", settled(lambda: clock(viewer)["remaining"] != "BUZZ", True, 2.5) is True, clock(viewer))
    # The pop-out closed with the button down: let go, as with the host's page closed.
    box = pip.locator(".buzz").bounding_box()
    pip.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    pip.mouse.down()
    settled(lambda: clock(viewer)["remaining"], "BUZZ", 2.5)
    pip.close()
    ok("the pop-out closed while held: let go within 2.5 s", settled(lambda: clock(viewer)["remaining"] != "BUZZ", True, 2.5) is True, clock(viewer))

    # A pop-up has the same line: another tab of the browser, its pop-out taken by this one; and a tab reloaded takes it back.
    pip = popped_from(host)
    other = connected(host_context, "MIKE", True)
    other.wait_for_timeout(1500)
    popup = popped_from(other)
    seen = popup.evaluate(_LINE_SEEN)
    ok("a pop-up the same line", (seen["modes"], seen["buzz"], seen["start"], seen["oneRow"]) == (_FULL_MODES, ["Buzz", False], ["Start", False], True), seen)
    popup.locator('[data-a="start"]').click()
    ok("Start in the pop-up: running", settled(lambda: popup.evaluate(_LINE_SEEN)["start"], ["Restart", True], 3.0) == ["Restart", True], popup.evaluate(_LINE_SEEN))
    other.reload()
    other.get_by_text("You are the host.").wait_for(timeout=8000)
    other.wait_for_timeout(1500)
    popup.locator('[data-a="stop"]').click()
    ok("its tab reloaded and the pop-up taken back: its buttons still work",
       settled(lambda: popup.evaluate(_LINE_SEEN)["stop"], ["Stop", True], 4.0) == ["Stop", True], popup.evaluate(_LINE_SEEN))
    popup.close()
    other.close()

    # A viewer made host gains the line in the pop-out it already has.
    pip.close()
    host.close()
    viewer.wait_for_timeout(1500)
    viewer_pip = popped_from(viewer)
    button(viewer, "Yes").wait_for(timeout=(HOST_GRACE_S + 10) * 1000)
    button(viewer, "Yes").click()
    viewer.get_by_text("You are the host.").wait_for(timeout=5000)
    seen = settled(lambda: (viewer_pip.evaluate(_LINE_SEEN)["buzz"]), ["Buzz", False], 3.0)
    ok("a viewer made host: its pop-out gains the host's line", seen == ["Buzz", False], viewer_pip.evaluate(_LINE_SEEN))
    host_context.close()
    viewer.context.close()


def join_link(browser, errors: list[str]) -> None:
    """23. A link that connects by itself: ``?session=ID`` (Terry, 2026-11-03), with ``&silent`` if wanted."""
    def opened(address: str, context=None) -> Page:
        page = (context or browser.new_context(viewport={"width": 700, "height": 1300})).new_page()
        page.on("pageerror", lambda error: errors.append(f"link: {error}"))
        page.goto(profile_url + address)
        return page

    host = opened("")
    host.locator('[data-testid="stApp"]').wait_for()
    host.wait_for_timeout(1500)
    id_box(host).fill("NOVEMBER")
    button(host, "Create New Session").click()
    host.get_by_text("You are the host.").wait_for(timeout=8000)

    # A speaker's page opened from a link: nothing typed or clicked. It may not sound until clicked, and says so.
    speaker = browser.new_context(viewport={"width": 700, "height": 1300}).new_page()
    speaker.on("pageerror", lambda error: errors.append(f"link: {error}"))
    read = quiet(speaker)
    speaker.goto(profile_url + "?session=november")
    seen = settled(lambda: read("document.body.innerText.includes('You are viewing it.')"), True, 10.0)
    ok("a link to a running session: the page connected, as a viewer, nothing typed", seen is True, seen)
    seen = settled(lambda: (read(_BUZZ_SEEN) or {}).get("note"), _BUZZ_NOTE, 5.0)
    ok("and says that it must be clicked once before it can sound", seen == _BUZZ_NOTE, seen)
    speaker.context.close()

    # With the switch too; Leave Session then takes the session out of the address and keeps the switch.
    linked = opened("?session=NOVEMBER&silent")
    linked.get_by_text("You are viewing it.").wait_for(timeout=10000)
    ok("a link with the switch too: connected, and silent", _SILENT_TEXT in main_text(linked))
    button(linked, "Leave Session").click()
    button(linked, "Connect to Session").wait_for()
    settled(lambda: "session=" in linked.url, False, 3.0)
    ok("Leave Session: the session out of the address, the switch kept", "session=" not in linked.url and "silent" in linked.url, linked.url)
    linked.reload()
    button(linked, "Connect to Session").wait_for(timeout=10000)
    linked.wait_for_timeout(1500)
    ok("and a reload stays on the start screen", "You are viewing it." not in main_text(linked), main_text(linked)[:80])
    linked.context.close()

    # The host's own browser, its host's page open: the link makes a viewer; no second host.
    own = opened("?session=NOVEMBER", host.context)
    own.get_by_text("You are viewing it.").wait_for(timeout=10000)
    ok("a link opened in the browser hosting that session, its host's page open: a viewer", "You are the host." in main_text(host))
    own.close()

    # A session not yet created: it waits, and joins by itself; Cancel takes it out of the address.
    waiting = opened("?session=OSCAR")
    waiting.get_by_text("Please wait, connection pending").wait_for(timeout=10000)
    ok("a link to a session not yet created: waiting for it", True)
    creator = opened("")
    creator.locator('[data-testid="stApp"]').wait_for()
    creator.wait_for_timeout(1500)
    id_box(creator).fill("OSCAR")
    button(creator, "Create New Session").click()
    waiting.get_by_text("You are viewing it.").wait_for(timeout=5000)
    ok("once it is created: joined by itself", True)
    creator.context.close()
    waiting.context.close()
    cancelled = opened("?session=PAPA")
    button(cancelled, "Cancel").click(timeout=10000)
    button(cancelled, "Connect to Session").wait_for()
    settled(lambda: "session=" in cancelled.url, False, 3.0)
    ok("Cancel: the session out of the address", "session=" not in cancelled.url, cancelled.url)
    cancelled.context.close()
    host.context.close()


# A viewer's Day/Night on its panel: whether it is shown, its buttons, the one on; and a press of one.
_DAYNIGHT_SEEN = f"""() => {{
  const group = {_CLOCK_ROOT}.querySelector('.daynight');
  return {{ shown: !group.hidden, labels: [...group.querySelectorAll('button')].map(b => b.textContent),
           on: group.querySelector('button.on')?.dataset.d ?? null }};
}}"""
_DAYNIGHT_PRESS = f"(choice) => {_CLOCK_ROOT}.querySelector('.daynight button[data-d=\"' + choice + '\"]').click()"
# The same in a pop-out or pop-up: its background, a label's text and the figures' colours, and the one on.
_DAYNIGHT_IN_WINDOW = """() => {
  const css = (sel, prop) => getComputedStyle(document.querySelector(sel))[prop];
  return { painted: [css('body', 'backgroundColor'), css('[data-l="elapsed"]', 'color'), css('[data-v="elapsed"]', 'color'), css('[data-v="remaining"]', 'color')],
           on: document.querySelector('.daynight button.on')?.dataset.d ?? null };
}"""
_DAYNIGHT_COOKIE = "speakertime_daynight="
# Each of its buttons' text, background and underline, on a panel (``root`` the clock's shadow root) or a pop-out (its document).
_DAYNIGHT_BUTTONS = """(root) => Object.fromEntries(['day', 'night'].map((d) => {
  const s = getComputedStyle(root.querySelector('.daynight button[data-d="' + d + '"]'));
  return [d, [s.color, s.backgroundColor, s.textDecorationLine]];
}))"""
_DAYNIGHT_BUTTONS_PANEL = f"() => ({_DAYNIGHT_BUTTONS})({_CLOCK_ROOT})"
_DAYNIGHT_BUTTONS_WINDOW = f"() => ({_DAYNIGHT_BUTTONS})(document)"


def day_and_night(browser, errors: list[str], shot) -> None:
    """24. A viewer's Day/Night, on its panel and pop-out (Terry, 2026-11-04): no effect until pressed, kept for that session, the host's night-or-day change ignored."""
    white, black, yellow, green = rgb("#ffffff"), rgb("#000000"), rgb(CLOCK_COLOURS["yellow"]), rgb(CLOCK_COLOURS["green"])
    night, day = [black, white, white, white], [white, black, black, black]
    size = {"width": 800, "height": 1300}

    def buttons(on: str) -> dict:
        """Day black on white and Night white on black, whichever is on (Terry, 2026-11-04), the one on underlined."""
        return {"day": [black, white, "underline" if on == "day" else "none"], "night": [white, black, "underline" if on == "night" else "none"]}

    def connected(context, sid: str, create: bool) -> Page:
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(f"day and night: {error}"))
        page.goto(profile_url)
        page.locator('[data-testid="stApp"]').wait_for()
        page.wait_for_timeout(1500)
        id_box(page).fill(sid)
        button(page, "Create New Session" if create else "Connect to Session").click()
        page.get_by_text("You are the host." if create else "You are viewing it.").wait_for(timeout=8000)
        page.wait_for_timeout(800)
        return page

    def painted(page: Page) -> list[str]:
        seen = on_clock(page, _PAINT)
        return [seen["panel"], seen["label"], seen["elapsed"], seen["remaining"]]

    def after(page: Page, expected: list[str]) -> list[str]:
        return settled(lambda: painted(page), expected, 4.0)

    def choose(timer: str, colour: str) -> None:
        on_clock(host, _SWATCH, [timer, colour])

    def kept(page: Page) -> bool:
        return _DAYNIGHT_COOKIE in page.evaluate("document.cookie")

    host, viewer_context = connected(browser.new_context(viewport=size), "QUEBEC", True), browser.new_context(viewport=size)
    viewer = connected(viewer_context, "QUEBEC", False)

    seen = on_clock(viewer, _DAYNIGHT_SEEN)
    ok("a viewer's panel has Day and Night, Night on under a black session", seen == {"shown": True, "labels": ["Day", "Night"], "on": "night"}, seen)
    ok("the host's panel has neither", on_clock(host, _DAYNIGHT_SEEN)["shown"] is False)
    seen = on_clock(viewer, _DAYNIGHT_BUTTONS_PANEL)
    ok("Day black on white and Night white on black, Night underlined as the one on", seen == buttons("night"), seen)

    # Never pressed: no effect at all, the viewer following the host either way. The one shown is not a press.
    on_clock(viewer, _DAYNIGHT_PRESS, "night")
    viewer.wait_for_timeout(300)
    ok("pressing the one already shown does nothing: nothing kept", not kept(viewer), viewer.evaluate("document.cookie"))
    choose("elapsed", "black")
    seen = after(viewer, day)
    ok("not pressed: the host's black makes the viewer's panel white, its text and timers black", seen == day, seen)
    ok("and the toggle shows Day", on_clock(viewer, _DAYNIGHT_SEEN)["on"] == "day")
    choose("elapsed", "white")
    seen = after(viewer, night)
    ok("not pressed: the host's white back to black", seen == night, seen)

    # Pressed: shown at once, the host's page unchanged.
    on_clock(viewer, _DAYNIGHT_PRESS, "day")
    seen = painted(viewer)
    ok("Day pressed: at once the panel white, its text and timers black", seen == day, seen)
    seen = settled(lambda: on_clock(viewer, _DAYNIGHT_BUTTONS_PANEL), buttons("day"), 2.0)
    ok("the buttons in the same colours under Day, Day underlined now", seen == buttons("day"), seen)
    ok("kept, and the host's own panel still black", kept(viewer) and painted(host) == night, painted(host))
    shot(viewer, "16-day.png")

    # The host's colours still come through.
    choose("elapsed", "yellow")
    seen = after(viewer, [white, black, yellow, black])
    ok("the host's yellow comes through, on the viewer's white", seen == [white, black, yellow, black], seen)

    # A change that would force the other of night and day is ignored: Elapsed stays yellow, Remaining black.
    choose("remaining", "black")
    settled(lambda: painted(host)[0], white, 4.0)
    choose("elapsed", "white")
    settled(lambda: painted(host), night, 4.0)
    viewer.wait_for_timeout(2500)  # check-ins have brought it
    seen = painted(viewer)
    ok("the host's white, which would force night: ignored, Elapsed staying yellow, the panel white", seen == [white, black, yellow, black], [seen, painted(host)])

    # Night pressed: its black flips to white, the colour staying; the host's white is no change of night or day.
    on_clock(viewer, _DAYNIGHT_PRESS, "night")
    seen = painted(viewer)
    ok("Night pressed: the panel black, black text and figures white, the yellow kept", seen == [black, white, yellow, white], seen)
    choose("elapsed", "green")
    seen = after(viewer, [black, white, green, white])
    ok("the host's green comes through", seen == [black, white, green, white], seen)
    choose("elapsed", "white")
    seen = after(viewer, night)
    ok("the host's white on a viewer's night: taken", seen == night, seen)
    choose("elapsed", "black")
    settled(lambda: painted(host), day, 4.0)
    viewer.wait_for_timeout(2500)
    seen = painted(viewer)
    ok("the host's black, which would force day: ignored, the panel still black", seen == night, [seen, painted(host)])

    # Remembered through a reload, for this session and no other.
    viewer.reload()
    viewer.get_by_text("You are viewing it.").wait_for(timeout=8000)
    seen = after(viewer, night)
    ok("reloaded: still Night, the host's panel being white", seen == night and on_clock(viewer, _DAYNIGHT_SEEN)["on"] == "night" and painted(host) == day, seen)
    other_host = connected(browser.new_context(viewport=size), "ROMEO", True)
    other = connected(viewer_context, "ROMEO", False)
    on_clock(other_host, _SWATCH, ["elapsed", "black"])
    seen = after(other, day)
    ok("the same browser in another session, the toggle not used there: the default, following its host", seen == day, seen)
    other.close()
    other_host.context.close()
    viewer.wait_for_timeout(1500)

    # One choice for a page and its pop-out.
    with viewer.context.expect_page(timeout=5000) as popped:
        on_clock(viewer, _CLICK_POP)
    pip = popped.value
    pip.on("pageerror", lambda error: errors.append(f"day and night, its window: {error}"))
    viewer.wait_for_timeout(1000)
    seen = pip.evaluate(_DAYNIGHT_IN_WINDOW)
    ok("a viewer's pop-out opens at its Night", seen == {"painted": night, "on": "night"}, seen)
    seen = pip.evaluate(_DAYNIGHT_BUTTONS_WINDOW)
    ok("its buttons Day black on white and Night white on black, Night underlined", seen == buttons("night"), seen)
    line = pip.evaluate(_LINE_SEEN)
    ok("on one line with the full H:MM:SS|MMM.M, inside its window", (line["modes"], line["daynight"], line["oneRow"], line["inside"]) == (_FULL_MODES, True, True, True), line)
    pip.locator('.daynight button[data-d="day"]').click()
    seen = settled(lambda: pip.evaluate(_DAYNIGHT_IN_WINDOW), {"painted": day, "on": "day"}, 2.0)
    ok("Day pressed in the pop-out: its background white, its text and figures black", seen == {"painted": day, "on": "day"}, seen)
    seen = settled(lambda: pip.evaluate(_DAYNIGHT_BUTTONS_WINDOW), buttons("day"), 2.0)
    ok("its buttons in the same colours, Day underlined now", seen == buttons("day"), seen)
    seen = after(viewer, day)
    ok("and the panel with it", seen == day, seen)
    shot(pip, "17-popout-day.png")
    pip.close()
    viewer.wait_for_timeout(1000)

    # Leave Session forgets it: back in the session, the viewer follows the host again.
    button(viewer, "Leave Session").click()
    button(viewer, "Connect to Session").wait_for()
    viewer.wait_for_timeout(800)
    ok("Leave Session forgets it", "QUEBEC" not in viewer.evaluate("decodeURIComponent(document.cookie)").split(_DAYNIGHT_COOKIE)[-1] or not kept(viewer), viewer.evaluate("decodeURIComponent(document.cookie)"))
    button(viewer, "Connect to Session").click()
    viewer.get_by_text("You are viewing it.").wait_for(timeout=8000)
    choose("elapsed", "white")
    seen = after(viewer, night)
    ok("back in the session: the default again, following the host", seen == night, seen)

    # A viewer made host goes by the session's look, and has no toggle.
    on_clock(viewer, _DAYNIGHT_PRESS, "day")
    ok("pressed once more", painted(viewer) == day, painted(viewer))
    host.close()
    button(viewer, "Yes").wait_for(timeout=(HOST_GRACE_S + 10) * 1000)
    button(viewer, "Yes").click()
    viewer.get_by_text("You are the host.").wait_for(timeout=5000)
    seen = after(viewer, night)
    ok("a viewer made host: the session's own look, no toggle", seen == night and on_clock(viewer, _DAYNIGHT_SEEN)["shown"] is False, seen)
    viewer_context.close()


def host_row_and_link(browser, errors: list[str], shot) -> None:
    """25. Leave Session on the row of Start, Stop and Reset, at the group's right edge; Copy Link immediately left of it (Terry, 2026-11-04)."""
    def opened(context, address: str = "") -> Page:
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(f"copy link: {error}"))
        page.goto(profile_url + address)
        return page

    def created(context, sid: str, address: str = "") -> Page:
        page = opened(context, address)
        page.locator('[data-testid="stApp"]').wait_for()
        page.wait_for_timeout(1500)
        id_box(page).fill(sid)
        button(page, "Create New Session").click()
        page.get_by_text("You are the host.").wait_for(timeout=8000)
        page.wait_for_timeout(800)
        return page

    def on_clipboard(page: Page) -> str:
        return page.evaluate("navigator.clipboard.readText()")

    context = browser.new_context(viewport={"width": 1100, "height": 1000}, permissions=["clipboard-read", "clipboard-write"])
    host = created(context, "TANGO", "?silent")
    ok("this host's page is silent", _SILENT_TEXT in main_text(host))
    boxes = {name: button(host, name).bounding_box() for name in ("Start", "Stop", "Reset", "Copy Link", "Leave Session")}
    middles = [box["y"] + box["height"] / 2 for box in boxes.values()]
    ok("Start, Stop, Reset, Copy Link and Leave Session on one row", max(middles) - min(middles) <= 3, middles)
    reset, copy, leave = boxes["Reset"], boxes["Copy Link"], boxes["Leave Session"]
    ok("Copy Link immediately left of Leave Session, the two apart from Reset",
       0 <= leave["x"] - (copy["x"] + copy["width"]) <= 16 and copy["x"] - (reset["x"] + reset["width"]) > 100, boxes)
    edge = host.get_by_text(_COOKIE_TEXT).bounding_box()
    ok("Leave Session right-aligned with the group's edge", abs(leave["x"] + leave["width"] - (edge["x"] + edge["width"])) <= 2, [leave, edge])
    shot(host, "18-host-row.png")

    # The link: of the form asked for, never with this page's ?silent.
    base = profile_url.split("?")[0].rstrip("/")
    host.bring_to_front()
    button(host, "Copy Link").click()
    seen = settled(lambda: on_clipboard(host), f"{base}/?session=TANGO", 3.0)
    ok("Copy Link: the session's link on the clipboard, without ?silent", seen == f"{base}/?session=TANGO", seen)
    ok("the button says so", button(host, "Copied").count() == 1 or "Copied" in main_text(host), main_text(host)[-200:])
    seen = settled(lambda: button(host, "Copy Link").count(), 1, 4.0)
    ok("and then reads Copy Link again", seen == 1, seen)
    seen = on_clock(host, _CLOCK_TEXTS)  # waits: a run of the page rebuilds the clock, missing for a moment
    ok("the host still hosting, the timer untouched", "You are the host." in main_text(host) and seen["elapsed"] == "0:00", seen)

    # The clipboard's API refused: the older way.
    host.evaluate("navigator.clipboard.writeText('other')")
    host.evaluate("() => { navigator.clipboard.writeText = () => Promise.reject(new Error('refused')); }")
    button(host, "Copy Link").click()
    seen = settled(lambda: on_clipboard(host), f"{base}/?session=TANGO", 3.0)
    ok("with the clipboard's API refused, the older way copies it", seen == f"{base}/?session=TANGO", seen)

    # An ID with Markdown's and an address's characters: encoded, and the link goes there.
    special = created(context, "A&B#1+@")
    special.bring_to_front()
    button(special, "Copy Link").click()
    want = f"{base}/?session=A%26B%231%2B%40"
    seen = settled(lambda: on_clipboard(special), want, 3.0)
    ok("an ID with & # + @: encoded in the link", seen == want, seen)
    other = browser.new_context(viewport={"width": 800, "height": 1000})
    viewer = other.new_page()
    viewer.on("pageerror", lambda error: errors.append(f"copy link, viewer: {error}"))
    viewer.goto(want)
    viewer.get_by_text("You are viewing it.").wait_for(timeout=10000)
    ok("and that link connects to that session", "A&B#1+@" in main_text(viewer), main_text(viewer)[:120])
    ok("a viewer has Leave Session and no Copy Link", button(viewer, "Leave Session").count() == 1 and button(viewer, "Copy Link").count() == 0)
    other.close()
    context.close()


def frame_minimum_and_recovery(browser, errors: list[str]) -> None:
    """26. The frame's minimum in a small window, twice what it was, and the text around it half as large again as all inside would leave it, a scroll reaching the rest (Terry, 2026-11-04); the fit cannot run away, and has a last look (a page left at the floor, seen once live)."""
    def connected(context, sid: str, create: bool) -> Page:
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(f"frame: {error}"))
        page.goto(profile_url)
        page.locator('[data-testid="stApp"]').wait_for()
        page.wait_for_timeout(1500)
        id_box(page).fill(sid)
        button(page, "Create New Session" if create else "Connect to Session").click()
        page.get_by_text("You are the host." if create else "You are viewing it.").wait_for(timeout=8000)
        return page

    def sized(width: int, height: int):
        return browser.new_context(viewport={"width": width, "height": height})

    def fit_of(page: Page) -> dict:
        page.wait_for_function(f"() => {{ {_HAS_FIT}; }}", polling=100, timeout=20000)
        return on_clock(page, _FIT_SEEN)

    # The room is the window less the column's padding (about 2 x 3.2 rem) and a pixel; the panel keeps 2/3 of it, up to 250 px.
    host = connected(sized(1545, 784), "UNIFORM", True)
    usual = fit_of(host)
    ok("a window of the usual size left as it was: the page's text at full size", usual["page"] == 1 and usual["size"][1] > 250, usual)
    # A column's scale of 1.5 times what would keep all of the page inside the window takes 1.5 times the room its text has.
    for width, height, want, was, who in ((500, 400, 198, 99, "a viewer's page"), (700, 500, 250, 132, "a viewer's page")):
        page = connected(sized(width, height), "UNIFORM", False)
        seen = fit_of(page)
        ok(f"{who} in a window of {width} by {height}: the panel {want} px high (it was {was}), the text half as large again as all inside would leave it, a scroll reaching the rest, the title clear of the top bar",
           abs(seen["size"][1] - want) <= 12 and seen["page"] < 1 and 1.4 <= seen["text"] <= 1.55 and scrolled(seen) and seen["titleClear"], seen)
        if width == 500:
            small = page
        else:
            page.context.close()
    laptop = connected(sized(1366, 600), "LAPTOP", True)
    seen = fit_of(laptop)
    ok("the host's page on a small laptop, 1366 by 600: the panel 250 px high, the text half as large again as all inside would leave it (0.621 before), a scroll reaching the rest",
       abs(seen["size"][1] - 250) <= 12 and 0.85 <= seen["page"] < 1 and 1.4 <= seen["text"] <= 1.55 and scrolled(seen), seen)
    laptop.context.close()
    laptop = connected(sized(1366, 600), "UNIFORM", False)
    seen = fit_of(laptop)
    ok("a viewer's page there: never above its full size, though half as large again would be", seen["page"] == 1 and abs(seen["size"][1] - 250) <= 12 and seen["text"] < 1.4 and scrolled(seen), seen)
    laptop.context.close()
    tiny = connected(sized(320, 200), "UNIFORM", False)
    seen = fit_of(tiny)
    ok("a window of almost no height: the panel's scale not below 0.2, the column's not below 0.3, the frame in view", seen["page"] >= 0.3 and seen["panel"] >= 0.2 and scrolled(seen), seen)
    tiny.context.close()

    # A window where the column's scale changes how the text wraps: at 1 the host's page needs 0.565, and at 0.565 it
    # measures as if 1 would fit. It must settle whatever the timing (it flipped until the fit's time ran out, passing or
    # failing by chance): so several pages, and how many times each changed its scales.
    counting = """() => { const before = () => window.speakerTime?.fitPage; let last = before(); window.__flips = 0;
      setInterval(() => { const now = before(); if (now !== last) { last = now; window.__flips += 1; } }, 20); }"""
    for attempt in range(1, 5):
        context = sized(390, 750)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(f"frame, phone: {error}"))
        page.goto(profile_url)
        page.locator('[data-testid="stApp"]').wait_for()
        page.wait_for_timeout(1500)
        id_box(page).fill(f"PHONE{attempt}")
        page.evaluate(counting)
        button(page, "Create New Session").click()
        page.get_by_text("You are the host.").wait_for(timeout=8000)
        seen = fit_of(page)
        flips = page.evaluate("window.__flips")
        ok(f"the host's page at 390 by 750 (try {attempt}): settled, the frame in view, a scroll reaching the rest, its scale changing only a few times",
           scrolled(seen) and flips <= 6, [flips, seen])
        context.close()

    # A fit forced to be past its time for rising (3 s), as a page arriving slowly may be.
    force = "(ago) => { const at = performance.now(); window.speakerTime.fitting = { at: at - ago, changed: at, widest: 1, most: 1, rose: false }; }"
    column = '[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"]'
    before = fit_of(small)
    small.evaluate(f"""() => {{ const s = document.createElement('style'); s.id = 'nozoom'; s.textContent = '{column} {{ zoom: 1 !important; }}'; document.head.appendChild(s); }}""")
    small.evaluate(force, 4000)
    small.wait_for_timeout(800)
    seen = on_clock(small, _FIT_SEEN)
    small.evaluate("document.getElementById('nozoom').remove()")
    # The page's full-size text wraps a little differently, so its scale may move a little; what it must not do is run away to its floor.
    ok("measured with the column's scale not in effect: no fall to the floor", abs(seen["page"] - before["page"]) <= 0.08, [before, seen])
    fit_of(small)

    before = fit_of(small)
    small.evaluate(force, 4000)
    small.evaluate(f"""() => {{ const d = document.createElement('div'); d.id = 'tall'; d.style.height = '4000px'; document.querySelector('{column}').appendChild(d); }}""")
    small.wait_for_timeout(500)
    fallen = on_clock(small, _FIT_SEEN)
    small.evaluate("document.getElementById('tall').remove()")
    seen = fit_of(small)
    # Fitted afresh, it may stand a little off where the last check left it: the wrapping again.
    ok("something tall for a moment, late: the column's scale falls to its floor, and a fit settled there is begun once more",
       fallen["page"] == 0.3 and abs(seen["page"] - before["page"]) <= 0.08 and scrolled(seen), [before, fallen, seen])

    # No window yet (Community Cloud's frame hidden): the fit's time does not run out.
    small.evaluate(force, 9500)
    small.evaluate("document.querySelector('[data-testid=\"stMain\"]').style.display = 'none'")
    small.wait_for_timeout(1000)
    waiting = small.evaluate("window.speakerTime.fitting !== null")
    small.evaluate("document.querySelector('[data-testid=\"stMain\"]').style.display = ''")
    seen = fit_of(small)
    ok("the page hidden past the fit's limit: it still waits, and ends once there is a window", waiting and scrolled(seen), [waiting, seen])
    small.context.close()

    # The way the floor was seen: a page waiting on a link, connecting when its session is created. Here with an empty
    # cache, a slowed network and slowed scripts, the page that creates the session in front.
    for attempt, sid in enumerate(("VICTOR", "WHISKEY"), 1):
        context = sized(1024, 768)
        waiting_page = context.new_page()
        waiting_page.on("pageerror", lambda error: errors.append(f"frame, slowed: {error}"))
        cdp = context.new_cdp_session(waiting_page)
        cdp.send("Network.enable")
        cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 200, "downloadThroughput": 500 * 1024, "uploadThroughput": 500 * 1024})
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": 6})
        waiting_page.goto(profile_url + f"?session={sid}")
        waiting_page.get_by_text("Please wait, connection pending").wait_for(timeout=40000)
        creator = connected(sized(1024, 768), sid, True)
        creator.bring_to_front()
        waiting_page.get_by_text("You are viewing it.").wait_for(timeout=20000)
        seen = fit_of(waiting_page)
        ok(f"connecting from the waiting screen, cold and slowed (try {attempt}): fitted as usual", seen["page"] == 1 and 0.5 < seen["panel"] < 0.75 and seen["inside"], seen)
        creator.context.close()
        context.close()
    host.context.close()


_KICKS_START = "() => { window.speakerTimeKicks = setInterval(() => window.speakerTime.kick(), 3); }"
_KICKS_STOP = "() => clearInterval(window.speakerTimeKicks)"
_WARNING_SHOWN = f"""() => {_CLOCK_ROOT}.querySelector('[data-v="warning"]').textContent"""


def yes_with_check_ins_waiting(browser, errors: list[str]) -> None:
    """27. A viewer's Yes keeps the session's Time Limit and warning with a check-in waiting, as one may be when it comes: before v1.18 the new host's boxes came up at 0.0, and the next check-in reported that as the host's change (1 full run of 8; every try here)."""
    def connected(context, sid: str, create: bool) -> Page:
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(f"yes: {error}"))
        page.goto(profile_url)
        page.locator('[data-testid="stApp"]').wait_for()
        page.wait_for_timeout(1500)
        id_box(page).fill(sid)
        button(page, "Create New Session" if create else "Connect to Session").click()
        page.get_by_text("You are the host." if create else "You are viewing it.").wait_for(timeout=8000)
        return page

    for attempt in (1, 2):
        sid = f"KEEP{attempt}"
        contexts = [browser.new_context(viewport={"width": 800, "height": 1500}) for _ in range(2)]
        host = connected(contexts[0], sid, True)
        warning = host.get_by_role("spinbutton", name="Time Left Warning (minutes)")
        limit_box(host).fill("2.25")
        limit_box(host).press("Enter")
        host.wait_for_timeout(800)
        warning.fill("1")
        warning.press("Enter")
        host.wait_for_timeout(800)
        viewer = connected(contexts[1], sid, False)
        viewer.wait_for_timeout(1200)
        before = (clock(viewer)["limit"], on_clock(viewer, _WARNING_SHOWN))
        host.close()
        button(viewer, "Yes").wait_for(timeout=(HOST_GRACE_S + 15) * 1000)
        viewer.evaluate(_KICKS_START)
        viewer.wait_for_timeout(300)
        button(viewer, "Yes").click()
        viewer.get_by_text("You are the host.").wait_for(timeout=15000)
        viewer.wait_for_timeout(3000)
        viewer.evaluate(_KICKS_STOP)
        viewer.wait_for_timeout(2500)
        after = (clock(viewer)["limit"], on_clock(viewer, _WARNING_SHOWN))
        boxes = (limit_box(viewer).input_value(), viewer.get_by_role("spinbutton", name="Time Left Warning (minutes)").input_value())
        ok(f"a Yes with check-ins waiting (try {attempt}): the Time Limit and warning kept, on the panel and in the host's boxes",
           after == before == ("2:18", "1:00") and boxes == ("2.3", "1.0"), [before, after, boxes])
        for context in contexts:
            context.close()


def box_cleared(browser, errors: list[str]) -> None:
    """28. A Time Limit or warning box cleared stays blank, its hint showing, and stays so through a reload; 0 typed shows as 0.0; either way, no limit (Terry, 2026-11-04: it filled back with 0.0)."""
    page = browser.new_context(viewport={"width": 800, "height": 1500}).new_page()
    page.on("pageerror", lambda error: errors.append(f"cleared: {error}"))
    page.goto(profile_url)
    page.locator('[data-testid="stApp"]').wait_for()
    page.wait_for_timeout(1500)
    id_box(page).fill("CLEARED")
    button(page, "Create New Session").click()
    page.get_by_text("You are the host.").wait_for(timeout=8000)
    warning = page.get_by_role("spinbutton", name="Time Left Warning (minutes)")

    def enter(box, text: str) -> None:
        box.fill(text)
        box.press("Enter")
        page.wait_for_timeout(1500)

    enter(limit_box(page), "5")
    enter(warning, "1")
    ok("both set: 5:00 on the panel", (clock(page)["limit"], limit_box(page).input_value(), warning.input_value()) == ("5:00", "5.0", "1.0"), clock(page))
    enter(limit_box(page), "")
    enter(warning, "")
    seen = (clock(page)["limit"], limit_box(page).input_value(), warning.input_value())
    ok("both cleared: blank, no limit on the panel", seen == ("none", "", ""), seen)
    page.reload()
    page.get_by_text("You are the host.").wait_for(timeout=8000)
    page.wait_for_timeout(1500)
    seen = (clock(page)["limit"], limit_box(page).input_value(), warning.input_value())
    ok("still blank after a reload", seen == ("none", "", ""), seen)
    enter(limit_box(page), "0")
    seen = (clock(page)["limit"], limit_box(page).input_value())
    ok("0 typed: shown as 0.0, no limit on the panel", seen == ("none", "0.0"), seen)
    page.context.close()


profile_url = ""
"""The page's address, for windows_of_one_browser, popups_of_one_browser and buzzer_held (set by run)."""


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
