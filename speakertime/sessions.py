"""The sessions every page shares, and the rules for who hosts them.

One process serves every visitor (Streamlit Community Cloud runs one per
app), each page in a thread of its own, so a table in this module behind one
lock is what the host's page and every viewer's page see. Nothing is written
to disk: a restart of the app (a reboot, a push, a sleep after 12 hours
without visitors) ends every session.

Nothing here imports Streamlit. A page is named by an id the caller gives
(the page's Streamlit session id), and the caller passes in a way to ask
whether a page is still connected; tests pass a clock they step by hand.

The rules (Terry, 2026-10-30):
- Create New Session with an ID whose session has a host connected is
  refused ("choose another"). Otherwise the ID becomes the creator's.
- A session goes on running when its host leaves (the window closed, the
  connection lost). Anyone connected to it is then offered the host's role
  ("Assume Host Role?"), an offer that stays up until a host connects, even
  after a No.
- A host who comes back (a reload, the window opened again) is the host again
  without typing anything, by a token kept in a cookie in their browser
  (``web``), unless someone else has taken the host's role meanwhile.
- (2026-11-01) The host sets the size of each timer's figures for everyone
  (``set_size``). Each panel chooses its own way of showing the times; the
  host's is the default of a page as it opens, and of every page again each
  time a host connects (``host_mode``, ``host_epoch``).
- (Terry, 2026-11-01, item 3) A host and a viewer may be two tabs of one
  browser. Every tab of a browser holds the host's token (the cookie is the
  browser's), so a page already showing the session takes the host's role
  by the token only if it is the host's own tab (a reload of it: ``host_tab``);
  another tab is a viewer like any other, offered the role after the grace.
  Measured 2026-11-03: before this, the host's tab reloaded while a viewer's
  tab of the same browser was open, and the viewer's tab became the host.
- (Terry, 2026-11-01, items 4, 9 and 10) The host sets each timer's colour
  and the Time Left Warning, with its colour, for everyone (``set_colour``,
  ``control("warning")``, ``set_warning_colour``; the rules in ``colours``).
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import ids
from .colours import (
    AUTO, CLOCK_COLOURS, DARK, DEFAULT_COLOUR, WARNING_CANDIDATES, choose, warning_choices, warning_colour,
)
from .timer import CLOCK, MODES, Timer, limit_minutes, limit_seconds

HOST_GRACE_S = 10.0
"""A host's page that has closed still counts as the host for this long after it last checked in.

A dropped connection that comes back within it (Streamlit reconnects a page
by itself) never offers the host's role to anyone. Its page checks in once a
second while shown (``web.CHECK_IN_S``).
"""

FORGET_PAGE_S = 120.0
"""A viewer's page that is neither connected nor heard from for this long is taken off the session's list."""

EXPIRE_AFTER_S = 6 * 3600.0
"""A session nobody has had open for this long is removed, and its ID is free again.

Long enough for a day's programme with a long break (a session is a few
numbers, so keeping it costs nothing); Community Cloud's app sleeps after
12 hours without visitors anyway, which ends every session.
"""

TOKEN_BYTES = 16
"""The host's token: 128 random bits, sent as 22 URL-safe characters (``secrets.token_urlsafe``)."""

HOST = "host"
VIEWER = "viewer"

SIZED = ("elapsed", "remaining")
"""The timers whose size the host sets (the Time Limit's figures keep theirs)."""

SIZE_MIN, SIZE_MAX, SIZE_STEP, SIZE_DEFAULT = 25, 400, 5, 100
"""A timer's size, in percent of the figures' own size on each panel or pop-out (which follows its width): a quarter to four times.

The most was 200 until Terry asked for twice it (2026-11-01). The figures
still never grow wider than their column (``web``), so where the width
already limits them a larger setting shows no larger: in a wide, low pop-out
the height limits them first, and there they grow up to four times.
"""


class Refused(Exception):
    """An action not done, with the reason to show the person (the message is shown as it is)."""


