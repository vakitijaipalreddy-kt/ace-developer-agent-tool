# ACE DEVELOPER AGENT

A desktop tool that takes a Jira key and drives the whole change through to a PR:

```
Enter Jira: ACE-24308
   -> Read Jira details      (REST v3, ADF flattened to text)
   -> Analyze local repo     (layout, file types, files matching the ticket)
   -> Ask Claude             (issue + repo context -> prompt)
   -> Generate & verify      (Claude edits, then checks its own work)
   -> Show diff for approval (YOU approve / regenerate / reject)
   -> Commit changes
   -> Push branch
   -> Create PR              (gh pr create)
   -> Post PR link to Jira
```

Nothing is committed, pushed, or published until you click **Approve & Push**.

This is a standalone project: it operates on whatever repository you point it
at, and keeps nothing inside that repository.

## How to install and use the tool

Build the distributable:

```powershell
cd C:/repos/jira-to-pr
python build_release.py
```

That runs the tests, builds a standalone `.exe` (Python and every library
bundled in), and produces `release/ACE-Developer-Agent-1.0.0.zip`. Send that
one file. Your teammate extracts it and double-clicks **Install.cmd** - no
admin rights, and no Python needed on their machine.

The installer copies the app to `%LOCALAPPDATA%/Programs`, adds Start Menu and
Desktop shortcuts, registers it in Add/Remove Programs, and reports which of
`git` / `gh` / `claude` are missing. To remove it: Settings > Apps, or
`Install.cmd -Uninstall`.

**They still need `git`, `gh` and `claude` installed** - those are separately
installed and separately authenticated, so they cannot be bundled. The app
checks at startup and says exactly what is missing.

## Running from source

Requires Python 3.9+ plus the three CLIs above.

```powershell
pip install -r requirements.txt
```

Then double-click **`ACE-Developer-Agent.vbs`**, or run
`pythonw run.py` from the project folder.

`truststore` lets Python use the Windows certificate store (needed on networks
that inspect TLS). `pywinpty` gives the Terminal tab a real console so
interactive commands like `gh auth login` work.

## Configure

Click **Settings**. Every setting is a labelled field — no JSON to edit.
The config lives at `%USERPROFILE%\.ace-pr-agent\config.json`, deliberately
**outside any repository**, so a token can never be committed by accident.

| Field | Notes |
|---|---|
| Jira site URL | e.g. `https://kroger.atlassian.net` |
| Jira email | the address you sign in with |
| Jira API token | from id.atlassian.com/manage-profile/security/api-tokens |
| Repository folder | the git checkout to work in |
| Base branch | what PRs are opened against |
| GitHub token | `repo` scope; or sign in from the Terminal tab instead |
| Claude model | defaults to Opus 5 |
| Branch name | placeholders `{key}` and `{summary}` |
| Commit message | placeholders `{key}` and `{summary}` |

**Test Jira** and **Test GitHub** verify credentials before you save.

Environment variables override the file: `JIRA_EMAIL`, `JIRA_TOKEN`,
`JIRA_BASE_URL`, `GH_TOKEN`, `ACE_REPO_PATH`, `ACE_BASE_BRANCH`,
`ACE_CLAUDE_MODEL`. The form warns when one is shadowing a field.

## Using it

1. Type a Jira key (`ACE-24308`, lowercase, or a full browse URL), press Enter.
2. Optionally add **extra instructions** — these go to Claude verbatim.
3. Watch the **Activity** tab; Claude's output streams live.
4. When the run pauses on the **Diff** tab, review and choose:
   - **Approve & Push** — commit, push, open the PR, comment on Jira.
   - **Regenerate** — discard and ask again (up to 4 tries).
   - **Reject & Discard** — discard everything, return to the base branch.

## What Claude can do

Claude does not edit blind in one pass. It reads the relevant files first,
makes the change, then verifies: re-reads what it changed, runs `git diff`,
syntax-checks Python, runs tests where they exist, and fixes what it finds
before handing the diff back.

To do that it may read files, search the repo, run read-only git commands
(`status`, `diff`, `log`, `show`, `blame`), and run tests. It is explicitly
denied `git push`, `git commit`, `git reset`, `git checkout`, `git clean`,
`gh`, `rm`, `mv`, `curl`, `wget`, package installs, and all network tools —
those belong to the pipeline, after you approve.

The lists live in `agent/permissions.py` and are enforced by Claude Code via
`--allowedTools` / `--disallowedTools`.

## Terminal

The **Terminal** tab runs any command in the repo folder, with a real console
(ConPTY) so interactive programs work — including `gh auth login`.

Quick buttons: **Sign in to GitHub**, **GitHub status**, **git status**,
**Recent commits**. Up/Down walks history. While a command is running, what
you type is sent to it as input, so you can answer its prompts.

If GitHub is not signed in, the tool notices at startup and offers to run the
login — rather than launching a browser on every start.

## No console windows

Under `pythonw.exe` the app has no console, so every child process Windows
starts would normally get its own console window — the black boxes that flash
on screen. Every launch goes through `agent/proc.py`, which passes
`CREATE_NO_WINDOW` and a hidden `STARTUPINFO`. `tests/test_no_console.py`
pins this, including a check that no module calls `subprocess` directly.

## Themes

Light and dark. The button in the top bar toggles; the choice is saved and
restored next launch.

## Safety

- **Preflight** blocks the run if the working tree is dirty, the key is
  malformed, credentials are missing, or `gh`/`claude` are unavailable.
- The branch is created **before** generation, so edits never land on `main`.
- Rejecting runs `git reset`, `git checkout -- .`, `git clean -fd` and returns
  you to the base branch.
- A re-run on an existing branch checks it out and resumes rather than failing.
- If the Jira comment fails, the PR still stands and the error is reported.
- The GitHub token is passed through the environment, never the command line,
  so it cannot appear in a process listing.

## Tests

```powershell
cd C:\repos\jira-to-pr
python tests/test_core.py        # 86 checks - jira, vcs, permissions, config
python tests/test_pipeline.py    # 41 checks - the 9 steps, approval, rollback
python tests/test_gui.py         # 72 checks - settings, themes, terminal
python tests/test_no_console.py  # 11 checks - no console windows
```

210 checks against a scratch git repo with Jira, Claude and `gh` stubbed —
no network, and your real repository is never touched.

## Layout

| File | Role |
|---|---|
| `run.py` | Entry point |
| `ACE-Developer-Agent.vbs` | Launcher for running from source |
| `build_release.py` | Builds the exe, installer and zip |
| `ACE-Developer-Agent.spec` | PyInstaller configuration |
| `make_icon.py` | Generates the app icon |
| `installer/Install.ps1` | The installer / uninstaller |
| `agent/doctor.py` | Checks git / gh / claude are present |
| `agent/proc.py` | Subprocess helpers that suppress the console |
| `agent/config.py` | Config loading, env overrides |
| `agent/permissions.py` | What Claude may run while verifying |
| `agent/theme.py` | Light/dark palettes |
| `agent/settings_dialog.py` | The settings form |
| `agent/terminal.py` | Embedded terminal (ConPTY) |
| `agent/jira.py` | Jira REST v3 client, ADF -> text |
| `agent/vcs.py` | git + `gh` operations |
| `agent/engine.py` | Repo analysis, Claude Code bridge |
| `agent/pipeline.py` | The 9 steps, approval gate, rollback |
| `agent/gui.py` | Tkinter UI |
