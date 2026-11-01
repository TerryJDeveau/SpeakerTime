"""SpeakerTime: a talk's Elapsed Time and Time Remaining, shared between a host and any number of viewers.

One person (the host, usually the session's chair) creates a session and runs
its timer; anyone who knows the session's ID connects and sees the same two
timers, live: the speaker, on the machine showing the slides, typically.

Package layout
--------------
timer     the timer itself: Elapsed Time, Time Remaining and the Time Limit,
          with no Streamlit in it
ids       session IDs: what may be typed, how it is read, random ones
sessions  the sessions every page shares (one process serves every visitor),
          and the rules for who hosts them
shared    copies of ratesplot's helpers (the version shown, the cookie's
          reader); they are to come from the shared library planned for
          ratesplot and NG_analysis, once that exists
web       the Streamlit page; ``streamlit_app.py`` runs it

The dependency direction is one-way: ``web`` imports the others; ``sessions``
imports ``timer`` and ``ids``; ``timer``, ``ids`` and ``shared`` import none of
this package.
"""

# The version, major.minor (ratesplot's rule, 2026-10-27, taken over here):
# the minor is raised by one in a commit of its own before each push of a
# build; the major after a significant new capability.
__version__ = "1.1"