@dataclass
class Session:
    sid: str
    token: str
    """The host's token: a page that holds it may host without being asked (``Registry.join``, ``check_in``)."""
    timer: Timer = field(default_factory=Timer)
    host: str | None = None
    """The hosting page's id; None once the host has left by Leave Session."""
    host_tab: str | None = None
    """The hosting page's tab (a random id the tab keeps through its reloads, ``web``); None where the page could not say."""
    host_seen: float = 0.0
    """When the host's page last checked in (the monotonic clock)."""
    viewers: dict[str, float] = field(default_factory=dict)
    """Each viewer's page id and when it last checked in."""
    attended: float = 0.0
    """When any page last had the session open: it is removed EXPIRE_AFTER_S after."""
    version: int = 0
    """Raised by every change a page shows: the timer, the Time Limit, the host, the sizes."""
    sizes: dict[str, int] = field(default_factory=lambda: dict.fromkeys(SIZED, SIZE_DEFAULT))
    """Each timer's size in percent (SIZED), as the host set it."""
    host_mode: str = CLOCK
    """The host's way of showing the times: the default of a page as it opens, and of every page when a host connects."""
    host_epoch: int = 0
    """Raised each time a page becomes the host: every other page then takes ``host_mode`` again."""
    browsers: dict[str, str] = field(default_factory=dict)
    """Each page's browser (a random id its cookie keeps), so that a browser's own pages can be told (``Registry.candidates``)."""
    colours: dict[str, str] = field(default_factory=lambda: dict.fromkeys(SIZED, DEFAULT_COLOUR))
    """Each timer's figures' colour (a name of ``colours.CLOCK_COLOURS``), as the host set it."""
    theme: str = DARK
    """The panel's theme (``colours.DARK`` or ``LIGHT``), as the host's last choice of black or white left it."""
    warning: float | None = None
    """The Time Left Warning in minutes, to a tenth; None (blank), 0 or less mean none."""
    warning_colour: str = AUTO
    """The warning's colour: AUTO (Terry's rule) or a name of ``colours.WARNING_CANDIDATES``."""



@dataclass(frozen=True)
class Shown:
    """What a page shows of a session at one moment."""

    elapsed: float
    running: bool
    limit: float | None
    version: int
    hosted: bool
    """True while a host's page is connected (or was, within HOST_GRACE_S)."""
    viewers: int
    """How many viewers' pages are connected."""
    sizes: dict[str, int]
    """Each timer's size in percent, as the host set it."""
    host_mode: str
    """The host's way of showing the times."""
    host_epoch: int
    """How many times a page has become the host."""
    colours: dict[str, str]
    """Each timer's figures' colour, a name of ``colours.CLOCK_COLOURS``."""
    theme: str
    """The panel's theme."""
    warning: float | None
    """The Time Left Warning in minutes; None for none."""
    warning_colour: str
    """The warning's colour as chosen: AUTO or a name of ``colours.WARNING_CANDIDATES``."""

    def for_script(self) -> dict[str, object]:
        """What the page's script needs to run the clock (``web._HUB_JS``): the limit and the warning in whole seconds (None for none), and the colours as drawn."""
        return {
            "elapsed": self.elapsed,
            "running": self.running,
            "limit": limit_seconds(self.limit),
            "warning": limit_seconds(self.warning),
            "version": self.version,
            "sizes": dict(self.sizes),
            "look": {
                "theme": self.theme,
                "names": dict(self.colours),
                "elapsed": CLOCK_COLOURS[self.colours["elapsed"]],
                "remaining": CLOCK_COLOURS[self.colours["remaining"]],
                "warning": warning_colour(self.warning_colour, self.colours),
            },
        }

    def warning_buttons(self) -> tuple[list[tuple[str, str]], str]:
        """The warning's colour buttons for the host: (name, hex) for AUTO and the four others, and the one chosen (AUTO where the choice is not among them)."""
        choices = warning_choices(self.colours)
        buttons = [(AUTO, warning_colour(AUTO, self.colours))] + choices
        chosen = self.warning_colour if any(name == self.warning_colour for name, _ in choices) else AUTO
        return buttons, chosen


@dataclass(frozen=True)
class Checked:
    """A page's check-in: its role now, and what it shows."""

    role: str
    """HOST or VIEWER."""
    shown: Shown
    token: str | None
    """The session's token, given to the page that became its host in this check-in (its own token was the session's and the host had gone); None otherwise."""


def _new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


