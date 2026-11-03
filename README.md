# SpeakerTime

A talk's **Elapsed Time** and **Time Remaining**, shared live between a host
(usually the session's chair) and any number of viewers (usually the speaker,
on the machine showing the slides). It runs in a browser; nothing to install.

## Using it

**The host**: leave the Session ID box blank (a random six-digit ID is chosen)
or type one, and press **Create New Session**. Set the **Time Limit** in
minutes, to one decimal place (2.5 is two and a half minutes), and use
**Start**, **Stop** (a pause) and **Reset** (pressed while running, it stops
the timer too). Once the timer has run, Start reads **Restart**, greyed while
running, and goes on from where it stopped; after Reset it reads Start
again (v1.11). Time Remaining is the Time Limit minus
Elapsed Time. It turns red and blinks when it reaches zero, and once the limit
is passed it goes negative and its label reads **Overtime**, red and blinking
too; with a blank, 0 or negative limit it stays at zero (and plain). The
slider on the right of each timer sets its size on every viewer's panel and
pop-out as well as the host's, from 25 to 800, starting at 200 (800 is four
times 200's size). The panel is as wide as its figures need, not as the
window (v1.11): the sliders and colour buttons stand just beside the larger
timer, and the panel stands in the middle of the page. A panel wider than
the window runs past its edge, with a scroll bar under it: make the window
wider, or scroll. On a phone (a window under 640 pixels wide) the panel stays
within the page and the figures shrink to fit it, as before.

A **pop-out** (and a pop-up) shows the host's panel scaled as a whole to fit
its window (v1.11): make the window larger or smaller by hand and everything
in it grows or shrinks together, as a larger or smaller copy of what the host
sees. When the sizes change (a slider, the switch, the Time Left Warning, a
longer time), the window changes size to show them at the same scale, where
the browser allows it. Chrome lets a page resize a pop-up at any time, but a
pop-out only within a few seconds of a click or a key on the page, so the
host's own pop-out follows the host's sliders, while a viewer's keeps its
size and scales its contents to fit it instead.

**Colours**: beside each slider, a column of colour buttons sets that
timer's colour for everyone: white (the default), black, yellow, orange,
green, blue and magenta (never red: red means time is out). Choosing black
for one timer turns the other black too if it was white, and turns the
panel white with black text; choosing white does the opposite.

**Time Left Warning**: to the right of the Time Limit box, in minutes (blank
for none). Once set, it shows beside the Time Limit on every panel and
pop-out, the two together in the middle as the switch's buttons are, and
while Time Remaining is that many minutes or fewer (and more
than zero) it changes to the warning's colour. That colour is green while
Time Remaining is white or black, and otherwise the colour opposite Time
Remaining's on the colour wheel (the **A** button); the four buttons after it
are other colours to choose instead.

