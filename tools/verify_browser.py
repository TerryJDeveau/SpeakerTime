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

        ok("no script errors on any page", not errors, errors)
        browser.close()


def main() -> int:
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
