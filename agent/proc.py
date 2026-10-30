"""Subprocess helpers that never flash a console window.

The app runs under `pythonw.exe`, which has no console of its own. Every time
it starts a child process Windows therefore creates a brand new console window
for that child - which is the black box that blinks on screen. Passing
CREATE_NO_WINDOW (and a hidden STARTUPINFO) suppresses it.

Everything in this project that starts a process goes through here so the
flag can never be forgotten in one place.
"""

from __future__ import annotations

import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"

# 0x08000000 - the child gets no console at all.
CREATE_NO_WINDOW = 0x08000000


def hidden_startupinfo():
    """A STARTUPINFO that hides the window for processes that still make one."""
    if not IS_WINDOWS:
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return si


def no_window_kwargs() -> dict:
    """Keyword arguments to add to any subprocess call on Windows."""
    if not IS_WINDOWS:
        return {}
    return {
        "creationflags": CREATE_NO_WINDOW,
        "startupinfo": hidden_startupinfo(),
    }


def run(args, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run with the console suppressed."""
    kwargs.setdefault("shell", False)
    for key, value in no_window_kwargs().items():
        kwargs.setdefault(key, value)
    return subprocess.run(args, **kwargs)


def popen(args, **kwargs) -> subprocess.Popen:
    """subprocess.Popen with the console suppressed."""
    kwargs.setdefault("shell", False)
    for key, value in no_window_kwargs().items():
        kwargs.setdefault(key, value)
    return subprocess.Popen(args, **kwargs)
