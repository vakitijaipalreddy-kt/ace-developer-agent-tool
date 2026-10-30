"""An embedded terminal panel.

Interactive commands like `gh auth login` need a real console: they probe for
a TTY, print a device code, and wait for Enter. A plain subprocess pipe is not
a TTY, so those programs either refuse to run or hang. This uses a ConPTY via
pywinpty when it is available, and falls back to hidden pipes (fine for
non-interactive commands) when it is not.

A ConPTY does not create a visible window - the pseudoconsole is headless, and
its output is piped back to us - so nothing flashes on screen.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading

from . import proc

try:
    import winpty

    HAVE_PTY = True
except Exception:
    winpty = None
    HAVE_PTY = False

# CSI / OSC escape sequences, plus the odd single-char controls a PTY emits.
_ANSI = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"   # OSC ... BEL / ST
    r"|\x1b[@-Z\\-_]"                       # single-char escapes
    r"|\x1b\[[0-?]*[ -/]*[@-~]"             # CSI
    r"|\x1b[()][B0]"                        # charset selection
)


def strip_ansi(text: str) -> str:
    """Remove escape sequences so the output reads cleanly in a Text widget."""
    text = _ANSI.sub("", text)
    # a PTY ends lines with \r\n; normalise and drop lone \r cursor returns
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\r+", "", text)
    return text.replace("\x07", "").replace("\x00", "")


class TerminalSession:
    """One running command. Output is pushed to `on_output` line by line."""

    def __init__(self, on_output, on_exit, cwd: str, env: dict | None = None):
        self.on_output = on_output
        self.on_exit = on_exit
        self.cwd = cwd
        self.env = env
        self.proc = None
        self._pty = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self.command = ""

    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        if self._pty is not None:
            try:
                return bool(self._pty.isalive())
            except Exception:
                return False
        return self.proc is not None and self.proc.poll() is None

    def _build_env(self) -> dict:
        env = dict(os.environ)
        if self.env:
            env.update(self.env)
        # keep CLIs from drawing boxes and spinners we would have to strip
        env.setdefault("TERM", "dumb")
        env.setdefault("NO_COLOR", "1")
        env.setdefault("CLICOLOR", "0")
        return env

    def start(self, command: str) -> None:
        if self.running:
            raise RuntimeError("A command is already running.")
        self.command = command
        self._stop.clear()
        if HAVE_PTY:
            self._start_pty(command)
        else:
            self._start_pipe(command)

    def _start_pty(self, command: str) -> None:
        self._pty = winpty.PtyProcess.spawn(
            "cmd.exe /c " + command,
            cwd=self.cwd,
            env=self._build_env(),
            dimensions=(30, 110),
        )
        self._reader = threading.Thread(target=self._pump_pty, daemon=True)
        self._reader.start()

    def _pump_pty(self) -> None:
        buf = ""
        try:
            while not self._stop.is_set():
                try:
                    data = self._pty.read()
                except EOFError:
                    break
                except Exception:
                    break
                if not data:
                    if not self._pty.isalive():
                        break
                    continue
                buf += strip_ansi(data)
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    self.on_output(line)
                if buf and not self._pty.isalive():
                    break
        finally:
            if buf.strip():
                self.on_output(buf)
            code = None
            try:
                code = self._pty.exitstatus
            except Exception:
                pass
            self._pty = None
            self.on_exit(code)

    def _start_pipe(self, command: str) -> None:
        # cmd.exe /c keeps shell builtins working without shell=True, and
        # proc.popen hides the console window.
        self.proc = proc.popen(
            ["cmd.exe", "/c", command],
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=self._build_env(),
        )
        self._reader = threading.Thread(target=self._pump_pipe, daemon=True)
        self._reader.start()

    def _pump_pipe(self) -> None:
        try:
            assert self.proc is not None and self.proc.stdout is not None
            for line in self.proc.stdout:
                if self._stop.is_set():
                    break
                self.on_output(strip_ansi(line.rstrip("\n")))
        finally:
            code = self.proc.wait() if self.proc else None
            self.proc = None
            self.on_exit(code)

    # ------------------------------------------------------------------

    def send(self, text: str) -> None:
        """Send a line to the running command's stdin."""
        if self._pty is not None:
            try:
                self._pty.write(text + "\r\n")
            except Exception:
                pass
        elif self.proc is not None and self.proc.stdin:
            try:
                self.proc.stdin.write(text + "\n")
                self.proc.stdin.flush()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._pty is not None:
            try:
                self._pty.terminate(force=True)
            except Exception:
                pass
            self._pty = None
        if self.proc is not None:
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None
