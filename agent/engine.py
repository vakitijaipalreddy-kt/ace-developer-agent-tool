"""Repo analysis and the Claude Code bridge.

Code generation is delegated to the `claude` CLI running in headless mode
(`claude -p`) inside the repository. That gives the model the same file tools
and repo context the developer gets interactively, and keeps credentials in
Claude Code's own auth rather than in this tool.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from . import permissions, proc, vcs

SKIP_DIRS = {".git", ".metadata", ".vscode", ".idea", "__pycache__", "node_modules"}


class EngineError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# repo analysis
# --------------------------------------------------------------------------

def analyze_repo(repo_path: str, issue_text: str = "", max_hits: int = 25) -> str:
    """Summarize the repository and point at files related to the issue.

    The summary goes into the Claude prompt. It is deliberately compact: the
    model can open anything it needs, so this only has to orient it.
    """
    files = vcs.tracked_files(repo_path)
    by_dir: Counter = Counter()
    by_ext: Counter = Counter()
    for f in files:
        parts = f.split("/")
        by_dir[parts[0] if len(parts) > 1 else "(root)"] += 1
        by_ext[Path(f).suffix.upper() or "(none)"] += 1

    lines = [
        "Repository: " + (vcs.repo_slug(repo_path) or repo_path),
        "Tracked files: {}".format(len(files)),
        "",
        "Top-level layout (file counts):",
    ]
    for d, n in by_dir.most_common(15):
        lines.append("  {:<20} {}".format(d, n))

    lines.append("")
    lines.append("File types:")
    for e, n in by_ext.most_common(12):
        lines.append("  {:<10} {}".format(e, n))

    hits = find_related_files(repo_path, files, issue_text, max_hits)
    if hits:
        lines.append("")
        lines.append("Files whose names match terms from the Jira issue:")
        for h in hits:
            lines.append("  " + h)

    commits = vcs.recent_commits(repo_path, 15)
    if commits:
        lines.append("")
        lines.append("Recent commits (for message/branch conventions):")
        for c in commits.splitlines():
            lines.append("  " + c)

    return "\n".join(lines)


def find_related_files(
    repo_path: str, files: list[str], issue_text: str, limit: int = 25
) -> list[str]:
    """Match issue tokens against file names. Cheap, but usually on target.

    ACE tickets name the artifact directly ("Update STAGE9LD.BAT ..."), so a
    filename match is a strong signal.
    """
    if not issue_text:
        return []

    named = {
        m.group(0).upper()
        for m in re.finditer(r"\b[A-Za-z0-9_$~-]{2,}\.[A-Za-z0-9]{1,4}\b", issue_text)
    }
    stop = {
        "THE", "AND", "FOR", "WITH", "THIS", "THAT", "FROM", "HAVE", "WILL",
        "NEED", "WHEN", "ARE", "NOT", "ISSUE", "JIRA", "PLEASE", "SHOULD",
        "UPDATE", "CHANGE", "ADD", "REMOVE", "FIX", "CODE", "FILE", "TEST",
    }
    words = {
        w.upper()
        for w in re.findall(r"\b[A-Za-z][A-Za-z0-9_]{3,}\b", issue_text)
        if w.upper() not in stop
    }

    scored: list[tuple[int, str]] = []
    for f in files:
        base = Path(f).name.upper()
        stem = Path(f).stem.upper()
        score = 0
        if base in named:
            score += 100
        for w in words:
            if w == stem:
                score += 20
            elif len(w) >= 5 and w in base:
                score += 5
        if score:
            scored.append((score, f))

    scored.sort(key=lambda t: (-t[0], t[1]))
    return [f for _, f in scored[:limit]]


# --------------------------------------------------------------------------
# Claude Code bridge
# --------------------------------------------------------------------------

def claude_executable() -> str:
    exe = shutil.which("claude")
    if not exe:
        raise EngineError(
            "The `claude` CLI was not found on PATH. Install Claude Code, or "
            "open a terminal where `claude --version` works and start this "
            "tool there."
        )
    return exe


SYSTEM_RULES = """You are making a focused code change in an existing repository.

