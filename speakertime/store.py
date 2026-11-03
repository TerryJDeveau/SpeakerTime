"""The remembered settings kept off the app's machine as well, in a secret GitHub gist (v1.9).

On Streamlit Community Cloud a Reboot starts the app on a new machine from
a fresh copy of the repository (measured 2026-11-03), so the file the
settings are kept in (``sessions.REMEMBER_FILE``) goes with the old one.
Terry, the same day: a host should be able to set up a session under an ID,
leave, give someone the ID, and have it start from those settings for them,
"even if Streamlit reboots in between". So the table of settings is kept in
a gist of the account whose token the app is given (``TOKEN_NAME``, in the
app's secrets) as well: read when the app starts, written in the background
a few seconds after each change.

The gist is secret (not listed anywhere, but readable by anyone given its
address) and is found by its description, so the token is the one thing to
set: the app makes the gist the first time it writes. A token needs only
the "gist" scope. Without one, nothing here is used and the settings live
as long as the machine does (the file only).

Nothing here imports Streamlit. The checks point ``api`` at a stand-in
server of their own (``tools/verify_logic.py``).
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request

TOKEN_NAME = "SPEAKERTIME_GIST_TOKEN"
"""The secret (or environment variable) holding the token: a GitHub token with the "gist" scope."""

GITHUB_API = "https://api.github.com"

GIST_DESCRIPTION = "SpeakerTime: the sessions' remembered settings (written by the app at speakertime.streamlit.app)"
"""How the app knows its gist among the account's others."""

GIST_FILE = "speakertime_remembered_sessions.json"

TIMEOUT_S = 10.0
"""The longest one request to GitHub may take."""

WRITE_AFTER_S = 3.0
"""A write waits this long after the latest change, so that a host setting several things makes one write, not many."""

RETRY_AFTER_S = (30.0, 120.0, 600.0)
"""The pauses after a failed write, one after another; then every 600 s until a write succeeds."""

_PAGES = 10
"""The most pages of an account's gists looked through for this app's (100 a page)."""


class StoreError(Exception):
    """The gist could not be read or written; the message says why."""


class GistStore:
    """One table of settings (its text) in one gist: ``load`` once, ``save`` at each change."""

    def __init__(self, token: str, api: str = GITHUB_API, write_after: float = WRITE_AFTER_S,
                 retry_after: tuple[float, ...] = RETRY_AFTER_S) -> None:
        self._token = token
        self._api = api.rstrip("/")
        self._write_after = write_after
        self._retry_after = retry_after
        self._lock = threading.Lock()
        self._gist_id: str | None = None
        self._pending: str | None = None
        """The latest table not yet written."""
        self._changed = 0.0
        """When it was given (the monotonic clock)."""
        self._worker: threading.Thread | None = None
        self.writes = 0
        """Writes that succeeded (for the checks)."""

    def _request(self, method: str, path: str, body: object = None) -> object:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "SpeakerTime"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self._api + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                return json.loads(response.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as error:
            raise StoreError(f"GitHub answered {error.code} to {method} {path}") from None
        except (OSError, ValueError) as error:  # no connection or a timeout (OSError), or an answer that is not JSON
            raise StoreError(f"{method} {path} failed: {error}") from None

    def _find(self) -> str | None:
        """The gist's id, looked for by its description among the account's gists; None once all are seen and it is not there.

        Raises StoreError where the list could not be read, so a gist is
        never made a second time because the first could not be seen.
        """
        if self._gist_id is not None:
            return self._gist_id
        for page in range(1, _PAGES + 1):
            gists = self._request("GET", f"/gists?per_page=100&page={page}")
            if not isinstance(gists, list):
                raise StoreError("the list of gists was not a list")
            for gist in gists:
                if isinstance(gist, dict) and gist.get("description") == GIST_DESCRIPTION and isinstance(gist.get("id"), str):
                    self._gist_id = gist["id"]
                    return self._gist_id
            if len(gists) < 100:
                return None
        raise StoreError(f"more than {_PAGES * 100} gists: this app's was not among the first")

    def load(self) -> str | None:
        """The table as last written (its text), or None where the app has written none yet. Raises StoreError."""
        gist_id = self._find()
        if gist_id is None:
            return None
        gist = self._request("GET", f"/gists/{gist_id}")
        file = (gist.get("files") or {}).get(GIST_FILE) if isinstance(gist, dict) else None
        if not isinstance(file, dict):
            return None
        if file.get("truncated"):
            raise StoreError("the gist's file is too large for GitHub to give whole")
        content = file.get("content")
        return content if isinstance(content, str) else None

    def save(self, text: str) -> None:
        """Write ``text`` as the table, in the background, WRITE_AFTER_S after the latest call; a later call replaces it."""
        with self._lock:
            self._pending = text
            self._changed = time.monotonic()
            if self._worker is None:
                self._worker = threading.Thread(target=self._write_all, name="speakertime-gist", daemon=True)
                self._worker.start()

    def flush(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for every table given to be written; whether it was (for the checks)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if self._worker is None:
                    return True
            time.sleep(0.05)
        return False

    def _write_all(self) -> None:
        """The writer: the latest table once it has waited WRITE_AFTER_S, again after a pause if that fails; ends when none is left."""
        failures = 0
        while True:
            with self._lock:
                if self._pending is None:
                    self._worker = None
                    return
                wait = self._changed + self._write_after - time.monotonic()
                if wait <= 0:
                    text, self._pending = self._pending, None
            if wait > 0:
                time.sleep(wait)
                continue
            try:
                self._write(text)
            except StoreError as problem:
                print(f"Warning: the remembered settings could not be written to the gist ({problem}); trying again.",
                      file=sys.stderr)
                with self._lock:
                    if self._pending is None:  # nothing newer meanwhile: this one is still the latest
                        self._pending = text
                time.sleep(self._retry_after[min(failures, len(self._retry_after) - 1)])
                failures += 1
                continue
            failures = 0
            with self._lock:
                self.writes += 1

    def _write(self, text: str) -> None:
        files = {GIST_FILE: {"content": text}}
        gist_id = self._find()
        if gist_id is not None:
            self._request("PATCH", f"/gists/{gist_id}", {"files": files})
            return
        made = self._request("POST", "/gists", {"description": GIST_DESCRIPTION, "public": False, "files": files})
        if not (isinstance(made, dict) and isinstance(made.get("id"), str)):
            raise StoreError("GitHub made the gist but gave no id")
        self._gist_id = made["id"]
