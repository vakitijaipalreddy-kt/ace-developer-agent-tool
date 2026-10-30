"""GUI suite: settings form, light/dark themes, embedded terminal."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
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
        FAILED.append(name)
        print("  FAIL  " + name + ("  -> " + detail if detail else ""))


tmp = tempfile.mkdtemp(prefix="acegui_")
cfg_file = Path(tmp) / "config.json"
os.environ["ACE_PR_AGENT_CONFIG"] = str(cfg_file)
for _var in ("JIRA_EMAIL", "JIRA_TOKEN", "JIRA_BASE_URL", "ACE_REPO_PATH",
             "ACE_BASE_BRANCH", "ACE_CLAUDE_MODEL", "GH_TOKEN", "GITHUB_TOKEN"):
    os.environ.pop(_var, None)

from agent import config as cfgmod  # noqa: E402
from agent import pipeline, terminal  # noqa: E402
from agent.gui import App  # noqa: E402
from agent.settings_dialog import (LABEL_TO_MODEL, MODEL_CHOICES,  # noqa: E402
                                   MODEL_TO_LABEL, SettingsDialog)
from agent.theme import DARK, LIGHT, ROLES, T  # noqa: E402

repo = Path(tmp) / "repo"
repo.mkdir()
subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)


def walk(widget, out=None):
    out = out if out is not None else []
    out.append(widget)
    for c in widget.winfo_children():
        walk(c, out)
    return out


def find(widget, label):
    for w in walk(widget):
        try:
            if "text" in w.keys() and str(w.cget("text")) == label:
                return w
        except Exception:
            pass
    return None


def colors_in_use(root) -> set[str]:
    found = set()
    for w in walk(root):
        for opt in ("background", "foreground"):
            try:
                if opt in w.keys():
                    found.add(str(w.cget(opt)).lower())
            except Exception:
                pass
    return found


def fresh_app(theme="dark"):
    cfgmod.save({"theme": theme})
    app = App()
    app.update_idletasks()
    return app


# ---------------------------------------------------------------- settings

def test_model_dropdown() -> None:
    print(NL + "[model dropdown]")
    check("Opus 5 is first", MODEL_CHOICES[0][0] == "claude-opus-5")
    check("Opus 5 labelled default", "default" in MODEL_CHOICES[0][1].lower())
    check("label round trips",
          LABEL_TO_MODEL[MODEL_TO_LABEL["claude-sonnet-5"]] == "claude-sonnet-5")
    check("blank option exists", any(v == "" for v, _ in MODEL_CHOICES))


def test_settings_roundtrip() -> None:
    print(NL + "[settings round-trip]")
    app = fresh_app()
    dlg = SettingsDialog(app)
    dlg.withdraw()

    check("all fields have vars",
          all(k in dlg.vars for k, _, _, _ in SettingsDialog.FIELDS))
    check("github token field exists", "github_token" in dlg.vars)

    dlg.vars["jira_base_url"].set("https://kroger.atlassian.net")
    dlg.vars["jira_email"].set("tester@kroger.com")
    dlg.vars["jira_token"].set("secret-token")
    dlg.vars["github_token"].set("gh-secret")
    dlg.vars["repo_path"].set(str(repo))
    dlg.vars["base_branch"].set("main")
    dlg.vars["claude_model"].set(MODEL_TO_LABEL["claude-opus-5"])
    dlg.vars["branch_template"].set("{key}")
    dlg.vars["commit_template"].set("{key} - {summary}")
    dlg.vars["post_pr_link_to_jira"].set(True)

    collected = dlg._collect()
    check("model label mapped to id", collected["claude_model"] == "claude-opus-5")
    check("bool collected as bool", collected["post_pr_link_to_jira"] is True)
    check("no validation problems", dlg._problems(collected) == [],
          str(dlg._problems(collected)))

    dlg._save()
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    check("jira email persisted", saved["jira_email"] == "tester@kroger.com")
    check("github token persisted", saved["github_token"] == "gh-secret")
    check("model persisted", saved["claude_model"] == "claude-opus-5")
    check("parent config refreshed", app.cfg.jira_email == "tester@kroger.com")

    dlg2 = SettingsDialog(app)
    dlg2.withdraw()
    check("reload shows email",
          dlg2.vars["jira_email"].get() == "tester@kroger.com")
    check("reload shows model label",
          dlg2.vars["claude_model"].get() == MODEL_TO_LABEL["claude-opus-5"])
    dlg2.destroy()
    dlg.destroy()
    app.destroy()


def test_settings_validation() -> None:
    print(NL + "[settings validation]")
    app = fresh_app()
    dlg = SettingsDialog(app)
    dlg.withdraw()
    base = {
        "jira_base_url": "https://kroger.atlassian.net",
        "jira_email": "t@kroger.com", "jira_token": "tok",
        "github_token": "", "repo_path": str(repo), "base_branch": "main",
        "claude_model": "claude-opus-5", "branch_template": "{key}",
        "commit_template": "{key} - {summary}", "post_pr_link_to_jira": True,
    }
    check("clean values pass", dlg._problems(dict(base)) == [])
    check("rejects url without scheme",
          any("http" in p for p in
              dlg._problems(dict(base, jira_base_url="kroger.atlassian.net"))))
    check("rejects bad email",
          any("address" in p for p in
              dlg._problems(dict(base, jira_email="nope"))))
    check("rejects empty jira token",
          any("token" in p.lower() for p in
              dlg._problems(dict(base, jira_token=""))))
    check("rejects non-git folder",
          any("git checkout" in p for p in
              dlg._problems(dict(base, repo_path=str(Path(tmp))))))
    check("rejects unknown placeholder",
          any("placeholder" in p for p in
              dlg._problems(dict(base, branch_template="{nope}"))))
    check("accepts summary placeholder",
          dlg._problems(dict(base, branch_template="{key}-{summary}")) == [])
    dlg.destroy()
    app.destroy()


def test_settings_buttons_visible() -> None:
    """Regression: the Save row was pushed off by the tall form."""
    print(NL + "[save button is reachable]")
    app = fresh_app()
    dlg = SettingsDialog(app)
    dlg.update_idletasks()
    dlg.update()

    height = dlg.winfo_height()
    for label in ("Save", "Cancel", "Test Jira", "Test GitHub"):
        btn = find(dlg, label)
        check(label + " exists", btn is not None)

    save = find(dlg, "Save")
    if save is not None:
        save_top = save.winfo_rooty() - dlg.winfo_rooty()
        form_bottom = (dlg.canvas.winfo_rooty() - dlg.winfo_rooty()
                       + dlg.canvas.winfo_height())
        check("Save sits below the form, not above it",
              save_top >= form_bottom,
              "save_top={} form_bottom={}".format(save_top, form_bottom))
        check("Save is in the lower part of the window",
              save_top > height * 0.6,
              "save_top={} height={}".format(save_top, height))
        check("buttons live outside the scroll canvas",
              save.master.master is dlg)
    check("form scrolls", hasattr(dlg, "canvas"))
    dlg.destroy()
    app.destroy()


def test_no_json_editor() -> None:
    print(NL + "[settings is a form, not JSON]")
    src = (ROOT / "agent" / "settings_dialog.py").read_text(encoding="utf-8")
    check("no ScrolledText editor", "ScrolledText" not in src)
    check("does not dump json", "json.dumps" not in src)
    check("secrets are masked", 'show="*"' in src)


# ---------------------------------------------------------------- theme

def test_palettes() -> None:
    print(NL + "[palettes]")
    check("both define every role",
          all(r in P for P in (DARK, LIGHT) for r in ROLES))
    check("dark bg is dark", DARK["BG"] < "#808080")
    check("light bg is light", LIGHT["BG"] > "#808080")
    check("palettes differ", DARK["BG"] != LIGHT["BG"])


def test_theme_toggle() -> None:
    print(NL + "[theme toggle recolours everything]")
    app = fresh_app("dark")
    check("starts dark", DARK["BG"].lower() in colors_in_use(app))

    app.toggle_theme()
    app.update_idletasks()
    light = colors_in_use(app)
    check("flipped to light", T.name == "light")
    check("light bg present", LIGHT["BG"].lower() in light)
    check("no dark bg remains", DARK["BG"].lower() not in light)
    check("no dark panel remains", DARK["PANEL"].lower() not in light)
    check("button says Dark mode", app.theme_btn.cget("text") == "Dark mode")

    app.toggle_theme()
    app.update_idletasks()
    check("flips back", T.name == "dark")
    check("no light bg remains", LIGHT["BG"].lower() not in colors_in_use(app))
    app.destroy()


def test_theme_diff_and_steps() -> None:
    print(NL + "[diff colours and step states follow the theme]")
    app = fresh_app("dark")
    app._hook_diff("diff --git a/x b/x" + NL + "+added" + NL + "-gone" + NL,
                   " x | 2 +-")
    app._hook_step(0, pipeline.STEPS[0], pipeline.DONE)
    app._hook_step(1, pipeline.STEPS[1], pipeline.FAILED)
    app._drain_queue()
    app.update_idletasks()

    check("dark add colour",
          str(app.diff_text.tag_cget("add", "foreground")).lower()
          == DARK["DIFF_ADD"].lower())
    check("done is green in dark",
          app.step_labels[0].cget("fg").lower() == DARK["OK"].lower())

    app.toggle_theme()
    app.update_idletasks()
    check("light add colour",
          str(app.diff_text.tag_cget("add", "foreground")).lower()
          == LIGHT["DIFF_ADD"].lower())
    check("done still green in light",
          app.step_labels[0].cget("fg").lower() == LIGHT["OK"].lower())
    check("failed still red in light",
          app.step_labels[1].cget("fg").lower() == LIGHT["BAD"].lower())
    check("diff text survived", "added" in app.diff_text.get("1.0", "end"))
    app.destroy()


def test_theme_persisted() -> None:
    print(NL + "[theme choice is remembered]")
    app = fresh_app("dark")
    app.toggle_theme()
    check("saved to config", cfgmod.load().theme == "light")
    app.destroy()
    app2 = App()
    app2.update_idletasks()
    check("new window starts light", T.name == "light")
    check("new window bg is light",
          str(app2.cget("bg")).lower() == LIGHT["BG"].lower())
    app2.destroy()


# ---------------------------------------------------------------- terminal

def test_strip_ansi() -> None:
    print(NL + "[ansi stripping]")
    s = terminal.strip_ansi
    check("removes colour", s("\x1b[31mred\x1b[0m") == "red")
    check("removes cursor hide", s("\x1b[?25lhi") == "hi")
    check("removes OSC title", s("\x1b]0;t\x07text") == "text")
    check("normalises crlf", s("a\r\nb") == "a" + NL + "b")
    check("drops lone cr", s("loading\rdone") == "loadingdone")
    check("leaves plain text", s("just text") == "just text")


def test_terminal_tab() -> None:
    print(NL + "[terminal tab]")
    app = fresh_app()
    tabs = [app.notebook.tab(i, "text") for i in range(app.notebook.index("end"))]
    check("Terminal tab exists", "Terminal" in tabs, str(tabs))
    check("has input box", hasattr(app, "term_entry"))
    check("stop starts disabled",
          str(app.term_stop_btn.cget("state")) == "disabled")
    check("has GitHub sign-in button", find(app, "Sign in to GitHub") is not None)
    check("has GitHub status button", find(app, "GitHub status") is not None)
    check("has git status button", find(app, "git status") is not None)

    done = threading.Event()
    real_exit = app._on_terminal_exit

    def wrapped(code):
        real_exit(code)
        done.set()

    app._on_terminal_exit = wrapped
    app.run_terminal_command("git --version")
    for _ in range(600):
        app.update()
        if done.is_set():
            break
        time.sleep(0.05)
    for _ in range(25):
        app.update()
        time.sleep(0.02)

    text = app.term_text.get("1.0", "end")
    check("command echoed", "> git --version" in text)
    check("output shown", "git version" in text, text[-250:])
    check("exit line shown", "[finished, exit 0]" in text, text[-150:])
    check("history recorded", "git --version" in app._term_history)
    check("stop disabled again",
          str(app.term_stop_btn.cget("state")) == "disabled")
    app.destroy()


def test_branding() -> None:
    print(NL + "[branding]")
    app = fresh_app()
    check("window title", app.title() == "ACE DEVELOPER AGENT", app.title())
    app.destroy()


if __name__ == "__main__":
    try:
        test_model_dropdown()
        test_settings_roundtrip()
        test_settings_validation()
        test_settings_buttons_visible()
        test_no_json_editor()
        test_palettes()
        test_theme_toggle()
        test_theme_diff_and_steps()
        test_theme_persisted()
        test_strip_ansi()
        test_terminal_tab()
        test_branding()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(NL + "=" * 60)
    print("passed: {}   failed: {}".format(len(PASSED), len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  FAILED: " + f)
        sys.exit(1)
    print("ALL TESTS PASSED")
