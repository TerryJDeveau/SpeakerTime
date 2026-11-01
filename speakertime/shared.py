"""Helpers copied from ratesplot, meant to come from the shared library planned for ratesplot and NG_analysis.

Terry (2026-10-30): reuse ratesplot's code where it fits; a library shared by
his programs is planned, but deferred until ratesplot has been fleshed out.
Until it exists, these are copies, each naming where it came from (ratesplot
at commit d2f9e6e, 2026-10-30), so the move to the library is a swap of the
import. (The cookie reader copied here at first became the page's own on
2026-11-01, ``web._READER_JS``, when it took on the tab's note and the
browser's id; it keeps ratesplot's method.) Change them only together with the original, or not at all: a fix
made here alone would be lost at that swap.
"""

from __future__ import annotations

from pathlib import Path

from . import __version__

# The program's own folder: the repository's working folder when it is run
# from a clone (ratesplot.frontend.PROGRAM_FOLDER).
PROGRAM_FOLDER = Path(__file__).resolve().parents[1]


def started_commit(folder: Path) -> str | None:
    """The commit ``folder``'s git checkout is at, abbreviated (``.git/HEAD``, a branch's ref or packed-refs); None without one.

    Read once, when the program starts: the web page's server pulls new
    commits without loading their code again, so the commit it started from
    is the one its code is. (ratesplot.frontend._started_commit)
    """
    git = folder / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="ascii").strip()
        if not head.startswith("ref: "):
            return head[:7] or None
        ref = head.removeprefix("ref: ")
        if (git / ref).is_file():
            return (git / ref).read_text(encoding="ascii").strip()[:7] or None
        for line in (git / "packed-refs").read_text(encoding="ascii").splitlines():
            if line.endswith(" " + ref):
                return line[:7]
    except (OSError, UnicodeDecodeError):
        pass
    return None


_STARTED_COMMIT = started_commit(PROGRAM_FOLDER)


def version_text() -> str:
    """The version as the page shows it: "v1.0, build: 8c3e41b" (Terry's form, 2026-10-28), the commit left off where there is no checkout.

    (ratesplot.frontend.version_text)
    """
    return f"v{__version__}, build: {_STARTED_COMMIT}" if _STARTED_COMMIT else f"v{__version__}"
