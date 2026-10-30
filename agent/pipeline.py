"""The ACE Developer Agent pipeline.

    Enter Jira -> Read Jira -> Analyze Repo -> Ask Claude -> Generate & verify
       -> Show Diff (approval gate) -> Commit -> Push -> Create PR
       -> Post PR link to Jira

The pipeline runs on a worker thread and talks to the UI only through the
callbacks in `Hooks`, so the same object can drive the GUI or a console.
"""

from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, field
from typing import Callable

from . import engine, jira, permissions, vcs
from .config import Config

STEPS = [
    "Read Jira details",
    "Analyze local repo",
    "Ask Claude",
    "Generate & verify code",
    "Show diff for approval",
    "Commit changes",
    "Push branch",
    "Create PR",
    "Post PR link to Jira",
]

PENDING, RUNNING, DONE, FAILED, SKIPPED = (
    "pending", "running", "done", "failed", "skipped")


@dataclass
class Hooks:
    """UI callbacks. All are invoked from the worker thread."""

    on_step: Callable[[int, str, str], None] = lambda i, name, state: None
    on_log: Callable[[str], None] = lambda msg: None
    on_issue: Callable[[jira.Issue], None] = lambda issue: None
    on_diff: Callable[[str, str], None] = lambda diff, stat: None
    on_finish: Callable[[bool, str], None] = lambda ok, msg: None
    # Must block and return "approve" | "regenerate" | "abort"
    ask_approval: Callable[[], str] = lambda: "abort"


@dataclass
class Result:
    issue: jira.Issue | None = None
    branch: str = ""
    commit: str = ""
    pr_url: str = ""
    diff: str = ""
    notes: list[str] = field(default_factory=list)


class Aborted(RuntimeError):
    pass


class Regenerate(RuntimeError):
    """Raised internally when the user asks for another attempt."""


