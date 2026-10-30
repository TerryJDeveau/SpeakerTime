"""Session IDs: what may be typed, how it is read, and random ones.

The rules (Terry, 2026-10-30): letters, numbers and certain special
characters; spaces and tabs ignored; case ignored; special characters that
would cause problems ignored; at most 20 characters.

So an ID is read as follows: accents are taken off letters (é is E), every
letter is put in upper case, and whatever is not an ASCII letter, a digit or
one of ``ALLOWED_SPECIALS`` is left out: spaces, tabs, quotes, backslashes,
angle brackets, ``$``, ``%``, ``;``, ``,`` and the rest. The ones left out are
those that mean something to a web page, a cookie or the page's text
(Markdown takes two ``$`` for a formula), or that look alike when read aloud
or typed on a phone. "Room #5", "room#5" and "ROOM #5" are all one session.
"""

from __future__ import annotations

import secrets
import unicodedata
from collections.abc import Callable

MAX_LENGTH = 20
"""The longest ID, counted after it is read (spaces and the characters left out do not count)."""

ALLOWED_SPECIALS = "-_.#@+&"
"""The special characters kept in an ID. Each is safe in every place an ID goes once encoded, and easy to say."""

MAX_TYPED = 60
"""The most the box takes, spaces and all: room for a spaced-out ID of MAX_LENGTH, and a stop to a paste of a whole page."""

RANDOM_DIGITS = 6
"""A random ID's length: six digits, easy to read out to a room and to type on a phone, and a million of them."""

_KEPT = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" + ALLOWED_SPECIALS)


def normalized(typed: str) -> str:
    """The ID ``typed`` names: accents off, upper case, only the characters kept (none of them spaces)."""
    # NFKD splits an accented letter into the letter and its accent (a
    # combining mark), and a full-width letter into the ordinary one; upper()
    # then makes "ß" "SS" before the filter, which keeps ASCII only.
    plain = "".join(c for c in unicodedata.normalize("NFKD", typed) if not unicodedata.combining(c))
    return "".join(c for c in plain.upper() if c in _KEPT)


def problem(sid: str) -> str | None:
    """Why ``sid`` (already ``normalized``) cannot be a session's ID; None if it can. A blank one is for the caller to judge."""
    if len(sid) > MAX_LENGTH:
        return (
            f"A session ID can have at most {MAX_LENGTH} characters (spaces, tabs and the characters left out do not count); "
            f"{sid} has {len(sid)}."
        )
    return None


def random_id(taken: Callable[[str], bool]) -> str:
    """A random ID of RANDOM_DIGITS digits, the first not 0 (so it reads as a number), that ``taken`` says no session has."""
    while True:
        sid = str(secrets.randbelow(9 * 10 ** (RANDOM_DIGITS - 1)) + 10 ** (RANDOM_DIGITS - 1))
        if not taken(sid):
            return sid
