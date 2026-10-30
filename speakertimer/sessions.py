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
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import ids
from .timer import Timer, limit_seconds

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
    host_seen: float = 0.0
    """When the host's page last checked in (the monotonic clock)."""
    viewers: dict[str, float] = field(default_factory=dict)
    """Each viewer's page id and when it last checked in."""
    attended: float = 0.0
    """When any page last had the session open: it is removed EXPIRE_AFTER_S after."""
    version: int = 0
    """Raised by every change a page shows: the timer, the Time Limit, the host."""


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

    def for_script(self) -> dict[str, object]:
        """What the page's script needs to run the clock (``web._HUB_JS``): the limit in whole seconds, None for none."""
        return {
            "elapsed": self.elapsed,
            "running": self.running,
            "limit": limit_seconds(self.limit),
            "version": self.version,
        }


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

    def _make_host(self, session: Session, page: str, now: float, token: str | None = None) -> None:
        """``page`` hosts ``session`` from now; a new token unless the page brought the session's own."""
        session.host = page
        session.host_seen = now
        session.token = token or _new_token()
        session.viewers.pop(page, None)
        session.version += 1

    def _expire(self, now: float) -> None:
        """Remove the sessions nobody has had open for EXPIRE_AFTER_S, and the viewers' pages long gone."""
        for sid, session in list(self._sessions.items()):
            for page, seen in list(session.viewers.items()):
                if now - seen > FORGET_PAGE_S and self._connected(page) is not True:
                    del session.viewers[page]
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
        )

    # -- what pages do ------------------------------------------------------

    def exists(self, sid: str) -> bool:
        with self._lock:
            return sid in self._sessions

    def create(self, sid: str | None, page: str) -> tuple[str, str, str | None]:
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
                self._make_host(session, page, now, session.token)
                self._sessions[sid] = session
                return sid, session.token, None
            if session.host == page:
                return sid, session.token, None
            if self._host_live(session, now):
                raise Refused(f"Session {sid} already has a host. Please choose another session ID.")
            self._make_host(session, page, now)
            session.attended = now
            return sid, session.token, (
                f"Session {sid} was already running, without a host: you are now its host. "
                "Its timers are as they were; Reset starts them again."
            )

    def _attend(self, session: Session, page: str, token: str | None, now: float) -> Checked:
        """``page`` has ``session`` open: its role, the host's if it brings the token and the host's page has closed."""
        session.attended = now
        if session.host == page:
            session.host_seen = now
            return Checked(HOST, self._shown(session, now), None)
        if token is not None and token == session.token and self._host_gone(session, now):
            self._make_host(session, page, now, token)
            return Checked(HOST, self._shown(session, now), token)
        session.viewers[page] = now
        return Checked(VIEWER, self._shown(session, now), None)

    def join(self, sid: str, page: str, token: str | None) -> Checked | None:
        """Connect to Session: None if there is no session ``sid`` (the page waits); otherwise this page's role.

        A page bringing the session's token hosts it, unless another page that
        is still connected hosts it; any other page views it.
        """
        with self._lock:
            now = self._clock()
            self._expire(now)
            session = self._sessions.get(sid)
            return None if session is None else self._attend(session, page, token, now)

    def check_in(self, sid: str, page: str, token: str | None) -> Checked | None:
        """A page showing session ``sid`` checks in (once a second): its role now, and what it shows. None if the session is gone.

        A viewer bringing the session's token becomes its host here once the
        host's page has closed, as ``join`` would have made it: so the token's
        holder is never left waiting behind its own old page.
        """
        with self._lock:
            now = self._clock()
            session = self._sessions.get(sid)
            return None if session is None else self._attend(session, page, token, now)

    def assume_host(self, sid: str, page: str) -> str:
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
            self._make_host(session, page, now)
            session.attended = now
            return session.token

    def leave(self, sid: str, page: str) -> None:
        """Leave Session: the page is no longer the host or a viewer. A host's session runs on without one."""
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return
            session.viewers.pop(page, None)
            if session.host == page:
                session.host = None
                session.version += 1

    def control(self, sid: str, page: str, action: str, value: float | None = None) -> None:
        """The host's buttons and Time Limit: "start", "stop", "reset" or "limit" (with ``value``). Refused for a page that does not host ``sid``."""
        with self._lock:
            now = self._clock()
            session = self._sessions.get(sid)
            if session is None:
                raise Refused(f"Session {sid} has ended.")
            if session.host != page:
                raise Refused(f"You are no longer the host of session {sid}.")
            timer = session.timer
            if action == "start":
                changed = timer.start(now)
            elif action == "stop":
                changed = timer.stop(now)
            elif action == "reset":
                changed = timer.reset(now)
            elif action == "limit":
                changed = timer.set_limit(value)
            else:
                raise ValueError(f"no such action: {action!r}")
            session.host_seen = now
            session.attended = now
            if changed:
                session.version += 1

    def shown(self, sid: str) -> Shown | None:
        """What session ``sid`` shows now; None if it has gone."""
        with self._lock:
            session = self._sessions.get(sid)
            return None if session is None else self._shown(session, self._clock())


REGISTRY = Registry()
"""The app's sessions. ``web`` gives it the way to ask whether a page is connected (``Registry.set_connected``)."""