Work in three passes: understand, change, then verify.

1. UNDERSTAND - before editing anything
- Read the files the issue concerns. Do not guess at their contents.
- Look at how similar things are already done here (`git log`, `grep`) and
  follow that precedent rather than inventing a new approach.

2. CHANGE
- Change only what the Jira issue asks for. No drive-by refactors, no
  reformatting of untouched lines, no new dependencies.
- Match the surrounding code's style, casing and comment density exactly.
  This repo contains legacy POS control code (.BAT, .PY, .INI, .BOB, .SQL and
  similar); keep existing conventions even where they differ from modern style.
- Edit files directly with your file tools. Do not print a patch for someone
  else to apply.

3. VERIFY - this is required, not optional
- Re-read every file you changed and confirm the edit is actually what you
  intended, in the right place, and complete.
- Run `git diff` and review your own change as a reviewer would.
- If you changed Python, syntax-check it (`python -m py_compile <file>`).
  If the repo has tests covering what you touched, run them.
- If verification finds a problem, FIX IT and verify again. Do not hand back
  work you know is broken.

You can run read-only commands and tests to do this. You cannot commit, push,
delete files, install packages, or reach the network - a human reviews and
approves your diff afterwards, so leave those to them.

Finally:
- If the issue is ambiguous or you cannot safely determine the change, make no
  edits and explain what is blocking you.
- End your reply with a one-line summary prefixed exactly with 'SUMMARY: ',
  and if you verified the change, state briefly how on the line before it.
"""


def build_prompt(issue_context: str, repo_summary: str, extra: str = "") -> str:
    sections = [
        SYSTEM_RULES,
        "=" * 70,
        "JIRA ISSUE",
        "=" * 70,
        issue_context,
        "",
        "=" * 70,
        "REPOSITORY CONTEXT",
        "=" * 70,
        repo_summary,
    ]
    if extra.strip():
        sections += [
            "",
            "=" * 70,
            "ADDITIONAL INSTRUCTIONS FROM THE DEVELOPER",
            "=" * 70,
            extra.strip(),
        ]
    sections += [
        "",
        "=" * 70,
        "TASK",
        "=" * 70,
        "Implement the change described by the Jira issue in this repository. "
        "Inspect the relevant files first, then make the edits.",
    ]
    return "\n".join(sections)


def generate_code(
    repo_path: str,
    prompt: str,
    model: str = "",
    timeout: int = 1800,
    on_output=None,
) -> str:
    """Run Claude Code headless in the repo so it edits files in place.

    Streams stdout to `on_output` as it arrives. Returns the full transcript.
    """
    exe = claude_executable()
    args = [exe, "-p", prompt, "--permission-mode", "acceptEdits"]

    # Let Claude inspect the repo and run tests so it can check its own work,
    # but keep publishing, deletion and the network off the table.
    args += ["--allowedTools"] + permissions.ALLOWED_TOOLS
    args += ["--disallowedTools"] + permissions.DENIED_TOOLS

    if model:
        args += ["--model", model]

    env = dict(os.environ)
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"

    try:
        process = proc.popen(
            args,
            cwd=repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
    except OSError as exc:
        raise EngineError("Could not start Claude Code: {}".format(exc)) from exc

    chunks: list[str] = []
    try:
        assert process.stdout is not None
        for line in process.stdout:
            chunks.append(line)
            if on_output:
                on_output(line.rstrip("\n"))
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        raise EngineError("Claude Code timed out after {}s.".format(timeout))

    transcript = "".join(chunks).strip()
    if code != 0:
        raise EngineError(
            "Claude Code exited with status {}.\n\n{}".format(code, transcript[-4000:])
        )
    return transcript


def extract_summary(transcript: str, fallback: str) -> str:
    """Pull the 'SUMMARY: ...' line out of the transcript."""
    for line in reversed(transcript.splitlines()):
        s = line.strip()
        if s.upper().startswith("SUMMARY:"):
            text = s[len("SUMMARY:"):].strip()
            if text:
                return text
    return fallback
