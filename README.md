# SpeakerTime

A talk's **Elapsed Time** and **Time Remaining**, shared live between a host
(usually the session's chair) and any number of viewers (usually the speaker,
on the machine showing the slides). It runs in a browser; nothing to install.

## Using it

**The host**: leave the Session ID box blank (a random six-digit ID is chosen)
or type one, and press **Create New Session**. Set the **Time Limit** in
minutes, to one decimal place (2.5 is two and a half minutes), and use
**Start**, **Stop** (a pause: Start resumes) and **Reset** (pressed while
running, it stops the timer too). Time Remaining is the Time Limit minus
Elapsed Time. It turns red and blinks when it reaches zero, and once the limit
is passed it goes negative and its label reads **Overtime**, red and blinking
too; with a blank, 0 or negative limit it stays at zero (and plain). The
slider on the right of each timer sets its size, from a quarter to four
times, on every viewer's panel and pop-out as well as the host's (the
figures never grow wider than the panel, so the largest settings show only
where the height, not the width, limits them).

**Colours**: beside each slider, a column of colour buttons sets that
timer's colour for everyone: white (the default), black, yellow, orange,
green, blue and magenta (never red: red means time is out). Choosing black
for one timer turns the other black too if it was white, and turns the
panel white with black text; choosing white does the opposite.

**Time Left Warning**: to the right of the Time Limit box, in minutes (blank
for none). Once set, it shows beside the Time Limit on every panel and
pop-out, and while Time Remaining is that many minutes or fewer (and more
than zero) it changes to the warning's colour. That colour is green while
Time Remaining is white or black, and otherwise the colour opposite Time
Remaining's on the colour wheel (the **A** button); the four buttons after it
are other colours to choose instead.

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
  other windows. A browser keeps one pop-out at a time: opening one from
  another tab or window of the same browser closes the first.
- Or, on Windows, Microsoft PowerToys' *Always On Top* (Win+Ctrl+T) keeps any
  window on top, including an installed app's.

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
| `speakertime/shared.py` | helpers copied from ratesplot, meant for the shared library planned for it and NG_analysis |
| `speakertime/web.py` | the page (Streamlit), the clock and pop-out scripts, and the cookies |
| `streamlit_app.py` | the entry point Streamlit runs |
| `tools/` | the three checks above |
