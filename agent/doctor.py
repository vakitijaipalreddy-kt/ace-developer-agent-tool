"""Check that the external tools this app drives are actually present.

The app is shipped as a single executable with Python and its libraries
bundled, but it drives three CLIs that cannot be bundled because they are
separately installed and separately authenticated:

    git     - does the branching, committing and pushing
    gh      - opens the pull request
    claude  - writes the code

Rather than failing deep inside a run, we check at startup and say exactly
what is missing and how to install it.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from . import proc


@dataclass
class ToolStatus:
    name: str
    found: bool
    version: str = ""
    path: str = ""
    required: bool = True
    install_hint: str = ""

    @property
    def line(self) -> str:
        mark = "OK  " if self.found else ("MISSING" if self.required else "absent")
        detail = self.version or self.path or ""
        return "{:<8} {:<8} {}".format(self.name, mark, detail)


TOOLS = [
    ("git", ["git", "--version"], True,
     "Install Git for Windows from https://git-scm.com/download/win"),
    ("gh", ["gh", "--version"], True,
     "Install the GitHub CLI from https://cli.github.com "
     "(or `winget install GitHub.cli`)"),
    ("claude", ["claude", "--version"], True,
     "Install Claude Code from https://claude.com/claude-code"),
]


def check_tool(name: str, args: list[str], required: bool,
               hint: str) -> ToolStatus:
    path = shutil.which(name) or ""
    if not path:
        return ToolStatus(name, False, required=required, install_hint=hint)
    try:
        res = proc.run(args, capture_output=True, text=True, timeout=30)
        version = (res.stdout or res.stderr or "").strip().splitlines()
        return ToolStatus(name, True, version[0] if version else "", path,
                          required, hint)
    except Exception:
        # it exists on PATH but would not run
        return ToolStatus(name, False, "", path, required, hint)


def check_all() -> list[ToolStatus]:
    return [check_tool(n, a, r, h) for n, a, r, h in TOOLS]


def missing_required(statuses=None) -> list[ToolStatus]:
    statuses = statuses if statuses is not None else check_all()
    return [s for s in statuses if s.required and not s.found]


def report(statuses=None) -> str:
    statuses = statuses if statuses is not None else check_all()
    lines = ["Required command-line tools:", ""]
    lines += ["  " + s.line for s in statuses]
    missing = [s for s in statuses if s.required and not s.found]
    if missing:
        lines += ["", "Missing:"]
        for s in missing:
            lines += ["  " + s.name + " - " + s.install_hint]
    return "\n".join(lines)
