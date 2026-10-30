"""What Claude is allowed to run while it verifies its own work.

The agent gets read-only inspection plus test runners so it can check the
change it just made and fix what it finds. It must not be able to publish
anything, reach the network, or destroy work that is not its own - those
steps stay behind the human approval gate in the pipeline.

The allowlist is passed to `claude --allowedTools`; the denylist to
`--disallowedTools`. Claude Code enforces them. `is_blocked` mirrors the
denylist so the rules can be unit tested here.
"""

from __future__ import annotations

import re

ALLOWED_TOOLS = [
    # --- inspection -------------------------------------------------
    "Read",
    "Glob",
    "Grep",
    "NotebookRead",
    # --- editing (the whole point of the run) ------------------------
    "Edit",
    "Write",
    "NotebookEdit",
    # --- read-only git ----------------------------------------------
    "Bash(git status:*)",
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(git show:*)",
    "Bash(git ls-files:*)",
    "Bash(git blame:*)",
    "Bash(git branch:*)",
    "Bash(git rev-parse:*)",
    # --- read-only shell --------------------------------------------
    "Bash(cat:*)",
    "Bash(head:*)",
    "Bash(tail:*)",
    "Bash(ls:*)",
    "Bash(find:*)",
    "Bash(grep:*)",
    "Bash(rg:*)",
    "Bash(wc:*)",
    "Bash(sort:*)",
    "Bash(uniq:*)",
    "Bash(diff:*)",
    "Bash(file:*)",
    "Bash(which:*)",
    "Bash(echo:*)",
    # --- checking the work ------------------------------------------
    "Bash(python -m py_compile:*)",
    "Bash(python -m pytest:*)",
    "Bash(python -m unittest:*)",
    "Bash(python -m json.tool:*)",
    "Bash(pytest:*)",
    "Bash(py_compile:*)",
]

DENIED_TOOLS = [
    "Bash(git push:*)",
    "Bash(git commit:*)",
    "Bash(git reset:*)",
    "Bash(git checkout:*)",
    "Bash(git clean:*)",
    "Bash(git rebase:*)",
    "Bash(git merge:*)",
    "Bash(git stash:*)",
    "Bash(git remote:*)",
    "Bash(git config:*)",
    "Bash(gh:*)",
    "Bash(rm:*)",
    "Bash(rmdir:*)",
    "Bash(del:*)",
    "Bash(mv:*)",
    "Bash(curl:*)",
    "Bash(wget:*)",
    "Bash(ssh:*)",
    "Bash(scp:*)",
    "Bash(pip:*)",
    "Bash(npm:*)",
    "Bash(choco:*)",
    "Bash(reg:*)",
    "Bash(shutdown:*)",
    "Bash(format:*)",
    "WebFetch",
    "WebSearch",
]

_BLOCK_PATTERNS = [
    r"\bgit\s+push\b",
    r"\bgit\s+commit\b",
    r"\bgit\s+reset\b",
    r"\bgit\s+checkout\b",
    r"\bgit\s+clean\b",
    r"\bgit\s+rebase\b",
    r"\bgit\s+merge\b",
    r"\bgit\s+stash\b",
    r"\bgit\s+remote\b",
    r"\bgit\s+config\b",
    r"\bgh\b",
    r"\brm\b",
    r"\brmdir\b",
    r"\bdel\s",
    r"\bmv\b",
    r"\bcurl\b",
    r"\bwget\b",
    r"\bssh\b",
    r"\bscp\b",
    r"\bpip\s+install\b",
    r"\bnpm\b",
    r"\bshutdown\b",
    r"\bformat\b",
    r">\s*/dev/sd",
    r"\bmkfs\b",
    r":\(\)\s*\{.*\};:",
]

_BLOCK_RE = [re.compile(p, re.IGNORECASE) for p in _BLOCK_PATTERNS]


def is_blocked(command: str) -> bool:
    """True if `command` matches something we never want Claude to run."""
    if not command:
        return False
    return any(rx.search(command) for rx in _BLOCK_RE)


def describe() -> str:
    """A short, human-readable summary for the UI and the prompt."""
    return (
        "Claude may read files, search the repo, run read-only git commands, "
        "and run tests or syntax checks to verify its work. It cannot commit, "
        "push, delete files, install packages, or reach the network - those "
        "stay behind your approval."
    )