class Registry:
    """Every session, behind one lock. One instance, ``REGISTRY``, serves the app; tests make their own."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        connected: Callable[[str], bool | None] | None = None,
    ) -> None:
        """``connected(page)`` says whether a page's connection is open: True, False, or None where it cannot tell.

        Where it cannot tell, a page counts only while it checks in (HOST_GRACE_S).
        """
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._clock = clock
        self._connected = connected or (lambda _page: None)

    def set_connected(self, connected: Callable[[str], bool | None]) -> None:
        """Give the way to ask whether a page is connected: the page has it only once Streamlit runs."""
        self._connected = connected

    # -- the rules about pages -------------------------------------------

    def _live(self, page: str | None, seen: float, now: float) -> bool:
        """Whether a page counts as there: connected, or heard from within HOST_GRACE_S."""
        if page is None:
            return False
        return now - seen < HOST_GRACE_S or self._connected(page) is True

    def _host_live(self, session: Session, now: float) -> bool:
        return self._live(session.host, session.host_seen, now)

    def _host_gone(self, session: Session, now: float) -> bool:
        """Whether the host's own page has closed, for the holder of the token, who need not wait out the grace.

        So a host's page that is reloaded is the host again at once, though its
        old page checked in a moment ago. Where connections cannot be told,
        the grace applies to the token's holder too.
        """
        if session.host is None:
            return True
        connected = self._connected(session.host)
        if connected is None:
            return now - session.host_seen >= HOST_GRACE_S
        return not connected

    def _viewers_live(self, session: Session, now: float) -> int:
        return sum(1 for page, seen in session.viewers.items() if self._live(page, seen, now))

    def _present(self, page: str, seen: float, now: float) -> bool:
        """Whether a page is open now: connected; heard from within HOST_GRACE_S only where connections cannot be told.

        Stricter than ``_live``: a page closed a moment ago (a reload) is not open.
        """
        connected = self._connected(page)
        return connected if connected is not None else now - seen < HOST_GRACE_S

    def _make_host(self, session: Session, page: str, now: float, token: str | None = None, mode: str | None = None,
                   tab: str | None = None) -> None:
        """``page`` (of tab ``tab``) hosts ``session`` from now; a new token unless the page brought the session's own.

        The page's way of showing the times (``mode``, None for a page that has
        none yet) becomes the host's, and every other page takes it at its next
        check-in (``host_epoch``): Terry, 2026-11-01, "when the host connects".
        """
        session.host = page
        session.host_tab = tab
        session.host_seen = now
        session.token = token or _new_token()
        session.viewers.pop(page, None)
        if mode in MODES:
            session.host_mode = mode
        session.host_epoch += 1
        session.version += 1

    def _expire(self, now: float) -> None:
        """Remove the sessions nobody has had open for EXPIRE_AFTER_S, and the viewers' pages long gone."""
        for sid, session in list(self._sessions.items()):
            for page, seen in list(session.viewers.items()):
                if now - seen > FORGET_PAGE_S and self._connected(page) is not True:
                    del session.viewers[page]
                    session.browsers.pop(page, None)
            attended = self._host_live(session, now) or self._viewers_live(session, now) > 0
            if not attended and now - session.attended > EXPIRE_AFTER_S:
                del self._sessions[sid]

    def _shown(self, session: Session, now: float) -> Shown:
        return Shown(
            elapsed=session.timer.elapsed(now),
            running=session.timer.running,
            limit=session.timer.limit,
            version=session.version,
            hosted=self._host_live(session, now),
            viewers=self._viewers_live(session, now),
            sizes=dict(session.sizes),
            host_mode=session.host_mode,
            host_epoch=session.host_epoch,
            colours=dict(session.colours),
            theme=session.theme,
            warning=session.warning,
            warning_colour=session.warning_colour,
        )

    # -- what pages do ------------------------------------------------------

    def exists(self, sid: str) -> bool:
        with self._lock:
            return sid in self._sessions

    def create(self, sid: str | None, page: str, browser: str | None = None, mode: str | None = None,
               tab: str | None = None) -> tuple[str, str, str | None]:
        """Create New Session: ``sid``, or a random ID for None. Returns the ID, the host's token and a notice (None for none).

        Refused when another page hosts a session of that ID. A session of the
        ID with no host connected is taken over as it is, its timers running
        on, so its viewers see nothing change (Terry's rule that a session
        survives its host's leaving); Reset starts it again.
        """
        with self._lock:
            now = self._clock()
            self._expire(now)
            if sid is None:
                sid = ids.random_id(lambda candidate: candidate in self._sessions)
            session = self._sessions.get(sid)
            if session is None:
                session = Session(sid=sid, token=_new_token(), attended=now)
                self._make_host(session, page, now, session.token, mode, tab)
                self._sessions[sid] = session
                self._note_browser(session, page, browser)
                return sid, session.token, None
            if session.host == page:
                return sid, session.token, None
            if self._host_live(session, now):
                raise Refused(f"Session {sid} already has a host. Please choose another session ID.")
            self._make_host(session, page, now, mode=mode, tab=tab)
            self._note_browser(session, page, browser)
            session.attended = now
            return sid, session.token, (
                f"Session {sid} was already running, without a host: you are now its host. "
                "Its timers are as they were; Reset starts them again."
            )

    @staticmethod
    def _note_browser(session: Session, page: str, browser: str | None) -> None:
        if browser:
            session.browsers[page] = browser

    def _attend(self, session: Session, page: str, token: str | None, now: float,
                browser: str | None, mode: str | None, tab: str | None, joining: bool) -> Checked:
        """``page`` has ``session`` open: its role, the host's if it brings the token and the host's page has closed.

        A page already showing the session (``joining`` False: a check-in) takes
        the role by the token only from its own tab's earlier page, or where
        the host's tab is not known (module docstring: two tabs of one browser).
        """
        session.attended = now
        self._note_browser(session, page, browser)
        if session.host == page:
            session.host_seen = now
            return Checked(HOST, self._shown(session, now), None)
        own_tab = joining or session.host_tab is None or tab == session.host_tab
        if token is not None and token == session.token and own_tab and self._host_gone(session, now):
            self._make_host(session, page, now, token, mode, tab)
            return Checked(HOST, self._shown(session, now), token)
        session.viewers[page] = now
        return Checked(VIEWER, self._shown(session, now), None)

    def join(self, sid: str, page: str, token: str | None, browser: str | None = None,
             mode: str | None = None, tab: str | None = None) -> Checked | None:
        """Connect to Session (or a window going back to one): None if there is no session ``sid`` (the page waits); otherwise this page's role.

        A page bringing the session's token hosts it, unless another page that
        is still connected hosts it; any other page views it.
        """
        with self._lock:
            now = self._clock()
            self._expire(now)
            session = self._sessions.get(sid)
            return None if session is None else self._attend(session, page, token, now, browser, mode, tab, True)

    def check_in(self, sid: str, page: str, token: str | None, browser: str | None = None,
                 mode: str | None = None, tab: str | None = None) -> Checked | None:
        """A page showing session ``sid`` checks in (once a second): its role now, and what it shows. None if the session is gone.

        A viewer of the host's own tab (the host's page reloaded) bringing the
        session's token becomes its host here once the host's old page has
        closed, as ``join`` would have made it: so the token's holder is never
        left waiting behind its own old page. A viewer of another tab is not.
        """
        with self._lock:
            now = self._clock()
            session = self._sessions.get(sid)
            return None if session is None else self._attend(session, page, token, now, browser, mode, tab, False)

    def candidates(self, sids: list[str], page: str, browser: str | None,
                   tokens: dict[str, str]) -> list[tuple[str, bool]]:
        """The sessions of ``sids`` (a browser's recent ones) that a new page of that browser could go back to, in their order.

        Each running session not open in another page of the same browser,
        with whether the page would host it (it brings the session's token, and
        the host's page has closed). ``web`` reconnects a new window by itself
        only where this leaves no doubt.
        """
        with self._lock:
            now = self._clock()
            self._expire(now)
            found = []
            for sid in sids:
                session = self._sessions.get(sid)
                if session is None:
                    continue
                pages = dict(session.viewers)
                if session.host is not None:
                    pages[session.host] = session.host_seen
                open_here = browser is not None and any(
                    other != page and session.browsers.get(other) == browser and self._present(other, seen, now)
                    for other, seen in pages.items()
                )
                if not open_here:
                    token = tokens.get(sid)
                    found.append((sid, token is not None and token == session.token and self._host_gone(session, now)))
            return found

    def assume_host(self, sid: str, page: str, mode: str | None = None, tab: str | None = None) -> str:
        """Yes to "Assume Host Role?": this page hosts ``sid`` from now, with a new token, which is returned.

        Refused if the session has gone, or if a host has connected meanwhile
        (two viewers who both say Yes: the first is the host).
        """
        with self._lock:
            now = self._clock()
            session = self._sessions.get(sid)
            if session is None:
                raise Refused(f"Session {sid} has ended.")
            if session.host == page:
                return session.token
            if self._host_live(session, now):
                raise Refused(f"Someone else is now the host of session {sid}.")
            self._make_host(session, page, now, mode=mode, tab=tab)
            session.attended = now
            return session.token

    def leave(self, sid: str, page: str) -> None:
        """Leave Session: the page is no longer the host or a viewer. A host's session runs on without one."""
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return
            session.viewers.pop(page, None)
            session.browsers.pop(page, None)
            if session.host == page:
                session.host = None
                session.version += 1

    def control(self, sid: str, page: str, action: str, value: float | None = None) -> None:
        """The host's buttons, Time Limit and Time Left Warning: "start", "stop", "reset", "limit" or "warning" (the last two with ``value``).

        Refused for a page that does not host ``sid``. The warning is read as
        the limit is, minutes to a tenth (``timer.limit_minutes``).
        """
        with self._lock:
            now = self._clock()
            session = self._host_session(sid, page)
            timer = session.timer
            if action == "start":
                changed = timer.start(now)
            elif action == "stop":
                changed = timer.stop(now)
            elif action == "reset":
                changed = timer.reset(now)
            elif action == "limit":
                changed = timer.set_limit(value)
            elif action == "warning":
                warning = limit_minutes(value)
                changed = warning != session.warning
                session.warning = warning
            else:
                raise ValueError(f"no such action: {action!r}")
            session.host_seen = now
            session.attended = now
            if changed:
                session.version += 1

    def set_size(self, sid: str, page: str, timer: str, percent: object) -> None:
        """The host's slider beside a timer: its size for every page, in percent. Refused for a page that does not host ``sid``.

        What the browser sends is checked here: a timer of SIZED, and a number
        taken to the slider's step within SIZE_MIN to SIZE_MAX.
        """
        if timer not in SIZED:
            raise ValueError(f"no such timer: {timer!r}")
        try:
            number = float(percent)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError(f"not a size: {percent!r}") from None
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError(f"not a size: {percent!r}")
        size = min(SIZE_MAX, max(SIZE_MIN, round(number / SIZE_STEP) * SIZE_STEP))
        with self._lock:
            session = self._host_session(sid, page)
            if session.sizes[timer] != size:
                session.sizes[timer] = size
                session.version += 1

    def set_colour(self, sid: str, page: str, timer: str, colour: object) -> None:
        """A colour button beside the host's slider: ``timer``'s figures' colour for every page, by Terry's rule for black and white (``colours.choose``).

        Refused for a page that does not host ``sid``; ValueError for a timer
        or colour not known. A warning colour that the change rules out (now
        a timer's colour, or the default) goes back to AUTO.
        """
        if timer not in SIZED or not isinstance(colour, str):
            raise ValueError(f"not a timer's colour: {timer!r}, {colour!r}")
        with self._lock:
            session = self._host_session(sid, page)
            after, theme = choose(session.colours, session.theme, timer, colour)
            if (after, theme) == (session.colours, session.theme):
                return
            session.colours, session.theme = after, theme
            if session.warning_colour not in {name for name, _ in warning_choices(after)}:
                session.warning_colour = AUTO
            session.version += 1

    def set_warning_colour(self, sid: str, page: str, choice: object) -> None:
        """A colour button beside the Time Left Warning: AUTO (Terry's rule) or one of the four others shown (``colours.warning_choices``).

        Refused for a page that does not host ``sid``; ValueError for a choice
        that is not one of them now.
        """
        with self._lock:
            session = self._host_session(sid, page)
            shown = {AUTO} | {name for name, _ in warning_choices(session.colours)}
            if choice not in shown or choice not in WARNING_CANDIDATES and choice != AUTO:
                raise ValueError(f"not a warning colour now: {choice!r}")
            if session.warning_colour != choice:
                session.warning_colour = choice
                session.version += 1

    def set_host_mode(self, sid: str, page: str, mode: str) -> None:
        """The host's page has changed its way of showing the times: the default for pages that open from now on.

        Pages already open keep their own until a host next connects. Nothing
        is done for a page that does not host ``sid`` (it is only a viewer's
        own choice) or for a mode not in MODES.
        """
        with self._lock:
            session = self._sessions.get(sid)
            if session is not None and session.host == page and mode in MODES:
                session.host_mode = mode

    def _host_session(self, sid: str, page: str) -> Session:
        session = self._sessions.get(sid)
        if session is None:
            raise Refused(f"Session {sid} has ended.")
        if session.host != page:
            raise Refused(f"You are no longer the host of session {sid}.")
        return session

    def shown(self, sid: str) -> Shown | None:
        """What session ``sid`` shows now; None if it has gone."""
        with self._lock:
            session = self._sessions.get(sid)
            return None if session is None else self._shown(session, self._clock())


REGISTRY = Registry()
"""The app's sessions. ``web`` gives it the way to ask whether a page is connected (``Registry.set_connected``)."""