**Buzzer** (v1.10): on the host's panel only, beside Pop out. While the host
holds it down (the mouse button, a finger, or Space or Enter on it), Time
Remaining reads **BUZZ** in steady red on every panel and pop-out, the
host's included, and a buzzer sounds on every viewer's page (not the
host's). Let go, everything is as before. It stops by itself 15 seconds
after the host's page last said it was held, so a page that loses its
connection with the button down cannot leave it sounding. A browser lets a
page make a sound only once someone has clicked it, or pressed a key on it,
since it loaded. A viewer who pressed Connect to Session has done that; a
viewer's page that reconnected by itself after a reload has not, and says
so under its clock: "Click anywhere on this page once so that it can sound
the buzzer."

**H:MM:SS or MMM.M**: the switch under the timers shows them as hours, minutes
and seconds, or as minutes to a tenth ("12.5"). Every panel and every pop-out
has its own switch. A panel starts at the host's choice when it opens, and
goes back to it whenever a host connects; a pop-out starts at its panel's.

**A viewer**: type the session's ID and press **Connect to Session**. If the
host has not created it yet, the page waits ("Please wait, connection
pending") and connects by itself as soon as it is created; **Cancel** goes
back.

Session IDs: letters, numbers and the characters `- _ . # @ + &`, at most 20.
Spaces, tabs and other characters are ignored, and so is case: "room 5b" and
"ROOM5B" are the same session.

**If the host's window closes** or loses its connection, the session keeps
running as if nothing happened. After about 10 seconds every viewer is offered
**Assume Host Role?** (Yes or No). The offer stays up until a host connects,
even after a No.

**Coming back**: a reload takes each window back to its own session, without
typing anything, as host if it was the host (unless someone else has taken the
role meanwhile). A window opened again goes back by itself when there is no
doubt which session it means: the one session it would host, or the one
session of yours still running and not open in another window of the browser.
With several, it offers a **Rejoin** button for each. One browser can host
several sessions, view several, or both, each in a window of its own; a
host and a viewer of the same session can be two tabs of one browser (handy
for trying it out): only the host's own tab is the host again after a reload.
**Leave Session** forgets the session.

**A session used before**: a session nobody has open goes six hours later
(or when the app restarts), but its settings are kept: **Create New
Session** under its ID later starts from its Time Limit, Time Left Warning,
sizes, colours and way of showing the times, and says so. So a host can set a
session up under an ID, leave, give the ID to someone else, and that person's
Create New Session starts from those settings. They are kept in
`remembered_sessions.json` beside the program (the last 500 sessions), and,
from v1.9, in a secret GitHub gist as well, once the app is given a token
for one (below, Running it). On Streamlit Community Cloud the file is lost
when the app is rebooted (measured 2026-11-03: a Reboot starts a new machine
from a fresh copy of the repository), so without the gist the settings last
only until the next Reboot, and perhaps the next sleep.

## Its own window, kept on top

A web page cannot make itself open in a window of its own, but the browser can:

- **Chrome**: open the page, then ⋮ menu → *Cast, save, and share* →
  *Install page as app…*. **Edge**: ⋯ menu → *Apps* → *Install this site as
  an app*. Either one puts a SpeakerTime icon on the desktop or taskbar that
  opens the page in its own window, without tabs or an address bar.
- **Pop out (stays on top)**, under the timers, opens a small window holding
  just the timers. It stays above every other window, the slides included,
  and can be resized. This needs Chrome or Edge on a computer (version 116 or
  later). The page that opened it must stay open, but it can be hidden behind
  other windows.
- Chrome keeps **one** such pop-out for the whole browser: every tab, window
  and profile, Incognito too, and a video's picture-in-picture (YouTube, or
  another site's pop-out) closes it. Edge keeps its own, so Chrome and Edge
  side by side can have one each. While another tab of the same browser has
  the pop-out, a tab's button reads **Pop-up (may get obscured)**: a small
  window like the pop-out, which any number of tabs may have, but which other
  windows can cover. Once the pop-out closes, the tabs without a pop-up offer
  it again, and the first to press it has it. A pop-up stays open if its tab
  is closed, and then says so instead of showing the timers; a tab that is
  reloaded takes its pop-up back.
- When SpeakerTime is updated, a page left open reloads itself once; a
  pop-out it had is closed (the page says so), and a pop-up is taken back.
- Or, on Windows, Microsoft PowerToys' *Always On Top* (Win+Ctrl+T) keeps any
  window on top, including an installed app's or a pop-up.

## Running it

```bash
pip install -r requirements.txt
```

```bash
python -m streamlit run streamlit_app.py
```

Then open http://localhost:8501. Run it from this folder: Streamlit reads
`.streamlit/config.toml` from the current folder.

On **Streamlit Community Cloud** (free), deploy this repository's `main`
branch with `streamlit_app.py` as the entry file. All the sessions live in
the app's memory, so anything that restarts the app ends every session that
is running: a Reboot, a push of new code, or a sleep after 12 hours without
visitors. The first visitor after a sleep has to wake the app, which takes
up to a minute, so open the page a few minutes before a talk.

**Keeping the remembered settings through a Reboot** (v1.9): give the app a
GitHub token that may write gists, and it keeps the settings in a secret gist
of that account as well. It reads the gist when it starts, and writes to it
a few seconds after each change. It finds its gist by the gist's
description, and makes it the first time it writes, so the token is the
only setting:

1. On GitHub: Settings, Developer settings, Personal access tokens, Tokens
   (classic), Generate new token (classic). Tick the **gist** scope alone,
   choose an expiry ("No expiration" means nothing to renew), and copy the
   token.
2. On Streamlit Community Cloud: the app's Settings, Secrets, and add the
   line `SPEAKERTIME_GIST_TOKEN = "the token"`. Save.

A secret gist is not listed anywhere, but anyone given its address can read
it: it holds session IDs and their settings, nothing else. Locally, the
environment variable `SPEAKERTIME_GIST_TOKEN` does the same; with neither,
the settings are kept in the file alone.

## Checks

```bash
python tools/verify_logic.py
```

The timer, session IDs, and the rules for hosts, with a clock stepped by hand.

```bash
python tools/verify_web.py
```

The screens and buttons through Streamlit's AppTest, no browser needed.

```bash
python tools/verify_browser.py --chrome
```

A host and viewers in browsers of their own, through a session's whole life.
It needs `pip install playwright`; `--chrome` uses the installed Google Chrome,
and `--shots FOLDER` keeps a screenshot of each screen.

## Layout

| file | what it holds |
|---|---|
| `speakertime/timer.py` | the timer: Start, Stop, Reset, the Time Limit, what is shown |
| `speakertime/ids.py` | session IDs: what may be typed, how it is read, random ones |
| `speakertime/sessions.py` | the sessions every page shares, and the rules for hosts |
| `speakertime/store.py` | the remembered settings kept in a secret GitHub gist as well (v1.9) |
| `speakertime/shared.py` | helpers copied from ratesplot, meant for the shared library planned for it and NG_analysis |
| `speakertime/web.py` | the page (Streamlit), the clock and pop-out scripts, and the cookies |
| `streamlit_app.py` | the entry point Streamlit runs |
| `tools/` | the three checks above |
