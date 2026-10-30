"""The settings window: one labelled field per setting, no JSON."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from . import config as cfgmod
from . import jira
from .theme import T

# (value passed to `claude --model`, label shown in the dropdown)
MODEL_CHOICES = [
    ("claude-opus-5", "Opus 5 - most capable (default)"),
    ("opus", "Opus - latest"),
    ("claude-sonnet-5", "Sonnet 5 - faster"),
    ("sonnet", "Sonnet - latest"),
    ("claude-fable-5-1", "Fable 5.1"),
    ("fable", "Fable - latest"),
    ("claude-haiku-4-5-20251001", "Haiku 4.5 - fastest"),
    ("", "Use my Claude Code default"),
]
MODEL_LABELS = [label for _, label in MODEL_CHOICES]
LABEL_TO_MODEL = {label: value for value, label in MODEL_CHOICES}
MODEL_TO_LABEL = {value: label for value, label in MODEL_CHOICES}


class SettingsDialog(tk.Toplevel):
    """A plain form. Fields are declared once and the UI is built from that."""

    # (key, label, hint, kind)
    FIELDS = [
        ("jira_base_url", "Jira site URL", "e.g. https://kroger.atlassian.net",
         "text"),
        ("jira_email", "Jira email", "The address you sign in to Jira with",
         "text"),
        ("jira_token", "Jira API token",
         "Create one at id.atlassian.com/manage-profile/security/api-tokens",
         "secret"),
        ("repo_path", "Repository folder", "The local git checkout to work in",
         "folder"),
        ("base_branch", "Base branch", "Branch that PRs are opened against",
         "text"),
        ("github_token", "GitHub token",
         "Needed to open the PR. Create at github.com/settings/tokens with "
         "the 'repo' scope. Leave blank to sign in from the Terminal tab.",
         "secret"),
        ("claude_model", "Claude model", "Which model writes the code", "choice"),
        ("branch_template", "Branch name", "Placeholders: {key} and {summary}",
         "text"),
        ("commit_template", "Commit message",
         "Placeholders: {key} and {summary}", "text"),
        ("post_pr_link_to_jira", "Comment the PR link on the Jira issue", "",
         "bool"),
    ]

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.parent = parent
        self.title("Settings")
        self.configure(bg=T.BG)
        # Tall enough for the whole form, but never taller than the screen.
        height = min(960, max(600, parent.winfo_screenheight() - 120))
        self.geometry("740x{}".format(height))
        self.minsize(620, 420)
        self.transient(parent)

        self.vars: dict[str, tk.Variable] = {}
        self.path = cfgmod.config_path()
        self.shadowed = cfgmod.shadowing_env_vars()

        self._build()
        self._load_values()
        self.grab_set()
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self._save())

    # ------------------------------------------------------------------

    def _build(self) -> None:
        header = tk.Frame(self, bg=T.BG)
        header.pack(fill="x", padx=18, pady=(16, 4))
        tk.Label(header, text="Settings", bg=T.BG, fg=T.FG,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w")
        tk.Label(header, text="Saved to " + str(self.path), bg=T.BG, fg=T.MUTED,
                 font=("Consolas", 8)).pack(anchor="w", pady=(2, 0))

        # Buttons and status are packed to the bottom FIRST so they always
        # keep their space - the form can never push them off-screen.
        self._build_buttons()

        # The form scrolls, so every field stays reachable on a short screen.
        outer = tk.Frame(self, bg=T.BG)
        outer.pack(fill="both", expand=True, padx=(18, 0), pady=10)

        self.canvas = tk.Canvas(outer, bg=T.BG, highlightthickness=0, bd=0)
        scroll = tk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        form = tk.Frame(self.canvas, bg=T.BG)
        self._form_window = self.canvas.create_window((0, 0), window=form,
                                                      anchor="nw")

        form.bind("<Configure>",
                  lambda e: self.canvas.configure(
                      scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(
                             self._form_window, width=e.width - 18))
        self.bind("<MouseWheel>", self._on_wheel)

        form.columnconfigure(1, weight=1)

        row = 0
        for key, label, hint, kind in self.FIELDS:
            if kind == "bool":
                boolvar = tk.BooleanVar()
                self.vars[key] = boolvar
                tk.Checkbutton(
                    form, text=label, variable=boolvar, bg=T.BG, fg=T.FG,
                    selectcolor=T.PANEL, activebackground=T.BG,
                    activeforeground=T.FG, font=("Segoe UI", 10), anchor="w",
                    relief="flat", highlightthickness=0, cursor="hand2",
                ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(12, 2))
                row += 1
                continue

            tk.Label(form, text=label, bg=T.BG, fg=T.FG, font=("Segoe UI", 10),
                     anchor="w").grid(row=row, column=0, sticky="w", pady=(12, 2))
            if key in self.shadowed:
                tk.Label(form, text="overridden by " + self.shadowed[key],
                         bg=T.BG, fg=T.WARN, font=("Segoe UI", 8)).grid(
                    row=row, column=1, sticky="e", pady=(12, 2))
            row += 1

            strvar = tk.StringVar()
            self.vars[key] = strvar
            holder = tk.Frame(form, bg=T.BG)
            holder.grid(row=row, column=0, columnspan=2, sticky="ew")
            holder.columnconfigure(0, weight=1)

            if kind == "choice":
                ttk.Combobox(
                    holder, textvariable=strvar, values=MODEL_LABELS,
                    state="readonly", font=("Segoe UI", 10),
                ).grid(row=0, column=0, sticky="ew", ipady=3)
                row += 1
                if hint:
                    tk.Label(form, text=hint, bg=T.BG, fg=T.MUTED,
                             font=("Segoe UI", 8), anchor="w").grid(
                        row=row, column=0, columnspan=2, sticky="w", pady=(2, 0))
                    row += 1
                continue

            entry = tk.Entry(
                holder, textvariable=strvar, bg=T.ENTRY_BG, fg=T.FG,
                insertbackground=T.FG, relief="flat", font=("Consolas", 10),
                show="*" if kind == "secret" else "",
            )
            entry.grid(row=0, column=0, sticky="ew", ipady=6, ipadx=6)

            if kind == "secret":
                self._add_reveal_button(holder, entry)
            elif kind == "folder":
                tk.Button(holder, text="Browse", command=self._browse,
                          bg=T.PANEL, fg=T.MUTED, relief="flat",
                          font=("Segoe UI", 9), padx=10, cursor="hand2").grid(
                    row=0, column=1, padx=(6, 0))
            row += 1

            if hint:
                tk.Label(form, text=hint, bg=T.BG, fg=T.MUTED,
                         font=("Segoe UI", 8), anchor="w", wraplength=640,
                         justify="left").grid(
                    row=row, column=0, columnspan=2, sticky="w", pady=(2, 0))
                row += 1

        if self.shadowed:
            names = ", ".join(sorted(set(self.shadowed.values())))
            self.status.configure(
                text="Note: " + names + " set in the environment will override "
                     "whatever you save here.",
                fg=T.WARN,
            )

    def _build_buttons(self) -> None:
        """Anchored to the bottom of the window, outside the scrolling form."""
        btns = tk.Frame(self, bg=T.BG)
        btns.pack(side="bottom", fill="x", padx=18, pady=(6, 14))

        self.save_btn = tk.Button(
            btns, text="Save", command=self._save, bg=T.ACCENT, fg=T.BTN_FG,
            relief="flat", padx=26, pady=8, font=("Segoe UI", 10, "bold"),
            cursor="hand2",
        )
        self.save_btn.pack(side="right")
        tk.Button(btns, text="Cancel", command=self.destroy, bg=T.PANEL,
                  fg=T.FG, relief="flat", padx=16, pady=8,
                  font=("Segoe UI", 10), cursor="hand2").pack(side="right", padx=8)
        tk.Button(btns, text="Test Jira", command=self._test, bg=T.PANEL,
                  fg=T.FG, relief="flat", padx=16, pady=8,
                  font=("Segoe UI", 10), cursor="hand2").pack(side="left")
        tk.Button(btns, text="Test GitHub", command=self._test_github,
                  bg=T.PANEL, fg=T.FG, relief="flat", padx=16, pady=8,
                  font=("Segoe UI", 10), cursor="hand2").pack(side="left", padx=8)

        self.status = tk.Label(self, text="", bg=T.BG, fg=T.MUTED,
                               font=("Segoe UI", 9), anchor="w",
                               wraplength=680, justify="left")
        self.status.pack(side="bottom", fill="x", padx=18, pady=(0, 2))

    def _on_wheel(self, event) -> None:
        self.canvas.yview_scroll(-1 * (event.delta // 120), "units")

    def _add_reveal_button(self, holder: tk.Frame, entry: tk.Entry) -> None:
        shown = tk.BooleanVar(value=False)
        btn = tk.Button(holder, text="Show", bg=T.PANEL, fg=T.MUTED,
                        relief="flat", font=("Segoe UI", 9), padx=10,
                        cursor="hand2")

        def toggle() -> None:
            shown.set(not shown.get())
            entry.configure(show="" if shown.get() else "*")
            btn.configure(text="Hide" if shown.get() else "Show")

        btn.configure(command=toggle)
        btn.grid(row=0, column=1, padx=(6, 0))

    # ------------------------------------------------------------------

    def _load_values(self) -> None:
        cfg = cfgmod.load()
        for key, _, _, kind in self.FIELDS:
            value = getattr(cfg, key, "")
            if kind == "bool":
                self.vars[key].set(bool(value))
            elif kind == "choice":
                self.vars[key].set(
                    MODEL_TO_LABEL.get(str(value),
                                       MODEL_TO_LABEL["claude-opus-5"])
                )
            else:
                # never echo the placeholder token back into the field
                if key == "jira_token" and str(value).startswith("<"):
                    value = ""
                self.vars[key].set(str(value))

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(
            parent=self,
            title="Select the git repository folder",
            initialdir=self.vars["repo_path"].get() or str(Path.home()),
        )
        if chosen:
            self.vars["repo_path"].set(chosen)

    def _collect(self) -> dict:
        out: dict = {}
        for key, _, _, kind in self.FIELDS:
            if kind == "bool":
                out[key] = bool(self.vars[key].get())
            elif kind == "choice":
                out[key] = LABEL_TO_MODEL.get(str(self.vars[key].get()), "")
            else:
                out[key] = str(self.vars[key].get()).strip()
        return out

    def _problems(self, values: dict) -> list[str]:
        probs = []
        if not values["jira_base_url"].startswith("http"):
            probs.append("Jira site URL should start with http:// or https://")
        if "@" not in values["jira_email"]:
            probs.append("Jira email does not look like an address")
        if not values["jira_token"]:
            probs.append("Jira API token is required")

        repo = values["repo_path"]
        if not repo:
            probs.append("Repository folder is required")
        elif not (Path(repo) / ".git").exists():
            probs.append("Repository folder is not a git checkout: " + repo)

        if not values["base_branch"]:
            probs.append("Base branch is required")

        for key, label in (("branch_template", "Branch name"),
                           ("commit_template", "Commit message")):
            try:
                values[key].format(key="X", summary="Y")
            except (KeyError, IndexError, ValueError):
                probs.append(
                    label + " may only use the {key} and {summary} placeholders"
                )
        return probs

    def _save(self) -> None:
        values = self._collect()
        probs = self._problems(values)
        if probs:
            self.status.configure(text="- " + "\n- ".join(probs), fg=T.BAD)
            return
        cfgmod.save(values, self.path)
        self.parent.cfg = cfgmod.load()
        self.status.configure(text="Saved.", fg=T.OK)
        self.after(700, self.destroy)

    def _test_github(self) -> None:
        """Check the GitHub token: who it is, and whether it can open PRs.

        `gh auth status` exits 0 even for a rejected token, so this asks the
        API directly and reads the scopes off the response headers.
        """
        values = self._collect()
        token = values["github_token"]
        if not token:
            from . import vcs

            name = vcs.gh_login_name(values["repo_path"] or ".")
            if name:
                self.status.configure(
                    text="No token set, but `gh` is signed in as " + name
                         + " - that will be used.", fg=T.OK)
            else:
                self.status.configure(
                    text="No GitHub token, and `gh` is not signed in. The "
                         "Create PR step will fail.", fg=T.BAD)
            return

        self.status.configure(text="Checking GitHub...", fg=T.MUTED)
        self.update_idletasks()
        try:
            import requests

            resp = requests.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": "Bearer " + token,
                    "Accept": "application/vnd.github+json",
                },
                timeout=20,
            )
            if resp.status_code == 401:
                self.status.configure(
                    text="GitHub rejected the token (401). It may be expired "
                         "or mistyped.", fg=T.BAD)
                return
            if resp.status_code >= 400:
                self.status.configure(
                    text="GitHub returned HTTP {}: {}".format(
                        resp.status_code, resp.text[:200]), fg=T.BAD)
                return

            login = resp.json().get("login", "unknown")
            scopes = resp.headers.get("x-oauth-scopes", "")
            # fine-grained tokens report no scopes; only warn for classic ones
            if scopes and "repo" not in scopes:
                self.status.configure(
                    text="Connected as " + login + ", but the token lacks the "
                         "'repo' scope so it cannot open PRs. Scopes: " + scopes,
                    fg=T.WARN)
            else:
                detail = " (scopes: " + scopes + ")" if scopes else ""
                self.status.configure(
                    text="Connected to GitHub as " + login + detail, fg=T.OK)
        except Exception as exc:
            self.status.configure(
                text="{}: {}".format(type(exc).__name__, exc), fg=T.BAD)

    def _test(self) -> None:
        """Verify the credentials actually authenticate against Jira."""
        values = self._collect()
        if not (values["jira_email"] and values["jira_token"]):
            self.status.configure(text="Enter an email and token first.", fg=T.BAD)
            return
        self.status.configure(text="Checking Jira...", fg=T.MUTED)
        self.update_idletasks()
        try:
            client = jira.JiraClient(
                values["jira_base_url"], values["jira_email"],
                values["jira_token"],
            )
            resp = client._request("GET", "/myself")
            name = resp.json().get("displayName", "unknown")
            self.status.configure(text="Connected to Jira as " + name, fg=T.OK)
        except jira.JiraError as exc:
            self.status.configure(text=str(exc), fg=T.BAD)
        except Exception as exc:
            self.status.configure(
                text="{}: {}".format(type(exc).__name__, exc), fg=T.BAD)
