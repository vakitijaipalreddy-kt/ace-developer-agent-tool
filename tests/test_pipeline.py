"""End-to-end pipeline tests against a scratch git repo.

Jira, Claude and the GitHub CLI are stubbed, so these exercise the real
branch/commit/approval/rollback logic without network access.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASSED: list[str] = []
FAILED: list[str] = []
NL = chr(10)


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print("  PASS  " + name)
    else:
        FAILED.append(name + (" :: " + detail if detail else ""))
        print("  FAIL  " + name + ("  -> " + detail if detail else ""))


_cfgtmp = tempfile.mkdtemp(prefix="acepipecfg_")
os.environ["ACE_PR_AGENT_CONFIG"] = str(Path(_cfgtmp) / "config.json")

from agent import engine, jira, pipeline, vcs  # noqa: E402
from agent.config import Config  # noqa: E402


def make_repo() -> str:
    """A scratch repo with a fake 'origin' so push/branching behave normally."""
    tmp = tempfile.mkdtemp(prefix="acepr_")
    origin = Path(tmp) / "origin.git"
    work = Path(tmp) / "work"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   capture_output=True, check=True)
    subprocess.run(["git", "clone", str(origin), str(work)],
                   capture_output=True, check=True)
    for args in (["git", "config", "user.email", "test@example.com"],
                 ["git", "config", "user.name", "Test"]):
        subprocess.run(args, cwd=work, capture_output=True, check=True)
    (work / "UPGM").mkdir()
    (work / "UPGM" / "STAGE9LD.BAT").write_text(
        "ECHO OFF" + NL + "REM stage the 9 load" + NL + "CALL LOAD9.BAT" + NL,
        encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=work, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=work,
                   capture_output=True, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=work,
                   capture_output=True, check=True)
    return str(work)


FAKE_ISSUE = jira.Issue(
    key="ACE-24308",
    summary="Update STAGE9LD.BAT to avoid 9 loads occurring twice",
    description="Add a lock file guard so LOAD9.BAT runs once per cycle.",
    status="In Progress",
    issue_type="Task",
    assignee="Tester",
    url="https://kroger.atlassian.net/browse/ACE-24308",
)


class FakeJiraClient:
    instances: list["FakeJiraClient"] = []

    def __init__(self, *a, **kw):
        self.comments: list[tuple[str, str]] = []
        FakeJiraClient.instances.append(self)

    def get_issue(self, key):
        return FAKE_ISSUE

    def add_comment(self, key, text):
        self.comments.append((key, text))
        return "10001"


def install_stubs(monkey: dict, repo: str, *, edit: bool = True,
                  jira_comment_fails: bool = False):
    record = {"pushed": [], "pr": [], "prompt": ""}

    monkey["jira_client"] = jira.JiraClient
    jira.JiraClient = FakeJiraClient
    pipeline.jira.JiraClient = FakeJiraClient

    if jira_comment_fails:
        def boom(self, key, text):
            raise jira.JiraError("comment rejected")
        FakeJiraClient.add_comment = boom
    else:
        def ok(self, key, text):
            self.comments.append((key, text))
            return "10001"
        FakeJiraClient.add_comment = ok

    monkey["generate"] = engine.generate_code

    def fake_generate(repo_path, prompt, model="", timeout=1800, on_output=None):
        record["prompt"] = prompt
        if on_output:
            on_output("Reading UPGM/STAGE9LD.BAT")
        if edit:
            p = Path(repo_path) / "UPGM" / "STAGE9LD.BAT"
            p.write_text(
                "ECHO OFF" + NL + "REM stage the 9 load" + NL
                + "IF EXIST TRM9LOAD.LCK GOTO SKIP" + NL
                + "ECHO LOCKED > TRM9LOAD.LCK" + NL
                + "CALL LOAD9.BAT" + NL + "DEL TRM9LOAD.LCK" + NL + ":SKIP" + NL,
                encoding="utf-8")
        return "did it" + NL + "SUMMARY: Guarded the 9-load with a lock file."

    engine.generate_code = fake_generate
    pipeline.engine.generate_code = fake_generate

    monkey["push"] = vcs.push
    monkey["create_pr"] = vcs.create_pr

    def fake_push(cwd, branch):
        record["pushed"].append(branch)
        return "pushed " + branch

    def fake_create_pr(cwd, branch, base, title, body, token=""):
        record["pr"].append({"branch": branch, "base": base,
                             "title": title, "body": body})
        return "https://github.com/krogertechnology/pos-ace-control-code/pull/999"

    vcs.push = fake_push
    vcs.create_pr = fake_create_pr
    pipeline.vcs.push = fake_push
    pipeline.vcs.create_pr = fake_create_pr

    # gh checks in preflight must not touch the network
    monkey["gh_avail"] = vcs.gh_available
    monkey["gh_auth"] = vcs.gh_authenticated
    vcs.gh_available = lambda cwd: True
    vcs.gh_authenticated = lambda cwd, token="": True
    pipeline.vcs.gh_available = vcs.gh_available
    pipeline.vcs.gh_authenticated = vcs.gh_authenticated
    return record


def restore(monkey: dict) -> None:
    jira.JiraClient = monkey["jira_client"]
    pipeline.jira.JiraClient = monkey["jira_client"]
    engine.generate_code = monkey["generate"]
    pipeline.engine.generate_code = monkey["generate"]
    vcs.push = monkey["push"]
    vcs.create_pr = monkey["create_pr"]
    pipeline.vcs.push = monkey["push"]
    pipeline.vcs.create_pr = monkey["create_pr"]
    vcs.gh_available = monkey["gh_avail"]
    vcs.gh_authenticated = monkey["gh_auth"]
    pipeline.vcs.gh_available = monkey["gh_avail"]
    pipeline.vcs.gh_authenticated = monkey["gh_auth"]


def make_cfg(repo: str) -> Config:
    return Config(
        jira_base_url="https://kroger.atlassian.net",
        jira_email="t@example.com",
        jira_token="tok",
        repo_path=repo,
        base_branch="main",
        branch_template="{key}",
        commit_template="{key} - {summary}",
        post_pr_link_to_jira=True,
    )


def collect_hooks(decision="approve"):
    log: list[str] = []
    steps: list[tuple[int, str]] = []
    diffs: list[str] = []
    finish: list[tuple[bool, str]] = []
    hooks = pipeline.Hooks(
        on_step=lambda i, n, s: steps.append((i, s)),
        on_log=log.append,
        on_issue=lambda issue: None,
        on_diff=lambda d, s: diffs.append(d),
        on_finish=lambda ok, m: finish.append((ok, m)),
        ask_approval=lambda: decision,
    )
    return hooks, log, steps, diffs, finish


# ---------------------------------------------------------------- tests

def test_happy_path() -> None:
    print(NL + "[happy path - approve]")
    repo = make_repo()
    monkey: dict = {}
    FakeJiraClient.instances.clear()
    rec = install_stubs(monkey, repo)
    try:
        hooks, log, steps, diffs, finish = collect_hooks("approve")
        pl = pipeline.Pipeline(make_cfg(repo), hooks)
        result = pl.run("ace-24308")

        check("finished ok", bool(finish) and finish[0][0], str(finish))
        check("branch created", result.branch == "ACE-24308", result.branch)
        check("on the branch", vcs.current_branch(repo) == "ACE-24308")
        check("commit made", bool(result.commit), result.commit)
        check("pushed", rec["pushed"] == ["ACE-24308"], str(rec["pushed"]))
        check("PR created", len(rec["pr"]) == 1)
        check("PR url returned", result.pr_url.endswith("/pull/999"),
              result.pr_url)
        if rec["pr"]:
            check("PR title has key and summary",
                  rec["pr"][0]["title"] == "ACE-24308 - " + FAKE_ISSUE.summary,
                  rec["pr"][0]["title"])
            check("PR base is main", rec["pr"][0]["base"] == "main")
            check("PR body links Jira", FAKE_ISSUE.url in rec["pr"][0]["body"])
        check("diff was shown", bool(diffs) and "TRM9LOAD.LCK" in diffs[0])

        client = FakeJiraClient.instances[0]
        check("Jira comment posted", len(client.comments) == 1,
              str(client.comments))
        check("comment has PR link", "/pull/999" in client.comments[0][1])

        last = subprocess.run(["git", "log", "-1", "--pretty=%s"], cwd=repo,
                              capture_output=True, text=True).stdout.strip()
        check("commit message", last == "ACE-24308 - " + FAKE_ISSUE.summary, last)
        check("tree clean after commit", vcs.is_clean(repo))
        check("all 9 steps done",
              sum(1 for _, s in steps if s == pipeline.DONE) == 9)
        check("prompt carried the issue", "ACE-24308" in rec["prompt"])
        check("prompt carried repo context", "Tracked files" in rec["prompt"])
    finally:
        restore(monkey)
        shutil.rmtree(Path(repo).parent, ignore_errors=True)


def test_reject() -> None:
    print(NL + "[reject - discard everything]")
    repo = make_repo()
    monkey: dict = {}
    FakeJiraClient.instances.clear()
    rec = install_stubs(monkey, repo)
    try:
        hooks, log, steps, diffs, finish = collect_hooks("abort")
        pl = pipeline.Pipeline(make_cfg(repo), hooks)
        result = pl.run("ACE-24308")

        check("did not finish ok", bool(finish) and not finish[0][0], str(finish))
        check("nothing pushed", rec["pushed"] == [])
        check("no PR", rec["pr"] == [])
        check("no commit recorded", result.commit == "")
        check("back on main", vcs.current_branch(repo) == "main",
              vcs.current_branch(repo))
        check("tree clean", vcs.is_clean(repo), vcs.status_porcelain(repo))
        original = (Path(repo) / "UPGM" / "STAGE9LD.BAT").read_text(encoding="utf-8")
        check("file restored", "TRM9LOAD.LCK" not in original, original)
        check("no Jira comment", not FakeJiraClient.instances[0].comments)
    finally:
        restore(monkey)
        shutil.rmtree(Path(repo).parent, ignore_errors=True)


def test_regenerate() -> None:
    print(NL + "[regenerate propagates to the caller]")
    repo = make_repo()
    monkey: dict = {}
    FakeJiraClient.instances.clear()
    install_stubs(monkey, repo)
    try:
        hooks, *_ = collect_hooks("regenerate")
        pl = pipeline.Pipeline(make_cfg(repo), hooks)
        raised = False
        try:
            pl.run("ACE-24308")
        except pipeline.Regenerate:
            raised = True
        check("Regenerate propagates", raised)
    finally:
        restore(monkey)
        shutil.rmtree(Path(repo).parent, ignore_errors=True)


def test_no_edits() -> None:
    print(NL + "[claude makes no edits]")
    repo = make_repo()
    monkey: dict = {}
    FakeJiraClient.instances.clear()
    rec = install_stubs(monkey, repo, edit=False)
    try:
        hooks, log, steps, diffs, finish = collect_hooks("approve")
        pl = pipeline.Pipeline(make_cfg(repo), hooks)
        pl.run("ACE-24308")
        check("did not finish ok", bool(finish) and not finish[0][0])
        check("message explains", "no file changes" in finish[0][1].lower(),
              finish[0][1])
        check("nothing pushed", rec["pushed"] == [])
        check("no PR", rec["pr"] == [])
        check("no approval was requested", diffs == [])
    finally:
        restore(monkey)
        shutil.rmtree(Path(repo).parent, ignore_errors=True)


def test_jira_comment_failure_keeps_pr() -> None:
    print(NL + "[jira comment fails - PR must still stand]")
    repo = make_repo()
    monkey: dict = {}
    FakeJiraClient.instances.clear()
    install_stubs(monkey, repo, jira_comment_fails=True)
    try:
        hooks, log, steps, diffs, finish = collect_hooks("approve")
        pl = pipeline.Pipeline(make_cfg(repo), hooks)
        result = pl.run("ACE-24308")
        check("run reported success", bool(finish) and finish[0][0], str(finish))
        check("PR still created", result.pr_url.endswith("/pull/999"))
        check("failure surfaced in notes",
              any("Jira comment failed" in n for n in result.notes),
              str(result.notes))
        check("step 9 marked failed", (8, pipeline.FAILED) in steps)
    finally:
        restore(monkey)
        shutil.rmtree(Path(repo).parent, ignore_errors=True)


def test_preflight_guards() -> None:
    print(NL + "[preflight guards]")
    repo = make_repo()
    monkey: dict = {}
    install_stubs(monkey, repo)
    try:
        cfg = make_cfg(repo)
        pl = pipeline.Pipeline(cfg, pipeline.Hooks())
        probs = pl.preflight("not-a-key")
        check("rejects bad key",
              any("not a valid Jira issue key" in p for p in probs), str(probs))

        (Path(repo) / "dirty.txt").write_text("x", encoding="utf-8")
        probs = pl.preflight("ACE-24308")
        check("rejects dirty tree",
              any("uncommitted changes" in p for p in probs), str(probs))

        cfg2 = make_cfg(repo)
        cfg2.jira_token = ""
        pl2 = pipeline.Pipeline(cfg2, pipeline.Hooks())
        check("rejects missing token",
              any("jira_token" in p for p in pl2.preflight("ACE-24308")))
    finally:
        restore(monkey)
        shutil.rmtree(Path(repo).parent, ignore_errors=True)


def test_resume_existing_branch() -> None:
    print(NL + "[re-run on an existing branch resumes]")
    repo = make_repo()
    subprocess.run(["git", "checkout", "-b", "ACE-24308"], cwd=repo,
                   capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=repo, capture_output=True)
    msg = vcs.create_branch(repo, "ACE-24308", "main")
    check("checked out existing", "existing branch" in msg, msg)
    check("on the branch", vcs.current_branch(repo) == "ACE-24308")
    shutil.rmtree(Path(repo).parent, ignore_errors=True)


if __name__ == "__main__":
    try:
        test_happy_path()
        test_reject()
        test_regenerate()
        test_no_edits()
        test_jira_comment_failure_keeps_pr()
        test_preflight_guards()
        test_resume_existing_branch()
    finally:
        shutil.rmtree(_cfgtmp, ignore_errors=True)

    print(NL + "=" * 60)
    print("passed: {}   failed: {}".format(len(PASSED), len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  FAILED: " + f)
        sys.exit(1)
    print("ALL TESTS PASSED")
