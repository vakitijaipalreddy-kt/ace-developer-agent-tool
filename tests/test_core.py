"""Core suite: jira parsing, vcs helpers, permissions, engine, config."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASSED: list[str] = []
FAILED: list[str] = []
NL = chr(10)


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print("  PASS  " + name)
    else:
        FAILED.append(name)
        print("  FAIL  " + name + ("  -> " + detail if detail else ""))


tmp = tempfile.mkdtemp(prefix="acecore_")
os.environ["ACE_PR_AGENT_CONFIG"] = str(Path(tmp) / "config.json")
# the real environment may set these; they legitimately override the config
# file, so clear them here or the precedence hides what we are testing
for _var in ("JIRA_EMAIL", "JIRA_TOKEN", "JIRA_BASE_URL", "ACE_REPO_PATH",
             "ACE_BASE_BRANCH", "ACE_CLAUDE_MODEL", "GH_TOKEN", "GITHUB_TOKEN"):
    os.environ.pop(_var, None)

from agent import config as cfgmod  # noqa: E402
from agent import engine, jira, permissions, vcs  # noqa: E402


def test_jira_keys():
    print(NL + "[jira key normalisation]")
    cases = [
        ("ace-24308", "ACE-24308"),
        (" ACE-24308 ", "ACE-24308"),
        ("https://kroger.atlassian.net/browse/ACE-24308", "ACE-24308"),
        ("https://kroger.atlassian.net/browse/ACE-24308?focus=1", "ACE-24308"),
    ]
    for raw, want in cases:
        check("normalises " + repr(raw), jira.normalize_key(raw) == want)
    for bad in ["", "nonsense", "ACE24308", "24308"]:
        try:
            jira.normalize_key(bad)
            check("rejects " + repr(bad), False)
        except jira.JiraError:
            check("rejects " + repr(bad), True)


def test_jira_url():
    print(NL + "[jira url]")
    c = jira.JiraClient("https://kroger.atlassian.net", "e@x.com", "tok")
    want = "https://kroger.atlassian.net/rest/api/3/issue/ACE-24308"
    check("issue URL is the documented form",
          c.api + "/issue/ACE-24308" == want, c.api + "/issue/ACE-24308")
    c2 = jira.JiraClient("https://kroger.atlassian.net/", "e@x.com", "tok")
    check("trailing slash handled", c2.api + "/issue/ACE-24308" == want)
    check("browse url",
          c.browse + "/ACE-24308"
          == "https://kroger.atlassian.net/browse/ACE-24308")
    check("truststore active", jira.SYSTEM_CERTS)


def test_adf():
    print(NL + "[adf to text]")
    doc = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 2},
             "content": [{"type": "text", "text": "Problem"}]},
            {"type": "paragraph", "content": [
                {"type": "text", "text": "Update "},
                {"type": "text", "text": "STAGE9LD.BAT",
                 "marks": [{"type": "code"}]},
                {"type": "text", "text": " see",
                 "marks": [{"type": "link",
                            "attrs": {"href": "https://x.test/1"}}]}]},
            {"type": "bulletList", "content": [
                {"type": "listItem", "content": [
                    {"type": "paragraph",
                     "content": [{"type": "text", "text": "first"}]}]}]},
            {"type": "codeBlock", "attrs": {"language": "bat"},
             "content": [{"type": "text", "text": "ECHO OFF"}]},
        ],
    }
    out = jira.adf_to_text(doc)
    check("heading", "## Problem" in out)
    check("inline code", "`STAGE9LD.BAT`" in out)
    check("link target", "<https://x.test/1>" in out)
    check("bullet", "- first" in out)
    check("code block", "ECHO OFF" in out)
    check("none is empty", jira.adf_to_text(None) == "")

    adf = jira.text_to_adf("line one" + NL + NL + "line two")
    check("text_to_adf shape", adf["type"] == "doc" and len(adf["content"]) == 3)


def test_branch_names():
    print(NL + "[branch sanitising]")
    cases = [
        ("ACE-24308", "ACE-24308"),
        ("ACE-24308 fix the thing", "ACE-24308-fix-the-thing"),
        ("feat/ACE-1 bad*chars!", "feat/ACE-1-badchars"),
        ("---", "ace-change"),
    ]
    for raw, want in cases:
        got = vcs.sanitize_branch(raw)
        check("sanitises " + repr(raw), got == want, got)


def test_permissions():
    print(NL + "[permission boundary]")
    must_block = [
        "git push origin main", "git commit -m x", "git reset --hard",
        "git checkout main", "git clean -fdx", "gh pr create", "rm -rf /",
        "mv a b", "curl http://evil.test", "wget x", "ssh h",
        "pip install x", "npm install x", "shutdown /s", "mkfs.ext4 /dev/sda1",
    ]
    for c in must_block:
        check("blocks: " + c, permissions.is_blocked(c))

    must_allow = [
        "git status", "git diff", "git log --oneline", "git show HEAD",
        "git blame f", "cat f", "head -20 f", "ls .", "find . -name x",
        "grep -rn x .", "wc -l f", "python -m py_compile f", "pytest -q",
    ]
    for c in must_allow:
        check("allows: " + c, not permissions.is_blocked(c))

    overlap = set(permissions.ALLOWED_TOOLS) & set(permissions.DENIED_TOOLS)
    check("no tool both allowed and denied", not overlap, str(overlap))
    for forbidden in ("git push", "git commit", "gh pr"):
        check("allowlist never grants " + forbidden,
              not any(forbidden in t for t in permissions.ALLOWED_TOOLS))


def test_engine_flags():
    print(NL + "[engine passes the lists]")
    captured = {}

    class FakeProc:
        stdout = iter(["SUMMARY: ok" + NL])

        def wait(self, timeout=None):
            return 0

    def fake_popen(args, **kw):
        captured["args"] = args
        return FakeProc()

    real_popen = engine.proc.popen
    real_which = engine.shutil.which
    engine.proc.popen = fake_popen
    engine.shutil.which = lambda n: "C:/fake/claude.exe"
    try:
        engine.generate_code(str(ROOT), "do the thing", model="claude-opus-5")
    finally:
        engine.proc.popen = real_popen
        engine.shutil.which = real_which

    args = captured.get("args", [])
    check("--allowedTools passed", "--allowedTools" in args)
    check("--disallowedTools passed", "--disallowedTools" in args)
    check("model passed", "claude-opus-5" in args)
    check("acceptEdits", "acceptEdits" in args)
    joined = " ".join(args)
    check("allow entries present", "Bash(git diff:*)" in joined)
    check("deny entries present", "Bash(git push:*)" in joined)


def test_prompt():
    print(NL + "[prompt demands verification]")
    r = engine.SYSTEM_RULES
    fragments = [
        ("VERIFY", "verify section"),
        ("required, not optional", "verification is required"),
        ("Re-read every file", "re-read changed files"),
        ("git diff", "run git diff"),
        ("FIX IT", "fix what it finds"),
        ("cannot commit, push", "states its limits"),
        ("SUMMARY: ", "summary marker"),
    ]
    for frag, label in fragments:
        check("prompt: " + label, frag in r)
    check("extract_summary finds it",
          engine.extract_summary("x" + NL + "SUMMARY: did it", "fb") == "did it")
    check("extract_summary falls back",
          engine.extract_summary("nothing", "fb") == "fb")


def test_related_files():
    print(NL + "[related file matching]")
    files = ["UPGM/STAGE9LD.BAT", "IPGM/9LOADTRM.PY", "IDT1/TRM9LOAD.CFG",
             "UPGM/OTHER.PY"]
    hits = engine.find_related_files(
        ".", files, "Update STAGE9LD.BAT to avoid 9 loads. Check 9LOADTRM.PY too.")
    check("finds named .BAT", any("STAGE9LD.BAT" in h for h in hits), str(hits))
    check("finds named .PY", any("9LOADTRM.PY" in h for h in hits), str(hits))
    check("empty issue -> no hits", engine.find_related_files(".", files, "") == [])


def test_github_token_env():
    print(NL + "[github token travels in the env]")
    captured = {}

    class FakeDone:
        returncode = 0
        stdout = "logged in"
        stderr = ""

    def fake_run(args, **kw):
        captured["args"] = args
        captured["env"] = kw.get("env")
        return FakeDone()

    real = vcs.proc.run
    vcs.proc.run = fake_run
    try:
        vcs.run(["gh", "auth", "status"], ".", token="SECRET123")
    finally:
        vcs.proc.run = real

    check("token not in argv",
          not any("SECRET123" in str(a) for a in captured["args"]))
    check("token in GH_TOKEN", captured["env"].get("GH_TOKEN") == "SECRET123")
    check("token in GITHUB_TOKEN",
          captured["env"].get("GITHUB_TOKEN") == "SECRET123")

    vcs.proc.run = fake_run
    try:
        vcs.run(["gh", "--version"], ".")
    finally:
        vcs.proc.run = real
    check("no env built without a token", captured["env"] is None)


def test_gh_auth_detection():
    print(NL + "[gh exits 0 even when refused]")

    class Rejected:
        returncode = 0
        stdout = ""
        stderr = "X Failed to log in. The token in GH_TOKEN is invalid."

    class Good:
        returncode = 0
        stdout = ""
        stderr = "v Logged in to github.com account someone"

    real = vcs.proc.run
    vcs.proc.run = lambda args, **kw: Rejected()
    try:
        check("rejected token is not authenticated",
              not vcs.gh_authenticated(".", "bad"))
    finally:
        vcs.proc.run = real

    vcs.proc.run = lambda args, **kw: Good()
    try:
        check("valid token is authenticated", vcs.gh_authenticated(".", "good"))
    finally:
        vcs.proc.run = real


def test_config():
    print(NL + "[config]")
    cfgmod.save({"jira_email": "a@b.com", "github_token": "ght",
                 "theme": "light", "claude_model": "claude-opus-5"})
    c = cfgmod.load()
    check("email saved", c.jira_email == "a@b.com")
    check("github token saved", c.github_token == "ght")
    check("theme saved", c.theme == "light")
    check("model saved", c.claude_model == "claude-opus-5")
    check("default model is opus 5",
          cfgmod.Config().claude_model == "claude-opus-5")
    probs = cfgmod.Config().validate()
    check("validate flags missing email", any("jira_email" in p for p in probs))
    check("validate flags missing token", any("jira_token" in p for p in probs))


if __name__ == "__main__":
    try:
        test_jira_keys()
        test_jira_url()
        test_adf()
        test_branch_names()
        test_permissions()
        test_engine_flags()
        test_prompt()
        test_related_files()
        test_github_token_env()
        test_gh_auth_detection()
        test_config()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(NL + "=" * 60)
    print("passed: {}   failed: {}".format(len(PASSED), len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  FAILED: " + f)
        sys.exit(1)
    print("ALL TESTS PASSED")
