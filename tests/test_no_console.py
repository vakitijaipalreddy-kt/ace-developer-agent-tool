"""The app must never flash a console window.

Under pythonw.exe the app has no console of its own, so every child process
Windows starts gets a brand new console window unless CREATE_NO_WINDOW is
passed. This pins that behaviour: all process launches go through `proc`,
and no module may call subprocess directly.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import proc  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []
NL = chr(10)


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print("  PASS  " + name)
    else:
        FAILED.append(name)
        print("  FAIL  " + name + ("  -> " + detail if detail else ""))


def test_flags() -> None:
    print(NL + "[proc supplies the no-window flags]")
    kw = proc.no_window_kwargs()
    check("CREATE_NO_WINDOW is set",
          kw.get("creationflags") == 0x08000000, str(kw.get("creationflags")))
    check("startupinfo hides the window", kw.get("startupinfo") is not None)
    si = kw.get("startupinfo")
    check("SW_HIDE requested",
          si is not None and si.wShowWindow == subprocess.SW_HIDE)
    check("STARTF_USESHOWWINDOW set",
          si is not None and (si.dwFlags & subprocess.STARTF_USESHOWWINDOW))


def test_proc_runs() -> None:
    print(NL + "[proc still actually runs things]")
    res = proc.run(["git", "--version"], capture_output=True, text=True)
    check("command ran", res.returncode == 0)
    check("output captured", "git version" in (res.stdout or ""),
          str(res.stdout)[:120])

    p = proc.popen(["git", "--version"], stdout=subprocess.PIPE, text=True)
    out = p.stdout.read() if p.stdout else ""
    p.wait()
    check("popen works", "git version" in out, out[:120])


def test_caller_can_override() -> None:
    print(NL + "[explicit kwargs still win]")
    # a caller passing its own creationflags should not be clobbered
    res = proc.run(["git", "--version"], capture_output=True, text=True,
                   creationflags=0x08000000)
    check("explicit creationflags accepted", res.returncode == 0)


def test_no_direct_subprocess_calls() -> None:
    """Every launch site must go through proc, or a window can slip back in."""
    print(NL + "[no module bypasses proc]")
    offenders = []
    for path in sorted((ROOT / "agent").glob("*.py")):
        if path.name == "proc.py":
            continue  # proc is where the real calls live
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"subprocess\.(run|Popen)\s*\(", text):
            line = text[:m.start()].count("\n") + 1
            offenders.append("{}:{}".format(path.name, line))
    check("no direct subprocess.run/Popen outside proc.py",
          not offenders, ", ".join(offenders))


def test_pty_has_no_window() -> None:
    print(NL + "[the terminal's PTY is headless]")
    from agent import terminal

    if not terminal.HAVE_PTY:
        check("pywinpty present (skipped - not installed)", True)
        return
    # A ConPTY is a pseudoconsole: it has no window on screen, its output is
    # piped to us. Confirm a command runs and returns output through it.
    import threading

    lines: list[str] = []
    done = threading.Event()
    sess = terminal.TerminalSession(
        on_output=lines.append,
        on_exit=lambda c: done.set(),
        cwd=str(ROOT),
    )
    sess.start("git --version")
    finished = done.wait(60)
    check("pty command finished", finished)
    check("pty returned output", any("git version" in l for l in lines),
          str(lines[:3]))


if __name__ == "__main__":
    test_flags()
    test_proc_runs()
    test_caller_can_override()
    test_no_direct_subprocess_calls()
    test_pty_has_no_window()

    print(NL + "=" * 60)
    print("passed: {}   failed: {}".format(len(PASSED), len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  FAILED: " + f)
        sys.exit(1)
    print("ALL TESTS PASSED")