class Pipeline:
    def __init__(self, cfg: Config, hooks: Hooks, extra_instructions: str = ""):
        self.cfg = cfg
        self.hooks = hooks
        self.extra = extra_instructions
        self.result = Result()
        self.cancel_event = threading.Event()
        self._states = {i: PENDING for i in range(len(STEPS))}

    # ---------------- helpers ----------------

    def log(self, msg: str) -> None:
        self.hooks.on_log(msg)

    def _set(self, idx: int, state: str) -> None:
        self._states[idx] = state
        self.hooks.on_step(idx, STEPS[idx], state)

    def _check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Aborted("Cancelled by user.")

    def cancel(self) -> None:
        self.cancel_event.set()

    def reset_steps(self) -> None:
        for i in range(len(STEPS)):
            self._set(i, PENDING)

    # ---------------- preflight ----------------

    def preflight(self, issue_key: str) -> list[str]:
        """Return a list of blocking problems before anything is changed."""
        problems = self.cfg.validate()
        try:
            jira.normalize_key(issue_key)
        except jira.JiraError as exc:
            problems.append(str(exc))

        repo = self.cfg.repo_path
        if repo and vcs.is_repo(repo):
            if not vcs.is_clean(repo):
                problems.append(
                    "The working tree has uncommitted changes. Commit, stash or "
                    "discard them first so the generated diff is unambiguous."
                )
            if not vcs.gh_available(repo):
                problems.append("GitHub CLI (`gh`) was not found on PATH.")
            elif not vcs.gh_authenticated(repo, self.cfg.github_token):
                if self.cfg.github_token:
                    problems.append(
                        "The GitHub token in Settings was rejected. Check it has "
                        "not expired and carries the 'repo' scope."
                    )
                else:
                    problems.append(
                        "GitHub is not authenticated. Either paste a GitHub token "
                        "in Settings, or use the Terminal tab to sign in."
                    )
        try:
            engine.claude_executable()
        except engine.EngineError as exc:
            problems.append(str(exc))
        return problems

    # ---------------- the run ----------------

    def run(self, issue_key: str) -> Result:
        repo = self.cfg.repo_path
        try:
            # 1 - Read Jira ------------------------------------------------
            self._set(0, RUNNING)
            key = jira.normalize_key(issue_key)
            self.log("Fetching {} from Jira...".format(key))
            client = jira.JiraClient(
                self.cfg.jira_base_url, self.cfg.jira_email, self.cfg.jira_token
            )
            issue = client.get_issue(key)
            self.result.issue = issue
            self.hooks.on_issue(issue)
            self.log("  {}: {}".format(issue.key, issue.summary))
            self.log("  status={} type={} assignee={}".format(
                issue.status or "-", issue.issue_type or "-",
                issue.assignee or "-"))
            self._set(0, DONE)
            self._check_cancel()

            # 2 - Analyze repo ---------------------------------------------
            self._set(1, RUNNING)
            self.log("Analyzing repository at {}...".format(repo))
            issue_text = issue.summary + "\n" + issue.description
            repo_summary = engine.analyze_repo(repo, issue_text)
            related = engine.find_related_files(
                repo, vcs.tracked_files(repo), issue_text)
            if related:
                self.log("  Likely files: " + ", ".join(related[:6]))
            else:
                self.log("  No filename matches; Claude will search the repo itself.")
            self._set(1, DONE)
            self._check_cancel()

            # branch before any edits, so generated code lands on the branch
            branch = vcs.sanitize_branch(
                self.cfg.branch_template.format(
                    key=issue.key, summary=issue.summary)
            )
            self.log(vcs.create_branch(repo, branch, self.cfg.base_branch))
            self.result.branch = branch

            # 3 + 4 - Ask Claude / Generate code ---------------------------
            self._set(2, RUNNING)
            prompt = engine.build_prompt(
                issue.as_prompt_context(), repo_summary, self.extra
            )
            self.log("Asking Claude ({} chars of context)...".format(len(prompt)))
            self.log("  " + permissions.describe())
            self._set(2, DONE)

            self._set(3, RUNNING)
            transcript = engine.generate_code(
                repo,
                prompt,
                model=self.cfg.claude_model,
                on_output=lambda line: self.log("  | " + line),
            )
            self._check_cancel()

            changed = vcs.changed_files(repo)
            if not changed:
                self._set(3, FAILED)
                tail = transcript[-1500:] if transcript else "(no output)"
                raise Aborted("Claude made no file changes. It said:\n\n" + tail)
            self.log("Changed files: " + ", ".join(changed))
            self.result.notes.append(
                engine.extract_summary(transcript, issue.summary))
            self._set(3, DONE)

            # 5 - Show diff for approval -----------------------------------
            self._set(4, RUNNING)
            diff = vcs.diff_unstaged(repo)
            stat = vcs.diff_stat(repo)
            self.result.diff = diff
            self.hooks.on_diff(diff, stat)
            self.log("Waiting for your approval of the diff...")

            decision = self.hooks.ask_approval()
            if decision == "abort":
                self.log("Rejected. Discarding changes and returning to base branch.")
                vcs.discard_all_changes(repo)
                vcs.git(["checkout", self.cfg.base_branch], repo)
                self._set(4, FAILED)
                raise Aborted("You rejected the generated changes; nothing was pushed.")
            if decision == "regenerate":
                self._set(4, SKIPPED)
                raise Regenerate()
            self._set(4, DONE)
            self._check_cancel()

            # 6 - Commit ----------------------------------------------------
            self._set(5, RUNNING)
            message = self.cfg.commit_template.format(
                key=issue.key, summary=issue.summary)
            sha = vcs.commit_all(repo, message)
            self.result.commit = sha
            self.log("Committed {} - {}".format(sha, message))
            self._set(5, DONE)

            # 7 - Push ------------------------------------------------------
            self._set(6, RUNNING)
            self.log("Pushing {} to origin...".format(branch))
            vcs.push(repo, branch)
            self._set(6, DONE)

            # 8 - Create PR -------------------------------------------------
            self._set(7, RUNNING)
            title = "{} - {}".format(issue.key, issue.summary)
            body = self._pr_body(issue)
            pr_url = vcs.create_pr(repo, branch, self.cfg.base_branch, title, body,
                                   token=self.cfg.github_token)
            self.result.pr_url = pr_url
            self.log("PR: " + pr_url)
            self._set(7, DONE)

            # 9 - Post link to Jira -----------------------------------------
            if self.cfg.post_pr_link_to_jira:
                self._set(8, RUNNING)
                try:
                    client.add_comment(
                        issue.key,
                        "Pull request raised for this issue: {}\n"
                        "Branch: {}\n"
                        "Commit: {}".format(pr_url, branch, sha),
                    )
                    self.log("Posted PR link to " + issue.key)
                    self._set(8, DONE)
                except jira.JiraError as exc:
                    # The PR exists; a failed comment should not fail the run.
                    self.log("Could not comment on Jira: " + str(exc))
                    self._set(8, FAILED)
                    self.result.notes.append("Jira comment failed: " + str(exc))
            else:
                self._set(8, SKIPPED)
                self.log("Skipped Jira comment (disabled in config).")

            self.hooks.on_finish(True, "Done. PR: " + pr_url)
            return self.result

        except Regenerate:
            raise
        except Aborted as exc:
            self._mark_running_failed()
            self.hooks.on_finish(False, str(exc))
            return self.result
        except (jira.JiraError, vcs.VcsError, engine.EngineError) as exc:
            self._mark_running_failed()
            self.log("ERROR: " + str(exc))
            self.hooks.on_finish(False, str(exc))
            return self.result
        except Exception as exc:  # unexpected - show the trace, do not swallow
            self._mark_running_failed()
            self.log("UNEXPECTED ERROR:\n" + traceback.format_exc())
            self.hooks.on_finish(False, "{}: {}".format(type(exc).__name__, exc))
            return self.result

    def _mark_running_failed(self) -> None:
        for i, state in self._states.items():
            if state == RUNNING:
                self._set(i, FAILED)

    def _pr_body(self, issue: jira.Issue) -> str:
        notes = "\n".join("- " + n for n in self.result.notes if n)
        return (
            "## {key} - {summary}\n\n"
            "Jira: {url}\n\n"
            "### What changed\n{notes}\n\n"
            "### Issue description\n{desc}\n\n"
            "---\n"
            "Raised with the ACE Developer Agent.\n"
            "\N{ROBOT FACE} Generated with [Claude Code](https://claude.com/claude-code)\n"
        ).format(
            key=issue.key,
            summary=issue.summary,
            url=issue.url,
            notes=notes or "- See the diff.",
            desc=(issue.description or "(none)")[:3000],
        )
