"""Build the distributable: one exe, an installer, and a zip to hand out.

    python build_release.py

Produces:
    dist/ACE Developer Agent.exe          the standalone application
    release/ACE-Developer-Agent-<ver>/    the folder a teammate extracts
    release/ACE-Developer-Agent-<ver>.zip the file you actually send

Run it from a shell where `python -m PyInstaller --version` works.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "1.0.0"
APP = "ACE Developer Agent"
STAGE_NAME = "ACE-Developer-Agent-" + VERSION


def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    print("  $ " + " ".join(args))
    return subprocess.run(args, cwd=str(ROOT), check=True, **kw)


def step(msg: str) -> None:
    print("\n=== " + msg + " ===")


def main() -> int:
    step("1/5  icon")
    run([sys.executable, "make_icon.py"])

    step("2/5  tests")
    failed = []
    for test in sorted((ROOT / "tests").glob("test_*.py")):
        print("  running " + test.name)
        res = subprocess.run([sys.executable, str(test)], cwd=str(ROOT),
                             capture_output=True, text=True)
        tail = (res.stdout or "").strip().splitlines()[-1:] or ["(no output)"]
        print("    " + tail[0])
        if res.returncode != 0:
            failed.append(test.name)
    if failed:
        print("\nTests failed: " + ", ".join(failed))
        print("Refusing to build a release from a failing tree.")
        return 1

    step("3/5  executable")
    # A previously built copy may still be running, which locks the .exe and
    # makes PyInstaller fail with "Access is denied".
    subprocess.run(["taskkill", "/IM", APP + ".exe", "/F"],
                   capture_output=True)
    for stale in (ROOT / "build", ROOT / "dist"):
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)
    run([sys.executable, "-m", "PyInstaller", "ACE-Developer-Agent.spec",
         "--noconfirm", "--clean"])

    exe = ROOT / "dist" / (APP + ".exe")
    if not exe.exists():
        print("PyInstaller did not produce " + str(exe))
        return 1
    print("  built {} ({:.1f} MB)".format(exe.name, exe.stat().st_size / 1e6))

    step("4/5  staging folder")
    release = ROOT / "release"
    stage = release / STAGE_NAME
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    shutil.copy2(exe, stage / exe.name)
    shutil.copy2(ROOT / "installer" / "Install.ps1", stage / "Install.ps1")
    shutil.copy2(ROOT / "installer" / "Install.cmd", stage / "Install.cmd")
    shutil.copy2(ROOT / "README.md", stage / "README.md")

    (stage / "READ-ME-FIRST.txt").write_text(
        "\n".join([
            APP + "  v" + VERSION,
            "=" * 44,
            "",
            "TO INSTALL",
            "  Double-click  Install.cmd",
            "",
            "  It installs to your user profile (no admin needed), adds Start",
            "  Menu and Desktop shortcuts, and tells you if anything is missing.",
            "",
            "BEFORE IT WILL WORK you need three command-line tools installed:",
            "",
            "  git     https://git-scm.com/download/win",
            "  gh      https://cli.github.com",
            "  claude  https://claude.com/claude-code",
            "",
            "  The installer checks for these and says which are missing.",
            "",
            "FIRST RUN",
            "  1. Open the app from the Start Menu.",
            "  2. Click Settings:",
            "       - Jira email and API token",
            "         (id.atlassian.com/manage-profile/security/api-tokens)",
            "       - Repository folder: your local git checkout",
            "       - GitHub token with 'repo' scope, OR use the Terminal",
            "         tab to run 'Sign in to GitHub'",
            "  3. Click Test Jira and Test GitHub to confirm, then Save.",
            "",
            "WHAT IT DOES",
            "  Type a Jira key, press Run. It reads the ticket, has Claude make",
            "  and verify the change, shows you the diff, and only after you",
            "  click Approve does it commit, push, open the PR and comment on",
            "  the Jira issue.",
            "",
            "YOUR CREDENTIALS",
            "  Stored per-user in %USERPROFILE%\\.ace-pr-agent\\config.json,",
            "  never inside a repository, so they cannot be committed.",
            "",
            "TO UNINSTALL",
            "  Settings > Apps, or run:  Install.cmd -Uninstall",
            "",
            "See README.md for the full documentation.",
            "",
        ]),
        encoding="utf-8",
    )

    for f in sorted(stage.iterdir()):
        print("  {:<34} {:>10,} bytes".format(f.name, f.stat().st_size))

    step("5/5  zip")
    zip_path = release / (STAGE_NAME + ".zip")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in stage.rglob("*"):
            z.write(f, Path(STAGE_NAME) / f.relative_to(stage))

    print("\nDone.")
    print("  Send this file: {}".format(zip_path))
    print("  Size: {:.1f} MB".format(zip_path.stat().st_size / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
