"""Git and GitHub operations for the ACE Developer Agent.

Everything shells out to `git` and `gh` so the tool inherits whatever
credentials and proxy settings the developer already has working. All process
launches go through `proc`, which suppresses the console window that would
otherwise flash on screen under pythonw.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass

from . import proc


class VcsError(RuntimeError):
    pass


@dataclass
class CommandResult:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def text(self) -> str:
        return (self.out + ("\n" + self.err if self.err else "")).strip()


def run(args: list[str], cwd: str, timeout: int = 120, check: bool = False,
        token: str = "") -> CommandResult:
    """Run a command without a shell or a console window.

    `token`, when given, is exported as GH_TOKEN for this one call so `gh`
    authenticates without the developer having to run `gh auth login`. It is
    passed through the environment rather than the command line so it never
    appears in a process listing or in any error message we echo back.
    """
    env = None
    if token:
        env = dict(os.environ)
        env["GH_TOKEN"] = token
        env["GITHUB_TOKEN"] = token

    try:
        completed = proc.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as exc:
        raise VcsError("Command not found: {} ({})".format(args[0], exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise VcsError(
            "Command timed out after {}s: {}".format(timeout, " ".join(args))
        ) from exc

    result = CommandResult(
        completed.returncode, completed.stdout or "", completed.stderr or ""
    )
    if check and not result.ok:
        raise VcsError(
            "`{}` failed ({}):\n{}".format(" ".join(args), result.code, result.text)
        )
    return result


def git(args: list[str], cwd: str, **kw) -> CommandResult:
    return run(["git"] + args, cwd, **kw)


# --------------------------------------------------------------------------
# repository inspection
# --------------------------------------------------------------------------

def is_repo(cwd: str) -> bool:
    return git(["rev-parse", "--is-inside-work-tree"], cwd).ok


def current_branch(cwd: str) -> str:
    return git(["rev-parse", "--abbrev-ref", "HEAD"], cwd, check=True).out.strip()


def status_porcelain(cwd: str) -> str:
    return git(["status", "--porcelain"], cwd, check=True).out.strip()


def is_clean(cwd: str) -> bool:
    return status_porcelain(cwd) == ""


def branch_exists(cwd: str, branch: str) -> bool:
    return git(["rev-parse", "--verify", "--quiet", "refs/heads/" + branch], cwd).ok


def remote_branch_exists(cwd: str, branch: str) -> bool:
    res = git(["ls-remote", "--heads", "origin", branch], cwd)
    return res.ok and bool(res.out.strip())


def repo_slug(cwd: str) -> str:
    """Return 'owner/name' parsed from origin, or '' if it cannot be read."""
    res = git(["remote", "get-url", "origin"], cwd)
    if not res.ok:
        return ""
    url = res.out.strip()
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else ""


def tracked_files(cwd: str, limit: int = 4000) -> list[str]:
    res = git(["ls-files"], cwd, check=True)
    files = [f for f in res.out.splitlines() if f.strip()]
    return files[:limit]


def recent_commits(cwd: str, count: int = 20) -> str:
    res = git(["log", "--oneline", "-n", str(count)], cwd)
    return res.out.strip() if res.ok else ""


# --------------------------------------------------------------------------
# branch / commit / push
# --------------------------------------------------------------------------

def sanitize_branch(name: str) -> str:
    """Make a string safe for use as a git branch name."""
    name = name.strip().replace(" ", "-")
    name = re.sub(r"[^A-Za-z0-9._/-]", "", name)
    name = re.sub(r"-{2,}", "-", name).strip("-/.")
    return name[:100] or "ace-change"


def fetch(cwd: str) -> CommandResult:
    return git(["fetch", "origin", "--prune"], cwd, timeout=180)


def create_branch(cwd: str, branch: str, base: str, from_origin: bool = True) -> str:
    """Create and check out `branch` off `base`. Returns a log line.

    If the branch already exists locally we check it out instead of failing,
    so a re-run of the tool on the same ticket resumes rather than blows up.
    """
    branch = sanitize_branch(branch)
    if branch_exists(cwd, branch):
        git(["checkout", branch], cwd, check=True)
        return "Checked out existing branch " + branch

    start_point = base
    if from_origin:
        fetch(cwd)
        if remote_branch_exists(cwd, base):
            start_point = "origin/" + base

    git(["checkout", "-b", branch, start_point], cwd, check=True)
    return "Created branch {} from {}".format(branch, start_point)


def diff_unstaged(cwd: str) -> str:
    """Working-tree diff including untracked files (shown via --intent-to-add)."""
    git(["add", "--intent-to-add", "--all"], cwd)
    return git(["diff", "--no-color"], cwd).out


def diff_staged(cwd: str) -> str:
    return git(["diff", "--cached", "--no-color"], cwd).out


def diff_stat(cwd: str) -> str:
    git(["add", "--intent-to-add", "--all"], cwd)
    return git(["diff", "--stat"], cwd).out.strip()


def changed_files(cwd: str) -> list[str]:
    git(["add", "--intent-to-add", "--all"], cwd)
    res = git(["diff", "--name-only"], cwd)
    return [f for f in res.out.splitlines() if f.strip()]


def discard_all_changes(cwd: str) -> None:
    """Throw away every uncommitted change, including new files."""
    git(["reset"], cwd)
    git(["checkout", "--", "."], cwd)
    git(["clean", "-fd"], cwd)


def commit_all(cwd: str, message: str) -> str:
    git(["add", "--all"], cwd, check=True)
    if not git(["diff", "--cached", "--quiet"], cwd).ok:
        git(["commit", "-m", message], cwd, check=True)
        return git(["rev-parse", "--short", "HEAD"], cwd, check=True).out.strip()
    raise VcsError("Nothing staged to commit.")


def push(cwd: str, branch: str) -> str:
    res = git(["push", "--set-upstream", "origin", branch], cwd, timeout=300)
    if not res.ok:
        raise VcsError("git push failed:\n" + res.text)
    return res.text


# --------------------------------------------------------------------------
# GitHub via gh
# --------------------------------------------------------------------------

def gh_available(cwd: str) -> bool:
    return run(["gh", "--version"], cwd).ok


def gh_authenticated(cwd: str, token: str = "") -> bool:
    """True if `gh` can act as a user.

    `gh auth status` exits 0 even when a supplied token is rejected, so the
    text is what matters, not the return code.
    """
    res = run(["gh", "auth", "status"], cwd, timeout=60, token=token)
    text = res.text.lower()
    if "is invalid" in text or "not logged" in text:
        return False
    return res.ok and ("logged in" in text or "active account: true" in text)


def gh_login_name(cwd: str, token: str = "") -> str:
    """The account `gh` is acting as, or '' if it cannot tell."""
    res = run(["gh", "api", "user", "--jq", ".login"], cwd, timeout=60, token=token)
    name = res.out.strip()
    return name if res.ok and name and not name.startswith("{") else ""


def existing_pr_url(cwd: str, branch: str, token: str = "") -> str:
    res = run(
        ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "url"],
        cwd,
        timeout=120,
        token=token,
    )
    if not res.ok:
        return ""
    try:
        items = json.loads(res.out or "[]")
    except json.JSONDecodeError:
        return ""
    return items[0].get("url", "") if items else ""


def create_pr(cwd: str, branch: str, base: str, title: str, body: str,
              token: str = "") -> str:
    """Create a PR and return its URL. Returns the existing PR if there is one."""
    already = existing_pr_url(cwd, branch, token=token)
    if already:
        return already

    res = run(
        ["gh", "pr", "create", "--base", base, "--head", branch,
         "--title", title, "--body", body],
        cwd,
        timeout=300,
        token=token,
    )
    if not res.ok:
        raise VcsError("gh pr create failed:\n" + res.text)

    m = re.search(r"https://\S*/pull/\d+", res.text)
    if m:
        return m.group(0)
    found = existing_pr_url(cwd, branch, token=token)
    if found:
        return found
    raise VcsError("PR may have been created but no URL was returned:\n" + res.text)
