"""Helpers copied from ratesplot, meant to come from the shared library planned for ratesplot and NG_analysis.

Terry (2026-10-30): reuse ratesplot's code where it fits; a library shared by
his programs is planned, but deferred until ratesplot has been fleshed out.
Until it exists, these are copies, each naming where it came from (ratesplot
at commit d2f9e6e, 2026-10-30), so the move to the library is a swap of the
import. Change them only together with the original, or not at all: a fix
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


# A page's cookies, read back through the page's own script (ratesplot.web
# _READER_JS, batch 24.6), generalized to several cookies. Not through
# st.context.cookies: on Streamlit Community Cloud the proxy filters almost
# every cookie from the app's connection, and Streamlit's staff say
# st.context.cookies cannot be used there (ratesplot 6.10 wrote its cookie
# live and never saw it, 2026-10-27). The script sends the raw values once,
# by name ("" for a cookie not set, or where the browser refuses cookies to
# scripts); the page keeps them from the element's callback and starts from
# them on the run that follows. One round trip; nothing else is laid out
# before the answer. It runs in the page (a components v2 element is not a
# frame), so it reads the app's own cookies.
COOKIE_READER_JS = """
export default function (component) {
  const { data, setTriggerValue } = component;
  const values = {};
  for (const name of data.names) {
    let value = "";
    try {
      const kept = document.cookie.split("; ").find((pair) => pair.startsWith(name + "="));
      if (kept !== undefined) value = kept.slice(name.length + 1);
    } catch (error) {
      value = "";
    }
    values[name] = value;
  }
  setTriggerValue("cookies", values);
}
"""
